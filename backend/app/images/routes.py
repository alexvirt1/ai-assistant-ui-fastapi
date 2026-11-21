"""HTTP surface for generated images.

GET    /api/images/models  the image models AI-1 can run now
GET    /api/images/{id}    the PNG; 404 if not yours, 410 once deleted
DELETE /api/images/{id}    delete it for good (204)

Images are generated from the chat, through the generate_image tool, not from
here.
"""

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response

from ..identity import current_user_id
from . import comfyui, store

router = APIRouter(prefix="/api/images", tags=["images"])


@router.get("/models")
async def list_image_models() -> dict:
    available = await comfyui.available_models()
    return {
        "configured": bool(comfyui.base_url()),
        "default": comfyui.default_model(),
        "models": [
            {"name": name, "description": comfyui.MODELS[name].description}
            for name in available
        ],
    }


def download_name(prompt: str) -> str:
    """A file name from the first words of the prompt, ASCII only."""
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:60].rstrip("-")
    return f"{slug or 'image'}.png"


@router.get("/{image_id}")
async def get_image(image_id: uuid.UUID, user_id: str = Depends(current_user_id)) -> Response:
    image = await store.get_image(image_id, user_id)
    if image is None:
        raise HTTPException(status_code=404, detail="No such image")
    if image.deleted or image.png is None:
        raise HTTPException(status_code=410, detail="Image deleted")
    return Response(
        content=image.png,
        media_type="image/png",
        headers={
            # inline: shown in the chat; the Download button names the file.
            "Content-Disposition": f'inline; filename="{download_name(image.prompt)}"',
            # Private, and short-lived so a deleted image does not linger in
            # the browser cache for long.
            "Cache-Control": "private, max-age=300",
        },
    )


@router.delete("/{image_id}", status_code=204)
async def delete_image(image_id: uuid.UUID, user_id: str = Depends(current_user_id)) -> None:
    if not await store.delete_image(image_id, user_id):
        raise HTTPException(status_code=404, detail="No such image")
