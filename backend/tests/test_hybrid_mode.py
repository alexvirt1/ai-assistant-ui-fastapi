"""Hybrid mode: cloud model config, routing, what Claude is shown, streaming.

No network: the Anthropic API is never called. Streaming is exercised through
the real chat route with a fake graph that emits what langchain-anthropic does.
"""

import json

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from app import add_langgraph_route as route_mod
from app.chats.messages import to_core_messages
from app.cloud import models as cloud_models
from app.cloud import usage as cloud_usage
from app.cloud.history import cloud_view
from app.langgraph import agent


@pytest.fixture
def cloud_config(tmp_path, monkeypatch):
    path = tmp_path / "cloud_models.yaml"
    path.write_text(
        "- name: claude-sonnet\n  label: Claude Sonnet 5.5\n  provider: anthropic\n"
        "  model: claude-sonnet-5-5\n  api_key_env: TEST_ANTHROPIC_KEY\n  effort: medium\n"
        "  max_tokens: 8000\n  price_per_mtok: {input: 2.0, output: 10.0}\n")
    monkeypatch.setenv("CLOUD_MODELS_CONFIG", str(path))
    monkeypatch.delenv("CLOUD_MODEL", raising=False)
    cloud_models.make_chat_model.cache_clear()
    yield path
    cloud_models.make_chat_model.cache_clear()


# --- config ----------------------------------------------------------------

def test_model_offered_only_with_its_key(cloud_config, monkeypatch):
    monkeypatch.delenv("TEST_ANTHROPIC_KEY", raising=False)
    assert cloud_models.default_model() is None
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "sk-test")
    spec = cloud_models.default_model()
    assert spec.name == "claude-sonnet" and spec.label == "Claude Sonnet 5.5"


def test_cost_and_lookup(cloud_config):
    spec = cloud_models.load_models()[0]
    assert spec.cost(4000, 1000) == pytest.approx(0.018)
    assert cloud_models.by_model_id("claude-sonnet-5-5").name == "claude-sonnet"
    assert cloud_models.by_model_id("qwen3:8b") is None


def test_shipped_config_is_sonnet_5_5(monkeypatch):
    monkeypatch.delenv("CLOUD_MODELS_CONFIG", raising=False)
    spec = cloud_models.load_models()[0]
    assert (spec.model, spec.api_key_env) == ("claude-sonnet-5-5", "ANTHROPIC_API_KEY")


