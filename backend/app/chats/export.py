"""A conversation as a single PDF document.

Built from the same replayed transcript the frontend restores from
(to_core_messages), so the PDF shows what the chat shows: one block per turn,
system prompt left out, tool calls folded into the answer that made them.

fpdf2 rather than an HTML-to-PDF engine: WeasyPrint needs Pango and Chromium
needs a browser, neither of which the slim image or the VM has. fpdf2 is pure
Python and renders the subset of HTML that markdown produces - which is all an
answer contains.

Kept free of FastAPI so it can be tested without it.
"""

import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from io import BytesIO
from urllib.parse import quote

from fpdf import FPDF, FontFace, TextStyle
from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin

from .pdf_math import display_svg, inline_html, inline_text

logger = logging.getLogger(__name__)

# DejaVu, bundled rather than looked up on the host: the core PDF fonts are
# Latin-1 only, so a Russian answer would come out as blanks, and the slim
# Docker image has no fonts at all. It has no CJK or emoji glyphs; those are
# dropped from the page, with a warning from fpdf2, rather than failing.
FONT_DIR = Path(__file__).parent / "fonts"
SANS = "DejaVuSans"
MONO = "DejaVuSansMono"
_FONT_FILES = {
    SANS: {
        "": "DejaVuSans.ttf",
        "B": "DejaVuSans-Bold.ttf",
        "I": "DejaVuSans-Oblique.ttf",
        "BI": "DejaVuSans-BoldOblique.ttf",
    },
    MONO: {
        "": "DejaVuSansMono.ttf",
        "B": "DejaVuSansMono-Bold.ttf",
        "I": "DejaVuSansMono-Oblique.ttf",
        "BI": "DejaVuSansMono-BoldOblique.ttf",
    },
}

BODY_SIZE = 10.5
# Matches the spacing write_html gives an answer, so a question and its answer
# read as the same document.
LINE_HEIGHT = 4.6
MUTED = "#6b7280"
# Display math is set a little larger than the text around it, as KaTeX does.
MATH_SIZE = 12.5
PT_TO_MM = 25.4 / 72

# fpdf2's defaults colour headings dark red and bullets bright red; these match
# the chat instead. Sizes are stepped down because an answer's "# Heading" is a
# section inside a turn, not the title of the document.
TAG_STYLES = {
    "h1": TextStyle(font_size_pt=16, font_style="B", t_margin=4, b_margin=1),
    "h2": TextStyle(font_size_pt=14, font_style="B", t_margin=4, b_margin=1),
    "h3": TextStyle(font_size_pt=12.5, font_style="B", t_margin=3, b_margin=1),
    "h4": TextStyle(font_size_pt=11.5, font_style="B", t_margin=3, b_margin=1),
    "h5": TextStyle(font_size_pt=BODY_SIZE, font_style="B", t_margin=2),
    "h6": TextStyle(font_size_pt=BODY_SIZE, font_style="B", t_margin=2),
    # Explicit margins, because fpdf2 otherwise puts a full blank line above
    # every paragraph - including the first in an answer, under its label - and
    # nothing below one, so a table started flush against the sentence that
    # introduced it.
    "p": TextStyle(t_margin=1, b_margin=1.5),
    "ul": TextStyle(t_margin=1, b_margin=2),
    "ol": TextStyle(t_margin=1, b_margin=2),
    "pre": TextStyle(font_family=MONO, font_size_pt=9, t_margin=2, b_margin=2),
    "code": FontFace(family=MONO),
    "blockquote": TextStyle(color=MUTED, l_margin=6, t_margin=2, b_margin=2),
    "a": FontFace(color="#2563eb", emphasis="UNDERLINE"),
}


def _image_as_alt_text(self, tokens, idx, options, env) -> str:
    """Render an image as its alt text.

    fpdf2 fetches an <img src> itself. An answer is model output, so an image
    in it would have the server request any URL the model wrote - including
    addresses on the LAN that only this machine can reach.
    """
    alt = self.renderInlineAsText(tokens[idx].children or [], options, env)
    return f"<em>[{escapeHtml(alt or 'image')}]</em>"


def _math_inline(self, tokens, idx, options, env) -> str:
    return inline_html(tokens[idx].content)


