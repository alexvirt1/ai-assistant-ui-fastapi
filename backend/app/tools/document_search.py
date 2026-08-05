"""Searching an attached document from within a conversation.

Returns the relevant passages rather than a finished answer. The agent then
writes the reply itself, with the rest of the conversation in view - a tool that
answered on its own would mean two models answering and the agent parroting the
second one.

Indexing is lazy: the first search on an unindexed document embeds it, which
takes about a minute per 5 MB. Subsequent searches reuse it.
"""

import logging
import os
import uuid

from langchain_core.tools import tool

from ..documents.embeddings import embed_query, make_embedder
from ..documents.indexing import ensure_indexed
from ..documents.retrieval import build_context, hybrid_search
from ..documents.store import get_document, load_retrieval_chunks
from .base import ToolSpec, register

logger = logging.getLogger(__name__)

# Raised from 5 after a question about a Russian novel returned four passages
# from the wrong scenes. Five ~400-token windows is enough for "what is the
# calibration constant" and nowhere near enough for "who was present", where the
# answer is spread across a scene several chunks long.
TOP_K = int(os.getenv("DOCUMENT_SEARCH_TOP_K", "12"))

# Recomputed from the environment rather than imported from app.langgraph.agent:
# that module builds the graph, which imports the tool registry, which imports
# this. Same expression, no cycle.
HISTORY_MAX_TOKENS = int(
    os.getenv("HISTORY_MAX_TOKENS") or int(os.getenv("OLLAMA_NUM_CTX", "8192")) // 3
)

# Derived from the history budget rather than fixed, because the two have to
# move together: the passages arrive as a ToolMessage and are trimmed like
# anything else in the conversation, so a tool result larger than
# HISTORY_MAX_TOKENS is discarded before the model reads it. Somewhat over half
# leaves room for the question and a few prior turns.
#
# This is the binding constraint on document questions, not top_k. Raising
# OLLAMA_NUM_CTX lifts it automatically; raising this alone evicts the
# conversation instead.
CONTEXT_TOKENS = int(
    os.getenv("DOCUMENT_CONTEXT_TOKENS") or int(HISTORY_MAX_TOKENS * 0.55)
)


@tool
async def search_document(document_id: str, question: str) -> str:
    """Search an attached document and return the passages most relevant to a question.

    Use this whenever the conversation mentions an attached document with an id.
    The document is too large to read in full, so search it for the parts that
    matter instead of guessing.
    """
    try:
        doc_uuid = uuid.UUID(document_id)
    except ValueError:
        return f"Error: '{document_id}' is not a valid document id."

    document = await get_document(doc_uuid)
    if document is None:
        return f"Error: no document with id {document_id}."

    embedder, embed_model = make_embedder()
    # Indexes on first use, waiting rather than duplicating if the upload's
    # background index is still running.
    await ensure_indexed(document_id, embedder, embed_model)
    stored, texts = await load_retrieval_chunks(document_id, embed_model)

    if not stored:
        return f"Error: {document.name} could not be indexed."

    query_vector = await embed_query(question, embedder)
    matches = hybrid_search(question, query_vector, stored, texts, top_k=TOP_K)
    if not matches:
        return f"No passages in {document.name} matched that question."

    context = build_context(matches, texts, max_tokens=CONTEXT_TOKENS)
    return (
        f"Passages from {document.name} most relevant to the question:\n\n"
        f"{context}\n\n"
        "Answer from these passages only, and name the section each fact came "
        "from. You may recognise this document from training - ignore anything "
        "you recall about it, because your memory of it is unreliable and these "
        "passages are not. If they do not contain the answer, say so plainly "
        "rather than filling the gap."
    )


register(
    ToolSpec(
        tool=search_document,
        prompt_hint=(
            "When the conversation contains an <attached-document> reference, "
            "call search_document with that id to answer questions about it. "
            "The document's text is not in the conversation - searching is the "
            "only way to read it."
        ),
        # Retrieval needs the documents tables; without Postgres the tool would
        # fail on every call, so it self-disables like the REST tools do.
        available=lambda: bool(os.getenv("DATABASE_URL")),
    )
)
