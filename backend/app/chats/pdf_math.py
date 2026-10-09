"""LaTeX in answers, for the PDF export.

The chat renders $...$ and $$...$$ with KaTeX. The PDF has no TeX to hand
(installing one needs root, and the slim image has none), so it does two
things, each matched to what fpdf2 can lay out:

- Display math ($$ on its own lines) is typeset by ziamath into an SVG, which
  is placed as a centred vector image. Fractions, roots, limits, matrices and
  aligned equations look as they do in the chat.
- Inline math ($...$ inside a sentence) becomes text: Greek and operators as
  Unicode, scripts as <sup>/<sub>, a fraction as a/b. fpdf2 cannot place an
  image inside a run of text - an <img> lands at the cursor, outside the
  paragraph - so an image here would float away from the words around it.

Inline conversion goes through latex2mathml, which ziamath uses itself, so
both paths read LaTeX the same way and every command it knows maps to the
right Unicode character without a table of our own.
"""

import logging
import re
from functools import lru_cache
from html import escape
from xml.etree import ElementTree

import ziamath
from latex2mathml.converter import convert as latex_to_mathml

logger = logging.getLogger(__name__)

_NS = "{http://www.w3.org/1998/Math/MathML}"

# Operators that get space on both sides, as TeX gives a binary operator or a
# relation. Everything else (brackets, commas, factorial) sits tight.
_SPACED = set("=+−±∓×÷·<>≤≥≠≈≡∼≃≅∝→←↔⇒⇐⇔∈∉⊂⊃⊆⊇∪∩∧∨") | {"*"}

_SUPERSCRIPT = str.maketrans("0123456789+−-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿⁱ")
_SUBSCRIPT = str.maketrans("0123456789+−-=()aeoxhklmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜ")
_SUPERSCRIPTABLE = set("0123456789+−-=()ni")
_SUBSCRIPTABLE = set("0123456789+−-=()aeoxhklmnpst")

_UNARY = {"−", "+", "±", "∓", "-"}
_CLOSING = set(")]}|⟩")

# Large operators whose limits are scripts; a space after them keeps the
# operand from running into the upper limit ("∑ⁿxᵢ" -> "∑ⁿ xᵢ").
_LARGE = set("∑∏∐∫∬∭∮⋃⋂⋀⋁") | {"lim", "max", "min", "sup", "inf"}

# TeX sets Latin and lowercase Greek letters in italic; capital Greek (Δ, Σ)
# and letterlike symbols (ℝ, ℏ) stay upright.
_ITALIC = re.compile(r"[A-Za-zα-ωϑϕϖϵ]")

# A combining accent per MathML <mover> accent. Applied only to a single
# character; "hat" over a whole expression has no Unicode form.
_ACCENTS = {"^": "̂", "ˆ": "̂", "→": "⃗", "¯": "̄", "‾": "̄",
            "~": "̃", "˜": "̃", "˙": "̇", "¨": "̈"}


def _local(element: ElementTree.Element) -> str:
    return element.tag.removeprefix(_NS)


def _enclosed(text: str) -> bool:
    """Whether one bracket pair spans all of text: "(a+b)" yes, "(a)−(b)" no."""
    if not (text.startswith("(") and text.endswith(")")):
        return False
    depth = 0
    for i, char in enumerate(text):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0 and i < len(text) - 1:
            return False
    return depth == 0


def _is_operator(element: ElementTree.Element) -> bool:
    tag = _local(element)
    return tag == "mo" or (tag == "mi" and (element.text or "").strip() in _SPACED)


