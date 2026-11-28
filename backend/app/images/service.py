"""Generate an image for a chat and keep it.

What this adds on top of the ComfyUI client is the GPU hand-off. AI-1 has one
12 GB GPU, shared by Ollama (the chat model) and ComfyUI (the image model), and
the two do not know about each other. With both loaded, the image model either
fails out of memory or runs from system RAM at a fraction of the speed. So:

1. unload whatever Ollama holds (the chat model, maybe the embedding model)
2. generate the image
3. ask ComfyUI to free its models again, wait until it has, and preload the
   chat model - so it lands on the GPU, not the CPU, before the reply

Only one image is generated at a time per backend, behind a lock; a second
request waits for the first rather than fighting it for the GPU. Steps 1 and 3
are skipped between images of one batch ("three more variants"): the chat
model is only needed again after the last one.
"""

import asyncio
import dataclasses
from dataclasses import dataclass
import logging
import os
import random
import time
import uuid
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from ..models import ollama
from ..models.selection import current_tag
from ..references import caption as captions
from ..references import store as reference_store
from . import comfyui, store
from .comfyui import ImageGenerationError, ImageRequest

logger = logging.getLogger(__name__)

SIZES: dict[str, tuple[int, int]] = {
    "square": (1024, 1024),
    "portrait": (832, 1216),
    "landscape": (1216, 832),
    "wide": (1344, 768),
}
DEFAULT_SIZE = "square"

# A 1024x1024 PNG from these models is 1-2 MB; anything far larger is not one
# of ours.
MAX_PNG_BYTES = 20 * 1024 * 1024
MAX_PROMPT_CHARS = 2000

_lock = asyncio.Lock()
# Images waiting for or holding the lock. The last one out hands the GPU back.
_pending = 0

# Self-heal (heal_chat_model): at most one attempt per this many seconds, so
# a GPU that is genuinely too full can't make every turn wait for a reload.
HEAL_INTERVAL = float(os.getenv("CHAT_MODEL_HEAL_INTERVAL", "300"))
_last_heal = float("-inf")

# What each chat model took in memory when last seen resident (it depends on
# num_ctx), so the hand-back can wait for as much free VRAM as it needs.
_model_sizes: dict[str, int] = {}


def fit_margin_bytes() -> int:
    """VRAM Ollama wants free beyond a model's size to put all of it on the GPU.

    qwen3:8b (7065 MiB at 24576) needed about 8.96 GB free: llama.cpp's 1 GiB
    fit margin plus its 457 MiB minimum.
    """
    return int(float(os.getenv("CHAT_MODEL_FIT_MARGIN_BYTES", str(1.6e9))))


# Not yet seen resident: its file plus room for the context (qwen3:8b: a
# 5.2 GB file took 5.7 GB at num_ctx 8192).
CONTEXT_ALLOWANCE = 0.8e9


async def chat_model_size(tag: str) -> int | None:
    """What `tag` takes in memory: as last seen resident, else estimated from its file."""
    if tag in _model_sizes:
        return _model_sizes[tag]
    on_disk = await ollama.disk_size(tag)
    return int(on_disk + CONTEXT_ALLOWANCE) if on_disk else None


async def handback_needed(tag: str) -> int:
    """Free VRAM to wait for before reloading `tag` (capped in comfyui by the card)."""
    size = await chat_model_size(tag)
    floor = comfyui.handback_free_bytes()
    return max(floor, size + fit_margin_bytes()) if size else floor


async def load_references(reference_ids: list[str], thread_id: str, user_id: str) -> list:
    """The chat's reference photos by id, checked. Raises ImageGenerationError."""
    ids = list(dict.fromkeys(r.strip() for r in reference_ids if r and r.strip()))
    if len(ids) > comfyui.MAX_REFERENCES:
        raise ImageGenerationError(
            f"At most {comfyui.MAX_REFERENCES} reference images can be used for one image.")
    refs = []
    for ref_id in ids:
        try:
            ref = await reference_store.get(uuid.UUID(ref_id), user_id)
        except ValueError:
            ref = None
        if ref is None or ref.thread_id != thread_id:
            raise ImageGenerationError(f"There is no reference image {ref_id} in this chat.")
        if ref.deleted:
            raise ImageGenerationError(f"The reference image {ref.name} was deleted.")
        refs.append(ref)
    return refs


