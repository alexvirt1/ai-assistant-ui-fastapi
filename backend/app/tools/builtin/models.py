"""Checking, listing and switching the chat model from inside a conversation.

The same selection the header's model picker drives (app/models/selection.py),
so a switch made here shows up there after the turn, and the reverse.
"""

from langchain_core.tools import tool

from ...models import selection
from ..base import ToolSpec, register


def _describe(model: selection.ModelInfo) -> str:
    notes = []
    if model.roles:
        notes.append("role: " + ", ".join(model.roles))
    if model.current:
        notes.append("current")
    if model.default:
        notes.append("default")
    if model.resident:
        notes.append("loaded")
    if not model.tools:
        notes.append("no tool support, cannot be selected")
    return f"- {model.tag}" + (f" ({'; '.join(notes)})" if notes else "")


@tool
def current_model() -> str:
    """Return the name of the LLM model currently answering in this chat."""
    current, default = selection.current_tag(), selection.default_tag()
    suffix = " (the default)" if current == default else f" (default is {default})"
    return f"Current model: {current}{suffix}"


@tool
async def list_models() -> str:
    """List the LLM models available to switch to, marking the current one."""
    models = await selection.catalog()
    if not models:
        return "Error: the Ollama VM is unreachable, so no models can be listed."
    return "Available chat models:\n" + "\n".join(_describe(m) for m in models)


@tool
async def switch_model(model: str) -> str:
    """Switch the LLM model that answers in this chat.

    `model` is a model name from list_models (e.g. "qwen2.5:14b-instruct-q4_K_M"),
    a short prefix of one (e.g. "gemma4"), a role name ("fast", "deep", "code"),
    or "default" to go back to the default model.
    """
    try:
        tag = await selection.select(model)
    except selection.ModelSelectionError as exc:
        return f"Error: {exc}"
    # The agent resolves its model on every step, so the reply to this very
    # turn already comes from the new model - after a cold load if it was not
    # resident.
    return f"Switched to {tag}. Replies now come from this model."


_HINT = (
    "When the user asks which model you are or which model is running, call "
    "current_model - never answer that from memory. When they ask what models "
    "are available, call list_models and show its list. When they ask to switch, "
    "change or use another model, call switch_model with the name they gave."
)

for _tool in (current_model, list_models, switch_model):
    register(ToolSpec(tool=_tool, prompt_hint=_HINT))
