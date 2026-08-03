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
from psycopg.types.json import Jsonb

from .chunker import chunk_text, estimate_tokens
from .reduce import DocumentSummary
from .summaries import ChunkSummary

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

-- Keyed by model and prompt_version as well as position: changing either must
-- invalidate rather than silently serve summaries produced by the old wording.
CREATE TABLE IF NOT EXISTS chunk_summaries (
    document_id     UUID    NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    idx             INTEGER NOT NULL,
    model_name      TEXT    NOT NULL,
    prompt_version  TEXT    NOT NULL,
    topic           TEXT    NOT NULL,
    findings        TEXT    NOT NULL,
    entities        JSONB   NOT NULL DEFAULT '[]',
    uncertain       TEXT    NOT NULL DEFAULT '',
    degraded        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, idx, model_name, prompt_version)
);

-- The finished article. Stored so a second question about the same document
-- costs nothing, and so a completed 78-minute job survives a restart.
CREATE TABLE IF NOT EXISTS document_summaries (
    document_id       UUID    NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    model_name        TEXT    NOT NULL,
    prompt_version    TEXT    NOT NULL,
    overview          TEXT    NOT NULL,
    key_findings      TEXT    NOT NULL,
    outline           JSONB   NOT NULL DEFAULT '[]',
    entities          JSONB   NOT NULL DEFAULT '[]',
    gaps              TEXT    NOT NULL DEFAULT '',
    sections          INTEGER NOT NULL DEFAULT 0,
    degraded_sections INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, model_name, prompt_version)
);

-- Added after the tables shipped, so ALTER rather than a column in CREATE:
-- CREATE TABLE IF NOT EXISTS silently skips an existing table.
ALTER TABLE chunk_summaries    ADD COLUMN IF NOT EXISTS key_facts JSONB NOT NULL DEFAULT '[]';
ALTER TABLE document_summaries ADD COLUMN IF NOT EXISTS key_facts JSONB NOT NULL DEFAULT '[]';

-- Vectors are stored as JSONB rather than a pgvector column: the extension is
-- not installed here and needs OS-level access. Similarity is computed in
-- Python, which is fine for a document's ~87 chunks.
-- Retrieval chunks are far smaller than the summarisation chunks in
-- document_chunks. A 16k-token chunk embeds to a vector dominated by whatever
-- is most common in it, so a single sentence contributes almost nothing; ~400
-- tokens keeps an embedding specific enough to find that sentence.
CREATE TABLE IF NOT EXISTS retrieval_chunks (
    document_id  UUID    NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL,
    text         TEXT    NOT NULL,
    model_name   TEXT    NOT NULL,
    vector       JSONB   NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, idx, model_name)
);

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    document_id  UUID    NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL,
    model_name   TEXT    NOT NULL,
    dimensions   INTEGER NOT NULL,
    vector       JSONB   NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, idx, model_name)
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


async def get_chunks(document_id: uuid.UUID) -> list[str]:
    """Every chunk's text, in order - the map step's input."""
    url = database_url()
    if not url:
        return []
    async with await AsyncConnection.connect(url) as conn:
        rows = await (
            await conn.execute(
                "SELECT text FROM document_chunks WHERE document_id = %s ORDER BY idx",
                (document_id,),
            )
        ).fetchall()
    return [r[0] for r in rows]


async def load_cached_summary(
    document_id: str, idx: int, model_name: str, prompt_version: str
) -> ChunkSummary | None:
    """A previously computed summary, if one exists for this exact key."""
    url = database_url()
    if not url:
        return None
    async with await AsyncConnection.connect(url) as conn:
        conn.row_factory = dict_row
        row = await (
            await conn.execute(
                "SELECT topic, findings, entities, key_facts, uncertain FROM chunk_summaries"
                " WHERE document_id = %s AND idx = %s AND model_name = %s"
                " AND prompt_version = %s",
                (document_id, idx, model_name, prompt_version),
            )
        ).fetchone()
    return ChunkSummary(**row) if row else None


async def save_summary(
    *,
    document_id: str,
    index: int,
    model_name: str,
    prompt_version: str,
    summary: ChunkSummary,
    degraded: bool,
) -> None:
    """Persist one summary immediately.

    Written per chunk rather than batched at the end so a job killed at chunk 60
    keeps the first 59 - the whole point of the cache.
    """
    url = database_url()
    if not url:
        return
    async with await AsyncConnection.connect(url) as conn:
        await conn.execute(
            "INSERT INTO chunk_summaries (document_id, idx, model_name,"
            " prompt_version, topic, findings, entities, key_facts, uncertain,"
            " degraded) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (document_id, idx, model_name, prompt_version)"
            " DO UPDATE SET topic = EXCLUDED.topic, findings = EXCLUDED.findings,"
            " entities = EXCLUDED.entities, key_facts = EXCLUDED.key_facts,"
            " uncertain = EXCLUDED.uncertain, degraded = EXCLUDED.degraded",
            (
                document_id,
                index,
                model_name,
                prompt_version,
                summary.topic,
                summary.findings,
                Jsonb(summary.entities),
                Jsonb(summary.key_facts),
                summary.uncertain,
                degraded,
            ),
        )
        await conn.commit()


