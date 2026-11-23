"""Text-to-image: the ComfyUI client, the service around it, replay and export.

ComfyUI is replaced by an httpx.MockTransport, Ollama and Postgres by
monkeypatched functions, so none of this needs AI-1.
"""

import asyncio
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image
from pypdf import PdfReader

from app.chats.export import generated_image_id, render_chat_pdf
from app.chats.messages import to_core_messages
from app.images import comfyui, service
from app.images.comfyui import ImageGenerationError, ImageRequest
from app.images.routes import download_name
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def png_bytes(width=64, height=48) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), (200, 100, 50)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def comfy_env(monkeypatch):
    monkeypatch.setenv("COMFYUI_BASE_URL", "http://comfy.test")
    monkeypatch.delenv("IMAGE_MODEL", raising=False)
    monkeypatch.setattr(comfyui, "POLL_INTERVAL", 0)
    monkeypatch.setattr(comfyui, "open_ws", lambda url: _ACTIVE[0].open_ws(url))
    comfyui.reset_cache()
    yield
    comfyui.reset_cache()


_ACTIVE: list = [None]
PNG_FRAME_HEADER = b"\x00\x00\x00\x01\x00\x00\x00\x02"  # event type, image format


class FakeWS:
    """ComfyUI's websocket for one client: progress messages, then the image."""

    def __init__(self, fake, url):
        self.fake, self.url = fake, url

    async def __aenter__(self):
        self.fake.events.append("ws-open")
        return self

    async def __aexit__(self, *exc):
        self.fake.events.append("ws-close")

    def __aiter__(self):
        return self.messages()

    async def messages(self):
        fake = self.fake
        def text(kind, **data):
            return json.dumps({"type": kind, "data": data})
        # Noise first: another job's message, and a sampler preview frame.
        yield text("executing", node="3", prompt_id="someone-elses")
        yield text("executing", node="8", prompt_id="p1")
        yield PNG_FRAME_HEADER + b"sampler preview, not the result"
        if fake.never_done:
            await asyncio.sleep(3600)
        if fake.error:
            yield text("execution_error", prompt_id="p1", exception_message=fake.error)
            return
        output = next(k for k, v in fake.submitted["prompt"].items()
                      if v["class_type"] == comfyui.OUTPUT_NODE)
        yield text("executing", node=output, prompt_id="p1")
        if not fake.no_image:
            yield PNG_FRAME_HEADER + png_bytes()
        yield text("executing", node=None, prompt_id="p1")


class FakeComfy:
    """Enough of ComfyUI's HTTP and websocket API for one generation."""

    def __init__(self, *, busy=False, reject=None, error=None, never_done=False,
                 files=None, nodes=None, no_image=False):
        _ACTIVE[0] = self
        self.busy = busy
        self.own_running = False
        self.reject = reject
        self.error = error
        self.never_done = never_done
        self.no_image = no_image
        self.files = files or {
            "diffusion_models": ["z_image_turbo_int8_convrot.safetensors",
                                 "flux-2-klein-4b.safetensors"],
            "text_encoders": ["qwen_3_4b_fp8_mixed.safetensors"],
            "vae": ["ae.safetensors", "flux2-vae.safetensors"],
        }
        self.nodes = nodes
        self.submitted = None
        self.freed = False
        self.cancelled = False
        self.history_deleted = []
        self.events = []
        self.ws_urls = []

    def open_ws(self, url):
        self.ws_urls.append(url)
        return FakeWS(self, url)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/models/"):
            return httpx.Response(200, json=self.files.get(path.split("/")[-1], []))
        if path.startswith("/object_info/"):
            node = path.split("/")[-1]
            if self.nodes is not None and node not in self.nodes:
                return httpx.Response(200, json={})
            return httpx.Response(200, json={node: {}})
        if path == "/queue" and request.method == "GET":
            running = [[1, "other-job", {}, {"client_id": "someone-else"}, []]] if self.busy else []
            if self.own_running:
                running.append([2, "p0", {}, {"client_id": comfyui.CLIENT_ID + "-0123abcd"}, []])
            return httpx.Response(200, json={"queue_running": running, "queue_pending": []})
        if path == "/queue":
            self.cancelled = True
            return httpx.Response(200, json={})
        if path == "/interrupt":
            return httpx.Response(200, json={})
        if path == "/prompt":
            self.events.append("prompt")
            if self.reject:
                return httpx.Response(400, json={"error": {"message": self.reject},
                                                 "node_errors": {}})
            self.submitted = json.loads(request.content)
            return httpx.Response(200, json={"prompt_id": "p1", "number": 1})
        if path == "/history" and request.method == "POST":
            self.history_deleted += json.loads(request.content)["delete"]
            return httpx.Response(200, json={})
        if path == "/free":
            self.freed = True
            return httpx.Response(200, json={})
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url="http://comfy.test",
                                 transport=httpx.MockTransport(self.handler))


