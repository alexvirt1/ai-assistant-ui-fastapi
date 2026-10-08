"""Which model the chat agent is running on, and switching it.

One selection for the whole process rather than one per chat: the VM serves a
single model at a time, so two chats on different models would evict each
other on every turn. It lives in memory, so a restart returns to the default
role (`fast`, or OLLAMA_MODEL).

Only the chat agent follows the selection. Document jobs keep asking for their
roles - a summary pipeline that changed model halfway because someone switched
in a chat would cache chunks under two models.
"""

import asyncio
from dataclasses import asdict, dataclass

from . import ollama, registry

# None means "the default role", so a change to OLLAMA_MODEL or models.yaml is
# picked up rather than shadowed by a stale copy of the old tag.
_selected: str | None = None


class ModelSelectionError(ValueError):
    """A requested model cannot be used; the message is shown to the user."""


@dataclass(frozen=True)
class ModelInfo:
    tag: str
    # Roles in models.yaml that point at this tag, e.g. ("fast",).
    roles: tuple[str, ...]
    # False only when the VM says the model cannot call tools. The agent's
    # tools are bound on every call, so such a model errors on every turn.
    tools: bool
    # Held in memory right now, so switching to it costs no cold load.
    resident: bool
    current: bool
    default: bool

    def to_dict(self) -> dict:
        return asdict(self)


def default_tag() -> str:
    return registry.get_tag(registry.DEFAULT_ROLE)


def current_tag() -> str:
    return _selected or default_tag()


def reset() -> None:
    global _selected
    _selected = None


def _roles_by_tag() -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {}
    for spec in registry.get_specs():
        roles.setdefault(registry.get_tag(spec.role), []).append(spec.role)
    return roles


def match_tag(name: str, available: list[str]) -> str | None:
    """Resolve what a user typed to a tag the VM serves.

    Accepts a role ("deep"), an exact tag, a tag without its ":latest", or an
    unambiguous prefix ("gemma4") - the last because a chat request is phrased
    the way people say model names, not the way Ollama spells them. Pure, so
    the matching rules are testable without the VM.
    """
    name = name.strip()
    if name in registry.roles():
        name = registry.get_tag(name)
    if name in available:
        return name
    if f"{name}:latest" in available:
        return f"{name}:latest"
    lowered = name.lower()
    prefixed = [tag for tag in available if tag.lower().startswith(lowered)]
    return prefixed[0] if len(prefixed) == 1 else None


async def catalog() -> list[ModelInfo]:
    """Chat-capable models on the VM. Empty if the VM is unreachable.

    Embedding models are left out: they are on the VM for retrieval and cannot
    answer a chat turn at all.
    """
    available = await ollama.list_available()
    caps, loaded = await asyncio.gather(
        asyncio.gather(*(ollama.capabilities(tag) for tag in available)),
        ollama.resident(),
    )
    loaded_tags = {m.tag for m in loaded}
    roles = _roles_by_tag()
    current, default = current_tag(), default_tag()

    models = []
    for tag, cap in zip(available, caps):
        if cap and "completion" not in cap:
            continue
        models.append(
            ModelInfo(
                tag=tag,
                roles=tuple(roles.get(tag, ())),
                tools=not cap or "tools" in cap,
                resident=tag in loaded_tags,
                current=tag == current,
                default=tag == default,
            )
        )
    return models


async def select(name: str) -> str:
    """Switch the chat agent to a model, returning the tag now in use.

    "default" clears the selection. Raises ModelSelectionError, with a message
    fit to show the user, when the model is absent or cannot run the agent.
    """
    global _selected
    name = name.strip()
    if not name:
        raise ModelSelectionError("No model was named.")
    if name.lower() == "default":
        reset()
        return current_tag()

    available = await ollama.list_available()
    if not available:
        raise ModelSelectionError(
            "The Ollama VM is unreachable, so the model cannot be checked."
        )
    tag = match_tag(name, available)
    if tag is None:
        raise ModelSelectionError(
            f'No single model on the VM matches "{name}". '
            "List the available models and use one of those names."
        )

    cap = await ollama.capabilities(tag)
    if cap and "completion" not in cap:
        raise ModelSelectionError(f"{tag} is an embedding model and cannot chat.")
    if cap and "tools" not in cap:
        raise ModelSelectionError(
            f"{tag} cannot call tools, which this assistant needs."
        )

    # Stored as None when it is the default, so the selection keeps following
    # the default role rather than pinning today's copy of it.
    _selected = None if tag == default_tag() else tag
    return tag