def test_claude_request_settings(cloud_config, monkeypatch):
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "sk-test")
    model = cloud_models.make_chat_model("claude-sonnet")
    payload = model._get_request_payload([SystemMessage("s"), HumanMessage("hi")])
    assert payload["model"] == "claude-sonnet-5-5"
    assert payload["output_config"] == {"effort": "medium"}
    assert payload["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in payload["betas"]
    # Current Claude models reject non-default sampling settings.
    assert "temperature" not in payload


# --- what Claude is shown --------------------------------------------------

def test_cloud_view_keeps_text_and_drops_old_tool_traffic():
    messages = [
        HumanMessage("summarise my contract"),
        AIMessage("", tool_calls=[{"id": "c1", "name": "search_document", "args": {"query": "x"}}]),
        ToolMessage("SECRET PASSAGE FROM THE CONTRACT", tool_call_id="c1"),
        AIMessage("It is a lease for 2 years."),
        HumanMessage("what is 467521 x 278985?"),
        AIMessage("", tool_calls=[{"id": "c2", "name": "escalate", "args": {"reason": "exact arithmetic"}}]),
        ToolMessage('{"proposed": true}', tool_call_id="c2"),
        AIMessage("I suggest asking the cloud model."),
        HumanMessage("Answer my last question with Claude."),
    ]
    view = cloud_view(messages)
    text = " ".join(str(m.content) for m in view)
    assert "SECRET PASSAGE" not in text
    assert not any(isinstance(m, ToolMessage) for m in view)
    assert "suggested handing this question to a cloud model: exact arithmetic" in text
    assert [type(m).__name__ for m in view] == ["HumanMessage", "AIMessage", "HumanMessage",
                                                "AIMessage", "HumanMessage"]
    assert view[-1].content == "Answer my last question with Claude."


def test_cloud_view_keeps_the_current_turn_whole():
    call = {"id": "t1", "name": "current_time", "args": {}}
    messages = [HumanMessage("time?"), AIMessage("", tool_calls=[call]),
                ToolMessage("12:00", tool_call_id="t1")]
    view = cloud_view(messages)
    assert isinstance(view[-1], ToolMessage) and view[1].tool_calls[0]["id"] == "t1"


@tool
def search_document(query: str) -> str:
    """Search."""
    return ""


@tool
def escalate(reason: str) -> str:
    """Escalate."""
    return ""


@tool
def current_time() -> str:
    """Time."""
    return ""


TOOLS = [search_document, escalate, current_time]


@pytest.mark.parametrize("route, expected", [
    ({}, {"search_document", "current_time"}),
    ({"answer_with": "local", "chat_mode": "local"}, {"search_document", "current_time"}),
    ({"answer_with": "local", "chat_mode": "auto"}, {"search_document", "escalate", "current_time"}),
    ({"answer_with": "cloud", "chat_mode": "cloud"}, {"current_time"}),
    ({"answer_with": "cloud", "chat_mode": "auto", "allow_documents": True},
     {"search_document", "current_time"}),
])
def test_tools_per_route(route, expected):
    assert {t.name for t in agent.tools_for(route, TOOLS)} == expected


def test_cloud_prompt_documents_only_when_allowed():
    messages = [HumanMessage("question")]
    block = "Documents attached to this conversation: id=1"
    denied = agent.cloud_prompt("BASE", messages, block,
                                {"cloud_label": "Claude Sonnet 5.5", "allow_documents": False})
    allowed = agent.cloud_prompt("BASE", messages, block,
                                 {"cloud_label": "Claude Sonnet 5.5", "allow_documents": True})
    assert denied[0].content.startswith("You are Claude Sonnet 5.5.")
    assert "Documents attached" not in denied[0].content
    assert "Documents attached" in allowed[0].content


# --- routing ---------------------------------------------------------------

class Req:
    def __init__(self, mode=None, escalate=None, docs=None):
        self.mode, self.escalate, self.allowCloudDocuments = mode, escalate, docs


@pytest.fixture
def routing(cloud_config, monkeypatch):
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "sk-test")
    state = {"spent": 0.0}

    async def month_total(user_id):
        return state["spent"]

    monkeypatch.setattr(cloud_usage, "month_total", month_total)
    monkeypatch.setenv("CLOUD_BUDGET_USD", "10")
    return state


@pytest.mark.parametrize("req, answer_with", [
    (Req("local"), "local"), (Req("auto"), "local"), (Req("cloud"), "cloud"),
    (Req("auto", escalate=True), "cloud"), (Req("local", escalate=True), "cloud"),
])
async def test_decide_route(routing, req, answer_with):
    route, note = await route_mod.decide_route(req, "t1", "alice")
    assert route["answer_with"] == answer_with and note is None
    if answer_with == "cloud":
        assert route["cloud_model"] == "claude-sonnet" and route["cloud_label"] == "Claude Sonnet 5.5"


async def test_over_budget_stays_local(routing):
    routing["spent"] = 10.5
    route, note = await route_mod.decide_route(Req("cloud"), "t1", "alice")
    assert route["answer_with"] == "local" and "budget is used up" in note


async def test_no_key_stays_local(routing, monkeypatch):
    monkeypatch.delenv("TEST_ANTHROPIC_KEY")
    route, note = await route_mod.decide_route(Req("cloud"), "t1", "alice")
    assert route["answer_with"] == "local" and route["chat_mode"] == "local"
    assert "No cloud model is configured" in note


# --- streaming through the real chat route ----------------------------------

ANTHROPIC = {"ls_provider": "anthropic"}


def chunk(msg_id, content="", tool_call_chunks=None, usage=None):
    return AIMessageChunk(id=msg_id, content=content, tool_call_chunks=tool_call_chunks or [],
                          usage_metadata=usage,
                          response_metadata={"model_provider": "anthropic"})


