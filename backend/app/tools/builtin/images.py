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
    reference_ids: list[str] | None = None,
) -> str:
    """Generate an image from a text description and show it to the user.

    `prompt`: a detailed description of the image in English - subject, setting,
    style, lighting, composition - keeping every detail the user asked for.
    `model`: an image model from list_image_models; leave empty for the default.
    `size`: "square" (default), "portrait", "landscape" or "wide".
    `seed`: leave at -1 for a random image; reuse a previous seed to vary one.
    `reference_ids`: ids of reference photos attached to this chat (up to 4) when
    the image should look like them - a person, an animal, an object, a colour
    scheme. They are image 1, image 2, ... in this order; the prompt must name
    each subject by its image number with a short visual description.
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
            reference_ids=reference_ids,
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
    "When reference photos are attached to the chat and the user wants an image "
    "that looks like them (this dog, my face, that vase), pass their ids as "
    "reference_ids - preferably 1 or 2, at most 4 (with 3-4 each is used at lower "
    "resolution and details are less faithful); one photo per subject is enough. "
    "The photos are image 1, image 2, ... in the order of reference_ids. In the "
    "prompt, name EVERY referenced subject by its image number with a short visual "
    "description taken from what the user said or the photo's file name, e.g. "
    "\"the pirate with a red bandana and beaded dreadlocks from image 1 and the sea "
    "captain whose beard is octopus tentacles from image 2 sit together at a tavern "
    "table\". Never turn a referenced subject into a generic one (\"a friend\", "
    "\"another pirate\"): the image follows the text, and a subject the prompt does "
    "not describe comes out generic. If you can't tell who or what a photo shows, "
    "ask the user before generating. "
    "If the tool returns an error, tell the user what it says. When asked which "
    "image models exist, call list_image_models."
)

for _tool in (generate_image, list_image_models):
    register(ToolSpec(tool=_tool, prompt_hint=_HINT, required_env=("COMFYUI_BASE_URL",)))
