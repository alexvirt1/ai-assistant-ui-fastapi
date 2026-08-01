"""Model registry: named roles mapped to Ollama tags.

Callers ask for a role ("fast", "deep") rather than a tag, so the model a step
runs on is deployment configuration instead of something baked into agent code.
This mirrors the tool registry in app/tools/: declarative YAML, sensible
defaults, and graceful degradation when something is not present.

Roles are resolved against what the VM actually has (see ollama.py). A role
pointing at a tag the VM does not serve falls back to the default role rather
than failing, the same way a tool with a missing API key self-disables.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_CONFIG = Path(__file__).parents[2] / "models.yaml"

# Role used whenever a requested role is unknown or unavailable. This is the
# model the agent has always run on, so falling back to it is never a surprise.
DEFAULT_ROLE = "fast"


@dataclass(frozen=True)
class ModelSpec:
    role: str
    tag: str
    description: str = ""


# Used when models.yaml is absent, so the module works out of the box.
_BUILTIN: tuple[ModelSpec, ...] = (
    ModelSpec("fast", os.getenv("OLLAMA_MODEL", "qwen3:8b"),
              "Tool calls and short reasoning; the default."),
)


def _load_specs() -> dict[str, ModelSpec]:
    path = Path(os.getenv("MODELS_CONFIG", str(_DEFAULT_CONFIG)))
    if not path.exists():
        return {spec.role: spec for spec in _BUILTIN}

    entries = yaml.safe_load(path.read_text()) or []
    specs = {
        entry["role"]: ModelSpec(
            role=entry["role"],
            tag=entry["tag"],
            description=entry.get("description", ""),
        )
        for entry in entries
    }
    # Guarantee the fallback role always resolves, even if the file omits it.
    specs.setdefault(DEFAULT_ROLE, _BUILTIN[0])
    return specs


_REGISTRY: dict[str, ModelSpec] = _load_specs()


def get_specs() -> list[ModelSpec]:
    return list(_REGISTRY.values())


def get_spec(role: str) -> ModelSpec:
    """Resolve a role to its spec, falling back to the default role."""
    return _REGISTRY.get(role) or _REGISTRY[DEFAULT_ROLE]


def get_tag(role: str = DEFAULT_ROLE) -> str:
    """Resolve a role to an Ollama tag.

    OLLAMA_MODEL still wins for the default role. It predates this registry and
    is the documented way to change the agent's model, so honouring it keeps
    existing .env files behaving exactly as before; models.yaml governs the
    other roles.
    """
    if role == DEFAULT_ROLE:
        override = os.getenv("OLLAMA_MODEL", "").strip()
        if override:
            return override
    return get_spec(role).tag


def roles() -> list[str]:
    return list(_REGISTRY)


def resolve_against(available: set[str]) -> dict[str, ModelSpec]:
    """Return the registry with unavailable roles remapped to the default.

    `available` is the set of tags the VM serves. Kept separate from the HTTP
    call so this stays pure and testable; ollama.validate() supplies the set.
    """
    fallback = _REGISTRY[DEFAULT_ROLE]
    return {
        role: (spec if spec.tag in available else fallback)
        for role, spec in _REGISTRY.items()
    }
