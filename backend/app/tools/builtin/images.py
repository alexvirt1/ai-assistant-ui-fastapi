"""Generating images from the chat (text-to-image on AI-1's ComfyUI).

generate_image returns a small JSON object, never the image: the frontend
renders the card for this tool from the image id, and the model only needs to
know that it worked. Enabled when COMFYUI_BASE_URL is set.
"""

import json

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from ...identity import single_user_id
from ...images import comfyui, service
from ...images.comfyui import ImageGenerationError
from ..base import ToolSpec, register


@tool
async def generate_image(
    prompt: str,
    config: RunnableConfig,
    model: str = "",
    size: str = "square",
    seed: int = -1,
) -> str:
    """Generate an image from a text description and show it to the user.

    `prompt`: a detailed description of the image in English - subject, setting,
    style, lighting, composition - keeping every detail the user asked for.
    `model`: an image model from list_image_models; leave empty for the default.
    `size`: "square" (default), "portrait", "landscape" or "wide".
    `seed`: leave at -1 for a random image; reuse a previous seed to vary one.
    """
    configurable = (config or {}).get("configurable", {})
    thread_id = configurable.get("thread_id")
    if not thread_id:
        return "Error: images can only be generated inside a saved chat."
    try:
        result = await service.generate_for_chat(
            prompt=prompt,
            thread_id=thread_id,
            user_id=configurable.get("user_id") or single_user_id(),
            model=model or None,
            size=size or None,
            seed=seed,
        )
    except ImageGenerationError as exc:
        return f"Error: {exc}"
    except RuntimeError as exc:
        return f"Error: {exc}"
    return json.dumps(result)


@tool
async def list_image_models() -> str:
    """List the image generation models available, marking the default."""
    names = await comfyui.available_models()
    if not names:
        return "No image models are available: the image server on AI-1 is unreachable."
    default = comfyui.default_model()
    return "Image models:\n" + "\n".join(
        f"- {n}{' (default)' if n == default else ''}: {comfyui.MODELS[n].description}"
        for n in names
    )


_HINT = (
    "When the user asks you to draw, paint, generate, create or show a picture, "
    "image, photo, illustration, logo or similar, call generate_image with a "
    "detailed English description; translate the request if needed and keep all "
    "its details. Pick size portrait/landscape/wide when the subject suggests it. "
    "For paintings, drawings or other art styles use model flux2-klein; leave the "
    "model empty for photos. "
    "Only generate_image can make images: never invent image URLs or use outside "
    "image services such as pollinations.ai. The chat shows each generated image "
    "itself: after the tool returns, reply in one or two sentences and never write "
    "links, URLs, file names, image ids or markdown images (![...](...)). "
    "If the tool returns an error, tell the user what it says. When asked which "
    "image models exist, call list_image_models."
)

for _tool in (generate_image, list_image_models):
    register(ToolSpec(tool=_tool, prompt_hint=_HINT, required_env=("COMFYUI_BASE_URL",)))
