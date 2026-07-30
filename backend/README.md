# Backend — LangGraph agent on FastAPI

A LangGraph ReAct agent served over FastAPI, streaming to an assistant-ui
frontend via [assistant-stream](https://github.com/assistant-ui/assistant-ui).
The model is a local Ollama model (`qwen3:8b` by default); tools come from an
auto-discovering registry under `app/tools/`.

For the project-level overview and the frontend, see the [root README](../README.md).

## Layout

```
backend/
├── app/
│   ├── server.py              # FastAPI app, lifespan, entrypoint (uvicorn :8000)
│   ├── add_langgraph_route.py # POST /api/chat — LangGraph <-> assistant-stream bridge
│   ├── langgraph/agent.py     # model + create_react_agent graph
│   └── tools/                 # tool registry (see root README for the full map)
├── .env.example               # copy to .env
├── mcp_servers.example.yaml   # copy to mcp_servers.yaml to add MCP servers
└── rest_tools.example.yaml    # copy to rest_tools.yaml to add REST tools
```

## Running

```bash
poetry install
poetry run python -m app.server      # serves 0.0.0.0:8000
```

Poetry is configured with `virtualenvs.in-project`, so dependencies land in
`backend/.venv`. For development with auto-reload, or to run a second instance
alongside one already on port 8000:

```bash
.venv/bin/uvicorn app.server:app --host 127.0.0.1 --port 8001 --reload
```

`load_dotenv()` is called without `override=True`, so a variable already
present in the real environment wins over the same key in `.env`. That matters
when the app runs under systemd with an `Environment=` or override file — the
unit's value takes precedence, and a manual shell run falls through to `.env`.

## Configuration

Copy `.env.example` to `.env`. The model and server variables:

| Variable | Effect |
| --- | --- |
| `OLLAMA_MODEL` | Model name (default `qwen3:8b`). |
| `OLLAMA_BASE_URL` | Ollama endpoint. |
| `OLLAMA_NUM_CTX` | Context window passed to the model (default `8192`). |
| `HISTORY_MAX_TOKENS` | Token budget for conversation history sent to the model (default `3000`). |
| `DATABASE_URL` | When set, enables per-thread conversation persistence. |

Tool-related variables (`ENABLED_TOOLS`, provider API keys, YAML config paths)
are documented in the [root README](../README.md).

### History trimming

The checkpointer keeps every message in a thread forever, but the context
window is fixed. Ollama truncates an oversized prompt **from the front**, which
is where the system prompt lives — so a long thread silently loses its tool
instructions while stale assistant answers, being recent, survive. The model
then repeats old answers instead of calling tools.

`agent.py` therefore passes `create_react_agent` a `prompt` **callable** rather
than a string. On every model call it prepends the composed system prompt and
appends a `trim_messages` window of recent history bounded by
`HISTORY_MAX_TOKENS`. The window uses `start_on="human"` so a `ToolMessage` can
never be sent without the `AIMessage` that called it, and falls back to the most
recent message if a single turn exceeds the whole budget.

This has to live in the agent, not in `add_langgraph_route`: the route passes
only the newest human message, and everything before it is loaded from the
checkpointer inside the graph.

Trimming changes only what the model sees. Nothing is deleted from Postgres, so
the stored transcript stays complete.

### Conversation state

If `DATABASE_URL` is set, the lifespan hook opens an `AsyncPostgresSaver`,
runs its `setup()` to create the checkpoint tables, and builds the graph with
it as the checkpointer. Conversation history is then keyed by the `threadId`
the frontend sends with each request. Without `DATABASE_URL` the graph runs
without a checkpointer and every request starts from an empty history.

## The `/api/chat` streaming contract

`add_langgraph_route` converts the frontend's message format into LangChain
messages, runs `graph.astream(..., stream_mode="messages")`, and translates
what comes back into an assistant-stream `DataStreamResponse`:

- **Text** is appended through a deduplicating helper. Some providers resend
  the full message rather than a delta, so each new payload is diffed against
  what has already been emitted before anything is written.
- **Tool calls** become stream parts via `controller.add_tool_call(name, id)`,
  with argument text appended as it arrives. This is what lets the frontend
  render live per-tool indicators while a call is in flight.
- **Tool results** are matched back to their call by `tool_call_id` and
  attached with `set_result`, which completes the part and dismisses the
  indicator.

### Tool-call accumulation keys

Providers disagree on how tool calls arrive, and the bridge handles both:

- **Incremental (OpenAI-style)** — a call is streamed across several chunks
  that share an integer `index`, with arguments accumulating over time. The
  `index` keys the accumulation.
- **Single-shot (Ollama)** — `langchain-ollama` builds
  `AIMessageChunk(tool_calls=...)`, and langchain-core derives the chunk with
  **`index: None`**. `agent.py` also sets `disable_streaming="tool_calling"`,
  so a tool-calling turn is not streamed at all and the call arrives complete
  in one message.

Because of this, accumulation falls back to the tool call id when `index` is
absent. Reinstating an `index is None` guard here silently drops every Ollama
tool call: the tool still executes and the answer is still correct, but the
client receives a text-only stream and no tool UI can render. This was the bug
fixed in 1.1.0.

Note that `langchain-ollama` mints a fresh `uuid4()` each time it parses a
response, so ids are not stable across repeated parses of the same call.

## Adding a tool

Add one module under `app/tools/`; `app/tools/__init__.py` walks the package
and imports everything, and each module registers itself on import:

```python
from langchain_core.tools import tool
from ..base import ToolSpec, register

@tool
def my_tool(query: str) -> str:
    """One-line description the model sees."""
    return do_something(query)

register(ToolSpec(
    tool=my_tool,
    prompt_hint="Use my_tool to ...",
    required_env=("MY_API_KEY",),
))
```

`required_env` makes the tool self-disabling: if a key is missing it is
skipped at startup rather than breaking the app. `prompt_hint` is folded into
the system prompt, deduplicated, for the enabled set only — so the prompt
always describes the tools actually available. No agent code changes.

Tools can also be added without Python at all, through `rest_tools.yaml`
(declarative REST) or `mcp_servers.yaml` (MCP servers); see the example files
and the root README.
