"""Chunking invariants.

The budget guarantee is the important one: a chunk that overflows the context
window is not gracefully truncated, it derails the map step for that section.
"""

import pytest

from app.documents.chunker import chunk_text, estimate_tokens


def paragraphs(count: int, words_per_paragraph: int = 60) -> str:
    return "\n\n".join(
        f"Paragraph {i}. " + "lorem ipsum dolor sit amet " * (words_per_paragraph // 5)
        for i in range(count)
    )


def test_empty_document_yields_nothing():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_small_document_is_a_single_chunk():
    text = "A short note that comfortably fits."
    chunks = chunk_text(text, max_tokens=1000)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].index == 0


@pytest.mark.parametrize("max_tokens", [200, 500, 1000, 4000])
def test_no_chunk_exceeds_the_budget(max_tokens):
    """The invariant everything else depends on."""
    chunks = chunk_text(paragraphs(200), max_tokens=max_tokens, overlap_tokens=50)
    assert chunks, "expected the document to be chunked"
    for chunk in chunks:
        assert chunk.tokens <= max_tokens, (
            f"chunk {chunk.index} is {chunk.tokens} tokens, over the {max_tokens} budget"
        )


def test_chunks_are_indexed_in_order():
    chunks = chunk_text(paragraphs(120), max_tokens=800, overlap_tokens=80)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_whole_document_is_covered():
    """Every paragraph must appear somewhere - chunking must not drop content."""
    text = paragraphs(60)
    chunks = chunk_text(text, max_tokens=700, overlap_tokens=70)
    joined = "".join(c.text for c in chunks)
    for i in range(60):
        assert f"Paragraph {i}." in joined, f"paragraph {i} was dropped"


def test_consecutive_chunks_overlap():
    """A fact spanning a boundary must survive in at least one chunk.

    Asserted as "the start of each chunk reappears in its predecessor", which
    holds whatever length the carried overlap happens to be after it is trimmed
    to a sentence boundary.
    """
    chunks = chunk_text(paragraphs(80), max_tokens=800, overlap_tokens=200)
    assert len(chunks) > 2
    for previous, following in zip(chunks, chunks[1:]):
        head = following.text[:100]
        assert head in previous.text, (
            f"chunk {following.index} does not overlap its predecessor"
        )


def test_overlap_size_tracks_the_request():
    """More overlap means more repeated text, so nothing is lost at a boundary."""
    text = paragraphs(80)
    small = chunk_text(text, max_tokens=800, overlap_tokens=50)
    large = chunk_text(text, max_tokens=800, overlap_tokens=400)
    # Repetition costs chunks: the same document needs more of them.
    assert len(large) > len(small)


def test_zero_overlap_is_allowed():
    chunks = chunk_text(paragraphs(40), max_tokens=600, overlap_tokens=0)
    assert len(chunks) > 1
    assert all(c.tokens <= 600 for c in chunks)


def test_text_without_separators_is_still_bounded():
    """Pathological input - one enormous "word" - must still be cut."""
    chunks = chunk_text("x" * 60_000, max_tokens=300, overlap_tokens=0)
    assert chunks
    assert all(c.tokens <= 300 for c in chunks)


def test_prefers_paragraph_boundaries():
    text = paragraphs(30, words_per_paragraph=40)
    chunks = chunk_text(text, max_tokens=900, overlap_tokens=0)
    # With generous paragraphs and no overlap, chunks should begin at a
    # paragraph rather than mid-sentence.
    assert sum(c.text.lstrip().startswith("Paragraph") for c in chunks) >= len(chunks) - 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_tokens": 0},
        {"max_tokens": -1},
        {"max_tokens": 100, "overlap_tokens": -1},
        {"max_tokens": 100, "overlap_tokens": 100},
        {"max_tokens": 100, "overlap_tokens": 200},
    ],
)
def test_invalid_parameters_are_rejected(kwargs):
    # Overlap >= max_tokens would never advance, looping forever.
    with pytest.raises(ValueError):
        chunk_text("some text", **kwargs)


def test_estimate_tokens_scales_with_length():
    assert estimate_tokens("") == 0
    short = estimate_tokens("word " * 100)
    long = estimate_tokens("word " * 1000)
    assert 0 < short < long
