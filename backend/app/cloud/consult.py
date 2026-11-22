"""Consult mode: ask a cloud model one question and return its answer as text.

The alternative to hand-over that the hybrid-mode evals compare against: the
local model keeps the turn and relays the cloud model's answer. Not offered in
the chat UI.
"""

from langchain_core.messages import HumanMessage

from ..chats.messages import content_to_text
from . import models


async def consult(question: str, name: str | None = None) -> tuple[str, dict]:
    """The cloud model's answer, and its token usage."""
    spec = next((m for m in models.available_models() if m.name == name), None) if name \
        else models.default_model()
    if spec is None:
        raise RuntimeError("No cloud model is configured (set its API key in backend/.env).")
    reply = await models.make_chat_model(spec.name).ainvoke([HumanMessage(content=question)])
    return content_to_text(reply.content), dict(reply.usage_metadata or {})
