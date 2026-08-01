"""Invariants of the prompt callable that trims conversation history.

The bug this guards against: when history outgrew the context window, Ollama
truncated it from the front, dropping the system prompt and its tool guidance
while recent stale answers survived — so the model answered from memory instead
of calling tools.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from app.langgraph import agent
from app.langgraph.agent import HISTORY_MAX_TOKENS, make_prompt

SYSTEM = "SYSTEM: call web_search for current events."


def build_history(turns: int = 40) -> list:
    """A conversation long enough to blow the budget, with tool exchanges."""
    messages: list = []
    for i in range(turns):
        messages.append(HumanMessage(f"question {i}: " + "lorem ipsum dolor " * 25))
        if i % 3 == 0:
            messages.append(
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "web_search",
                            "args": {"query": f"q{i}"},
                            "id": f"call{i}",
                            "type": "tool_call",
                        }
                    ],
                )
            )
            messages.append(ToolMessage(content=f"result {i} " + "data " * 40,
                                        tool_call_id=f"call{i}"))
        messages.append(AIMessage(f"answer {i}: " + "some prose " * 25))
    return messages


def test_history_is_actually_trimmed():
    history = build_history()
    assert count_tokens_approximately(history) > HISTORY_MAX_TOKENS
    out = make_prompt(SYSTEM)({"messages": history})
    assert len(out) < len(history)
    assert count_tokens_approximately(out[1:]) <= HISTORY_MAX_TOKENS


def test_system_prompt_survives_and_leads():
    """The whole point: guidance must never be the thing that gets dropped."""
    out = make_prompt(SYSTEM)({"messages": build_history()})
    assert isinstance(out[0], SystemMessage)
    assert "web_search" in out[0].content


def test_latest_message_is_always_kept():
    history = build_history()
    out = make_prompt(SYSTEM)({"messages": history})
    assert out[-1] is history[-1]


def assert_no_orphans(messages: list) -> None:
    open_calls: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                open_calls.add(call["id"])
        if isinstance(message, ToolMessage):
            assert message.tool_call_id in open_calls, "orphaned tool result"


@pytest.mark.parametrize("budget", range(200, 3001, 137))
def test_no_orphaned_tool_results_at_any_budget(monkeypatch, budget):
    """A ToolMessage whose calling AIMessage was trimmed away is rejected by the
    provider, so the window must never open on one.

    Swept across budgets deliberately: a single fixed budget can land on a safe
    boundary by luck and pass even when the guard that prevents this is gone.
    """
    monkeypatch.setattr(agent, "HISTORY_MAX_TOKENS", budget)
    out = make_prompt(SYSTEM)({"messages": build_history()})
    assert_no_orphans(out)


@pytest.mark.parametrize("budget", range(200, 3001, 137))
def test_window_starts_on_a_human_turn_at_any_budget(monkeypatch, budget):
    monkeypatch.setattr(agent, "HISTORY_MAX_TOKENS", budget)
    out = make_prompt(SYSTEM)({"messages": build_history()})
    assert isinstance(out[1], HumanMessage), "history must open on a human turn"


def test_single_turn_larger_than_budget_still_reaches_the_model():
    """Degenerate case: trimming to nothing would send a system prompt with no
    question attached."""
    huge = [HumanMessage("x " * 20000)]
    out = make_prompt(SYSTEM)({"messages": huge})
    assert len(out) == 2
    assert out[-1] is huge[-1]


def test_oversized_tool_result_does_not_orphan(monkeypatch):
    """Regression: the budget-exhausted fallback used to return just the final
    message. Mid-ReAct-loop that is a ToolMessage, so the provider received a
    tool result with no matching call. A tool returning more than the whole
    budget — the Yahoo history endpoint returns ~186 KB — reaches this.
    """
    monkeypatch.setattr(agent, "HISTORY_MAX_TOKENS", 50)
    history = [
        HumanMessage("q " * 200),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "t", "args": {}, "id": "c1", "type": "tool_call"}
            ],
        ),
        ToolMessage(content="result " * 200, tool_call_id="c1"),
    ]
    out = make_prompt(SYSTEM)({"messages": history})
    assert_no_orphans(out)
    assert out[-1] is history[-1], "the tool result must still reach the model"


def test_short_history_is_left_alone():
    history = [HumanMessage("hello"), AIMessage("hi")]
    out = make_prompt(SYSTEM)({"messages": history})
    assert out[1:] == history
