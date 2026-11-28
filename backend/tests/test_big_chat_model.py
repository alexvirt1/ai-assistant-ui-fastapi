"""A chat model as large as the GPU: model-sized hand-back, and AI-1's reservations."""

import httpx
import pytest

from app.images import comfyui, service
from app.models.ollama import ResidentModel

GB = 1_000_000_000


@pytest.fixture
def sizes(monkeypatch):
    disk = {}

    async def disk_size(tag):
        return disk.get(tag)

    monkeypatch.setattr(service.ollama, "disk_size", disk_size)
    monkeypatch.setattr(service, "_model_sizes", {})
    monkeypatch.delenv("IMAGE_HANDBACK_FREE_BYTES", raising=False)
    return disk


async def test_handback_waits_for_what_the_model_needs(sizes):
    service._model_sizes["qwen3.6"] = int(11.5 * GB)       # seen resident
    assert await service.handback_needed("qwen3.6") == int(11.5 * GB + 1.6 * GB)


async def test_a_small_model_keeps_the_usual_threshold(sizes):
    service._model_sizes["qwen3:8b"] = int(5.7 * GB)
    assert await service.handback_needed("qwen3:8b") == int(9.5e9)


async def test_a_model_not_seen_yet_is_estimated_from_its_file(sizes):
    sizes["qwen3.6"] = int(11.5 * GB)
    assert await service.handback_needed("qwen3.6") == int(11.5 * GB + 0.8 * GB + 1.6 * GB)
    assert await service.handback_needed("unknown") == int(9.5e9)


def stats(free_for_others, total=12.49 * GB):
    return {"devices": [{"vram_total": int(total), "vram_free": int(free_for_others),
                         "torch_vram_free": 0}]}


async def test_wait_target_is_capped_by_an_empty_card():
    # 13.1 GB wanted on a 12.49 GB card: "ComfyUI holds nothing" is enough.
    def handler(request):
        return httpx.Response(200, json=stats(12.16 * GB))   # measured: ComfyUI empty

    async with httpx.AsyncClient(base_url="http://comfy",
                                 transport=httpx.MockTransport(handler)) as client:
        assert await comfyui.wait_until_released(client, timeout=1, needed=int(13.1 * GB))


async def test_wait_still_waits_while_comfyui_holds_memory():
    def handler(request):
        return httpx.Response(200, json=stats(4 * GB))

    async with httpx.AsyncClient(base_url="http://comfy",
                                 transport=httpx.MockTransport(handler)) as client:
        assert not await comfyui.wait_until_released(client, timeout=0, needed=int(13.1 * GB))


# --- during AI-1's reservation ----------------------------------------------------

@pytest.fixture
def reserved(monkeypatch, sizes):
    state = {"held": comfyui.parse_reservation(
        '{"holder": "ai-1", "until": "2999-01-01T00:00:00Z", "reason": "renders"}'),
        "resident": [], "tag": "qwen3.6"}

    async def reservation():
        return state["held"]

    async def resident():
        return state["resident"]

    monkeypatch.setattr(comfyui, "reservation", reservation)
    monkeypatch.setattr(service.ollama, "resident", resident)
    monkeypatch.setattr(service, "current_tag", lambda: state["tag"])
    sizes["qwen3.6"] = int(11.5 * GB)
    sizes["qwen3:8b"] = int(5.2 * GB)
    return state


async def test_big_model_answers_from_the_cpu_with_a_small_one(reserved, monkeypatch):
    monkeypatch.setenv("RESERVED_GPU_CHAT", "cpu")
    plan = await service.reserved_gpu_plan()
    assert plan.action == "cpu" and plan.model == "qwen3:8b"
    assert plan.num_gpu == 0 and plan.num_thread == 6
    assert "reserved by ai-1" in plan.note and "on the CPU" in plan.note


async def test_busy_by_default(reserved):
    # qwen3:8b on AI-1's CPU writes 3.7 tok/s: too slow to answer with.
    plan = await service.reserved_gpu_plan()
    assert plan.action == "busy" and "can't answer until then" in plan.note


@pytest.mark.parametrize("case", ["no reservation", "small model", "already loaded"])
async def test_usual_turn(reserved, case):
    if case == "no reservation":
        reserved["held"] = None
    elif case == "small model":
        reserved["tag"] = "qwen3:8b"
    else:
        reserved["resident"] = [ResidentModel("qwen3.6", int(11.5 * GB), "", int(11.5 * GB))]
    assert await service.reserved_gpu_plan() is None


async def test_errors_fall_back_to_the_usual_turn(reserved, monkeypatch):
    async def broken():
        raise RuntimeError("down")

    monkeypatch.setattr(comfyui, "reservation", broken)
    assert await service.reserved_gpu_plan() is None


async def test_chat_model_is_big(reserved):
    assert await service.chat_model_is_big()
    reserved["tag"] = "qwen3:8b"
    assert not await service.chat_model_is_big()


def test_question_embedder_can_stay_off_the_gpu():
    from app.documents.embeddings import make_embedder

    on_cpu, tag = make_embedder(on_cpu=True)
    usual, same_tag = make_embedder()
    assert on_cpu.num_gpu == 0 and usual.num_gpu is None and tag == same_tag


def test_idle_reserve_default_is_measured(monkeypatch):
    monkeypatch.delenv("GPU_IDLE_RESERVE_BYTES", raising=False)
    assert comfyui.idle_reserve_bytes() == int(0.4e9)


@pytest.mark.parametrize("big", [True, False])
async def test_document_question_skips_the_embedding_beside_a_big_model(monkeypatch, big):
    import types
    import uuid as uuidlib

    from app.tools import document_search as ds

    made = []

    def make_embedder(role="embedding", *, on_cpu=False):
        made.append(on_cpu)
        return types.SimpleNamespace(on_cpu=on_cpu), "qwen3-embedding:0.6b"

    async def chat_model_is_big():
        return big

    async def get_document(doc_id):
        return types.SimpleNamespace(name="kafka.pdf")

    async def nothing(*a, **k):
        return None

    async def chunks(doc_id, model):
        return [object()], ["text"]

    used = []

    async def embed_query(question, embedder):
        used.append(embedder.on_cpu)
        return [0.1]

    monkeypatch.setattr(ds, "make_embedder", make_embedder)
    monkeypatch.setattr(ds.image_service, "chat_model_is_big", chat_model_is_big)
    monkeypatch.setattr(ds, "get_document", get_document)
    monkeypatch.setattr(ds, "ensure_indexed", nothing)
    monkeypatch.setattr(ds, "load_retrieval_chunks", chunks)
    monkeypatch.setattr(ds, "embed_query", embed_query)
    monkeypatch.setattr(ds, "hybrid_search", lambda *a, **k: [])
    await ds.search_document.ainvoke({"document_id": str(uuidlib.uuid4()), "question": "retention?"})
    # Beside a big model: no embedding call at all (it would evict the model);
    # keyword search only. Otherwise the usual GPU embedding.
    assert used == ([] if big else [False])
    assert made == [False]        # indexing keeps the GPU
