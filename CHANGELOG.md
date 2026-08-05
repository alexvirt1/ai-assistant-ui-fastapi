# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.3.0] - 2026-08-05

Attachments, end to end: a file dropped into the composer becomes a searchable
document with quote-backed, openable citations. What follows is one feature told
in five parts; the entries below give the measurements behind each.

**1. Attachments to documents.** Configuring an attachment adapter is enough to
make the composer's "+" button appear. Small files are inlined as ordinary text
parts, which the chat route already handles, so this needed no backend change;
size is capped via `MAX_ATTACHMENT_CHARS` and `MAX_ATTACHMENT_BYTES`, read
server-side and passed to the client so a change takes effect on restart rather
than a rebuild. Larger files are routed by size to `/api/documents` through a
Next proxy, and the model receives a reference instead of the text — a 5 MB
attachment becomes a 509-byte request. If the upload fails the adapter degrades
to a truncated inline copy rather than dropping the file. Document references
are pinned to the system prompt, not left in the conversation, because the
history trimmer had been discarding them and making an attached document
permanently unreachable; the frontend resends them on every request and persists
them to localStorage keyed by thread id, so they survive a reload, and "New
chat" clears them. A chip per document shows `name · N sections · preparing… →
ready`, reading the same store the runtime sends to the backend so it cannot
disagree with what the model was told is attached.

**2. Backend document pipeline** (`backend/app/documents/`, thirteen modules)
built in five phases: upload, Postgres storage, token-aware chunking and a
pre-flight cost estimate with no model calls; a map step producing one
structured summary per chunk, cached so an 80-minute job killed at chunk 60
keeps the first 59; a reduce step merging entities, outline and gaps
deterministically in code rather than by the model; background jobs with
progress, ETA and real cancellation; and retrieval. Retrieval is the clear
winner over summarising — 59s to index and roughly 10s per question against
43 minutes for a map-reduce pass over the same 5 MB document, answering two
questions map-reduce could not — because it re-chunks at its own granularity,
~400 tokens against 16 000. Vectors are JSONB with cosine computed in Python,
since pgvector is unavailable here. A `search_document` tool retrieves passages
and indexes lazily on first use; the agent calls it unprompted.

**3. Retrieval quality.** BM25 is fused with the vector search by reciprocal
rank, with candidates capped per channel — fusing full rankings measured worse
than lexical search alone. On Russian text the vector channel ranked the correct
chunk 168th and 374th with `top_k=5` where BM25 put both at rank 1, and end to
end, passages containing the answer went from 0/6 to 5/6. The embedding model
changed from `nomic-embed-text` to `qwen3-embedding:0.6b`; bge-m3 scores
marginally better but cannot run on an 11.75 GB card alongside the chat model at
32k context. The tool now requires a section number *and* a supporting quote per
fact — sparser but honest: the question that once produced twelve confidently
wrong names now states one fact with the sentence that supports it. A remark
plugin rewrites `[Section 148]` into a button that fetches the passage on
demand, and a citation stays plain text when no document is attached or more
than one is, since showing a passage from the wrong document is worse than
showing none.

**4. Notable fixes.** The token estimator was 1.92x wrong on non-Latin text and
is now script-aware and shared with the history trimmer, so the two budgets
cannot drift. Follow-up questions were answered from stale passages, or from the
internet; blanking earlier tool results took the reproduction from 11 137 to 170
tokens. A failed run showed an empty assistant bubble because `graph.astream`
had no exception handling. A document could be embedded twice concurrently, now
guarded by one `ensure_indexed()`. A GPU out-of-memory cost the whole answer;
search degrades to lexical-only, which needs no model on the GPU.

**5. Infrastructure.** Context window 8192 to 32768 and history budget 3000 to
12000 tokens, the latter now defaulting to a third of the former so the two
scale together — measured, 4x more input costs nothing in throughput on this
card. A new frontend test suite (Vitest, Testing Library, jsdom) alongside
roughly 200 backend tests, 14 new test files in all. React 18 to 19, Next 15 to
16, ESLint 8 to 9 flat config, `@ai-sdk/openai` and `zod` dropped.

### Added

- **Large files now reach the chat UI** (phase A of the attachment work). The
  attachment adapter routes by size: files that fit the context window are
  inlined as before, larger ones go to `/api/documents` and the model receives a
  reference instead of the text. A new `search_document` tool
  (`backend/app/tools/document_search.py`) retrieves the relevant passages —
  indexing lazily on first use — and the agent writes the answer itself, so a
  5 MB attachment becomes a 509-byte request. Verified live: the agent called
  the tool unprompted and answered correctly in 4.5s.
  - A Next proxy at `app/api/documents/[[...path]]` keeps the backend address
    server-side. The **optional** catch-all matters: `[...path]` does not match
    `/api/documents` itself, and Next then treats the multipart upload as a
    Server Action and returns "Server action not found".
  - If the upload fails the adapter degrades to a truncated inline copy with an
    explanatory note, rather than dropping the attachment.
  - **Document references are pinned to the system prompt**, not left in the
    conversation. Testing showed why: on turn 3 the model replied "please
    provide the document ID" while holding it, and at 121 messages the history
    trimmer had discarded the reference entirely, making the document
    permanently unreachable. The frontend now sends attached documents on every
    request (`lib/documentStore.ts`), the chat route accepts them, and
    `DocumentAwareState` carries them into the prompt callable, which appends
    them to the system prompt — the one part never trimmed. The previously
    failing turn-3 case now calls the tool and answers correctly.
  - "New chat" clears attached documents, so they are not announced in the next
    conversation's system prompt.
  - **Attached documents are now visible** (`components/attachments/DocumentChips.tsx`).
    Testing a 5 MB file exposed the gap: the text is never displayed, the
    reference goes only to the model, and embedding runs for about a minute in
    the background, so the only sign anything had happened was the
    `search_document` tool firing on the next question. A chip per document now
    shows `name · N sections · preparing… → ready`, reading the same store the
    runtime sends to the backend so it cannot disagree with what the model was
    told is attached. `startIndexing()` returns its outcome instead of
    discarding it, which is what drives the transition. A failed background
    index reads as a delay rather than an error, because `search_document`
    indexes on demand anyway. The scope estimate stays in the tooltip: it is
    the cost of *summarising*, and showing "about 45 minutes" inline would
    imply the next question takes that long.

- **Hybrid retrieval: BM25 fused with the vector search**
  (`backend/app/documents/lexical.py`, `retrieval.py`). Testing the complete
  text of *War and Peace* produced a confident, largely fabricated answer, and
  the cause was that the model never received the right passages. Measured on
  the 2 669-chunk index, the vector channel ranked the correct chunk **168th**
  for "Что произошло с Платоном Каратаевым?" and **374th** for "Кто такая
  Марья Дмитриевна Ахросимова?" — with `top_k=5`. BM25 puts both at **rank 1**.
  `nomic-embed-text` is English-only and its Russian vectors collapse into a
  narrow cone: median cosine 0.80 against 0.48 for the same pipeline on English,
  so rare proper nouns — exactly what these questions turn on — cannot win.
  - Fused by **reciprocal rank**, not score: cosine sat between 0.80 and 0.88
    while BM25 ran 0 to 30, and the cosine spread was too narrow to weight.
  - **Candidates are capped per channel**, which is what makes fusion work
    rather than merely average. Fusing full rankings measured *worse* than
    lexical search alone, because deep vector ranks lifted mediocre hits above
    good ones: capped at 50, Ахросимова goes 374 → 1 and Каратаев 168 → 4.
  - Retrieved chunks are expanded to their **neighbours**, since a scene runs
    across consecutive chunks, and the context is assembled **matches first,
    then neighbours** so a good match is never displaced by context around a
    better one.
  - `top_k` 5 → 12, and the context budget is now denominated in **tokens, not
    characters** — 12 000 characters is ~3 000 tokens of English but ~5 700 of
    Russian, so one cap admitted twice as much of one language as the other.
  - End to end on six Russian questions, passages containing the answer went
    from **0/6 to 5/6**. The English document that already worked still returns
    its planted facts. Verified live.

- **Citations you can open** (`frontend/lib/remarkSections.ts`,
  `components/attachments/SectionCitation.tsx`, plus
  `GET /api/documents/{id}/sections/{n}`). Answers cite `[Section 148]`, but a
  citation nobody can check is only a claim about a claim — and this is exactly
  where the model is least reliable: on *War and Peace* it cited real sections
  while merging two different scenes into one answer, which is invisible unless
  you can read the passage. A remark plugin rewrites citations into links with a
  `section:` URL and the `a` component renders them as buttons that fetch the
  passage on demand — an answer can cite a dozen sections and most are never
  opened. With no document attached, or more than one, a citation stays plain
  text: the model does not say *which* document it cited, and showing a passage
  from the wrong one is worse than showing none.
  - The href is `#section-148`, not a `section:` protocol. react-markdown runs
    every URL through `defaultUrlTransform`, which permits only http, https,
    irc, mailto and xmpp and rewrites anything else to the empty string — so the
    first version reached the renderer with `href=""` and every citation fell
    back to plain text. All the unit tests passed, because none of them went
    through react-markdown. A regression test now asserts that every href the
    plugin emits survives that sanitiser.
  - Attached documents now survive a page reload
    (`frontend/lib/documentStore.ts`). The store was memory-only while the
    conversation lives in Postgres, so after a refresh the thread still showed
    an answer citing `[Section 148]` with nothing left to resolve it against —
    the citation fell back to a dead link. Worse, the backend also stopped being
    told a document was attached, losing the pinned system-prompt block that
    makes it searchable at all. Persisted to localStorage keyed by thread id, so
    a different conversation never inherits them, and hydrated from an effect
    rather than at import so the first client render still matches the
    server-rendered HTML.

