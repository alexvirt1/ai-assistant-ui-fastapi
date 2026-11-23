"""Converting the frontend's messages for LangGraph."""

from app.add_langgraph_route import LanguageModelUserMessage, convert_to_langchain_messages
from app.chats.messages import first_user_text


def test_nul_characters_are_dropped():
    # A binary file sent as a text attachment (a phone photo in 1.9.4) carried
    # NUL bytes; Postgres refused the chat's title and the chat was never listed.
    message = LanguageModelUserMessage(role="user", content=[
        {"type": "text", "text": "Make him a pirate"},
        {"type": "text", "text": "<attachment name=photo.webp>\nRIFF\x00\x00WEBP\n</attachment>"},
    ])
    [human] = convert_to_langchain_messages([message])
    text = first_user_text([human])
    assert "\x00" not in text
    assert text.startswith("Make him a pirate") and "RIFFWEBP" in text
