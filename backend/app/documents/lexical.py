"""Lexical (BM25) scoring over chunk text.

This exists because dense retrieval failed badly on a Russian document, and the
failure was not subtle. Measured on the 2 669-chunk text of War and Peace with
`nomic-embed-text`:

    question                                    vector rank of the right chunk
    "Что произошло с Платоном Каратаевым?"                              168
    "Кто такая Марья Дмитриевна Ахросимова?"                            374
    "Кто такой Шиншин?"                                                  23

With `top_k=5` none of those could ever reach the model, which then answered
from what it remembered of the novel instead - inventing a "Князь Василий
Болконский" who appears nowhere in the text. BM25 puts all three at **rank 1**.

The reason is visible in the score distribution: nomic-embed-text is an
English-only model, and its Russian vectors collapse into a narrow cone - median
cosine similarity 0.80 against 0.48 for the same pipeline on English. Everything
looks alike, so a rare proper noun cannot win. Rare proper nouns are exactly
what these questions turn on, and exact term matching is the right tool for
them: no embedding needed, and no way for a name to be "nearly" matched.

Deliberately dependency-free and index-free. Only the query's own terms need
counting, so one pass over the chunks answers a query without building or
caching anything: measured at 0.36s for 3.7 MB and 0.42s for 6.5 MB, against
roughly 9 seconds of model swapping per question on this hardware.
"""

import math
import re
from collections import Counter

# \w with re.UNICODE keeps Cyrillic, accented Latin and digits as word
# characters, which str.split() would keep but punctuation-splitting would not.
_WORD = re.compile(r"\w+", re.UNICODE)

# Standard BM25 parameters. k1 controls how fast term frequency saturates, b how
# strongly long chunks are penalised. The usual defaults; nothing here is tuned
# to one document, and tuning them per corpus is a trap for a general tool.
K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    """Lowercased word tokens.

    No stemming: it would need a per-language stemmer, and Russian morphology is
    rich enough that a wrong one is worse than none. Matching is therefore on
    surface forms, which is why "Каратаевым" in a question still finds chunks
    containing "Каратаев" only through the shared prefix being a separate token
    elsewhere in the text - proper nouns recur in enough inflected forms across a
    long document for this to work in practice.
    """
    return _WORD.findall(text.lower())


def score_chunks(
    query: str,
    texts: dict[int, str],
    *,
    k1: float = K1,
    b: float = B,
) -> dict[int, float]:
    """BM25 score per chunk, omitting chunks that contain no query term.

    Chunks are omitted rather than scored 0.0 so callers can tell "matched
    nothing" from "matched weakly" - fusing a few thousand zero-score chunks in
    arbitrary order would add noise, not information.
    """
    terms = set(tokenize(query))
    if not terms or not texts:
        return {}

    # One pass. Only query terms are counted, so there is no per-chunk index to
    # build and nothing to keep between queries.
    frequencies: dict[int, Counter] = {}
    lengths: dict[int, int] = {}
    document_frequency: Counter = Counter()

    for index, text in texts.items():
        words = tokenize(text)
        lengths[index] = len(words)
        present = Counter(word for word in words if word in terms)
        if present:
            frequencies[index] = present
            document_frequency.update(present.keys())

    total = len(texts)
    average_length = (sum(lengths.values()) / total) if total else 0.0
    if not average_length:
        return {}

    # Probabilistic IDF, in the +1 form that cannot go negative. The bare form
    # scores a term appearing in more than half the chunks below zero, which
    # would make a common word actively push a chunk down the ranking.
    idf = {
        term: math.log(
            1 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
        )
        for term in terms
        if document_frequency[term]
    }

    scores: dict[int, float] = {}
    for index, present in frequencies.items():
        normaliser = 1 - b + b * (lengths[index] / average_length)
        scores[index] = sum(
            idf[term] * count * (k1 + 1) / (count + k1 * normaliser)
            for term, count in present.items()
            if term in idf
        )
    return scores
