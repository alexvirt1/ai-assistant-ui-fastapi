import os
from datetime import datetime

from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

load_dotenv()

configured_model = os.getenv("OLLAMA_MODEL", "qwen3:8b")
ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://192.168.87.160:11434")


@tool
def current_time() -> str:
    """Return the current server time in ISO format."""
    print("### TOOL EXECUTED: current_time", flush=True)
    return datetime.now().isoformat(timespec="seconds")


tools = [current_time]


def make_model():
    return ChatOllama(
        model=os.getenv("OLLAMA_MODEL", configured_model),
        base_url=os.getenv("OLLAMA_BASE_URL", ollama_base_url),
        temperature=0,
        num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "8192")),
        disable_streaming="tool_calling",
        model_kwargs={"think": False},
    )


SYSTEM_PROMPT = (
    "You are a private local assistant. "
    "When the user asks for current time or server time, you must call current_time. "
    "Do not answer time questions from memory."
)


def build_graph(checkpointer=None):
    return create_react_agent(
        model=make_model(),
        tools=tools,
        prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )


assistant_ui_graph = build_graph()
