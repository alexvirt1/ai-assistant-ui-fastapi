"""Managing the model inventory on the Ollama VM.

Wraps the handful of Ollama endpoints needed to see what the VM has, what it
currently holds in memory, and to pull or preload a model.

Sizing note that shapes how these are used: the VM serves one model at a time.
Loading a second evicts the first, and a cold load costs roughly 6s for an 8B
and 19s for a 14B against 0.3s warm. So switching models mid-request is
expensive, and callers should group work by model rather than alternating.
"""

import os
from dataclasses import dataclass

import httpx

BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://192.168.87.160:11434").rstrip("/")
TIMEOUT = float(os.getenv("OLLAMA_ADMIN_TIMEOUT", "30"))
# Ollama's own default is 5m, which means an idle conversation re-pays the cold
# load on its next turn.
KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")


@dataclass(frozen=True)
class ResidentModel:
    tag: str
    size_bytes: int
    expires_at: str

    @property
    def size_gb(self) -> float:
        return self.size_bytes / 1e9


async def list_available() -> list[str]:
    """Tags the VM can serve, from /api/tags. Empty list if it is unreachable."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(f"{BASE_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    return sorted(m["name"] for m in data.get("models", []))


async def resident() -> list[ResidentModel]:
    """Models currently held in memory, from /api/ps."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(f"{BASE_URL}/api/ps")
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    return [
        ResidentModel(
            tag=m.get("name", ""),
            size_bytes=m.get("size", 0),
            expires_at=m.get("expires_at", ""),
        )
        for m in data.get("models", [])
    ]


async def ensure(tag: str) -> bool:
    """Make sure a tag is present on the VM, pulling it if it is not.

    Pulling is slow and streams progress; this waits for completion. Returns
    True when the tag is available afterwards.
    """
    if tag in await list_available():
        return True
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.post(
                f"{BASE_URL}/api/pull", json={"name": tag, "stream": False}
            )
            resp.raise_for_status()
    except httpx.HTTPError:
        return False
    return tag in await list_available()


async def warm(tag: str) -> bool:
    """Preload a model so the next real call does not pay the cold load.

    An empty prompt asks Ollama to load the weights and stop.
    """
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.post(
                f"{BASE_URL}/api/generate",
                json={"model": tag, "prompt": "", "keep_alive": KEEP_ALIVE},
            )
            resp.raise_for_status()
    except httpx.HTTPError:
        return False
    return True


async def validate() -> dict[str, str]:
    """Check every configured role against the VM.

    Returns role -> the tag that will actually be used, after falling back for
    anything the VM does not serve. Never raises: an unreachable VM yields the
    configured tags unchanged, matching the app's behaviour of degrading rather
    than failing at startup.
    """
    from . import registry

    available = set(await list_available())
    if not available:
        return {spec.role: spec.tag for spec in registry.get_specs()}
    return {
        role: spec.tag for role, spec in registry.resolve_against(available).items()
    }
