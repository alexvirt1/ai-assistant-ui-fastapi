import os

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    trim_messages,
)
from langgraph.config import get_config
from langgraph.prebuilt import create_react_agent
from langgraph.prebuilt.chat_agent_executor import AgentState

from ..cloud import models as cloud_models
from ..cloud.history import cloud_view
from ..documents.chunker import count_message_tokens
from ..models import DEFAULT_ROLE, make_chat_model
from ..models.selection import current_tag
from ..tools import compose_prompt, get_enabled_specs
from ..tools.builtin.escalate import ESCALATE_HINT

load_dotenv()


def make_model(role: str = DEFAULT_ROLE):
    """The chat model for a role.

    Construction moved to app/models/factory.py so planner and executor nodes
    can request other roles later. Behaviour for the default role is unchanged
    apart from an explicit keep_alive; OLLAMA_MODEL still overrides it.
    """
    return make_chat_model(role)


# --- hybrid mode -------------------------------------------------------------
#
# Each turn's route comes from the chat route through the run's config:
#   answer_with      "local" or "cloud" - which model answers this turn
#   chat_mode        the chat's mode: "local", "auto" or "cloud"
#   cloud_model      name of the cloud model (app/cloud/models.py)
#   cloud_label      its display name
#   allow_documents  whether the chat's documents may go to the cloud model

# Bound only to the local model in auto mode.
AUTO_ONLY_TOOLS = {"escalate"}
# Bound to a cloud model only when the chat allows its documents to be sent.
DOCUMENT_TOOLS = {"search_document"}

CLOUD_PREAMBLE = (
    "You are {label}. The user's private assistant normally runs a small local model; this "
    "turn has been handed over to you because it needs a stronger model. Answer the user's "
    "latest message directly and completely, in the user's language. Earlier turns are shown "
    "as text only. The user's attached documents are not shared with you unless they allowed "
    "it for this chat.\n\n"
)

# History sent to a cloud model, in tokens: it is paid for on every turn.
CLOUD_HISTORY_MAX_TOKENS = int(os.getenv("CLOUD_HISTORY_MAX_TOKENS", "8000"))


def turn_route() -> dict:
    """This run's hybrid-mode settings (empty outside a graph run)."""
    try:
        config = get_config()
    except RuntimeError:
        return {}
    return dict((config or {}).get("configurable") or {})


def tools_for(route: dict, tools: list) -> list:
    """The tools the model answering this turn may call."""
    to_cloud = route.get("answer_with") == "cloud"
    chosen = []
    for t in tools:
        if t.name in AUTO_ONLY_TOOLS and (to_cloud or route.get("chat_mode") != "auto"):
            continue
        if to_cloud and t.name in DOCUMENT_TOOLS and not route.get("allow_documents"):
            continue
        chosen.append(t)
    return chosen


def make_model_selector(tools: list):
    """The agent's model, re-resolved on every step from the selection.

    A callable rather than a model so a switch takes effect on the next model
    call without rebuilding the graph - including mid-turn, right after the
    switch_model tool returns. Bound models are cached per model and tool set,
    since binding the tool schemas on every step would be repeated work.

    A turn routed to the cloud (hybrid mode) gets the configured cloud model
    with the same tools, minus the ones it may not use (see tools_for).
    """
    bound = {}

    def select_model(state, runtime):
        route = turn_route()
        chosen = tools_for(route, tools)
        names = tuple(sorted(t.name for t in chosen))
        if route.get("answer_with") == "cloud" and route.get("cloud_model"):
            key = ("cloud", route["cloud_model"], names)
            if key not in bound:
                bound[key] = cloud_models.make_chat_model(route["cloud_model"]).bind_tools(chosen)
            return bound[key]
        tag = current_tag()
        key = (tag, names)
        if key not in bound:
            bound[key] = make_chat_model(DEFAULT_ROLE, model=tag).bind_tools(chosen)
        return bound[key]

    return select_model


BASE_PROMPT = (
    "You are a private local assistant. "
    # The frontend renders math with remark-math, which recognises $...$ and
    # $$...$$ only. Left to itself the model often emits \\(...\\) or \\[...\\],
    # which would show up as literal backslashes.
    "Write any mathematics as LaTeX: $...$ for inline math and $$...$$ on its "
    "own lines for displayed equations. Do not use \\( \\) or \\[ \\]. "
    # The frontend draws ```mermaid fences as diagrams (MermaidDiagram.tsx).
    # Without this the model answers "draw a flowchart" with ASCII art or a
    # list of steps. Worded as "when asked or clearly useful" so it does not
    # start decorating every answer with a diagram.
    "When the user asks for a diagram, flowchart, sequence, or other chart, or "
    "one would clearly help, draw it in a ```mermaid code block; the chat "
    "renders it as a diagram."
)

