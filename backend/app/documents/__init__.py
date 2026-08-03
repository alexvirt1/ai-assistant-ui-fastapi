"""Large-document handling: chunking, sizing, storage, map and reduce.

Phases 1 to 4 of the map-reduce pipeline.

Chunking and sizing involve no model calls, so a document is measured before any
inference is paid for - "87 chunks, about 78 minutes" is known in milliseconds.
The map step (mapper.py) then summarises chunks one at a time, caching each
result so the cost is paid once.

The reduce step (reducer.py) combines those summaries into one document
summary, recursing hierarchically if they overflow a single window. Entities,
the outline and the gap report are merged deterministically in code rather than
by the model, so nothing is lost between levels.

Long jobs are detached (jobs.py): starting one returns immediately with an id,
progress is polled, and cancellation stops the work while keeping whatever was
already cached.

Model access is injected into map_document and reduce_document rather than
imported by them, which keeps the orchestration testable without a VM;
callers.py supplies the real clients.
"""

from .chunker import Chunk, chunk_text, estimate_tokens
from .jobs import Job, JobRegistry, JobStatus, Phase, registry, run_summary
from .mapper import MapProgress, MappedChunk, map_document, summarize_chunk
from .reduce import DocumentSummary, ReduceOutput, merge_entities
from .reducer import reduce_document
from .scope import Scope, Tier, estimate_scope, scope_for_text
from .summaries import PROMPT_VERSION, ChunkSummary

__all__ = [
    "PROMPT_VERSION",
    "Chunk",
    "ChunkSummary",
    "DocumentSummary",
    "Job",
    "JobRegistry",
    "JobStatus",
    "MapProgress",
    "MappedChunk",
    "Scope",
    "Tier",
    "chunk_text",
    "estimate_scope",
    "estimate_tokens",
    "map_document",
    "merge_entities",
    "reduce_document",
    "registry",
    "run_summary",
    "scope_for_text",
    "summarize_chunk",
]
