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
import logging
import os
import random
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
        if not await comfyui.wait_until_released():
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