def request(prompt="a lighthouse at dusk", **kw) -> ImageRequest:
    return ImageRequest(prompt=prompt, width=kw.get("width", 1024),
                        height=kw.get("height", 1024), seed=kw.get("seed", 7),
                        steps=kw.get("steps", 9))


# --- workflows ---------------------------------------------------------------

@pytest.mark.parametrize("name", list(comfyui.MODELS))
def test_workflow_carries_values_as_inputs_only(name):
    model = comfyui.MODELS[name]
    hostile = 'x", "class_type": "SaveImage'
    workflow = model.build(request(prompt=hostile, width=832, height=1216, seed=42, steps=3))
    # Survives a JSON round trip unchanged: the prompt is a value, not markup.
    assert json.loads(json.dumps(workflow)) == workflow
    texts = [n["inputs"]["text"] for n in workflow.values() if n["class_type"] == "CLIPTextEncode"]
    assert texts == [hostile]
    classes = {n["class_type"] for n in workflow.values()}
    assert classes == set(model.nodes)
    # Never written to ComfyUI's output folder, so no lasting copy on AI-1.
    assert "SaveImage" not in classes
    values = json.dumps(workflow)
    assert "832" in values and "1216" in values and "42" in values


def test_workflow_links_point_at_existing_nodes():
    for model in comfyui.MODELS.values():
        workflow = model.build(request())
        for node in workflow.values():
            for value in node["inputs"].values():
                if isinstance(value, list):
                    assert value[0] in workflow


def test_default_model_falls_back_when_unknown(monkeypatch):
    assert comfyui.default_model() == "z-image-turbo"
    monkeypatch.setenv("IMAGE_MODEL", "flux2-klein")
    assert comfyui.default_model() == "flux2-klein"
    monkeypatch.setenv("IMAGE_MODEL", "nonsense")
    assert comfyui.default_model() == "z-image-turbo"


# --- available models --------------------------------------------------------

async def test_available_models_lists_installed():
    fake = FakeComfy()
    async with fake.client() as client:
        assert await comfyui.available_models(client) == ["z-image-turbo", "flux2-klein"]


async def test_available_models_hides_missing_file():
    fake = FakeComfy(files={"diffusion_models": ["flux-2-klein-4b.safetensors"],
                            "text_encoders": ["qwen_3_4b_fp8_mixed.safetensors"],
                            "vae": ["flux2-vae.safetensors"]})
    async with fake.client() as client:
        assert await comfyui.available_models(client) == ["flux2-klein"]


async def test_available_models_hides_missing_node():
    nodes = set(comfyui.MODELS["z-image-turbo"].nodes)
    fake = FakeComfy(nodes=nodes)
    async with fake.client() as client:
        assert await comfyui.available_models(client) == ["z-image-turbo"]


async def test_available_models_empty_when_unconfigured(monkeypatch):
    monkeypatch.delenv("COMFYUI_BASE_URL")
    assert await comfyui.available_models() == []


async def test_available_models_empty_when_unreachable():
    def down(request):
        raise httpx.ConnectError("refused")
    client = httpx.AsyncClient(base_url="http://comfy.test", transport=httpx.MockTransport(down))
    async with client:
        assert await comfyui.available_models(client) == []


# --- generation --------------------------------------------------------------

