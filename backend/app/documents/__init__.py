"""Large-document handling: chunking, sizing, and storage.

Phase 1 of the map-reduce pipeline. Everything here is deliberately free of
model calls - a document is chunked and sized before any inference is paid for,
so "86 chunks, about 80 minutes" is known instantly.
"""

from .chunker import Chunk, chunk_text, estimate_tokens
from .scope import Scope, Tier, estimate_scope, scope_for_text

__all__ = [
    "Chunk",
    "Scope",
    "Tier",
    "chunk_text",
    "estimate_scope",
    "estimate_tokens",
    "scope_for_text",
]
