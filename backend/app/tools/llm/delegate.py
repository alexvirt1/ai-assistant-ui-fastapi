"""Delegate hard questions to external AI models.

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


@tool
async def ask_claude(prompt: str) -> str:
    """Ask Anthropic's Claude a question and return its answer. Use for complex reasoning or analysis that needs a more capable external model."""
    import anthropic

    client = anthropic.AsyncAnthropic()
    try:
        # fallbacks="default": if Claude's safety classifiers decline, the API
        # retries the same request server-side on the recommended fallback model.
        response = await client.beta.messages.create(
            model=os.getenv("CLAUDE_DELEGATE_MODEL", "claude-opus-5"),
            max_tokens=MAX_TOKENS,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        return f"Error: Claude request failed: {exc}."
    if response.stop_reason == "refusal":
        return "Error: Claude declined to answer this request."
    return _truncate(
        "".join(block.text for block in response.content if block.type == "text")
    )


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

register(
    ToolSpec(
        tool=ask_claude,
        prompt_hint=(
            "For complex reasoning or analysis beyond your ability, you may "
            "delegate with ask_claude."
        ),
        required_env=("ANTHROPIC_API_KEY",),
    )
)