@pytest.mark.parametrize("name", list(comfyui.MODELS))
async def test_generate_returns_png_over_the_websocket(name):
    fake = FakeComfy()
    async with fake.client() as client:
        png = await comfyui.generate(comfyui.MODELS[name], request(), client)
    # The image itself, not the sampler preview sent earlier.
    assert png == png_bytes()
    client_id = fake.submitted["client_id"]
    assert client_id.startswith(comfyui.CLIENT_ID + "-")
    assert fake.ws_urls == [f"ws://comfy.test/ws?clientId={client_id}"]
    # Websocket open before queueing, so a fast job cannot finish unseen.
    assert fake.events[:2] == ["ws-open", "prompt"] and "ws-close" in fake.events
    # The prompt does not stay in ComfyUI's history.
    assert fake.history_deleted == ["p1"]


async def test_generate_writes_no_file_on_ai1():
    for model in comfyui.MODELS.values():
        classes = {n["class_type"] for n in model.build(request()).values()}
        assert comfyui.OUTPUT_NODE in classes
        assert not classes & {"SaveImage", "PreviewImage"}


async def test_generate_without_image_is_an_error():
    fake = FakeComfy(no_image=True)
    async with fake.client() as client:
        with pytest.raises(ImageGenerationError, match="without producing an image"):
            await comfyui.generate(comfyui.MODELS["z-image-turbo"], request(), client)
    assert fake.history_deleted == ["p1"]


async def test_generate_unreachable_websocket(monkeypatch):
    fake = FakeComfy()

    def refuse(url):
        raise OSError("connection refused")

    monkeypatch.setattr(comfyui, "open_ws", refuse)
    async with fake.client() as client:
        with pytest.raises(ImageGenerationError, match="unreachable"):
            await comfyui.generate(comfyui.MODELS["z-image-turbo"], request(), client)
    assert fake.submitted is None


async def test_generate_refuses_when_ai1_busy():
    fake = FakeComfy(busy=True)
    async with fake.client() as client:
        with pytest.raises(ImageGenerationError, match="busy"):
            await comfyui.generate(comfyui.MODELS["z-image-turbo"], request(), client)
    assert fake.submitted is None


async def test_generate_reports_rejection():
    fake = FakeComfy(reject="Prompt outputs failed validation")
    async with fake.client() as client:
        with pytest.raises(ImageGenerationError, match="failed validation"):
            await comfyui.generate(comfyui.MODELS["z-image-turbo"], request(), client)
    assert "ws-close" in fake.events and fake.history_deleted == []


async def test_generate_reports_execution_error():
    fake = FakeComfy(error="CUDA out of memory")
    async with fake.client() as client:
        with pytest.raises(ImageGenerationError, match="out of memory"):
            await comfyui.generate(comfyui.MODELS["z-image-turbo"], request(), client)
    assert fake.history_deleted == ["p1"]


async def test_generate_times_out_and_cancels(monkeypatch):
    monkeypatch.setenv("IMAGE_TIMEOUT_SECONDS", "0.05")
    fake = FakeComfy(never_done=True)
    async with fake.client() as client:
        with pytest.raises(ImageGenerationError, match="cancelled"):
            await comfyui.generate(comfyui.MODELS["z-image-turbo"], request(), client)
    assert fake.cancelled and fake.history_deleted == ["p1"]


async def test_free_memory_skips_when_others_queued():
    fake = FakeComfy(busy=True)
    async with fake.client() as client:
        await comfyui.free_memory(client)
    assert not fake.freed
    fake = FakeComfy()
    async with fake.client() as client:
        await comfyui.free_memory(client)
    assert fake.freed


# --- service -----------------------------------------------------------------

