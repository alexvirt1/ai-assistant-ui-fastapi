"""Job lifecycle: progress, completion, failure, cancellation.

Map and reduce are injected, so an 80-minute pipeline is exercised in
milliseconds and the branches that only occur on long jobs - cancellation
mid-flight, duplicate starts - are actually reachable.
"""

import asyncio

import pytest

from app.documents.jobs import JobRegistry, JobStatus, Phase, run_summary
from app.documents.reduce import DocumentSummary
from app.documents.summaries import ChunkSummary


def doc_summary(overview="ov") -> DocumentSummary:
    return DocumentSummary(overview=overview, key_findings="kf", sections=3)


class Mapped:
    def __init__(self, summary):
        self.summary = summary


class Progress:
    def __init__(self, completed, degraded=0, cached=0):
        self.completed, self.degraded, self.cached = completed, degraded, cached


async def settle():
    """Let the background task run."""
    for _ in range(20):
        await asyncio.sleep(0)


class TestRegistry:
    async def test_job_completes_and_carries_its_result(self):
        registry = JobRegistry()

        async def run(job):
            job.total, job.completed = 2, 2
            return doc_summary("finished")

        job = registry.start("doc", run)
        await settle()

        assert job.status is JobStatus.COMPLETED
        assert job.phase is Phase.DONE
        assert job.result.overview == "finished"
        assert job.fraction == 1.0

    async def test_failure_is_recorded_not_swallowed(self):
        registry = JobRegistry()

        async def run(job):
            raise ValueError("model exploded")

        job = registry.start("doc", run)
        await settle()

        assert job.status is JobStatus.FAILED
        assert "model exploded" in job.error
        assert job.finished_at is not None

    async def test_starting_twice_returns_the_running_job(self):
        # Two concurrent jobs over one document would compete for the same
        # single-model VM and double an 80-minute wait for nothing.
        registry = JobRegistry()
        started = []

        async def run(job):
            started.append(job.id)
            await asyncio.sleep(0.5)
            return doc_summary()

        first = registry.start("doc", run)
        await asyncio.sleep(0)
        second = registry.start("doc", run)

        assert first.id == second.id
        await registry.cancel(first.id)
        assert len(started) == 1

    async def test_a_finished_job_does_not_block_a_new_one(self):
        registry = JobRegistry()

        async def run(job):
            return doc_summary()

        first = registry.start("doc", run)
        await settle()
        second = registry.start("doc", run)
        assert second.id != first.id

    async def test_different_documents_run_independently(self):
        registry = JobRegistry()

        async def run(job):
            await asyncio.sleep(0.2)
            return doc_summary()

        a = registry.start("doc-a", run)
        b = registry.start("doc-b", run)
        assert a.id != b.id
        await registry.cancel(a.id)
        await registry.cancel(b.id)


class TestCancellation:
    async def test_cancelling_stops_the_work(self):
        registry = JobRegistry()
        progressed = []

        async def run(job):
            for i in range(100):
                progressed.append(i)
                await asyncio.sleep(0.01)
            return doc_summary()

        job = registry.start("doc", run)
        await asyncio.sleep(0.05)
        assert await registry.cancel(job.id) is True

        count_at_cancel = len(progressed)
        await asyncio.sleep(0.05)

        assert job.status is JobStatus.CANCELLED
        assert len(progressed) == count_at_cancel, "work continued after cancel"

    async def test_cancelling_a_finished_job_is_refused(self):
        registry = JobRegistry()

        async def run(job):
            return doc_summary()

        job = registry.start("doc", run)
        await settle()
        assert await registry.cancel(job.id) is False

    async def test_cancelling_an_unknown_job_is_refused(self):
        assert await JobRegistry().cancel("nope") is False


class TestRunSummary:
    async def test_tracks_both_phases(self):
        job_holder = {}
        registry = JobRegistry()

        async def map_fn(chunks, on_progress):
            # degraded is cumulative in the real MapProgress, so the fake counts
            # up rather than reporting per-chunk.
            degraded = 0
            for i in range(len(chunks)):
                if i == 0:
                    degraded += 1
                on_progress(Progress(i + 1, degraded=degraded))
            return [Mapped(ChunkSummary(topic="t", findings="f")) for _ in chunks]

        async def reduce_fn(summaries, on_progress):
            on_progress(0, 1, 1)
            return doc_summary()

        async def run(job):
            job_holder["job"] = job
            return await run_summary(job, ["a", "b", "c"], map_fn, reduce_fn)

        job = registry.start("doc", run)
        await settle()

        assert job.status is JobStatus.COMPLETED
        assert job.result is not None
        assert job.degraded == 1

    async def test_map_progress_reaches_the_job(self):
        seen = []

        async def map_fn(chunks, on_progress):
            for i in range(len(chunks)):
                on_progress(Progress(i + 1, cached=i))
                seen.append(job.completed)
            return [Mapped(ChunkSummary(topic="t", findings="f")) for _ in chunks]

        async def reduce_fn(summaries, on_progress):
            return doc_summary()

        from app.documents.jobs import Job

        job = Job(id="j", document_id="d")
        await run_summary(job, ["a", "b"], map_fn, reduce_fn)
        assert seen == [1, 2]


def test_eta_is_absent_before_any_progress():
    from app.documents.jobs import Job

    assert Job(id="j", document_id="d").eta_seconds() is None


def test_eta_projects_from_observed_rate():
    from app.documents.jobs import Job

    job = Job(id="j", document_id="d", total=10, completed=2)
    job.started_at -= 20  # 20s for 2 chunks
    eta = job.eta_seconds()
    assert eta is not None and 60 < eta < 100, f"expected ~80s, got {eta}"


def test_description_mentions_the_phase():
    from app.documents.jobs import Job

    job = Job(id="j", document_id="d", total=87, completed=12)
    assert "map" in job.describe() and "12/87" in job.describe()
