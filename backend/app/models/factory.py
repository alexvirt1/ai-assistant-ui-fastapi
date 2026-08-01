"""Building a ChatOllama for a named role.

Single place where model construction settings live, so every caller — the
agent today, planner and executor nodes later — gets the same configuration
with only the tag varying by role.
"""

import os

from langchain_ollama import ChatOllama

from .ollama import BASE_URL, KEEP_ALIVE
from .registry import DEFAULT_ROLE, get_tag


def make_chat_model(role: str = DEFAULT_ROLE, **overrides) -> ChatOllama:
    """Construct a ChatOllama for a role.

    keep_alive is set explicitly: Ollama's 5-minute default means an idle
    conversation re-pays the cold model load (~6s for an 8B, ~19s for a 14B)
    on its next turn.

    disable_streaming="tool_calling" and the disabled thinking mode are carried
    over from the original agent configuration - qwen3 emits its tool calls in
    one shot rather than streaming them, and the thinking preamble is not
    wanted in chat output.

    Thinking is disabled via `reasoning=False`, which langchain-ollama sends as
    the `think` request field. It used to be `model_kwargs={"think": False}`;
    langchain-ollama 1.x dropped `model_kwargs`, and because the model config
    is `extra="ignore"` that spelling is now silently discarded rather than
    raising — which would quietly let reasoning output back into chat.
    """
    settings = dict(
        model=get_tag(role),
        base_url=os.getenv("OLLAMA_BASE_URL", BASE_URL),
        temperature=0,
        num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "8192")),
        keep_alive=KEEP_ALIVE,
        disable_streaming="tool_calling",
        reasoning=False,
    )
    settings.update(overrides)
    return ChatOllama(**settings)
