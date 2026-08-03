"""Running the reduce step, hierarchically when one pass will not fit.

87 chunk summaries at ~300 tokens each fit a single 32k reduce prompt, so the
common case is one call. The recursion exists for documents several times
larger, where the summaries themselves overflow the window - without it there
would be a cliff at roughly 100 chunks.

As in the map step, the model client is injected so every branch is testable
without a VM.
"""

import asyncio
import logging
from typing import Awaitable, Callable, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from .reduce import (
    DocumentSummary,
    REDUCE_SYSTEM_PROMPT,
    ReduceOutput,
    as_chunk_summary,
    batch_summaries,
    build_outline,
    build_reduce_prompt,
    collect_gaps,
    merge_entities,
    merge_key_facts,
)
from .summaries import ChunkSummary

logger = logging.getLogger(__name__)

# Room for the summaries themselves, leaving the rest of the window for the
# system prompt, the instruction and the generated output.
DEFAULT_REDUCE_BUDGET = 20_000

# A runaway hierarchy would mean the batching is not converging.
MAX_LEVELS = 5


class ReduceCaller(Protocol):
    async def __call__(self, messages: list) -> ReduceOutput: ...


async def _reduce_batch(
    summaries: list[ChunkSummary], call: ReduceCaller, level: int
) -> ReduceOutput:
    messages = [
        SystemMessage(REDUCE_SYSTEM_PROMPT),
        HumanMessage(build_reduce_prompt(summaries, level)),
    ]
    try:
        return await call(messages)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        # Losing the whole reduce because one call returned malformed JSON would
        # waste the entire map phase, so fall back to stitching the inputs.
        logger.warning("reduce failed at level %d (%s); stitching instead", level, exc)
        return ReduceOutput(
            overview="(automatic overview unavailable for this group)",
            key_findings=" ".join(s.findings for s in summaries),
        )


async def reduce_document(
    summaries: list[ChunkSummary],
    call: ReduceCaller,
    *,
    budget_tokens: int = DEFAULT_REDUCE_BUDGET,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> DocumentSummary:
    """Combine chunk summaries into one document summary.

    `on_progress` receives (level, completed_batches, total_batches).
    """
    if not summaries:
        return DocumentSummary(
            overview="(empty document)", key_findings="", sections=0
        )

    # Computed once over the original summaries, never re-derived from the
    # model's prose - so nothing is lost however many levels the recursion runs.
    entities = merge_entities(summaries)
    key_facts = merge_key_facts(summaries)
    outline = build_outline(summaries)
    gaps = collect_gaps(summaries)
    degraded = sum(1 for s in summaries if "unvalidated prose" in s.uncertain)

    current = summaries
    level = 0
    while True:
        batches = batch_summaries(current, budget_tokens)
        if on_progress is not None:
            on_progress(level, 0, len(batches))

        results: list[ChunkSummary] = []
        for i, batch in enumerate(batches):
            output = await _reduce_batch(batch, call, level)
            results.append(as_chunk_summary(output, batch))
            if on_progress is not None:
                on_progress(level, i + 1, len(batches))

        if len(batches) == 1:
            final = output  # the single batch's prose is the document's
            break

        current = results
        level += 1
        if level >= MAX_LEVELS:
            logger.warning("reduce hit the level cap; stitching %d partials", len(results))
            final = ReduceOutput(
                overview=results[0].topic,
                key_findings=" ".join(r.findings for r in results),
            )
            break

    return DocumentSummary(
        overview=final.overview,
        key_findings=final.key_findings,
        outline=outline,
        entities=entities,
        key_facts=key_facts,
        gaps=gaps,
        sections=len(summaries),
        degraded_sections=degraded,
    )