async def load_document_summary(
    document_id: str, model_name: str, prompt_version: str
) -> DocumentSummary | None:
    """A finished summary, if this document has already been reduced."""
    url = database_url()
    if not url:
        return None
    async with await AsyncConnection.connect(url) as conn:
        conn.row_factory = dict_row
        row = await (
            await conn.execute(
                "SELECT overview, key_findings, outline, entities, key_facts, gaps, sections,"
                " degraded_sections FROM document_summaries WHERE document_id = %s"
                " AND model_name = %s AND prompt_version = %s",
                (document_id, model_name, prompt_version),
            )
        ).fetchone()
    return DocumentSummary(**row) if row else None


async def save_document_summary(
    document_id: str,
    model_name: str,
    prompt_version: str,
    summary: DocumentSummary,
) -> None:
    url = database_url()
    if not url:
        return
    async with await AsyncConnection.connect(url) as conn:
        await conn.execute(
            "INSERT INTO document_summaries (document_id, model_name,"
            " prompt_version, overview, key_findings, outline, entities,"
            " key_facts, gaps, sections, degraded_sections)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (document_id, model_name, prompt_version) DO UPDATE SET"
            " overview = EXCLUDED.overview, key_findings = EXCLUDED.key_findings,"
            " outline = EXCLUDED.outline, entities = EXCLUDED.entities,"
            " key_facts = EXCLUDED.key_facts, gaps = EXCLUDED.gaps, sections = EXCLUDED.sections,"
            " degraded_sections = EXCLUDED.degraded_sections",
            (
                document_id,
                model_name,
                prompt_version,
                summary.overview,
                summary.key_findings,
                Jsonb(summary.outline),
                Jsonb(summary.entities),
                Jsonb(summary.key_facts),
                summary.gaps,
                summary.sections,
                summary.degraded_sections,
            ),
        )
        await conn.commit()


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


async def save_embeddings(
    document_id: str, model_name: str, vectors: list[list[float]]
) -> None:
    """Persist one vector per chunk, replacing any earlier run for this model."""
    url = database_url()
    if not url or not vectors:
        return
    async with await AsyncConnection.connect(url) as conn:
        await conn.cursor().executemany(
            "INSERT INTO chunk_embeddings (document_id, idx, model_name,"
            " dimensions, vector) VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (document_id, idx, model_name) DO UPDATE SET"
            " vector = EXCLUDED.vector, dimensions = EXCLUDED.dimensions",
            [
                (document_id, i, model_name, len(v), Jsonb(v))
                for i, v in enumerate(vectors)
            ],
        )
        await conn.commit()


async def load_embeddings(
    document_id: str, model_name: str
) -> list[tuple[int, list[float]]]:
    """Every stored vector for a document, as (chunk index, vector)."""
    url = database_url()
    if not url:
        return []
    async with await AsyncConnection.connect(url) as conn:
        rows = await (
            await conn.execute(
                "SELECT idx, vector FROM chunk_embeddings"
                " WHERE document_id = %s AND model_name = %s ORDER BY idx",
                (document_id, model_name),
            )
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


async def count_embeddings(document_id: str, model_name: str) -> int:
    url = database_url()
    if not url:
        return 0
    async with await AsyncConnection.connect(url) as conn:
        row = await (
            await conn.execute(
                "SELECT count(*) FROM chunk_embeddings"
                " WHERE document_id = %s AND model_name = %s",
                (document_id, model_name),
            )
        ).fetchone()
    return row[0] if row else 0


async def save_retrieval_chunks(
    document_id: str, model_name: str, texts: list[str], vectors: list[list[float]]
) -> None:
    url = database_url()
    if not url or not texts:
        return
    async with await AsyncConnection.connect(url) as conn:
        await conn.execute(
            "DELETE FROM retrieval_chunks WHERE document_id = %s AND model_name = %s",
            (document_id, model_name),
        )
        await conn.cursor().executemany(
            "INSERT INTO retrieval_chunks (document_id, idx, text, model_name, vector)"
            " VALUES (%s, %s, %s, %s, %s)",
            [
                (document_id, i, t, model_name, Jsonb(v))
                for i, (t, v) in enumerate(zip(texts, vectors))
            ],
        )
        await conn.commit()


async def load_retrieval_chunks(
    document_id: str, model_name: str
) -> tuple[list[tuple[int, list[float]]], dict[int, str]]:
    """Vectors and texts for retrieval, as (index, vector) pairs plus a lookup."""
    url = database_url()
    if not url:
        return [], {}
    async with await AsyncConnection.connect(url) as conn:
        rows = await (
            await conn.execute(
                "SELECT idx, vector, text FROM retrieval_chunks"
                " WHERE document_id = %s AND model_name = %s ORDER BY idx",
                (document_id, model_name),
            )
        ).fetchall()
    return [(r[0], r[1]) for r in rows], {r[0]: r[2] for r in rows}