@pytest.fixture
def fake_service(monkeypatch):
    calls = {"unloaded": [], "saved": None, "freed": False}

    async def available():
        return ["z-image-turbo", "flux2-klein"]

    async def generate(model, req):
        calls["request"] = (model.name, req)
        return calls.get("png", png_bytes())

    async def free_memory():
        calls["freed"] = True
        calls["handbacks"] = calls.get("handbacks", 0) + 1

    async def released():
        return True

    async def warm(tag, num_ctx=None):
        calls["warmed"] = (tag, num_ctx)
        return True

    class Resident:
        tag = "qwen3:8b"
        size_bytes = 100

        def __init__(self):
            # A list gives one value per resident() call, in order.
            vram = calls.get("vram", 100)
            self.vram_bytes = vram.pop(0) if isinstance(vram, list) else vram

        @property
        def fully_on_gpu(self):
            return self.vram_bytes >= self.size_bytes

    async def resident():
        return [Resident()]

    async def unload(tag):
        calls["unloaded"].append(tag)
        return True

    async def save_image(**kw):
        calls["saved"] = kw
        return "11111111-1111-1111-1111-111111111111"

    monkeypatch.setattr(service.comfyui, "available_models", available)
    monkeypatch.setattr(service.comfyui, "generate", generate)
    monkeypatch.setattr(service.comfyui, "free_memory", free_memory)
    monkeypatch.setattr(service.comfyui, "wait_until_released", released)

    async def no_reservation():
        return calls.get("reservation")

    monkeypatch.setattr(service.comfyui, "reservation", no_reservation)
    monkeypatch.setattr(service.ollama, "warm", warm)
    monkeypatch.setattr(service, "current_tag", lambda: "qwen3:8b")
    monkeypatch.setattr(service.ollama, "resident", resident)
    monkeypatch.setattr(service.ollama, "unload", unload)
    monkeypatch.setattr(service.store, "save_image", save_image)
    return calls


async def test_service_hands_gpu_over_and_stores(fake_service):
    result = await service.generate_for_chat(
        prompt="  a red fox  ", thread_id="t1", user_id="alice", size="portrait", seed=5
    )
    assert fake_service["unloaded"] == ["qwen3:8b"]
    assert fake_service["freed"]
    name, req = fake_service["request"]
    assert name == "z-image-turbo" and (req.width, req.height) == (832, 1216)
    assert req.prompt == "a red fox" and req.seed == 5 and req.steps == 9
    assert fake_service["saved"]["thread_id"] == "t1"
    assert fake_service["saved"]["user_id"] == "alice"
    assert result == {"image_id": "11111111-1111-1111-1111-111111111111",
                      "model": "z-image-turbo", "width": 832, "height": 1216,
                      "seed": 5, "prompt": "a red fox"}


async def test_service_frees_memory_even_on_failure(fake_service, monkeypatch):
    async def failing(model, req):
        raise ImageGenerationError("boom")
    monkeypatch.setattr(service.comfyui, "generate", failing)
    with pytest.raises(ImageGenerationError):
        await service.generate_for_chat(prompt="x", thread_id="t", user_id="u")
    assert fake_service["freed"]
    assert fake_service["saved"] is None


@pytest.mark.parametrize("kwargs, message", [
    ({"prompt": "  "}, "needs a description"),
    ({"prompt": "x", "model": "dall-e"}, "Unknown or unavailable"),
    ({"prompt": "x", "size": "huge"}, "Unknown size"),
])
async def test_service_rejects_bad_requests(fake_service, kwargs, message):
    with pytest.raises(ImageGenerationError, match=message):
        await service.generate_for_chat(thread_id="t", user_id="u", **kwargs)
    assert fake_service["unloaded"] == []


async def test_service_rejects_non_png(fake_service):
    fake_service["png"] = b"<html>not an image</html>"
    with pytest.raises(ImageGenerationError, match="not a PNG"):
        await service.generate_for_chat(prompt="x", thread_id="t", user_id="u")
    assert fake_service["saved"] is None


async def test_service_random_seed_when_unset(fake_service):
    result = await service.generate_for_chat(prompt="x", thread_id="t", user_id="u", seed=-1)
    assert 0 <= result["seed"] < 2**31


async def test_tool_needs_a_thread():
    from app.tools.builtin.images import generate_image
    out = await generate_image.ainvoke({"prompt": "x"}, config={"configurable": {}})
    assert out.startswith("Error:")


async def test_tool_returns_json_result(fake_service):
    from app.tools.builtin.images import generate_image
    out = await generate_image.ainvoke(
        {"prompt": "a cat"}, config={"configurable": {"thread_id": "t9", "user_id": "bob"}}
    )
    assert json.loads(out)["model"] == "z-image-turbo"
    assert fake_service["saved"]["user_id"] == "bob"


# --- history replay ----------------------------------------------------------

def _image_turn(with_text: bool):
    call = {"id": "c1", "name": "generate_image", "args": {"prompt": "a cat"}}
    messages = [
        HumanMessage(content="draw a cat"),
        AIMessage(content="", tool_calls=[call]),
        ToolMessage(content=json.dumps({"image_id": "abc"}), tool_call_id="c1"),
    ]
    if with_text:
        messages.append(AIMessage(content="Here is your cat."))
    return messages


