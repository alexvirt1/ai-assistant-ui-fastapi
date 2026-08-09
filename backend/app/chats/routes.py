"""HTTP surface for the chat list and a thread's stored history.

Built as a factory rather than a module-level router because the history
endpoint reads the compiled graph's state, and the graph only exists once the
lifespan has built it (see app/server.py).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..identity import current_user_id
from . import store as chat_store
from .messages import to_core_messages

logger = logging.getLogger(__name__)


class ChatSummary(BaseModel):
    id: str
    title: str
    preview: str
    turnCount: int
    archived: bool
    createdAt: str
    updatedAt: str

    @classmethod
    def of(cls, thread: chat_store.ChatThread) -> "ChatSummary":
        return cls(
            id=thread.id,
            title=thread.title,
            preview=thread.preview,
            turnCount=thread.turn_count,
            archived=thread.archived,
            createdAt=thread.created_at.isoformat(),
            updatedAt=thread.updated_at.isoformat(),
        )


class ChatUpdate(BaseModel):
    title: str | None = None
    archived: bool | None = None


def make_chats_router(graph, checkpointer=None) -> APIRouter:
    router = APIRouter(prefix="/api/chats", tags=["chats"])

    @router.get("")
    async def list_chats(
        q: str | None = Query(default=None, description="Substring of title or first message"),
        limit: int = Query(default=30, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        archived: bool = Query(default=False),
        user_id: str = Depends(current_user_id),
    ) -> list[ChatSummary]:
        threads = await chat_store.list_threads(
            user_id,
            query=q or None,
            limit=limit,
            offset=offset,
            include_archived=archived,
        )
        return [ChatSummary.of(t) for t in threads]

    @router.get("/{thread_id}/messages")
    async def get_chat_messages(
        thread_id: str,
        user_id: str = Depends(current_user_id),
    ) -> list[dict]:
        # Ownership first. The checkpointer has no notion of users and will
        # load any thread id it is given, so this check is the only thing
        # between a guessed id and someone else's transcript.
        if await chat_store.get_thread(thread_id, user_id) is None:
            raise HTTPException(status_code=404, detail="No such chat")

        state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        messages = (state.values or {}).get("messages", []) if state else []
        return to_core_messages(messages)

    @router.get("/{thread_id}/documents")
    async def get_chat_documents(
        thread_id: str,
        user_id: str = Depends(current_user_id),
    ) -> list[dict]:
        if await chat_store.get_thread(thread_id, user_id) is None:
            raise HTTPException(status_code=404, detail="No such chat")
        return await chat_store.list_thread_documents(thread_id, user_id)

    @router.patch("/{thread_id}")
    async def update_chat(
        thread_id: str,
        update: ChatUpdate,
        user_id: str = Depends(current_user_id),
    ) -> ChatSummary:
        thread = await chat_store.update_thread(
            thread_id, user_id, title=update.title, archived=update.archived
        )
        if thread is None:
            raise HTTPException(status_code=404, detail="No such chat")
        return ChatSummary.of(thread)

    @router.delete("/{thread_id}", status_code=204)
    async def delete_chat(
        thread_id: str,
        user_id: str = Depends(current_user_id),
    ) -> None:
        if not await chat_store.delete_thread(thread_id, user_id):
            raise HTTPException(status_code=404, detail="No such chat")

        # Deleting only the registry row would leave the transcript in the
        # checkpoint tables forever - invisible, unreachable, and still on disk.
        if checkpointer is not None:
            try:
                await checkpointer.adelete_thread(thread_id)
            except Exception:
                # The chat is already gone from the user's point of view;
                # failing the request now would be misleading.
                logger.exception("could not delete checkpoints for %s", thread_id)

    return router
