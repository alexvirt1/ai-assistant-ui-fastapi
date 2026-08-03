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

## Tests

```bash
poetry run pytest
```

The suite is **offline by design** — no Ollama VM, no Postgres, no outbound
HTTP — so it runs in about a second and needs nothing configured. It covers the
pure logic where the subtle bugs have actually been: model role resolution and
fallback, the history-trimming window, tool registration and self-disabling, and
REST tool construction with `{arg}` / `${ENV_VAR}` interpolation.

Anything needing a live model, the VM, or a real HTTP endpoint stays a manual
check (`python -m app.models`, or a second backend on port 8001) rather than
being mocked into the suite.

Two conventions worth keeping. `conftest.py` clears `OLLAMA_MODEL`,
`ENABLED_TOOLS`, `MODELS_CONFIG` and `REST_TOOLS_CONFIG` for every test, so a
developer's `.env` cannot change the result. And the trimming tests sweep a
range of token budgets rather than asserting at one: a single budget can land on
a safe boundary by luck and pass even when the guard being tested is gone —
which is exactly what happened while writing them.

## Configuration

Copy `.env.example` to `.env`. The model and server variables:

| Variable | Effect |
| --- | --- |
| `OLLAMA_MODEL` | Model name (default `qwen3:8b`). |
| `OLLAMA_BASE_URL` | Ollama endpoint. |
| `OLLAMA_NUM_CTX` | Context window passed to the model (default `32768`). |
| `HISTORY_MAX_TOKENS` | Token budget for conversation history (defaults to a third of `OLLAMA_NUM_CTX`, so the two scale together). |
| `OLLAMA_KEEP_ALIVE` | How long the VM holds a model in memory (default `30m`). |
| `MODELS_CONFIG` | Path to the model-roles YAML (default `backend/models.yaml`). |
| `DATABASE_URL` | When set, enables per-thread conversation persistence. |

Tool-related variables (`ENABLED_TOOLS`, provider API keys, YAML config paths)
are documented in the [root README](../README.md).

### Model roles

Code asks for a *role* rather than an Ollama tag, so which model runs a given
piece of work is deployment configuration. Roles are declared in `models.yaml`
(copy `models.example.yaml`) and resolved by `app/models/`:

```python
from ..models import make_chat_model
model = make_chat_model("deep")     # planning / synthesis
```

`OLLAMA_MODEL` still wins for the default `fast` role, so existing `.env` files
behave exactly as before. A role whose tag the VM does not serve falls back to
`fast` rather than failing — the same degradation the tool registry uses for a
missing API key.

Inspect roles, VM inventory and what is currently loaded:

```bash
poetry run python -m app.models
```

**The VM serves one model at a time.** Loading a second evicts the first, and a
cold load costs roughly 6s for an 8B and 19s for a 14B against 0.3s once warm.
Group work by model rather than alternating; `OLLAMA_KEEP_ALIVE` (default `30m`,
against Ollama's own 5m) stops an idle conversation re-paying that load.

### Large documents

`app/documents/` is phase 1 of the map-reduce pipeline: everything needed to
accept a large file and say what processing it would cost, **without any model
calls**.

```bash
curl -F "file=@handbook.txt" http://127.0.0.1:8000/api/documents
```

```json
{"id": "…", "reused": false,
 "scope": {"tokens": 1310720, "chunks": 87, "estimated_minutes": 78.3,
           "tier": "consider_retrieval",
           "message": "87 chunks, about 78 minutes. If you want to ask targeted
                       questions rather than summarise the whole document,
                       retrieval answers in seconds instead."}}
```

A large document deliberately does **not** travel through the chat message:
inlined text is persisted into the LangGraph checkpoint and re-sent on every
later turn, so a 5 MB attachment would poison the thread permanently. It is
stored in `documents` / `document_chunks` and the conversation carries only a
reference.

- **Chunking** (`chunker.py`) is token-aware, prefers paragraph boundaries, and
  overlaps consecutive chunks so a fact spanning a boundary is not lost to both
  neighbours. No chunk may exceed the budget — that invariant is what the tests
  guard hardest.
- **Sizing** (`scope.py`) is arithmetic, so a 5 MB file is sized in
  milliseconds. Tiers: `single_pass` (fits one window — skip the pipeline
  entirely), `quick`, `confirm` (warn first), `consider_retrieval`. Throughput
  constants default to this deployment's measured figures (317 tok/s prompt,
  57 tok/s generation) and are env-overridable.
- **Storage** (`store.py`) deduplicates by SHA-256, so re-uploading the same
  file reuses its chunks — and, once phase 2 lands, the per-chunk summaries that
  cost 80 minutes to produce.

Requires `DATABASE_URL`; without it the tables are not created and uploads
return 503, matching how conversation persistence already degrades.

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
