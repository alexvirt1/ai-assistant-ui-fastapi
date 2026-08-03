"""The structured-prose summary produced for each chunk.

Fields hold prose rather than bullet fragments, because a human may read the
intermediates and because prose survives the reduce step better. `entities` is
the exception: a list deduplicates cleanly across 87 chunks, which is exactly
what the reduce step needs.

Structure is what makes reduce tractable. Merging 87 freeform paragraphs
produces mush; merging 87 aligned records produces a document map - topics
concatenate into an outline, entities deduplicate, findings group by topic.
"""

from pydantic import BaseModel, Field

# Bumping this invalidates every cached summary. Cache entries are keyed by it,
# so a prompt change cannot silently serve results produced by the old wording.
PROMPT_VERSION = "1"


class ChunkSummary(BaseModel):
    """One chunk's contribution to the document map."""

    topic: str = Field(
        description="One sentence naming what this section of the document covers."
    )
    findings: str = Field(
        description=(
            "The substantive content, in prose. Two to five sentences. State "
            "what the text says, not what kind of text it is."
        )
    )
    entities: list[str] = Field(
        default_factory=list,
        description=(
            "Names appearing in this section: people, organisations, systems, "
            "products, or notable figures. Short strings, no descriptions."
        ),
    )
    uncertain: str = Field(
        default="",
        description=(
            "Anything ambiguous, or a thought that is cut off because the "
            "section boundary interrupted it. Empty string if nothing."
        ),
    )


SYSTEM_PROMPT = (
    "You summarise one section of a larger document at a time. You see only "
    "this section, never the whole document, so do not speculate about what "
    "comes before or after it. Report only what this section actually says."
)


def build_user_prompt(text: str, index: int, total: int) -> str:
    """The per-chunk instruction.

    The position is included because it measurably changes behaviour: told it is
    section 12 of 87, the model stops writing openings like "This document
    describes..." and summarises the section in front of it.

    The field-by-field instructions are spelled out here rather than left to the
    schema descriptions alone. Tested against qwen3:8b, the schema descriptions
    by themselves produced empty `entities` even for text naming five people and
    companies, and a `topic` that echoed the document title instead of the
    section's own subject.
    """
    return (
        f"Section {index + 1} of {total} of a longer document.\n\n"
        f"<section>\n{text}\n</section>\n\n"
        "Summarise this section, filling every field:\n"
        "- topic: what THIS section is specifically about. Do not just repeat "
        "the document's title.\n"
        "- findings: what the section actually states, in two to five "
        "sentences. Include specific numbers, values and rules where present.\n"
        "- entities: list every proper name that appears - people, companies, "
        "systems, products, places. Copy the names exactly. Use an empty list "
        "only if the section genuinely names none.\n"
        "- uncertain: note anything cut off mid-thought by the section "
        "boundary, otherwise leave it empty."
    )


REPAIR_SUFFIX = (
    "\n\nRespond with valid JSON only, matching the required fields exactly. "
    "No commentary before or after the JSON."
)


def degraded_summary(index: int, total: int, raw: str) -> ChunkSummary:
    """Fallback when the model will not produce valid structure twice running.

    Keeping the prose is far better than losing the chunk: over 87 chunks a
    small model will occasionally fail a schema, and an 80-minute job must not
    die on chunk 61. The `uncertain` field records the degradation so the reduce
    step - and the reader - know this entry is lower quality.
    """
    text = (raw or "").strip()
    first_sentence = text.split(". ")[0][:200] if text else "(no summary produced)"
    return ChunkSummary(
        topic=first_sentence,
        findings=text or "(the model returned nothing for this section)",
        entities=[],
        uncertain=(
            f"Structured output failed for section {index + 1} of {total}; "
            f"this entry is unvalidated prose."
        ),
    )
