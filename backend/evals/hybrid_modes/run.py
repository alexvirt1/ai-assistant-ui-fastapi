"""Compare hybrid modes on the same questions: local, consult, hand-over, auto.

Uses the 59 machine-graded questions of the self-judgment eval
(evals/self_judgment/cases.py), graded exactly, with no judge model.

  local     qwen3:8b answers alone
  consult   the cloud model answers; the local model gets that answer as a
            tool result and writes the reply (the cloud is consulted on every
            question, so the comparison measures the relay, not self-judgment)
  handover  the cloud model answers directly, as the app does in cloud mode
  auto      qwen3:8b with the escalate tool; whatever it escalates is handed
            over (every proposal approved), the rest it answers itself

Per mode: accuracy, cloud cost and tokens sent to the cloud, time per
question. For consult also how often the relayed reply differs from the cloud
model's own answer.

Spends money: every cloud call is billed to ANTHROPIC_API_KEY (Claude Sonnet
5.5, about $0.5 for the full set in all modes; --dry-run prints an estimate
and calls nothing). Uses AI-1's GPU for the local model; refuses to start while
AI-1 holds a GPU reservation (agreed protocol).

Usage (from backend/, with backend/.env loaded for the key):
  .venv/bin/python -m evals.hybrid_modes.run --dry-run
  .venv/bin/python -m evals.hybrid_modes.run --modes local,consult,handover,auto --limit 10
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from ..self_judgment.cases import Case, build_cases
from ..self_judgment.run import (
    CHAT_MODEL, COMFYUI, ESCALATE_PROMPT, ESCALATE_TOOL, OLLAMA, SOLVE_PROMPT,
    extract_answer, grade, gpu_is_ours,
)

RESULTS = Path(__file__).parent / "results"
MODES = ("local", "consult", "handover", "auto")

CONSULT_TOOL = {
    "type": "function",
    "function": {
        "name": "consult_cloud",
        "description": "Ask a more capable cloud model and get its answer back.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}},
                       "required": ["question"]},
    },
}

RELAY_PROMPT = (
    SOLVE_PROMPT + " You consulted a more capable cloud model; its answer is in the tool "
    "result. Use it to write your reply."
)


@dataclass
class Reply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0


# A model call: (system, question, extra messages) -> Reply. Injected so the
# harness can be tested without Ollama or the Anthropic API.
LocalCall = Callable[..., Awaitable[dict]]
CloudCall = Callable[[str, str], Awaitable[Reply]]


async def ollama_chat(messages: list[dict], tools=None, num_ctx: int = 24576,
                      max_tokens: int = 1024) -> dict:
    body = {"model": CHAT_MODEL, "messages": messages, "stream": False, "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": max_tokens}}
    if tools:
        body["tools"] = tools
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(f"{OLLAMA}/api/chat", json=body)
        resp.raise_for_status()
        data = resp.json()
    data["_seconds"] = time.monotonic() - started
    return data


async def anthropic_answer(system: str, question: str) -> Reply:
    """The configured cloud model (app/cloud) answering one question."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.chats.messages import content_to_text
    from app.cloud import models

    spec = models.default_model()
    if spec is None:
        raise SystemExit("No cloud model configured: set ANTHROPIC_API_KEY.")
    started = time.monotonic()
    reply = await models.make_chat_model(spec.name).ainvoke(
        [SystemMessage(system), HumanMessage(question)])
    usage = reply.usage_metadata or {}
    return Reply(content_to_text(reply.content), usage.get("input_tokens", 0),
                 usage.get("output_tokens", 0), time.monotonic() - started)


def cloud_price() -> tuple[float, float]:
    from app.cloud import models

    spec = models.default_model() or next(iter(models.load_models()), None)
    return (spec.price_input, spec.price_output) if spec else (0.0, 0.0)


async def run_case(case: Case, mode: str, local: LocalCall, cloud: CloudCall) -> dict:
    question = case.question
    row = {"id": case.id, "category": case.category, "difficulty": case.difficulty,
           "mode": mode, "expected": case.answer, "escalated": None, "relay_changed": None,
           "cloud_in": 0, "cloud_out": 0}
    started = time.monotonic()

    if mode == "local":
        data = await local([{"role": "system", "content": SOLVE_PROMPT},
                            {"role": "user", "content": question}])
        answer = extract_answer(data["message"].get("content", ""))

    elif mode == "handover":
        reply = await cloud(SOLVE_PROMPT, question)
        answer = extract_answer(reply.text)
        row.update(cloud_in=reply.input_tokens, cloud_out=reply.output_tokens)

    elif mode == "consult":
        reply = await cloud(SOLVE_PROMPT, question)
        cloud_answer = extract_answer(reply.text)
        data = await local([
            {"role": "system", "content": RELAY_PROMPT},
            {"role": "user", "content": question},
            {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "consult_cloud", "arguments": {"question": question}}}]},
            {"role": "tool", "content": reply.text},
        ], tools=[CONSULT_TOOL])
        answer = extract_answer(data["message"].get("content", ""))
        row.update(cloud_in=reply.input_tokens, cloud_out=reply.output_tokens,
                   cloud_answer=cloud_answer,
                   relay_changed=grade(case, cloud_answer) != grade(case, answer))

    elif mode == "auto":
        data = await local([{"role": "system", "content": ESCALATE_PROMPT},
                            {"role": "user", "content": question}], tools=[ESCALATE_TOOL])
        calls = data["message"].get("tool_calls") or []
        escalated = any(c.get("function", {}).get("name") == "escalate" for c in calls)
        row["escalated"] = escalated
        if escalated:
            reply = await cloud(SOLVE_PROMPT, question)
            answer = extract_answer(reply.text)
            row.update(cloud_in=reply.input_tokens, cloud_out=reply.output_tokens)
        else:
            answer = extract_answer(data["message"].get("content", ""))
    else:
        raise ValueError(mode)

    row.update(answer=answer, correct=grade(case, answer),
               seconds=round(time.monotonic() - started, 1))
    return row


