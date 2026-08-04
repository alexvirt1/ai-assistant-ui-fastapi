"""Indexing a document for retrieval, exactly once.

Two callers race here by design: the frontend fires an index request as soon as
a document is uploaded, and search_document indexes lazily if it finds nothing.
Ask a question five seconds after attaching - normal behaviour - and both see an
empty index and both embed the whole document. That is ~60s of duplicated work
per 5 MB, and save_retrieval_chunks does DELETE-then-INSERT, so the two passes
can interleave.

A per-document lock serialises them: the second caller waits, then finds the
rows the first one wrote and returns immediately.
"""

import asyncio
import logging
import uuid

from .embeddings import embed_chunks, split_for_retrieval
from .store import get_chunks, load_retrieval_chunks, save_retrieval_chunks

logger = logging.getLogger(__name__)

# Keyed by document and embedding model. In-process, which is sufficient here:
# the backend runs as a single uvicorn process, and both racing callers live in
# it. Across multiple workers this would need a Postgres advisory lock instead.
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def ensure_indexed(
    document_id: str,
    embedder,
    embed_model: str,
) -> tuple[int, bool]:
    """Make sure the document is embedded. Returns (chunk count, reused).

    Safe to call concurrently: whoever arrives second waits for the first and
    then takes its result.
    """
    key = f"{document_id}:{embed_model}"

    # Cheap path first: an already-indexed document needs no lock at all.
    stored, _ = await load_retrieval_chunks(document_id, embed_model)
    if stored:
        return len(stored), True

    async with _lock_for(key):
        # Re-check under the lock. The holder may have finished while we waited,
        # which is the whole point of serialising.
        stored, _ = await load_retrieval_chunks(document_id, embed_model)
        if stored:
            return len(stored), True

        logger.info("indexing document %s with %s", document_id, embed_model)
        full_text = "".join(await get_chunks(uuid.UUID(document_id)))
        pieces = split_for_retrieval(full_text)
        if not pieces:
            return 0, False

        vectors = await embed_chunks(pieces, embedder)
        await save_retrieval_chunks(document_id, embed_model, pieces, vectors)
        return len(pieces), False
