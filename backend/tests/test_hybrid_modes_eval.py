"""The hybrid-modes comparison harness, with fake models (no GPU, no API)."""

import pytest

from evals.hybrid_modes.run import Reply, estimate, report, run_case, summarise
from evals.self_judgment.cases import build_cases

CASE = next(c for c in build_cases() if c.id == "arith-hard-0")  # answer 130431346185


def local_returning(text="", tool_calls=None):
    calls = []

    async def local(messages, tools=None):
        calls.append({"messages": messages, "tools": tools})
        return {"message": {"content": text, "tool_calls": tool_calls or []}, "_seconds": 0.1}

    local.calls = calls
    return local


async def cloud_right(system, question):
    return Reply("Working... ANSWER: 130431346185", input_tokens=300, output_tokens=200)


async def test_local_mode_uses_no_cloud():
    row = await run_case(CASE, "local", local_returning("ANSWER: 1"), cloud_right)
    assert row["correct"] is False and row["cloud_in"] == 0


async def test_handover_is_the_cloud_answer():
    row = await run_case(CASE, "handover", local_returning(), cloud_right)
    assert row["correct"] and (row["cloud_in"], row["cloud_out"]) == (300, 200)


async def test_consult_measures_the_relay():
    garbling = local_returning("The cloud says about 130 billion. ANSWER: 130000000000")
    row = await run_case(CASE, "consult", garbling, cloud_right)
    assert row["cloud_answer"] == "130431346185"
    assert row["correct"] is False and row["relay_changed"] is True
    # The local model saw the cloud's reply as a tool result.
    sent = garbling.calls[0]["messages"]
    assert sent[-1]["role"] == "tool" and "130431346185" in sent[-1]["content"]


async def test_auto_hands_over_only_what_is_escalated():
    escalating = local_returning("", [{"function": {"name": "escalate", "arguments": {"reason": "x"}}}])
    row = await run_case(CASE, "auto", escalating, cloud_right)
    assert row["escalated"] and row["correct"] and row["cloud_in"] == 300
    answering = local_returning("ANSWER: 5")
    row = await run_case(CASE, "auto", answering, cloud_right)
    assert row["escalated"] is False and row["cloud_in"] == 0 and not row["correct"]


def test_summary_and_report():
    rows = [
        {"id": "a", "difficulty": "hard", "correct": True, "cloud_in": 300, "cloud_out": 200,
         "seconds": 2.0, "relay_changed": False},
        {"id": "b", "difficulty": "easy", "correct": False, "cloud_in": 300, "cloud_out": 200,
         "seconds": 4.0, "relay_changed": True},
    ]
    s = summarise("consult", rows, (2.0, 10.0))
    assert s["accuracy"] == 50 and s["cloud_calls"] == 2 and s["relay_changed"] == 1
    assert s["cost_usd"] == pytest.approx((600 * 2 + 400 * 10) / 1e6, abs=1e-4)
    assert "| consult | 50% (100%) | 2 |" in report([s])


def test_estimate_mentions_calls_and_cost():
    text = estimate(build_cases()[:10], ["local", "handover"])
    assert "10 questions" in text and "cloud calls" in text and "$" in text
