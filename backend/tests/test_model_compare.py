"""The model comparison harness (offline: both kinds of server mocked)."""

import json

import httpx

from evals.model_compare import run


def sse(*chunks) -> bytes:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


def test_openai_stream_text_tool_calls_and_usage():
    body = sse(
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {
            "name": "current_time", "arguments": '{"timez'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {
            "arguments": 'one": "Asia/Tokyo"}'}}]}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 9}},
    )
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    backend = run.OpenAICompatible("http://x/v1", "qwen3.8")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        reply = backend.chat(client, [{"role": "user", "content": "time?"}], tools=run.TOOLS)
    assert reply.tool_calls == [{"name": "current_time", "arguments": {"timezone": "Asia/Tokyo"}}]
    assert (reply.prompt_tokens, reply.output_tokens) == (120, 9)
    assert reply.first_token_seconds is not None
    assert seen[0]["stream"] and seen[0]["temperature"] == 0 and seen[0]["tools"]


def test_ollama_reply_with_exact_timings():
    def handler(request):
        body = json.loads(request.content)
        assert body["think"] is False and body["options"]["num_ctx"] == 8192
        return httpx.Response(200, json={
            "message": {"content": "ANSWER: 42", "tool_calls": []},
            "prompt_eval_count": 2000, "prompt_eval_duration": 1_000_000_000,
            "eval_count": 400, "eval_duration": 10_000_000_000, "done_reason": "stop"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        reply = run.Ollama("qwen3:8b", 8192, base="http://x").chat(client, [])
    assert reply.read_rate == 2000 and reply.write_rate == 40


def by_id(case_id):
    return next(c for c in run.TOOL_CASES if c.id == case_id)


def test_tool_checks():
    tokyo = by_id("time-tokyo")
    assert run.check_call(tokyo, [{"name": "current_time", "arguments": {"timezone": "Asia/Tokyo"}}]) == (True, True)
    assert run.check_call(tokyo, [{"name": "current_time", "arguments": {}}]) == (True, False)
    assert run.check_call(tokyo, [{"name": "web_search", "arguments": {"query": "x"}}]) == (False, False)
    none = by_id("no-tool-math")
    assert run.check_call(none, []) == (True, True)
    assert run.check_call(none, [{"name": "web_search", "arguments": {}}]) == (False, False)
    refs = by_id("image-refs")
    assert run.check_call(refs, [{"name": "generate_image",
                                  "arguments": {"prompt": "dog", "reference_ids": ["ref-a1"]}}]) == (True, True)


def test_russian_arguments_are_matched_as_text():
    case = by_id("search-ru")
    assert run.check_call(case, [{"name": "web_search",
                                  "arguments": {"query": "запуск Starship новости"}}]) == (True, True)


def test_summary_and_report():
    rows = [
        {"part": "speed", "test": "write", "write_tok_s": 40.0, "first_token_s": 0.2},
        {"part": "speed", "test": "write", "write_tok_s": 44.0, "first_token_s": 0.3},
        {"part": "speed", "test": "read-2000", "read_tok_s": 1500.0},
        {"part": "answers", "difficulty": "easy", "correct": True, "ran_away": False},
        {"part": "answers", "difficulty": "hard", "correct": False, "ran_away": True},
        {"part": "tools", "right_tool": True, "right_args": False},
    ]
    summary = run.summarise("qwen3:8b", rows)
    assert summary["write_tok_s"] == 42.0 and summary["read-2000_tok_s"] == 1500.0
    assert summary["answers_correct"] == "1/2" and summary["answers_ran_away"] == 1
    assert summary["tools_right"] == "1/1" and summary["tools_args_right"] == "0/1"
    table = run.report([summary, {"label": "other", "write_tok_s": 50.0}])
    assert "| write_tok_s | 42.0 | 50.0 |" in table and "| answers_correct | 1/2 | – |" in table


def test_long_prompt_size_is_about_right():
    words = len(run.long_prompt(6000)[0]["content"].split())
    assert 3500 < words < 6000     # ~0.75 words per token