def _math_block_as_text(self, tokens, idx, options, env) -> str:
    """Display math where an image cannot go - inside a list or a quote.

    Top-level display math never reaches this: _rich typesets it as an image.
    """
    return f'<p align="center">{inline_html(tokens[idx].content)}</p>\n'


_ALIGN = re.compile(r"text-align:\s*(left|center|right)")


def _prepare_tables(tokens: list[Token]) -> None:
    """Adjust table cells to what fpdf2 can lay out.

    - Alignment moves from markdown-it's style attribute to the align
      attribute, the only one fpdf2 reads. An unaligned cell is set to left
      explicitly; fpdf2 would otherwise justify it, stretching the spaces of
      a short wrapped note across the whole column.
    - A cell with mixed formatting is reduced to its plain text. fpdf2 lays a
      cell out from a single run: "good at `tool calls`" is two runs and
      raises NotImplementedError, which would send the whole answer to the
      plain-text fallback. A cell that is one run - all bold, all code -
      keeps its formatting.
    """
    in_cell = False
    for token in tokens:
        if token.type in ("th_open", "td_open"):
            in_cell = True
            match = _ALIGN.search(str(token.attrGet("style") or ""))
            token.attrs.pop("style", None)
            token.attrSet("align", match.group(1) if match else "left")
        elif token.type in ("th_close", "td_close"):
            in_cell = False
        elif in_cell and token.type == "inline" and token.children:
            # Math in a cell becomes plain Unicode first: the cell takes no
            # markup, and as text it joins the run count below like any other.
            for i, child in enumerate(token.children):
                if child.type in ("math_inline", "math_inline_double"):
                    as_text = Token("text", "", 0)
                    as_text.content = inline_text(child.content)
                    token.children[i] = as_text
            runs = [c for c in token.children if c.type in ("text", "code_inline") and c.content.strip()]
            if len(runs) > 1:
                plain = Token("text", "", 0)
                plain.content = "".join(
                    " " if c.type in ("softbreak", "hardbreak") else c.content
                    for c in token.children
                    if c.type in ("text", "code_inline", "softbreak", "hardbreak")
                )
                token.children = [plain]


def _tighten_lists(tokens: list[Token]) -> None:
    """Drop the paragraph wrapping the first block of each list item.

    A list with blank lines between its items ("loose", and how models
    usually write them) wraps every item in <p>, and fpdf2 starts a new
    paragraph for it without the bullet or number - the list rendered as
    plain unmarked lines. Unwrapping the first paragraph puts the text where
    the marker is; any further paragraphs in the item stay as they are.
    """
    for i, token in enumerate(tokens[:-1]):
        if token.type == "list_item_open" and tokens[i + 1].type == "paragraph_open":
            opening = tokens[i + 1]
            opening.hidden = True
            # Its closing tag is the next paragraph_close at the same level.
            for later in tokens[i + 2 :]:
                if later.type == "paragraph_close" and later.level == opening.level:
                    later.hidden = True
                    break


def _parser() -> MarkdownIt:
    """markdown-it-py set up to read an answer as the chat does.

    GFM tables and strikethrough, as remark-gfm; $...$ and $$...$$ math, as
    remark-math. The same nesting rules too, so a list indented two or three
    spaces nests here as it does on screen. `html` is off, so raw HTML in an
    answer is shown as text rather than handed to fpdf2 - a stray unclosed
    tag would otherwise derail everything after it.
    """
    md = (
        MarkdownIt("commonmark", {"html": False})
        .enable(["table", "strikethrough"])
        .use(dollarmath_plugin, double_inline=True)
    )
    md.add_render_rule("image", _image_as_alt_text)
    md.add_render_rule("math_inline", _math_inline)
    md.add_render_rule("math_inline_double", _math_inline)
    md.add_render_rule("math_block", _math_block_as_text)
    return md


