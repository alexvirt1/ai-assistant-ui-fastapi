"""BM25 scoring, with no model and no database.

These exist because dense retrieval put the right chunk at rank 374 for a
question naming a character explicitly. The property that matters is not "good
scores" but "a rare term beats a common one" - that is the whole reason this
channel is here.
"""

import pytest

from app.documents.lexical import score_chunks, tokenize


class TestTokenize:
    def test_lowercases(self):
        assert tokenize("Hello WORLD") == ["hello", "world"]

    def test_keeps_cyrillic(self):
        # The failure that motivated this module was on Russian; a tokenizer
        # that dropped Cyrillic would silently score every chunk zero.
        assert tokenize("Граф Ростов") == ["граф", "ростов"]

    def test_drops_punctuation(self):
        assert tokenize("Кто такой Шиншин?") == ["кто", "такой", "шиншин"]

    def test_keeps_digits(self):
        assert tokenize("capacity 412 people") == ["capacity", "412", "people"]

    def test_empty_text_yields_no_tokens(self):
        assert tokenize("") == []


class TestScoring:
    def test_a_chunk_containing_the_term_outranks_one_that_does_not(self):
        texts = {0: "the cat sat on the mat", 1: "quantum chromodynamics"}
        scores = score_chunks("quantum", texts)
        assert 1 in scores
        assert 0 not in scores

    def test_chunks_without_any_query_term_are_omitted(self):
        # Omitted rather than scored zero, so fusion is not handed thousands of
        # arbitrarily-ordered ties.
        texts = {0: "alpha", 1: "beta", 2: "gamma"}
        assert set(score_chunks("alpha", texts)) == {0}

    def test_a_rare_term_outweighs_a_common_one(self):
        # The property the whole channel rests on. "Ахросимова" appears in one
        # chunk of 2669 and must beat "кто", which appears in hundreds.
        texts = {i: "кто это такой" for i in range(50)}
        texts[7] = "кто такая Ахросимова"
        scores = score_chunks("кто такая Ахросимова", texts)
        assert max(scores, key=scores.get) == 7

    def test_one_rare_hit_beats_many_hits_on_a_ubiquitous_term(self):
        # Isolates IDF specifically: chunk 0 matches a query term eight times,
        # chunk 10 matches a different one once. Counting hits alone would pick
        # chunk 0, which is how a passage full of "кто" outranks the single
        # passage naming the person actually asked about.
        texts = {i: "the common word here" for i in range(50)}
        texts[0] = "the the the the the the the the"
        texts[10] = "Ахросимова"
        scores = score_chunks("the Ахросимова", texts)
        assert max(scores, key=scores.get) == 10
        assert scores[10] > scores[0]

    def test_repeating_a_term_helps_but_saturates(self):
        # BM25's k1 term: ten mentions should not score ten times one mention,
        # or a keyword-stuffed chunk would beat a genuinely relevant one.
        texts = {0: "alpha " * 1, 1: "alpha " * 3, 2: "alpha " * 30}
        scores = score_chunks("alpha", texts)
        assert scores[0] < scores[1] < scores[2]
        assert scores[2] < 3 * scores[1]

    def test_a_short_chunk_beats_a_long_one_with_the_same_hit(self):
        # BM25's b term. One mention in a sentence is a stronger signal than one
        # mention buried in a page.
        texts = {0: "Шиншин", 1: "Шиншин " + "прочий текст " * 200}
        scores = score_chunks("Шиншин", texts)
        assert scores[0] > scores[1]

    def test_matching_more_query_terms_scores_higher(self):
        texts = {0: "именины графини", 1: "именины", 2: "нечто иное"}
        scores = score_chunks("именины графини", texts)
        assert scores[0] > scores[1]

    def test_a_term_in_every_chunk_cannot_push_a_chunk_down(self):
        # The bare IDF form goes negative for a term in more than half the
        # corpus, which would make a common word actively harmful.
        texts = {i: "the alpha" for i in range(10)}
        scores = score_chunks("the", texts)
        assert all(score >= 0 for score in scores.values())

    def test_case_and_punctuation_do_not_prevent_a_match(self):
        texts = {0: "Платон Каратаев умер."}
        assert score_chunks("каратаев", texts)


class TestEdgeCases:
    def test_empty_query_matches_nothing(self):
        assert score_chunks("", {0: "text"}) == {}

    def test_punctuation_only_query_matches_nothing(self):
        assert score_chunks("???", {0: "text"}) == {}

    def test_empty_corpus_returns_nothing(self):
        assert score_chunks("anything", {}) == {}

    def test_corpus_of_empty_strings_does_not_divide_by_zero(self):
        # Average chunk length is zero here; BM25's length normalisation would
        # otherwise raise.
        assert score_chunks("alpha", {0: "", 1: ""}) == {}

    def test_scores_are_stable_between_runs(self):
        texts = {i: f"chunk {i} alpha" for i in range(20)}
        assert score_chunks("alpha", texts) == score_chunks("alpha", texts)

    @pytest.mark.parametrize("k1,b", [(0.0, 0.0), (2.0, 1.0), (1.2, 0.5)])
    def test_parameters_stay_within_sane_bounds(self, k1, b):
        texts = {0: "alpha beta", 1: "alpha " * 20}
        scores = score_chunks("alpha", texts, k1=k1, b=b)
        assert all(score >= 0 for score in scores.values())
