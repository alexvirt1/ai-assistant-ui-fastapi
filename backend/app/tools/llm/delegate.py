"""Delegate hard questions to external AI models.

ask_claude was replaced by hybrid mode (app/cloud): a turn is handed over to
Claude whole, with the user's approval, instead of the local model relaying one
question.

Each tool self-disables (via required_env) when its API key is absent. The
answer is truncated so it fits comfortably in the local model's context.
"""

import os

from langchain_core.tools import tool

from ..base import ToolSpec, register

MAX_ANSWER_CHARS = int(os.getenv("DELEGATE_MAX_CHARS", "6000"))
MAX_TOKENS = int(os.getenv("DELEGATE_MAX_TOKENS", "4096"))


def _truncate(text: str) -> str:
    if len(text) > MAX_ANSWER_CHARS:
        return text[:MAX_ANSWER_CHARS] + "\n[...truncated]"
    return text


@tool
async def ask_openai(prompt: str) -> str:
    """Ask an OpenAI model (GPT) a question and return its answer. Use for complex questions that need a more capable external model."""
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=os.getenv("OPENAI_DELEGATE_MODEL", "gpt-4o-mini"),
        max_tokens=MAX_TOKENS,
        timeout=120,
    )
    try:
        result = await model.ainvoke(prompt)
    except Exception as exc:  # surface API failures to the model, don't crash the run
        return f"Error: OpenAI request failed: {exc}."
    content = result.content if isinstance(result.content, str) else str(result.content)
    return _truncate(content)


register(
    ToolSpec(
        tool=ask_openai,
        prompt_hint=(
            "For complex questions beyond your ability, you may delegate with "
            "ask_openai."
        ),
        required_env=("OPENAI_API_KEY",),
    )
)
