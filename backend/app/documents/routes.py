"""HTTP surface for large documents.

Upload returns the scope estimate immediately - chunking and sizing involve no
model calls, so a 5 MB file is sized in milliseconds and the caller learns
"87 chunks, about 78 minutes" before committing to anything.
"""

import os
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile

from .scope import estimate_scope
from .store import get_document, store_document

router = APIRouter(prefix="/api/documents", tags=["documents"])

# Generous compared with the chat attachment cap: this path exists precisely
# for files too large to inline into a message.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_DOCUMENT_BYTES", str(32 * 1024 * 1024)))

_TEXT_TYPES = ("text/", "application/json", "application/xml")


def _looks_like_text(content_type: str, name: str) -> bool:
    if content_type and content_type.startswith(_TEXT_TYPES):
        return True
    # Browsers report an empty or generic type for .md, .log and friends.
    return name.lower().endswith(
        (".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".yaml",
         ".yml", ".log", ".html", ".rst")
    )


@router.post("")
async def upload_document(file: UploadFile = File(...)):
    """Store a document and report what processing it would cost."""
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{file.filename} is {len(raw) / 1e6:.1f} MB; the limit is "
                f"{MAX_UPLOAD_BYTES / 1e6:.0f} MB."
            ),
        )

    name = file.filename or "untitled"
    if not _looks_like_text(file.content_type or "", name):
        # PDF and DOCX need an extraction step that does not exist yet; saying
        # so is better than storing bytes the pipeline cannot read.
        raise HTTPException(
            status_code=415,
            detail=(
                f"{name} is not a text document. PDF and DOCX extraction is "
                f"not implemented yet."
            ),
        )

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")

    try:
        document = await store_document(name=name, content=content)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    scope = estimate_scope(document.token_estimate, document.chunk_count)
    return {
        "id": str(document.id),
        "name": document.name,
        "size_bytes": document.size_bytes,
        "reused": document.reused,
        "scope": {
            "tokens": scope.tokens,
            "chunks": scope.chunks,
            "estimated_seconds": round(scope.estimated_seconds),
            "estimated_minutes": round(scope.estimated_minutes, 1),
            "tier": scope.tier.value,
            "message": scope.message,
        },
    }


@router.get("/{document_id}")
async def describe_document(document_id: uuid.UUID):
    document = await get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="No such document.")

    scope = estimate_scope(document.token_estimate, document.chunk_count)
    return {
        "id": str(document.id),
        "name": document.name,
        "size_bytes": document.size_bytes,
        "chunks": document.chunk_count,
        "scope": {
            "tokens": scope.tokens,
            "chunks": scope.chunks,
            "estimated_minutes": round(scope.estimated_minutes, 1),
            "tier": scope.tier.value,
            "message": scope.message,
        },
    }
