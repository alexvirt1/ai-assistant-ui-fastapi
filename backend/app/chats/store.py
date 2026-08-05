"""Postgres registry for chat threads.

The transcript is *not* here. It stays in the LangGraph checkpointer, keyed by
the same thread id, so there is exactly one copy of a conversation and no
dual-write to drift. This table holds what the checkpointer has no concept of:
who owns a thread, what to call it in a sidebar, and when it last moved.

Ownership is the reason this exists at all. The checkpointer will happily load
any thread id handed to it, so without a registry, posting someone else's
thread id would pull their transcript into the model's context. Every read and
write below is scoped by user_id for that reason - see app/identity.py.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime

from psycopg import AsyncConnection
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

# Kept in one place so the schema is greppable; created idempotently at
# startup, matching app/documents/store.py.
SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_threads (
    id         TEXT PRIMARY KEY,
    -- TEXT rather than UUID: identity will arrive from an identity provider
    -- as an opaque subject claim, not as a UUID, and there is deliberately no
    -- FK - Postgres is not going to be the identity store.
    --
    -- NOT NULL DEFAULT rather than nullable: a nullable owner nothing filters
    -- on is how "add auth later" turns into auditing every query. With a
    -- sentinel there is never an unowned row, and every statement below
    -- already carries its WHERE user_id.
    user_id    TEXT        NOT NULL DEFAULT 'alice',
    title      TEXT        NOT NULL DEFAULT '',
    -- The first user message, kept verbatim (truncated) so search can match
    -- what was asked, not just the shortened title.
    preview    TEXT        NOT NULL DEFAULT '',
    turn_count INTEGER     NOT NULL DEFAULT 0,
    archived   BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- user_id leads because the sidebar's query is always "this user's threads,
-- newest first". Putting it in front of an existing index later means
-- rebuilding the index.
CREATE INDEX IF NOT EXISTS chat_threads_owner_recent_idx
    ON chat_threads (user_id, archived, updated_at DESC);
"""

# Trigram search over title+preview, scoped by owner in the same index.
# btree_gin is what lets a plain scalar (user_id) share a GIN index with the
# trigram expression, so one index scan can answer
# "user_id = %s AND (title||preview) ILIKE %s" - measured on 20k rows, the
# Index Cond does carry both.
#
# It will not be chosen at small thread counts, and that is fine. When one
# user owns every row, a btree on user_id is just as selective and cheaper,
# so the planner prefers chat_threads_owner_recent_idx and applies ILIKE as a
# filter (measured: seq scan at 2k rows, owner btree + filter at 20k). This
# index is insurance for a list that grows past that, not a day-one win - at
# a few hundred threads every plan is sub-millisecond anyway.
SEARCH_INDEX = """
CREATE INDEX IF NOT EXISTS chat_threads_owner_search_idx ON chat_threads
    USING GIN (user_id, (title || ' ' || preview) gin_trgm_ops);
"""

SEARCH_EXTENSIONS = ("pg_trgm", "btree_gin")

# Long enough to be a recognisable sentence fragment in a 16rem sidebar,
# short enough not to wrap to three lines.
TITLE_MAX_CHARS = 60
# Only the head of a long paste is worth searching; the tail is usually
# quoted material rather than the question.
PREVIEW_MAX_CHARS = 2000


class ThreadOwnershipError(Exception):
    """A thread id was presented by someone who does not own it."""


@dataclass(frozen=True)
class ChatThread:
    id: str
    user_id: str
    title: str
    preview: str
    turn_count: int
    archived: bool
    created_at: datetime
    updated_at: datetime


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


