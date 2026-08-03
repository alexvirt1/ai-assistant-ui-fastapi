"""Long-running summarisation jobs.

A 78-minute job cannot be an HTTP request: the Next.js proxy, uvicorn and any
intermediary would all have opinions about a connection held open that long. So
starting a job returns immediately with an id, and progress is polled.

Job state is deliberately in-memory. The expensive artefacts - per-chunk
summaries and the final document summary - live in Postgres, so a server
restart loses the *tracking* but not the *work*: restarting the job replays it
from cache in seconds. Persisting job rows as well would add schema and
lifecycle for very little gain.

Map and reduce are injected, so the whole lifecycle is testable without a VM.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from .reduce import DocumentSummary

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Phase(str, Enum):
    MAP = "map"
    REDUCE = "reduce"
    DONE = "done"


@dataclass
class Job:
    id: str
    document_id: str
    status: JobStatus = JobStatus.RUNNING
    phase: Phase = Phase.MAP
    completed: int = 0
    total: int = 0
    degraded: int = 0
    cached: int = 0
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    result: DocumentSummary | None = None
    error: str | None = None

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started_at

    @property
    def fraction(self) -> float:
        if self.status is JobStatus.COMPLETED:
            return 1.0
        return self.completed / self.total if self.total else 0.0

    def eta_seconds(self) -> float | None:
        """Projected time remaining, from observed rate rather than the estimate.

        Once a few chunks are done the measured rate beats the pre-flight guess,
        because it accounts for cache hits and this document's actual content.
        """
        if self.completed == 0 or self.status is not JobStatus.RUNNING:
            return None
        rate = self.elapsed_seconds / self.completed
        return max(0.0, rate * (self.total - self.completed))

    def describe(self) -> str:
        if self.status is JobStatus.RUNNING:
            eta = self.eta_seconds()
            suffix = f", ~{eta / 60:.0f} min remaining" if eta and eta > 90 else ""
            return f"{self.phase.value}: {self.completed}/{self.total}{suffix}"
        if self.status is JobStatus.COMPLETED:
            return f"done in {self.elapsed_seconds / 60:.1f} min"
        if self.status is JobStatus.CANCELLED:
            return f"cancelled after {self.completed}/{self.total} sections"
        return f"failed: {self.error}"


class JobRegistry:
    """Tracks running jobs and their asyncio tasks."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def for_document(self, document_id: str) -> Job | None:
        """The active job for a document, if any.

        Used to refuse duplicate starts: two concurrent jobs over one document
        would compete for the same single-model VM and double an 80-minute wait
        for no benefit.
        """
        for job in self._jobs.values():
            if job.document_id == document_id and job.status is JobStatus.RUNNING:
                return job
        return None

    def start(
        self,
        document_id: str,
        run: Callable[[Job], Awaitable[DocumentSummary]],
    ) -> Job:
        existing = self.for_document(document_id)
        if existing is not None:
            return existing

        job = Job(id=str(uuid.uuid4()), document_id=document_id)
        self._jobs[job.id] = job

        async def wrapper() -> None:
            try:
                job.result = await run(job)
                job.status = JobStatus.COMPLETED
                job.phase = Phase.DONE
            except asyncio.CancelledError:
                job.status = JobStatus.CANCELLED
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced on the job, not lost
                logger.exception("summary job %s failed", job.id)
                job.status = JobStatus.FAILED
                job.error = f"{type(exc).__name__}: {exc}"
            finally:
                job.finished_at = time.monotonic()

        self._tasks[job.id] = asyncio.create_task(wrapper())
        return job

    async def cancel(self, job_id: str) -> bool:
        """Stop a running job. Work already cached is kept."""
        task = self._tasks.get(job_id)
        job = self._jobs.get(job_id)
        if task is None or job is None or job.status is not JobStatus.RUNNING:
            return False

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        job.status = JobStatus.CANCELLED
        job.finished_at = job.finished_at or time.monotonic()
        return True


registry = JobRegistry()


async def run_summary(
    job: Job,
    chunks: list[str],
    map_fn: Callable[..., Awaitable[list[Any]]],
    reduce_fn: Callable[..., Awaitable[DocumentSummary]],
) -> DocumentSummary:
    """Map then reduce, keeping the job's progress current throughout."""
    job.phase = Phase.MAP
    job.total = len(chunks)

    def on_map_progress(progress) -> None:
        job.completed = progress.completed
        job.degraded = progress.degraded
        job.cached = progress.cached

    mapped = await map_fn(chunks=chunks, on_progress=on_map_progress)

    job.phase = Phase.REDUCE
    # Reduce is one or two calls against many chunks; showing it as a separate
    # short phase is more honest than pretending the bar is still at 100%.
    job.completed = 0
    job.total = 1

    def on_reduce_progress(level: int, done: int, total: int) -> None:
        job.total = max(total, 1)
        job.completed = done

    summary = await reduce_fn(
        summaries=[m.summary for m in mapped], on_progress=on_reduce_progress
    )
    job.completed = job.total
    return summary
