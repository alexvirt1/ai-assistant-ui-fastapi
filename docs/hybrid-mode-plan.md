# Hybrid mode (local first, cloud when needed): proposal

Status: **proposal, awaiting decisions** (see the last section). Target release: 1.9.0.
Branch: `hybrid-mode`, merged into `dev`, then `dev` into `main`.

## Goal

The local model (`qwen3:8b` on AI-1) answers by default. When a task is beyond it, a more capable
cloud model (Claude, OpenAI/Codex, DeepSeek, or a custom OpenAI-compatible endpoint) answers
instead. The user can always see which model answered and what it cost, and nothing leaves the
network unless the chat's mode or the user's request allows it.

## Today

`app/tools/llm/delegate.py` has `ask_openai` and `ask_claude`, enabled by `OPENAI_API_KEY` /
`ANTHROPIC_API_KEY`. The local model may send one prompt and gets the text back, cut at 6000
characters. Three weaknesses:

- an 8B model rarely knows when it is out of its depth, so it seldom calls them
- the cloud model sees one question, not the conversation
- its answer is rewritten by the local model, which loses quality

The backend log shows no use of either tool.

## Design

### 1. Three modes per chat

A picker in the header, next to the model picker:

| Mode | Behaviour |
|---|---|
| **Local** (default) | Everything stays on AI-1, as now. |
| **Auto** | The local model answers; when it or a rule decides the task is too hard, the chat proposes escalating and the user approves with one click. |
| **Cloud: \<model\>** | That cloud model answers every turn in this chat. |

A request in the message itself ("ask Claude", "use DeepSeek for this") escalates that turn only.
The mode is stored per chat (a column on `chat_threads`).

### 2. What escalation does: hand-over (recommended)

The cloud model takes over **the whole turn**:

- It gets the conversation (recent history, trimmed to a token budget) and the same tools: web search,
  documents, image generation, time.
- Its answer streams straight to the user, labelled "☁ Claude Sonnet 5.5 · 3.1k tokens · ≈ $0.02".
- The next turn goes back to local, unless the chat is in Cloud mode.

This fits the existing graph: `make_model_selector` in `app/langgraph/agent.py` already resolves the
model on every step, which is how model switching works today. The selector would return the cloud
model, with the same tools bound, for a turn that is escalated.

**Consult** (today's pattern: the cloud answers a sub-question and the local model writes the
reply) stays available as an option. It costs less and sends less, but its answers are weaker.

### 3. When Auto escalates

Rules, rather than the 8B model's judgement alone:

- **The model asks:** a single `escalate(reason)` tool replaces `ask_openai` / `ask_claude`.
- **Failure signals:** an empty or cut-off answer, repeated tool errors, or the local model saying it
  can't do something.
- **Task signals:** large code generation or review, long multi-step reasoning, or a conversation
  longer than the local context window.

Escalation shows an approval card: "Local model suggests Claude Sonnet: *needs deeper code review*.
Sends ~4,200 tokens (≈ $0.03). [Send to cloud] [Keep local]". Sending starts a new run of the
same turn with the cloud model.

### 4. Providers

One config file, `backend/cloud_models.yaml` (like `models.yaml`):

```yaml
- name: claude-sonnet
  provider: anthropic
  model: claude-sonnet-5-5
  api_key_env: ANTHROPIC_API_KEY
  price_per_mtok: {input: ..., output: ...}
  tools: true
  vision: true
- name: deepseek
  provider: openai_compatible
  base_url: https://api.deepseek.com
  model: <model id>
  api_key_env: DEEPSEEK_API_KEY
  price_per_mtok: {input: ..., output: ...}
  tools: true
```

| Provider type | Covers | Library |
|---|---|---|
| `anthropic` | Claude (Opus / Sonnet / Haiku) | `langchain-anthropic` (**new dependency, needs approval**), or a small adapter on the `anthropic` SDK already installed |
| `openai` | OpenAI models, including Codex models | `langchain-openai`, already installed |
| `openai_compatible` | DeepSeek, OpenRouter, vLLM, any custom endpoint | `langchain-openai` with `base_url` |

An entry is offered only when its key variable is set. API keys live only in `backend/.env`, which
the owner edits; `.env.example` gets the variable names.

### 5. Privacy and cost guardrails

- **Local by default.** Nothing goes to a cloud model without Auto or Cloud mode, or an explicit request.
- **Documents and image prompts:** not sent unless allowed per chat.
- **Budgets:** a monthly limit (`CLOUD_BUDGET_USD`) and a per-turn token cap
  (`CLOUD_MAX_TOKENS_PER_TURN`). Over budget, the chat says so and stays local.
- **Usage log:** a `cloud_usage` table (chat, provider, model, input/output tokens, cost, time). The
  month's total is shown in the header.
- **Labels:** every cloud answer is marked with its model, in the chat and in Export PDF.
- **Failures:** a cloud error, timeout or refusal falls back to the local model, with a note saying so.

### 6. Frontend

- Mode picker in the header, per chat.
- Badge on cloud answers (model, tokens, cost).
- Approval card for Auto escalations.
- Month-to-date cloud cost in the header; a warning near the budget.
- Settings for the chat: allow documents / image prompts to be sent.

### 7. Tests

- **Backend (offline):** provider config and key detection; model selection per mode; history
  trimming for the cloud budget; escalation rules; budget enforcement; usage logging; fallback on
  cloud errors; the privacy switches. Cloud APIs are mocked.
- **Frontend:** mode picker, badges, approval card, budget warning.
- **Live:** a few short turns per configured provider, after the owner sets the keys.

## Phases

| # | Work | Result |
|---|---|---|
| 1 | Provider config, cloud models in the agent loop, Cloud mode, labels, usage log, budget | Pick a cloud model per chat |
| 2 | Auto mode: `escalate` tool, rules, approval card | Local first, cloud on approval |
| 3 | Consult mode, the document/image privacy switches, retire `ask_openai` / `ask_claude` | Rounding off |
| 4 | Docs, CHANGELOG, `.env.example`, runbook note | Release 1.9.0 |

## Open decisions

1. **Providers to start with:** Claude / OpenAI (Codex) / DeepSeek / custom (which endpoint?), and
   which API keys the owner already has.
2. **Escalation style:** hand-over (recommended) or consult.
3. **Approval in Auto mode:** ask every time (recommended), or escalate silently within budget.
4. **Monthly budget** in USD.
5. **Documents:** may attached documents ever be sent to a cloud model?
6. **Dependency:** add `langchain-anthropic` for Claude, or write a small adapter on the `anthropic`
   SDK already installed.
