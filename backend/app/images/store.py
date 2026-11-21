"""Generated images, kept in Postgres next to the chat they belong to.

The PNG lives here, not in the transcript: the checkpointed tool result holds
only the image id, so a conversation's history stays small enough to replay to
the model, and deleting an image is one row update rather than a rewrite of
checkpoint blobs.

Deleting clears the bytes and keeps the row as a tombstone (png NULL,
deleted_at set). The transcript still refers to the id, and the tombstone is
what lets the chat say "Image deleted" instead of failing as if the image had
never existed. Deleting the chat removes its rows entirely (ON DELETE CASCADE).

Every read and write is scoped by user_id, as in app/chats/store.py.
"""

import os
import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg import AsyncConnection
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_images (
    id         UUID PRIMARY KEY,
    thread_id  TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    prompt     TEXT NOT NULL,
    model      TEXT NOT NULL,
    seed       BIGINT NOT NULL,
    width      INTEGER NOT NULL,
    height     INTEGER NOT NULL,
    png        BYTEA,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS chat_images_thread ON chat_images (thread_id);
"""


@dataclass(frozen=True)
class StoredImage:
    id: uuid.UUID
    thread_id: str
    prompt: str
    model: str
    seed: int
    width: int
    height: int
    png: bytes | None
    created_at: datetime
    deleted_at: datetime | None

    @property
    def deleted(self) -> bool:
        return self.deleted_at is not None


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


async def save_image(*, thread_id: str, user_id: str, prompt: str, model: str,
                     seed: int, width: int, height: int, png: bytes) -> uuid.UUID:
    url = database_url()
    if not url:
        raise RuntimeError("Images need DATABASE_URL: there is nowhere to keep them.")
    image_id = uuid.uuid4()
    async with await AsyncConnection.connect(url) as conn:
        await conn.execute(
            "INSERT INTO chat_images (id, thread_id, user_id, prompt, model, seed,"
            " width, height, png) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (image_id, thread_id, user_id, prompt, model, seed, width, height, png),
        )
        await conn.commit()
    return image_id


async def get_image(image_id: uuid.UUID, user_id: str) -> StoredImage | None:
    url = database_url()
    if not url:
        return None
    async with await AsyncConnection.connect(url, row_factory=dict_row) as conn:
        cursor = await conn.execute(
            "SELECT id, thread_id, prompt, model, seed, width, height, png,"
            " created_at, deleted_at FROM chat_images WHERE id = %s AND user_id = %s",
            (image_id, user_id),
        )
        row = await cursor.fetchone()
    return StoredImage(**row) if row else None


async def delete_image(image_id: uuid.UUID, user_id: str) -> bool:
    """Remove the image's bytes for good. False if the user has no such image."""
    url = database_url()
    if not url:
        return False
    async with await AsyncConnection.connect(url) as conn:
        cursor = await conn.execute(
            "UPDATE chat_images SET png = NULL, deleted_at = COALESCE(deleted_at, now())"
            " WHERE id = %s AND user_id = %s",
            (image_id, user_id),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def get_thread_images(thread_id: str, user_id: str) -> dict[str, StoredImage]:
    """A chat's images by id, for the PDF export."""
    url = database_url()
    if not url:
        return {}
    async with await AsyncConnection.connect(url, row_factory=dict_row) as conn:
        cursor = await conn.execute(
            "SELECT id, thread_id, prompt, model, seed, width, height, png,"
            " created_at, deleted_at FROM chat_images"
            " WHERE thread_id = %s AND user_id = %s",
            (thread_id, user_id),
        )
        rows = await cursor.fetchall()
    return {str(row["id"]): StoredImage(**row) for row in rows}
