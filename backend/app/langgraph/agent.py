import os

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from ..tools import compose_prompt, get_enabled_specs

load_dotenv()

configured_model = os.getenv("OLLAMA_MODEL", "qwen3:8b")
ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://192.168.87.160:11434")


def make_model():
    return ChatOllama(
        model=os.getenv("OLLAMA_MODEL", configured_model),
        base_url=os.getenv("OLLAMA_BASE_URL", ollama_base_url),
        temperature=0,
        num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "8192")),
        disable_streaming="tool_calling",
        model_kwargs={"think": False},
    )


BASE_PROMPT = "You are a private local assistant."

# Token budget for conversation history handed to the model, excluding the
# system prompt and the tool schemas. Keep it well under OLLAMA_NUM_CTX so
# there is room for both of those plus the response.
HISTORY_MAX_TOKENS = int(os.getenv("HISTORY_MAX_TOKENS", "3000"))


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
            trimmed = messages[-1:]
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
