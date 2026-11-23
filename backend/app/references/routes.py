"""HTTP surface for reference photos.

POST   /api/references             upload (multipart: file, threadId); returns its summary
GET    /api/references/{id}        the stored PNG; 404 if not yours, 410 once deleted
DELETE /api/references/{id}        delete it for good (204)
GET    /api/chats/{id}/references  the chat's references (in app/chats/routes.py)

An upload can come before the chat's first message - the photo is attached in
the composer - so it registers the thread, as the chat route does on a first
turn; the title is filled in by that turn.
"""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from ..chats import store as chat_store
from ..identity import current_user_id
from . import normalize as norm
from . import store

router = APIRouter(prefix="/api/references", tags=["references"])


@router.post("")
async def upload_reference(
    file: UploadFile = File(...),
    threadId: str = Form(..., min_length=1, max_length=200),
    user_id: str = Depends(current_user_id),
) -> dict:
    raw = await file.read(norm.MAX_UPLOAD_BYTES + 1)
    try:
        image = norm.normalize(raw)
    except norm.ReferenceError as exc:
        status = 413 if len(raw) > norm.MAX_UPLOAD_BYTES else 415
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    try:
        await chat_store.claim_thread(threadId, user_id)
    except chat_store.ThreadOwnershipError as exc:
        raise HTTPException(status_code=403, detail="Not your chat") from exc
    try:
        saved = await store.save(thread_id=threadId, user_id=user_id,
                                 name=file.filename or "image", png=image.png,
                                 width=image.width, height=image.height)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return saved.describe()


@router.get("/{ref_id}")
async def get_reference(ref_id: uuid.UUID, user_id: str = Depends(current_user_id)) -> Response:
    ref = await store.get(ref_id, user_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="No such reference")
    if ref.deleted:
        raise HTTPException(status_code=410, detail="Reference deleted")
    return Response(content=ref.png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=300"})


@router.delete("/{ref_id}", status_code=204)
async def delete_reference(ref_id: uuid.UUID, user_id: str = Depends(current_user_id)) -> None:
    if not await store.delete(ref_id, user_id):
        raise HTTPException(status_code=404, detail="No such reference")