def _blocks(text: str) -> list[str | tuple[str, str]]:
    """An answer split into HTML runs and top-level display math.

    Display math is typeset as an image, which write_html cannot place, so the
    answer is cut around each formula: HTML before it, the formula, HTML
    after. Only top-level formulas are cut out; one inside a list or a quote
    stays in the HTML as centred text rather than breaking the list in two.
    """
    md = _parser()
    tokens = md.parse(text)
    _prepare_tables(tokens)
    _tighten_lists(tokens)

    blocks: list[str | tuple[str, str]] = []
    run: list[Token] = []
    for token in tokens:
        if token.type == "math_block" and token.level == 0:
            if run:
                blocks.append(md.renderer.render(run, md.options, {}))
                run = []
            blocks.append(("math", token.content))
        else:
            run.append(token)
    if run:
        blocks.append(md.renderer.render(run, md.options, {}))
    return blocks


def markdown_to_html(text: str) -> str:
    """An answer as one HTML string, display math included as centred text."""
    md = _parser()
    tokens = md.parse(text)
    _prepare_tables(tokens)
    _tighten_lists(tokens)
    return md.renderer.render(tokens, md.options, {})


class _ChatPDF(FPDF):
    def __init__(self, title: str):
        super().__init__(format="A4")
        self.chat_title = title
        for family, styles in _FONT_FILES.items():
            for style, filename in styles.items():
                self.add_font(family, style, str(FONT_DIR / filename))
        self.set_margins(18, 18, 18)
        self.set_auto_page_break(True, margin=18)
        self.set_title(title)
        self.set_creator("assistant-ui-langgraph-fastapi")

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font(SANS, size=8)
        self.set_text_color(MUTED)
        self.cell(0, 5, f"{self.page_no()} / {{nb}}", align="C")


