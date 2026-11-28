"""Text-to-image on the ComfyUI server on AI-1.

ComfyUI runs a workflow: a graph of nodes in its "API format", a dict of
node id -> {"class_type", "inputs"}. The workflows here are built in Python
from the settings of the image workflows already used on AI-1, rather than
loaded from exported JSON with placeholders. Only four values ever vary - the
prompt, the size, the seed and the step count - and they go into node inputs
as plain values, so nothing the model writes can change the graph itself.

The flow for one image:

    WS   /ws?clientId=<job>   open first, so a fast job cannot finish unseen
    POST /prompt             queue the workflow with the same client id
    (websocket)              progress messages, then the PNG as a binary frame
    POST /history {delete}   drop the job, so its prompt is not kept in memory

The workflows end in SaveImageWebsocket, which sends the PNG to the client
that queued the job and writes no file. The PNG the app stores is then the
only copy, so deleting it in the chat deletes the image. (Before 1.9.4 they
ended in PreviewImage, whose files stayed in ComfyUI's temp folder - readable
from the LAN - until ComfyUI restarted; since 2026-11-23 AI-1 also deletes
temp files older than an hour.)

The GPU on AI-1 is shared with AI-1's own image and video jobs, which can run
for many minutes. A chat image does not queue behind them: if ComfyUI is busy
with someone else's work, generation fails at once with a message saying so.
"""

import asyncio
import dataclasses
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import uuid

import httpx


def base_url() -> str:
    return os.getenv("COMFYUI_BASE_URL", "").rstrip("/")


def timeout_seconds() -> float:
    return float(os.getenv("IMAGE_TIMEOUT_SECONDS", "180"))


# Seconds between GPU-memory polls while waiting for ComfyUI to free its models.
POLL_INTERVAL = 1.0

# Identifies this app's jobs in ComfyUI's queue: each job's client id is this
# prefix plus a job id, since SaveImageWebsocket sends the image only to the
# client that queued the job.
CLIENT_ID = "ai-assistant-ui"
OUTPUT_NODE = "SaveImageWebsocket"

logger = logging.getLogger(__name__)


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
    # Whether the workflow can take reference images (ImageRequest.references).
    supports_references: bool = False


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    width: int
    height: int
    seed: int
    steps: int
    # Reference images already uploaded to ComfyUI, as LoadImage names
    # ("ai-assistant-ui-<job>-ref0.png [temp]"). Only for models that
    # support_references.
    references: tuple[str, ...] = ()


# Total reference area in megapixels, as advised by the AI-1 agent: each
# reference adds about as many image tokens as the image itself, so 4 refs at
# 1 MP would be about 5x the sequence length on a 12 GB GPU.
REFERENCE_TOTAL_MEGAPIXELS = 2.0
MAX_REFERENCES = 4
REFERENCE_NODES = ("LoadImage", "ImageScaleToTotalPixels", "VAEEncode", "ReferenceLatent")


def reference_megapixels(count: int) -> float:
    """Size each reference is scaled to: 1 MP each, up to the total cap."""
    return min(1.0, REFERENCE_TOTAL_MEGAPIXELS / max(count, 1))


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
        "10": {"class_type": OUTPUT_NODE, "inputs": {"images": ["9", 0]}},
    }


def _flux2_klein(req: ImageRequest) -> dict:
    # Settings from AI-1's 08_klein_poster workflow: cfg 1, euler, the Flux2
    # scheduler (8 steps here, 4 there). References as in its
    # 02_klein_keyframe_refs: each one is scaled, encoded and attached to both
    # the prompt and the zeroed negative with ReferenceLatent.
    workflow = {
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
        "13": {"class_type": OUTPUT_NODE, "inputs": {"images": ["12", 0]}},
    }
    positive, negative = ["4", 0], ["5", 0]
    megapixels = reference_megapixels(len(req.references))
    for i, name in enumerate(req.references):
        load, scale, encode, pos, neg = (f"ref{i}_{part}" for part in
                                         ("load", "scale", "encode", "pos", "neg"))
        workflow[load] = {"class_type": "LoadImage", "inputs": {"image": name}}
        workflow[scale] = {"class_type": "ImageScaleToTotalPixels",
                           "inputs": {"image": [load, 0], "upscale_method": "lanczos",
                                      "megapixels": megapixels, "resolution_steps": 16}}
        workflow[encode] = {"class_type": "VAEEncode",
                            "inputs": {"pixels": [scale, 0], "vae": ["3", 0]}}
        workflow[pos] = {"class_type": "ReferenceLatent",
                         "inputs": {"conditioning": positive, "latent": [encode, 0]}}
        workflow[neg] = {"class_type": "ReferenceLatent",
                         "inputs": {"conditioning": negative, "latent": [encode, 0]}}
        positive, negative = [pos, 0], [neg, 0]
    workflow["6"]["inputs"].update(positive=positive, negative=negative)
    return workflow


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
                   "KSampler", "VAEDecode", OUTPUT_NODE),
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
                   "SamplerCustomAdvanced", "VAEDecode", OUTPUT_NODE) + REFERENCE_NODES,
            # The distilled model's usual 4 steps lost detail on long prompts
            # (tested live); 8 keeps more of it at roughly twice the sampling time.
            default_steps=8,
            build=_flux2_klein,
            supports_references=True,
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


