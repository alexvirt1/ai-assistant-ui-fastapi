"""What a cloud model is shown of the conversation.

Earlier turns go as plain text only: what the user asked and what the
assistant answered. Their tool calls and tool results are left out, and with
them any document passages, web pages or other tool output from earlier turns -
privacy by construction rather than by instruction, and fewer tokens to pay
for. The current turn is passed as is, since it holds the cloud model's own
tool calls and their results.

Whether documents may be used at all is decided elsewhere (the document block
in the system prompt and the search_document tool are only given to the cloud
model when the chat allows it).
"""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from ..chats.messages import content_to_text

ESCALATE_TOOL = "escalate"


def _merge(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Join consecutive messages of the same role, which the API expects alternating."""
    out: list[BaseMessage] = []
    for message in messages:
        if out and type(out[-1]) is type(message):
            joined = f"{content_to_text(out[-1].content)}\n\n{content_to_text(message.content)}"
            out[-1] = type(message)(content=joined.strip())
        else:
            out.append(message)
    return out


def cloud_view(messages: list[BaseMessage]) -> list[BaseMessage]:
    """The conversation as a cloud model sees it (see the module docstring)."""
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    if last_human < 0:
        return list(messages)

    earlier: list[BaseMessage] = []
    for message in messages[:last_human]:
        if isinstance(message, HumanMessage):
            text = content_to_text(message.content).strip()
            if text:
                earlier.append(HumanMessage(content=text))
        elif isinstance(message, AIMessage):
            parts = [content_to_text(message.content).strip()]
            for call in message.tool_calls or []:
                if call.get("name") == ESCALATE_TOOL:
                    reason = (call.get("args") or {}).get("reason", "")
                    parts.append(f"(The local model suggested handing this question to a "
                                 f"cloud model{': ' + reason if reason else ''}.)")
            text = "\n".join(p for p in parts if p)
            if text:
                earlier.append(AIMessage(content=text))
        # ToolMessages and system messages from earlier turns are dropped.

    # The API wants the conversation to start with the user.
    while earlier and isinstance(earlier[0], AIMessage):
        earlier.pop(0)
    current = list(messages[last_human:])
    return _merge(earlier + current[:1]) + current[1:]


def is_cloud_message(message: BaseMessage) -> bool:
    return isinstance(message, AIMessage) and (
        (message.response_metadata or {}).get("model_provider") == "anthropic"
        or str((message.response_metadata or {}).get("model_name", "")).startswith("claude")
        or str((message.response_metadata or {}).get("model", "")).startswith("claude")
    )


def cloud_model_id(message: BaseMessage) -> str | None:
    meta = message.response_metadata or {}
    return meta.get("model_name") or meta.get("model")


__all__ = ["cloud_view", "is_cloud_message", "cloud_model_id", "ToolMessage"]
