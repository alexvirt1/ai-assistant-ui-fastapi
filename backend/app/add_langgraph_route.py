import asyncio
import json
import logging
from uuid import uuid4
from typing import Any, List, Literal, Optional, Union

import anthropic
from assistant_stream import RunController, create_run
from assistant_stream.serialization import DataStreamResponse
from fastapi import Depends, FastAPI, HTTPException
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from pydantic import BaseModel

from .chats import store as chat_store
from .chats.messages import content_to_text
from .identity import current_user_id
from .cloud import models as cloud_models
from .cloud import usage as cloud_usage
from .images.comfyui import reservation as comfyui_reservation
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
    # Hybrid mode (app/cloud). The chat's mode and document permission are
    # stored on the thread when sent; `escalate` sends this one turn to the
    # cloud model (the user approved an escalation in auto mode).
    mode: Optional[Literal["local", "auto", "cloud"]] = None
    allowCloudDocuments: Optional[bool] = None
    escalate: Optional[bool] = None


# Seconds between heartbeats on a running turn's stream.
HEARTBEAT_SECONDS = 30


async def decide_route(request: "ChatRequest", thread_id: str, user_id: str) -> tuple[dict, str | None]:
    """Which model answers this turn, and a note to show if not the one asked for.

    Returns the hybrid-mode keys for the run's configurable (see turn_route in
    app/langgraph/agent.py). Stores the chat's mode and document permission
    when the request carries them.
    """
    mode, allow_documents = request.mode, request.allowCloudDocuments
    thread = None
    if chat_store.database_url():
        try:
            if mode is not None or allow_documents is not None:
                thread = await chat_store.update_thread(
                    thread_id, user_id, cloud_mode=mode, cloud_documents=allow_documents)
            else:
                thread = await chat_store.get_thread(thread_id, user_id)
        except Exception:
            logger.exception("could not read or store hybrid settings for %s", thread_id)
    if thread is not None:
        mode, allow_documents = thread.cloud_mode, thread.cloud_documents
    mode, allow_documents = mode or "local", bool(allow_documents)

    spec = cloud_models.default_model()
    wants_cloud = mode == "cloud" or bool(request.escalate)
    route = {"answer_with": "local", "chat_mode": mode if spec else "local",
             "allow_documents": allow_documents}
    if not wants_cloud:
        return route, None
    if spec is None:
        return route, "_No cloud model is configured (its API key is not set), so this was answered locally._\n\n"
    spent, budget = await cloud_usage.month_total(user_id), cloud_usage.budget_usd()
    if spent >= budget:
        return route, (f"_This month's cloud budget is used up (${spent:.2f} of ${budget:.2f}), "
                       "so this was answered locally._\n\n")
    route.update(answer_with="cloud", cloud_model=spec.name, cloud_label=spec.label)
    return route, None


def is_streamed_delta(msg, metadata: dict) -> bool:
    """Whether a streamed message is a delta from a cloud model (Claude)."""
    return isinstance(msg, AIMessageChunk) and (
        (metadata or {}).get("ls_provider") == "anthropic"
        or (msg.response_metadata or {}).get("model_provider") == "anthropic"
    )


async def record_cloud_turn(controller, route: dict, thread_id: str, user_id: str,
                            tokens: dict) -> None:
    """Log a cloud answer's usage and show its badge (the cloud_answer card)."""
    spec = next((m for m in cloud_models.load_models() if m.name == route.get("cloud_model")), None)
    cost = spec.cost(tokens["input"], tokens["output"]) if spec else 0.0
    try:
        await cloud_usage.record(thread_id=thread_id, user_id=user_id,
                                 model=spec.model if spec else "?", input_tokens=tokens["input"],
                                 output_tokens=tokens["output"], cost_usd=cost)
    except Exception:
        logger.exception("could not record cloud usage for %s", thread_id)
    badge = await controller.add_tool_call("cloud_answer", f"cloud-{uuid4().hex[:12]}")
    badge.set_result(json.dumps({"model": spec.label if spec else route.get("cloud_label"),
                                 "input_tokens": tokens["input"],
                                 "output_tokens": tokens["output"], "cost_usd": round(cost, 4)}))