def _is_ours(item) -> bool:
    """Whether a /queue entry was submitted by this app.

    An entry is [number, prompt_id, workflow, extra_data, outputs, ...], with
    the client_id sent to /prompt in extra_data.
    """
    try:
        return str(item[3].get("client_id", "")).startswith(CLIENT_ID)
    except (IndexError, AttributeError, TypeError):
        return False


async def queue_busy() -> bool:
    """Whether another client's job is running or waiting; True if ComfyUI can't be asked."""
    if not base_url():
        return False
    try:
        async with httpx.AsyncClient(base_url=base_url(), timeout=10) as client:
            return await _queue_busy(client)
    except (httpx.HTTPError, ValueError):
        return True


async def _queue_busy(client: httpx.AsyncClient) -> bool:
    """Whether ComfyUI has someone else's work running or waiting.

    Our own jobs do not count: ComfyUI reports a job's outputs in /history a
    moment before it takes the job off the running list, so a check made right
    after an image finished used to see that very job and decide AI-1 was busy
    - skipping /free, so the chat model then loaded beside the image model.
    """
    resp = await client.get("/queue")
    resp.raise_for_status()
    data = resp.json()
    items = list(data.get("queue_running") or []) + list(data.get("queue_pending") or [])
    return any(not _is_ours(item) for item in items)


def ws_url(client_id: str) -> str:
    return base_url().replace("http", "ws", 1) + f"/ws?clientId={client_id}"


def open_ws(url: str):
    """Connect to ComfyUI's websocket. Replaced in tests."""
    from websockets.asyncio.client import connect

    return connect(url, max_size=None, open_timeout=10)


async def upload_reference(client: httpx.AsyncClient, png: bytes, name: str) -> str:
    """Upload one reference to ComfyUI's temp folder; return its LoadImage name.

    Temp, never input/ (permanent by design): AI-1 deletes temp files older
    than an hour. Uploaded right before the job that uses it, under a name no
    other job uses, as agreed with the AI-1 agent.
    """
    try:
        resp = await client.post(
            "/upload/image",
            files={"image": (name, png, "image/png")},
            data={"type": "temp", "overwrite": "false"},
        )
        resp.raise_for_status()
        info = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ImageGenerationError(f"Could not send a reference image to AI-1 ({exc}).") from exc
    path = f"{info['subfolder']}/{info['name']}" if info.get("subfolder") else info["name"]
    return f"{path} [temp]"