def derive_title(text: str) -> str:
    """A sidebar label from the first prompt.

    First non-blank line rather than the first N characters: a message that
    opens with a blank line or a heading would otherwise be titled with
    whitespace. No model call - the title has to exist before the answer
    starts streaming, and a wrong-but-instant title beats a good one that
    appears thirty seconds later.
    """
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if len(first) <= TITLE_MAX_CHARS:
        return first
    # Cut at a word boundary when there is one nearby, so the label does not
    # end mid-word for the sake of two characters.
    head = first[: TITLE_MAX_CHARS - 1]
    space = head.rfind(" ")
    if space > TITLE_MAX_CHARS // 2:
        head = head[:space]
    return head.rstrip() + "…"


async def setup() -> None:
    """Create the table, indexes and search extensions if absent.

    A no-op without DATABASE_URL, matching how conversation persistence and
    document storage already degrade rather than failing startup.
    """
    url = database_url()
    if not url:
        return

    async with await AsyncConnection.connect(url) as conn:
        await conn.execute(SCHEMA)
        await conn.commit()

        created = []
        for extension in SEARCH_EXTENSIONS:
            if await _ensure_extension(conn, extension):
                created.append(extension)

        if len(created) == len(SEARCH_EXTENSIONS):
            await conn.execute(SEARCH_INDEX)
            await conn.commit()
        else:
            # Search still works - ILIKE without an index it can use. Saying so
            # once at startup beats wondering later why it got slow.
            logger.warning(
                "chat search index skipped (missing %s); falling back to an "
                "unindexed scan",
                ", ".join(e for e in SEARCH_EXTENSIONS if e not in created),
            )


async def _ensure_extension(conn: AsyncConnection, name: str) -> bool:
    """CREATE EXTENSION, tolerating a server that cannot offer it.

    pg_trgm and btree_gin are both trusted extensions (PG13+), so the database
    owner can create them without superuser. A managed Postgres that disallows
    it, or one built without contrib, must not take startup down with it.
    """
    available = await (
        await conn.execute(
            "SELECT 1 FROM pg_available_extensions WHERE name = %s", (name,)
        )
    ).fetchone()
    if not available:
        return False

    try:
        await conn.execute(f"CREATE EXTENSION IF NOT EXISTS {name}")
        await conn.commit()
        return True
    except Exception:
        # A failed statement leaves the connection in an aborted transaction;
        # without this rollback every later statement would fail too.
        await conn.rollback()
        logger.warning("could not create extension %s", name, exc_info=True)
        return False


async def claim_thread(
    thread_id: str,
    user_id: str,
    *,
    title: str = "",
    preview: str = "",
) -> None:
    """Register a thread to a user, or verify they already own it.

    Called on every chat turn, before the graph runs. On the first turn it
    creates the row and names it; afterwards it is an ownership check.

    Raises ThreadOwnershipError when the id belongs to someone else. Under a
    single user that never fires - which is the point of installing it now,
    while the hot path is easy to change.
    """
    url = database_url()
    if not url:
        return

    async with await AsyncConnection.connect(url) as conn:
        conn.row_factory = dict_row

        await conn.execute(
            "INSERT INTO chat_threads (id, user_id, title, preview)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (thread_id, user_id, title, preview[:PREVIEW_MAX_CHARS]),
        )
        # A thread that predates the registry (or was created by the backfill)
        # has no title; fill it from the first turn that follows rather than
        # leaving it "Untitled" forever. Guarded on title = '' so a rename is
        # never overwritten.
        if title:
            await conn.execute(
                "UPDATE chat_threads SET title = %s, preview = %s"
                " WHERE id = %s AND user_id = %s AND title = ''",
                (title, preview[:PREVIEW_MAX_CHARS], thread_id, user_id),
            )
        await conn.commit()

        row = await (
            await conn.execute(
                "SELECT user_id FROM chat_threads WHERE id = %s", (thread_id,)
            )
        ).fetchone()

    if row is not None and row["user_id"] != user_id:
        raise ThreadOwnershipError(thread_id)


