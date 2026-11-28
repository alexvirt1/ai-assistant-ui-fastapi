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
    # How much of it is on the GPU; the rest runs on the CPU, many times slower.
    vram_bytes: int = 0

    @property
    def fully_on_gpu(self) -> bool:
        return self.vram_bytes >= self.size_bytes

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
    # A set first: the VM has been seen listing the same tag twice.
    return sorted({m["name"] for m in data.get("models", [])})


_DISK_SIZES: dict[str, int] = {}


async def disk_size(tag: str) -> int | None:
    """A model's file size from /api/tags (kept per process), or None."""
    if tag in _DISK_SIZES:
        return _DISK_SIZES[tag]
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(f"{BASE_URL}/api/tags")
            resp.raise_for_status()
            models = resp.json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return None
    for model in models:
        _DISK_SIZES[model["name"]] = int(model.get("size", 0))
    return _DISK_SIZES.get(tag)


# Capabilities of a tag do not change while it exists, and the model picker
# asks for every tag at once, so a successful answer is kept for the process.
_CAPABILITIES: dict[str, frozenset[str]] = {}


async def capabilities(tag: str) -> frozenset[str]:
    """What a tag can do, from /api/show: "completion", "tools", "embedding"...

    Empty when the VM is unreachable or too old to report them - callers treat
    empty as "unknown", not as "can do nothing".
    """
    if tag in _CAPABILITIES:
        return _CAPABILITIES[tag]
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(f"{BASE_URL}/api/show", json={"model": tag})
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return frozenset()
    found = frozenset(data.get("capabilities") or ())
    if found:
        _CAPABILITIES[tag] = found
    return found


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
            vram_bytes=m.get("size_vram", 0),
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


async def warm(tag: str, num_ctx: int | None = None) -> bool:
    """Preload a model so the next real call does not pay the cold load.

    An empty prompt asks Ollama to load the weights and stop. Pass the context
    size the chat will use: Ollama reloads a model whose num_ctx differs, so
    warming at its default size would be wasted.
    """
    body: dict = {"model": tag, "prompt": "", "keep_alive": KEEP_ALIVE}
    if num_ctx:
        body["options"] = {"num_ctx": num_ctx}
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.post(f"{BASE_URL}/api/generate", json=body)
            resp.raise_for_status()
    except httpx.HTTPError:
        return False
    return True


async def unload(tag: str) -> bool:
    """Drop a model from the VM's memory now, rather than when keep_alive ends.

    Used to make room on the GPU for image generation, which shares it.
    """
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{BASE_URL}/api/generate", json={"model": tag, "keep_alive": 0}
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
