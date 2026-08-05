"""Real model callers for the map step.

Kept apart from mapper.py so the orchestration stays free of model imports and
can be tested without a VM. These are the only pieces that actually talk to
Ollama.
"""

from ..models import make_chat_model
from .reduce import ReduceOutput
from .summaries import ChunkSummary

# The map step is high-volume and low-judgement: 87 short extractions rather
# than one hard reasoning task. The `fast` role keeps every chunk on one model,
# so the job pays no swap cost - the VM holds only one model at a time.
MAP_ROLE = "fast"

# Reduce is the opposite: one or two calls where quality decides the output the
# user actually reads. Worth the single model swap (~7-19s on this VM) against
# a job measured in tens of minutes.
REDUCE_ROLE = "deep"


def make_callers(role: str = MAP_ROLE):
    """Return (structured, plain, model_name) for map_document.

    `num_predict` is bounded: a summary that runs long wastes minutes across 87
    chunks, and the schema does not need more than a few hundred tokens.
    """
    model = make_chat_model(role, num_predict=512)
    structured_model = model.with_structured_output(ChunkSummary)

    async def structured(messages: list) -> ChunkSummary:
        result = await structured_model.ainvoke(messages)
        if isinstance(result, ChunkSummary):
            return result
        # with_structured_output can hand back a dict depending on the method
        # it negotiates; validate rather than assume.
        return ChunkSummary.model_validate(result)

    async def plain(messages: list) -> str:
        response = await model.ainvoke(messages)
        content = getattr(response, "content", response)
        return content if isinstance(content, str) else str(content)

    return structured, plain, model.model


def make_reduce_caller(role: str = REDUCE_ROLE):
    """Return (caller, model_name) for reduce_document.

    A larger num_predict than the map step: the document overview and key
    findings are the output a person reads, so they need room.
    """
    model = make_chat_model(role, num_predict=1536)
    structured_model = model.with_structured_output(ReduceOutput)

    async def call(messages: list) -> ReduceOutput:
        result = await structured_model.ainvoke(messages)
        if isinstance(result, ReduceOutput):
            return result
        return ReduceOutput.model_validate(result)

    return call, model.model