async def generate_for_chat(*, prompt: str, thread_id: str, user_id: str,
                            model: str | None = None, size: str | None = None,
                            seed: int | None = None,
                            reference_ids: list[str] | None = None) -> dict:
    """Generate, store and describe one image. Raises ImageGenerationError."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ImageGenerationError("The image needs a description.")
    prompt = prompt[:MAX_PROMPT_CHARS]
    refs = await load_references(reference_ids or [], thread_id, user_id)
    if refs:
        # Only FLUX.2 Klein takes references; whatever model was asked for.
        model = next(m.name for m in comfyui.MODELS.values() if m.supports_references)

    available = await comfyui.available_models()
    if not available:
        raise ImageGenerationError("No image model is available on AI-1 right now.")
    name = (model or "").strip().lower() or comfyui.default_model()
    if name not in available:
        raise ImageGenerationError(
            f"Unknown or unavailable image model '{name}'. Available: {', '.join(available)}."
        )
    spec = comfyui.MODELS[name]

    size_name = (size or "").strip().lower() or DEFAULT_SIZE
    if size_name not in SIZES:
        raise ImageGenerationError(f"Unknown size '{size_name}'. Use one of: {', '.join(SIZES)}.")
    width, height = SIZES[size_name]
    seed = seed if seed is not None and seed >= 0 else random.randint(0, 2**31 - 1)
    request = ImageRequest(prompt=prompt, width=width, height=height,
                           seed=seed, steps=spec.default_steps)

    # Agreed with the AI-1 agent: while it holds a reservation, no images and
    # no unloading of its Ollama models.
    held = await comfyui.reservation()
    if held is not None:
        raise ImageGenerationError(f"{held.describe()}. Try again after that.")

    global _pending
    _pending += 1
    try:
        async with _lock:
            await _unload_chat_models()
            try:
                if refs:
                    texts = await _captions(refs)
                    request = dataclasses.replace(
                        request, prompt=captions.prompt_with_captions(prompt, texts))
                png = await comfyui.generate(spec, request, references=[r.png for r in refs])
            finally:
                if _pending == 1:
                    await _return_gpu_to_chat()
    finally:
        _pending -= 1

    _check_png(png)
    image_id = await store.save_image(
        thread_id=thread_id, user_id=user_id, prompt=prompt, model=name,
        seed=seed, width=width, height=height, png=png,
        reference_ids=[str(r.id) for r in refs],
    )
    return {
        "image_id": str(image_id),
        "model": name,
        "width": width,
        "height": height,
        "seed": seed,
        "prompt": prompt,
        "references": [{"id": str(r.id), "name": r.name} for r in refs],
        **({"note": "With 3 or 4 references each one is used at lower resolution, so "
                    "shapes and details follow them less closely than with 1 or 2."}
           if len(refs) > 2 else {}),
    }


async def _captions(refs: list) -> list[str | None]:
    """What each reference shows, captioning the ones not seen before.

    Called with the chat model already unloaded. ComfyUI may still hold its
    model from an earlier image, so it is asked to free the GPU first; the
    vision model unloads itself after answering (keep_alive 0), and Klein
    loads next. A photo that can't be captioned is used without one.
    """
    texts = [r.caption for r in refs]
    missing = [i for i, text in enumerate(texts) if not text]
    if not missing or not captions.caption_model():
        return texts
    await comfyui.free_memory()
    await comfyui.wait_until_released()
    for i in missing:
        text = await captions.caption(refs[i].png)
        if text:
            texts[i] = text
            try:
                await reference_store.set_caption(refs[i].id, text)
            except Exception:
                logger.exception("could not store the caption of reference %s", refs[i].id)
    await ollama.unload(captions.caption_model())
    return texts


async def _unload_chat_models() -> None:
    for model in await ollama.resident():
        _model_sizes[model.tag] = model.size_bytes
        if not await ollama.unload(model.tag):
            logger.warning("could not unload %s before generating an image", model.tag)


async def _return_gpu_to_chat() -> None:
    """Free ComfyUI's memory, wait for it, then preload the chat model.

    Skipped when AI-1 took a reservation while the images ran: then the GPU is
    its to use, and preloading the chat model would get in the way.
    """
    held = await comfyui.reservation()
    if held is not None:
        logger.info("not handing the GPU back to the chat model: %s", held.describe())
        return
    await comfyui.free_memory()
    tag = current_tag()
    num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
    # Twice at most: a model loaded while memory was still short stays partly
    # on the CPU until it is unloaded, so check and reload once.
    for attempt in (1, 2):
        if not await comfyui.wait_until_released(needed=await handback_needed(tag)):
            logger.warning("ComfyUI did not release GPU memory in time; the chat model "
                           "may load partly on the CPU")
        await ollama.warm(tag, num_ctx)
        model = next((m for m in await ollama.resident() if m.tag == tag), None)
        if model is None or model.fully_on_gpu:
            return
        share = 100 * model.vram_bytes // max(model.size_bytes, 1)
        if attempt == 1:
            logger.info("%s loaded only %d%% on the GPU; reloading it once", tag, share)
            await ollama.unload(tag)
            continue
        logger.warning(
            "%s is only %d%% on the GPU (%d of %d MiB); replies will be slow. "
            "Lower OLLAMA_NUM_CTX or free GPU memory on the Ollama host.",
            tag, share, model.vram_bytes // 2**20, model.size_bytes // 2**20,
        )


# --- chat turns while AI-1 holds a reservation --------------------------------

# A chat model this large fills the card (qwen3.6 35B-A3B: 11.5 GB of 12).
BIG_CHAT_MODEL_BYTES = int(float(os.getenv("BIG_CHAT_MODEL_BYTES", str(8e9))))


@dataclass(frozen=True)
class ReservedPlan:
    """How a local turn runs while AI-1 holds the GPU and the chat model is big."""
    action: str              # "cpu": answer with a small model on the CPU; "busy": don't answer
    note: str                # shown above the answer
    model: str | None = None
    num_gpu: int = 0
    num_thread: int | None = None


async def chat_model_is_big() -> bool:
    """Whether the chat model fills the GPU, so nothing else fits beside it."""
    try:
        size = await chat_model_size(current_tag())
    except Exception:
        return False
    return bool(size and size >= BIG_CHAT_MODEL_BYTES)


async def reserved_gpu_plan() -> ReservedPlan | None:
    """None if the turn may load the chat model as usual.

    Agreed with the AI-1 agent: a big chat model is never loaded on the GPU
    while AI-1 holds a valid reservation. Loading 11.5 GB beside its running
    job would make the job run out of memory, or put the model mostly on the
    CPU. RESERVED_GPU_CHAT picks what happens instead: "busy" (default) says
    local chat is unavailable until the reservation ends; "cpu" answers with
    RESERVED_GPU_FALLBACK_MODEL with no GPU layers. Measured on AI-1's Xeon:
    qwen3:8b on the CPU writes 3.7 tok/s, too slow to be the default. A small chat model (qwen3:8b)
    keeps the old behaviour, and so does a big one already fully loaded.
    """
    try:
        held = await comfyui.reservation()
        if held is None:
            return None
        tag = current_tag()
        size = await chat_model_size(tag)
        if not size or size < BIG_CHAT_MODEL_BYTES:
            return None
        if any(m.tag == tag and m.fully_on_gpu for m in await ollama.resident()):
            return None
    except Exception:
        logger.exception("could not check AI-1's reservation before a chat turn")
        return None
    if os.getenv("RESERVED_GPU_CHAT", "busy").strip().lower() != "cpu":
        return ReservedPlan("busy", f"_{held.describe()}. The local chat model needs the whole "
                                    "GPU, so it can't answer until then. Please try again later._")
    fallback = os.getenv("RESERVED_GPU_FALLBACK_MODEL", "qwen3:8b").strip()
    threads = int(os.getenv("RESERVED_GPU_CPU_THREADS", "6"))
    return ReservedPlan("cpu", f"_{held.describe()}, so {fallback} answers on the CPU; "
                               "this is slower than usual._\n\n",
                        model=fallback, num_gpu=0, num_thread=threads)


async def heal_chat_model() -> bool:
    """Reload the chat model if it sits partly on the CPU. True if it tried.

    Seen 2026-11-27: an image model left loaded in ComfyUI by other work on
    AI-1 held ~7 GB, so qwen3:8b loaded 3.3 of its 6.0 GB on the GPU and every
    reply was several times slower, until something reloaded it. Our own
    image jobs hand the GPU back (_return_gpu_to_chat); this covers whoever
    else left a model there. Called before a local chat turn.

    Does nothing while an image job of ours runs (it unloads the chat model on
    purpose), during AI-1's reservation (no Ollama load or unload calls then,
    as agreed), while another client's ComfyUI job runs (freeing would not
    work, and the wait would stall the reply), or more than once per
    HEAL_INTERVAL. Never raises: the turn goes ahead either way.
    """
    global _last_heal
    try:
        if not comfyui.base_url() or _pending or _lock.locked():
            return False
        tag = current_tag()
        model = next((m for m in await ollama.resident() if m.tag == tag), None)
        if model is None or model.fully_on_gpu:
            return False
        _model_sizes[tag] = model.size_bytes
        if time.monotonic() - _last_heal < HEAL_INTERVAL:
            return False
        if await comfyui.reservation() is not None or await comfyui.queue_busy():
            return False
        _last_heal = time.monotonic()
        share = 100 * model.vram_bytes // max(model.size_bytes, 1)
        logger.warning("chat model was partly on CPU (%s %d%% on the GPU); freeing ComfyUI "
                       "and reloading it", tag, share)
        async with _lock:
            await ollama.unload(tag)
            await _return_gpu_to_chat()
        return True
    except Exception:
        logger.exception("could not check or heal the chat model's GPU placement")
        return False


def _check_png(png: bytes) -> None:
    if len(png) > MAX_PNG_BYTES:
        raise ImageGenerationError("The image server returned an unexpectedly large file.")
    try:
        with Image.open(BytesIO(png)) as image:
            if image.format != "PNG":
                raise ValueError(image.format)
            image.verify()
    except (UnidentifiedImageError, ValueError, OSError) as exc:
        raise ImageGenerationError("The image server returned something that is not a PNG.") from exc
