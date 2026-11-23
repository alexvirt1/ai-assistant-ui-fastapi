"""Reference photos: cleaning uploads, and the endpoints (store mocked, no Postgres)."""

import uuid
from datetime import datetime, timezone
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.chats import store as chat_store
from app.references import normalize as norm
from app.references import routes, store
from app.references.store import StoredReference


def encode(image: Image.Image, fmt: str, **kw) -> bytes:
    buf = BytesIO()
    image.save(buf, format=fmt, **kw)
    return buf.getvalue()


def decoded(png: bytes) -> Image.Image:
    image = Image.open(BytesIO(png))
    image.load()
    return image


# --- cleaning uploads --------------------------------------------------------

def test_png_kept_as_is():
    out = norm.normalize(encode(Image.new("RGB", (640, 480), (10, 20, 30)), "PNG"))
    assert (out.width, out.height, out.source_format) == (640, 480, "PNG")
    assert decoded(out.png).getpixel((5, 5)) == (10, 20, 30)


def test_jpeg_turned_upright_and_stripped_of_metadata():
    exif = Image.Exif()
    exif[0x0112] = 6                       # orientation: rotate 90° to display
    exif[0x010F] = "PhoneMaker"            # camera make
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (45.0, 25.0, 0.0)   # a GPS position
    raw = encode(Image.new("RGB", (400, 300), (200, 50, 50)), "JPEG", exif=exif.tobytes())
    assert Image.open(BytesIO(raw)).getexif().get(0x0112) == 6

    out = norm.normalize(raw)
    assert (out.width, out.height) == (300, 400)          # upright
    image = decoded(out.png)
    assert image.format == "PNG"
    assert not image.getexif() and "exif" not in image.info
    assert b"PhoneMaker" not in out.png and b"GPS" not in out.png


def test_animated_gif_gives_its_first_frame():
    frames = [Image.new("P", (64, 64), i) for i in (1, 2)]
    for frame, colour in zip(frames, [(255, 0, 0), (0, 0, 255)]):
        frame.putpalette([0, 0, 0, *colour, *colour] + [0] * 759)
    raw = encode(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    out = norm.normalize(raw)
    assert out.source_format == "GIF"
    assert decoded(out.png).convert("RGB").getpixel((10, 10)) == (255, 0, 0)


def test_webp_accepted():
    out = norm.normalize(encode(Image.new("RGB", (100, 80), (0, 128, 0)), "WEBP", lossless=True))
    assert (out.source_format, out.width, out.height) == ("WEBP", 100, 80)


def test_transparency_flattened_onto_white():
    image = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    image.paste((255, 0, 0, 255), (0, 0, 25, 50))
    out = decoded(norm.normalize(encode(image, "PNG")).png)
    assert out.mode == "RGB"
    assert out.getpixel((40, 10)) == (255, 255, 255) and out.getpixel((10, 10)) == (255, 0, 0)


def test_large_photo_scaled_to_2048():
    out = norm.normalize(encode(Image.new("RGB", (4000, 1000), (1, 2, 3)), "PNG"))
    assert (out.width, out.height) == (2048, 512)


@pytest.mark.parametrize("raw, message", [
    (b"not an image at all", "not an image"),
    (encode(Image.new("RGB", (10, 10)), "BMP"), "PNG, JPEG, GIF or WebP"),
    (b"\0" * (norm.MAX_UPLOAD_BYTES + 1), "limit is 20 MB"),
])
def test_unusable_uploads_refused(raw, message):
    with pytest.raises(norm.ReferenceError, match=message):
        norm.normalize(raw)


def test_decompression_bomb_refused(monkeypatch):
    monkeypatch.setattr(norm, "MAX_PIXELS", 1000)
    with pytest.raises(norm.ReferenceError, match="too large"):
        norm.normalize(encode(Image.new("RGB", (100, 100)), "PNG"))


# --- endpoints ----------------------------------------------------------------

NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)


def ref(png=b"\x89PNG...", deleted=False, **kw):
    return StoredReference(id=kw.get("id", uuid.uuid4()), thread_id="t1", name="dog.jpg",
                           width=300, height=400, png=None if deleted else png,
                           created_at=NOW, deleted_at=NOW if deleted else None)


@pytest.fixture
def api(monkeypatch):
    state = {"refs": {}, "claimed": [], "foreign": set()}

    async def claim_thread(thread_id, user_id, **kw):
        if thread_id in state["foreign"]:
            raise chat_store.ThreadOwnershipError(thread_id)
        state["claimed"].append(thread_id)

    async def save(**kw):
        saved = ref(png=kw["png"])
        state["refs"][saved.id] = saved
        state["saved"] = kw
        return saved

    async def get(ref_id, user_id):
        return state["refs"].get(ref_id)

    async def delete(ref_id, user_id):
        return ref_id in state["refs"]

    monkeypatch.setattr(chat_store, "claim_thread", claim_thread)
    monkeypatch.setattr(store, "save", save)
    monkeypatch.setattr(store, "get", get)
    monkeypatch.setattr(store, "delete", delete)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), state


def jpeg() -> bytes:
    return encode(Image.new("RGB", (400, 300), (9, 9, 9)), "JPEG")


def test_upload_registers_the_chat_and_stores_a_clean_png(api):
    client, state = api
    res = client.post("/api/references", data={"threadId": "t1"},
                      files={"file": ("dog.jpg", jpeg(), "image/jpeg")})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "dog.jpg" and not body["deleted"]
    assert state["claimed"] == ["t1"]
    assert state["saved"]["png"].startswith(b"\x89PNG") and state["saved"]["width"] == 400


def test_upload_to_someone_elses_chat_is_refused(api):
    client, state = api
    state["foreign"].add("t2")
    res = client.post("/api/references", data={"threadId": "t2"},
                      files={"file": ("dog.jpg", jpeg(), "image/jpeg")})
    assert res.status_code == 403 and "saved" not in state


def test_upload_of_a_non_image_is_refused(api):
    client, state = api
    res = client.post("/api/references", data={"threadId": "t1"},
                      files={"file": ("notes.txt", b"hello", "text/plain")})
    assert res.status_code == 415 and "saved" not in state and state["claimed"] == []


def test_get_and_delete(api):
    client, state = api
    live, gone = ref(png=b"\x89PNGdata"), ref(deleted=True)
    state["refs"].update({live.id: live, gone.id: gone})
    res = client.get(f"/api/references/{live.id}")
    assert res.status_code == 200 and res.content == b"\x89PNGdata"
    assert res.headers["content-type"] == "image/png"
    assert client.get(f"/api/references/{gone.id}").status_code == 410
    assert client.get(f"/api/references/{uuid.uuid4()}").status_code == 404
    assert client.delete(f"/api/references/{live.id}").status_code == 204
    assert client.delete(f"/api/references/{uuid.uuid4()}").status_code == 404
