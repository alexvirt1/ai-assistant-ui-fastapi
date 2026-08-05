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


# Characters per token, by script.
#
# The plain estimator assumes 4.0 for everything, which is close for English and
# badly wrong for anything else. Measured against cl100k on the 3.2 MB Russian
# text of War and Peace: 2.08 characters per token, a 1.94x undercount. Every
# budget denominated in these units inherited that error - "400-token" retrieval
# chunks were really ~770 tokens, and the history trimmer kept nearly twice what
# it believed it was keeping, which is an overflow rather than a rounding error.
#
# 1.8 rather than the measured 2.08 because over-estimating is the safe
# direction: chunks come out under budget and the trimmer keeps slightly less
# than it could, where under-estimating overflows the context window.
_ASCII_CHARS_PER_TOKEN = 4.0
_WIDE_CHARS_PER_TOKEN = 1.8


def estimate_tokens(text: str) -> int:
    """Approximate token count for raw text.

    Uses the same estimator as the history trimmer, so the chunker's budget and
    the trimmer's budget are denominated in the same (approximate) unit.

    Pure-ASCII text takes the original path unchanged, so English chunking,
    scope estimates and the trimmer behave exactly as before; only text the old
    estimator was demonstrably wrong about is treated differently.
    """
    if not text:
        return 0
    if text.isascii():
        return max(0, count_tokens_approximately([HumanMessage(text)]) - _MESSAGE_OVERHEAD)

    # UTF-8 byte count minus character count gives 1 per two-byte character
    # (Cyrillic, Greek, accented Latin) and 2 per three-byte one (CJK). C-speed,
    # where a per-character loop is far too slow to call from the chunker's
    # inner loop. CJK is thereby weighted double, which is roughly right: it is
    # about one token per character, not one per two.
    wide = len(text.encode("utf-8")) - len(text)
    ascii_chars = max(0, len(text) - wide)
    return int(ascii_chars / _ASCII_CHARS_PER_TOKEN + wide / _WIDE_CHARS_PER_TOKEN)


def count_message_tokens(messages) -> int:
    """Token counter for `trim_messages`, in the same unit as estimate_tokens.

    Kept here rather than in the agent so the trimmer's budget and the
    chunker's cannot drift apart: they are the same number denominated the same
    way, which is the whole reason the chunker borrowed this estimator.

    Content may be a plain string or the list-of-parts form a multimodal message
    uses. Tool calls are counted too: their name and arguments are serialised
    into the prompt like anything else, and an AIMessage that requests a tool
    often has empty content, so counting only `.content` would score a real
    request as nearly free and let the trimmer keep far more than its budget.
    """
    total = 0
    for message in messages:
        content = getattr(message, "content", message)
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        else:
            text = str(content)

        # The whole call, not just name and args: the id and type fields are
        # serialised into the request too, and leaving them out made this
        # counter more permissive than the one it replaced on ASCII history -
        # the wrong direction for a budget whose job is to prevent overflow.
        for call in getattr(message, "tool_calls", None) or []:
            text += f" {call}"

        total += estimate_tokens(text) + _MESSAGE_OVERHEAD

    # Never below the counter this replaces. The correction exists for scripts
    # that one undercounts; it must not become a licence to keep more history
    # than before on the ASCII text where that one is already accurate, and it
    # models role names and metadata slightly more cheaply than langchain does.
    return max(total, count_tokens_approximately(messages))


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
