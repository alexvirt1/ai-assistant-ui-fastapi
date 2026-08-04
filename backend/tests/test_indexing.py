"""Indexing exactly once, with no model and no database.

The bug this guards against is not hypothetical: uploading a document fires an
index request, and asking a question a few seconds later triggers the lazy index
inside search_document. Both used to see an empty index and embed the whole
document, which for a 5 MB file is a minute of duplicated work on a machine that
can only run one model at a time.
"""

import asyncio
import uuid

import pytest

from app.documents import indexing


class FakeStore:
    """Just enough of store.py to observe how often embedding happens."""

    def __init__(self, text="alpha beta gamma", embed_delay=0.05):
        self.text = text
        self.embed_delay = embed_delay
        self.rows: dict[str, list] = {}
        self.embed_calls = 0
        self.save_calls = 0

    async def load_retrieval_chunks(self, document_id, embed_model):
        rows = self.rows.get(f"{document_id}:{embed_model}")
        if not rows:
            return [], []
        return [(i, v) for i, (_, v) in enumerate(rows)], [t for t, _ in rows]

    async def get_chunks(self, document_uuid):
        return [self.text]

    async def embed_chunks(self, pieces, embedder):
        self.embed_calls += 1
        # Long enough that a second caller arrives mid-flight, which is the
        # window the lock has to cover.
        await asyncio.sleep(self.embed_delay)
        return [[float(len(p))] for p in pieces]

    async def save_retrieval_chunks(self, document_id, embed_model, pieces, vectors):
        self.save_calls += 1
        self.rows[f"{document_id}:{embed_model}"] = list(zip(pieces, vectors))

    def install(self, monkeypatch, split=None):
        monkeypatch.setattr(indexing, "load_retrieval_chunks", self.load_retrieval_chunks)
        monkeypatch.setattr(indexing, "get_chunks", self.get_chunks)
        monkeypatch.setattr(indexing, "embed_chunks", self.embed_chunks)
        monkeypatch.setattr(indexing, "save_retrieval_chunks", self.save_retrieval_chunks)
        monkeypatch.setattr(indexing, "split_for_retrieval", split or (lambda t: t.split()))


@pytest.fixture(autouse=True)
def clear_locks():
    # The lock table lives for the process; leaking one test's lock into the
    # next would mask exactly the failure these tests look for.
    indexing._locks.clear()
    yield
    indexing._locks.clear()


@pytest.fixture
def document_id():
    return str(uuid.uuid4())


class TestFirstIndex:
    @pytest.mark.asyncio
    async def test_embeds_and_reports_not_reused(self, monkeypatch, document_id):
        store = FakeStore()
        store.install(monkeypatch)

        count, reused = await indexing.ensure_indexed(document_id, object(), "nomic")

        assert (count, reused) == (3, False)
        assert store.embed_calls == 1

    @pytest.mark.asyncio
    async def test_empty_document_indexes_nothing_rather_than_saving_empty_rows(
        self, monkeypatch, document_id
    ):
        store = FakeStore(text="")
        store.install(monkeypatch)

        count, reused = await indexing.ensure_indexed(document_id, object(), "nomic")

        assert (count, reused) == (0, False)
        assert store.save_calls == 0


class TestReuse:
    @pytest.mark.asyncio
    async def test_second_call_reuses_without_embedding(self, monkeypatch, document_id):
        store = FakeStore()
        store.install(monkeypatch)

        await indexing.ensure_indexed(document_id, object(), "nomic")
        count, reused = await indexing.ensure_indexed(document_id, object(), "nomic")

        assert (count, reused) == (3, True)
        assert store.embed_calls == 1

    @pytest.mark.asyncio
    async def test_reuse_path_takes_no_lock(self, monkeypatch, document_id):
        # An indexed document is the common case; making it wait behind a lock
        # would serialise every search in the process.
        store = FakeStore()
        store.install(monkeypatch)
        await indexing.ensure_indexed(document_id, object(), "nomic")

        lock = indexing._lock_for(f"{document_id}:nomic")
        await lock.acquire()
        try:
            reused = await asyncio.wait_for(
                indexing.ensure_indexed(document_id, object(), "nomic"), timeout=1.0
            )
        finally:
            lock.release()

        assert reused == (3, True)

    @pytest.mark.asyncio
    async def test_a_different_embedding_model_indexes_separately(
        self, monkeypatch, document_id
    ):
        # Vectors from two models are not comparable, so switching models must
        # re-embed rather than reuse rows keyed to the old one.
        store = FakeStore()
        store.install(monkeypatch)

        await indexing.ensure_indexed(document_id, object(), "nomic")
        _, reused = await indexing.ensure_indexed(document_id, object(), "mxbai")

        assert reused is False
        assert store.embed_calls == 2


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_two_concurrent_callers_embed_once(self, monkeypatch, document_id):
        store = FakeStore()
        store.install(monkeypatch)

        results = await asyncio.gather(
            indexing.ensure_indexed(document_id, object(), "nomic"),
            indexing.ensure_indexed(document_id, object(), "nomic"),
        )

        assert store.embed_calls == 1
        assert store.save_calls == 1
        # Both callers get a usable answer; only one did the work.
        assert [count for count, _ in results] == [3, 3]
        assert sorted(reused for _, reused in results) == [False, True]

    @pytest.mark.asyncio
    async def test_a_crowd_of_callers_still_embeds_once(self, monkeypatch, document_id):
        store = FakeStore()
        store.install(monkeypatch)

        results = await asyncio.gather(
            *(indexing.ensure_indexed(document_id, object(), "nomic") for _ in range(8))
        )

        assert store.embed_calls == 1
        assert all(count == 3 for count, _ in results)

    @pytest.mark.asyncio
    async def test_different_documents_index_in_parallel(self, monkeypatch):
        # The lock is per document; one slow upload must not block another.
        store = FakeStore(embed_delay=0.3)
        store.install(monkeypatch)
        ids = [str(uuid.uuid4()) for _ in range(3)]

        loop = asyncio.get_running_loop()
        started = loop.time()
        await asyncio.gather(
            *(indexing.ensure_indexed(d, object(), "nomic") for d in ids)
        )
        elapsed = loop.time() - started

        assert store.embed_calls == 3
        # Serialised would be ~0.9s; overlapping stays near one delay.
        assert elapsed < 0.6

    @pytest.mark.asyncio
    async def test_a_failed_index_does_not_wedge_later_callers(
        self, monkeypatch, document_id
    ):
        # If embedding raises while holding the lock, the next question on that
        # document must be able to retry rather than hang forever.
        store = FakeStore()
        store.install(monkeypatch)
        boom = True

        async def sometimes_fails(pieces, embedder):
            if boom:
                raise RuntimeError("ollama is down")
            return await store.embed_chunks(pieces, embedder)

        monkeypatch.setattr(indexing, "embed_chunks", sometimes_fails)
        with pytest.raises(RuntimeError):
            await indexing.ensure_indexed(document_id, object(), "nomic")

        boom = False
        count, reused = await asyncio.wait_for(
            indexing.ensure_indexed(document_id, object(), "nomic"), timeout=1.0
        )

        assert (count, reused) == (3, False)
