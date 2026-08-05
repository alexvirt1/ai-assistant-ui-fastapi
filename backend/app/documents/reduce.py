"""The reduce step: many chunk summaries into one document summary.

The central choice here is how little the model is asked to do. Entities, the
outline and the gap report are merged **deterministically in code**; the model
only writes the prose that genuinely requires judgement - the overview and the
key findings.

That split matters for correctness, not just cost. Asked to merge 87 entity
lists, a model silently drops some, and with hierarchical reduce the loss
compounds at every level. Computed in code, an entity named once in chunk 3
survives to the final summary no matter how many levels it passes through.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .chunker import estimate_tokens
from .summaries import ChunkSummary

# Bumping this invalidates reduce results the same way PROMPT_VERSION does for
# the map step.
REDUCE_PROMPT_VERSION = "1"

# Beyond this, the entity list stops informing the reduce prompt and starts
# crowding it out.
MAX_ENTITIES = 120

# Facts are the payload a reader most often wants back, so this is generous;
# 87 chunks yielding a handful each still fits comfortably.
MAX_KEY_FACTS = 400


class ReduceOutput(BaseModel):
    """What the model is asked for - deliberately only the prose."""

    overview: str = Field(
        description=(
            "Two to four sentences describing what this document is and what it "
            "covers, as a whole."
        )
    )
    key_findings: str = Field(
        description=(
            "The substantive content across all sections, in prose. Group "
            "related points. Keep specific numbers, values and rules."
        )
    )


@dataclass
class DocumentSummary:
    """The assembled result: model prose plus deterministically merged fields."""

    overview: str
    key_findings: str
    outline: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    key_facts: list[str] = field(default_factory=list)
    gaps: str = ""
    sections: int = 0
    degraded_sections: int = 0


def _normalise(entity: str) -> str:
    return re.sub(r"\s+", " ", entity).strip()


def merge_entities(summaries: list[ChunkSummary], limit: int = MAX_ENTITIES) -> list[str]:
    """Union of every entity, most frequently mentioned first.

    Deduplicated case-insensitively while preserving the first spelling seen.
    Deliberately does *not* merge substrings - "Fleetwise" and "Fleetwise
    routing system" stay separate, because collapsing them guesses, and a wrong
    merge is worse than a duplicate.
    """
    counts: Counter[str] = Counter()
    spellings: dict[str, str] = {}

    for summary in summaries:
        for raw in summary.entities:
            name = _normalise(raw)
            if not name:
                continue
            key = name.casefold()
            counts[key] += 1
            spellings.setdefault(key, name)

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], spellings[kv[0]].casefold()))
    return [spellings[key] for key, _ in ranked[:limit]]


def merge_key_facts(summaries: list[ChunkSummary], limit: int = MAX_KEY_FACTS) -> list[str]:
    """Every extracted fact, in document order, deduplicated.

    Order is preserved rather than ranked by frequency: facts are positional
    (chapter 3's threshold is not chapter 40's), and a reader following the
    document benefits from them appearing as they do in the text.

    This exists because summarising loses them. In an 87-chunk run, three of
    four planted facts vanished during the map step - each chunk compresses
    ~130:1, and one sentence in 64 KB does not survive that. Extracted into a
    list and merged here, a fact stated once reaches the final summary.
    """
    seen: set[str] = set()
    facts: list[str] = []

    for summary in summaries:
        for raw in summary.key_facts:
            fact = _normalise(raw)
            if not fact:
                continue
            key = fact.casefold()
            if key in seen:
                continue
            seen.add(key)
            facts.append(fact)

    return facts[:limit]


def build_outline(summaries: list[ChunkSummary]) -> list[str]:
    """Section topics in document order, with consecutive repeats collapsed.

    Overlapping chunks often describe the same section twice; repeating it in
    the outline would misrepresent the document's shape.
    """
    outline: list[str] = []
    for summary in summaries:
        topic = _normalise(summary.topic)
        if topic and (not outline or topic.casefold() != outline[-1].casefold()):
            outline.append(topic)
    return outline


def collect_gaps(summaries: list[ChunkSummary]) -> str:
    """What the summary cannot vouch for.

    Reported rather than hidden: a reader deserves to know that section 61 was
    unvalidated prose, or that a thought was cut off at a boundary.
    """
    notes = [_normalise(s.uncertain) for s in summaries if _normalise(s.uncertain)]
    if not notes:
        return ""
    unique = list(dict.fromkeys(notes))
    shown = unique[:10]
    suffix = "" if len(unique) <= 10 else f" (and {len(unique) - 10} more)"
    return " ".join(shown) + suffix


def batch_summaries(
    summaries: list[ChunkSummary], max_tokens: int
) -> list[list[ChunkSummary]]:
    """Group summaries into batches that each fit one reduce prompt.

    A single oversized summary still gets its own batch rather than being
    dropped - better a tight prompt than a lost section.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")

    batches: list[list[ChunkSummary]] = []
    current: list[ChunkSummary] = []
    running = 0

    for summary in summaries:
        cost = estimate_tokens(render_summary(summary))
        if current and running + cost > max_tokens:
            batches.append(current)
            current, running = [], 0
        current.append(summary)
        running += cost

    if current:
        batches.append(current)
    return batches


def render_summary(summary: ChunkSummary) -> str:
    """One chunk summary as the reduce prompt sees it."""
    parts = [f"Topic: {summary.topic}", f"Findings: {summary.findings}"]
    if summary.key_facts:
        parts.append("Facts: " + "; ".join(summary.key_facts))
    if summary.entities:
        parts.append("Names: " + ", ".join(summary.entities))
    return "\n".join(parts)


REDUCE_SYSTEM_PROMPT = (
    "You combine section summaries into a summary of the whole document. You "
    "are given summaries, not the original text, so do not invent detail that "
    "is not present in them. Where sections overlap, state the point once."
)


def build_reduce_prompt(summaries: list[ChunkSummary], level: int = 0) -> str:
    body = "\n\n".join(
        f"[Section {i + 1}]\n{render_summary(s)}" for i, s in enumerate(summaries)
    )
    scope = (
        "section summaries" if level == 0 else "partial summaries of groups of sections"
    )
    return (
        f"Below are {len(summaries)} {scope} from one document, in order.\n\n"
        f"{body}\n\n"
        "Write an overview of the document as a whole, and the key findings "
        "across all of it.\n\n"
        "Rules for key_findings:\n"
        "- Every number, measurement, threshold, constant, code and identifier "
        "that appears above must appear in your key_findings. Copy them "
        "exactly. Losing a specific value is the worst possible error here.\n"
        "- Prefer a longer answer that keeps all the values over a shorter one "
        "that reads well.\n"
        "- Do not list the sections back one by one; group related points."
    )


def as_chunk_summary(result: ReduceOutput, merged: list[ChunkSummary]) -> ChunkSummary:
    """An intermediate reduce result, shaped for the next level up.

    Entities are carried forward explicitly so nothing is lost between levels -
    the failure mode that made deterministic merging necessary in the first
    place.
    """
    return ChunkSummary(
        topic=result.overview[:200],
        findings=result.key_findings,
        entities=merge_entities(merged),
        key_facts=merge_key_facts(merged),
        uncertain=collect_gaps(merged),
    )