async def generate(model: ImageModel, req: ImageRequest,
                   client: httpx.AsyncClient | None = None,
                   references: list[bytes] | tuple[bytes, ...] = ()) -> bytes:
    """Run one workflow and return the PNG it produced.

    `references` are PNGs to condition on; they are uploaded just before the
    job is queued and replace req.references.
    """
    if not base_url():
        raise ImageGenerationError("Image generation is not configured (COMFYUI_BASE_URL).")
    if references and not model.supports_references:
        raise ImageGenerationError(f"{model.name} cannot use reference images.")
    if len(references) > MAX_REFERENCES:
        raise ImageGenerationError(f"At most {MAX_REFERENCES} reference images per image.")

    client_id = f"{CLIENT_ID}-{uuid.uuid4().hex[:12]}"

    owns_client = client is None
    client = client or httpx.AsyncClient(base_url=base_url(), timeout=30)
    try:
        try:
            if await _queue_busy(client):
                raise ImageGenerationError(
                    "The image server on AI-1 is busy with another job. Try again in a few minutes."
                )
            ws_cm = open_ws(ws_url(client_id))
            ws = await ws_cm.__aenter__()
        except (httpx.HTTPError, OSError) as exc:
            raise ImageGenerationError(f"The image server on AI-1 is unreachable ({exc}).") from exc
        except ImageGenerationError:
            raise
        except Exception as exc:  # websocket handshake errors
            raise ImageGenerationError(f"The image server on AI-1 is unreachable ({exc}).") from exc

        prompt_id = None
        try:
            if references:
                names = [await upload_reference(client, png, f"{client_id}-ref{i}.png")
                         for i, png in enumerate(references)]
                req = dataclasses.replace(req, references=tuple(names))
            workflow = model.build(req)
            output_node = next(k for k, v in workflow.items() if v["class_type"] == OUTPUT_NODE)
            try:
                resp = await client.post(
                    "/prompt", json={"prompt": workflow, "client_id": client_id})
            except httpx.HTTPError as exc:
                raise ImageGenerationError(
                    f"The image server on AI-1 is unreachable ({exc}).") from exc
            if resp.status_code != 200:
                # ComfyUI validates the workflow before queueing and explains
                # what it rejected, e.g. a model file that is no longer there.
                raise ImageGenerationError(
                    f"The image server rejected the request: {_reason(resp)}")
            prompt_id = resp.json()["prompt_id"]
            try:
                async with asyncio.timeout(timeout_seconds()):
                    return await receive_image(ws, prompt_id, output_node)
            except TimeoutError:
                # Take it off the queue so it does not occupy the GPU after
                # the user has been told it failed.
                try:
                    await client.post("/queue", json={"delete": [prompt_id]})
                    await client.post("/interrupt", json={"prompt_id": prompt_id})
                except httpx.HTTPError:
                    pass
                raise ImageGenerationError(
                    f"The image took longer than {timeout_seconds():.0f} s and was cancelled."
                ) from None
        finally:
            await ws_cm.__aexit__(None, None, None)
            if prompt_id is not None:
                # The prompt text would otherwise stay in ComfyUI's memory
                # until it restarts.
                try:
                    await client.post("/history", json={"delete": [prompt_id]})
                except httpx.HTTPError:
                    pass
    finally:
        if owns_client:
            await client.aclose()


async def receive_image(ws, prompt_id: str, output_node: str) -> bytes:
    """Read the job's websocket messages until it is done; return its PNG.

    Text frames are JSON progress messages. A binary frame is an image: 4 bytes
    event type, 4 bytes image format, then the image. Only frames that arrive
    while the output node runs are the result - samplers can also send
    previews.
    """
    current_node = None
    image = None
    async for message in ws:
        if isinstance(message, (bytes, bytearray)):
            if current_node == output_node and len(message) > 8:
                image = bytes(message[8:])
            continue
        try:
            event = json.loads(message)
        except ValueError:
            continue
        kind, data = event.get("type"), event.get("data") or {}
        if data.get("prompt_id") not in (None, prompt_id):
            continue
        if kind == "executing":
            current_node = data.get("node")
            if current_node is None and data.get("prompt_id") == prompt_id:
                break
        elif kind == "execution_success" and data.get("prompt_id") == prompt_id:
            break
        elif kind == "execution_error":
            reason = str(data.get("exception_message") or "unknown error").strip()
            raise ImageGenerationError(f"Generation failed: {reason}")
        elif kind == "execution_interrupted":
            raise ImageGenerationError("Generation was interrupted on the image server.")
    if image is None:
        raise ImageGenerationError("The image server finished without producing an image.")
    return image


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
# 30 s, as agreed with the AI-1 agent: 10-20 s was seen not to be enough.
RELEASE_TIMEOUT = 30.0


def handback_free_bytes() -> int:
    """Free VRAM needed before the chat model is preloaded after images.

    9.5 GB by default. Ollama fits qwen3:8b at 24576 only with about 8.96 GB
    free: 7065 MiB for the model, llama.cpp's 1 GiB fit margin and its 457 MiB
    minimum. 8.5 GB was seen live to load it 61% on the GPU. The card shows
    about 11.7 GB free when idle. Measured on the whole card, not ComfyUI's
    share. service._return_gpu_to_chat also reloads once if it still misses.
    """
    return int(float(os.getenv("IMAGE_HANDBACK_FREE_BYTES", str(9.5e9))))


