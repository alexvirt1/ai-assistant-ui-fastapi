# assistant-ui-langgraph-fastapi


A demonstration project that combines LangGraph, assistant-stream, and FastAPI to create an AI agent with a modern UI. The project uses [assistant-ui](https://www.assistant-ui.com/) and Next.js.

## Overview

This project showcases:

- A LangGraph agent running on a FastAPI
- Real-time response streaming to the frontend using assistant-stream
- A modern chat UI built with assistant-ui and Next.js
- An extensible tool registry: built-in, web, external-LLM, and declarative REST tools

## Tool Architecture

Tools live in `backend/app/tools/`, one module per tool (or tool family). Each
module registers itself with the central registry as an import side effect, and
the agent is assembled from whatever is registered and enabled — adding a tool
means adding one file, with no changes to the agent code.

```
backend/app/tools/
├── base.py            # ToolSpec + registry (register / get_enabled_specs / compose_prompt)
├── __init__.py        # auto-discovers and imports every tool module
├── builtin/time.py    # current_time
├── web/scraper.py     # fetch_page — fetch a URL and return readable text
├── web/search.py      # web_search — provider abstraction (SerpAPI/Google, Brave)
├── llm/delegate.py    # ask_openai / ask_claude — delegate to external AI models
├── rest/generic.py    # factory: YAML entries in rest_tools.yaml become tools
└── mcp/loader.py      # MCP servers from mcp_servers.yaml become tools
```

Key properties:

- **Self-disabling tools** — a tool declares the env vars it needs
  (`required_env`); if a key is missing the tool is silently skipped, so
  optional integrations never break startup.
- **Composed system prompt** — each tool contributes a one-line `prompt_hint`;
  the system prompt is built from the enabled set, so it always matches the
  tools actually available.
- **Declarative REST tools** — copy `backend/rest_tools.example.yaml` to
  `backend/rest_tools.yaml` and each entry becomes a tool (URL/query/body
  templating with `{args}` and `${ENV_VARS}`). No Python needed.
- **MCP servers** — copy `backend/mcp_servers.example.yaml` to
  `backend/mcp_servers.yaml`; every tool each server exposes is registered at
  startup (via `langchain-mcp-adapters`). Supports `stdio` (local subprocess)
  and `streamable_http`/`sse` (remote) transports, with the same `${ENV_VAR}`
  interpolation and skip-if-unconfigured/skip-if-unreachable behavior as the
  other tool sources. Connections are stateless — each tool call opens a
  short-lived session, so there is no lifecycle to manage.

### Tool configuration (backend `.env`)

| Variable | Effect |
| --- | --- |
| `ENABLED_TOOLS` | Comma-separated allowlist of tool names (empty = all available). Keep the set small for small local models. |
| `SERPAPI_API_KEY` / `BRAVE_API_KEY` | Enables `web_search` (provider auto-detected, or forced via `SEARCH_PROVIDER=serpapi\|brave`). |
| `OPENAI_API_KEY` | Enables `ask_openai` (`OPENAI_DELEGATE_MODEL`, default `gpt-4o-mini`). |
| `ANTHROPIC_API_KEY` | Enables `ask_claude` (`CLAUDE_DELEGATE_MODEL`, default `claude-opus-5`). |
| `REST_TOOLS_CONFIG` | Path to the REST tools YAML (default `backend/rest_tools.yaml`). |
| `MCP_SERVERS_CONFIG` | Path to the MCP servers YAML (default `backend/mcp_servers.yaml`). |
| `FETCH_PAGE_MAX_CHARS` | Truncation limit for `fetch_page` output (default 6000). |

## Prerequisites

- Python 3.11
- Node.js v20.18.0
- npm v10.9.2
- Yarn v1.22.22

## Project Structure

```
assistant-ui-langgraph-fastapi/
├── backend/         # FastAPI + assistant-stream + LangGraph server
└── frontend/        # Next.js + assistant-ui client
```

## Setup Instructions

### Set up environment variables

Go to `./backend` and create `.env` file. Follow the example in `.env.example`.

### Backend Setup

The backend is built using the LangChain CLI and utilizes LangGraph's `create_react_agent` for agent creation.

```bash
cd backend
poetry install
poetry run python -m app.server
```

### Frontend Setup

The frontend is generated using the assistant-ui CLI tool.

```bash
cd frontend
yarn install
yarn dev
```

## Credits

Based on https://github.com/hminle/langserve-assistant-ui