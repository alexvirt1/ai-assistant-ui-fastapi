"""Compare chat models for this app: speed, answers, tool calls.

Built to compare the app's qwen3:8b (Ollama) with Qwen3.8-Flash-Next served by
PolyStrata's OpenAI-compatible server, but any two backends work:

    ollama:<tag>                      Ollama's /api/chat, with num_ctx and exact timings
    openai:<base_url>#<model>         any /v1/chat/completions server (PolyStrata, llama.cpp)

Three parts, each optional (--parts):

- speed    writing speed on a ~400-token answer, time to the first token, and
           reading speed for prompts of about 2K and 6K tokens (and larger ones
           with --long, for a backend whose context allows it)
- answers  the self-judgment questions (evals/self_judgment), graded exactly;
           thinking off, as in the app
- tools    when to call which of the app's tools, and with what arguments,
           in English and Russian

Speed runs are repeated (--repeat) and the median kept. Results go to
evals/model_compare/results/<label>-<date>.jsonl plus a summary; --report
prints a Markdown table from result files.

The GPU on AI-1 is shared: run only with the AI-1 agent's OK and outside its
reservations. A full run of qwen3:8b takes about 5 minutes.
"""

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from evals.self_judgment.cases import build_cases
from evals.self_judgment.run import extract_answer, grade

RESULTS = Path(__file__).parent / "results"
OLLAMA = "http://192.168.87.160:11434"

ANSWER_SYSTEM = ("Answer the question. Be brief. End with one line 'ANSWER: <answer>'. "
                 "If the answer cannot be known, write 'ANSWER: UNKNOWN'.")
TOOL_SYSTEM = ("You are a helpful assistant in a chat app. Use a tool when it helps; "
               "otherwise answer directly.")

# --- backends -------------------------------------------------------------------


@dataclass
class Reply:
    text: str
    tool_calls: list[dict]
    prompt_tokens: int
    output_tokens: int
    seconds: float
    first_token_seconds: float | None = None
    prompt_seconds: float | None = None   # exact, where the server reports it
    output_seconds: float | None = None
    stopped_at_limit: bool = False

    @property
    def write_rate(self) -> float | None:
        seconds = self.output_seconds
        if seconds is None and self.first_token_seconds is not None:
            seconds = self.seconds - self.first_token_seconds
        return self.output_tokens / seconds if seconds and self.output_tokens > 1 else None

    @property
    def read_rate(self) -> float | None:
        seconds = self.prompt_seconds if self.prompt_seconds is not None else self.first_token_seconds
        return self.prompt_tokens / seconds if seconds else None


class Ollama:
    def __init__(self, tag: str, num_ctx: int, base: str = OLLAMA):
        self.tag, self.num_ctx, self.base = tag, num_ctx, base
        self.label = tag

    def chat(self, client, messages, *, tools=None, max_tokens=512) -> Reply:
        body = {"model": self.tag, "messages": messages, "stream": False, "think": False,
                "keep_alive": "30m",
                "options": {"temperature": 0, "num_ctx": self.num_ctx, "num_predict": max_tokens}}
        if tools:
            body["tools"] = tools
        started = time.monotonic()
        resp = client.post(f"{self.base}/api/chat", json=body)
        resp.raise_for_status()
        data = resp.json()
        message = data.get("message", {})
        calls = [{"name": c["function"]["name"], "arguments": c["function"].get("arguments") or {}}
                 for c in message.get("tool_calls") or []]
        ns = 1e9
        return Reply(text=message.get("content", ""), tool_calls=calls,
                     prompt_tokens=data.get("prompt_eval_count", 0),
                     output_tokens=data.get("eval_count", 0),
                     seconds=time.monotonic() - started,
                     prompt_seconds=(data.get("prompt_eval_duration") or 0) / ns or None,
                     output_seconds=(data.get("eval_duration") or 0) / ns or None,
                     stopped_at_limit=data.get("done_reason") == "length")


