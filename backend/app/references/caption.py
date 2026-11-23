"""One-sentence visual captions of reference photos, by a vision model on AI-1.

FLUX.2 Klein follows the text prompt and treats reference photos as guides: a
subject the prompt does not describe comes out generic (tested on AI-1: "Davy
Jones from image 2" drew an old man; "the sea captain whose beard is octopus
tentacles from image 2" drew him). The chat model writes that prompt and
cannot see the photos, so each photo gets a caption, appended to the prompt as
"Image 2 shows: ..." and listed for the chat model in later turns.

Runs inside an image job, when the chat model is already unloaded, and only
for photos without a caption yet; the caption is stored with the photo.
keep_alive 0 so the vision model leaves the GPU as soon as it has answered.
Measured on AI-1: about 6 s to load qwen3-vl:8b, then 3 s per photo.
"""

import base64
import logging
import os
import re
from io import BytesIO

import httpx
from PIL import Image

from ..models import ollama

logger = logging.getLogger(__name__)

CAPTION_SIDE = 1024
MAX_CAPTION_CHARS = 400

PROMPT = (
    "Describe the main subject of this photo for an image generator, in one sentence of "
    "at most 45 words. Say what it is (a man, a woman, a child, a creature, an animal, an "
    "object) and what makes it recognisable: face, skin, hair, facial hair, headwear, "
    "clothing, colours, distinctive features. Describe only what is visible. No names, no "
    "background, no opinions."
)


def caption_model() -> str:
    """The vision model; empty turns captioning off."""
    return os.getenv("REFERENCE_CAPTION_MODEL", "qwen3-vl:8b").strip()


def timeout_seconds() -> float:
    return float(os.getenv("REFERENCE_CAPTION_TIMEOUT", "120"))


def _jpeg_b64(png: bytes) -> str:
    with Image.open(BytesIO(png)) as image:
        image = image.convert("RGB")
        image.thumbnail((CAPTION_SIDE, CAPTION_SIDE))
        out = BytesIO()
        image.save(out, format="JPEG", quality=90)
    return base64.b64encode(out.getvalue()).decode("ascii")


def clean(text: str) -> str:
    """One plain line: no thinking block, quotes, markdown or line breaks."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    text = re.sub(r"[*_`#]+", "", text)
    text = " ".join(text.split()).strip(" \"'")
    if len(text) > MAX_CAPTION_CHARS:
        text = text[:MAX_CAPTION_CHARS].rsplit(" ", 1)[0] + "…"
    return text


async def caption(png: bytes, client: httpx.AsyncClient | None = None) -> str | None:
    """A caption for one photo, or None if the vision model could not give one."""
    model = caption_model()
    if not model:
        return None
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout_seconds())
    try:
        resp = await client.post(f"{ollama.BASE_URL}/api/chat", json={
            "model": model,
            "messages": [
                {"role": "user", "content": PROMPT, "images": [_jpeg_b64(png)]},
                # qwen3-vl reasons first even with think off, and spent the
                # whole num_predict on it (seen live: empty answer). An empty
                # thinking block, continued by the model, skips that.
                {"role": "assistant", "content": "<think>\n\n</think>\n\n"},
            ],
            "stream": False,
            "think": False,
            "keep_alive": 0,
            "options": {"num_ctx": 4096, "temperature": 0.2, "num_predict": 100},
        })
        resp.raise_for_status()
        text = clean(resp.json().get("message", {}).get("content", ""))
        return text or None
    except (httpx.HTTPError, ValueError, OSError) as exc:
        logger.warning("could not caption a reference photo with %s: %s", model, exc)
        return None
    finally:
        if owns_client:
            await client.aclose()


def prompt_with_captions(prompt: str, captions: list[str | None]) -> str:
    """The image prompt with what each reference shows, by image number."""
    lines = [f"Image {i} shows: {text}" for i, text in enumerate(captions, 1) if text]
    if not lines:
        return prompt
    return prompt.rstrip() + "\n\n" + "\n".join(lines)
