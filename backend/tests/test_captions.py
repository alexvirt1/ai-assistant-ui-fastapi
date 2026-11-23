"""Captions of reference photos (vision model mocked) and their use in the image prompt."""

import json
import uuid
from datetime import datetime, timezone
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.images import comfyui, service
from app.langgraph.agent import render_reference_block
from app.references import caption as cap
from app.references.store import StoredReference


@pytest.fixture(autouse=True)
def captioning_on(monkeypatch):
    monkeypatch.setenv("REFERENCE_CAPTION_MODEL", "qwen3-vl:8b")


def png(colour=(200, 30, 30), size=(64, 48)) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


def client_answering(content=None, status=200, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        if status != 200:
            return httpx.Response(status, json={"error": "model not found"})
        return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_caption_asks_the_vision_model_once_and_lets_it_unload():
    seen = []
    text = await cap.caption(png(), client_answering("A man with a red bandana.", seen=seen))
    assert text == "A man with a red bandana."
    body = seen[0]
    assert body["model"] == "qwen3-vl:8b" and body["keep_alive"] == 0 and body["think"] is False
    assert body["options"]["num_predict"] <= 100
    assert len(body["messages"][0]["images"]) == 1
    # Prefilled empty thinking block: qwen3-vl otherwise reasons until num_predict runs out.
    assert body["messages"][-1] == {"role": "assistant", "content": "<think>\n\n</think>\n\n"}


@pytest.mark.parametrize("raw, expected", [
    ("<think>hmm, a pirate</think>\n**A pirate** with dreadlocks.", "A pirate with dreadlocks."),
    ('"A creature whose beard is octopus tentacles."', "A creature whose beard is octopus tentacles."),
    ("A man\nin a hat.", "A man in a hat."),
])
def test_clean(raw, expected):
    assert cap.clean(raw) == expected


def test_long_captions_are_cut_at_a_word():
    text = cap.clean("word " * 200)
    assert len(text) <= cap.MAX_CAPTION_CHARS + 1 and text.endswith("…")


async def test_a_failing_vision_model_gives_no_caption():
    assert await cap.caption(png(), client_answering(status=404)) is None
    assert await cap.caption(png(), client_answering("   ")) is None


async def test_captioning_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("REFERENCE_CAPTION_MODEL", "")
    assert await cap.caption(png(), client_answering("x")) is None


def test_captions_are_appended_by_image_number():
    out = cap.prompt_with_captions("Two pirates in a pub", ["A man in a red bandana.", None,
                                                            "A tentacle-bearded captain."])
    assert out == ("Two pirates in a pub\n\nImage 1 shows: A man in a red bandana.\n"
                   "Image 3 shows: A tentacle-bearded captain.")
    assert cap.prompt_with_captions("p", [None]) == "p"


NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)


def ref(caption=None):
    return StoredReference(id=uuid.uuid4(), thread_id="t1", name="dog.jpg", width=64, height=48,
                           png=png(), created_at=NOW, deleted_at=None, caption=caption)


def test_the_chat_model_sees_known_captions():
    block = render_reference_block([ref("A brown dog."), ref()])
    assert "shows: A brown dog." in block
    assert block.count("shows:") == 1


@pytest.fixture
def gpu(monkeypatch):
    calls = []

    async def record(name, result=None):
        calls.append(name)
        return result

    monkeypatch.setattr(comfyui, "free_memory", lambda *a, **k: record("free"))
    monkeypatch.setattr(comfyui, "wait_until_released", lambda *a, **k: record("wait", True))
    monkeypatch.setattr(service.ollama, "unload", lambda tag: record(f"unload {tag}", True))
    return calls


async def test_only_new_photos_are_captioned_and_stored(monkeypatch, gpu):
    stored = []
    asked = []

    async def fake_caption(data):
        asked.append(data)
        return "A creature whose beard is octopus tentacles."

    async def set_caption(ref_id, text):
        stored.append((ref_id, text))

    monkeypatch.setattr(service.captions, "caption", fake_caption)
    monkeypatch.setattr(service.reference_store, "set_caption", set_caption)
    known, new = ref("A man in a red bandana."), ref()
    texts = await service._captions([known, new])
    assert texts == ["A man in a red bandana.", "A creature whose beard is octopus tentacles."]
    assert asked == [new.png] and stored == [(new.id, texts[1])]
    # ComfyUI freed before the vision model loads; the vision model unloaded after.
    assert gpu == ["free", "wait", "unload qwen3-vl:8b"]


async def test_nothing_happens_when_all_photos_have_captions(monkeypatch, gpu):
    async def never(_):
        raise AssertionError("must not caption again")

    monkeypatch.setattr(service.captions, "caption", never)
    assert await service._captions([ref("A dog.")]) == ["A dog."]
    assert gpu == []


async def test_a_failed_caption_leaves_the_photo_without_one(monkeypatch, gpu):
    async def fail(_):
        return None

    monkeypatch.setattr(service.captions, "caption", fail)
    monkeypatch.setattr(service.reference_store, "set_caption",
                        lambda *a: (_ for _ in ()).throw(AssertionError("nothing to store")))
    assert await service._captions([ref()]) == [None]
