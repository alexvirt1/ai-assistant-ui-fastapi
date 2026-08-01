"""Model roles and the Ollama VM inventory.

Public surface:

    get_tag("deep")        -> the Ollama tag configured for a role
    make_chat_model("fast") -> a ChatOllama bound to that role

    await list_available() -> tags the VM can serve
    await resident()       -> what it currently holds in memory
    await ensure(tag)      -> pull if absent
    await warm(tag)        -> preload
    await validate()       -> role -> tag actually usable

Inspect the current state with:  python -m app.models
"""

from .factory import make_chat_model
from .ollama import (
    KEEP_ALIVE,
    ResidentModel,
    ensure,
    list_available,
    resident,
    validate,
    warm,
)
from .registry import DEFAULT_ROLE, ModelSpec, get_spec, get_specs, get_tag, roles

__all__ = [
    "DEFAULT_ROLE",
    "KEEP_ALIVE",
    "ModelSpec",
    "ResidentModel",
    "ensure",
    "get_spec",
    "get_specs",
    "get_tag",
    "list_available",
    "make_chat_model",
    "resident",
    "roles",
    "validate",
    "warm",
]