class _Inline:
    """MathML to inline text, as HTML (html=True) or plain Unicode."""

    def __init__(self, html: bool):
        self.html = html

    def render(self, element: ElementTree.Element) -> str:
        tag = _local(element)
        kids = list(element)
        text = element.text or ""

        if tag == "mi" and text.strip() in _SPACED:
            # latex2mathml emits some operators as identifiers (\pm as <mi>).
            return self._operator(text.strip(), unary=False)
        if tag == "mi":
            # One letter is a variable and goes italic, as TeX sets it; a word
            # ("sin", "max") is an operator name and stays upright.
            if self.html and _ITALIC.fullmatch(text):
                return f"<i>{escape(text)}</i>"
            # A trailing space keeps "sin" off its argument; doubled spaces
            # are collapsed at the end.
            out = f"{text} " if len(text) > 1 else text
            return escape(out) if self.html else out
        if tag in ("mn", "mtext", "ms"):
            return escape(text) if self.html else text
        if tag == "mo":
            return self._operator(text.strip(), unary=False)
        if tag == "mspace":
            return " "
        if tag in ("mphantom", "none", "annotation", "annotation-xml"):
            return ""
        if tag in ("msub", "munder") and len(kids) == 2:
            large = (kids[0].text or "").strip() in _LARGE
            return (
                self.render(kids[0]).rstrip()
                + self._script(kids[1], sup=False)
                + (" " if large else "")
            )
        if tag in ("msup",) and len(kids) == 2:
            return self.render(kids[0]) + self._script(kids[1], sup=True)
        if tag == "mover" and len(kids) == 2:
            return self._over(kids[0], kids[1])
        if tag in ("msubsup", "munderover") and len(kids) == 3:
            large = (kids[0].text or "").strip() in _LARGE
            return (
                self.render(kids[0]).rstrip()
                + self._script(kids[1], sup=False)
                + self._script(kids[2], sup=True)
                + (" " if large else "")
            )
        if tag == "mfrac" and len(kids) == 2:
            return f"{self._grouped(kids[0])}/{self._grouped(kids[1])}"
        if tag == "msqrt":
            inner = _Inline(self.html).join(kids)
            return f"√{self._wrap(inner, _Inline(False).join(kids))}"
        if tag == "mroot" and len(kids) == 2:
            index = self._script(kids[1], sup=True)
            return f"{index}√{self._grouped(kids[0])}"
        if tag == "mtable":
            return "; ".join(self.render(row) for row in kids)
        if tag in ("mtr", "mlabeledtr"):
            return ", ".join(self.render(cell).strip() for cell in kids)
        # mrow, mstyle, mpadded, menclose, math, semantics, mtd, merror and
        # anything unforeseen: their content in order.
        return text + self.join(kids)

    def join(self, elements: list[ElementTree.Element]) -> str:
        out = []
        for i, element in enumerate(elements):
            op = (element.text or "").strip()
            # A sign that opens a group or follows another operator is unary
            # and sits against its operand: "= −x", "(−1)", not "= − x".
            if _is_operator(element) and op in _UNARY:
                previous = elements[i - 1] if i else None
                unary = previous is None or (
                    _is_operator(previous)
                    and (previous.text or "").strip() not in _CLOSING
                )
                out.append(self._operator(op, unary=unary))
            else:
                out.append(self.render(element))
        return "".join(out)

    def _operator(self, op: str, unary: bool) -> str:
        if unary:
            out = op
        elif op in _SPACED:
            out = f" {op} "
        else:
            out = ", " if op == "," else op
        return escape(out) if self.html else out

    def _wrap(self, rendered: str, plain: str) -> str:
        # Brackets only where they change the meaning: a/b needs none, but
        # (a+b)/c does, since "a+b/c" reads as a + (b/c).
        plain = plain.strip()
        atomic = (
            re.fullmatch(r"[\w.′″⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ]+", plain) is not None
            or _enclosed(plain)
            or (plain.startswith("√") and (_enclosed(plain[1:]) or " " not in plain))
        )
        return rendered if atomic else f"({rendered})"

    def _grouped(self, element: ElementTree.Element) -> str:
        return self._wrap(self.render(element), _Inline(False).render(element))

    def _script(self, element: ElementTree.Element, sup: bool) -> str:
        # Tight, as TeX sets a script: "n=1", "10⁻³".
        plain = re.sub(r"\s+", "", _Inline(False).render(element))
        if self.html:
            # Plain text inside: fpdf2's <sup> is a baseline shift, and a
            # script of a script has nowhere further to shift to.
            tag = "sup" if sup else "sub"
            return f"<{tag}>{escape(plain)}</{tag}>"
        allowed, table, mark = (
            (_SUPERSCRIPTABLE, _SUPERSCRIPT, "^") if sup else (_SUBSCRIPTABLE, _SUBSCRIPT, "_")
        )
        if plain and set(plain) <= allowed:
            return plain.translate(table)
        return f"{mark}{plain}" if len(plain) == 1 else f"{mark}({plain})"

    def _over(self, base: ElementTree.Element, over: ElementTree.Element) -> str:
        accent = _ACCENTS.get((over.text or "").strip())
        rendered = self.render(base)
        plain = _Inline(False).render(base)
        if accent and len(plain) == 1:
            return rendered + accent
        # Not an accent (a limit over an operator), or over more than one
        # character: show it as a superscript instead.
        return rendered + self._script(over, sup=True)


def _mathml(tex: str) -> ElementTree.Element:
    return ElementTree.fromstring(latex_to_mathml(tex))


def _tidy(text: str) -> str:
    """Collapse the spacing the per-element rules leave behind."""
    text = re.sub(r" {2,}", " ", text).strip()
    return re.sub(r" +([),;\]])", r"\1", text)


def inline_html(tex: str) -> str:
    """Inline math as HTML for fpdf2's write_html. Source as code if unparseable."""
    try:
        return _tidy(_Inline(html=True).render(_mathml(tex)))
    except Exception:
        logger.warning("could not convert inline math %r", tex[:80])
        return f"<code>{escape(tex)}</code>"


def inline_text(tex: str) -> str:
    """Inline math as plain Unicode, for places that take no markup (table cells)."""
    try:
        return _tidy(_Inline(html=False).render(_mathml(tex)))
    except Exception:
        logger.warning("could not convert inline math %r", tex[:80])
        return tex


@lru_cache(maxsize=256)
def display_svg(tex: str, size_pt: float) -> tuple[bytes, float, float] | None:
    """Display math as (svg, width_pt, height_pt), or None if it will not typeset.

    Cached because a formula is often repeated across a conversation - a
    derivation quoted back, or the same chat exported twice - and typesetting
    takes 20-200 ms.
    """
    try:
        svg = ziamath.Latex(tex.strip(), size=size_pt).svg()
    except Exception:
        logger.warning("could not typeset display math %r", tex[:80])
        return None
    root = re.search(r"<svg\b[^>]*>", svg)
    width = re.search(r'\bwidth="([\d.]+)"', root.group(0)) if root else None
    height = re.search(r'\bheight="([\d.]+)"', root.group(0)) if root else None
    if not (width and height):
        return None
    return svg.encode(), float(width.group(1)), float(height.group(1))
