from datetime import datetime

from langchain_core.tools import tool

from ..base import ToolSpec, register


@tool
def current_time() -> str:
    """Return the current server time in ISO format."""
    return datetime.now().isoformat(timespec="seconds")


register(
    ToolSpec(
        tool=current_time,
        prompt_hint=(
            "When the user asks for the current time or server time, call "
            "current_time. Do not answer time questions from memory."
        ),
    )
)
