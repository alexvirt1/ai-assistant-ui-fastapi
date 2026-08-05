"""Reduce step: deterministic merging, batching, and hierarchical recursion."""

import pytest

from app.documents.reduce import (
    ReduceOutput,
    batch_summaries,
    build_outline,
    collect_gaps,
    merge_entities,
    render_summary,
)
from app.documents.reducer import reduce_document
from app.documents.summaries import ChunkSummary


def summary(topic="T", findings="F", entities=None, uncertain="") -> ChunkSummary:
    return ChunkSummary(
        topic=topic, findings=findings, entities=entities or [], uncertain=uncertain
    )


class TestMergeEntities:
    def test_unions_across_chunks(self):
        merged = merge_entities([summary(entities=["Acme"]), summary(entities=["Zeta"])])
        assert set(merged) == {"Acme", "Zeta"}

    def test_deduplicates_case_insensitively_keeping_first_spelling(self):
        merged = merge_entities(
            [summary(entities=["Marta Chen"]), summary(entities=["marta chen"])]
        )
        assert merged == ["Marta Chen"]

    def test_orders_by_frequency(self):
        merged = merge_entities([
            summary(entities=["Rare"]),
            summary(entities=["Common"]),
            summary(entities=["Common"]),
        ])
        assert merged[0] == "Common"

    def test_does_not_merge_substrings(self):
        # Collapsing these would be a guess, and a wrong merge is worse than a
        # duplicate.
        merged = merge_entities(
            [summary(entities=["Fleetwise"]), summary(entities=["Fleetwise routing system"])]
        )
        assert len(merged) == 2

    def test_ignores_blank_and_whitespace_entities(self):
        assert merge_entities([summary(entities=["", "  ", "Real"])]) == ["Real"]

    def test_normalises_internal_whitespace(self):
        merged = merge_entities(
            [summary(entities=["Acme   Corp"]), summary(entities=["Acme Corp"])]
        )
        assert merged == ["Acme Corp"]

    def test_respects_the_limit(self):
        many = [summary(entities=[f"E{i}"]) for i in range(200)]
        assert len(merge_entities(many, limit=50)) == 50

    def test_nothing_is_lost_from_a_single_mention(self):
        """The property that motivated merging in code rather than by model."""
        summaries = [summary(entities=[f"Name{i}"]) for i in range(60)]
        merged = merge_entities(summaries, limit=200)
        assert len(merged) == 60


class TestOutline:
    def test_preserves_document_order(self):
        outline = build_outline([summary(topic="A"), summary(topic="B")])
        assert outline == ["A", "B"]

    def test_collapses_consecutive_duplicates(self):
        # Overlapping chunks routinely describe the same section twice.
        outline = build_outline(
            [summary(topic="A"), summary(topic="a"), summary(topic="B")]
        )
        assert outline == ["A", "B"]

    def test_keeps_a_topic_that_recurs_later(self):
        outline = build_outline(
            [summary(topic="A"), summary(topic="B"), summary(topic="A")]
        )
        assert outline == ["A", "B", "A"]

    def test_skips_empty_topics(self):
        assert build_outline([summary(topic="  "), summary(topic="A")]) == ["A"]


class TestGaps:
    def test_empty_when_nothing_uncertain(self):
        assert collect_gaps([summary(), summary()]) == ""

    def test_collects_and_deduplicates(self):
        gaps = collect_gaps([summary(uncertain="cut off"), summary(uncertain="cut off")])
        assert gaps == "cut off"

    def test_caps_the_list_but_says_how_many_more(self):
        gaps = collect_gaps([summary(uncertain=f"issue {i}") for i in range(15)])
        assert "and 5 more" in gaps


class TestBatching:
    def test_small_input_is_one_batch(self):
        assert len(batch_summaries([summary() for _ in range(5)], 10_000)) == 1

    def test_splits_when_over_budget(self):
        big = [summary(findings="word " * 400) for _ in range(10)]
        batches = batch_summaries(big, 500)
        assert len(batches) > 1

    def test_no_summary_is_dropped(self):
        big = [summary(topic=f"T{i}", findings="word " * 300) for i in range(20)]
        batches = batch_summaries(big, 400)
        assert sum(len(b) for b in batches) == 20

    def test_an_oversized_summary_still_gets_a_batch(self):
        huge = summary(findings="word " * 5000)
        assert len(batch_summaries([huge], 100)) == 1

    def test_rejects_a_nonsense_budget(self):
        with pytest.raises(ValueError):
            batch_summaries([summary()], 0)


def caller_returning(*outputs):
    calls = {"n": 0}

    async def call(messages):
        index = min(calls["n"], len(outputs) - 1)
        calls["n"] += 1
        result = outputs[index]
        if isinstance(result, Exception):
            raise result
        return result

    call.calls = calls  # type: ignore[attr-defined]
    return call


