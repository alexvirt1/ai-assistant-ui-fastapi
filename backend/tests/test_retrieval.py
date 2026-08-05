"""Ranking behaviour, with no model and no database."""

import pytest

from app.documents.chunker import estimate_tokens
from app.documents.retrieval import Match, build_context, cosine_similarity, rank_chunks


class TestCosineSimilarity:
    def test_identical_vectors_score_one(self):
        assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_score_minus_one(self):
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_magnitude_does_not_matter(self):
        # Only direction should count, so a longer chunk is not favoured simply
        # for having a larger vector.
        assert cosine_similarity([1.0, 1.0], [5.0, 5.0]) == pytest.approx(1.0)

    def test_zero_vector_scores_zero_rather_than_dividing_by_zero(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_dimension_mismatch_is_an_error(self):
        # Silently comparing a 768-dim vector with a 384-dim one would return
        # meaningless similarities; better to fail loudly.
        with pytest.raises(ValueError, match="dimension mismatch"):
            cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])


class TestRanking:
    def setup_method(self):
        self.vectors = [
            (0, [1.0, 0.0, 0.0]),
            (1, [0.0, 1.0, 0.0]),
            (2, [0.9, 0.1, 0.0]),
            (3, [0.0, 0.0, 1.0]),
        ]
        self.texts = {i: f"chunk {i}" for i in range(4)}

    def test_returns_the_closest_chunk_first(self):
        matches = rank_chunks([1.0, 0.0, 0.0], self.vectors, self.texts, top_k=2)
        assert [m.index for m in matches] == [0, 2]

    def test_respects_top_k(self):
        assert len(rank_chunks([1.0, 0.0, 0.0], self.vectors, self.texts, top_k=3)) == 3

    def test_carries_the_chunk_text(self):
        matches = rank_chunks([0.0, 1.0, 0.0], self.vectors, self.texts, top_k=1)
        assert matches[0].text == "chunk 1"

    def test_scores_are_attached(self):
        matches = rank_chunks([1.0, 0.0, 0.0], self.vectors, self.texts, top_k=1)
        assert matches[0].score == pytest.approx(1.0)

    def test_min_score_filters_weak_matches(self):
        matches = rank_chunks(
            [1.0, 0.0, 0.0], self.vectors, self.texts, top_k=4, min_score=0.5
        )
        assert [m.index for m in matches] == [0, 2]

    def test_ties_break_on_index_so_results_are_stable(self):
        # The same question asked twice must not reorder its sources.
        vectors = [(5, [1.0, 0.0]), (2, [1.0, 0.0]), (9, [1.0, 0.0])]
        matches = rank_chunks([1.0, 0.0], vectors, {}, top_k=3)
        assert [m.index for m in matches] == [2, 5, 9]

    def test_no_embeddings_yields_nothing(self):
        assert rank_chunks([1.0, 0.0], [], {}, top_k=5) == []

    def test_rejects_a_nonsense_top_k(self):
        with pytest.raises(ValueError):
            rank_chunks([1.0], [(0, [1.0])], {}, top_k=0)


class TestContext:
    def test_labels_sections_so_answers_can_be_checked(self):
        context = build_context([Match(0, 0.9, "alpha"), Match(4, 0.8, "beta")])
        assert "[Section 1]" in context and "[Section 5]" in context
        assert "alpha" in context and "beta" in context

    def test_respects_the_token_budget(self):
        # Budgeted in tokens rather than characters: a character cap admitted
        # nearly twice as much Russian as English for the same number.
        matches = [Match(i, 1.0, "x " * 2500) for i in range(10)]
        context = build_context(matches, max_tokens=3_000, neighbour_radius=0)
        assert estimate_tokens(context) < 3_300

    def test_always_includes_the_best_match_even_if_oversized(self):
        # Returning nothing because the top chunk is large would be worse than
        # a slightly over-budget prompt.
        matches = [Match(0, 1.0, "y " * 25_000)]
        assert build_context(matches, max_tokens=1_000, neighbour_radius=0) != ""

    def test_empty_matches_give_empty_context(self):
        assert build_context([]) == ""