def is_cloud_failure(exc: BaseException) -> bool:
    """A failure on the cloud provider's side, worth answering locally instead.

    Not a malformed request (400): that would be a bug here, and hiding it
    behind a local answer would keep it from being seen.
    """
    return isinstance(exc, anthropic.APIError) and not isinstance(exc, anthropic.BadRequestError)


def cloud_failure_note(exc: BaseException) -> str:
    """What the user is told before the local model answers instead."""
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        why = ("Claude rejected the API key. Check ANTHROPIC_API_KEY in backend/.env "
               "(a Console key starting sk-ant-api03-).")
    elif isinstance(exc, anthropic.NotFoundError):
        why = "This API key has no access to the configured Claude model."
    elif isinstance(exc, anthropic.RateLimitError):
        why = "Claude's rate limit for this API key was reached."
    elif isinstance(exc, (anthropic.OverloadedError, anthropic.InternalServerError)):
        why = "Claude is overloaded or unavailable right now."
    elif isinstance(exc, anthropic.APIConnectionError):
        why = "Claude could not be reached."
    else:
        why = "Claude could not answer."
    return f"_{why} Answered by the local model instead._\n\n"


async def note_reservation_conflict(thread_id: str) -> None:
    """Log a chat turn that starts while AI-1 holds a GPU reservation.

    The turn goes ahead - chat requests are allowed during a reservation - but
    it loads the chat model on AI-1, which can evict the model AI-1 reserved
    the GPU for. Agreed with the AI-1 agent: such conflicts get reported, and
    this line is what to report from.
    """
    try:
        held = await comfyui_reservation()
    except Exception:
        return
    if held is not None:
        logger.warning("chat turn during a GPU reservation on AI-1 (%s); the chat model "
                       "will be loaded there. Report this to the AI-1 agent.", held.describe())


async def keep_alive(controller: RunController) -> None:
    """Send a data chunk every HEARTBEAT_SECONDS until cancelled.

    A turn can be silent for minutes: with tools bound, the model's reply is not
    streamed (disable_streaming="tool_calling"), and an image takes a minute to
    generate. The Next.js proxy's fetch (undici) aborts a response body that
    sends nothing for 300 s, and the chat then shows an empty answer while the
    backend carries on - seen live when qwen3:8b ran on the CPU and took five
    minutes to call generate_image. assistant-ui's decoder ignores data chunks
    ("2:", a TODO in assistantDecoderStream), so these change nothing on screen.
    """
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        controller.add_data({"heartbeat": True})