class TestReduceDocument:
    async def test_empty_input(self):
        result = await reduce_document([], caller_returning(ReduceOutput(overview="", key_findings="")))
        assert result.sections == 0

    async def test_single_pass_for_a_normal_document(self):
        call = caller_returning(ReduceOutput(overview="An overview", key_findings="Findings"))
        result = await reduce_document([summary(topic=f"T{i}") for i in range(20)], call)

        assert call.calls["n"] == 1, "20 small summaries should need one call"
        assert result.overview == "An overview"
        assert result.sections == 20
        assert result.outline[:2] == ["T0", "T1"]

    async def test_entities_survive_hierarchical_reduce(self):
        """The correctness property: a name mentioned once must reach the top."""
        summaries = [
            summary(topic=f"T{i}", findings="word " * 300, entities=[f"Name{i}"])
            for i in range(12)
        ]
        call = caller_returning(ReduceOutput(overview="o", key_findings="k"))
        result = await reduce_document(summaries, call, budget_tokens=400)

        assert call.calls["n"] > 1, "expected more than one batch"
        for i in range(12):
            assert f"Name{i}" in result.entities

    async def test_reports_degraded_sections(self):
        summaries = [
            summary(uncertain="Structured output failed for section 3 of 5; this "
                              "entry is unvalidated prose."),
            summary(),
        ]
        call = caller_returning(ReduceOutput(overview="o", key_findings="k"))
        result = await reduce_document(summaries, call)
        assert result.degraded_sections == 1
        assert "unvalidated prose" in result.gaps

    async def test_a_failed_call_stitches_rather_than_losing_the_map_phase(self):
        # Discarding an hour of map work because one reduce call returned bad
        # JSON would be the worst possible failure here.
        call = caller_returning(ValueError("bad json"))
        result = await reduce_document(
            [summary(findings="alpha"), summary(findings="beta")], call
        )
        assert "alpha" in result.key_findings and "beta" in result.key_findings

    async def test_progress_is_reported(self):
        seen = []
        call = caller_returning(ReduceOutput(overview="o", key_findings="k"))
        await reduce_document(
            [summary() for _ in range(4)], call,
            on_progress=lambda level, done, total: seen.append((level, done, total)),
        )
        assert seen[-1] == (0, 1, 1)


def test_render_summary_includes_the_fields_the_model_needs():
    rendered = render_summary(summary(topic="T", findings="F", entities=["Acme"]))
    assert "T" in rendered and "F" in rendered and "Acme" in rendered


class TestMergeKeyFacts:
    """Facts are extracted and merged in code because summarising loses them.

    An 87-chunk run lost 3 of 4 planted facts during the *map* step: each chunk
    compresses ~130:1, and one sentence in 64 KB does not survive that.
    """

    def test_collects_facts_across_chunks(self):
        from app.documents.reduce import merge_key_facts

        merged = merge_key_facts([
            summary(),
            ChunkSummary(topic="t", findings="f", key_facts=["threshold is 2 percent"]),
            ChunkSummary(topic="t", findings="f", key_facts=["retention is 90 days"]),
        ])
        assert merged == ["threshold is 2 percent", "retention is 90 days"]

    def test_preserves_document_order(self):
        from app.documents.reduce import merge_key_facts

        merged = merge_key_facts([
            ChunkSummary(topic="t", findings="f", key_facts=["first"]),
            ChunkSummary(topic="t", findings="f", key_facts=["second"]),
        ])
        assert merged == ["first", "second"], "facts are positional, not ranked"

    def test_deduplicates_case_insensitively(self):
        from app.documents.reduce import merge_key_facts

        merged = merge_key_facts([
            ChunkSummary(topic="t", findings="f", key_facts=["Limit Is 5"]),
            ChunkSummary(topic="t", findings="f", key_facts=["limit is 5"]),
        ])
        assert merged == ["Limit Is 5"]

    def test_a_fact_stated_once_survives_many_chunks(self):
        from app.documents.reduce import merge_key_facts

        chunks = [summary() for _ in range(86)]
        chunks[3] = ChunkSummary(
            topic="t", findings="f", key_facts=["calibration constant is 8.472"]
        )
        merged = merge_key_facts(chunks)
        assert "calibration constant is 8.472" in merged

    def test_respects_the_limit(self):
        from app.documents.reduce import merge_key_facts

        many = [
            ChunkSummary(topic="t", findings="f", key_facts=[f"fact {i}"])
            for i in range(500)
        ]
        assert len(merge_key_facts(many, limit=100)) == 100


class TestKeyFactsThroughReduce:
    async def test_facts_survive_hierarchical_reduce(self):
        summaries = [
            ChunkSummary(
                topic=f"T{i}", findings="word " * 300, key_facts=[f"value {i} is {i * 7}"]
            )
            for i in range(12)
        ]
        call = caller_returning(ReduceOutput(overview="o", key_findings="k"))
        result = await reduce_document(summaries, call, budget_tokens=400)

        assert call.calls["n"] > 1, "expected multiple batches"
        for i in range(12):
            assert f"value {i} is {i * 7}" in result.key_facts

    async def test_reduce_prompt_shows_the_model_the_facts(self):
        from app.documents.reduce import render_summary

        rendered = render_summary(
            ChunkSummary(topic="t", findings="f", key_facts=["constant is 8.472"])
        )
        assert "8.472" in rendered
