import logging
from uuid import uuid4
from typing import Any, List, Literal, Optional, Union

from assistant_stream import RunController, create_run
from assistant_stream.serialization import DataStreamResponse
from fastapi import FastAPI
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from pydantic import BaseModel

from .langgraph.agent import render_document_block

logger = logging.getLogger(__name__)


class LanguageModelTextPart(BaseModel):
    type: Literal["text"]
    text: str
    providerMetadata: Optional[Any] = None


class LanguageModelImagePart(BaseModel):
    type: Literal["image"]
    image: str
    mimeType: Optional[str] = None
    providerMetadata: Optional[Any] = None


class LanguageModelFilePart(BaseModel):
    type: Literal["file"]
    data: str
    mimeType: str
    providerMetadata: Optional[Any] = None


class LanguageModelToolCallPart(BaseModel):
    type: Literal["tool-call"]
    toolCallId: str
    toolName: str
    args: Any
    providerMetadata: Optional[Any] = None


class LanguageModelToolResultContentPart(BaseModel):
    type: Literal["text", "image"]
    text: Optional[str] = None
    data: Optional[str] = None
    mimeType: Optional[str] = None


class LanguageModelToolResultPart(BaseModel):
    type: Literal["tool-result"]
    toolCallId: str
    toolName: str
    result: Any
    isError: Optional[bool] = None
    content: Optional[List[LanguageModelToolResultContentPart]] = None
    providerMetadata: Optional[Any] = None


class LanguageModelSystemMessage(BaseModel):
    role: Literal["system"]
    content: str


class LanguageModelUserMessage(BaseModel):
    role: Literal["user"]
    content: List[
        Union[LanguageModelTextPart, LanguageModelImagePart, LanguageModelFilePart]
    ]


class LanguageModelAssistantMessage(BaseModel):
    role: Literal["assistant"]
    content: List[Union[LanguageModelTextPart, LanguageModelToolCallPart]]


class LanguageModelToolMessage(BaseModel):
    role: Literal["tool"]
    content: List[LanguageModelToolResultPart]


LanguageModelV1Message = Union[
    LanguageModelSystemMessage,
    LanguageModelUserMessage,
    LanguageModelAssistantMessage,
    LanguageModelToolMessage,
]


def convert_to_langchain_messages(
    messages: List[LanguageModelV1Message],
) -> List[BaseMessage]:
    result: List[BaseMessage] = []

    for msg in messages:
        if msg.role == "system":
            result.append(SystemMessage(content=msg.content))

        elif msg.role == "user":
            content = []
            for p in msg.content:
                if isinstance(p, LanguageModelTextPart):
                    content.append({"type": "text", "text": p.text})
                elif isinstance(p, LanguageModelImagePart):
                    content.append({"type": "image_url", "image_url": p.image})
            result.append(HumanMessage(content=content))

        elif msg.role == "assistant":
            text_parts = [
                p for p in msg.content if isinstance(p, LanguageModelTextPart)
            ]
            text_content = " ".join(p.text for p in text_parts)
            tool_calls = [
                {
                    "id": p.toolCallId,
                    "name": p.toolName,
                    "args": p.args,
                }
                for p in msg.content
                if isinstance(p, LanguageModelToolCallPart)
            ]
            result.append(AIMessage(content=text_content, tool_calls=tool_calls))

        elif msg.role == "tool":
            for tool_result in msg.content:
                result.append(
                    ToolMessage(
                        content=str(tool_result.result),
                        tool_call_id=tool_result.toolCallId,
                    )
                )

    return result


class FrontendToolCall(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: dict[str, Any]


class AttachedDocument(BaseModel):
    id: str
    name: Optional[str] = None
    sections: Optional[int] = None


class ChatRequest(BaseModel):
    id: Optional[str] = None
    threadId: Optional[str] = None
    system: Optional[str] = ""
    tools: Optional[List[FrontendToolCall]] = []
    # Sent on every turn, not just the one carrying the attachment: the
    # reference has to reach the system prompt, because a reference living in
    # the conversation is trimmed away as the thread grows.
    documents: Optional[List[AttachedDocument]] = []
    messages: List[LanguageModelV1Message]


def add_langgraph_route(app: FastAPI, graph, path: str):
    async def chat_completions(request: ChatRequest):
        # inputs = convert_to_langchain_messages(request.messages)
        all_inputs = convert_to_langchain_messages(request.messages)

        inputs = []
        for message in reversed(all_inputs):
            if getattr(message, "type", None) == "human":
                inputs = [message]
                break

        if not inputs and all_inputs:
            inputs = [all_inputs[-1]]

        thread_id = request.threadId or request.id or str(uuid4())
        print(f"REQUEST id={request.id} threadId={request.threadId} LANGGRAPH thread_id={thread_id}",
              flush=True,
        )
        async def run(controller: RunController):
            tool_calls = {}
            tool_calls_by_idx = {}
            emitted_text = ""

            def content_to_text(content: Any) -> str:
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

            def append_deduped_text(text: str):
                nonlocal emitted_text

                if not text:
                    return

                if text == emitted_text:
                    return

                if text.startswith(emitted_text):
                    delta = text[len(emitted_text):]
                    if delta:
                        controller.append_text(delta)
                        emitted_text += delta
                    return

                controller.append_text(text)
                emitted_text += text

            document_block = render_document_block(
                [d.model_dump() for d in (request.documents or [])]
            )

            try:
                async for msg, metadata in graph.astream(
                    {"messages": inputs, "documents": document_block},
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "system": request.system,
                            "frontend_tools": request.tools,
                        }
                    },
                    stream_mode="messages",
                ):
                    if isinstance(msg, ToolMessage):
                        tool_controller = tool_calls.get(msg.tool_call_id)
                        if tool_controller is not None:
                            tool_controller.set_result(str(msg.content))
                        continue

                    if isinstance(msg, AIMessageChunk) or isinstance(msg, AIMessage):
                        append_deduped_text(content_to_text(msg.content))

                        for chunk in getattr(msg, "tool_call_chunks", []) or []:
                            idx = chunk.get("index")
                            call_id = chunk.get("id")
                            name = chunk.get("name")
                            args = chunk.get("args") or ""

                            if not call_id or not name:
                                continue

                            # Providers that stream tool calls incrementally (OpenAI)
                            # key the accumulation by index; Ollama delivers each call
                            # complete in one chunk with index None, so fall back to
                            # the call id.
                            key = call_id if idx is None else idx

                            if key not in tool_calls_by_idx:
                                tool_controller = await controller.add_tool_call(
                                    name, call_id
                                )
                                tool_calls_by_idx[key] = tool_controller
                                tool_calls[call_id] = tool_controller
                            else:
                                tool_controller = tool_calls_by_idx[key]

                            if args:
                                tool_controller.append_args_text(str(args))
            except Exception as exc:
                # Without this the run dies mid-stream and the user gets an
                # assistant message with no content at all - a blank bubble with
                # no hint that anything went wrong. Seen live when the embedding
                # model hit cudaMalloc out of memory: the tool node raised, the
                # graph aborted, and the UI simply showed nothing.
                logger.exception("chat run failed")
                # Not str(exc).splitlines()[0] - an exception with an empty
                # message ("".splitlines() == []) would raise IndexError here,
                # inside the handler, and write nothing at all. TimeoutError()
                # and ValueError() both do that.
                message = str(exc).strip()
                reason = message.splitlines()[0][:200] if message else type(exc).__name__
                controller.append_text(
                    f"\n\n_Something went wrong while answering: {reason}_"
                )

        return DataStreamResponse(create_run(run))

    app.add_api_route(path, chat_completions, methods=["POST"])