class OpenAICompatible:
    """Streams, to time the first token; reads usage from the last chunk."""

    def __init__(self, base: str, model: str):
        self.base, self.model = base.rstrip("/"), model
        self.label = model

    def chat(self, client, messages, *, tools=None, max_tokens=512) -> Reply:
        body = {"model": self.model, "messages": messages, "stream": True,
                "temperature": 0, "max_tokens": max_tokens,
                "stream_options": {"include_usage": True},
                # Thinking off, as the app runs qwen3:8b; servers that don't know
                # the field ignore it.
                "chat_template_kwargs": {"enable_thinking": False}}
        if tools:
            body["tools"] = tools
        started = time.monotonic()
        first = None
        text, finish, usage = [], None, {}
        calls: dict[int, dict] = {}
        chunks = 0
        with client.stream("POST", f"{self.base}/chat/completions", json=body) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                data = json.loads(payload)
                usage = data.get("usage") or usage
                for choice in data.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content") or delta.get("tool_calls"):
                        first = first if first is not None else time.monotonic() - started
                        chunks += 1
                    if delta.get("content"):
                        text.append(delta["content"])
                    for call in delta.get("tool_calls") or []:
                        slot = calls.setdefault(call.get("index", 0), {"name": "", "arguments": ""})
                        fn = call.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
                    finish = choice.get("finish_reason") or finish
        parsed = []
        for call in calls.values():
            try:
                args = json.loads(call["arguments"] or "{}")
            except ValueError:
                args = {"_unparsed": call["arguments"]}
            parsed.append({"name": call["name"], "arguments": args})
        return Reply(text="".join(text), tool_calls=parsed,
                     prompt_tokens=usage.get("prompt_tokens", 0),
                     output_tokens=usage.get("completion_tokens", chunks),
                     seconds=time.monotonic() - started, first_token_seconds=first,
                     stopped_at_limit=finish == "length")


def check_placement(backend, client, allow_cpu: bool = False) -> float | None:
    """The share of an Ollama model on the GPU; stop if part is on the CPU.

    With allow_cpu (a model too big for the card, measured as it would run)
    the share is returned instead. Otherwise a partial load means something
    else holds the GPU and the numbers would be meaningless.

    Seen on the first baseline run: ComfyUI still held a model, qwen3:8b loaded
    3.3 of its 6.0 GB on the GPU, and every reply was several times slower.
    """
    if not isinstance(backend, Ollama):
        return None
    models = client.get(f"{backend.base}/api/ps").json().get("models") or []
    for model in models:
        if model["name"] != backend.tag:
            continue
        share = model.get("size_vram", 0) / max(model.get("size", 1), 1)
        if share < 1 and not allow_cpu:
            raise SystemExit(f"{backend.tag} is only {model['size_vram'] / 1e9:.1f} of "
                             f"{model['size'] / 1e9:.1f} GB on the GPU; free the GPU and rerun.")
        return share
    return None


def backend_from(spec: str, num_ctx: int):
    kind, _, rest = spec.partition(":")
    if kind == "ollama":
        return Ollama(rest, num_ctx)
    if kind == "openai":
        base, _, model = rest.partition("#")
        if not model:
            raise SystemExit("openai backends are openai:<base_url>#<model>")
        return OpenAICompatible(base, model)
    raise SystemExit(f"unknown backend {spec!r}: use ollama:<tag> or openai:<url>#<model>")


# --- speed ----------------------------------------------------------------------

FILLER = ("The harbour log for the week records the tides, the weather and every ship that "
          "came in, with its cargo, crew and the berth it was given. ")