- **The model must quote what it claims**
  (`backend/app/tools/document_search.py`). A section number can be attached to
  an invented fact; a verbatim quote cannot, because the quote either appears in
  the passages or it does not. The tool now requires a section number *and* a
  supporting quote per fact, and explicitly warns that the passages come from
  different places in the document and may describe different occasions — the
  failure that put Денисов and Долохов, who belong to the 1806 Moscow visit, at
  an 1805 name-day. The effect is a sparser but honest answer: on the question
  that once produced twelve confidently wrong names, it now states one fact with
  the sentence that supports it.
  - A 14B answering model was measured and rejected: `qwen2.5:14b` at
    `num_ctx=32768` takes the entire 11.75 GB card, so loading the embedder
    evicts it and every question pays a ~19s cold reload.

### Changed

- **Embedding model: `nomic-embed-text` → `qwen3-embedding:0.6b`**
  (`backend/models.yaml`).
  The `embed` role was already registry-driven, so this is configuration, not
  code. nomic-embed-text is English-only, and on Russian it was not merely
  weaker but close to useless. A/B on 826 chunks of *War and Peace*, six
  questions, measuring where the answering chunk lands:

  | model | gold in top-5 | gold in top-12 | separation | VRAM |
  |---|---|---|---|---|
  | nomic-embed-text | 1/6 | 3/6 | 0.07 | 0.3 GB |
  | qwen3-embedding:0.6b | 5/6 | 6/6 | 0.24 | 2.2 GB |
  | **bge-m3** | **6/6** | **6/6** | **0.24** | **1.3 GB** |

  bge-m3 ranks best on paper but **cannot run on this card**. Loading it needs
  ~5 GB transient despite settling at 1.3 GB, and with qwen3:8b resident at
  `num_ctx=32768` (8.16 GB of 11.75 GB) it dies with `cudaMalloc out of memory`.
  Measured alongside the real 32k chat model: qwen3-embedding 10.33 GB and
  works, bge-m3 OOMs, and bge-m3 fits only if `OLLAMA_NUM_CTX` drops to 16384 or
  `OLLAMA_MAX_LOADED_MODELS=1` forces a swap on every question. Since retrieval
  fuses with BM25 at `top_k=12`, where both score 6/6, the model that runs wins.
  (bge-m3 was briefly configured on the strength of a benchmark taken without
  production's `num_ctx`, which made the chat model look 2 GB smaller than it
  is; it OOM'd in the UI within minutes.)
  - On the full re-indexed document the vector channel now ranks the answering
    chunk **1st, 1st, 2nd, 1st, 1st, 3rd and 1st** for the seven test questions,
    against nomic's 82nd to 374th. Passages containing the answer: **7/7**.
  - Re-chunking under the corrected token estimator took the document from
    2 669 to **6 238 chunks** — the Russian chunks were ~770 tokens while
    claiming to be 400, and are now genuinely ~400, which is finer granularity
    as well as an honest budget.
  - Storage keys by model name, so the switch re-indexed rather than silently
    comparing 1024-dimension vectors against 768-dimension ones, and the old
    rows are still there to A/B against. `EMBED_DIMENSIONS` is gone: it was
    unused and asserted 768, which is now wrong.
  - **The fabrications are gone.** The name-day question that invented a "Князь
    Василий Болконский", a "Борис Ростов" and an "Анна Павловна" as countess
    Rostova now names only real characters, correctly identified — Анна
    Михайловна Друбецкая, граф Илья Андреевич Ростов, Соня — and cites the
    sections it used. The list is still incomplete and still mixes in the 1806
    Moscow visit, because retrieval draws its twelve best chunks from across the
    whole novel (2.3% to 90.7% of it) with nothing marking which belong to the
    same occasion. That is scene disambiguation, not retrieval quality.

### Fixed

- **Follow-up questions were answered from stale passages, or from the
  internet** (`backend/app/langgraph/agent.py`,
  `app/tools/document_search.py`). A second question about an attached document
  produced "the passages provided do not mention Napoleon… would you like me to
  search?" — the agent reasoned over passages retrieved for the *previous*
  question instead of searching again. Two causes, both measured:
  - The tool hint told the model to act "when the conversation contains an
    `<attached-document>` reference". That format stopped being emitted when
    references moved to the system prompt, so the trigger it named appeared
    nowhere. Hint and block now describe the same thing, with a test asserting
    they agree.
  - A search returns ~11 000 tokens of passages, which then sat in the history
    as both the largest thing in the context and stale by construction.
    Reproduced with one in the history: the agent called `web_search` and
    `fetch_page` instead of `search_document`, answered from the internet
    (claiming Pierre met Napoleon, which never happens), and on another run
    cited a section number appearing nowhere in the passages. Tool results from
    earlier turns are now blanked to a placeholder telling the model to search
    again — the message is kept and only its content replaced, so the calling
    AIMessage is never orphaned. Context on the reproduction fell from **11 137
    to 170 tokens**, and both failing questions now search and answer correctly
    with quotes.

- **The token estimator was 1.92x wrong on non-Latin text**
  (`backend/app/documents/chunker.py`). It assumed 4 characters per token for
  every script; measured against cl100k on 3.2 MB of Russian, the real figure is
  2.08. Every budget denominated in these units inherited the error —
  "400-token" retrieval chunks were really ~770 tokens, and the history trimmer
  kept nearly double what it believed, which is a context overflow rather than a
  rounding error. Now script-aware, and shared with the trimmer via
  `count_message_tokens` so the two budgets cannot drift apart. ASCII text takes
  the original path unchanged, so English behaviour is bit-for-bit identical, and
  the new counter is floored at the old one so it can never keep *more* history
  than before. Tool calls are counted too: their content is often empty while
  the call itself is real tokens.

- **A failed run showed an empty message instead of an error**
  (`backend/app/add_langgraph_route.py`). `graph.astream` had no exception
  handling, so when the embedding model hit `cudaMalloc out of memory` the tool
  node raised, the graph aborted mid-stream, and the UI rendered an assistant
  bubble with no content — indistinguishable from the model choosing to say
  nothing. A failure is now appended to the stream, keeping any text already
  emitted, and logged with a traceback. Covered by `tests/test_stream_errors.py`,
  which caught a bug in the first version of the handler: `"".splitlines()[0]`
  raises `IndexError` for an exception with an empty message — inside the
  handler, so `TimeoutError()` and `ValueError()` would still have written
  nothing.

- **A GPU out-of-memory cost the whole answer**
  (`backend/app/tools/document_search.py`, `documents/retrieval.py`).
  `search_document` now degrades to lexical-only search when the question
  cannot be embedded: BM25 needs no model on the GPU, and on the questions that
  motivated it lexical search alone ranked the answering chunk 1st. Indexing
  failures are likewise non-fatal when the document was indexed on an earlier
  run. Verified against the 6 238-chunk document with the exact production error
  injected — 22 829 characters of passages returned where it previously raised.

- **A document could be embedded twice, concurrently**
  (`backend/app/documents/indexing.py`). Uploading fires an index request while
  `search_document` indexes lazily if it finds nothing, so asking a question a
  few seconds after attaching — ordinary use — had both see an empty index and
  both embed the whole document: ~60s of duplicated work per 5 MB on a machine
  that runs one model at a time, with `save_retrieval_chunks` doing
  DELETE-then-INSERT so the two passes could interleave. Both call sites now go
  through one `ensure_indexed()` guarded by a per-document lock, which also
  removes the duplicated indexing logic they each carried. Verified against the
  live database on the 4 974-chunk document: three concurrent callers, a
  booby-trapped embedder that raises if touched, and no re-embedding.

- **Large-document pipeline, phase 5 — retrieval**
  (`backend/app/documents/retrieval.py`, `embeddings.py`, plus `/index` and
  `/ask`): question-answering over a document, as the counterpart to
  summarising rather than a replacement.
  - On the same 5 MB document: **59s to index, ~10s per question**, against
    **43 minutes** for a map-reduce pass — and it answered two questions
    map-reduce could not. "Emergency muster point Delta-9, capacity 412" was
    lost by *both* summarisation runs; retrieval returns it verbatim.
  - **Retrieval re-chunks at its own granularity** (~400 tokens versus 16 000
    for summarising), which is what makes it work. With the coarse
    summarisation chunks every similarity score sat between 0.44 and 0.50 — no
    discrimination at all, because a 64 KB chunk embeds to a vector dominated by
    its boilerplate. With fine chunks the correct chunk scores 0.61 against 0.50.
  - Fixed a bug found by that live test: the first implementation truncated each
    chunk to 8 000 characters before embedding, and the facts under test sat at
    offsets 11 440 and 18 795 — they were never in the index.
  - Vectors are JSONB with cosine computed in Python: pgvector is unavailable
    here (needs OS-level install) and at ~5 000 chunks the maths is
    milliseconds. Answers cite the sections they came from, and the model is
    told to say so when the retrieved text does not contain the answer —
    verified with a question the document does not cover.
  - Each question costs ~9s of model swapping because the VM will not hold the
    embedding and chat models together; `OLLAMA_MAX_LOADED_MODELS=2` on the
    Ollama service would remove it (0.6 GB + 6 GB against 11.75 GB).
  - 18 new tests (198 backend total) covering cosine edge cases, ranking order,
    stable tie-breaking, score thresholds, and context assembly.
  - End-to-end on the live service with a 650 KB document, all five phases:
    upload and scope (11 chunks, tier `confirm`), index (597 retrieval chunks in
    12.6s), three planted facts all retrieved correctly, summarise (5.4 min, 0
    degraded, 117 key_facts), and a cached summary served in 0.28s. Retrieval
    found all three facts; the summary carried one — the same split the 5 MB run
    showed, reproduced at a tenth the size.
  - Scope estimator recalibrated a second time. `CHUNK_SUMMARY_TOKENS` 150 → 800:
    key_facts extraction roughly quintupled output per chunk, so the previous
    figure under-predicted by 1.7x. It now reproduces both measured runs — 87
    chunks predicted 45.6 against 43.5 actual, 11 chunks 6.5 against 5.4.

- **Large-document pipeline, phase 4 — jobs, progress and cancellation**
  (`backend/app/documents/jobs.py`, plus three endpoints): a 78-minute pipeline
  cannot be an HTTP request, so `POST /api/documents/{id}/summarize` returns
  immediately with a job id, `GET /api/documents/jobs/{id}` reports progress,
  and `POST /api/documents/jobs/{id}/cancel` stops it.
  - Verified live: start returned in **0.27s**, polling tracked `map 1/2` then
    `reduce 0/1` with an ETA computed from the observed rate, and the job
    completed in 1.0 min with the calibration constant retained.
  - **Cancellation genuinely stops the work** — the job showed `0/1` eight
    seconds after cancelling, and a second cancel is refused with 409. Chunk
    summaries already computed survive, so a cancelled job is not wasted.
  - **Completed summaries are stored** in a new `document_summaries` table and
    served from it — a repeat request returned in **0.24s** instead of re-running
    the pipeline. `?force=true` overrides.
  - **Duplicate starts return the running job.** Two concurrent jobs over one
    document would compete for the same single-model VM and double the wait for
    nothing.
  - Job state is deliberately in-memory: the expensive artefacts are in
    Postgres, so a restart loses tracking but not work — a restarted job
    replays from cache in seconds.
  - 13 new tests (166 backend total) covering completion, failure capture,
    duplicate starts, independent documents, cancellation actually halting
    execution, refusing to cancel a finished job, phase transitions, and ETA
    projection.

- **Large-document pipeline, phase 3 — the reduce step**
  (`backend/app/documents/reducer.py`, `reduce.py`): combines chunk summaries
  into a `DocumentSummary` of `overview`, `key_findings`, `outline`, `entities`
  and `gaps`.
  - **Entities, outline and gaps are merged deterministically in code**, not by
    the model, which only writes the two prose fields. Asked to merge 87 entity
    lists a model silently drops some, and hierarchical reduce compounds that
    loss at every level; computed in code, a name appearing once in chunk 3
    reaches the final summary regardless of depth. A test asserts this, and
    mutation-testing confirms it fails if the merge is moved off the originals.
  - **Hierarchical when needed**: 87 chunk summaries fit a single reduce pass,
    so the recursion exists for documents several times larger. Batching packs
    summaries under a token budget and never drops one, even if a single summary
    exceeds the budget alone.
  - Runs on the `deep` role (qwen2.5:14b) — one model swap, worth it for the
    output a person reads. A malformed response stitches the inputs rather than
    discarding the entire map phase.
  - **Numeric preservation fixed by testing**: on a live 4-chunk run the map
    correctly captured a calibration constant of `8.472` and the reduce dropped
    it, producing a shorter, better-reading summary that had lost the document's
    most important value. Making the prompt insist that every number, threshold
    and identifier be carried through restored it and lengthened the output from
    711 to 1204 characters.
  - 27 new tests (153 backend total) covering entity merging, outline
    collapsing, gap collection, batching, hierarchical recursion, and stitching
    on failure.

- **Large-document pipeline, phase 2 — the map step**
  (`backend/app/documents/mapper.py`, `summaries.py`, `callers.py`): one
  structured-prose summary per chunk — `topic`, `findings`, `entities`,
  `uncertain` — with caching that makes a long job resumable.
  - **Three attempts of decreasing strictness**: structured output, structured
    with an explicit JSON reminder, then unvalidated prose flagged `degraded`.
    Over 87 chunks a small model will fail a schema occasionally; an 80-minute
    job must not die on chunk 61. `asyncio.CancelledError` is re-raised rather
    than mistaken for a schema failure and retried.
  - **Cached in `chunk_summaries`**, keyed by
    `(document_id, idx, model_name, prompt_version)` and written as each chunk
    completes rather than batched, so a job killed at chunk 60 keeps the first
    59. Measured against Postgres: a re-run was **106x faster** and fully
    cache-served, and bumping `PROMPT_VERSION` correctly missed the cache
    instead of serving summaries produced by older wording.
  - **Sequential, not fanned out.** The VM serves one model on one GPU, so
    parallel calls would queue at Ollama for no gain while making progress
    reporting and cancellation harder.
  - **Model access is injected**, so validation, repair, degradation, caching,
    progress and cancellation are all covered by 12 offline tests (126 backend
    total). `callers.py` is the only module that talks to Ollama.
  - The per-chunk prompt spells out each field explicitly. Relying on the
    schema's field descriptions alone, qwen3:8b returned an **empty `entities`
    list** for text naming five people and companies, and a `topic` echoing the
    document title; with explicit instructions it extracted all five names and
    section-specific topics.

- **Large-document pipeline, phase 1** (`backend/app/documents/`): upload,
  storage, token-aware chunking, and a pre-flight scope check — all with **no
  model calls**, so a 5 MB file is sized in milliseconds instead of silently
  truncated to 0.46% of itself.
  - `POST /api/documents` stores the file and returns what processing would
    cost: for a real 5 MB upload, `87 chunks, about 78 minutes`, tier
    `consider_retrieval`. `GET /api/documents/{id}` repeats the estimate.
  - Documents are stored in Postgres rather than inlined into the chat message.
    Inlined text is persisted into the LangGraph checkpoint and re-sent every
    turn, so a large attachment would poison the thread permanently.
  - Chunking prefers paragraph boundaries and overlaps consecutive chunks, so a
    fact spanning a boundary survives in at least one of them. The invariant
    that no chunk exceeds the budget is enforced and tested across budgets.
  - Deduplication by SHA-256: re-uploading the same 5 MB file returned in 0.14s
    versus 2.6s, with no duplicate rows. This matters more in phase 2, where it
    will preserve per-chunk summaries that cost ~80 minutes to compute.
  - Tier `single_pass` means a document fits one context window and skips the
    pipeline entirely, so ordinary attachments never pay for any of this.
  - 31 new tests (114 backend total), covering chunk budgets, overlap, coverage,
    pathological input with no separators, tier thresholds, and that the
    arithmetic chunk estimate stays within 10% of what the chunker really
    produces — an estimate that drifts from reality would make the warning a lie.

### Changed

- **Attachment limits are now configurable** via `MAX_ATTACHMENT_CHARS` and
  `MAX_ATTACHMENT_BYTES` in `frontend/.env.local` (see
  `.env.local.example`). Read **server-side** in `app/page.tsx` and passed to
  the client rather than exposed as `NEXT_PUBLIC_*`, because those are inlined
  at build time — this way a change takes effect on a frontend restart with no
  rebuild, which is the point when experimenting with large files. The page is
  `force-dynamic` so the value is not frozen into a prerender. Invalid values
  (empty, non-numeric, zero, negative) fall back to the defaults rather than
  silently disabling the cap. `frontend/.gitignore` gained a negation so the
  example template stays tracked while `.env.local` remains ignored.
- **Context window 8192 → 32768**, history budget 3000 → 12000 tokens, and the
  frontend attachment cap 6000 → 24000 characters. Measured on the 12 GB VM
  rather than estimated: `qwen3:8b` uses 6.08 GB at 8k and 8.83 GB at its full
  40 960-token context, **entirely in VRAM either way**, generating at 57.5 vs
  57.6 tok/s — so ~4x more input costs nothing. 32768 leaves headroom under the
  model's 40 960 ceiling.
- `HISTORY_MAX_TOKENS` now defaults to a third of `OLLAMA_NUM_CTX` instead of a
  fixed 3000. The two have to move together: raising the window alone leaves it
  unused, raising the budget alone overflows it.
- For the record, CPU offload is available and automatic but is the wrong tool
  for large documents: `qwen2.5:14b` at 32k spills 3.46 GB to RAM and prompt
  processing collapses from 621 to **47 tok/s** — a 30k-token document would
  take ~10 minutes just to ingest, versus ~48s fully on GPU.

### Fixed

- `tests/test_history_trimming.py::test_history_is_actually_trimmed` asserted
  against the deployed `HISTORY_MAX_TOKENS`, so raising it in `.env` failed a
  test whose subject code was correct. It now pins its own budget, as the other
  trimming tests already did.

### Added

- **Frontend test suite** (Vitest + Testing Library + jsdom): 23 tests, `pnpm
  test`, ~3s. Added because the `crypto.randomUUID` attachment bug passed
  `tsc`, `eslint` **and** the production build while the feature was entirely
  broken — it was a runtime, environment-dependent failure, so only tests that
  execute the code in a stubbed browser environment can catch that class.
  - `lib/attachments.test.ts` and
    `components/attachments/TextAttachmentAdapter.test.ts` cover truncation,
    the truncation marker, the hard file limit, the text-part contract with the
    backend, and — as an explicit regression — `add()` succeeding when
    `crypto.randomUUID` is absent.
  - `components/ThemeToggle.test.tsx` demonstrates component coverage with
    next-themes stubbed.
  - Tests run in `jsdom` rather than node, since the bugs worth catching here
    involve browser APIs behaving differently than they do in Node. Stubbing an
    insecure context requires replacing `globalThis.crypto` wholesale —
    `delete crypto.randomUUID` silently does nothing, which would have made the
    regression test pass against broken code.
  - Verified by mutation: reintroducing the `crypto.randomUUID` bug, removing
    the size cap, dropping the truncation marker, and switching the sent part
    from `text` to `file` each fail specific tests.

- **Text file attachments** (phase 1 of attachment support). Configuring an
  attachment adapter on `useEdgeRuntime` is enough to make the composer's "+"
  button appear — assistant-ui's built-in `ComposerAddAttachment` opens an
  `<input type="file">` filtered by the adapter's `accept`, so no custom UI was
  needed. Verified by a control build: the button is absent without the adapter.
  - `frontend/components/attachments/TextAttachmentAdapter.ts` reads the file in
    the browser and sends it as an ordinary **text** part, which the chat route's
    `convert_to_langchain_messages` already handles — so this needs no backend
    change.
  - `frontend/lib/attachments.ts` caps each attachment at 6000 characters
    (matching the backend's `REST_TOOL_MAX_CHARS`) and appends an explicit
    truncation marker; files over 1 MB are rejected before being read. An
    attachment is inlined into the message and then persisted in the checkpoint,
    so an uncapped one would compete for the model's 8192-token context on every
    later turn of the thread — `SimpleTextAttachmentAdapter` from the library
    has no such limit, which is why it is not used.
  - Accepts `.txt .md .csv .tsv .json .xml .yaml .html .css .log` plus `text/*`;
    extensions are listed alongside MIME types because browsers report
    inconsistent types for Markdown and YAML.
  - Attachment ids come from `attachmentId()`, which falls back when
    `crypto.randomUUID` is missing. That API exists only in a **secure context**
    (HTTPS or localhost), and this app is served over plain HTTP on
    `0.0.0.0:3000` — so a browser reaching it by LAN address threw inside
    `add()`, the attachment was never added, and the message was sent without
    it while the model correctly reported seeing no file.

## [1.2.0] - 2026-08-03

### Added

- **Model registry and Ollama VM management** (`backend/app/models/`) — phase 0
  of the multi-step planner architecture. Code asks for a *role* (`fast`,
  `deep`, `code`, `vision`, `embed`) instead of an Ollama tag; roles are
  declared in `models.yaml` (see `models.example.yaml`) and resolved by
  `make_chat_model(role)`. A role whose tag the VM does not serve falls back to
  `fast`, mirroring how a tool with a missing API key self-disables, and
  `OLLAMA_MODEL` still wins for the default role so existing `.env` files are
  unaffected. `app/models/ollama.py` wraps the VM endpoints —
  `list_available()`, `resident()`, `ensure()` (pull), `warm()` (preload),
  `validate()` — and `python -m app.models` prints roles, inventory and what is
  currently loaded.
- **Test suite** (`backend/tests/`, pytest + pytest-asyncio, configured in
  `pyproject.toml`): 83 offline tests covering model role resolution and
  fallback, model construction, the history-trimming window, tool registration
  and self-disabling, and REST tool construction with `{arg}` / `${ENV_VAR}`
  interpolation. No VM, database or network required; runs in ~2s.
  Live-infrastructure checks stay manual rather than being mocked.
- `backend/tests/test_model_factory.py` constructs the model and asserts on the
  resulting fields. The langgraph migration exposed that nothing exercised
  `make_chat_model`, so a setting that silently stopped applying failed no test.

### Changed

- **Migrated to langgraph 1.x** — langgraph 0.2.76 → 1.2.10, langgraph-checkpoint
  2.1.2 → 4.1.1, langgraph-checkpoint-postgres 2.0.25 → 3.1.1, langchain-core
  0.3.86 → 1.5.3, langchain-ollama 0.2.3 → 1.1.0, langchain-openai 0.2.14 →
  1.4.1. This removes the `allowed_objects` pending-deprecation warning at its
  source: checkpoint 4.1.1 constructs `Reviver(allowed_objects="core")`, which
  no version reachable from langgraph 0.2.x did. The Postgres checkpoint schema
  is unchanged — the migration lists are identical and the database was already
  at v9 — so rollback is a lockfile revert with no database action.
- **Frontend upgraded to Next 16 and React 19.** `next` 15.0.3 → 16.2.12,
  `react`/`react-dom` 18.3.1 → 19.2.8, types to match, `@types/node` → 26.1.2,
  `postcss` → 8.5.25. Next 15.0.3 accepted only React 18 or a specific 19
  release candidate, so Next had to move first; Next 16 accepts React 18, which
  made it a valid intermediate step. `tsconfig.json` now sets
  `"jsx": "react-jsx"`, which Next 16 requires.
- **ESLint 8 → 9 with flat config.** Next 16 removed the `next lint` command,
  which silently broke the `lint` script, and `eslint-config-next` 16 requires
  ESLint >= 9. `.eslintrc.json` is replaced by `eslint.config.mjs` and the
  script is now `eslint .`. Fixing the resulting errors converted three
  `require()` calls in `tailwind.config.ts` to ESM imports.
- `uvicorn` 0.23.2 → 0.52.0, `mcp` 1.12.4 → 1.29.0, `langchain-mcp-adapters`
  0.1.14 → 0.3.1, `sse-starlette` 1.8.2 → 3.4.6.
- `assistant-stream` 0.0.5 → 0.0.34. Verified wire-compatible with the frontend
  before upgrading: the data-stream encoder emits the same `0:` / `b:` / `c:` /
  `a:` prefixes with identical JSON keys, confirmed by diffing raw responses
  from both versions.
- `OLLAMA_KEEP_ALIVE` (default `30m`) is now passed explicitly to `ChatOllama`.
  Ollama's own default is 5 minutes, so an idle conversation re-paid the cold
  model load — measured at ~6s for an 8B and ~19s for a 14B, against 0.3s warm.
  Measured while building this: the VM holds **one model at a time**; loading a
  second evicts the first. Step-level model routing would therefore be dominated
  by load time, so the planned executor groups steps by model rather than
  alternating.
- `fastapi` is now a declared dependency. `app/server.py` imports it directly
  but it arrived only via `langserve`, a transitive of the `langchain-cli` dev
  dependency, so a production-only install would not have had it.
- **`langchain-cli` removed from dev dependencies.** Nothing imports it or
  `langserve`; it was the scaffolding tool that generated the project, and its
  `langserve[all]` dependency pinned `uvicorn <0.24`, blocking the upgrade. It
  was also the only source of `fastapi`, now declared explicitly.
- **`mcp` held at 1.x, declared explicitly to enforce it.** `langchain-mcp-adapters`
  0.3.1 requires `mcp>=1.24.0` with no upper bound, so resolvers pair it with
  mcp 2.x — but they are incompatible: the adapter imports `RequestContext`
  from `mcp.shared.context`, which mcp 2.0 removed. Without the explicit pin a
  future relock could silently produce that combination.
- Removed `zod` and `@ai-sdk/openai` from the frontend — neither was imported
  anywhere. Dropping the latter also removed the AI SDK v6 migration from scope.
- Version bumped to 1.2.0 (backend `pyproject.toml`, frontend `package.json`).

### Fixed

- **Orphaned tool result when a single turn exceeded the history budget**
  (`backend/app/langgraph/agent.py`): the budget-exhausted fallback returned
  just the final message, which mid-ReAct-loop is a `ToolMessage` — sending a
  tool result with no matching `AIMessage`, which providers reject. Reachable
  whenever a tool returns more than the whole budget (the Yahoo Finance history
  endpoint returns ~186 KB). The fallback now falls back to the last human turn
  onward, keeping any tool exchange intact. Found by the new tests: sweeping
  budgets rather than asserting at a single one exposed it.
- **A failed MCP import could take down the whole backend**
  (`backend/app/tools/mcp/loader.py`): `connect_mcp_servers()` imported
  `MultiServerMCPClient` outside its `try`, so an incompatible
  langchain-mcp-adapters/mcp pair raised inside the FastAPI lifespan and the
  app failed to start — contradicting the function's own promise that a broken
  server is skipped rather than blocking startup. Only reachable with an
  `mcp_servers.yaml` present. The import is now guarded: MCP is logged as
  unavailable and its servers skipped.
- `model_kwargs={"think": False}` replaced with `reasoning=False` in
  `backend/app/models/factory.py`. langchain-ollama 1.x dropped `model_kwargs`,
  and because the model config is `extra="ignore"` the old spelling is silently
  discarded rather than raising — which would have let qwen3's reasoning
  preamble back into the chat window undetected.

### Notes

- **Staying on `@assistant-ui/react` 0.7.17** rather than upgrading to 0.15.1.
  0.15 removes everything this project's integration is built on —
  `useEdgeRuntime`, the styled `Thread`, `makeMarkdownText`, `useMessage`,
  `useThread`, `useAssistantRuntime` — and drops the `/tailwindcss` plugin
  entrypoints that supply the `--aui-*` light/dark palette. The replacement
  runtime, `useAssistantTransportRuntime`, is marked `@alpha`. The upgrade would
  therefore be a rewrite of the presentation layer (runtime, chat UI, theming)
  with no test coverage to catch regressions, for no feature this project needs.
  0.7.17 declares `react: ^18 || ^19` and runs unchanged on React 19 / Next 16.
- Consequence: 0.7.17 declares `tailwindcss: ^3.4.4` as a peer dependency, so
  **Tailwind is pinned to 3.x** for as long as assistant-ui stays on 0.7.17.

## [1.1.2] - 2026-07-31

### Added

- **Light/dark theme with a toggle button**: `next-themes` mounted in
  `frontend/app/layout.tsx` via `frontend/components/ThemeProvider.tsx`
  (`attribute="class"`, `defaultTheme="system"`), and a
  `frontend/components/ThemeToggle.tsx` button in the chat header beside "New
  chat". No new palette was needed — assistant-ui's tailwind plugin already
  defines both a `:root` and a `.dark` set of `--aui-*` variables, and
  `tailwind.config.ts` was already `darkMode: ["class"]`, so the whole chat
  surface follows the class on `<html>`. `<body>` now carries
  `bg-aui-background text-aui-foreground` so the page around the thread matches,
  and `<html>` has `suppressHydrationWarning` because next-themes sets the class
  before React hydrates. The choice persists in localStorage and defaults to the
  OS preference; an inlined script applies it before first paint, so there is no
  flash of the wrong theme.

- **LaTeX math rendering in the chat UI**: `makeMarkdownText` in
  `frontend/components/MyAssistant.tsx` now runs `remark-math` (parses `$...$`
  and `$$...$$`) and `rehype-katex` (renders it), with `katex/dist/katex.min.css`
  imported once in `frontend/app/layout.tsx` — KaTeX emits markup only, so
  without the stylesheet math renders unstyled. `BASE_PROMPT` in
  `backend/app/langgraph/agent.py` now instructs the model to use `$...$` and
  `$$...$$` rather than `\(...\)` or `\[...\]`, which `remark-math` does not
  recognise; the `preprocess` prop that would normalise those client-side does
  not exist in the installed `@assistant-ui/react-markdown` 0.7.5. Adds
  `remark-math`, `rehype-katex` and `katex` as frontend dependencies (~88 kB to
  the route's First Load JS, plus a separate 27 kB stylesheet and KaTeX fonts).

- `RAPIDAPI_KEY` documented in `backend/.env.example`, for the Yahoo Finance
  REST tool entries (`get_stock_quote`, and a commented `get_stock_history`)
  configured in `backend/rest_tools.yaml`. Both self-disable while the key is
  unset, so no restart-time failure if it is absent.

### Changed

- Version bumped to 1.1.2 (backend `pyproject.toml`, frontend `package.json`).

## [1.1.1] - 2026-07-30

### Added

- **Conversation history trimming** (`backend/app/langgraph/agent.py`):
  `create_react_agent` now receives a `prompt` callable instead of a string. It
  prepends the composed system prompt and appends a `trim_messages` window of
  recent history bounded by `HISTORY_MAX_TOKENS` (default 3000). Previously the
  full thread was sent on every call; once it outgrew `OLLAMA_NUM_CTX`, Ollama
  truncated from the front and dropped the system prompt — including the tool
  guidance — while recent stale answers survived, so the model answered from
  memory instead of calling `web_search`. The window uses `start_on="human"` so
  a `ToolMessage` is never sent without its calling `AIMessage`, and falls back
  to the latest message if one turn exceeds the budget. Trimming affects only
  what the model sees; the stored transcript is untouched.

### Fixed

- **Post-tool waiting dot did not match the one shown before the first token**
  (`frontend/components/tools/ToolExecutionIndicators.tsx`): assistant-ui draws
  its streaming indicator as a pulsing U+25CF glyph that inherits the
  surrounding text colour and size, via
  `:where(.aui-md-running):empty::after`. The replacement dot added in 1.1.0
  was a fixed 10px background-filled `div` in gray-400, so it read as a
  different element. It now uses the same glyph and utilities.

### Changed

- Version bumped to 1.1.1 (backend `pyproject.toml`, frontend `package.json`).

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
