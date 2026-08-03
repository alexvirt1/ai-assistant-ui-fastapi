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


def build_context(matches: list[Match], max_chars: int = 12_000) -> str:
    """Retrieved chunks as the answering prompt sees them.

    Each is labelled with its section number so the model can cite where an
    answer came from, and so a reader can check it against the original.
    """
    parts: list[str] = []
    used = 0
    for match in matches:
        block = f"[Section {match.index + 1}]\n{match.text}"
        if used + len(block) > max_chars and parts:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)
