"""LaTeX in the PDF export: inline math as text, display math typeset."""

import pytest

from app.chats.pdf_math import display_svg, inline_html, inline_text


class TestInlineText:
    @pytest.mark.parametrize(
        "tex, expected",
        [
            (r"v = 0.99c", "v = 0.99c"),
            (r"E = mc^2", "E = mc²"),
            (r"\Delta t \le 10^{-3}", "Δt ≤ 10⁻³"),
            (r"\alpha + \beta \approx \pi", "α + β ≈ π"),
            # Brackets only where the meaning needs them.
            (r"\frac{L}{v}", "L/v"),
            (r"\frac{a+b}{c}", "(a + b)/c"),
            (r"\frac{x^2}{2}", "x²/2"),
            (r"\gamma = \frac{1}{\sqrt{1-\beta^2}}", "γ = 1/√(1 − β²)"),
            (r"\frac{(a)-(b)}{c}", "((a) − (b))/c"),
            (r"\frac{-b \pm \sqrt{b^2-4ac}}{2a}", "(−b ± √(b² − 4ac))/2a"),
            # A sign is unary at the start of a group or after an operator.
            (r"a = -1", "a = −1"),
            (r"(-1)^n", "(−1)ⁿ"),
            (r"x = \pm 1", "x = ±1"),
            # Scripts with no Unicode form are marked, and set tight.
            (r"t_{obs}", "t_(obs)"),
            (r"\sum_{n=1}^{\infty} x_n", "∑ₙ₌₁^∞ xₙ"),
            (r"\sin\theta", "sin θ"),
            (r"\text{км/с}", "км/с"),
        ],
    )
    def test_reads_as_the_formula(self, tex, expected):
        assert inline_text(tex) == expected

    def test_unparseable_latex_comes_back_as_written(self):
        assert inline_text(r"\frac{a}{") == r"\frac{a}{"


class TestInlineHtml:
    def test_variables_are_italic_and_scripts_are_tags(self):
        assert inline_html(r"t_{obs} = x^2") == "<i>t</i><sub>obs</sub> = <i>x</i><sup>2</sup>"

    def test_capital_greek_and_letterlike_symbols_stay_upright(self):
        # As TeX sets them: Δt, not *Δ*t; ℝ is a set, not a variable.
        assert inline_html(r"\Delta t") == "Δ<i>t</i>"
        assert inline_html(r"\mathbb{R}") == "ℝ"

    def test_text_is_escaped(self):
        assert inline_html(r"x < y") == "<i>x</i> &lt; <i>y</i>"

    def test_unparseable_latex_is_shown_as_code(self):
        assert inline_html(r"\frac{a}{") == r"<code>\frac{a}{</code>"


class TestDisplaySvg:
    def test_typesets_to_an_svg_with_its_size(self):
        svg, width, height = display_svg(r"\frac{a}{b} + \sqrt{x}", 12.5)
        assert svg.startswith(b"<svg")
        assert width > 0 and height > 0

    def test_scales_with_the_font_size(self):
        _, small, _ = display_svg(r"x^2 + y^2", 10)
        _, large, _ = display_svg(r"x^2 + y^2", 20)
        assert large == pytest.approx(small * 2, rel=0.05)

    def test_malformed_latex_is_none_not_an_error(self):
        assert display_svg(r"\frac{a}{", 12) is None
