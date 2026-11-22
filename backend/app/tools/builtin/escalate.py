"""Auto mode: the local model proposes handing a question to a cloud model.

The tool does not call the cloud. It returns a proposal that the chat shows as
an approval card ("Ask Claude Sonnet 5.5 / Keep local"); only the user's click
sends anything out. It is bound to the local model in auto mode only (see
make_model_selector in app/langgraph/agent.py), and its instructions are added
to the system prompt only then.
"""

import json

from langchain_core.tools import tool

from ...cloud import models as cloud_models
from ..base import ToolSpec, register

ESCALATE_HINT = (
    "You run locally and are small. When a question needs exact arithmetic on large numbers, "
    "careful multi-step reasoning, substantial code, deep analysis, or current facts you cannot "
    "have, call escalate with a short reason instead of answering, then reply with one short "
    "sentence saying you suggest asking the cloud model. The user decides whether to send it. "
    "For everything else, answer yourself as usual."
)


@tool
def escalate(reason: str) -> str:
    """Propose handing the user's question to a more capable cloud model. The user approves or declines."""
    model = cloud_models.default_model()
    return json.dumps({"proposed": True, "reason": reason,
                       "model": model.label if model else None})


register(ToolSpec(tool=escalate, available=lambda: cloud_models.default_model() is not None))