def test_replay_keeps_image_turn_without_text():
    out = to_core_messages(_image_turn(with_text=False))
    assert [m["role"] for m in out] == ["user", "assistant"]
    part = out[1]["content"][0]
    assert part["toolName"] == "generate_image" and json.loads(part["result"])["image_id"] == "abc"


def test_replay_still_drops_other_textless_turns():
    call = {"id": "c1", "name": "current_time", "args": {}}
    messages = [HumanMessage(content="time?"), AIMessage(content="", tool_calls=[call]),
                ToolMessage(content="12:00", tool_call_id="c1")]
    assert [m["role"] for m in to_core_messages(messages)] == ["user"]


# --- PDF export --------------------------------------------------------------

def test_generated_image_id():
    part = {"type": "tool-call", "toolName": "generate_image",
            "result": json.dumps({"image_id": "abc"})}
    assert generated_image_id(part) == "abc"
    assert generated_image_id({**part, "result": "Error: busy"}) is None
    assert generated_image_id({**part, "toolName": "web_search"}) is None
    assert generated_image_id({**part, "isError": True}) is None


def _pdf_text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)


def _pdf_image_count(pdf: bytes) -> int:
    return sum(len(page.images) for page in PdfReader(BytesIO(pdf)).pages)


def test_export_embeds_image_with_prompt():
    messages = to_core_messages(_image_turn(with_text=True))
    pdf = render_chat_pdf("Cats", messages, images={"abc": (png_bytes(), "a cat on a mat")})
    assert _pdf_image_count(pdf) == 1
    text = _pdf_text(pdf)
    assert "a cat on a mat" in text and "Here is your cat." in text
    assert "generate_image" not in text


def test_export_marks_deleted_image():
    messages = to_core_messages(_image_turn(with_text=True))
    pdf = render_chat_pdf("Cats", messages, images={"abc": (None, "a cat")})
    assert _pdf_image_count(pdf) == 0
    assert "[Image deleted]" in _pdf_text(pdf)


def test_download_name():
    assert download_name("A Lighthouse at Dusk, watercolour!") == "a-lighthouse-at-dusk-watercolour.png"
    assert download_name("Кот") == "image.png"
    assert len(download_name("word " * 50)) <= 64


