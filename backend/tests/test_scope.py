"""Pre-flight sizing of a map-reduce job."""

import pytest

from app.documents.chunker import chunk_text, estimate_tokens
from app.documents.scope import Tier, estimate_scope, scope_for_text


def document(chars: int) -> str:
    unit = "The quick brown fox jumps over the lazy dog. "
    return (unit * (chars // len(unit) + 1))[:chars]


def test_empty_document_costs_nothing():
    scope = scope_for_text("")
    assert scope.chunks == 0
    assert scope.estimated_seconds == 0
    assert scope.tier is Tier.SINGLE_PASS


def test_small_document_needs_no_chunking():
    scope = scope_for_text(document(2_000))
    assert scope.chunks == 1
    assert scope.tier is Tier.SINGLE_PASS
    assert "single context" in scope.message


def test_tiers_escalate_with_size():
    tiers = [
        scope_for_text(document(size)).tier
        for size in (2_000, 300_000, 2_000_000, 5 * 1024 * 1024)
    ]
    assert tiers == [
        Tier.SINGLE_PASS,
        Tier.QUICK,
        Tier.CONFIRM,
        Tier.CONSIDER_RETRIEVAL,
    ]


def test_five_megabyte_file_is_sized_realistically():
    """The case that motivated this: a 5 MB attachment.

    The bound is anchored to a real run: 87 chunks mapped and reduced in 29.7
    minutes on this deployment. An earlier calibration predicted 78 minutes for
    the same work, which is the kind of error that makes the warning it exists
    to give less credible rather than more.
    """
    scope = scope_for_text(document(5 * 1024 * 1024))
    assert scope.tokens > 1_000_000
    assert 60 < scope.chunks < 120
    assert 15 < scope.estimated_minutes < 60, (
        f"estimate {scope.estimated_minutes:.0f} min is far from the measured 29.7"
    )
    assert scope.tier is Tier.CONSIDER_RETRIEVAL
    assert "retrieval" in scope.message


def test_large_job_recommends_the_cheaper_path():
    scope = scope_for_text(document(6 * 1024 * 1024))
    assert scope.tier is Tier.CONSIDER_RETRIEVAL
    assert "seconds" in scope.message


def test_confirm_tier_asks_before_starting():
    scope = scope_for_text(document(2_000_000))
    assert scope.tier is Tier.CONFIRM
    assert "confirm" in scope.message.lower()


def test_estimate_grows_with_chunk_count():
    small = estimate_scope(tokens=50_000, chunks=4)
    large = estimate_scope(tokens=500_000, chunks=40)
    assert large.estimated_seconds > small.estimated_seconds


def test_single_chunk_skips_the_reduce_cost():
    one = estimate_scope(tokens=10_000, chunks=1)
    two = estimate_scope(tokens=10_000, chunks=2)
    # Same content, but two chunks additionally pay for a reduce pass.
    assert two.estimated_seconds > one.estimated_seconds


@pytest.mark.parametrize("size", [200_000, 1_000_000, 5_000_000])
def test_predicted_chunk_count_matches_the_chunker(size):
    """The estimate is arithmetic, not a real split - it must not drift from
    what the chunker actually produces, or the warning would be a lie."""
    text = document(size)
    predicted = scope_for_text(text, max_tokens=16_000, overlap_tokens=800).chunks
    actual = len(chunk_text(text, max_tokens=16_000, overlap_tokens=800))
    assert abs(predicted - actual) <= max(2, actual * 0.1), (
        f"predicted {predicted} chunks, chunker produced {actual}"
    )


def test_scope_reports_the_token_count():
    text = document(100_000)
    assert scope_for_text(text).tokens == estimate_tokens(text)
