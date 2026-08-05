"""The map step: one structured summary per chunk.

Sequential by design. Independent chunks look like an obvious parallel fan-out,
but the VM serves one model on one GPU, so concurrent calls would simply queue
at Ollama and gain nothing while making progress reporting and cancellation
harder.

The model client is injected rather than imported, so every branch here -
validation, repair, degradation, caching, cancellation - is testable without a
VM. `app/models/` supplies the real one.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from .summaries import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    REPAIR_SUFFIX,
    ChunkSummary,
    build_user_prompt,
    degraded_summary,
)

logger = logging.getLogger(__name__)


class StructuredCaller(Protocol):
    """Produces a ChunkSummary from messages, or raises if it cannot."""

    async def __call__(self, messages: list) -> ChunkSummary: ...


class TextCaller(Protocol):
    """Plain text completion, used only for the degraded path."""

    async def __call__(self, messages: list) -> str: ...


@dataclass(frozen=True)
class MappedChunk:
    index: int
    summary: ChunkSummary
    degraded: bool
    cached: bool


@dataclass
class MapProgress:
    document_id: str
    total: int
    completed: int
    degraded: int
    cached: int

    @property
    def fraction(self) -> float:
        return self.completed / self.total if self.total else 1.0


async def summarize_chunk(
    text: str,
    index: int,
    total: int,
    structured: StructuredCaller,
    plain: TextCaller | None = None,
) -> tuple[ChunkSummary, bool]:
    """Summarise one chunk. Returns the summary and whether it is degraded.

    Three attempts in decreasing order of strictness: structured, structured
    with an explicit JSON reminder, then unvalidated prose. A small model will
    occasionally fail a schema; losing the chunk would be far worse than
    keeping unstructured text and saying so.
    """
    base = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(build_user_prompt(text, index, total)),
    ]

    try:
        return await structured(base), False
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - any failure earns a retry
        logger.warning("chunk %d: structured output failed (%s); retrying", index, exc)

    repair = [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(build_user_prompt(text, index, total) + REPAIR_SUFFIX),
    ]
    try:
        return await structured(repair), False
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("chunk %d: repair attempt failed (%s); degrading", index, exc)

    raw = ""
    if plain is not None:
        try:
            raw = await plain(base)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("chunk %d: plain completion failed too (%s)", index, exc)

    return degraded_summary(index, total, raw), True


async def map_document(
    document_id: str,
    chunks: list[str],
    structured: StructuredCaller,
    plain: TextCaller | None = None,
    *,
    model_name: str = "unknown",
    load_cached: Callable[[str, int, str, str], Awaitable[ChunkSummary | None]]
    | None = None,
    save_summary: Callable[..., Awaitable[None]] | None = None,
    on_progress: Callable[[MapProgress], None] | None = None,
) -> list[MappedChunk]:
    """Summarise every chunk, reusing cached results.

    Caching is not an optimisation here, it is what makes an 80-minute job
    survivable: a crash, a restart, or simply asking a second question must not
    re-run work already paid for. Cache entries are keyed by model and prompt
    version so changing either invalidates cleanly.
    """
    total = len(chunks)
    progress = MapProgress(document_id, total, 0, 0, 0)
    results: list[MappedChunk] = []

    for index, text in enumerate(chunks):
        cached: ChunkSummary | None = None
        if load_cached is not None:
            cached = await load_cached(document_id, index, model_name, PROMPT_VERSION)

        if cached is not None:
            results.append(MappedChunk(index, cached, degraded=False, cached=True))
            progress.cached += 1
        else:
            summary, degraded = await summarize_chunk(
                text, index, total, structured, plain
            )
            if save_summary is not None:
                # Persisted per chunk, not batched at the end: a job killed at
                # chunk 60 must keep the first 59.
                await save_summary(
                    document_id=document_id,
                    index=index,
                    model_name=model_name,
                    prompt_version=PROMPT_VERSION,
                    summary=summary,
                    degraded=degraded,
                )
            results.append(MappedChunk(index, summary, degraded, cached=False))
            if degraded:
                progress.degraded += 1

        progress.completed += 1
        if on_progress is not None:
            on_progress(progress)

    return results
