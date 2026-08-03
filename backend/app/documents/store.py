"""Postgres storage for uploaded documents and their chunks.

A large document must not travel through the chat message: inlined text is
persisted into the LangGraph checkpoint and re-sent on every later turn, so a
5 MB attachment would poison the thread permanently. It is stored here instead,
and the conversation carries only a reference.

Content is deduplicated by SHA-256. Re-uploading the same file reuses the
existing chunks - and, once phase 2 lands, the cached per-chunk summaries that
cost 80 minutes to produce.
"""

import hashlib
import os
import uuid
from dataclasses import dataclass

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from .chunker import chunk_text, estimate_tokens

# Kept in one place so the schema is greppable; created idempotently at startup.
SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id              UUID PRIMARY KEY,
    name            TEXT        NOT NULL,
    content_type    TEXT        NOT NULL DEFAULT 'text/plain',
    size_bytes      BIGINT      NOT NULL,
    sha256          TEXT        NOT NULL UNIQUE,
    token_estimate  INTEGER     NOT NULL,
    chunk_count     INTEGER     NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_chunks (
    document_id     UUID    NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    idx             INTEGER NOT NULL,
    text            TEXT    NOT NULL,
    token_estimate  INTEGER NOT NULL,
    PRIMARY KEY (document_id, idx)
);
"""


@dataclass(frozen=True)
class StoredDocument:
    id: uuid.UUID
    name: str
    size_bytes: int
    sha256: str
    token_estimate: int
    chunk_count: int
    reused: bool


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


async def setup() -> None:
    """Create the tables if they are absent.

    Called from the FastAPI lifespan alongside the checkpointer's own setup. A
    no-op when DATABASE_URL is unset, matching how conversation persistence
    already degrades rather than failing startup.
    """
    url = database_url()
    if not url:
        return
    async with await AsyncConnection.connect(url) as conn:
        await conn.execute(SCHEMA)
        await conn.commit()


async def store_document(
    name: str,
    content: str,
    content_type: str = "text/plain",
    max_tokens: int = 16_000,
    overlap_tokens: int = 800,
) -> StoredDocument:
    """Persist a document and its chunks, or return the existing one.

    Chunking happens here rather than at map time so the cost is paid once and
    the chunk boundaries stay stable across runs - a per-chunk summary cache is
    worthless if the chunks move.
    """
    url = database_url()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set; document storage requires Postgres."
        )

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

    async with await AsyncConnection.connect(url) as conn:
        conn.row_factory = dict_row

        existing = await (
            await conn.execute(
                "SELECT id, name, size_bytes, sha256, token_estimate, chunk_count "
                "FROM documents WHERE sha256 = %s",
                (digest,),
            )
        ).fetchone()
        if existing:
            return StoredDocument(reused=True, **existing)

        chunks = chunk_text(content, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
        document_id = uuid.uuid4()
        tokens = estimate_tokens(content)
        size = len(content.encode("utf-8"))

        await conn.execute(
            "INSERT INTO documents (id, name, content_type, size_bytes, sha256,"
            " token_estimate, chunk_count) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (document_id, name, content_type, size, digest, tokens, len(chunks)),
        )
        # executemany keeps an 87-chunk insert to a single round trip.
        await conn.cursor().executemany(
            "INSERT INTO document_chunks (document_id, idx, text, token_estimate)"
            " VALUES (%s, %s, %s, %s)",
            [(document_id, c.index, c.text, c.tokens) for c in chunks],
        )
        await conn.commit()

        return StoredDocument(
            id=document_id,
            name=name,
            size_bytes=size,
            sha256=digest,
            token_estimate=tokens,
            chunk_count=len(chunks),
            reused=False,
        )


async def get_document(document_id: uuid.UUID) -> StoredDocument | None:
    url = database_url()
    if not url:
        return None
    async with await AsyncConnection.connect(url) as conn:
        conn.row_factory = dict_row
        row = await (
            await conn.execute(
                "SELECT id, name, size_bytes, sha256, token_estimate, chunk_count "
                "FROM documents WHERE id = %s",
                (document_id,),
            )
        ).fetchone()
    return StoredDocument(reused=False, **row) if row else None


async def get_chunk(document_id: uuid.UUID, idx: int) -> str | None:
    """One chunk's text - what the map step will consume, one call at a time."""
    url = database_url()
    if not url:
        return None
    async with await AsyncConnection.connect(url) as conn:
        row = await (
            await conn.execute(
                "SELECT text FROM document_chunks WHERE document_id = %s AND idx = %s",
                (document_id, idx),
            )
        ).fetchone()
    return row[0] if row else None
