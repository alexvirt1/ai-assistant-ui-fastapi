import os
from dataclasses import dataclass
from typing import Callable, Optional

from langchain_core.tools import BaseTool


@dataclass(frozen=True)
class ToolSpec:
    """A registered tool plus the metadata needed to enable and prompt for it."""

    tool: BaseTool
    prompt_hint: str = ""
    required_env: tuple[str, ...] = ()
    # For availability rules required_env can't express (e.g. "any one of several keys").
    available: Optional[Callable[[], bool]] = None


_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> None:
    _REGISTRY[spec.tool.name] = spec


def get_registered_specs() -> list[ToolSpec]:
    return list(_REGISTRY.values())


def get_enabled_specs() -> list[ToolSpec]:
    """Registered tools filtered by ENABLED_TOOLS and runtime availability.

    ENABLED_TOOLS is a comma-separated allowlist; unset or empty means "all".
    A tool whose required env vars are missing (or whose available() check
    fails) is silently skipped, so optional integrations never break startup.
    """
    enabled_csv = os.getenv("ENABLED_TOOLS", "").strip()
    allowlist = {name.strip() for name in enabled_csv.split(",") if name.strip()}

    specs = []
    for spec in _REGISTRY.values():
        if allowlist and spec.tool.name not in allowlist:
            continue
        if any(not os.getenv(var) for var in spec.required_env):
            continue
        if spec.available is not None and not spec.available():
            continue
        specs.append(spec)
    return specs


def compose_prompt(base_prompt: str, specs: list[ToolSpec]) -> str:
    """Build the system prompt from the base prompt plus enabled tools' hints."""
    # dict.fromkeys dedupes while keeping order — tools from the same MCP
    # server share one server-level hint.
    hints = list(dict.fromkeys(spec.prompt_hint for spec in specs if spec.prompt_hint))
    if not hints:
        return base_prompt
    return base_prompt + "\n\nTool guidance:\n" + "\n".join(f"- {hint}" for hint in hints)
