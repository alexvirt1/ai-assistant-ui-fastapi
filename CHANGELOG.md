# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.1.0] - 2026-07-28

### Added

- **MCP server support** (`backend/app/tools/mcp/loader.py`): servers declared
  in `mcp_servers.yaml` (see `backend/mcp_servers.example.yaml`) are connected
  at startup via `langchain-mcp-adapters`, and every tool they expose is
  registered in the ordinary tool registry — `ENABLED_TOOLS` filtering and
  system-prompt composition apply unchanged. Supports `stdio`,
  `streamable_http`, and `sse` transports with `${ENV_VAR}` interpolation; a
  server with missing env vars or an unreachable endpoint is logged and
  skipped without blocking startup. Tool discovery is awaited in the FastAPI
  lifespan (`connect_mcp_servers()`) before the graph is built; sessions are
  stateless per tool call, so no connection lifecycle is managed. Added
  `langchain-mcp-adapters` (0.1.x, langchain-core 0.3-compatible) as a backend
  dependency.
- **Generic running indicator for unknown tools**: tools without a dedicated
  icon (MCP server tools, declarative REST tools) now show a 🔧 pill with
  "Running <tool name>…" while executing, via the Thread's `ToolFallback`
  component slot. System-prompt hints contributed by multiple tools of the
  same MCP server are deduplicated.

- **Live tool-execution indicators in the chat UI**
  (`frontend/components/tools/ToolExecutionIndicators.tsx`): while a tool call
  is running, a small pill with an icon and a description of the current
  action is shown in the assistant message — 🕐 for `current_time`, 🌐 for
  `web_search` / `fetch_page` (including the search query or URL when
  available), 🧠 for the external AI delegates `ask_openai` / `ask_claude`.
  The indicator disappears as soon as the tool completes. Implemented with
  `makeAssistantToolUI`, driven by the tool-call status streamed from the
  backend.

- **"New chat" button** (`frontend/components/NewChatButton.tsx`): clears the
  `assistant_thread_id` cookie and calls `switchToNewThread()`, so both the
  displayed messages and the server-side LangGraph thread start fresh. Without
  a way to rotate the thread, the cookie's one-year `Max-Age` meant every
  conversation accumulated into a single thread indefinitely — one local thread
  had reached 159 checkpoints and ~100 KB of history against an 8192-token
  context window, and stale `current_time` results from weeks earlier were
  being echoed back as the current time. Disabled while a response is
  streaming. The cookie name now lives in `frontend/lib/thread.ts` and is
  imported by both the button and the chat proxy route so it cannot drift.

### Changed

- `LICENSE`: added `Copyright (c) 2026 Alexander Muratov` for this fork's
  contributions. The project remains MIT-licensed and the upstream notice for
  Simon Farshid and Hoang M. Le is retained, as MIT requires.
- `backend/pyproject.toml`: `authors` now lists Alexander Muratov alongside the
  original author.
- `frontend/components/MyAssistant.tsx` now wraps the chat in
  `AssistantRuntimeProvider` (required to register tool UIs) instead of
  passing the runtime directly to `Thread`.
- Version bumped to 1.1.0 (backend `pyproject.toml`, frontend
  `package.json`).
- `frontend/next.config.mjs` honours `NEXT_DIST_DIR`, so a verification build
  can write somewhere other than the `.next` a running `next start` is serving
  from. Defaults to `.next`, so normal builds are unaffected.

### Fixed

- **Tool-call parts were never streamed to the frontend with Ollama models**
  (`backend/app/add_langgraph_route.py`): the stream loop skipped any
  `tool_call_chunk` whose `index` was `None`. `langchain-ollama` builds
  `AIMessageChunk(tool_calls=...)` and langchain-core derives the chunk with
  `index: None`, so every Ollama tool call was discarded — the tool still ran
  server-side and the answer was correct, but the client received a text-only
  stream and no tool UI (neither the per-tool indicators nor the `🔧`
  fallback) could render. Accumulation is now keyed by tool call id when the
  index is absent, preserving the index-keyed path for providers such as
  OpenAI that stream tool calls incrementally.
