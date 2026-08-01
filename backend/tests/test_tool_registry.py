"""Tool registration, self-disabling, and system-prompt composition."""

import pytest
from langchain_core.tools import tool

from app.tools import base


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    """Swap the module-level registry so tests do not see, or pollute, the real
    tools discovered at import."""
    monkeypatch.setattr(base, "_REGISTRY", {})
    yield


def make_tool(name: str):
    @tool(name)
    def _fn(query: str) -> str:
        """A tool."""
        return query

    return _fn


def register(name: str, **kwargs) -> base.ToolSpec:
    spec = base.ToolSpec(tool=make_tool(name), **kwargs)
    base.register(spec)
    return spec


def enabled_names() -> set[str]:
    return {spec.tool.name for spec in base.get_enabled_specs()}


def test_registered_tool_is_enabled_by_default():
    register("alpha")
    assert enabled_names() == {"alpha"}


def test_enabled_tools_acts_as_an_allowlist(monkeypatch):
    register("alpha")
    register("beta")
    monkeypatch.setenv("ENABLED_TOOLS", "alpha")
    assert enabled_names() == {"alpha"}


def test_blank_enabled_tools_means_all(monkeypatch):
    register("alpha")
    register("beta")
    monkeypatch.setenv("ENABLED_TOOLS", "   ")
    assert enabled_names() == {"alpha", "beta"}


def test_allowlist_tolerates_spaces(monkeypatch):
    register("alpha")
    register("beta")
    monkeypatch.setenv("ENABLED_TOOLS", " alpha , beta ")
    assert enabled_names() == {"alpha", "beta"}


def test_tool_with_missing_env_self_disables(monkeypatch):
    monkeypatch.delenv("NEEDED_KEY", raising=False)
    register("needs_key", required_env=("NEEDED_KEY",))
    assert enabled_names() == set()


def test_tool_with_present_env_is_enabled(monkeypatch):
    monkeypatch.setenv("NEEDED_KEY", "value")
    register("needs_key", required_env=("NEEDED_KEY",))
    assert enabled_names() == {"needs_key"}


def test_available_callable_can_disable(monkeypatch):
    register("maybe", available=lambda: False)
    assert enabled_names() == set()


def test_registering_same_name_twice_replaces():
    register("alpha", prompt_hint="first")
    register("alpha", prompt_hint="second")
    specs = base.get_registered_specs()
    assert len(specs) == 1
    assert specs[0].prompt_hint == "second"


def test_compose_prompt_appends_hints():
    spec = register("alpha", prompt_hint="Use alpha wisely.")
    composed = base.compose_prompt("BASE.", [spec])
    assert composed.startswith("BASE.")
    assert "Use alpha wisely." in composed


def test_compose_prompt_dedupes_shared_hints():
    """Tools from one MCP server share a server-level hint; it should appear
    once, not once per tool."""
    a = register("a", prompt_hint="Shared server hint.")
    b = register("b", prompt_hint="Shared server hint.")
    composed = base.compose_prompt("BASE.", [a, b])
    assert composed.count("Shared server hint.") == 1


def test_compose_prompt_unchanged_without_hints():
    spec = register("alpha")
    assert base.compose_prompt("BASE.", [spec]) == "BASE."
