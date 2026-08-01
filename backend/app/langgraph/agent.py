import os

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    trim_messages,
)
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.prebuilt import create_react_agent

from ..models import DEFAULT_ROLE, make_chat_model
from ..tools import compose_prompt, get_enabled_specs

load_dotenv()


def make_model(role: str = DEFAULT_ROLE):
    """The chat model for a role.

    Construction moved to app/models/factory.py so planner and executor nodes
    can request other roles later. Behaviour for the default role is unchanged
    apart from an explicit keep_alive; OLLAMA_MODEL still overrides it.
    """
    return make_chat_model(role)


BASE_PROMPT = (
    "You are a private local assistant. "
    # The frontend renders math with remark-math, which recognises $...$ and
    # $$...$$ only. Left to itself the model often emits \\(...\\) or \\[...\\],
    # which would show up as literal backslashes.
    "Write any mathematics as LaTeX: $...$ for inline math and $$...$$ on its "
    "own lines for displayed equations. Do not use \\( \\) or \\[ \\]."
)

# Token budget for conversation history handed to the model, excluding the
# system prompt and the tool schemas. Keep it well under OLLAMA_NUM_CTX so
# there is room for both of those plus the response.
HISTORY_MAX_TOKENS = int(os.getenv("HISTORY_MAX_TOKENS", "3000"))


def _minimal_tail(messages: list) -> list:
    """Smallest suffix still valid to send when the budget trims to nothing.

    Taking just the final message is not safe: mid-ReAct-loop that message is
    often a ToolMessage, and a tool result whose calling AIMessage is absent is
    rejected by the provider. A tool returning a payload larger than the whole
    budget is enough to reach this.

    Prefers the last human turn onward. Exceeding the budget here is deliberate
    — an over-long prompt gets truncated by the provider, an invalid one errors.
    """
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i:]

    # No human turn in range: keep a trailing tool result with its call.
    last = messages[-1] if messages else None
    if isinstance(last, ToolMessage):
        for i in range(len(messages) - 2, -1, -1):
            message = messages[i]
            if isinstance(message, AIMessage) and any(
                call["id"] == last.tool_call_id for call in message.tool_calls or []
            ):
                return messages[i:]
    return messages[-1:]


def make_prompt(system_prompt: str):
    """Build the callable that assembles the model's input from graph state.

    The full conversation lives in the checkpointer and grows without bound,
    while the context window does not. Ollama truncates an oversized prompt
    from the front — exactly where the system prompt sits — so a long thread
    silently loses its tool instructions while stale assistant answers, being
    recent, survive. The model then repeats old answers instead of calling
    tools.

    Trimming here rather than in the route matters: the route only passes the
    newest human message, so by the time a request arrives the history has
    already been loaded from the checkpointer inside the graph. This runs on
    every model call, including each step of the ReAct loop.

    Trimming affects only what the model sees. Nothing is deleted from the
    checkpointer, so the stored transcript stays complete.
    """
    system = SystemMessage(system_prompt)

    def prompt(state) -> list:
        messages = state["messages"]
        trimmed = trim_messages(
            messages,
            max_tokens=HISTORY_MAX_TOKENS,
            token_counter=count_tokens_approximately,
            strategy="last",
            # Never begin on an orphaned ToolMessage: a tool result whose
            # calling AIMessage was trimmed away is rejected by the provider.
            start_on="human",
            # The system prompt is prepended explicitly, so keep it out of
            # both the window and the budget.
            include_system=False,
            allow_partial=False,
        )
        # A single turn larger than the budget can trim to nothing; sending
        # only a system prompt would lose the user's question entirely.
        if not trimmed:
            trimmed = _minimal_tail(messages)
        return [system] + list(trimmed)

    return prompt


def build_graph(checkpointer=None):
    specs = get_enabled_specs()
    return create_react_agent(
        model=make_model(),
        tools=[spec.tool for spec in specs],
        prompt=make_prompt(compose_prompt(BASE_PROMPT, specs)),
        checkpointer=checkpointer,
    )