# Token budget for conversation history handed to the model, excluding the
# system prompt and the tool schemas. Kept well under OLLAMA_NUM_CTX so there is
# room for both of those plus the response.
#
# The default is derived from the context window rather than fixed, because the
# two have to move together: raising OLLAMA_NUM_CTX alone leaves the extra
# window unused, and raising this alone overflows it. A third of the window
# matches the ratio this ran at before (3000 of 8192).
HISTORY_MAX_TOKENS = int(
    os.getenv("HISTORY_MAX_TOKENS")
    or int(os.getenv("OLLAMA_NUM_CTX", "8192")) // 3
)


class DocumentAwareState(AgentState, total=False):
    """Agent state plus a block describing any attached documents.

    Carried in state rather than in the conversation because the conversation
    is trimmed. An attached-document reference sent as a chat message sits in
    the *oldest* turn, which is exactly what HISTORY_MAX_TOKENS discards first -
    measured at 121 messages, the reference was gone and the agent could no
    longer reach a document it had been given. Pinned to the system prompt it
    survives any thread length.
    """

    documents: str
    # Reference photos attached to the conversation (render_reference_block).
    references: str


def render_reference_block(references: list) -> str:
    """The reference photos, by id and name, for the system prompt; empty if none.

    The model never gets the image data here: qwen3:8b cannot see images. It
    only needs the ids to pass to generate_image.
    """
    if not references:
        return ""
    lines = [f'- id={r.id} name="{r.name}" {r.width}x{r.height}' for r in references]
    return (
        "Reference photos attached to this conversation:\n" + "\n".join(lines)
        + "\n\nWhen the user wants an image that looks like one of them, call "
        "generate_image with its id in reference_ids; the first id you pass is image 1, "
        "the second image 2, and the prompt names each subject by that number with a "
        "short description. A message that attaches a photo "
        "contains <reference-image id=\"...\" name=\"...\" />; unless the user says "
        "otherwise, use the photos of their latest message that has any. You cannot "
        "see these photos; never describe what is in them unless the user told you."
    )


def render_document_block(documents: list[dict]) -> str:
    """The reminder appended to the system prompt, or empty if none."""
    if not documents:
        return ""
    lines = [
        f'- id={d.get("id")} name="{d.get("name", "?")}" '
        f'sections={d.get("sections", "?")}'
        for d in documents
    ]
    return (
        "Documents attached to this conversation:\n"
        + "\n".join(lines)
        + "\n\nTheir text is NOT in this conversation. Call search_document with "
        "the matching id to answer ANY question about them, including follow-up "
        "questions - passages already in the conversation were retrieved for an "
        "earlier question and will not answer a new one. Measured: with a large "
        "set of passages from a previous question filling the context, the model "
        "reached for web_search instead and answered from the internet. Never "
        "use web_search or fetch_page for a question about an attached document, "
        "and never tell the user you lack the document or ask for its id - it is "
        "listed above."
    )


STALE_TOOL_RESULT = (
    "[Passages from an earlier question in this conversation. They have been "
    "removed because they were retrieved for that question, not this one. "
    "Call search_document again to answer the current question.]"
)


def _retire_stale_tool_results(messages: list) -> list:
    """Blank tool results from turns before the current one.

    A document search returns ~11 000 tokens of passages. Left in the history
    they are both the largest thing in the context and stale by construction -
    chosen to answer a question that has already been answered. Measured with
    one such result in the history, the agent stopped calling search_document
    for the next question: it answered from those passages, reached for
    web_search instead, and on one run invented a section number that appeared
    nowhere in them.

    The message itself is kept and only its content replaced. Dropping it would
    orphan the AIMessage tool call that produced it, which providers reject.

    Only results from *earlier* turns are retired; everything from the current
    human turn onward is what the model is working with right now.
    """
    last_human = -1
    for i, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            last_human = i
    if last_human <= 0:
        return messages

    out = list(messages)
    for i in range(last_human):
        message = out[i]
        if isinstance(message, ToolMessage) and message.content != STALE_TOOL_RESULT:
            out[i] = ToolMessage(
                content=STALE_TOOL_RESULT,
                tool_call_id=message.tool_call_id,
                name=getattr(message, "name", None),
            )
    return out


def _minimal_tail(messages: list) -> list:
    """Smallest suffix still valid to send when the budget trims to nothing.

    Taking just the final message is not safe: mid-ReAct-loop that message is
    often a ToolMessage, and a tool result whose calling AIMessage is absent is
    rejected by the provider. A tool returning a payload larger than the whole
    budget is enough to reach this.

    Prefers the last human turn onward. Exceeding the budget here is deliberate
    — an over-long prompt gets truncated by the provider, an invalid one errors.
    """
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i:]

    # No human turn in range: keep a trailing tool result with its call.
    last = messages[-1] if messages else None
    if isinstance(last, ToolMessage):
        for i in range(len(messages) - 2, -1, -1):
            message = messages[i]
            if isinstance(message, AIMessage) and any(
                call["id"] == last.tool_call_id for call in message.tool_calls or []
            ):
                return messages[i:]
    return messages[-1:]


