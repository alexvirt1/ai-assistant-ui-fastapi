"""Similarity search over chunk embeddings.

Pure maths, no model calls and no database, so ranking behaviour is testable
without either.

Cosine similarity is computed in plain Python rather than numpy or pgvector.
Neither is available here - pgvector needs an OS-level install this deployment
cannot do - and at the scale that matters it does not pay: a document is ~87
chunks of 768 dimensions, which is ~67k multiply-adds and takes single-digit
milliseconds. Past roughly 20k chunks in one corpus this becomes the bottleneck
and numpy would be worth adding.
"""

import math
from dataclasses import dataclass

from .chunker import estimate_tokens
from .lexical import score_chunks

# How many candidates each channel contributes to the fusion.
#
# This cap is what makes hybrid search work rather than merely average the two.
# Fusing full rankings measured *worse* than lexical search alone - the vector
# channel is near-random on Russian, so it dragged good lexical hits down. Capped
# at 50, a chunk the vector channel ranks 374th simply contributes nothing, and
# the lexical channel's rank-1 hit wins:
#
#   term          vector rank    fused (uncapped)    fused (capped at 50)
#   Ахросимова            374                   5                       1
#   Каратаев              168                  10                       4
#   Шиншин                 23                   1                       1
CANDIDATES_PER_CHANNEL = 50

# Reciprocal-rank-fusion constant. The conventional 60: large enough that the
# difference between rank 1 and rank 2 does not dominate, small enough that
# rank 50 still counts for less than rank 5.
RRF_K = 60


@dataclass(frozen=True)
class Match:
    index: int
    score: float
    text: str


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine of the angle between two vectors, in [-1, 1].

    Returns 0.0 for a zero vector rather than dividing by zero: an unembeddable
    chunk should rank last, not crash the search.
    """
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def rank_chunks(
    query_vector: list[float],
    chunk_vectors: list[tuple[int, list[float]]],
    texts: dict[int, str],
    top_k: int = 5,
    min_score: float = 0.0,
) -> list[Match]:
    """The `top_k` chunks most similar to the query.

    Ties break on chunk index so results are stable between runs - a question
    asked twice should not return the same chunks in a different order.
    """
    if top_k <= 0:
        raise ValueError("top_k must be positive")

    scored = [
        Match(index=index, score=cosine_similarity(query_vector, vector),
              text=texts.get(index, ""))
        for index, vector in chunk_vectors
    ]
    scored = [m for m in scored if m.score >= min_score]
    scored.sort(key=lambda m: (-m.score, m.index))
    return scored[:top_k]


def reciprocal_rank_fusion(
    rankings: list[list[int]], k: int = RRF_K
) -> dict[int, float]:
    """Combine several rankings by rank rather than by score.

    Score-based fusion would need the two channels to be commensurable, and they
    are not: cosine similarities on this corpus sit between 0.80 and 0.88 while
    BM25 scores run from 0 to about 30. Worse, the cosine spread is so narrow
    that any weighting of it is dominated by noise. Ranks have neither problem.

    A chunk absent from a ranking contributes nothing, which is what lets a
    capped candidate list suppress a channel that is guessing.
    """
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, index in enumerate(ranking, 1):
            fused[index] = fused.get(index, 0.0) + 1.0 / (k + rank)
    return fused


def hybrid_search(
    query: str,
    query_vector: list[float],
    chunk_vectors: list[tuple[int, list[float]]],
    texts: dict[int, str],
    top_k: int = 12,
    candidates: int = CANDIDATES_PER_CHANNEL,
) -> list[Match]:
    """Rank chunks by fusing semantic similarity with exact term matching.

    The two channels fail in opposite directions, which is the point: vectors
    find a passage that means the same thing in different words, and BM25 finds
    the passage that contains the actual name. A question about a document is
    usually full of proper nouns.

    `score` on the returned matches is the fused rank score, not a similarity -
    it orders results but has no meaning on its own.
    """
    if top_k <= 0:
        raise ValueError("top_k must be positive")

    semantic = rank_chunks(query_vector, chunk_vectors, texts, top_k=candidates)
    lexical = score_chunks(query, texts)
    lexical_ranking = sorted(lexical, key=lambda i: (-lexical[i], i))[:candidates]

    fused = reciprocal_rank_fusion([[m.index for m in semantic], lexical_ranking])
    ordered = sorted(fused, key=lambda i: (-fused[i], i))[:top_k]
    return [Match(index=i, score=fused[i], text=texts.get(i, "")) for i in ordered]


def with_neighbours(indices: list[int], available: set[int], radius: int = 1) -> list[int]:
    """Each index plus its neighbours, in match order, without duplicates.

    A retrieved chunk is a ~400-token window cut at an arbitrary point, so the
    sentence that answers the question is often just past its edge. Narrative
    especially: the name-day scene in War and Peace introduces its guests across
    several consecutive chunks, and no single one holds the guest list.

    Order is preserved so the caller can spend a context budget on the best
    matches first.
    """
    if radius < 0:
        raise ValueError("radius must not be negative")

    out: list[int] = []
    seen: set[int] = set()
    for index in indices:
        for neighbour in range(index - radius, index + radius + 1):
            if neighbour in available and neighbour not in seen:
                seen.add(neighbour)
                out.append(neighbour)
    return out


def build_context(
    matches: list[Match],
    texts: dict[int, str] | None = None,
    max_tokens: int = 6_000,
    neighbour_radius: int = 1,
) -> str:
    """Retrieved chunks as the answering prompt sees them.

    Budgeted in estimated tokens rather than characters, because a character
    budget silently means different things per language: 12 000 characters is
    ~3 000 tokens of English but ~5 700 of Russian, so the same cap admitted
    almost twice as much of one as the other.

    Sections are selected in match order so the budget goes to the best matches,
    then emitted in document order so the model reads them as the text runs.
    Each is labelled so an answer can be traced back to the original.
    """
    if texts is None:
        texts = {m.index: m.text for m in matches}

    ranked = [m.index for m in matches]
    # Matches before neighbours, both in match order. Filling greedily from the
    # neighbour-expanded list instead spends the budget three chunks at a time
    # on the best match and its surroundings: measured on a 6 000-token budget
    # that admitted only the top three matches, discarding the other nine
    # ranked hits in favour of context around the first one.
    priority = ranked + [
        i for i in with_neighbours(ranked, set(texts), radius=neighbour_radius)
        if i not in set(ranked)
    ]

    kept: list[int] = []
    used = 0
    for index in priority:
        block = f"[Section {index + 1}]\n{texts.get(index, '')}"
        cost = estimate_tokens(block)
        if kept and used + cost > max_tokens:
            continue
        kept.append(index)
        used += cost

    return "\n\n".join(f"[Section {i + 1}]\n{texts.get(i, '')}" for i in sorted(kept))