def _role_label(pdf: _ChatPDF, label: str, color: str) -> None:
    pdf.ln(3)
    pdf.set_font(SANS, "B", 9)
    pdf.set_text_color(color)
    pdf.cell(0, 5, label.upper(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.set_font(SANS, size=BODY_SIZE)


def _plain(pdf: _ChatPDF, text: str) -> None:
    pdf.set_font(SANS, size=BODY_SIZE)
    # align="L": multi_cell justifies by default, which stretches the spaces of
    # every wrapped line of a question.
    pdf.multi_cell(0, LINE_HEIGHT, text, align="L", new_x="LMARGIN", new_y="NEXT")


def _display_math(pdf: _ChatPDF, tex: str) -> None:
    """A display formula, typeset and centred; its source if it will not typeset."""
    typeset = display_svg(tex, MATH_SIZE)
    if typeset is None:
        # Malformed LaTeX: show what the model wrote rather than nothing.
        pdf.set_font(MONO, size=9)
        pdf.multi_cell(0, LINE_HEIGHT, tex.strip(), align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font(SANS, size=BODY_SIZE)
        return

    svg, width_pt, height_pt = typeset
    width, height = width_pt * PT_TO_MM, height_pt * PT_TO_MM
    if width > pdf.epw:
        # A long equation shrinks to the page rather than running off it.
        width, height = pdf.epw, height * pdf.epw / width
    gap = 2
    # pdf.image does not break the page when given a y, so check by hand.
    if pdf.get_y() + gap + height > pdf.page_break_trigger:
        pdf.add_page()
    y = pdf.get_y() + gap
    pdf.image(BytesIO(svg), x=pdf.l_margin + (pdf.epw - width) / 2, y=y, w=width, h=height)
    pdf.set_xy(pdf.l_margin, y + height + gap)


def _rich(pdf: _ChatPDF, text: str) -> None:
    """An answer's markdown, falling back to plain text if it will not render.

    The export must not fail because one answer contains a construct fpdf2's
    HTML renderer rejects. A failure can leave part of the formatted text on
    the page before the plain copy; a duplicated paragraph is a better outcome
    than no PDF at all.
    """
    try:
        for block in _blocks(text):
            if isinstance(block, tuple):
                _display_math(pdf, block[1])
                continue
            pdf.write_html(
                block,
                font_family=SANS,
                tag_styles=TAG_STYLES,
                table_line_separators=True,
                ul_bullet_char="•",
                li_prefix_color=MUTED,
                warn_on_tags_not_matching=False,
            )
    except Exception:
        logger.exception("could not format an answer for PDF; using plain text")
        pdf.ln(LINE_HEIGHT)
        _plain(pdf, text)


# The delimiters the frontend wraps attachments in (frontend/lib/attachments.ts).
# A closing tag may be missing if the text was cut, so end-of-string closes too.
_ATTACHMENT = re.compile(r"<attachment name=([^>\n]*)>\n?(.*?)(?:\n?</attachment>|\Z)", re.S)
_DOCUMENT_REF = re.compile(
    r'<attached-document\b[^>]*?\bname="([^"]*)"[^>]*>.*?(?:</attached-document>|\Z)', re.S
)


def _summarize_attachments(text: str) -> str:
    """Replace an attached file's contents with a line naming it.

    A text attachment is inlined into the message it was sent with - measured
    on a real chat, two book-length files made a 987-page, 2 MB PDF that took
    43 s to build, almost none of it the conversation. The file is the user's
    already; the export is the conversation about it.
    """
    def file_line(match: re.Match) -> str:
        size = len(match.group(2))
        unit = "character" if size == 1 else "characters"
        return f"\n[Attached file: {match.group(1).strip()}, {size:,} {unit}]\n"

    text = _ATTACHMENT.sub(file_line, text)
    text = _DOCUMENT_REF.sub(lambda m: f"\n[Attached document: {m.group(1)}]\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _tool_line(pdf: _ChatPDF, names: list[str]) -> None:
    pdf.set_font(SANS, "I", 8.5)
    pdf.set_text_color(MUTED)
    pdf.multi_cell(
        0, 4.5, "Used " + ", ".join(names), align="L", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.set_text_color(0)
    pdf.set_font(SANS, size=BODY_SIZE)


def render_chat_pdf(
    title: str, messages: list[dict], exported_at: datetime | None = None
) -> bytes:
    """The conversation as PDF bytes.

    `messages` is to_core_messages output. Tool calls are named but their
    arguments and results are left out: a document search returns ~11 000
    tokens of passages, which would bury the conversation it was part of.
    """
    exported_at = exported_at or datetime.now().astimezone()
    pdf = _ChatPDF(title)
    pdf.add_page()

    pdf.set_font(SANS, "B", 18)
    pdf.multi_cell(0, 8, title, align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(SANS, size=9)
    pdf.set_text_color(MUTED)
    turns = sum(1 for m in messages if m.get("role") == "user")
    pdf.cell(
        0,
        6,
        f"Exported {exported_at:%Y-%m-%d %H:%M} · "
        f"{turns} question{'s' if turns != 1 else ''}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_text_color(0)
    pdf.set_draw_color(220)
    pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
    pdf.ln(3)

    if not messages:
        _plain(pdf, "This conversation has no messages.")

    for message in messages:
        parts = message.get("content") or []
        if message.get("role") == "user":
            _role_label(pdf, "You", "#374151")
            # As typed, not as markdown: the chat shows a prompt verbatim, so
            # "*" or "#" in a question must not turn into formatting here.
            text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
            _plain(pdf, _summarize_attachments(text))
            continue

        _role_label(pdf, "Assistant", "#2563eb")
        tools: list[str] = []
        for part in parts:
            if part.get("type") == "tool-call":
                tools.append(part.get("toolName", "tool"))
                continue
            # Consecutive calls share one line, ahead of the text they led to.
            if tools:
                _tool_line(pdf, tools)
                tools = []
            if part.get("type") == "text" and part.get("text", "").strip():
                _rich(pdf, part["text"])
        if tools:
            _tool_line(pdf, tools)

    return bytes(pdf.output())


def pdf_filename(title: str) -> str:
    """A filename for the download, from the chat's title."""
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", title)
    name = re.sub(r"\s+", " ", name).strip(" .")[:80].rstrip(" .")
    return f"{name or 'chat'}.pdf"


def content_disposition(filename: str) -> str:
    """An attachment header that survives a non-ASCII title.

    `filename` alone must be ASCII, so it carries a transliterated fallback;
    `filename*` carries the real name for every browser that reads it (all
    current ones do).
    """
    stem = filename.removesuffix(".pdf")
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem)
    # Dropped non-Latin words leave runs of spaces behind: "Найди на X" -> "  X".
    stem = re.sub(r"\s+", " ", stem).strip(" _.")
    ascii_name = f"{stem or 'chat'}.pdf"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
