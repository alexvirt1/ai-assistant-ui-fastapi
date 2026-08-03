"""Splitting a document into chunks that fit the model's context window.

Pure logic - no model calls, no database - so the parts most likely to be wrong
are also the cheapest to test.

Two properties matter more than elegance here:

* **No chunk may exceed the budget.** A chunk that overflows is not truncated
  by the model, it derails the whole map step for that section.
* **Chunks overlap.** A fact split across a boundary would otherwise be
  invisible to both neighbours - the sentence "the calibration constant is"
  ending one chunk and "8.472 kelvin-seconds" starting the next means neither
  chunk contains the fact.
"""

from dataclasses import dataclass

from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import count_tokens_approximately

# Separators tried largest-unit-first, so a split lands on a paragraph break
# rather than mid-sentence where possible. The empty string is the last resort:
# a hard character cut, used only when a single "word" exceeds the budget.
_SEPARATORS = ("\n\n", "\n", ". ", " ", "")

# The estimator counts messages, not strings, and charges a fixed overhead per
# message. Subtracting it keeps a text-only count honest.
_MESSAGE_OVERHEAD = count_tokens_approximately([HumanMessage("")])


def estimate_tokens(text: str) -> int:
    """Approximate token count for raw text.

    Uses the same estimator as the history trimmer, so the chunker's budget and
    the trimmer's budget are denominated in the same (approximate) unit.
    """
    if not text:
        return 0
    return max(0, count_tokens_approximately([HumanMessage(text)]) - _MESSAGE_OVERHEAD)


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    tokens: int


def _split_once(text: str, separator: str) -> list[str]:
    """Split, keeping the separator attached to the preceding piece."""
    if separator == "":
        return list(text)
    parts = text.split(separator)
    out = [p + separator for p in parts[:-1]]
    if parts[-1]:
        out.append(parts[-1])
    return out


def _atoms(text: str, max_tokens: int) -> list[str]:
    """Break text into pieces that each fit the budget.

    Walks the separator list, only descending to a finer separator for the
    pieces that are still too large - so most splits land on paragraph breaks
    and only pathological runs get cut mid-word.
    """
    pieces = [text]
    for separator in _SEPARATORS:
        if all(estimate_tokens(p) <= max_tokens for p in pieces):
            return pieces
        expanded: list[str] = []
        for piece in pieces:
            if estimate_tokens(piece) <= max_tokens:
                expanded.append(piece)
            else:
                expanded.extend(_split_once(piece, separator))
        pieces = [p for p in expanded if p]
    return pieces


def _tail_for_overlap(text: str, overlap_tokens: int) -> str:
    """The trailing slice of a chunk to prepend to the next one."""
    if overlap_tokens <= 0 or not text:
        return ""
    # Token counts are approximate anyway, so slicing by the estimator's own
    # chars-per-token ratio is as accurate as anything more elaborate.
    approx_chars = overlap_tokens * 4
    if len(text) <= approx_chars:
        return text
    tail = text[-approx_chars:]
    # Prefer starting the overlap at a sentence or line boundary.
    for separator in ("\n\n", "\n", ". "):
        position = tail.find(separator)
        if 0 <= position < len(tail) // 2:
            return tail[position + len(separator) :]
    return tail


def chunk_text(
    text: str,
    max_tokens: int = 16_000,
    overlap_tokens: int = 800,
) -> list[Chunk]:
    """Split text into overlapping chunks that each fit `max_tokens`.

    `max_tokens` is the budget for document content only; the caller must leave
    room in the context window for the system prompt, the instruction and the
    generated summary.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if overlap_tokens < 0:
        raise ValueError("overlap_tokens must not be negative")
    if overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be smaller than max_tokens")

    if not text.strip():
        return []

    chunks: list[Chunk] = []
    current = ""
    carry = ""

    for atom in _atoms(text, max_tokens):
        candidate = current + atom
        if current and estimate_tokens(carry + candidate) > max_tokens:
            body = carry + current
            chunks.append(Chunk(len(chunks), body, estimate_tokens(body)))
            carry = _tail_for_overlap(body, overlap_tokens)
            current = atom
        else:
            current = candidate

    if current.strip():
        body = carry + current
        chunks.append(Chunk(len(chunks), body, estimate_tokens(body)))

    return chunks
