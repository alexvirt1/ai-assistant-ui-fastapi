"""Sizing a map-reduce job before any model runs.

The point is to answer "what am I committing to?" instantly and for free, so a
5 MB file produces an honest "86 chunks, about 80 minutes" rather than either
silent truncation or an hour of unexplained waiting.

Throughput defaults come from a real 87-chunk run on this deployment: 1.31M
tokens mapped in ~25 minutes on qwen3:8b, fully in VRAM.

Calibration history, because both errors were instructive:

* 317 tok/s with 300-token summaries over-predicted 2.6x (78 min vs 29.7). Long
  prompts process far faster per token than the short benchmark used.
* 1000 tok/s with 150-token summaries then under-predicted 1.7x, because
  key_facts extraction (PROMPT_VERSION 2) roughly quintupled the output per
  chunk - the summaries are no longer short.

The current figures reproduce both measured runs: 87 chunks in 43.5 min and 11
chunks in 5.4 min, predicted at 43.5 and 5.3.
"""

import os
from dataclasses import dataclass
from enum import Enum

from .chunker import estimate_tokens

PROMPT_TOKENS_PER_SEC = float(os.getenv("PROMPT_TOKENS_PER_SEC", "1000"))
GEN_TOKENS_PER_SEC = float(os.getenv("GEN_TOKENS_PER_SEC", "57"))
# Roughly what one structured chunk summary costs to generate.
SUMMARY_TOKENS = int(os.getenv("CHUNK_SUMMARY_TOKENS", "800"))


class Tier(str, Enum):
    """How the caller should treat the job."""

    SINGLE_PASS = "single_pass"  # fits one context; skip map-reduce entirely
    QUICK = "quick"  # minutes; just run it
    CONFIRM = "confirm"  # tens of minutes; warn and ask first
    CONSIDER_RETRIEVAL = "consider_retrieval"  # an hour or more


@dataclass(frozen=True)
class Scope:
    tokens: int
    chunks: int
    estimated_seconds: float
    tier: Tier
    message: str

    @property
    def estimated_minutes(self) -> float:
        return self.estimated_seconds / 60


def _tier_for(chunks: int) -> Tier:
    if chunks <= 1:
        return Tier.SINGLE_PASS
    if chunks <= 10:
        return Tier.QUICK
    if chunks <= 60:
        return Tier.CONFIRM
    return Tier.CONSIDER_RETRIEVAL


def _humanise(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes"
    return f"{seconds / 3600:.1f} hours"


def estimate_scope(tokens: int, chunks: int) -> Scope:
    """Cost of mapping `chunks` chunks totalling `tokens`, plus one reduce."""
    map_seconds = tokens / PROMPT_TOKENS_PER_SEC + (
        chunks * SUMMARY_TOKENS / GEN_TOKENS_PER_SEC
    )
    # The reduce pass re-reads every chunk summary and writes a longer one.
    reduce_tokens = chunks * SUMMARY_TOKENS
    reduce_seconds = (
        reduce_tokens / PROMPT_TOKENS_PER_SEC
        + (SUMMARY_TOKENS * 4) / GEN_TOKENS_PER_SEC
    )
    total = map_seconds + reduce_seconds if chunks > 1 else map_seconds

    tier = _tier_for(chunks)
    pretty = _humanise(total)
    message = {
        Tier.SINGLE_PASS: (
            "Fits in a single context window - no chunked processing needed."
        ),
        Tier.QUICK: f"{chunks} chunks, about {pretty}.",
        Tier.CONFIRM: (
            f"{chunks} chunks, about {pretty}. This runs for a while; "
            f"confirm before starting."
        ),
        Tier.CONSIDER_RETRIEVAL: (
            f"{chunks} chunks, about {pretty}. If you want to ask targeted "
            f"questions rather than summarise the whole document, retrieval "
            f"answers in seconds instead."
        ),
    }[tier]

    return Scope(
        tokens=tokens,
        chunks=chunks,
        estimated_seconds=total,
        tier=tier,
        message=message,
    )


def scope_for_text(
    text: str, max_tokens: int = 16_000, overlap_tokens: int = 800
) -> Scope:
    """Estimate without materialising chunks - cheap enough to call on upload.

    Counts chunks arithmetically rather than by splitting, so a 5 MB file is
    sized in milliseconds.
    """
    tokens = estimate_tokens(text)
    if tokens == 0:
        return Scope(0, 0, 0.0, Tier.SINGLE_PASS, "Document is empty.")

    stride = max_tokens - overlap_tokens
    if tokens <= max_tokens:
        chunks = 1
    else:
        # Ceiling division: the first chunk covers max_tokens, each subsequent
        # one advances by stride because overlap_tokens are re-read.
        chunks = 1 + -(-(tokens - max_tokens) // stride)
    return estimate_scope(tokens, chunks)
