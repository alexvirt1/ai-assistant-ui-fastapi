"""Reloading the chat model when something left it partly on the CPU."""

import asyncio
import logging

import pytest

from app.images import comfyui, service
from app.models.ollama import ResidentModel

GB = 1_000_000_000


@pytest.fixture
def gpu(monkeypatch):
    state = {"resident": [], "reserved": None, "busy": False, "calls": []}

    async def resident():
        return state["resident"]

    async def unload(tag):
        state["calls"].append(f"unload {tag}")
        return True

    async def hand_back():
        state["calls"].append("hand back")

    async def reservation():
        return state["reserved"]

    async def queue_busy():
        return state["busy"]

    monkeypatch.setenv("COMFYUI_BASE_URL", "http://comfy")
    monkeypatch.setattr(service.ollama, "resident", resident)
    monkeypatch.setattr(service.ollama, "unload", unload)
    monkeypatch.setattr(service, "_return_gpu_to_chat", hand_back)
    monkeypatch.setattr(service, "current_tag", lambda: "qwen3:8b")
    monkeypatch.setattr(comfyui, "reservation", reservation)
    monkeypatch.setattr(comfyui, "queue_busy", queue_busy)
    monkeypatch.setattr(service, "_last_heal", float("-inf"))
    monkeypatch.setattr(service, "_pending", 0)
    return state


def model(on_gpu, size=6 * GB, tag="qwen3:8b"):
    return ResidentModel(tag=tag, size_bytes=size, expires_at="", vram_bytes=on_gpu)


async def test_a_model_partly_on_the_cpu_is_reloaded(gpu, caplog):
    gpu["resident"] = [model(3.3 * GB)]
    with caplog.at_level(logging.WARNING):
        assert await service.heal_chat_model()
    assert gpu["calls"] == ["unload qwen3:8b", "hand back"]
    assert "chat model was partly on CPU (qwen3:8b 55% on the GPU)" in caplog.text


@pytest.mark.parametrize("resident", [[], [model(6 * GB)], [model(1 * GB, tag="bge-m3:latest")]])
async def test_nothing_to_heal(gpu, resident):
    gpu["resident"] = resident
    assert not await service.heal_chat_model()
    assert gpu["calls"] == []


async def test_at_most_once_per_interval(gpu):
    gpu["resident"] = [model(3 * GB)]
    assert await service.heal_chat_model()
    assert not await service.heal_chat_model()
    assert gpu["calls"] == ["unload qwen3:8b", "hand back"]


@pytest.mark.parametrize("blocker", ["reserved", "busy", "pending", "no comfyui"])
async def test_left_alone(gpu, monkeypatch, blocker):
    gpu["resident"] = [model(3 * GB)]
    if blocker == "reserved":
        gpu["reserved"] = object()          # AI-1's reservation: no Ollama calls
    elif blocker == "busy":
        gpu["busy"] = True                   # another client's ComfyUI job
    elif blocker == "pending":
        monkeypatch.setattr(service, "_pending", 1)   # our image job unloaded it on purpose
    else:
        monkeypatch.delenv("COMFYUI_BASE_URL")
    assert not await service.heal_chat_model()
    assert gpu["calls"] == []


async def test_while_an_image_holds_the_lock(gpu):
    gpu["resident"] = [model(3 * GB)]
    async with service._lock:
        assert not await service.heal_chat_model()
    assert gpu["calls"] == []


async def test_errors_never_reach_the_turn(gpu, monkeypatch):
    async def broken():
        raise RuntimeError("Ollama down")

    monkeypatch.setattr(service.ollama, "resident", broken)
    assert not await service.heal_chat_model()
