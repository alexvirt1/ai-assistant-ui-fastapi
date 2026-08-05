"""Fusion, neighbour expansion and context budgeting.

No model and no database: the vectors here are hand-written so a channel can be
made deliberately useless, which is exactly the condition that broke the live
system - nomic-embed-text ranking a Russian document essentially at random.
"""

import pytest

from app.documents.retrieval import (
    Match,
    build_context,
    hybrid_search,
    reciprocal_rank_fusion,
    with_neighbours,
)


class TestReciprocalRankFusion:
    def test_a_chunk_ranked_well_by_both_channels_wins(self):
        fused = reciprocal_rank_fusion([[1, 2, 3], [1, 3, 2]])
        assert max(fused, key=fused.get) == 1

    def test_a_chunk_absent_from_a_ranking_contributes_nothing_for_it(self):
        # This is what lets a capped candidate list silence a guessing channel.
        fused = reciprocal_rank_fusion([[1], [2]])
        assert fused[1] == pytest.approx(fused[2])

    def test_earlier_ranks_score_higher(self):
        fused = reciprocal_rank_fusion([[1, 2, 3]])
        assert fused[1] > fused[2] > fused[3]

    def test_one_channel_alone_still_ranks(self):
        fused = reciprocal_rank_fusion([[5, 6], []])
        assert fused[5] > fused[6]

    def test_no_rankings_produce_no_scores(self):
        assert reciprocal_rank_fusion([]) == {}

    def test_agreement_between_channels_is_rewarded(self):
        # The point of fusing at all: a chunk both channels like should beat one
        # that only narrowly leads a single channel.
        fused = reciprocal_rank_fusion([[1, 2], [2]])
        assert max(fused, key=fused.get) == 2

    def test_a_rank_one_hit_beats_a_mid_ranked_hit_from_the_other_channel(self):
        # Ахросимова was rank 1 lexically and nowhere near the vector top; it
        # must still outrank chunks the vector channel merely quite likes.
        fused = reciprocal_rank_fusion([list(range(100, 130)), [7]])
        assert fused[7] > fused[101]

    def test_capping_stops_deep_ranks_burying_a_top_hit(self):
        # The measured failure, reproduced. Uncapped, a distractor the vector
        # channel ranks 100th collects enough from that channel to overtake the
        # true hit it ranks 300th - so fusing full rankings scored *worse* than
        # lexical search alone. Capping makes both deep ranks contribute nothing.
        true_hit, distractor = "A", "B"
        vector = [f"junk{i}" for i in range(400)]
        vector[99] = distractor
        vector[299] = true_hit
        lexical = [true_hit, distractor]

        uncapped = reciprocal_rank_fusion([vector, lexical])
        capped = reciprocal_rank_fusion([vector[:50], lexical])

        assert uncapped[distractor] > uncapped[true_hit]
        assert capped[true_hit] > capped[distractor]


class TestHybridSearch:
    def setup_method(self):
        # Chunk 3 is the answer. The vector channel is built to rank it last,
        # reproducing the live failure where the right chunk sat at rank 374.
        self.texts = {i: f"filler text number {i}" for i in range(10)}
        self.texts[3] = "Марья Дмитриевна Ахросимова приехала на именины"
        self.vectors = [(i, [1.0, 0.0]) for i in range(10)]
        self.vectors[3] = (3, [0.0, 1.0])
        self.query_vector = [1.0, 0.0]

    def test_lexical_channel_rescues_a_chunk_the_vectors_rank_last(self):
        # REGRESSION: with vectors alone this chunk was unreachable, and the
        # model answered from memory instead - inventing characters that appear
        # nowhere in the document.
        matches = hybrid_search(
            "Кто такая Ахросимова?", self.query_vector, self.vectors, self.texts, top_k=3
        )
        assert 3 in [m.index for m in matches]

    def test_semantic_channel_still_works_when_no_term_matches(self):
        # A question sharing no words with the text must still retrieve, or
        # adding BM25 would have traded one failure for another.
        matches = hybrid_search(
            "zzz nothing matches", self.query_vector, self.vectors, self.texts, top_k=3
        )
        assert len(matches) == 3
        assert 3 not in [m.index for m in matches]

    def test_returns_at_most_top_k(self):
        matches = hybrid_search("filler", self.query_vector, self.vectors, self.texts, top_k=4)
        assert len(matches) == 4

    def test_matches_carry_their_text(self):
        matches = hybrid_search("Ахросимова", self.query_vector, self.vectors, self.texts, top_k=1)
        assert "Ахросимова" in matches[0].text

    def test_results_are_ordered_best_first(self):
        matches = hybrid_search("filler", self.query_vector, self.vectors, self.texts, top_k=5)
        assert [m.score for m in matches] == sorted((m.score for m in matches), reverse=True)

    def test_ties_break_on_index_so_repeated_questions_agree(self):
        first = hybrid_search("filler", self.query_vector, self.vectors, self.texts, top_k=5)
        second = hybrid_search("filler", self.query_vector, self.vectors, self.texts, top_k=5)
        assert [m.index for m in first] == [m.index for m in second]

    def test_the_candidate_cap_is_honoured(self):
        # With one candidate per channel only two chunks can be fused at all:
        # the vector channel's best and the lexical channel's best.
        matches = hybrid_search(
            "Ахросимова", self.query_vector, self.vectors, self.texts,
            top_k=10, candidates=1,
        )
        assert sorted(m.index for m in matches) == [0, 3]

    def test_neither_channel_may_contribute_more_than_the_cap(self):
        # Uncapping the lexical side is invisible in a small corpus but is
        # exactly what let deep, weak matches accumulate on the real document
        # and outrank the passage that actually named the person.
        texts = {i: f"alpha filler {i}" for i in range(100)}
        vectors = [(i, [1.0, float(i) / 1000]) for i in range(100)]
        matches = hybrid_search("alpha", [1.0, 0.0], vectors, texts,
                                top_k=100, candidates=5)
        assert len(matches) <= 10

    def test_rejects_a_non_positive_top_k(self):
        with pytest.raises(ValueError, match="top_k must be positive"):
            hybrid_search("q", self.query_vector, self.vectors, self.texts, top_k=0)


