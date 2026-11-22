"""Measure how well local models know their own limits.

Each question is asked three ways, each in a fresh conversation except C:

  A. solve      answer it, or answer UNKNOWN if it cannot be known
  B. escalate   same, but with an `escalate` tool that hands the question to a
                stronger cloud model; we record whether the model used it
  C. confidence after A's answer, "how sure are you, 0-100?"

A says what the model can actually do. B and C say whether it knows that:
a model that judges itself well escalates exactly the questions it gets wrong
in A, and is more confident when it is right.

Usage (from backend/):
  .venv/bin/python -m evals.self_judgment.run --models qwen3:8b --limit 5
  .venv/bin/python -m evals.self_judgment.run --models qwen3:8b,qwen3.8-ud:27b-iq3_xxs

Runs on AI-1's GPU. Per the protocol agreed with the AI-1 agent, it refuses to
start while AI-1 holds a GPU reservation or ComfyUI runs someone else's job,
and anything beyond a short run is announced to the AI-1 agent first.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .cases import Case, build_cases

OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://192.168.87.160:11434").rstrip("/")
COMFYUI = os.getenv("COMFYUI_BASE_URL", "http://192.168.87.160:8188").rstrip("/")
CHAT_MODEL = "qwen3:8b"  # the chatbot's model; left loaded after a run
RESULTS = Path(__file__).parent / "results"

SOLVE_PROMPT = (
    "Answer the user's question. You may reason briefly first. End your reply with one line "
    "of the form 'ANSWER: <answer>', giving only the answer (a number, a word or a short "
    "phrase). If the answer cannot be known from your own knowledge, reply 'ANSWER: UNKNOWN'."
)

ESCALATE_PROMPT = (
    "You are a small local assistant. You can call the tool `escalate` to hand the user's "
    "question to a much more capable cloud model, which will answer it instead of you. Call "
    "it when you are not confident that you can answer correctly - for example when the "
    "question needs exact calculation, careful step-by-step reasoning, or information you "
    "cannot have. Otherwise answer yourself and end your reply with one line 'ANSWER: <answer>'."
)

ESCALATE_TOOL = {
    "type": "function",
    "function": {
        "name": "escalate",
        "description": "Hand the user's question to a more capable cloud model that answers instead.",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "Why you are escalating."}},
            "required": ["reason"],
        },
    },
}

CONFIDENCE_PROMPT = (
    "How confident are you that your answer is correct? Reply with a single integer from 0 to "
    "100 and nothing else."
)


# --- grading -----------------------------------------------------------------

def extract_answer(reply: str) -> str | None:
    matches = re.findall(r"ANSWER:\s*(.+)", reply or "", flags=re.IGNORECASE)
    if not matches:
        return None
    return matches[-1].strip().strip("*`").strip()


def _normalise_text(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _as_int(text: str) -> int | None:
    cleaned = re.sub(r"[,_\s$€£]", "", text)
    match = re.search(r"-?\d+(?:\.0+)?", cleaned)
    if not match:
        return None
    return int(float(match.group()))


# An honest refusal ("I don't have access to your inbox") is the right answer
# to an unknowable question, even without the ANSWER: UNKNOWN line asked for.
_REFUSAL = re.compile(r"\bunknown\b|can ?not|can't|cannot|don't have access|do not have access|"
                      r"unable to|not able to|no access|not possible to know", re.IGNORECASE)


def is_unknown(answer: str | None) -> bool:
    return answer is not None and bool(_REFUSAL.search(answer))


def grade(case: Case, answer: str | None) -> bool:
    """Whether an extracted answer is right. No answer is wrong."""
    if answer is None:
        return False
    if case.kind == "unknown":
        return is_unknown(answer)
    if is_unknown(answer):
        return False
    if case.kind == "int":
        return _as_int(answer) == int(case.answer)
    wanted = {_normalise_text(case.answer), *(_normalise_text(a) for a in case.aliases)}
    got = _normalise_text(answer)
    return got in wanted or any(got.startswith(w + " ") or got.endswith(" " + w) for w in wanted)


def parse_confidence(reply: str) -> int | None:
    match = re.search(r"\d{1,3}", reply or "")
    return min(int(match.group()), 100) if match else None


# --- talking to Ollama -------------------------------------------------------

# Sampling options for every call, set from the command line in main().
OPTIONS: dict = {}


def chat(client: httpx.Client, model: str, messages: list[dict], *, tools=None,
         think: bool, num_ctx: int, max_tokens: int | None = None) -> dict:
    """One /api/chat call.

    The output is capped (num_predict): at temperature 0 the big models were
    seen looping until the context filled, one call taking 7 minutes. Hitting
    the cap is recorded as running away, a result in its own right.
    """
    options = {"temperature": 0, "num_ctx": num_ctx, **OPTIONS}
    if max_tokens is not None:
        options["num_predict"] = max_tokens
    body = {
        "model": model, "messages": messages, "stream": False, "think": think,
        "keep_alive": "30m", "options": options,
    }
    if tools:
        body["tools"] = tools
    started = time.monotonic()
    resp = client.post(f"{OLLAMA}/api/chat", json=body)
    resp.raise_for_status()
    data = resp.json()
    data["_seconds"] = time.monotonic() - started
    data["_ran_away"] = data.get("done_reason") == "length"
    return data


def gpu_is_ours(client: httpx.Client) -> str | None:
    """None if we may use the GPU, else why not (agreed protocol with AI-1)."""
    try:
        res = client.get(f"{COMFYUI}/api/userdata/gpu-reservation.json")
        if res.status_code != 404:
            return f"AI-1 holds a GPU reservation: {res.text[:200]}"
        queue = client.get(f"{COMFYUI}/queue").json()
        if queue.get("queue_running") or queue.get("queue_pending"):
            return "ComfyUI on AI-1 is running or has queued jobs"
    except (httpx.HTTPError, ValueError) as exc:
        return f"could not check AI-1's GPU state ({exc})"
    return None


def placement(client: httpx.Client, model: str) -> str:
    for m in client.get(f"{OLLAMA}/api/ps").json().get("models", []):
        if m["name"] == model:
            return f"{100 * m.get('size_vram', 0) // max(m.get('size', 1), 1)}% GPU of {m['size'] / 1e9:.1f} GB"
    return "not loaded"


# --- one model ---------------------------------------------------------------

def run_case(client, model, case: Case, *, think: bool, num_ctx: int, max_tokens: int) -> dict:
    question = {"role": "user", "content": case.question}

    a = chat(client, model, [{"role": "system", "content": SOLVE_PROMPT}, question],
             think=think, num_ctx=num_ctx, max_tokens=max_tokens)
    a_reply = a["message"].get("content", "")
    a_answer = extract_answer(a_reply)

    c = chat(client, model, [{"role": "system", "content": SOLVE_PROMPT}, question,
                             {"role": "assistant", "content": a_reply},
                             {"role": "user", "content": CONFIDENCE_PROMPT}],
             think=think, num_ctx=num_ctx, max_tokens=max_tokens)
    confidence = parse_confidence(c["message"].get("content", ""))

    b = chat(client, model, [{"role": "system", "content": ESCALATE_PROMPT}, question],
             tools=[ESCALATE_TOOL], think=think, num_ctx=num_ctx, max_tokens=max_tokens)
    calls = b["message"].get("tool_calls") or []
    escalated = any(call.get("function", {}).get("name") == "escalate" for call in calls)
    b_content = b["message"].get("content", "")
    # Without a tagged answer, keep the reply's last line: it may be a refusal.
    b_answer = None if escalated else (extract_answer(b_content)
                                       or (b_content.strip().splitlines() or [""])[-1][:300] or None)

    return {
        "id": case.id, "category": case.category, "difficulty": case.difficulty,
        "expected": case.answer, "expect_escalate": case.expect_escalate,
        "a_answer": a_answer, "a_correct": grade(case, a_answer),
        "a_unknown": is_unknown(a_answer), "confidence": confidence,
        "b_escalated": escalated,
        "b_reason": next((call["function"].get("arguments", {}).get("reason")
                          for call in calls if call.get("function", {}).get("name") == "escalate"), None),
        "b_answer": b_answer, "b_correct": None if escalated else grade(case, b_answer),
        "a_ran_away": a["_ran_away"], "b_ran_away": b["_ran_away"],
        "seconds": round(a["_seconds"] + b["_seconds"] + c["_seconds"], 1),
        "tokens_out": a.get("eval_count", 0) + b.get("eval_count", 0) + c.get("eval_count", 0),
    }


def run_model(model: str, cases: list[Case], *, think: bool, num_ctx: int, max_tokens: int,
              out: Path) -> list[dict]:
    rows = []
    with httpx.Client(timeout=600) as client:
        why_not = gpu_is_ours(client)
        if why_not:
            sys.exit(f"Not starting {model}: {why_not}")
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            for i, case in enumerate(cases, 1):
                row = run_case(client, model, case, think=think, num_ctx=num_ctx, max_tokens=max_tokens)
                rows.append(row)
                f.write(json.dumps(row) + "\n")
                f.flush()
                mark = "✓" if row["a_correct"] else "✗"
                esc = "ESC" if row["b_escalated"] else "   "
                if row["a_ran_away"] or row["b_ran_away"]:
                    esc += " RAN-AWAY"
                print(f"  [{i:2}/{len(cases)}] {case.id:16} A{mark} {esc} conf={row['confidence']} "
                      f"{row['seconds']:5.1f}s", flush=True)
                if i == 1:
                    print(f"  placement: {placement(client, model)}", flush=True)
                # Between questions, give way to AI-1 if it reserves the GPU.
                if i % 10 == 0 and (why_not := gpu_is_ours(client)):
                    print(f"  stopping early: {why_not}")
                    break
        if model != CHAT_MODEL:
            client.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": 0})
    return rows


# --- report ------------------------------------------------------------------

def auroc(scores: list[tuple[float, bool]]) -> float | None:
    pos = [s for s, ok in scores if ok]
    neg = [s for s, ok in scores if not ok]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def summarise(model: str, rows: list[dict]) -> dict:
    n = len(rows)
    # Escalating is right when the model could not answer correctly on its own.
    should = [r for r in rows if not r["a_correct"] or r["expect_escalate"]]
    shouldnt = [r for r in rows if r["a_correct"] and not r["expect_escalate"]]
    escalated = [r for r in rows if r["b_escalated"]]
    answered = [r for r in rows if not r["b_escalated"]]
    conf = [(r["confidence"], r["a_correct"]) for r in rows if r["confidence"] is not None]
    pct = lambda a, b: round(100 * a / b) if b else None  # noqa: E731
    return {
        "model": model, "questions": n,
        "accuracy": pct(sum(r["a_correct"] for r in rows), n),
        "accuracy_easy": pct(sum(r["a_correct"] for r in rows if r["difficulty"] == "easy"),
                             sum(r["difficulty"] == "easy" for r in rows)),
        "accuracy_hard": pct(sum(r["a_correct"] for r in rows if r["difficulty"] == "hard"),
                             sum(r["difficulty"] == "hard" for r in rows)),
        "escalation_rate": pct(len(escalated), n),
        # Of the questions it should have escalated, how many it did.
        "escalation_recall": pct(sum(r["b_escalated"] for r in should), len(should)),
        # Of its escalations, how many were needed.
        "escalation_precision": pct(sum(not r["a_correct"] or r["expect_escalate"] for r in escalated),
                                    len(escalated)),
        "wasted_escalations": sum(r["b_escalated"] for r in shouldnt),
        # The harmful case: answered itself, and wrongly.
        "confidently_wrong": sum(r["b_correct"] is False for r in answered),
        "ran_away": sum(r.get("a_ran_away", False) or r.get("b_ran_away", False) for r in rows),
        "said_unknown_correctly": sum(r["a_unknown"] for r in rows if r["expect_escalate"]),
        "unknowable": sum(r["expect_escalate"] for r in rows),
        "mean_conf_right": round(sum(c for c, ok in conf if ok) / max(sum(ok for _, ok in conf), 1)),
        "mean_conf_wrong": round(sum(c for c, ok in conf if not ok) / max(sum(not ok for _, ok in conf), 1)),
        "confidence_auroc": (round(a, 2) if (a := auroc(conf)) is not None else None),
        "seconds_per_question": round(sum(r["seconds"] for r in rows) / max(n, 1), 1),
    }


def report(summaries: list[dict], per_category: dict[str, list[dict]]) -> str:
    lines = ["# Self-judgment eval", "",
             f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. Higher is better unless noted.", "",
             "| Model | Accuracy (easy / hard) | Escalation rate | Recall | Precision | Wasted | "
             "Confidently wrong (lower) | Ran away (lower) | Said UNKNOWN when unknowable | Conf. right / wrong | Conf. AUROC | s/question |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s['model']} | {s['accuracy']}% ({s['accuracy_easy']}% / {s['accuracy_hard']}%) | "
            f"{s['escalation_rate']}% | {s['escalation_recall']}% | {s['escalation_precision']}% | "
            f"{s['wasted_escalations']} | {s['confidently_wrong']} of {s['questions']} | {s['ran_away']} | "
            f"{s['said_unknown_correctly']} of {s['unknowable']} | {s['mean_conf_right']} / "
            f"{s['mean_conf_wrong']} | {s['confidence_auroc']} | {s['seconds_per_question']} |")
    lines += ["", "**Recall**: of the questions the model got wrong (or could not know), the share it "
              "escalated. **Precision**: of its escalations, the share that were needed. **Confidently "
              "wrong**: answered itself, with the tool available, and was wrong. **Ran away**: questions where an answer hit the output cap (looping or endless reasoning); such answers count as wrong. **Conf. AUROC**: 0.5 is "
              "no better than chance at telling its right answers from its wrong ones, 1.0 is perfect.", ""]
    for model, rows in per_category.items():
        lines += [f"## {model} by category", "", "| Category | Questions | Accuracy | Escalated | Escalated when wrong |",
                  "|---|---|---|---|---|"]
        for cat in sorted({r["category"] for r in rows}):
            rs = [r for r in rows if r["category"] == cat]
            wrong = [r for r in rs if not r["a_correct"]]
            lines.append(f"| {cat} | {len(rs)} | {sum(r['a_correct'] for r in rs)}/{len(rs)} | "
                         f"{sum(r['b_escalated'] for r in rs)} | {sum(r['b_escalated'] for r in wrong)}/{len(wrong)} |")
        lines.append("")
    return "\n".join(lines)


def regrade(rows: list[dict]) -> list[dict]:
    """Grade stored answers again with the current grader (no model calls)."""
    cases = {c.id: c for c in build_cases()}
    out = []
    for r in rows:
        case = cases[r["id"]]
        r = {**r, "a_correct": grade(case, r["a_answer"]), "a_unknown": is_unknown(r["a_answer"])}
        if not r["b_escalated"]:
            r["b_correct"] = grade(case, r["b_answer"])
        out.append(r)
    return out


def report_from_files(paths: list[str]) -> str:
    """Report on saved runs, regraded. Label a run with PATH=LABEL; by default
    the model name comes from the file name."""
    summaries, per_category = [], {}
    for item in paths:
        path, _, label = item.partition("=")
        name = label or Path(path).stem.split("-", 2)[-1]
        rows = regrade([json.loads(line) for line in Path(path).read_text().splitlines() if line])
        summaries.append(summarise(name, rows))
        per_category[name] = rows
    return report(summaries, per_category)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default=CHAT_MODEL, help="comma-separated Ollama tags")
    parser.add_argument("--limit", type=int, default=0, help="only the first N questions (0 = all)")
    parser.add_argument("--categories", default="", help="comma-separated categories to keep")
    parser.add_argument("--think", action="store_true", help="let the model think (the app does not)")
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--max-tokens", type=int, default=0,
                        help="output cap per call (default 1024, or 4096 with --think)")
    parser.add_argument("--repeat-penalty", type=float, default=None,
                        help="e.g. 1.1; off by default to test the models as the app runs them")
    parser.add_argument("--report", nargs="+", metavar="RESULTS.jsonl[=LABEL]",
                        help="no model calls: regrade saved results and print the report")
    args = parser.parse_args(argv)
    if args.report:
        print(report_from_files(args.report))
        return
    max_tokens = args.max_tokens or (4096 if args.think else 1024)
    if args.repeat_penalty is not None:
        OPTIONS["repeat_penalty"] = args.repeat_penalty

    cases = build_cases()
    if args.categories:
        keep = set(args.categories.split(","))
        cases = [c for c in cases if c.category in keep]
    if args.limit:
        # Spread a short run across categories rather than taking the first N.
        cases = cases[:: max(len(cases) // args.limit, 1)][: args.limit]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    summaries, per_category = [], {}
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        print(f"{model}: {len(cases)} questions, think={args.think}, num_ctx={args.num_ctx}, "
              f"max_tokens={max_tokens}, options={OPTIONS or 'defaults'}", flush=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", model)
        rows = run_model(model, cases, think=args.think, num_ctx=args.num_ctx,
                         max_tokens=max_tokens, out=RESULTS / f"{stamp}-{safe}.jsonl")
        summaries.append(summarise(model, rows))
        per_category[model] = rows
    text = report(summaries, per_category)
    (RESULTS / f"{stamp}-report.md").write_text(text)
    print("\n" + text)


if __name__ == "__main__":
    main()
