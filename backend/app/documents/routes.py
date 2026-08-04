"""HTTP surface for large documents.

Upload returns the scope estimate immediately - chunking and sizing involve no
model calls, so a 5 MB file is sized in milliseconds and the caller learns
"87 chunks, about 78 minutes" before committing to anything.
"""

import os
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile
from langchain_core.messages import HumanMessage, SystemMessage

from pydantic import BaseModel

from .callers import make_callers, make_reduce_caller
from .embeddings import embed_query, make_embedder
from .indexing import ensure_indexed
from .retrieval import build_context, rank_chunks
from .jobs import registry, run_summary
from .mapper import map_document
from .reduce import REDUCE_PROMPT_VERSION
from .reducer import reduce_document
from .scope import estimate_scope
from .store import (
    get_chunks,
    get_document,
    load_retrieval_chunks,
    load_cached_summary,
    load_document_summary,
    save_document_summary,
    save_summary,
    store_document,
)
from .summaries import PROMPT_VERSION

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


def _job_payload(job) -> dict:
    payload = {
        "job_id": job.id,
        "document_id": job.document_id,
        "status": job.status.value,
        "phase": job.phase.value,
        "completed": job.completed,
        "total": job.total,
        "fraction": round(job.fraction, 3),
        "degraded": job.degraded,
        "cached": job.cached,
        "elapsed_seconds": round(job.elapsed_seconds),
        "description": job.describe(),
    }
    eta = job.eta_seconds()
    if eta is not None:
        payload["eta_seconds"] = round(eta)
    if job.error:
        payload["error"] = job.error
    if job.result is not None:
        payload["summary"] = {
            "overview": job.result.overview,
            "key_findings": job.result.key_findings,
            "outline": job.result.outline,
            "entities": job.result.entities,
            "key_facts": job.result.key_facts,
            "gaps": job.result.gaps,
            "sections": job.result.sections,
            "degraded_sections": job.result.degraded_sections,
        }
    return payload


@router.post("/{document_id}/summarize")
async def start_summary(document_id: uuid.UUID, force: bool = False):
    """Kick off a map-reduce summary and return immediately.

    A finished summary is returned straight from storage unless `force=true`,
    because re-running 78 minutes of work to produce the same text would be a
    poor default.
    """
    document = await get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="No such document.")

    map_caller, plain_caller, map_model = make_callers()
    reduce_caller, reduce_model = make_reduce_caller()
    version = f"{PROMPT_VERSION}/{REDUCE_PROMPT_VERSION}"

    if not force:
        cached = await load_document_summary(str(document_id), reduce_model, version)
        if cached is not None:
            return {"status": "completed", "cached": True, "summary": cached.__dict__}

    running = registry.for_document(str(document_id))
    if running is not None:
        return _job_payload(running)

    chunks = await get_chunks(document_id)
    if not chunks:
        raise HTTPException(status_code=409, detail="Document has no chunks.")

    async def run(job):
        async def map_fn(chunks, on_progress):
            return await map_document(
                str(document_id), chunks, map_caller, plain_caller,
                model_name=map_model,
                load_cached=load_cached_summary, save_summary=save_summary,
                on_progress=on_progress,
            )

        async def reduce_fn(summaries, on_progress):
            result = await reduce_document(summaries, reduce_caller, on_progress=on_progress)
            await save_document_summary(str(document_id), reduce_model, version, result)
            return result

        return await run_summary(job, chunks, map_fn, reduce_fn)

    job = registry.start(str(document_id), run)
    return _job_payload(job)


@router.get("/jobs/{job_id}")
async def job_status(job_id: str):
    """Poll a job. Cheap enough to call every second."""
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job.")
    return _job_payload(job)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Stop a running job. Chunk summaries already computed are kept."""
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job.")
    cancelled = await registry.cancel(job_id)
    if not cancelled:
        raise HTTPException(
            status_code=409, detail=f"Job is {job.status.value}, not running."
        )
    return _job_payload(job)


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


@router.post("/{document_id}/index")
async def index_document(document_id: uuid.UUID):
    """Embed every chunk so the document can be questioned.

    A one-off cost per document. Cheap next to summarising: embedding is a
    single batched call rather than one generation per chunk.
    """
    document = await get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="No such document.")

    embedder, embed_model = make_embedder()
    # Shared with search_document's lazy path, and serialised: the frontend
    # fires this on upload while a question may trigger the lazy one, and both
    # would otherwise embed the whole document.
    count, reused = await ensure_indexed(str(document_id), embedder, embed_model)
    return {"indexed": count, "model": embed_model, "reused": reused}


class Question(BaseModel):
    question: str
    top_k: int = 5


@router.post("/{document_id}/ask")
async def ask_document(document_id: uuid.UUID, body: Question):
    """Answer a question from the most relevant chunks.

    This is the counterpart to summarising, not a replacement: a summary
    compresses ~130:1 and loses isolated facts, while retrieval reads the
    original text of whichever sections actually mention the subject.
    """
    document = await get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="No such document.")

    embedder, embed_model = make_embedder()
    stored, texts = await load_retrieval_chunks(str(document_id), embed_model)
    if not stored:
        raise HTTPException(
            status_code=409,
            detail="Document is not indexed; POST /index first.",
        )

    query_vector = await embed_query(body.question, embedder)
    matches = rank_chunks(query_vector, stored, texts, top_k=body.top_k)

    context = build_context(matches)
    structured, plain, answer_model = make_callers()
    answer = await plain([
        SystemMessage(
            "Answer using only the document sections provided. Quote specific "
            "values exactly. If the sections do not contain the answer, say so "
            "rather than guessing."
        ),
        HumanMessage(
            f"Sections from the document:\n\n{context}\n\n"
            f"Question: {body.question}"
        ),
    ])

    return {
        "question": body.question,
        "answer": answer,
        "model": answer_model,
        "sources": [
            {"section": m.index + 1, "score": round(m.score, 4)} for m in matches
        ],
    }
