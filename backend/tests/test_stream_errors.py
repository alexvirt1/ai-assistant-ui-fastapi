"""A failing run must say so rather than return an empty message.

Seen live: the embedding model hit cudaMalloc out of memory, the tool node
raised, the graph aborted mid-stream, and the UI showed an assistant bubble with
no text and no error - indistinguishable from the model choosing to say nothing.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk

from app.add_langgraph_route import add_langgraph_route


class FakeGraph:
    """Stands in for the compiled graph, streaming then optionally failing."""

    def __init__(self, chunks=(), error=None):
        self.chunks = chunks
        self.error = error

    async def astream(self, *args, **kwargs):
        for chunk in self.chunks:
            yield AIMessageChunk(content=chunk), {}
        if self.error is not None:
            raise self.error


def client_for(graph) -> TestClient:
    app = FastAPI()
    add_langgraph_route(app, graph, "/api/chat")
    return TestClient(app)


def post(client) -> str:
    response = client.post(
        "/api/chat",
        # A user message carries content parts, not a bare string.
        json={
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Кто такой Шиншин?"}]}
            ]
        },
    )
    assert response.status_code == 200
    return response.text


class TestFailureIsVisible:
    def test_a_run_that_dies_mid_stream_says_so(self):
        # REGRESSION: this produced a blank assistant bubble.
        body = post(client_for(FakeGraph(
            error=RuntimeError("llama runner process has terminated: cudaMalloc failed"),
        )))
        assert "went wrong" in body

    def test_the_reason_reaches_the_user(self):
        # "out of memory" is actionable; a blank message is not.
        body = post(client_for(FakeGraph(error=RuntimeError("cudaMalloc out of memory"))))
        assert "cudaMalloc out of memory" in body

    def test_text_already_streamed_is_kept(self):
        # A failure partway through must not discard what the model had said.
        body = post(client_for(FakeGraph(
            chunks=["Section 148 says "],
            error=RuntimeError("connection reset"),
        )))
        assert "148" in body
        assert "connection reset" in body

    def test_an_exception_with_no_message_still_reports_its_type(self):
        body = post(client_for(FakeGraph(error=ValueError())))
        assert "ValueError" in body

    def test_only_the_first_line_of_a_multiline_error_is_shown(self):
        # Ollama errors carry a second line of cgo noise that helps nobody.
        body = post(client_for(FakeGraph(
            error=RuntimeError("cudaMalloc failed\nsignal arrived during cgo execution"),
        )))
        assert "cudaMalloc failed" in body
        assert "cgo execution" not in body

    @pytest.mark.parametrize("error", [RuntimeError("boom"), TimeoutError(), KeyError("k")])
    def test_the_request_still_returns_200_rather_than_hanging(self, error):
        # The stream has already begun, so the status is long since sent; the
        # failure has to arrive as content or not at all.
        assert post(client_for(FakeGraph(error=error)))


class TestSuccessIsUnaffected:
    def test_a_healthy_run_streams_its_text(self):
        body = post(client_for(FakeGraph(chunks=["Shinshin was a guest."])))
        assert "Shinshin was a guest." in body

    def test_a_healthy_run_reports_no_error(self):
        body = post(client_for(FakeGraph(chunks=["an answer"])))
        assert "went wrong" not in body


class TestOneTurnPerChat:
    """A second turn in a chat whose reply is still running is refused."""

    def post_to(self, client, thread_id):
        return client.post("/api/chat", json={
            "threadId": thread_id,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        }).text

    def test_a_turn_while_one_runs_is_refused_without_running_the_model(self, monkeypatch):
        from app import add_langgraph_route as route

        class Untouchable(FakeGraph):
            async def astream(self, *args, **kwargs):
                raise AssertionError("the model must not run")
                yield  # pragma: no cover

        monkeypatch.setattr(route, "ACTIVE_TURNS", {"busy-chat"})
        text = self.post_to(client_for(Untouchable()), "busy-chat")
        assert "still being written" in text

    def test_the_chat_is_free_again_after_a_turn(self, monkeypatch):
        from app import add_langgraph_route as route

        monkeypatch.setattr(route, "ACTIVE_TURNS", set())
        client = client_for(FakeGraph(chunks=["Hello"]))
        assert "Hello" in self.post_to(client, "c1")
        assert route.ACTIVE_TURNS == set()
        assert "Hello" in self.post_to(client, "c1")

    def test_a_failed_turn_frees_the_chat(self, monkeypatch):
        from app import add_langgraph_route as route

        monkeypatch.setattr(route, "ACTIVE_TURNS", set())
        self.post_to(client_for(FakeGraph(error=RuntimeError("boom"))), "c2")
        assert route.ACTIVE_TURNS == set()


class TestHealBeforeALocalTurn:
    def test_a_local_turn_checks_the_chat_model_first(self, monkeypatch):
        from app.images import service as image_service

        order = []

        async def heal():
            order.append("heal")
            return False

        class Recording(FakeGraph):
            async def astream(self, *args, **kwargs):
                order.append("model")
                yield AIMessageChunk(content="Hi"), {}

        monkeypatch.setattr(image_service, "heal_chat_model", heal)
        assert "Hi" in post(client_for(Recording()))
        assert order == ["heal", "model"]


class TestDuringAI1Reservation:
    def plan(self, monkeypatch, plan):
        from app.images import service as image_service

        async def reserved_gpu_plan():
            return plan

        async def heal():
            return False

        monkeypatch.setattr(image_service, "reserved_gpu_plan", reserved_gpu_plan)
        monkeypatch.setattr(image_service, "heal_chat_model", heal)

    def test_busy_answers_with_the_note_only(self, monkeypatch):
        from app.images.service import ReservedPlan

        class Untouchable(FakeGraph):
            async def astream(self, *args, **kwargs):
                raise AssertionError("the model must not run")
                yield  # pragma: no cover

        self.plan(monkeypatch, ReservedPlan("busy", "_GPU reserved until 13:00._"))
        assert "GPU reserved until 13:00" in post(client_for(Untouchable()))

    def test_cpu_plan_reaches_the_model_selector(self, monkeypatch):
        from app.images.service import ReservedPlan

        seen = {}

        class Recording(FakeGraph):
            async def astream(self, graph_input, config=None, **kwargs):
                seen.update((config or {}).get("configurable", {}))
                yield AIMessageChunk(content="Hi"), {}

        self.plan(monkeypatch, ReservedPlan("cpu", "_On the CPU._\n\n", model="qwen3:8b",
                                            num_gpu=0, num_thread=6))
        text = post(client_for(Recording()))
        assert "On the CPU" in text and "Hi" in text
        assert (seen["local_model"], seen["local_num_gpu"], seen["local_num_thread"]) == ("qwen3:8b", 0, 6)