def free_for_others(device: dict) -> int:
    """GPU memory another process (Ollama) could use now.

    ComfyUI's vram_free also counts memory PyTorch has reserved but is not
    using (torch_vram_free), which no other process can have. Seen live: the
    wait passed at "9.5 GB free" and Ollama still loaded the chat model 61%
    on the GPU.
    """
    return int(device.get("vram_free", 0)) - int(device.get("torch_vram_free", 0))


async def wait_until_released(client: httpx.AsyncClient | None = None,
                              timeout: float | None = None) -> bool:
    """Wait after free_memory() until the GPU has room for the chat model.

    Without this the chat model can reload while the image model still holds
    the GPU, and Ollama then puts most of it on the CPU - where it stays until
    it is unloaded, making every reply many times slower. Seen live.
    True once there is room, False on timeout or error (callers carry on).
    """
    timeout = RELEASE_TIMEOUT if timeout is None else timeout
    needed = handback_free_bytes()
    owns_client = client is None
    client = client or httpx.AsyncClient(base_url=base_url(), timeout=10)
    started = time.monotonic()
    deadline = started + timeout
    asked_again = False
    try:
        while True:
            resp = await client.get("/system_stats")
            resp.raise_for_status()
            devices = resp.json().get("devices") or []
            if devices and free_for_others(devices[0]) >= needed:
                return True
            if time.monotonic() > deadline:
                return False
            # ComfyUI acts on /free between jobs, about 10 s after the last one
            # (measured). Ask once more in case the first request was dropped.
            if not asked_again and time.monotonic() - started > timeout / 3:
                asked_again = True
                await client.post("/free", json={"unload_models": True, "free_memory": True})
            await asyncio.sleep(POLL_INTERVAL or 0.5)
    except (httpx.HTTPError, ValueError):
        return False
    finally:
        if owns_client:
            await client.aclose()


# --- GPU reservations by the AI-1 agent ------------------------------------
#
# Agreed protocol: when AI-1 needs its GPU to itself (long renders, or scripts
# that use Ollama outside ComfyUI's queue), its agent writes ComfyUI userdata
# gpu-reservation.json = {"holder": "ai-1", "until": "<UTC ISO, Z>", "reason"}.
# While that is valid the chatbot generates no images and makes no Ollama
# load/unload calls of its own (normal chat requests still go through). A
# missing or expired file means free; a file that cannot be read means
# reserved until it is fixed (fail closed).

RESERVATION_PATH = "/api/userdata/gpu-reservation.json"


@dataclass(frozen=True)
class Reservation:
    holder: str
    until: datetime | None  # None: the file could not be read
    reason: str

    @property
    def unreadable(self) -> bool:
        return self.until is None

    def describe(self) -> str:
        if self.unreadable:
            return ("AI-1's GPU reservation file could not be read, so AI-1 counts as "
                    "reserved until it is fixed")
        local = self.until.astimezone().strftime("%H:%M")
        why = f" ({self.reason})" if self.reason else ""
        return f"AI-1's GPU is reserved by {self.holder or 'AI-1'} until {local}{why}"


def parse_reservation(text: str, now: datetime | None = None) -> Reservation | None:
    """The reservation in force, or None if it has expired."""
    now = now or datetime.now(timezone.utc)
    try:
        data = json.loads(text)
        until_text = data["until"]
        if not isinstance(until_text, str) or not until_text.endswith("Z"):
            raise ValueError("until must be UTC ending in Z")
        until = datetime.fromisoformat(until_text[:-1] + "+00:00")
    except (ValueError, KeyError, TypeError, AttributeError):
        return Reservation(holder="", until=None, reason="")
    if until <= now:
        return None
    return Reservation(holder=str(data.get("holder") or ""), until=until,
                       reason=str(data.get("reason") or ""))


async def reservation(client: httpx.AsyncClient | None = None) -> Reservation | None:
    """AI-1's GPU reservation in force now, if any.

    None when there is no file, it has expired, or ComfyUI cannot be reached
    (an unreachable ComfyUI fails generation on its own).
    """
    if not base_url():
        return None
    owns_client = client is None
    client = client or httpx.AsyncClient(base_url=base_url(), timeout=10)
    try:
        resp = await client.get(RESERVATION_PATH)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        found = parse_reservation(resp.text)
    except httpx.HTTPError:
        return None
    finally:
        if owns_client:
            await client.aclose()
    if found is not None and found.unreadable:
        logger.warning("AI-1's gpu-reservation.json is unreadable; treating AI-1 as reserved "
                       "until it is fixed. Tell the owner.")
    return found