def long_prompt(approx_tokens: int, run: int = 0) -> list[dict]:
    # About 30 tokens per filler sentence. The run number leads every line, so
    # no run can reuse another's cached prompt: the first baseline measured
    # 112,000 tok/s reading because runs 2 and 3 came from Ollama's cache.
    lines = [f"[{run}] {i}. {FILLER}" for i in range(max(1, approx_tokens // 32))]
    return [{"role": "user", "content": "\n".join(lines)
             + "\n\nIn one sentence: what does this log record?"}]


WRITE_PROMPT = [{"role": "user", "content":
                 "Write a story of about 300 words about a lighthouse keeper who finds a "
                 "message in a bottle. Plain prose, no title."}]


def run_speed(backend, client, repeat: int, sizes: list[int], allow_cpu: bool = False) -> list[dict]:
    rows = []
    backend.chat(client, [{"role": "user", "content": "Say OK."}], max_tokens=4)   # load it
    share = check_placement(backend, client, allow_cpu)
    if share is not None:
        rows.append({"part": "speed", "test": "placement", "gpu_share": share})
    for i in range(repeat):
        reply = backend.chat(client, WRITE_PROMPT, max_tokens=450)
        rows.append({"part": "speed", "test": "write", "run": i,
                     "write_tok_s": reply.write_rate, "first_token_s": reply.first_token_seconds,
                     "output_tokens": reply.output_tokens, "seconds": reply.seconds})
    for size in sizes:
        for i in range(repeat):
            reply = backend.chat(client, long_prompt(size, run=i + 1), max_tokens=60)
            rows.append({"part": "speed", "test": f"read-{size}", "run": i,
                         "prompt_tokens": reply.prompt_tokens, "read_tok_s": reply.read_rate,
                         "seconds": reply.seconds})
    return rows


# --- answers ----------------------------------------------------------------------


def run_answers(backend, client, limit: int) -> list[dict]:
    cases = build_cases()
    if limit:
        cases = cases[:limit]
    rows = []
    for case in cases:
        reply = backend.chat(client, [{"role": "system", "content": ANSWER_SYSTEM},
                                      {"role": "user", "content": case.question}], max_tokens=700)
        answer = extract_answer(reply.text)
        rows.append({"part": "answers", "id": case.id, "category": case.category,
                     "difficulty": case.difficulty, "answer": answer, "expected": case.answer,
                     "correct": grade(case, answer), "seconds": reply.seconds,
                     "ran_away": reply.stopped_at_limit})
    return rows


# --- tools ----------------------------------------------------------------------

def tool(name, description, properties, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(required)}}}


# Shaped like the app's tools (app/tools/builtin, the document search and web search).
TOOLS = [
    tool("current_time", "The current date and time, optionally in an IANA time zone.",
         {"timezone": {"type": "string", "description": "e.g. Europe/Berlin; empty for local"}}),
    tool("web_search", "Search the web for recent or specific information.",
         {"query": {"type": "string"}}, ["query"]),
    tool("search_document", "Search an attached document by id for passages answering a question.",
         {"document_id": {"type": "string"}, "query": {"type": "string"}},
         ["document_id", "query"]),
    tool("generate_image", "Generate an image from a detailed English description.",
         {"prompt": {"type": "string"},
          "size": {"type": "string", "enum": ["square", "portrait", "landscape", "wide"]},
          "reference_ids": {"type": "array", "items": {"type": "string"}}}, ["prompt"]),
]


@dataclass(frozen=True)
class ToolCase:
    id: str
    message: str
    expect: str | None                      # tool name, or None for no tool
    check: dict = field(default_factory=dict)   # argument -> substring it must contain
    system_extra: str = ""


DOC = "Documents attached to this conversation:\n- id=doc-7f3 name=\"kafka-ops.pdf\" sections=212"
REFS = ('Reference photos attached to this conversation:\n'
        '- id=ref-a1 name="dog.jpg" 900x1200\n- id=ref-b2 name="cat.png" 800x800')

TOOL_CASES = [
    ToolCase("time-tokyo", "What time is it in Tokyo right now?", "current_time",
             {"timezone": "Tokyo"}),
    ToolCase("time-ru", "Который сейчас час в Берлине?", "current_time", {"timezone": "Berlin"}),
    ToolCase("search-news", "Who won the most recent Formula 1 race?", "web_search"),
    ToolCase("search-ru", "Найди свежие новости о запуске Starship.", "web_search",
             {"query": "Starship"}),
    ToolCase("doc-question", "What does the document say about log retention?", "search_document",
             {"document_id": "doc-7f3", "query": "retention"}, DOC),
    ToolCase("doc-ru", "Что в документе сказано про партиции?", "search_document",
             {"document_id": "doc-7f3"}, DOC),
    ToolCase("image-plain", "Draw a lighthouse at dusk in watercolour.", "generate_image",
             {"prompt": "lighthouse"}),
    ToolCase("image-wide", "Make a wide panorama of snowy mountains at sunrise.", "generate_image",
             {"size": "wide"}),
    ToolCase("image-ru", "Нарисуй кота в космическом шлеме.", "generate_image", {"prompt": "cat"}),
    ToolCase("image-refs", "Make the dog from my photo an astronaut on the moon.", "generate_image",
             {"reference_ids": "ref-a1"}, REFS),
    ToolCase("no-tool-math", "What is 17 + 25?", None),
    ToolCase("no-tool-chat", "Thanks, that was helpful!", None),
    ToolCase("no-tool-ru", "Объясни в двух предложениях, что такое рекурсия.", None),
    ToolCase("no-tool-code", "Write a Python function that reverses a string.", None),
]


def check_call(case: ToolCase, calls: list[dict]) -> tuple[bool, bool]:
    """(right tool or rightly none, arguments as expected)."""
    if case.expect is None:
        return (not calls, not calls)
    match = next((c for c in calls if c["name"] == case.expect), None)
    if match is None:
        return (False, False)
    args = match["arguments"] if isinstance(match["arguments"], dict) else {}
    ok = all(want.lower() in json.dumps(args.get(key, ""), ensure_ascii=False).lower()
             for key, want in case.check.items())
    return (True, ok)


def run_tools(backend, client) -> list[dict]:
    rows = []
    for case in TOOL_CASES:
        system = TOOL_SYSTEM + ("\n\n" + case.system_extra if case.system_extra else "")
        reply = backend.chat(client, [{"role": "system", "content": system},
                                      {"role": "user", "content": case.message}],
                             tools=TOOLS, max_tokens=400)
        right_tool, right_args = check_call(case, reply.tool_calls)
        rows.append({"part": "tools", "id": case.id, "expect": case.expect,
                     "called": [c["name"] for c in reply.tool_calls],
                     "arguments": [c["arguments"] for c in reply.tool_calls],
                     "right_tool": right_tool, "right_args": right_args, "seconds": reply.seconds})
    return rows


# --- summary --------------------------------------------------------------------

def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def summarise(label: str, rows: list[dict]) -> dict:
    out = {"label": label}
    speed = [r for r in rows if r["part"] == "speed"]
    if speed:
        out["gpu_share_pct"] = median(100 * r["gpu_share"] for r in speed if r["test"] == "placement")
        out["write_tok_s"] = median(r["write_tok_s"] for r in speed if r["test"] == "write")
        out["first_token_s"] = median(r["first_token_s"] for r in speed if r["test"] == "write")
        for test in sorted({r["test"] for r in speed if r["test"].startswith("read-")},
                           key=lambda t: int(t.split("-")[1])):
            out[f"{test}_tok_s"] = median(r["read_tok_s"] for r in speed if r["test"] == test)
    answers = [r for r in rows if r["part"] == "answers"]
    if answers:
        out["answers_correct"] = f'{sum(r["correct"] for r in answers)}/{len(answers)}'
        out["answers_ran_away"] = sum(r["ran_away"] for r in answers)
        for level in ("easy", "hard"):
            part = [r for r in answers if r["difficulty"] == level]
            out[f"answers_{level}"] = f'{sum(r["correct"] for r in part)}/{len(part)}'
    tools = [r for r in rows if r["part"] == "tools"]
    if tools:
        out["tools_right"] = f'{sum(r["right_tool"] for r in tools)}/{len(tools)}'
        out["tools_args_right"] = f'{sum(r["right_args"] for r in tools)}/{len(tools)}'
    return out


def fmt(value) -> str:
    if value is None:
        return "–"
    return f"{value:.1f}" if isinstance(value, float) else str(value)


def report(summaries: list[dict]) -> str:
    keys = [k for k in summaries[0] if k != "label"]
    for summary in summaries[1:]:
        keys += [k for k in summary if k != "label" and k not in keys]
    lines = ["| Measure | " + " | ".join(s["label"] for s in summaries) + " |",
             "|---|" + "---:|" * len(summaries)]
    for key in keys:
        lines.append(f"| {key} | " + " | ".join(fmt(s.get(key)) for s in summaries) + " |")
    return "\n".join(lines)


def load(path: str) -> tuple[str, list[dict]]:
    path, _, label = path.partition("=")
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    return label or Path(path).stem, rows


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", action="append", default=[],
                        help="ollama:<tag> or openai:<base_url>#<model>; repeat to compare")
    parser.add_argument("--parts", default="speed,answers,tools")
    parser.add_argument("--num-ctx", type=int, default=8192, help="Ollama context (the app's)")
    parser.add_argument("--repeat", type=int, default=3, help="speed runs per test")
    parser.add_argument("--long", default="", help="extra prompt sizes, e.g. 16000,30000")
    parser.add_argument("--limit", type=int, default=0, help="only the first N questions")
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--allow-cpu", action="store_true",
                        help="measure an Ollama model even if part of it is on the CPU")
    parser.add_argument("--report", nargs="+", metavar="RESULTS.jsonl[=LABEL]")
    args = parser.parse_args(argv)

    if args.report:
        print(report([summarise(label, rows) for label, rows in map(load, args.report)]))
        return
    if not args.backend:
        parser.error("give at least one --backend (or --report)")

    parts = set(args.parts.split(","))
    sizes = [2000, 6000] + [int(s) for s in args.long.split(",") if s]
    RESULTS.mkdir(exist_ok=True)
    summaries = []
    with httpx.Client(timeout=args.timeout) as client:
        for spec in args.backend:
            backend = backend_from(spec, args.num_ctx)
            print(f"== {backend.label}", file=sys.stderr, flush=True)
            rows = []
            if "speed" in parts:
                rows += run_speed(backend, client, args.repeat, sizes, args.allow_cpu)
            if "answers" in parts:
                rows += run_answers(backend, client, args.limit)
            if "tools" in parts:
                rows += run_tools(backend, client)
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in backend.label)
            path = RESULTS / f"{safe}-{stamp}.jsonl"
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
            summaries.append(summarise(backend.label, rows))
            print(f"   {path}", file=sys.stderr)
    print(report(summaries))


if __name__ == "__main__":
    main()
