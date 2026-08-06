"""Checkpointed LangChain messages -> the shape assistant-ui restores from.

The inverse of convert_to_langchain_messages in app/add_langgraph_route.py:
that one turns a request into graph input, this one turns stored state back
into a transcript the frontend can hand to the runtime as initialMessages.

Kept free of FastAPI and psycopg so it can be tested without either.
"""

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def content_to_text(content: Any) -> str:
    """Flatten LangChain message content to plain text.

    Content is a string for most providers and a list of typed parts for
    others (and always a list for a HumanMessage built by the chat route).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        return "".join(parts)
    return ""


def first_user_text(messages: list) -> str:
    """The text of the earliest human turn - what a thread gets titled from."""
    for message in messages:
        if isinstance(message, HumanMessage):
            return content_to_text(message.content)
    return ""


def to_core_messages(messages: list) -> list[dict]:
    """Render stored state as assistant-ui CoreMessages.

    Four things are deliberate here:

    - System messages are dropped. The system prompt is assembled server-side
      on every call (see make_prompt) and is not part of the conversation the
      user had.
    - A tool result is folded into the tool-call part that produced it, rather
      than emitted as its own message. That is how assistant-ui models a tool
      call, and it means a restored thread renders its tool cards complete
      with results instead of showing a spinner that never resolves.
    - Everything the assistant produced between two user turns becomes ONE
      message, however many times the ReAct loop went round. A stored turn is
      AIMessage(tool_calls) -> ToolMessage -> AIMessage(text), which the live
      runtime shows as a single message with parts appended to it; emitting one
      message per AIMessage instead put an extra assistant bubble above every
      answer that used a tool. It rendered empty, because a completed tool call
      draws nothing by design (see ToolExecutionIndicators) - so a restored
      conversation showed a blank bubble where the live one had shown a
      progress indicator that has since finished.
    - An assistant turn with no text at all is dropped. That is a run cancelled
      after the tool call and before the answer: every part it has renders
      nothing, so keeping it would produce exactly the blank bubble above.
    """
    # Tool results arrive after the AIMessage that called them, so collect them
    # first and attach on the way out rather than mutating an emitted message.
    results: dict[str, ToolMessage] = {}
    for message in messages:
        if isinstance(message, ToolMessage) and message.tool_call_id:
            results[message.tool_call_id] = message

    out: list[dict] = []
    # Parts of the assistant turn being assembled, across however many
    # AIMessages the ReAct loop produced.
    turn: list[dict] = []

    def end_assistant_turn() -> None:
        # Text is what makes a turn worth showing: the tool parts render
        # nothing once complete, so a turn without text is a blank bubble.
        if any(part["type"] == "text" for part in turn):
            out.append({"role": "assistant", "content": list(turn)})
        turn.clear()

    for message in messages:
        if isinstance(message, HumanMessage):
            end_assistant_turn()
            text = content_to_text(message.content)
            if text:
                out.append({"role": "user", "content": [{"type": "text", "text": text}]})

        elif isinstance(message, AIMessage):
            text = content_to_text(message.content)
            if text:
                turn.append({"type": "text", "text": text})

            for call in message.tool_calls or []:
                call_id = call.get("id") or ""
                part: dict[str, Any] = {
                    "type": "tool-call",
                    "toolCallId": call_id,
                    "toolName": call.get("name", ""),
                    # assistant-ui expects an object; a provider that streamed
                    # nothing parseable leaves this empty rather than absent.
                    "args": call.get("args") or {},
                }
                result = results.get(call_id)
                if result is not None:
                    part["result"] = content_to_text(result.content)
                    if getattr(result, "status", None) == "error":
                        part["isError"] = True
                turn.append(part)

    end_assistant_turn()
    return out
