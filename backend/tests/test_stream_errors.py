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