def add_langgraph_route(app: FastAPI, graph, path: str):
    async def chat_completions(
        request: ChatRequest,
        user_id: str = Depends(current_user_id),
    ):
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

        # Registers the thread on its first turn and verifies ownership on
        # every later one. The thread id is client-supplied and the
        # checkpointer will load whatever it is handed, so this is what stops
        # one user's id from pulling another's transcript into the context.
        prompt_text = content_to_text(inputs[0].content) if inputs else ""
        try:
            await chat_store.claim_thread(
                thread_id,
                user_id,
                title=chat_store.derive_title(prompt_text),
                preview=prompt_text,
            )
        except chat_store.ThreadOwnershipError:
            raise HTTPException(status_code=403, detail="Not your chat")
        except Exception:
            # The registry is a sidebar, not the conversation. If Postgres is
            # unreachable the answer should still stream - the chat simply
            # will not be listed.
            logger.exception("could not register thread %s", thread_id)

        # Attach whatever the client says is on this conversation, then take
        # the authoritative list back from the database.
        #
        # The client is the only thing that knows a file was just uploaded, so
        # it still sends the list - but it must not be the only thing that
        # remembers. Reading it back means a document attached ten turns ago
        # stays reachable after a reload, in a second tab, or from a browser
        # that has never seen it, and a client cannot announce a document that
        # was never attached to this thread.
        documents = [d.model_dump() for d in (request.documents or [])]
        if chat_store.database_url():
            try:
                await chat_store.attach_documents(
                    thread_id, [str(d["id"]) for d in documents if d.get("id")]
                )
                documents = await chat_store.list_thread_documents(thread_id, user_id)
            except Exception:
                # Falls back to what the client sent, which is what this did
                # before the association existed.
                logger.exception("could not resolve documents for %s", thread_id)
        route, route_note = await decide_route(request, thread_id, user_id)
        to_cloud = route["answer_with"] == "cloud"

        async def run_turn(controller: RunController):
            tool_calls = {}
            # (message id, chunk index) -> call id, for providers that stream a
            # tool call in pieces: only the first piece carries the id and name.
            call_ids_by_index: dict = {}
            emitted_text = ""
            cloud_tokens = {"input": 0, "output": 0}
            if route_note:
                controller.append_text(route_note)

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

            document_block = render_document_block(documents)

            configurable = {
                "thread_id": thread_id,
                # For tools that store per-user data (images).
                "user_id": user_id,
                "system": request.system,
                "frontend_tools": request.tools,
                **route,
            }

            async def consume(graph_input, configurable: dict) -> None:
                """Stream one graph run into the controller."""
                nonlocal emitted_text
                async for msg, metadata in graph.astream(
                    graph_input,
                    {"configurable": configurable},
                    stream_mode="messages",
                ):
                    if isinstance(msg, ToolMessage):
                        tool_controller = tool_calls.get(msg.tool_call_id)
                        if tool_controller is not None:
                            tool_controller.set_result(str(msg.content))
                        continue

                    if isinstance(msg, AIMessageChunk) or isinstance(msg, AIMessage):
                        streamed = is_streamed_delta(msg, metadata)
                        if streamed:
                            # A cloud model streams deltas: each piece is new
                            # text, never the whole reply so far.
                            delta = content_to_text(msg.content)
                            if delta:
                                controller.append_text(delta)
                                emitted_text += delta
                            usage = getattr(msg, "usage_metadata", None) or {}
                            cloud_tokens["input"] += usage.get("input_tokens", 0) or 0
                            cloud_tokens["output"] += usage.get("output_tokens", 0) or 0
                        else:
                            append_deduped_text(content_to_text(msg.content))

                        for chunk in getattr(msg, "tool_call_chunks", []) or []:
                            idx = chunk.get("index")
                            call_id = chunk.get("id")
                            name = chunk.get("name")
                            args = chunk.get("args") or ""

                            # Ollama delivers each call whole, with its id. A
                            # streaming provider (Claude) sends the id and name
                            # in the first piece only; later pieces carry just
                            # the index and more argument text.
                            if call_id and name:
                                call_ids_by_index[(msg.id, idx)] = call_id
                                if call_id not in tool_calls:
                                    tool_calls[call_id] = await controller.add_tool_call(
                                        name, call_id
                                    )
                            else:
                                call_id = call_ids_by_index.get((msg.id, idx))
                                if call_id is None:
                                    continue

                            if args:
                                tool_calls[call_id].append_args_text(str(args))

            try:
                try:
                    await consume({"messages": inputs, "documents": document_block}, configurable)
                except Exception as exc:
                    if not (to_cloud and is_cloud_failure(exc)):
                        raise
                    # Claude failed on its side (bad key, limits, outage): say
                    # so plainly and let the local model answer the same turn.
                    # Re-running with no input resumes from the checkpoint, so
                    # the step that failed runs again, now locally.
                    logger.warning("cloud turn failed, answering locally: %s", exc)
                    controller.append_text(cloud_failure_note(exc))
                    emitted_text = ""
                    await consume(None, {**configurable, "answer_with": "local"})
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

            if to_cloud and (cloud_tokens["input"] or cloud_tokens["output"]):
                await record_cloud_turn(controller, route, thread_id, user_id, cloud_tokens)

            # Outside the except, so a turn that failed still counts: it
            # happened, it is in the transcript, and the chat should sort as
            # recently used. Failing to record it must not break the answer
            # that was just streamed.
            try:
                await chat_store.touch_thread(thread_id, user_id)
            except Exception:
                logger.exception("could not touch thread %s", thread_id)

        async def run(controller: RunController):
            asyncio.create_task(note_reservation_conflict(thread_id))
            heartbeat = asyncio.create_task(keep_alive(controller))
            try:
                await run_turn(controller)
            finally:
                heartbeat.cancel()

        return DataStreamResponse(create_run(run))

    app.add_api_route(path, chat_completions, methods=["POST"])
