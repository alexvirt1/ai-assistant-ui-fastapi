"""Generate an image for a chat and keep it.

What this adds on top of the ComfyUI client is the GPU hand-off. AI-1 has one
12 GB GPU, shared by Ollama (the chat model) and ComfyUI (the image model), and
the two do not know about each other. With both loaded, the image model either
fails out of memory or runs from system RAM at a fraction of the speed. So:

1. unload whatever Ollama holds (the chat model, maybe the embedding model)
2. generate the image
3. ask ComfyUI to free its models again, so the chat model can load for the
   reply - which then pays a cold load of about 6 s

Only one image is generated at a time per backend, behind a lock; a second
request waits for the first rather than fighting it for the GPU.
"""

import asyncio
import logging
import random
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from ..models import ollama
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


async def generate_for_chat(*, prompt: str, thread_id: str, user_id: str,
                            model: str | None = None, size: str | None = None,
                            seed: int | None = None) -> dict:
    """Generate, store and describe one image. Raises ImageGenerationError."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ImageGenerationError("The image needs a description.")
    prompt = prompt[:MAX_PROMPT_CHARS]

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

    async with _lock:
        await _unload_chat_models()
        try:
            png = await comfyui.generate(spec, request)
        finally:
            await comfyui.free_memory()

    _check_png(png)
    image_id = await store.save_image(
        thread_id=thread_id, user_id=user_id, prompt=prompt, model=name,
        seed=seed, width=width, height=height, png=png,
    )
    return {
        "image_id": str(image_id),
        "model": name,
        "width": width,
        "height": height,
        "seed": seed,
        "prompt": prompt,
    }


async def _unload_chat_models() -> None:
    for model in await ollama.resident():
        if not await ollama.unload(model.tag):
            logger.warning("could not unload %s before generating an image", model.tag)


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
