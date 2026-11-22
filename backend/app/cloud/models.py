"""Cloud models a turn can be handed over to (hybrid mode).

Configured in backend/cloud_models.yaml (or CLOUD_MODELS_CONFIG). An entry is
offered only when its API key variable is set, so with no key the app behaves
exactly as before: everything stays local.

Only Anthropic is wired up for now. Claude is called through
langchain-anthropic, which uses the official `anthropic` SDK underneath, so the
agent loop and its tools work unchanged with either model.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_DEFAULT_CONFIG = Path(__file__).parents[2] / "cloud_models.yaml"


@dataclass(frozen=True)
class CloudModel:
    name: str
    label: str
    provider: str
    model: str
    api_key_env: str
    effort: str = "medium"
    max_tokens: int = 8000
    price_input: float = 0.0  # USD per million tokens
    price_output: float = 0.0

    @property
    def available(self) -> bool:
        return bool(os.getenv(self.api_key_env))

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.price_input + output_tokens * self.price_output) / 1e6

    def describe(self) -> dict:
        return {"name": self.name, "label": self.label, "model": self.model,
                "price_per_mtok": {"input": self.price_input, "output": self.price_output}}


def load_models(path: str | None = None) -> list[CloudModel]:
    path = Path(path or os.getenv("CLOUD_MODELS_CONFIG", str(_DEFAULT_CONFIG)))
    if not path.exists():
        return []
    models = []
    for entry in yaml.safe_load(path.read_text()) or []:
        price = entry.get("price_per_mtok") or {}
        models.append(CloudModel(
            name=entry["name"], label=entry.get("label", entry["name"]),
            provider=entry["provider"], model=entry["model"], api_key_env=entry["api_key_env"],
            effort=entry.get("effort", "medium"), max_tokens=int(entry.get("max_tokens", 8000)),
            price_input=float(price.get("input", 0)), price_output=float(price.get("output", 0)),
        ))
    return models


def available_models() -> list[CloudModel]:
    return [m for m in load_models() if m.available and m.provider == "anthropic"]


def default_model() -> CloudModel | None:
    """The cloud model hand-over uses: CLOUD_MODEL if set and available, else the first."""
    models = available_models()
    wanted = os.getenv("CLOUD_MODEL")
    for m in models:
        if m.name == wanted:
            return m
    return models[0] if models else None


def by_model_id(model_id: str | None) -> CloudModel | None:
    """The config entry for a provider model id, e.g. from response metadata."""
    if not model_id:
        return None
    for m in load_models():
        if model_id == m.model or model_id.startswith(m.model):
            return m
    return None


@lru_cache(maxsize=8)
def make_chat_model(name: str):
    """The LangChain chat model for a configured cloud model."""
    from langchain_anthropic import ChatAnthropic

    spec = next(m for m in load_models() if m.name == name)
    return ChatAnthropic(
        model=spec.model,
        api_key=os.environ[spec.api_key_env],
        max_tokens=spec.max_tokens,
        # Effort sets thinking depth and token spend; no temperature, which
        # current Claude models reject at non-default values.
        output_config={"effort": spec.effort},
        # If Claude's safety classifiers decline, the API retries the request
        # on the recommended fallback model server-side.
        betas=["server-side-fallback-2026-07-01"],
        model_kwargs={"fallbacks": "default"},
        default_request_timeout=180,
        max_retries=2,
    )
