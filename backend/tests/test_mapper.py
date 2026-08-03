"""Map step: validation, repair, degradation, caching and progress.

Every model call is injected, so these run offline. The branches that matter -
what happens when a small model returns malformed output over 87 attempts - are
exactly the ones a live test would exercise least reliably.
"""

import asyncio

import pytest

from app.documents.mapper import MapProgress, map_document, summarize_chunk
from app.documents.summaries import PROMPT_VERSION, ChunkSummary


def good(topic="A section", findings="It says things.") -> ChunkSummary:
    return ChunkSummary(topic=topic, findings=findings, entities=["Acme"])


def structured_returning(*results):
    """A caller yielding the given results in order; exceptions are raised."""
    calls = {"n": 0}

    async def caller(messages):
        index = min(calls["n"], len(results) - 1)
        calls["n"] += 1
        outcome = results[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    caller.calls = calls  # type: ignore[attr-defined]
    return caller


async def plain_returning(text):
    async def caller(messages):
        return text

    return caller


class TestSummarizeChunk:
    async def test_returns_structured_output_on_first_try(self):
        caller = structured_returning(good())
        summary, degraded = await summarize_chunk("text", 0, 3, caller)
        assert degraded is False
        assert summary.topic == "A section"
        assert caller.calls["n"] == 1

    async def test_retries_once_before_degrading(self):
        caller = structured_returning(ValueError("bad json"), good("Second try"))
        summary, degraded = await summarize_chunk("text", 0, 3, caller)
        assert degraded is False
        assert summary.topic == "Second try"
        assert caller.calls["n"] == 2, "should have retried exactly once"

    async def test_degrades_to_prose_after_two_failures(self):
        # A small model will fail a schema occasionally; over 87 chunks that is
        # near-certain, and the job must not die on chunk 61.
        caller = structured_returning(ValueError("no"), ValueError("still no"))
        plain = await plain_returning("The section discusses warehouse logistics.")
        summary, degraded = await summarize_chunk("text", 60, 87, caller, plain)

        assert degraded is True
        assert "warehouse logistics" in summary.findings
        assert "section 61 of 87" in summary.uncertain

    async def test_degrades_even_when_plain_call_also_fails(self):
        async def failing_plain(messages):
            raise RuntimeError("model down")

        caller = structured_returning(ValueError("no"), ValueError("no"))
        summary, degraded = await summarize_chunk("t", 0, 1, caller, failing_plain)
        assert degraded is True
        assert summary.findings  # never empty - reduce must have something

    async def test_cancellation_is_not_swallowed(self):
        # A cancelled 80-minute job must actually stop, not get mistaken for a
        # schema failure and retried.
        async def cancelling(messages):
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await summarize_chunk("t", 0, 1, cancelling)


class TestMapDocument:
    async def test_summarises_every_chunk(self):
        caller = structured_returning(good())
        results = await map_document("doc", ["a", "b", "c"], caller)
        assert [r.index for r in results] == [0, 1, 2]
        assert all(not r.cached for r in results)

    async def test_uses_cached_summaries_and_skips_the_model(self):
        calls = {"n": 0}

        async def caller(messages):
            calls["n"] += 1
            return good()

        async def load_cached(doc, idx, model, version):
            assert version == PROMPT_VERSION
            return good("cached") if idx == 0 else None

        results = await map_document(
            "doc", ["a", "b"], caller, load_cached=load_cached
        )
        assert results[0].cached is True
        assert results[1].cached is False
        assert calls["n"] == 1, "cached chunk must not call the model"

    async def test_saves_each_summary_as_it_completes(self):
        saved = []

        async def save(**kwargs):
            saved.append(kwargs["index"])

        await map_document("doc", ["a", "b", "c"], structured_returning(good()),
                           save_summary=save)
        # Persisted per chunk, not batched: a job killed at chunk 2 keeps 0 and 1.
        assert saved == [0, 1, 2]

    async def test_cache_key_includes_model_and_prompt_version(self):
        seen = []

        async def load_cached(doc, idx, model, version):
            seen.append((model, version))
            return None

        await map_document(
            "doc", ["a"], structured_returning(good()),
            model_name="qwen3:8b", load_cached=load_cached,
        )
        assert seen == [("qwen3:8b", PROMPT_VERSION)]

    async def test_progress_is_reported_for_every_chunk(self):
        updates: list[MapProgress] = []
        await map_document(
            "doc", ["a", "b", "c", "d"], structured_returning(good()),
            on_progress=lambda p: updates.append(
                MapProgress(p.document_id, p.total, p.completed, p.degraded, p.cached)
            ),
        )
        assert [u.completed for u in updates] == [1, 2, 3, 4]
        assert updates[-1].fraction == 1.0

    async def test_progress_counts_degraded_chunks(self):
        caller = structured_returning(ValueError("x"), ValueError("x"))
        plain = await plain_returning("prose")
        final: list[MapProgress] = []
        await map_document("doc", ["a"], caller, plain,
                           on_progress=lambda p: final.append(p))
        assert final[-1].degraded == 1

    async def test_empty_document_does_nothing(self):
        results = await map_document("doc", [], structured_returning(good()))
        assert results == []
