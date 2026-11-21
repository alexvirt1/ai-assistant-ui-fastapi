"""The self-judgment eval's question set and grader (offline; no model calls)."""

from evals.self_judgment.cases import build_cases
from evals.self_judgment.run import auroc, extract_answer, grade, parse_confidence, summarise


def by_id():
    return {c.id: c for c in build_cases()}


def test_cases_are_stable_and_unique():
    first, second = build_cases(), build_cases()
    assert first == second
    assert len({c.id for c in first}) == len(first) >= 50
    assert all(c.answer for c in first)


def test_computed_answers():
    cases = by_id()
    assert cases["word-hard-5"].answer == "401"
    assert cases["code-hard-5"].answer == "6"     # late-binding lambdas
    assert cases["code-hard-6"].answer == "3"     # mutable default argument
    assert cases["logic-hard-4"].answer == "knight"


def test_extract_answer_takes_the_last_line():
    assert extract_answer("Let me think. ANSWER: 4\nWait, no.\nANSWER: **5**") == "5"
    assert extract_answer("no tagged answer") is None


def test_grade_ints_text_and_unknown():
    cases = by_id()
    assert grade(cases["arith-easy-0"], "2,686")
    assert grade(cases["arith-easy-0"], "$2686.0")
    assert not grade(cases["arith-easy-0"], "2687")
    assert grade(cases["facts-easy-0"], "Canberra.")
    assert grade(cases["dates-hard-0"], "monday")
    assert not grade(cases["facts-easy-0"], "Sydney")
    assert grade(cases["unknowable-0"], "UNKNOWN")
    assert not grade(cases["unknowable-0"], "189.50")
    assert not grade(cases["arith-easy-0"], "UNKNOWN")
    assert not grade(cases["arith-easy-0"], None)


def test_parse_confidence():
    assert parse_confidence("85") == 85
    assert parse_confidence("I'd say 120%") == 100
    assert parse_confidence("very sure") is None


def test_auroc():
    assert auroc([(90, True), (10, False)]) == 1.0
    assert auroc([(50, True), (50, False)]) == 0.5
    assert auroc([(90, True)]) is None


def test_summarise_escalation_metrics():
    row = dict(category="x", difficulty="hard", expected="1", seconds=1.0, tokens_out=1,
               a_unknown=False, b_reason=None, b_answer=None, confidence=50)
    rows = [
        {**row, "id": "a", "expect_escalate": False, "a_correct": False, "b_escalated": True, "b_correct": None},
        {**row, "id": "b", "expect_escalate": False, "a_correct": False, "b_escalated": False, "b_correct": False},
        {**row, "id": "c", "expect_escalate": False, "a_correct": True, "b_escalated": True, "b_correct": None},
        {**row, "id": "d", "expect_escalate": False, "a_correct": True, "b_escalated": False, "b_correct": True},
    ]
    s = summarise("m", rows)
    assert s["escalation_recall"] == 50       # escalated 1 of the 2 it got wrong
    assert s["escalation_precision"] == 50    # 1 of its 2 escalations was needed
    assert s["wasted_escalations"] == 1
    assert s["confidently_wrong"] == 1
