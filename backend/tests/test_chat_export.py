"""Exporting a conversation as a PDF.

Text is checked by extracting it back out of the PDF with pypdf, so these
tests see what a reader of the file would, not what was handed to fpdf2.
"""

import base64
import io
import re
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from PIL import Image
from pypdf import PdfReader

from app.chats import export, routes
from app.chats.export import (
    Diagram,
    content_disposition,
    markdown_to_html,
    pdf_filename,
    render_chat_pdf,
)
from app.chats.store import ChatThread
from app.identity import DEFAULT_USER_ID


def text_of(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    # Extraction puts line breaks where the layout wrapped; collapse them so
    # assertions do not depend on where a line happened to end.
    return re.sub(r"\s+", " ", text)


def said(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def png(width: int = 30, height: int = 20) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, format="PNG")
    return out.getvalue()


def images_in(pdf: bytes) -> int:
    return sum(len(page.images) for page in PdfReader(io.BytesIO(pdf)).pages)


FLOW = "graph TD\n  A[Start] --> B[Done]\n"


TABLE = (
    "Here is the comparison:\n\n"
    "| Model | Size | Tools |\n"
    "|:------|-----:|:-----:|\n"
    "| qwen3 | 8B | yes |\n"
    "| gemma4 | 12B | no |\n"
)


class TestRender:
    def test_is_a_pdf_with_title_and_both_sides(self):
        pdf = render_chat_pdf(
            "Capital of France",
            [said("user", "What is the capital of France?"), said("assistant", "**Paris.**")],
        )
        assert pdf.startswith(b"%PDF-")
        text = text_of(pdf)
        assert "Capital of France" in text
        assert "What is the capital of France?" in text
        assert "Paris." in text
        assert "YOU" in text and "ASSISTANT" in text

    def test_sets_document_metadata(self):
        reader = PdfReader(io.BytesIO(render_chat_pdf("Kafka retention", [])))
        assert reader.metadata.title == "Kafka retention"

    def test_header_says_when_and_how_many_questions(self):
        when = datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
        pdf = render_chat_pdf(
            "t",
            [said("user", "a"), said("assistant", "b"), said("user", "c")],
            exported_at=when,
        )
        assert "Exported 2026-10-09 14:30 · 2 questions" in text_of(pdf)

    def test_renders_a_table_as_cells_not_pipes(self):
        text = text_of(render_chat_pdf("t", [said("assistant", TABLE)]))
        for cell in ("Model", "Size", "Tools", "qwen3", "8B", "gemma4", "12B"):
            assert cell in text
        assert "|" not in text

    def test_cyrillic_survives(self):
        # The core PDF fonts are Latin-1; without the bundled font this
        # came out blank.
        text = text_of(render_chat_pdf("Чат", [said("user", "Кто такой Шиншин?")]))
        assert "Кто такой Шиншин?" in text
        assert "Чат" in text

    def test_user_text_is_shown_as_typed_not_as_markdown(self):
        text = text_of(render_chat_pdf("t", [said("user", "is 2*3*4 = 24? # not a heading")]))
        assert "2*3*4 = 24? # not a heading" in text

    def test_names_tools_without_dumping_their_results(self):
        # A document search returns ~11 000 tokens of passages.
        message = {
            "role": "assistant",
            "content": [
                {"type": "tool-call", "toolCallId": "c1", "toolName": "search_document",
                 "args": {"q": "x"}, "result": "PASSAGE " * 500},
                {"type": "tool-call", "toolCallId": "c2", "toolName": "web_search",
                 "args": {}, "result": "r"},
                {"type": "text", "text": "Found it."},
            ],
        }
        text = text_of(render_chat_pdf("t", [message]))
        assert "Used search_document, web_search" in text
        assert "Found it." in text
        assert "PASSAGE" not in text

    def test_a_long_conversation_spans_pages_with_numbers(self):
        messages = [said("user", f"Question {i}") for i in range(80)]
        reader = PdfReader(io.BytesIO(render_chat_pdf("t", messages)))
        assert len(reader.pages) > 1
        assert f"1 / {len(reader.pages)}" in reader.pages[0].extract_text()

    def test_an_empty_conversation_still_exports(self):
        assert "no messages" in text_of(render_chat_pdf("t", []))

    def test_an_answer_that_will_not_format_falls_back_to_plain_text(self, monkeypatch):
        monkeypatch.setattr(
            export, "_blocks", lambda text: (_ for _ in ()).throw(ValueError("bad"))
        )
        text = text_of(render_chat_pdf("t", [said("assistant", "still **here**")]))
        assert "still **here**" in text

    def test_unrenderable_glyphs_do_not_fail_the_export(self):
        # DejaVu has no emoji or CJK; they drop out, the rest stays.
        text = text_of(render_chat_pdf("t", [said("assistant", "Done 🎉 中文 ok")]))
        assert "Done" in text and "ok" in text


class TestAttachments:
    """Attached files are named, not reprinted. Measured on a real chat: two
    inlined books made a 987-page PDF that took 43 s."""

    def test_an_inline_file_becomes_one_line(self):
        body = "Summarise this<attachment name=notes.txt>\n" + "BOOKTEXT " * 5000 + "\n</attachment>"
        text = text_of(render_chat_pdf("t", [said("user", body)]))
        assert "Summarise this" in text
        assert "[Attached file: notes.txt, 45,000 characters]" in text
        assert "BOOKTEXT" not in text

    def test_an_indexed_document_reference_becomes_one_line(self):
        body = (
            "What does it say?\n"
            '<attached-document id="d1" name="Kafka guide.pdf" sections="12">\n'
            "This document is too large to include here. Use search_document.\n"
            "</attached-document>"
        )
        text = text_of(render_chat_pdf("t", [said("user", body)]))
        assert "[Attached document: Kafka guide.pdf]" in text
        assert "search_document" not in text

    def test_a_cut_off_attachment_is_still_summarised(self):
        body = "Read<attachment name=a.txt>\nNEVERCLOSED " * 1
        text = text_of(render_chat_pdf("t", [said("user", body)]))
        assert "[Attached file: a.txt" in text
        assert "NEVERCLOSED" not in text

    def test_several_attachments_each_get_a_line(self):
        body = (
            "Compare<attachment name=a.txt>\nA\n</attachment>"
            "<attachment name=b.txt>\nBB\n</attachment>"
        )
        text = text_of(render_chat_pdf("t", [said("user", body)]))
        assert "[Attached file: a.txt, 1 character]" in text
        assert "[Attached file: b.txt, 2 characters]" in text


class TestMarkdown:
    def test_images_become_alt_text_and_are_never_fetched(self):
        # fpdf2 downloads <img src> itself, which would let model output make
        # the server request any URL - LAN addresses included.
        html = markdown_to_html("![diagram](http://192.168.1.1/admin.png)")
        assert "<img" not in html
        assert "192.168.1.1" not in html
        assert "[diagram]" in html

    def test_an_image_in_a_pdf_does_not_trigger_a_request(self, monkeypatch):
        import urllib.request

        def no_network(*args, **kwargs):
            raise AssertionError("the export tried to fetch a URL")

        monkeypatch.setattr(urllib.request, "urlopen", no_network)
        render_chat_pdf("t", [said("assistant", "![x](http://example.com/x.png)")])

    def test_raw_html_is_text_not_markup(self):
        html = markdown_to_html("<script>alert(1)</script> and <b>bold</b>")
        assert "<script>" not in html and "<b>" not in html
        assert "&lt;script&gt;" in html

    def test_code_keeps_its_angle_brackets(self):
        text = text_of(render_chat_pdf(
            "t", [said("assistant", "```python\nif x < 3 and y > 2:\n    pass\n```")]
        ))
        assert "if x < 3 and y > 2:" in text


class TestMatchesTheChat:
    """The PDF parses markdown the way the chat does (CommonMark + GFM)."""

    @pytest.mark.parametrize(
        "source",
        # Indented to the parent's text, as CommonMark (and so the chat) nests:
        # two spaces under "- ", three under "1. ". Python-Markdown wanted four.
        ["- one\n  - a\n  - b\n- two", "1. one\n   - a\n   - b\n2. two"],
    )
    def test_a_list_indented_to_its_parent_nests(self, source):
        html = markdown_to_html(source)
        assert re.search(r"<li>one\s*<ul>\s*<li>a</li>", html)

    def test_double_tilde_strikes_through(self):
        assert "<s>wrong</s>" in markdown_to_html("~~wrong~~ right")

    def test_column_alignment_reaches_the_cells(self):
        html = markdown_to_html(TABLE)
        assert '<th align="right">Size</th>' in html
        assert '<td align="center">yes</td>' in html
        # Unaligned columns are left, not fpdf2's default of justified.
        assert '<td align="left">qwen3</td>' in html
        assert "style=" not in html

    def test_a_mixed_format_cell_is_flattened_and_a_plain_one_is_kept(self):
        html = markdown_to_html(
            "| a | b |\n|---|---|\n| good at `tool calls` | **bold** |"
        )
        assert '<td align="left">good at tool calls</td>' in html
        assert "<strong>bold</strong>" in html

    def test_a_table_with_code_in_a_cell_does_not_fall_back(self, caplog):
        # fpdf2 raises on a cell with two runs of text; that used to drop the
        # whole answer to plain text, pipes and all.
        text = text_of(render_chat_pdf(
            "t", [said("assistant", "| cmd | note |\n|---|---|\n| `ls -la` | lists `all` files |")]
        ))
        assert "could not format" not in caplog.text
        assert "lists all files" in text
        assert "|" not in text


class TestMath:
    """LaTeX is rendered, not copied through as source."""

    def test_inline_math_reads_as_math(self):
        text = text_of(render_chat_pdf(
            "t", [said("assistant", r"At $v = 0.99c$ the factor is $\gamma = \frac{1}{\sqrt{1-\beta^2}}$.")]
        ))
        assert "v = 0.99c" in text
        assert "γ = 1/√(1 − β" in text
        assert "$" not in text and "\\frac" not in text

    def test_display_math_is_typeset_as_an_image(self, monkeypatch):
        typeset = []
        real = export.display_svg

        def spy(tex, size):
            typeset.append(tex.strip())
            return real(tex, size)

        monkeypatch.setattr(export, "display_svg", spy)
        pdf = render_chat_pdf(
            "t", [said("assistant", "Before\n\n$$\n\\frac{vc}{c - v}\n$$\n\nAfter")]
        )
        assert typeset == [r"\frac{vc}{c - v}"]
        text = text_of(pdf)
        # The formula is drawn, not written: none of its source is text.
        assert "Before" in text and "After" in text
        assert "frac" not in text and "$$" not in text

    def test_malformed_display_math_shows_its_source(self):
        text = text_of(render_chat_pdf("t", [said("assistant", "$$\n\\frac{a}{\n$$")]))
        assert "\\frac{a}{" in text

    def test_display_math_in_a_list_stays_in_the_list(self):
        html = markdown_to_html("1. first\n\n   $$x^2$$\n\n2. second")
        assert '<p align="center"><i>x</i><sup>2</sup></p>' in html
        assert html.count("<li>") == 2

    def test_math_in_a_table_cell_is_plain_and_does_not_fall_back(self, caplog):
        text = text_of(render_chat_pdf(
            "t", [said("assistant", "| q | f |\n|---|---|\n| speed | $\\frac{vc}{c-v}$ |")]
        ))
        assert "could not format" not in caplog.text
        assert "vc/(c − v)" in text

    def test_a_loose_list_keeps_its_numbers(self):
        # Blank lines between items wrap each in <p>, and fpdf2 dropped the
        # marker for a paragraph - the numbers vanished.
        text = text_of(render_chat_pdf("t", [said("assistant", "1. one\n\n2. two\n\n3. three")]))
        assert "1. one" in text and "2. two" in text and "3. three" in text


class TestDiagrams:
    def test_a_diagram_with_an_image_is_drawn_not_written(self):
        pdf = render_chat_pdf(
            "t",
            [said("assistant", "Before\n\n```mermaid\n" + FLOW + "```\n\nAfter")],
            diagrams={FLOW.strip(): Diagram(png(), 300, 200)},
        )
        text = text_of(pdf)
        assert images_in(pdf) == 1
        assert "Before" in text and "After" in text
        assert "A[Start]" not in text

    def test_a_diagram_without_an_image_shows_its_source(self):
        # Exported without the browser's images (a plain GET, or one that
        # would not draw): the source is better than nothing.
        pdf = render_chat_pdf("t", [said("assistant", "```mermaid\n" + FLOW + "```")])
        assert images_in(pdf) == 0
        assert "A[Start] --> B[Done]" in text_of(pdf)

    def test_an_image_for_other_source_is_not_used(self):
        pdf = render_chat_pdf(
            "t",
            [said("assistant", "```mermaid\n" + FLOW + "```")],
            diagrams={"graph LR\n  X --> Y": Diagram(png(), 300, 200)},
        )
        assert images_in(pdf) == 0
        assert "A[Start]" in text_of(pdf)

    def test_other_code_blocks_are_never_replaced(self):
        pdf = render_chat_pdf(
            "t",
            [said("assistant", "```python\n" + FLOW + "```")],
            diagrams={FLOW.strip(): Diagram(png(), 300, 200)},
        )
        assert images_in(pdf) == 0

    def test_a_huge_diagram_fits_one_page(self):
        pdf = render_chat_pdf(
            "t",
            [said("assistant", "```mermaid\n" + FLOW + "```")],
            diagrams={FLOW.strip(): Diagram(png(), 4000, 9000)},
        )
        # Shrunk onto the page after the header rather than overflowing it.
        assert images_in(pdf) == 1
        assert len(PdfReader(io.BytesIO(pdf)).pages) == 2


class TestFilename:
    @pytest.mark.parametrize(
        "title, expected",
        [
            ("Capital of France", "Capital of France.pdf"),
            ('a/b\\c:d*e?f"g<h>i|j', "a b c d e f g h i j.pdf"),
            ("", "chat.pdf"),
            ("...", "chat.pdf"),
            ("Кто такой Шиншин?", "Кто такой Шиншин.pdf"),
        ],
    )
    def test_is_safe_for_a_filesystem(self, title, expected):
        assert pdf_filename(title) == expected

    def test_long_titles_are_capped(self):
        assert len(pdf_filename("word " * 100)) <= 84

    def test_header_has_ascii_fallback_and_utf8_name(self):
        header = content_disposition("Кто такой Шиншин.pdf")
        assert header.startswith("attachment; ")
        assert 'filename="' in header
        header.split('filename="')[1].split('"')[0].encode("ascii")
        assert "filename*=UTF-8''%D0%9A" in header

    def test_ascii_fallback_has_no_runs_of_spaces(self):
        header = content_disposition("Найди на privet.fun что пишут.pdf")
        assert 'filename="privet.fun.pdf"' in header

    def test_ascii_fallback_is_never_empty(self):
        assert 'filename="chat.pdf"' in content_disposition("中文.pdf")


class FakeGraph:
    def __init__(self, messages):
        self.messages = messages

    async def aget_state(self, config):
        class State:
            values = {"messages": self.messages}

        return State()


def client_for(monkeypatch, owned: dict[str, str], messages) -> TestClient:
    async def get_thread(thread_id, user_id):
        if thread_id not in owned:
            return None
        now = datetime.now(timezone.utc)
        return ChatThread(thread_id, user_id, owned[thread_id], "", 1, False, now, now)

    monkeypatch.setattr(routes.chat_store, "get_thread", get_thread)
    app = FastAPI()
    app.include_router(routes.make_chats_router(FakeGraph(messages)))
    return TestClient(app)


class TestEndpoint:
    def test_downloads_the_chat_as_a_pdf(self, monkeypatch):
        client = client_for(
            monkeypatch,
            {"t1": "Kafka retention"},
            [
                HumanMessage(content="How long is retention?"),
                AIMessage(content="", tool_calls=[{"id": "c1", "name": "web_search", "args": {}}]),
                ToolMessage(content="results", tool_call_id="c1"),
                AIMessage(content="Seven days by default."),
            ],
        )
        response = client.get("/api/chats/t1/export.pdf")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["cache-control"] == "no-store"
        assert 'filename="Kafka retention.pdf"' in response.headers["content-disposition"]
        text = text_of(response.content)
        assert "How long is retention?" in text
        assert "Used web_search" in text
        assert "Seven days by default." in text

    def test_someone_elses_or_missing_chat_is_404(self, monkeypatch):
        # get_thread returns None for both, so the two are indistinguishable -
        # a guessed id learns nothing.
        client = client_for(monkeypatch, {}, [HumanMessage(content="secret")])
        response = client.get("/api/chats/t1/export.pdf")
        assert response.status_code == 404
        assert b"secret" not in response.content

    def test_an_untitled_chat_gets_a_name(self, monkeypatch):
        client = client_for(monkeypatch, {"t1": ""}, [HumanMessage(content="hi")])
        response = client.get("/api/chats/t1/export.pdf")
        assert 'filename="Untitled chat.pdf"' in response.headers["content-disposition"]
        assert "Untitled chat" in text_of(response.content)


class TestEndpointWithDiagrams:
    def diagram(self, data: bytes) -> dict:
        return {
            "source": FLOW,
            "png": base64.b64encode(data).decode(),
            "width": 300,
            "height": 200,
        }

    def test_posted_diagrams_are_drawn(self, monkeypatch):
        client = client_for(
            monkeypatch, {"t1": "Flow"}, [AIMessage(content="```mermaid\n" + FLOW + "```")]
        )
        response = client.post(
            "/api/chats/t1/export.pdf", json={"diagrams": [self.diagram(png())]}
        )
        assert response.status_code == 200
        assert images_in(response.content) == 1
        assert "A[Start]" not in text_of(response.content)

    @pytest.mark.parametrize("data", [b"not an image", b"\x89PNG\r\n\x1a\nbroken"])
    def test_something_other_than_a_png_is_refused(self, monkeypatch, data):
        client = client_for(monkeypatch, {"t1": "Flow"}, [AIMessage(content="hi")])
        response = client.post(
            "/api/chats/t1/export.pdf", json={"diagrams": [self.diagram(data)]}
        )
        assert response.status_code == 422

    def test_a_jpeg_is_refused(self, monkeypatch):
        out = io.BytesIO()
        Image.new("RGB", (4, 4)).save(out, format="JPEG")
        client = client_for(monkeypatch, {"t1": "Flow"}, [AIMessage(content="hi")])
        response = client.post(
            "/api/chats/t1/export.pdf", json={"diagrams": [self.diagram(out.getvalue())]}
        )
        assert response.status_code == 422

    def test_someone_elses_chat_is_still_404(self, monkeypatch):
        client = client_for(monkeypatch, {}, [HumanMessage(content="secret")])
        response = client.post(
            "/api/chats/t1/export.pdf", json={"diagrams": [self.diagram(png())]}
        )
        assert response.status_code == 404


def test_default_user_is_what_the_endpoint_checks_against(monkeypatch):
    seen = {}

    async def get_thread(thread_id, user_id):
        seen["user"] = user_id
        return None

    monkeypatch.setattr(routes.chat_store, "get_thread", get_thread)
    app = FastAPI()
    app.include_router(routes.make_chats_router(FakeGraph([])))
    TestClient(app).get("/api/chats/t1/export.pdf")
    assert seen["user"] == DEFAULT_USER_ID