def summarise(mode: str, rows: list[dict], price: tuple[float, float]) -> dict:
    n = len(rows) or 1
    cloud_in = sum(r["cloud_in"] for r in rows)
    cloud_out = sum(r["cloud_out"] for r in rows)
    cost = (cloud_in * price[0] + cloud_out * price[1]) / 1e6
    out = {
        "mode": mode, "questions": len(rows),
        "accuracy": round(100 * sum(r["correct"] for r in rows) / n),
        "accuracy_hard": round(100 * sum(r["correct"] for r in rows if r["difficulty"] == "hard")
                               / max(sum(r["difficulty"] == "hard" for r in rows), 1)),
        "cloud_calls": sum(1 for r in rows if r["cloud_in"] or r["cloud_out"]),
        "cloud_tokens": cloud_in + cloud_out,
        "cost_usd": round(cost, 4),
        "cost_per_100": round(100 * cost / n, 3),
        "seconds_per_question": round(sum(r["seconds"] for r in rows) / n, 1),
    }
    if mode == "consult":
        out["relay_changed"] = sum(1 for r in rows if r.get("relay_changed"))
    if mode == "auto":
        out["escalated"] = sum(1 for r in rows if r.get("escalated"))
    return out


def report(summaries: list[dict]) -> str:
    lines = [
        "# Hybrid modes compared", "",
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. Same machine-graded questions in every mode.", "",
        "| Mode | Accuracy (hard) | Cloud calls | Cloud tokens | Cost | Cost per 100 questions | s/question | Notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        notes = []
        if "relay_changed" in s:
            notes.append(f"relay changed the verdict on {s['relay_changed']}")
        if "escalated" in s:
            notes.append(f"escalated {s['escalated']} of {s['questions']}")
        lines.append(
            f"| {s['mode']} | {s['accuracy']}% ({s['accuracy_hard']}%) | {s['cloud_calls']} | "
            f"{s['cloud_tokens']} | ${s['cost_usd']:.4f} | ${s['cost_per_100']:.3f} | "
            f"{s['seconds_per_question']} | {'; '.join(notes)} |")
    lines += ["", "**Relay changed the verdict**: questions where the local model's relayed reply "
              "was graded differently from the cloud model's own answer (consult mode)."]
    return "\n".join(lines)


def estimate(cases: list[Case], modes: list[str]) -> str:
    """Rough cost before running: ~350 tokens in and ~250 out per cloud call."""
    price_in, price_out = cloud_price()
    per_call = (350 * price_in + 250 * price_out) / 1e6
    calls = sum({"local": 0, "consult": 1, "handover": 1, "auto": 0.3}[m] for m in modes) * len(cases)
    return (f"{len(cases)} questions x modes {', '.join(modes)}: about {calls:.0f} cloud calls, "
            f"roughly ${calls * per_call:.2f} at ${price_in}/{price_out} per million tokens in/out.")


async def main_async(args) -> None:
    # The API key lives in backend/.env, like the app's other settings.
    from dotenv import load_dotenv

    load_dotenv()
    cases = build_cases()
    if args.categories:
        keep = set(args.categories.split(","))
        cases = [c for c in cases if c.category in keep]
    if args.limit:
        cases = cases[:: max(len(cases) // args.limit, 1)][: args.limit]
    modes = [m for m in args.modes.split(",") if m]
    unknown = set(modes) - set(MODES)
    if unknown:
        raise SystemExit(f"unknown modes: {', '.join(unknown)}")

    print(estimate(cases, modes), flush=True)
    if args.dry_run:
        return
    if any(m != "local" for m in modes) and not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set; only --modes local can run.")
    with httpx.Client(timeout=10) as client:
        why_not = gpu_is_ours(client)
    if why_not:
        raise SystemExit(f"Not starting: {why_not}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    RESULTS.mkdir(parents=True, exist_ok=True)
    price = cloud_price()
    summaries = []
    for mode in modes:
        rows = []
        out = RESULTS / f"{stamp}-{mode}.jsonl"
        with out.open("w") as f:
            for i, case in enumerate(cases, 1):
                row = await run_case(case, mode, ollama_chat, anthropic_answer)
                rows.append(row)
                f.write(json.dumps(row) + "\n")
                print(f"  {mode:8} [{i:2}/{len(cases)}] {case.id:16} "
                      f"{'✓' if row['correct'] else '✗'} {row['seconds']:5.1f}s", flush=True)
        summaries.append(summarise(mode, rows, price))
    text = report(summaries)
    (RESULTS / f"{stamp}-report.md").write_text(text)
    print("\n" + text)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--categories", default="")
    parser.add_argument("--dry-run", action="store_true", help="print the cost estimate only")
    args = parser.parse_args(argv)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