async def test_service_warms_chat_model_at_app_context(fake_service, monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_CTX", "24576")
    await service.generate_for_chat(prompt="x", thread_id="t", user_id="u")
    assert fake_service["warmed"] == ("qwen3:8b", 24576)


async def test_batch_hands_gpu_back_once(fake_service, monkeypatch):
    import asyncio
    started = []

    async def slow_generate(model, req):
        started.append(req.seed)
        await asyncio.sleep(0.01)
        return png_bytes()

    monkeypatch.setattr(service.comfyui, "generate", slow_generate)
    await asyncio.gather(*[
        service.generate_for_chat(prompt="x", thread_id="t", user_id="u", seed=i)
        for i in range(3)
    ])
    assert sorted(started) == [0, 1, 2]
    assert fake_service["handbacks"] == 1
    assert service._pending == 0


async def test_warns_when_chat_model_lands_on_cpu(fake_service, caplog):
    fake_service["vram"] = 17
    with caplog.at_level("WARNING"):
        await service.generate_for_chat(prompt="x", thread_id="t", user_id="u")
    assert "only 17% on the GPU" in caplog.text


async def test_wait_until_released():
    states = iter([8 * 2**30, 3 * 2**30, 96 * 2**20])

    def handler(request):
        used = next(states)
        return httpx.Response(200, json={"devices": [
            {"vram_total": 12 * 2**30, "vram_free": 12 * 2**30 - used, "torch_vram_total": used}]})

    client = httpx.AsyncClient(base_url="http://comfy.test", transport=httpx.MockTransport(handler))
    async with client:
        assert await comfyui.wait_until_released(client, timeout=5)


async def test_wait_until_released_gives_up():
    def handler(request):
        return httpx.Response(200, json={"devices": [
            {"vram_total": 12 * 2**30, "vram_free": 2**30, "torch_vram_total": 9 * 2**30}]})

    client = httpx.AsyncClient(base_url="http://comfy.test", transport=httpx.MockTransport(handler))
    async with client:
        assert not await comfyui.wait_until_released(client, timeout=0)


async def test_keep_alive_sends_data_chunks(monkeypatch):
    import asyncio
    from app import add_langgraph_route as route

    monkeypatch.setattr(route, "HEARTBEAT_SECONDS", 0.01)
    sent = []

    class Controller:
        def add_data(self, data):
            sent.append(data)

    task = asyncio.create_task(route.keep_alive(Controller()))
    await asyncio.sleep(0.05)
    task.cancel()
    assert sent and all(d == {"heartbeat": True} for d in sent)


async def test_own_job_on_queue_is_not_busy():
    fake = FakeComfy()
    fake.own_running = True
    async with fake.client() as client:
        await comfyui.free_memory(client)
        assert fake.freed
        png = await comfyui.generate(comfyui.MODELS["z-image-turbo"], request(), client)
    assert png == png_bytes()


async def test_wait_until_released_asks_again(monkeypatch):
    monkeypatch.setattr(comfyui, "RELEASE_TIMEOUT", 0.0)
    calls = {"free": 0, "stats": 0}

    def handler(request):
        if request.url.path == "/free":
            calls["free"] += 1
            return httpx.Response(200, json={})
        calls["stats"] += 1
        held = 9 * 2**30 if calls["free"] == 0 else 96 * 2**20
        return httpx.Response(200, json={"devices": [
            {"vram_total": 12 * 2**30, "vram_free": 12 * 2**30 - held, "torch_vram_total": held}]})

    client = httpx.AsyncClient(base_url="http://comfy.test", transport=httpx.MockTransport(handler))
    async with client:
        assert await comfyui.wait_until_released(client, timeout=2)
    assert calls["free"] == 1


# --- GPU reservations (agreed with the AI-1 agent) ---------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

NOW = datetime(2026, 11, 21, 17, 0, tzinfo=timezone.utc)


def test_parse_reservation_valid_and_expired():
    held = comfyui.parse_reservation(
        '{"holder": "ai-1", "until": "2026-11-21T18:00:00Z", "reason": "LTX batch"}', NOW)
    assert held.holder == "ai-1" and held.reason == "LTX batch" and not held.unreadable
    assert "until" in held.describe() and "LTX batch" in held.describe()
    assert comfyui.parse_reservation('{"holder": "ai-1", "until": "2026-11-21T16:59:59Z"}', NOW) is None


@pytest.mark.parametrize("text", [
    "not json", "{}", '{"until": 5}', '{"until": "2026-11-21T18:00:00"}',
    '{"until": "tomorrow Z"}', "[]",
])
def test_parse_reservation_fails_closed(text):
    held = comfyui.parse_reservation(text, NOW)
    assert held is not None and held.unreadable
    assert "could not be read" in held.describe()


def _reservation_client(status, body=""):
    def handler(request):
        assert request.url.path == "/api/userdata/gpu-reservation.json"
        return httpx.Response(status, text=body)
    return httpx.AsyncClient(base_url="http://comfy.test", transport=httpx.MockTransport(handler))


async def test_reservation_missing_file_is_free():
    async with _reservation_client(404) as client:
        assert await comfyui.reservation(client) is None


async def test_reservation_in_force():
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with _reservation_client(200, json.dumps({"holder": "ai-1", "until": until, "reason": "render"})) as client:
        held = await comfyui.reservation(client)
    assert held is not None and held.reason == "render"


async def test_reservation_unreadable_warns(caplog):
    async with _reservation_client(200, "{broken") as client:
        with caplog.at_level("WARNING"):
            held = await comfyui.reservation(client)
    assert held.unreadable and "Tell the owner" in caplog.text


async def test_reservation_unreachable_is_free():
    def down(request):
        raise httpx.ConnectError("refused")
    client = httpx.AsyncClient(base_url="http://comfy.test", transport=httpx.MockTransport(down))
    async with client:
        assert await comfyui.reservation(client) is None


async def test_service_refuses_during_reservation(fake_service):
    fake_service["reservation"] = comfyui.Reservation("ai-1", NOW + timedelta(hours=1), "render")
    with pytest.raises(ImageGenerationError, match="reserved by ai-1"):
        await service.generate_for_chat(prompt="x", thread_id="t", user_id="u")
    assert fake_service["unloaded"] == [] and "request" not in fake_service


async def test_handback_skipped_when_reserved_mid_batch(fake_service, monkeypatch):
    async def generate(model, req):
        # AI-1 takes a reservation while the image is being generated.
        fake_service["reservation"] = comfyui.Reservation("ai-1", NOW + timedelta(hours=1), "")
        return png_bytes()

    monkeypatch.setattr(service.comfyui, "generate", generate)
    await service.generate_for_chat(prompt="x", thread_id="t", user_id="u")
    assert not fake_service["freed"] and "warmed" not in fake_service


async def test_wait_until_released_needs_room_for_chat_model(monkeypatch):
    monkeypatch.setenv("IMAGE_HANDBACK_FREE_BYTES", str(8.5e9))
    frees = iter([5.1e9, 8.0e9, 11.7e9])
    polls = []

    def handler(request):
        if request.url.path == "/free":
            return httpx.Response(200, json={})
        polls.append(1)
        return httpx.Response(200, json={"devices": [{"vram_total": 12.5e9, "vram_free": next(frees),
                                                     "torch_vram_total": 0}]})

    client = httpx.AsyncClient(base_url="http://comfy.test", transport=httpx.MockTransport(handler))
    async with client:
        assert await comfyui.wait_until_released(client, timeout=5)
    # 5.1 GB free once had the chat model load 21/37 layers on the GPU: not enough.
    assert len(polls) == 3


async def test_chat_turn_during_reservation_is_logged(monkeypatch, caplog):
    from app import add_langgraph_route as route

    async def held():
        return comfyui.Reservation("ai-1", NOW + timedelta(hours=1), "render")

    monkeypatch.setattr(route, "comfyui_reservation", held)
    with caplog.at_level("WARNING"):
        await route.note_reservation_conflict("t1")
    assert "chat turn during a GPU reservation" in caplog.text


async def test_handback_reloads_once_when_chat_model_misses_the_gpu(fake_service, caplog):
    # resident() calls: the unload before the image, the first reload (61% on the
    # GPU), the second reload (fully on the GPU).
    fake_service["vram"] = [100, 61, 100]
    with caplog.at_level("INFO"):
        await service.generate_for_chat(prompt="x", thread_id="t", user_id="u")
    assert fake_service["unloaded"] == ["qwen3:8b", "qwen3:8b"]   # before the image, then the retry
    assert "reloading it once" in caplog.text and "replies will be slow" not in caplog.text


def test_handback_threshold_covers_ollamas_need(monkeypatch):
    monkeypatch.delenv("IMAGE_HANDBACK_FREE_BYTES", raising=False)
    # 7065 MiB model + 1 GiB fit margin + 457 MiB minimum, as Ollama computes it.
    assert comfyui.handback_free_bytes() > (7065 + 1024 + 457) * 2**20


def test_free_for_others_excludes_torch_cache():
    device = {"vram_free": int(10e9), "torch_vram_free": int(3e9)}
    assert comfyui.free_for_others(device) == int(7e9)
    assert comfyui.free_for_others({"vram_free": int(9e9)}) == int(9e9)


async def test_wait_until_released_ignores_memory_torch_still_holds(monkeypatch):
    monkeypatch.setenv("IMAGE_HANDBACK_FREE_BYTES", str(9.5e9))
    states = iter([(11.0e9, 3.0e9), (11.0e9, 0.2e9)])   # (vram_free, torch_vram_free)
    polls = []

    def handler(request):
        if request.url.path == "/free":
            return httpx.Response(200, json={})
        free, cached = next(states)
        polls.append(1)
        return httpx.Response(200, json={"devices": [{"vram_total": 12.5e9, "vram_free": free,
                                                     "torch_vram_free": cached}]})

    client = httpx.AsyncClient(base_url="http://comfy.test", transport=httpx.MockTransport(handler))
    async with client:
        assert await comfyui.wait_until_released(client, timeout=5)
    assert len(polls) == 2   # 11 GB "free" with 3 GB still in torch's cache was not enough
