"""Text-to-image on the ComfyUI server on AI-1.

ComfyUI runs a workflow: a graph of nodes in its "API format", a dict of
node id -> {"class_type", "inputs"}. The workflows here are built in Python
from the settings of the image workflows already used on AI-1, rather than
loaded from exported JSON with placeholders. Only four values ever vary - the
prompt, the size, the seed and the step count - and they go into node inputs
as plain values, so nothing the model writes can change the graph itself.

The flow for one image:

    POST /prompt            queue the workflow, get a prompt_id
    GET  /history/<id>      poll until it has finished (or failed)
    GET  /view?...          fetch the PNG it produced

The workflows end in PreviewImage, not SaveImage: preview images go to
ComfyUI's temp folder, which ComfyUI empties when it restarts, instead of its
output folder, which it keeps. The PNG the app stores is then the only lasting
copy, so deleting it in the chat deletes the image.

The GPU on AI-1 is shared with AI-1's own image and video jobs, which can run
for many minutes. A chat image does not queue behind them: if ComfyUI is busy
with someone else's work, generation fails at once with a message saying so.
"""

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Callable

import httpx


def base_url() -> str:
    return os.getenv("COMFYUI_BASE_URL", "").rstrip("/")


def timeout_seconds() -> float:
    return float(os.getenv("IMAGE_TIMEOUT_SECONDS", "180"))


# Seconds between /history polls. An image takes 30-60 s on AI-1, most of it
# loading the model from disk (it is freed after every image), so a second of
# latency at the end is not noticeable.
POLL_INTERVAL = 1.0

# Identifies this app's jobs in ComfyUI's queue and history.
CLIENT_ID = "ai-assistant-ui"


class ImageGenerationError(Exception):
    """Generation failed; the message is fit to show the user."""


@dataclass(frozen=True)
class ImageModel:
    """One text-to-image model and how to build a workflow for it."""

    name: str
    description: str
    # Files the workflow loads, by ComfyUI model folder. The model is offered
    # only when all of them are present on the server.
    files: dict[str, str]
    # Node classes the workflow uses, checked against /object_info.
    nodes: tuple[str, ...]
    default_steps: int
    build: Callable[["ImageRequest"], dict]


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    width: int
    height: int
    seed: int
    steps: int


def _z_image_turbo(req: ImageRequest) -> dict:
    # Settings from AI-1's 01_zimage_tower_t2i workflow: AuraFlow shift 3,
    # cfg 1 with a zeroed negative (Turbo is distilled and ignores it),
    # res_multistep / simple.
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "z_image_turbo_int8_convrot.safetensors",
                         "weight_dtype": "default"}},
        "2": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["1", 0], "shift": 3.0}},
        "3": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen_3_4b_fp8_mixed.safetensors",
                         "type": "lumina2", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"text": req.prompt, "clip": ["3", 0]}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": req.width, "height": req.height, "batch_size": 1}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["2", 0], "seed": req.seed, "steps": req.steps,
                         "cfg": 1.0, "sampler_name": "res_multistep",
                         "scheduler": "simple", "positive": ["5", 0],
                         "negative": ["6", 0], "latent_image": ["7", 0],
                         "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 0]}},
        "10": {"class_type": "PreviewImage", "inputs": {"images": ["9", 0]}},
    }


def _flux2_klein(req: ImageRequest) -> dict:
    # Settings from AI-1's 08_klein_poster workflow, without its reference
    # images: cfg 1, euler, the Flux2 scheduler (8 steps here, 4 there).
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": "flux-2-klein-4b.safetensors",
                         "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "qwen_3_4b_fp8_mixed.safetensors",
                         "type": "flux2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": req.prompt, "clip": ["2", 0]}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "CFGGuider",
              "inputs": {"model": ["1", 0], "positive": ["4", 0],
                         "negative": ["5", 0], "cfg": 1.0}},
        "7": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "8": {"class_type": "Flux2Scheduler",
              "inputs": {"steps": req.steps, "width": req.width, "height": req.height}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": req.seed}},
        "10": {"class_type": "EmptyFlux2LatentImage",
               "inputs": {"width": req.width, "height": req.height, "batch_size": 1}},
        "11": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["9", 0], "guider": ["6", 0], "sampler": ["7", 0],
                          "sigmas": ["8", 0], "latent_image": ["10", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "PreviewImage", "inputs": {"images": ["12", 0]}},
    }


