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


def stats(free_for_others, total=12.4 * GB):
    return {"devices": [{"vram_total": int(total), "vram_free": int(free_for_others),
                         "torch_vram_free": 0}]}


async def test_wait_target_is_capped_by_an_empty_card():
    # 13.1 GB wanted on a 12.4 GB card: "ComfyUI holds nothing" is enough.
    def handler(request):
        return httpx.Response(200, json=stats(11.9 * GB))

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


async def test_big_model_answers_from_the_cpu_with_a_small_one(reserved):
    plan = await service.reserved_gpu_plan()
    assert plan.action == "cpu" and plan.model == "qwen3:8b"
    assert plan.num_gpu == 0 and plan.num_thread == 6
    assert "reserved by ai-1" in plan.note and "on the CPU" in plan.note


async def test_busy_mode_says_so(reserved, monkeypatch):
    monkeypatch.setenv("RESERVED_GPU_CHAT", "busy")
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
