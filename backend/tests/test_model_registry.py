"""Model role resolution and degradation."""

import textwrap

import pytest


@pytest.fixture
def models_yaml(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(
        textwrap.dedent(
            """
            - role: fast
              tag: small:1b
            - role: deep
              tag: big:14b
              description: Planning.
            """
        ).strip()
    )
    return path


def test_roles_resolve_to_configured_tags(reloaded_registry, models_yaml):
    registry = reloaded_registry(models_yaml)
    assert registry.get_tag("fast") == "small:1b"
    assert registry.get_tag("deep") == "big:14b"


def test_unknown_role_falls_back_to_default(reloaded_registry, models_yaml):
    registry = reloaded_registry(models_yaml)
    assert registry.get_tag("no-such-role") == registry.get_tag(registry.DEFAULT_ROLE)


def test_default_role_always_exists_even_if_omitted(reloaded_registry, tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text("- role: deep\n  tag: big:14b\n")
    registry = reloaded_registry(path)
    # No "fast" entry, but the fallback role must still resolve or every lookup
    # for a missing role would raise.
    assert registry.get_tag(registry.DEFAULT_ROLE)


def test_missing_config_uses_builtin_default(reloaded_registry, tmp_path):
    registry = reloaded_registry(tmp_path / "does-not-exist.yaml")
    assert registry.roles() == [registry.DEFAULT_ROLE]
    assert registry.get_tag() == "qwen3:8b"


def test_ollama_model_env_overrides_default_role(
    reloaded_registry, models_yaml, monkeypatch
):
    """OLLAMA_MODEL predates the registry and must keep winning for `fast`.

    Existing .env files rely on it; silently overriding them from models.yaml
    would change which model the agent runs.
    """
    monkeypatch.setenv("OLLAMA_MODEL", "override:7b")
    registry = reloaded_registry(models_yaml)
    assert registry.get_tag("fast") == "override:7b"
    # Only the default role is affected.
    assert registry.get_tag("deep") == "big:14b"


def test_env_override_ignored_when_blank(reloaded_registry, models_yaml, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "   ")
    registry = reloaded_registry(models_yaml)
    assert registry.get_tag("fast") == "small:1b"


def test_resolve_against_remaps_only_missing_tags(reloaded_registry, models_yaml):
    registry = reloaded_registry(models_yaml)
    resolved = registry.resolve_against({"small:1b"})  # VM lacks big:14b
    assert resolved["fast"].tag == "small:1b"
    assert resolved["deep"].tag == "small:1b", "missing tag should fall back"


def test_resolve_against_keeps_everything_when_all_present(
    reloaded_registry, models_yaml
):
    registry = reloaded_registry(models_yaml)
    resolved = registry.resolve_against({"small:1b", "big:14b"})
    assert resolved["deep"].tag == "big:14b"