- `backend/README.md`, declared as `readme` in `backend/pyproject.toml` but
  never present, is now committed — `poetry install` and `poetry check`
  previously failed with "Declared README file does not exist".
- **Blank gap between a tool finishing and the answer streaming**
  (`frontend/components/tools/ToolExecutionIndicators.tsx`): once a tool call
  completed, its indicator unmounted and nothing replaced it — and because the
  message already had content parts, the Thread's own empty-message loading
  indicator no longer applied either, so the UI sat still for a second or more
  until the first token arrived. A completed tool call now renders a pulsing
  dot for as long as it remains the message's last part and the message is
  still running.

## [1.0.1] - 2026-07-27

### Added

- **Extensible tool registry** (`backend/app/tools/`): tools are one module
  each and self-register via `register(ToolSpec(...))` on import;
  `tools/__init__.py` auto-discovers every module in the package. `ToolSpec`
  carries the LangChain tool plus a `prompt_hint`, `required_env`, and an
  optional `available()` check.
- **Self-disabling tools**: a tool whose required env vars are missing (or
  whose availability check fails) is skipped at startup instead of breaking
  the app; `ENABLED_TOOLS` provides an explicit allowlist.
- **Web scraper tool** `fetch_page` (`tools/web/scraper.py`): async httpx
  fetch with stdlib HTML-to-text extraction, SSRF guard (private/loopback
  addresses refused), content-type/size/time limits, and output truncation.
- **Web search tool** `web_search` (`tools/web/search.py`): a single tool over
  a provider abstraction — SerpAPI (Google) and Brave supported, selected via
  `SEARCH_PROVIDER` or auto-detected from available API keys.
- **External AI model delegate tools** (`tools/llm/delegate.py`): `ask_openai`
  (via `langchain-openai`, default `gpt-4o-mini`) and `ask_claude` (via the
  official `anthropic` SDK, default `claude-opus-5`, with server-side refusal
  fallback enabled and `stop_reason` handling).
- **Declarative REST tool factory** (`tools/rest/generic.py`): entries in
  `rest_tools.yaml` (see `backend/rest_tools.example.yaml`) become
  `StructuredTool`s with generated Pydantic arg schemas, `{arg}` and
  `${ENV_VAR}` templating, and automatic self-disabling when a referenced env
  var is unset. New REST integrations require YAML only, no Python.
- `CHANGELOG.md` (this file).

### Changed

- `backend/app/langgraph/agent.py` now only assembles the model and graph:
  tools come from the registry and the system prompt is composed from the base
  prompt plus each enabled tool's `prompt_hint` (per-tool instructions are no
  longer hardcoded in the prompt string).
- `current_time` moved from `agent.py` into `tools/builtin/time.py`.
- README rewritten to document the tool architecture and its configuration
  env vars.
- Version bumped to 1.0.1 (backend `pyproject.toml`, frontend `package.json`).
- Added `anthropic` as a backend dependency.

### Removed

- Dead module `backend/app/langgraph/tools.py` (mock `get_stock_price` that
  was never imported).
- Module-level `assistant_ui_graph = build_graph()` side effect in `agent.py`
  (unused; the server builds the graph in its lifespan hook).

## [0.1.0]

### Added

- Initial project: LangGraph ReAct agent (Ollama/qwen3) on FastAPI with
  assistant-stream, and a Next.js + assistant-ui frontend.
- Frontend/backend streaming wiring (`/api/chat` route, tool-call streaming,
  text dedup).
- Thread ID propagation from the frontend and per-thread conversation state
  persisted in Postgres via `AsyncPostgresSaver` (enabled when `DATABASE_URL`
  is set).
- Built-in `current_time` tool.
