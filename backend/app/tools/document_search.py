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
from ..documents.retrieval import build_context, rank_chunks
from ..documents.store import get_document, load_retrieval_chunks
from .base import ToolSpec, register

logger = logging.getLogger(__name__)

TOP_K = int(os.getenv("DOCUMENT_SEARCH_TOP_K", "5"))


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
    matches = rank_chunks(query_vector, stored, texts, top_k=TOP_K)
    if not matches:
        return f"No passages in {document.name} matched that question."

    context = build_context(matches)
    return (
        f"Passages from {document.name} most relevant to the question:\n\n"
        f"{context}\n\n"
        "Answer from these passages only. If they do not contain the answer, "
        "say so rather than guessing."
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