class FakeCloudGraph:
    async def astream(self, inputs, config, stream_mode):
        self.config = config
        u = lambda i, o: {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}  # noqa: E731
        # Step 1: a tool call streamed in pieces, as Claude sends it.
        yield chunk("m1", [], usage=u(1200, 0)), ANTHROPIC
        yield chunk("m1", tool_call_chunks=[{"id": "toolu_1", "name": "current_time", "args": "", "index": 1}]), ANTHROPIC
        yield chunk("m1", tool_call_chunks=[{"id": None, "name": None, "args": '{"tz": ', "index": 1}]), ANTHROPIC
        yield chunk("m1", tool_call_chunks=[{"id": None, "name": None, "args": '"UTC"}', "index": 1}], usage=u(0, 20)), ANTHROPIC
        yield ToolMessage("12:00", tool_call_id="toolu_1"), {}
        # Step 2: the answer, streamed as deltas - two identical in a row.
        yield chunk("m2", [{"type": "text", "text": "a", "index": 0}], usage=u(1300, 0)), ANTHROPIC
        yield chunk("m2", [{"type": "text", "text": "a", "index": 0}]), ANTHROPIC
        yield chunk("m2", [{"type": "text", "text": " it is noon", "index": 0}], usage=u(0, 30)), ANTHROPIC


def run_route(graph, body, monkeypatch, routing_state=None):
    recorded = []

    async def record(**kw):
        recorded.append(kw)

    monkeypatch.setattr(cloud_usage, "record", record)
    app = FastAPI()
    route_mod.add_langgraph_route(app, graph, "/api/chat")

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            res = await client.post("/api/chat", json=body)
            return res.text

    return go(), recorded


async def test_cloud_turn_streams_text_tools_and_badge(routing, monkeypatch):
    graph = FakeCloudGraph()
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": "time?"}]}],
            "threadId": "t1", "tools": [], "mode": "cloud"}
    coro, recorded = run_route(graph, body, monkeypatch)
    text = await coro
    lines = [ln for ln in text.splitlines() if ln]
    deltas = "".join(json.loads(ln[2:]) for ln in lines if ln.startswith("0:"))
    assert deltas == "aa it is noon"
    args = "".join(json.loads(ln[2:])["argsTextDelta"] for ln in lines if ln.startswith("c:")
                   and json.loads(ln[2:])["toolCallId"] == "toolu_1")
    assert args == '{"tz": "UTC"}'
    begins = [json.loads(ln[2:]) for ln in lines if ln.startswith("b:")]
    assert [b["toolName"] for b in begins] == ["current_time", "cloud_answer"]
    badge = next(json.loads(json.loads(ln[2:])["result"]) for ln in lines if ln.startswith("a:")
                 and json.loads(ln[2:])["toolCallId"].startswith("cloud-"))
    assert badge == {"model": "Claude Sonnet 5.5", "input_tokens": 2500, "output_tokens": 50,
                     "cost_usd": round((2500 * 2 + 50 * 10) / 1e6, 4)}
    assert recorded and recorded[0]["input_tokens"] == 2500 and recorded[0]["model"] == "claude-sonnet-5-5"
    assert graph.config["configurable"]["answer_with"] == "cloud"


async def test_budget_note_streams_first(routing, monkeypatch):
    routing["spent"] = 99

    class LocalGraph:
        async def astream(self, inputs, config, stream_mode):
            self.config = config
            yield AIMessage("local answer"), {}

    graph = LocalGraph()
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "threadId": "t1", "tools": [], "mode": "cloud"}
    coro, recorded = run_route(graph, body, monkeypatch)
    text = await coro
    deltas = "".join(json.loads(ln[2:]) for ln in text.splitlines() if ln.startswith("0:"))
    assert deltas.startswith("_This month's cloud budget is used up") and deltas.endswith("local answer")
    assert graph.config["configurable"]["answer_with"] == "local" and not recorded


# --- replay ---------------------------------------------------------------

def test_replay_shows_cloud_badge_and_escalation(cloud_config):
    messages = [
        HumanMessage("big multiplication"),
        AIMessage("", tool_calls=[{"id": "e1", "name": "escalate", "args": {"reason": "exact"}}]),
        ToolMessage('{"proposed": true, "model": "Claude Sonnet 5.5"}', tool_call_id="e1"),
        HumanMessage("Answer my last question with Claude."),
        AIMessage("It is 130431346185.", usage_metadata={"input_tokens": 900, "output_tokens": 40,
                                                         "total_tokens": 940},
                  response_metadata={"model_provider": "anthropic", "model_name": "claude-sonnet-5-5"}),
    ]
    out = to_core_messages(messages)
    assert [m["role"] for m in out] == ["user", "assistant", "user", "assistant"]
    assert out[1]["content"][0]["toolName"] == "escalate"   # kept although it has no text
    badge = out[3]["content"][-1]
    assert badge["toolName"] == "cloud_answer"
    assert json.loads(badge["result"])["model"] == "Claude Sonnet 5.5"