def make_prompt(system_prompt: str):
    """Build the callable that assembles the model's input from graph state.

    The full conversation lives in the checkpointer and grows without bound,
    while the context window does not. Ollama truncates an oversized prompt
    from the front — exactly where the system prompt sits — so a long thread
    silently loses its tool instructions while stale assistant answers, being
    recent, survive. The model then repeats old answers instead of calling
    tools.

    Trimming here rather than in the route matters: the route only passes the
    newest human message, so by the time a request arrives the history has
    already been loaded from the checkpointer inside the graph. This runs on
    every model call, including each step of the ReAct loop.

    Trimming affects only what the model sees. Nothing is deleted from the
    checkpointer, so the stored transcript stays complete.
    """
    system = SystemMessage(system_prompt)

    def prompt(state) -> list:
        messages = state["messages"]
        # Appended per call, so an attached document stays reachable however
        # long the thread grows.
        attached = state.get("documents") if hasattr(state, "get") else None
        references = state.get("references") if hasattr(state, "get") else None
        route = turn_route()

        if route.get("answer_with") == "cloud":
            return cloud_prompt(system_prompt, messages, attached, route, references)

        base = system_prompt
        if route.get("chat_mode") == "auto":
            base = f"{system_prompt}\n- {ESCALATE_HINT}"
        extras = "\n\n".join(block for block in (attached, references) if block)
        active_system = (
            SystemMessage(f"{base}\n\n{extras}") if extras
            else (system if base is system_prompt else SystemMessage(base))
        )
        # Before trimming, not after: retiring these frees the single largest
        # block in the context, so the budget goes to actual conversation.
        messages = _retire_stale_tool_results(messages)
        trimmed = trim_messages(
            messages,
            max_tokens=HISTORY_MAX_TOKENS,
            # Script-aware, unlike count_tokens_approximately: on Russian that
            # one undercounts by ~1.94x, so the trimmer kept nearly double its
            # budget and could overflow the real context window. Identical on
            # ASCII, so English threads trim exactly as before.
            token_counter=count_message_tokens,
            strategy="last",
            # Never begin on an orphaned ToolMessage: a tool result whose
            # calling AIMessage was trimmed away is rejected by the provider.
            start_on="human",
            # The system prompt is prepended explicitly, so keep it out of
            # both the window and the budget.
            include_system=False,
            allow_partial=False,
        )
        # A single turn larger than the budget can trim to nothing; sending
        # only a system prompt would lose the user's question entirely.
        if not trimmed:
            trimmed = _minimal_tail(messages)
        return [active_system] + list(trimmed)

    return prompt


def cloud_prompt(system_prompt: str, messages: list, attached: str | None, route: dict,
                 references: str | None = None) -> list:
    """What a cloud model is sent for a handed-over turn.

    Earlier turns as text only (app/cloud/history.py), trimmed to
    CLOUD_HISTORY_MAX_TOKENS; the document block only when the chat allows it.
    Reference photos: always their ids and names (so it can call
    generate_image with them); the images themselves only when the chat allows
    documents, attached to the current question. They come from the run's
    config, not the state, so they are never written to the checkpoint.
    """
    text = CLOUD_PREAMBLE.format(label=route.get("cloud_label") or "a cloud model") + system_prompt
    if attached and route.get("allow_documents"):
        text += f"\n\n{attached}"
    if references:
        text += f"\n\n{references}"
        if route.get("reference_images"):
            text += ("\n\nThe reference photos are attached to the user's latest message, "
                     "in the order listed, so you can see them; describe what matters for "
                     "the image in the generate_image prompt.")
    view = cloud_view(messages)
    trimmed = trim_messages(
        view,
        max_tokens=CLOUD_HISTORY_MAX_TOKENS,
        token_counter=count_message_tokens,
        strategy="last",
        start_on="human",
        include_system=False,
        allow_partial=False,
    )
    out = [SystemMessage(text)] + list(trimmed or _minimal_tail(view))
    images = route.get("reference_images") or []
    if images:
        out = _attach_images(out, images)
    return out


def _attach_images(messages: list, images: list[dict]) -> list:
    """Add images to the last user message, as Anthropic image blocks."""
    from ..chats.messages import content_to_text

    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            blocks = [{"type": "text", "text": content_to_text(messages[i].content)}] + [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": image["data"]}}
                for image in images
            ]
            return messages[:i] + [HumanMessage(content=blocks)] + messages[i + 1:]
    return messages


def build_graph(checkpointer=None):
    specs = get_enabled_specs()
    tools = [spec.tool for spec in specs]
    return create_react_agent(
        model=make_model_selector(tools),
        tools=tools,
        prompt=make_prompt(compose_prompt(BASE_PROMPT, specs)),
        state_schema=DocumentAwareState,
        checkpointer=checkpointer,
    )