async def touch_thread(thread_id: str, user_id: str) -> None:
    """Record that a turn happened: bumps ordering and the turn counter.

    Counting turns rather than messages keeps this to a single UPDATE. A
    message count would mean reading the checkpoint back to see how many tool
    calls the turn produced, on every turn, to populate a sidebar number.
    """
    url = database_url()
    if not url:
        return

    async with await AsyncConnection.connect(url) as conn:
        await conn.execute(
            "UPDATE chat_threads SET updated_at = now(), turn_count = turn_count + 1"
            " WHERE id = %s AND user_id = %s",
            (thread_id, user_id),
        )
        await conn.commit()


async def list_threads(
    user_id: str,
    query: str | None = None,
    limit: int = 30,
    offset: int = 0,
    include_archived: bool = False,
) -> list[ChatThread]:
    url = database_url()
    if not url:
        return []

    sql = [
        "SELECT id, user_id, title, preview, turn_count, archived, created_at,"
        " updated_at FROM chat_threads WHERE user_id = %(user_id)s"
    ]
    params: dict = {"user_id": user_id, "limit": limit, "offset": offset}

    if not include_archived:
        sql.append(" AND NOT archived")

    if query:
        # Matches the search index expression exactly; a different expression
        # here would silently stop using it.
        sql.append(
            " AND (title || ' ' || preview) ILIKE '%%' || %(query)s || '%%'"
        )
        params["query"] = query

    sql.append(" ORDER BY updated_at DESC LIMIT %(limit)s OFFSET %(offset)s")

    async with await AsyncConnection.connect(url) as conn:
        conn.row_factory = dict_row
        rows = await (await conn.execute("".join(sql), params)).fetchall()

    return [ChatThread(**row) for row in rows]


async def get_thread(thread_id: str, user_id: str) -> ChatThread | None:
    """The thread if this user owns it, else None.

    Returning None for "someone else's" as well as "does not exist" is
    deliberate: the caller turns both into a 404, so a probe cannot tell an
    id that is taken from one that is free.
    """
    url = database_url()
    if not url:
        return None

    async with await AsyncConnection.connect(url) as conn:
        conn.row_factory = dict_row
        row = await (
            await conn.execute(
                "SELECT id, user_id, title, preview, turn_count, archived,"
                " created_at, updated_at FROM chat_threads"
                " WHERE id = %s AND user_id = %s",
                (thread_id, user_id),
            )
        ).fetchone()

    return ChatThread(**row) if row else None


async def update_thread(
    thread_id: str,
    user_id: str,
    *,
    title: str | None = None,
    archived: bool | None = None,
) -> ChatThread | None:
    """Rename and/or (un)archive. Returns the updated row, or None if not theirs."""
    url = database_url()
    if not url:
        return None

    sets = ["updated_at = now()"]
    params: dict = {"id": thread_id, "user_id": user_id}
    if title is not None:
        sets.append("title = %(title)s")
        params["title"] = title[:TITLE_MAX_CHARS]
    if archived is not None:
        sets.append("archived = %(archived)s")
        params["archived"] = archived

    async with await AsyncConnection.connect(url) as conn:
        conn.row_factory = dict_row
        row = await (
            await conn.execute(
                f"UPDATE chat_threads SET {', '.join(sets)}"
                " WHERE id = %(id)s AND user_id = %(user_id)s"
                " RETURNING id, user_id, title, preview, turn_count, archived,"
                " created_at, updated_at",
                params,
            )
        ).fetchone()
        await conn.commit()

    return ChatThread(**row) if row else None


async def delete_thread(thread_id: str, user_id: str) -> bool:
    """Remove the registry row. Returns False if the user does not own it.

    The transcript is deleted separately by the caller, via the checkpointer -
    dropping only this row would leave the checkpoint rows behind forever.
    """
    url = database_url()
    if not url:
        return False

    async with await AsyncConnection.connect(url) as conn:
        cursor = await conn.execute(
            "DELETE FROM chat_threads WHERE id = %s AND user_id = %s",
            (thread_id, user_id),
        )
        await conn.commit()
        return cursor.rowcount > 0
