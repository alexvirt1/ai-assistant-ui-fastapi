"""Tool registry with auto-discovery.

Importing this package imports every submodule, and each tool module registers
itself via base.register() as an import side effect. Adding a new tool means
adding one module under this package — nothing else to wire up.
"""

import importlib
import pkgutil

from .base import (
    ToolSpec,
    compose_prompt,
    get_enabled_specs,
    get_registered_specs,
    register,
)

__all__ = [
    "ToolSpec",
    "compose_prompt",
    "get_enabled_specs",
    "get_registered_specs",
    "register",
]


def _discover() -> None:
    for module in pkgutil.walk_packages(__path__, prefix=__name__ + "."):
        importlib.import_module(module.name)


_discover()