MODELS: dict[str, ImageModel] = {
    m.name: m
    for m in (
        ImageModel(
            name="z-image-turbo",
            description="Photorealistic images; tends towards photos even when a painting style is asked for. The default.",
            files={"diffusion_models": "z_image_turbo_int8_convrot.safetensors",
                   "text_encoders": "qwen_3_4b_fp8_mixed.safetensors",
                   "vae": "ae.safetensors"},
            nodes=("UNETLoader", "ModelSamplingAuraFlow", "CLIPLoader", "VAELoader",
                   "CLIPTextEncode", "ConditioningZeroOut", "EmptySD3LatentImage",
                   "KSampler", "VAEDecode", "PreviewImage"),
            default_steps=9,
            build=_z_image_turbo,
        ),
        ImageModel(
            name="flux2-klein",
            description="FLUX.2 Klein 4B: follows art styles (watercolour, illustration) closely; good with text in images.",
            files={"diffusion_models": "flux-2-klein-4b.safetensors",
                   "text_encoders": "qwen_3_4b_fp8_mixed.safetensors",
                   "vae": "flux2-vae.safetensors"},
            nodes=("UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode",
                   "ConditioningZeroOut", "CFGGuider", "KSamplerSelect",
                   "Flux2Scheduler", "RandomNoise", "EmptyFlux2LatentImage",
                   "SamplerCustomAdvanced", "VAEDecode", "PreviewImage"),
            # The distilled model's usual 4 steps lost detail on long prompts
            # (tested live); 8 keeps more of it at roughly twice the sampling time.
            default_steps=8,
            build=_flux2_klein,
        ),
    )
}

DEFAULT_MODEL = "z-image-turbo"


def default_model() -> str:
    name = os.getenv("IMAGE_MODEL", DEFAULT_MODEL)
    return name if name in MODELS else DEFAULT_MODEL


# Which models the server can run, re-checked at most this often. A model
# file renamed on AI-1 then stops being offered within a minute.
_AVAILABLE_TTL = 60.0
_available: tuple[float, list[str]] | None = None


async def available_models(client: httpx.AsyncClient | None = None) -> list[str]:
    """Names of the catalog models whose files and nodes the server has.

    Empty when ComfyUI is not configured or unreachable.
    """
    global _available
    if not base_url():
        return []
    now = time.monotonic()
    if _available and now - _available[0] < _AVAILABLE_TTL:
        return _available[1]

    owns_client = client is None
    client = client or httpx.AsyncClient(base_url=base_url(), timeout=10)
    try:
        folders: dict[str, set[str]] = {}
        for folder in {f for m in MODELS.values() for f in m.files}:
            resp = await client.get(f"/models/{folder}")
            resp.raise_for_status()
            folders[folder] = set(resp.json())
        node_classes: set[str] = set()
        for node in {n for m in MODELS.values() for n in m.nodes}:
            resp = await client.get(f"/object_info/{node}")
            if resp.status_code == 200 and node in resp.json():
                node_classes.add(node)
    except (httpx.HTTPError, ValueError):
        return []
    finally:
        if owns_client:
            await client.aclose()

    names = [
        m.name
        for m in MODELS.values()
        if all(file in folders.get(folder, set()) for folder, file in m.files.items())
        and all(node in node_classes for node in m.nodes)
    ]
    _available = (now, names)
    return names


def reset_cache() -> None:
    global _available
    _available = None


async def _queue_busy(client: httpx.AsyncClient) -> bool:
    resp = await client.get("/queue")
    resp.raise_for_status()
    data = resp.json()
    return bool(data.get("queue_running") or data.get("queue_pending"))


async def generate(model: ImageModel, req: ImageRequest,
                   client: httpx.AsyncClient | None = None) -> bytes:
    """Run one workflow and return the PNG it produced."""
    if not base_url():
        raise ImageGenerationError("Image generation is not configured (COMFYUI_BASE_URL).")

    owns_client = client is None
    client = client or httpx.AsyncClient(base_url=base_url(), timeout=30)
    try:
        try:
            if await _queue_busy(client):
                raise ImageGenerationError(
                    "The image server on AI-1 is busy with another job. Try again in a few minutes."
                )
            resp = await client.post(
                "/prompt", json={"prompt": model.build(req), "client_id": CLIENT_ID}
            )
        except httpx.HTTPError as exc:
            raise ImageGenerationError(f"The image server on AI-1 is unreachable ({exc}).") from exc
        if resp.status_code != 200:
            # ComfyUI validates the workflow before queueing and explains
            # what it rejected, e.g. a model file that is no longer there.
            raise ImageGenerationError(f"The image server rejected the request: {_reason(resp)}")
        prompt_id = resp.json()["prompt_id"]

        outputs = await _wait_for(client, prompt_id)
        image = _first_image(outputs)
        try:
            resp = await client.get("/view", params=image)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ImageGenerationError(f"Could not download the image ({exc}).") from exc
        return resp.content
    finally:
        if owns_client:
            await client.aclose()


