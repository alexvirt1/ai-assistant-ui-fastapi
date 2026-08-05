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


class TestScriptAwareEstimation:
    """The estimator assumed 4 characters per token for every script.

    Measured against cl100k on the 3.2 MB Russian text of War and Peace it was
    off by 1.94x, so "400-token" retrieval chunks were really ~770 tokens and the
    history trimmer kept nearly double its budget.
    """

    def test_ascii_is_unchanged(self):
        # English chunking, scope estimates and trimming must behave exactly as
        # before; only text the old estimator was wrong about may differ.
        from langchain_core.messages import HumanMessage
        from langchain_core.messages.utils import count_tokens_approximately

        from app.documents.chunker import _MESSAGE_OVERHEAD

        text = "The quick brown fox jumps over the lazy dog. " * 50
        expected = count_tokens_approximately([HumanMessage(text)]) - _MESSAGE_OVERHEAD
        assert estimate_tokens(text) == expected

    def test_cyrillic_counts_far_more_than_four_characters_per_token(self):
        russian = "Граф Илья Андреич Ростов приехал на именины. " * 50
        assert estimate_tokens(russian) > len(russian) / 4 * 1.5

    def test_cyrillic_estimate_lands_near_the_measured_ratio(self):
        # ~2.08 characters per token measured; the estimator errs high on
        # purpose, so accept 1.6-2.4 characters per token.
        russian = "Пьер Безухов вошёл в гостиную и оглядел собравшихся гостей. " * 200
        ratio = len(russian) / estimate_tokens(russian)
        assert 1.6 <= ratio <= 2.4

    def test_empty_text_is_zero(self):
        assert estimate_tokens("") == 0

    def test_mixed_scripts_fall_between_the_two_rates(self):
        mixed = "Hello Граф " * 100
        ascii_only = "Hello hello " * 100
        assert estimate_tokens(ascii_only) < estimate_tokens(mixed)

    def test_a_cyrillic_chunk_respects_its_budget(self):
        # The practical consequence: chunk_text must not hand back chunks that
        # are really twice the requested size.
        russian = "Наташа Ростова засмеялась и убежала в залу. " * 400
        chunks = chunk_text(russian, max_tokens=400, overlap_tokens=60)
        assert chunks
        assert all(c.tokens <= 400 for c in chunks)
        # And the real size is in the right neighbourhood, not double.
        assert all(len(c.text) / 2.08 <= 400 * 1.35 for c in chunks)


class TestMessageTokenCounter:
    def test_counts_a_plain_string_message(self):
        from langchain_core.messages import HumanMessage

        from app.documents.chunker import count_message_tokens

        assert count_message_tokens([HumanMessage("hello world")]) > 0

    def test_charges_more_for_cyrillic_than_the_old_counter(self):
        # The trimmer's budget was nearly double what it believed on Russian.
        from langchain_core.messages import HumanMessage
        from langchain_core.messages.utils import count_tokens_approximately

        from app.documents.chunker import count_message_tokens

        message = [HumanMessage("Здравствуйте, это проверка счётчика токенов. " * 20)]
        assert count_message_tokens(message) > count_tokens_approximately(message) * 1.5

    def test_handles_multimodal_content_parts(self):
        from langchain_core.messages import HumanMessage

        from app.documents.chunker import count_message_tokens

        message = [HumanMessage(content=[{"type": "text", "text": "hello there"}])]
        assert count_message_tokens(message) > 0

    def test_totals_across_messages(self):
        from langchain_core.messages import AIMessage, HumanMessage

        from app.documents.chunker import count_message_tokens

        one = count_message_tokens([HumanMessage("alpha beta gamma")])
        two = count_message_tokens(
            [HumanMessage("alpha beta gamma"), AIMessage("alpha beta gamma")]
        )
        assert two > one

    def test_never_undercounts_ascii_relative_to_the_counter_it_replaced(self):
        # The budget exists to prevent context overflow, so the replacement must
        # err high, never low. Tool-call messages were the gap: their content is
        # often empty while the call itself is real tokens.
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
        from langchain_core.messages.utils import count_tokens_approximately

        from app.documents.chunker import count_message_tokens

        history = [
            HumanMessage("please search for the weather " * 5),
            AIMessage(
                "",
                tool_calls=[{"name": "web_search", "args": {"query": "weather today"},
                             "id": "call1", "type": "tool_call"}],
            ),
            ToolMessage(content="sunny " * 30, tool_call_id="call1"),
            AIMessage("It is sunny. " * 10),
        ]
        assert count_message_tokens(history) >= count_tokens_approximately(history)
