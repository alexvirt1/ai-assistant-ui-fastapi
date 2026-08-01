"""The ChatOllama built for a role.

Construction is offline — no request is made — so these run without the VM.

This file exists because the langgraph 1.x migration exposed a gap: nothing
constructed a model, so a setting that silently stopped applying would not have
failed any test.
"""

from app.models import make_chat_model


def test_role_selects_the_model_tag(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "chosen:7b")
    assert make_chat_model("fast").model == "chosen:7b"


def test_thinking_is_disabled():
    """qwen3 emits a reasoning preamble unless told not to, and it must not
    reach the chat window.

    Asserted on the field rather than on constructor arguments: langchain-ollama
    models are `extra="ignore"`, so a misspelled or removed setting is dropped
    without error. The old `model_kwargs={"think": False}` spelling passes
    construction on langchain-ollama 1.x and does nothing.
    """
    assert make_chat_model("fast").reasoning is False


def test_streaming_disabled_for_tool_calls():
    """Ollama returns a tool-calling turn complete rather than streamed; the
    route's tool-call bridge depends on this."""
    assert make_chat_model("fast").disable_streaming == "tool_calling"


def test_keep_alive_is_explicit():
    """Ollama's own default is 5m, which makes an idle conversation re-pay the
    cold model load on its next turn."""
    model = make_chat_model("fast")
    assert model.keep_alive
    assert model.keep_alive != "5m"


def test_context_window_follows_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_CTX", "4096")
    assert make_chat_model("fast").num_ctx == 4096


def test_deterministic_by_default():
    assert make_chat_model("fast").temperature == 0


def test_overrides_win():
    assert make_chat_model("fast", temperature=0.7).temperature == 0.7