class TestNeighbours:
    def test_includes_the_chunks_on_either_side(self):
        # A scene runs across consecutive chunks; the guest list is never all in
        # the one chunk that matched.
        assert with_neighbours([5], set(range(10))) == [4, 5, 6]

    def test_does_not_invent_chunks_outside_the_document(self):
        assert with_neighbours([0], {0, 1}) == [0, 1]

    def test_preserves_match_order_so_the_budget_favours_good_matches(self):
        assert with_neighbours([8, 2], set(range(10))) == [7, 8, 9, 1, 2, 3]

    def test_does_not_repeat_a_chunk_two_matches_share(self):
        assert with_neighbours([4, 5], set(range(10))) == [3, 4, 5, 6]

    def test_radius_zero_returns_the_matches_untouched(self):
        assert with_neighbours([1, 5], set(range(10)), radius=0) == [1, 5]

    def test_a_wider_radius_reaches_further(self):
        assert with_neighbours([5], set(range(10)), radius=2) == [3, 4, 5, 6, 7]

    def test_rejects_a_negative_radius(self):
        with pytest.raises(ValueError, match="radius must not be negative"):
            with_neighbours([1], {1}, radius=-1)


class TestBuildContext:
    def test_labels_each_section_one_based(self):
        context = build_context([Match(0, 1.0, "alpha")], {0: "alpha"}, neighbour_radius=0)
        assert "[Section 1]" in context

    def test_emits_sections_in_document_order(self):
        texts = {i: f"body {i}" for i in range(5)}
        matches = [Match(4, 1.0, texts[4]), Match(1, 0.5, texts[1])]
        context = build_context(matches, texts, neighbour_radius=0)
        assert context.index("[Section 2]") < context.index("[Section 5]")

    def test_includes_neighbours_of_a_match(self):
        texts = {0: "before", 1: "hit", 2: "after"}
        context = build_context([Match(1, 1.0, "hit")], texts)
        assert "before" in context and "after" in context

    def test_budget_is_in_tokens_not_characters(self):
        # A character budget means different things per language: 12k characters
        # is ~3k tokens of English but ~5.7k of Russian, so the same cap let in
        # nearly twice as much of one as the other.
        russian = {0: "тест " * 400}
        english = {0: "test " * 400}
        assert build_context(
            [Match(0, 1.0, russian[0])], russian, max_tokens=50, neighbour_radius=0
        ) != ""
        # Same token budget, and neither overflows it wildly.
        assert len(build_context([Match(0, 1.0, english[0])], english,
                                 max_tokens=50, neighbour_radius=0)) > 0

    def test_drops_sections_that_do_not_fit_the_budget(self):
        texts = {i: "word " * 500 for i in range(5)}
        matches = [Match(i, 1.0, texts[i]) for i in range(5)]
        context = build_context(matches, texts, max_tokens=200, neighbour_radius=0)
        assert context.count("[Section ") < 5

    def test_always_keeps_at_least_one_section(self):
        # An over-budget single chunk must still be sent: returning nothing
        # would make the tool report "no passages matched" on a real hit.
        texts = {0: "word " * 5000}
        context = build_context([Match(0, 1.0, texts[0])], texts, max_tokens=10,
                                neighbour_radius=0)
        assert "[Section 1]" in context

    def test_no_matches_produce_no_context(self):
        assert build_context([], {}) == ""

    def test_works_without_a_texts_mapping(self):
        # Older callers pass matches alone; neighbours are simply unavailable.
        context = build_context([Match(2, 1.0, "body")])
        assert "[Section 3]" in context and "body" in context


class TestDegradedSearch:
    """Searching when the embedding model is unavailable.

    Not hypothetical: the chat model at num_ctx=32768 and the embedder together
    exceeded an 11.75 GB card, embed_query raised cudaMalloc out of memory, the
    tool node died and the user got an empty assistant message.
    """

    def setup_method(self):
        self.texts = {i: f"filler text number {i}" for i in range(10)}
        self.texts[3] = "Марья Дмитриевна Ахросимова приехала на именины"
        self.vectors = [(i, [1.0, 0.0]) for i in range(10)]

    def test_searches_lexically_when_there_is_no_query_vector(self):
        matches = hybrid_search("Ахросимова", None, self.vectors, self.texts, top_k=3)
        assert matches
        assert matches[0].index == 3

    def test_no_vector_still_returns_usable_matches(self):
        matches = hybrid_search("filler", None, self.vectors, self.texts, top_k=4)
        assert len(matches) == 4
        assert all(m.text for m in matches)

    def test_no_vector_and_no_lexical_hit_returns_nothing_rather_than_raising(self):
        # The tool turns an empty result into "no passages matched", which is a
        # far better answer than a blank message.
        assert hybrid_search("zzzz", None, self.vectors, self.texts, top_k=3) == []

    def test_an_empty_vector_is_treated_as_absent(self):
        # A provider returning [] must not reach cosine_similarity, which would
        # raise on the dimension mismatch.
        matches = hybrid_search("Ахросимова", [], self.vectors, self.texts, top_k=3)
        assert matches[0].index == 3
