"""Reference photos, kept in Postgres with the chat they were attached to.

Like generated images (app/images/store.py): deleting one clears its bytes and
keeps the row as a tombstone, so the chat can say "Reference deleted"; deleting
the chat removes its rows (ON DELETE CASCADE). Every read and write is scoped
by user_id.
"""

import os
import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg import AsyncConnection
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_references (
    id         UUID PRIMARY KEY,
    thread_id  TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    width      INTEGER NOT NULL,
    height     INTEGER NOT NULL,
    png        BYTEA,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS chat_references_thread ON chat_references (thread_id);
-- What the photo shows, written by a vision model the first time it is used (1.11.0).
ALTER TABLE chat_references ADD COLUMN IF NOT EXISTS caption TEXT;
"""

COLUMNS = "id, thread_id, name, width, height, png, created_at, deleted_at, caption"


@dataclass(frozen=True)
class StoredReference:
    id: uuid.UUID
    thread_id: str
    name: str
    width: int
    height: int
    png: bytes | None
    created_at: datetime
    deleted_at: datetime | None
    caption: str | None = None

    @property
    def deleted(self) -> bool:
        return self.deleted_at is not None or self.png is None

    def describe(self) -> dict:
        return {"id": str(self.id), "name": self.name, "width": self.width,
                "height": self.height, "createdAt": self.created_at.isoformat(),
                "deleted": self.deleted, "caption": self.caption}


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


async def setup() -> None:
    """Create the table. Runs after chats.store.setup(), for the foreign key."""
    url = database_url()
    if not url:
        return
    async with await AsyncConnection.connect(url) as conn:
        await conn.execute(SCHEMA)
        await conn.commit()


async def save(*, thread_id: str, user_id: str, name: str, png: bytes,
               width: int, height: int) -> StoredReference:
    url = database_url()
    if not url:
        raise RuntimeError("References need DATABASE_URL: there is nowhere to keep them.")
    ref_id = uuid.uuid4()
    async with await AsyncConnection.connect(url, row_factory=dict_row) as conn:
        row = await (await conn.execute(
            "INSERT INTO chat_references (id, thread_id, user_id, name, width, height, png)"
            f" VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {COLUMNS}",
            (ref_id, thread_id, user_id, name[:200], width, height, png),
        )).fetchone()
        await conn.commit()
    return StoredReference(**row)


async def get(ref_id: uuid.UUID, user_id: str) -> StoredReference | None:
    url = database_url()
    if not url:
        return None
    async with await AsyncConnection.connect(url, row_factory=dict_row) as conn:
        row = await (await conn.execute(
            f"SELECT {COLUMNS} FROM chat_references WHERE id = %s AND user_id = %s",
            (ref_id, user_id),
        )).fetchone()
    return StoredReference(**row) if row else None


async def delete(ref_id: uuid.UUID, user_id: str) -> bool:
    """Remove the photo's bytes for good. False if the user has no such reference."""
    url = database_url()
    if not url:
        return False
    async with await AsyncConnection.connect(url) as conn:
        cursor = await conn.execute(
            "UPDATE chat_references SET png = NULL, deleted_at = COALESCE(deleted_at, now())"
            " WHERE id = %s AND user_id = %s",
            (ref_id, user_id),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def set_caption(ref_id: uuid.UUID, caption: str) -> None:
    """Keep what the photo shows, so it is captioned only once."""
    url = database_url()
    if not url:
        return
    async with await AsyncConnection.connect(url) as conn:
        await conn.execute(
            "UPDATE chat_references SET caption = %s WHERE id = %s AND deleted_at IS NULL",
            (caption, ref_id),
        )
        await conn.commit()


async def list_for_thread(thread_id: str, user_id: str, *,
                          include_deleted: bool = False) -> list[StoredReference]:
    url = database_url()
    if not url:
        return []
    sql = f"SELECT {COLUMNS} FROM chat_references WHERE thread_id = %s AND user_id = %s"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    async with await AsyncConnection.connect(url, row_factory=dict_row) as conn:
        rows = await (await conn.execute(sql + " ORDER BY created_at",
                                         (thread_id, user_id))).fetchall()
    return [StoredReference(**row) for row in rows]