async def _wait_for(client: httpx.AsyncClient, prompt_id: str) -> dict:
    deadline = time.monotonic() + timeout_seconds()
    while True:
        try:
            resp = await client.get(f"/history/{prompt_id}")
            resp.raise_for_status()
            entry = resp.json().get(prompt_id)
        except (httpx.HTTPError, ValueError) as exc:
            raise ImageGenerationError(f"Lost contact with the image server ({exc}).") from exc

        if entry:
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                raise ImageGenerationError(f"Generation failed: {_execution_error(status)}")
            if status.get("completed") or entry.get("outputs"):
                return entry.get("outputs") or {}

        if time.monotonic() > deadline:
            # Take it off the queue so it does not occupy the GPU after the
            # user has been told it failed.
            try:
                await client.post("/queue", json={"delete": [prompt_id]})
                await client.post("/interrupt", json={"prompt_id": prompt_id})
            except httpx.HTTPError:
                pass
            raise ImageGenerationError(
                f"The image took longer than {timeout_seconds():.0f} s and was cancelled."
            )
        await asyncio.sleep(POLL_INTERVAL)


def _first_image(outputs: dict) -> dict:
    for node_output in outputs.values():
        for image in node_output.get("images") or []:
            return {
                "filename": image["filename"],
                "subfolder": image.get("subfolder", ""),
                "type": image.get("type", "temp"),
            }
    raise ImageGenerationError("The image server finished without producing an image.")


def _reason(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}"
    error = data.get("error") or {}
    message = error.get("message") or f"HTTP {resp.status_code}"
    details = [
        err.get("details") or err.get("message", "")
        for node in (data.get("node_errors") or {}).values()
        for err in node.get("errors", [])
    ]
    return message + (f" ({'; '.join(d for d in details if d)})" if any(details) else "")


def _execution_error(status: dict) -> str:
    for kind, data in status.get("messages") or []:
        if kind == "execution_error":
            return str(data.get("exception_message", "unknown error")).strip()
    return "unknown error"


async def free_memory(client: httpx.AsyncClient | None = None) -> None:
    """Ask ComfyUI to unload its models, unless other work is queued.

    Lets the chat model load straight back for the reply. Skipped when other
    jobs are waiting, since unloading would make each of them reload its
    models. Never raises: freeing memory is a courtesy, not part of the result.
    """
    owns_client = client is None
    client = client or httpx.AsyncClient(base_url=base_url(), timeout=10)
    try:
        if not await _queue_busy(client):
            await client.post("/free", json={"unload_models": True, "free_memory": True})
    except httpx.HTTPError:
        pass
    finally:
        if owns_client:
            await client.aclose()


# ComfyUI applies /free between jobs, not at once; how long to wait for it.
RELEASE_TIMEOUT = 20.0
# Below this much in use, ComfyUI counts as having let go of its models.
RELEASED_BELOW_BYTES = 1024**3


async def wait_until_released(client: httpx.AsyncClient | None = None,
                              timeout: float | None = None) -> bool:
    """Wait for ComfyUI's GPU memory to drop after free_memory().

    Without this the chat model can reload while the image model still holds
    the GPU, and Ollama then puts most of it on the CPU - where it stays until
    it is unloaded, making every reply many times slower. Seen live.
    True once released, False on timeout or error (callers carry on anyway).
    """
    timeout = RELEASE_TIMEOUT if timeout is None else timeout
    owns_client = client is None
    client = client or httpx.AsyncClient(base_url=base_url(), timeout=10)
    deadline = time.monotonic() + timeout
    try:
        while True:
            resp = await client.get("/system_stats")
            resp.raise_for_status()
            devices = resp.json().get("devices") or []
            if devices and all(
                d.get("vram_total", 0) - d.get("vram_free", 0) < RELEASED_BELOW_BYTES
                # torch_vram_total is what ComfyUI itself holds; vram_free is
                # the whole card, which includes Ollama's share.
                or d.get("torch_vram_total", 0) < RELEASED_BELOW_BYTES
                for d in devices
            ):
                return True
            if time.monotonic() > deadline:
                return False
            await asyncio.sleep(POLL_INTERVAL or 0.5)
    except (httpx.HTTPError, ValueError):
        return False
    finally:
        if owns_client:
            await client.aclose()
