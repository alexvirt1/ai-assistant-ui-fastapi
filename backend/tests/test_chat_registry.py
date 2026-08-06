"""The pure logic behind the chat list: titles, and restoring a transcript.

Postgres-touching code is exercised by hand against the dev database; what
lives here is what can go subtly wrong without anyone noticing - a title that
is blank, a restored thread whose tool cards never resolve, a system prompt
leaking into the visible conversation.
"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.chats.messages import content_to_text, first_user_text, to_core_messages
from app.chats.store import TITLE_MAX_CHARS, derive_title
from app.identity import DEFAULT_USER_ID, single_user_id


class TestDeriveTitle:
    def test_uses_the_first_non_blank_line(self):
        assert derive_title("\n\n  Why is the sky blue?  \n more") == "Why is the sky blue?"

    def test_empty_message_yields_empty_title(self):
        assert derive_title("   \n\n ") == ""

    def test_long_title_is_truncated_with_an_ellipsis(self):
        title = derive_title("word " * 40)
        assert len(title) <= TITLE_MAX_CHARS
        assert title.endswith("…")

    def test_truncation_does_not_split_a_word(self):
        title = derive_title(
            "Explain the difference between authentication and authorization please"
        )
        assert not title.rstrip("…").endswith(("authoriz", "authori"))
        assert title.rstrip("…").rstrip() == title.rstrip("…").rstrip()

    def test_a_single_long_word_is_still_cut_to_the_limit(self):
        # No space to break on: the guard against mid-word cuts must not stop
        # this from being truncated at all.
        title = derive_title("A" * 200)
        assert len(title) <= TITLE_MAX_CHARS

    def test_cyrillic_counts_as_characters_not_bytes(self):
        # A 60-character Russian title is 120 bytes; truncating on bytes would
        # cut it in half, and could split a character.
        title = derive_title("Кто такой Шиншин и почему он важен для сюжета романа")
        assert title == "Кто такой Шиншин и почему он важен для сюжета романа"


class TestFirstUserText:
    def test_finds_the_earliest_human_turn(self):
        messages = [
            SystemMessage("you are a helpful assistant"),
            HumanMessage(content=[{"type": "text", "text": "first"}]),
            AIMessage(content="answer"),
            HumanMessage(content="second"),
        ]
        assert first_user_text(messages) == "first"

    def test_no_human_turn_yields_empty(self):
        assert first_user_text([AIMessage(content="hi")]) == ""


class TestContentToText:
    def test_plain_string(self):
        assert content_to_text("hello") == "hello"

    def test_part_list(self):
        assert content_to_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "ab"

    def test_non_text_parts_are_ignored(self):
        assert content_to_text([{"type": "image_url", "image_url": "x"}]) == ""


class TestToCoreMessages:
    def test_round_trips_a_simple_exchange(self):
        restored = to_core_messages(
            [
                HumanMessage(content=[{"type": "text", "text": "hi"}]),
                AIMessage(content="hello"),
            ]
        )
        assert restored == [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ]

    def test_drops_the_system_prompt(self):
        # It is rebuilt server-side per call and was never part of what the
        # user saw; restoring it would put the tool instructions on screen.
        restored = to_core_messages(
            [SystemMessage("secret instructions"), HumanMessage(content="hi")]
        )
        assert [m["role"] for m in restored] == ["user"]

    def test_tool_result_is_folded_into_its_call(self):
        # Emitted as a separate message it would render as a tool card that
        # never resolves - a permanent spinner in a finished conversation.
        restored = to_core_messages(
            [
                HumanMessage(content="what time is it?"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "call_1", "name": "current_time", "args": {"tz": "UTC"}}
                    ],
                ),
                ToolMessage(content="12:00", tool_call_id="call_1"),
                AIMessage(content="It is noon."),
            ]
        )

        assert [m["role"] for m in restored] == ["user", "assistant"]
        call = restored[1]["content"][0]
        assert call == {
            "type": "tool-call",
            "toolCallId": "call_1",
            "toolName": "current_time",
            "args": {"tz": "UTC"},
            "result": "12:00",
        }

    def test_a_tool_turn_is_one_message_not_two(self):
        # REGRESSION: every answer that used a tool came back with an empty
        # assistant bubble above it. The tool-calling AIMessage became its own
        # message, and a completed tool call renders nothing by design, so the
        # bubble had an avatar and no content. Live, the same turn is one
        # message with the parts appended to it.
        restored = to_core_messages(
            [
                HumanMessage(content="what was invented in 2025?"),
                AIMessage(
                    content="",
                    tool_calls=[{"id": "c1", "name": "web_search", "args": {"query": "2025"}}],
                ),
                ToolMessage(content="results", tool_call_id="c1"),
                AIMessage(content="In 2025, several inventions…"),
            ]
        )

        assert [m["role"] for m in restored] == ["user", "assistant"]
        assert [p["type"] for p in restored[1]["content"]] == ["tool-call", "text"]

    def test_several_tool_rounds_still_make_one_message(self):
        restored = to_core_messages(
            [
                HumanMessage(content="compare them"),
                AIMessage(content="", tool_calls=[{"id": "c1", "name": "web_search", "args": {}}]),
                ToolMessage(content="first", tool_call_id="c1"),
                AIMessage(content="", tool_calls=[{"id": "c2", "name": "fetch_page", "args": {}}]),
                ToolMessage(content="second", tool_call_id="c2"),
                AIMessage(content="Here is the comparison."),
            ]
        )

        assert [m["role"] for m in restored] == ["user", "assistant"]
        assert [p["type"] for p in restored[1]["content"]] == [
            "tool-call",
            "tool-call",
            "text",
        ]

    def test_separate_turns_stay_separate(self):
        # The merge must stop at the next user turn, or a whole conversation
        # would collapse into a single answer.
        restored = to_core_messages(
            [
                HumanMessage(content="first question"),
                AIMessage(content="first answer"),
                HumanMessage(content="second question"),
                AIMessage(content="second answer"),
            ]
        )
        assert [m["role"] for m in restored] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]

    def test_a_call_without_a_result_keeps_no_result_key(self):
        # A run cancelled mid-tool leaves the call unanswered; inventing an
        # empty result would render as a tool that returned nothing.
        restored = to_core_messages(
            [
                HumanMessage(content="search it"),
                AIMessage(
                    content="answered anyway",
                    tool_calls=[{"id": "call_1", "name": "search_document", "args": {}}],
                ),
            ]
        )
        assert "result" not in restored[1]["content"][1]

    def test_a_turn_that_only_called_tools_is_dropped(self):
        # A run cancelled between the tool result and the answer. Every part it
        # has renders nothing, so keeping it is the blank bubble again.
        restored = to_core_messages(
            [
                HumanMessage(content="search it"),
                AIMessage(content="", tool_calls=[{"id": "c1", "name": "web_search", "args": {}}]),
                ToolMessage(content="results", tool_call_id="c1"),
            ]
        )
        assert [m["role"] for m in restored] == ["user"]

    def test_text_and_tool_call_in_one_message_keep_their_order(self):
        restored = to_core_messages(
            [
                AIMessage(
                    content="let me check",
                    tool_calls=[{"id": "c", "name": "t", "args": {}}],
                )
            ]
        )
        assert [p["type"] for p in restored[0]["content"]] == ["text", "tool-call"]

    def test_empty_messages_are_skipped(self):
        restored = to_core_messages(
            [AIMessage(content=""), HumanMessage(content=""), AIMessage(content="ok")]
        )
        assert restored == [
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}
        ]

    def test_orphan_tool_message_does_not_become_a_message(self):
        # Its calling AIMessage was trimmed or lost; on its own it is not
        # something the user ever saw as a turn.
        assert to_core_messages([ToolMessage(content="stale", tool_call_id="x")]) == []


class TestIdentity:
    def test_defaults_to_alice(self):
        assert single_user_id() == DEFAULT_USER_ID == "alice"

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("SINGLE_USER_ID", "bob")
        assert single_user_id() == "bob"

    def test_blank_override_falls_back_rather_than_owning_nothing(self, monkeypatch):
        # An empty SINGLE_USER_ID would otherwise scope every chat to "", which
        # is a user id no one can ever be again.
        monkeypatch.setenv("SINGLE_USER_ID", "")
        assert single_user_id() == "alice"
