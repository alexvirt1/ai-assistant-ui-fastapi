import os

from dotenv import load_dotenv
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


def build_graph(checkpointer=None):
    specs = get_enabled_specs()
    return create_react_agent(
        model=make_model(),
        tools=[spec.tool for spec in specs],
        prompt=compose_prompt(BASE_PROMPT, specs),
        checkpointer=checkpointer,
    )
