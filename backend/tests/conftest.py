"""Shared fixtures.

These tests are hermetic: no Ollama VM, no Postgres, no outbound HTTP. They
cover the pure logic — role resolution, history trimming, tool registration and
REST config interpolation — which is where the subtle bugs have actually been.
Anything requiring live infrastructure stays in the manual probes.
"""

import os

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Stop a developer's real .env from steering the tests.

    app.models.registry honours OLLAMA_MODEL, and app.tools.base honours
    ENABLED_TOOLS; if the ambient environment sets either, assertions about
    defaults would pass or fail depending on whose machine ran them.

    DATABASE_URL is cleared for a stronger reason than tidiness: importing the
    app calls load_dotenv(), so a developer's real .env would otherwise point
    the chat registry at a live Postgres and these tests would start writing
    rows to it.
    """
    for var in (
        "OLLAMA_MODEL",
        "ENABLED_TOOLS",
        "MODELS_CONFIG",
        "REST_TOOLS_CONFIG",
        "DATABASE_URL",
        "SINGLE_USER_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def reloaded_registry(monkeypatch):
    """Reload app.models.registry with a given models.yaml.

    The registry reads its YAML once at import and caches it in a module-level
    dict, which is right for the app and awkward for tests, so this reloads the
    module after pointing MODELS_CONFIG somewhere new.
    """
    import importlib

    from app.models import registry

    def _load(config_path: os.PathLike | str | None):
        if config_path is None:
            monkeypatch.delenv("MODELS_CONFIG", raising=False)
        else:
            monkeypatch.setenv("MODELS_CONFIG", str(config_path))
        return importlib.reload(registry)

    yield _load

    # Leave the module as the rest of the suite expects to find it.
    monkeypatch.delenv("MODELS_CONFIG", raising=False)
    importlib.reload(registry)
