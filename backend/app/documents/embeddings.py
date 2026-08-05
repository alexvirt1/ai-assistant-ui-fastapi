"""Embedding chunks and questions with the `embed` role.

Indexing is a one-off cost per document; answering then costs one embedding
call plus one generation.

Sizing note that shapes usage: the VM does not keep the embedding model and the
chat model resident together - an embed call evicts qwen3:8b, and generating the
answer loads it back. That is roughly 9 seconds of swapping per question,
against ~43 minutes for a full map-reduce pass. Setting
`OLLAMA_MAX_LOADED_MODELS=2` on the Ollama service would remove it: the two
models are 0.6 GB and 6 GB against an 11.75 GB ceiling, so they fit together.
"""

import logging

from langchain_ollama import OllamaEmbeddings

from ..models import get_tag
from .chunker import estimate_tokens

logger = logging.getLogger(__name__)

EMBED_ROLE = "embed"

# Dimensionality is a property of whichever model the `embed` role resolves to
# (bge-m3 gives 1024, nomic-embed-text 768), so it is read from the vectors at
# save time rather than declared here. Rows are keyed by model name, which is
# what keeps two models' vectors from ever being compared: switching models
# leaves the old rows untouched and indexes afresh, and cosine_similarity raises
# on a dimension mismatch rather than returning a meaningless number.

# Retrieval chunk size, deliberately far smaller than the 16k-token chunks used
# for summarising. Two reasons, both learned the hard way:
#
#  * A 64 KB chunk embeds to a vector dominated by whatever is most common in
#    it. A planted fact was invisible among the boilerplate around it.
#  * Truncating a big chunk to fit the embedding model simply discards most of
#    it. An earlier version cut at 8000 characters and the facts under test sat
#    at offsets 11,440 and 18,795 - they were never embedded at all.
RETRIEVAL_CHUNK_TOKENS = 400
RETRIEVAL_OVERLAP_TOKENS = 60


def make_embedder(role: str = EMBED_ROLE) -> tuple[OllamaEmbeddings, str]:
    import os

    tag = get_tag(role)
    return (
        OllamaEmbeddings(
            model=tag,
            base_url=os.getenv("OLLAMA_BASE_URL", "http://192.168.87.160:11434"),
        ),
        tag,
    )


def split_for_retrieval(text: str) -> list[str]:
    """Re-chunk a document at retrieval granularity.

    Retrieval and summarisation want opposite things from a chunk: summarising
    wants few, large chunks to keep the number of model calls down; retrieval
    wants many, small ones so each embedding is specific.
    """
    from .chunker import chunk_text

    return [
        c.text
        for c in chunk_text(
            text,
            max_tokens=RETRIEVAL_CHUNK_TOKENS,
            overlap_tokens=RETRIEVAL_OVERLAP_TOKENS,
        )
    ]


async def embed_chunks(
    chunks: list[str],
    embedder: OllamaEmbeddings,
    on_progress=None,
) -> list[list[float]]:
    """Embed every chunk, in order.

    Batched in one call where the client supports it: 87 round trips would
    otherwise dominate what is meant to be the cheap phase.
    """
    vectors = await embedder.aembed_documents(chunks)
    if on_progress is not None:
        on_progress(len(vectors), len(chunks))
    return vectors


async def embed_query(question: str, embedder: OllamaEmbeddings) -> list[float]:
    return await embedder.aembed_query(question)


def token_cost(chunks: list[str]) -> int:
    return sum(estimate_tokens(c) for c in chunks)
