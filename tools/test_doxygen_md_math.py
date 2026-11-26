"""Tests for tools/doxygen_md_math.py (Doxygen filter for Markdown math)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("markdown_it")
pytest.importorskip("mdit_py_plugins")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import doxygen_md_math as dmm  # pylint: disable=wrong-import-position


def _conv(text: str) -> str:
    out = dmm.convert(text)
    assert out.count("\n") == text.count("\n"), "line count must not change"
    return out


@pytest.mark.parametrize(
    ("src", "want"),
    [
        ("Cost $c_g$ here.\n", "Cost \\f$c_g\\f$ here.\n"),
        # GitHub strips Markdown escapes inside $-math: so do we
        ("Sub $x\\_{i}$ and $a\\*b$.\n", "Sub \\f$x_{i}\\f$ and \\f$a*b\\f$.\n"),
        # $`...`$ is verbatim on GitHub
        ("Space $`a \\, b`$.\n", "Space \\f$a \\, b\\f$.\n"),
        ("Inline $$x^2$$ display.\n", "Inline \\f$\\displaystyle x^2\\f$ display.\n"),
        ("$$x = 1$$\n", "\\f[x = 1\\f]\n"),
    ],
)
def test_inline_and_one_line_forms(src: str, want: str) -> None:
    assert _conv(src) == want


def test_display_block_keeps_lines() -> None:
    src = "Para.\n\n$$\na \\_b\n+c\n$$\n\nEnd.\n"
    assert _conv(src) == "Para.\n\n\\f[\na _b\n+c\n\\f]\n\nEnd.\n"


def test_display_block_with_content_on_delimiter_lines() -> None:
    src = "$$ a = b\n   + c $$\n"
    assert _conv(src) == "\\f[ a = b\n   + c \\f]\n"


def test_math_fence_is_verbatim() -> None:
    src = "Text.\n\n```math\n\\left\\{ x \\right\\} \\, y\n```\n"
    assert _conv(src) == "Text.\n\n\\f[\n\\left\\{ x \\right\\} \\, y\n\\f]\n"


def test_math_fence_in_list_item_keeps_indent() -> None:
    src = "1. item\n\n   ```math\n   x\n   ```\n"
    assert _conv(src) == "1. item\n\n   \\f[\n   x\n   \\f]\n"


@pytest.mark.parametrize(
    "src",
    [
        "Run `echo $HOME $PATH` now.\n",
        "```bash\necho $x$ and $$y$$\n```\n",
        "price \\$20 per unit, \\$/MWh and [$] header\n",
        "between $5 and $10\n",
        # wrapped: GitHub shows it as text, so does the filter
        "bound $x +\ny$ here\n",
        '```cpp\nint x; // "$a$"\n```\n',
    ],
)
def test_non_math_is_untouched(src: str) -> None:
    assert _conv(src) == src


def test_code_span_with_same_text_is_skipped() -> None:
    src = "Code `$x$` then math $x$.\n"
    assert _conv(src) == "Code `$x$` then math \\f$x\\f$.\n"


def test_table_cell_bar_becomes_vert() -> None:
    src = "| a | b |\n|---|---|\n| $\\|x\\|$ | $`\\Vert y`$ |\n"
    out = _conv(src)
    assert "\\f$\\vert x\\vert \\f$" in out
    assert "\\f$\\Vert y\\f$" in out


def test_quotes_around_formula_become_entities() -> None:
    # Doxygen does not expand commands inside "..." (raw \_form#N).
    src = 'A skip means "update every\n$N{+}1$ iterations" `"code"`.\n'
    out = _conv(src)
    assert out == (
        'A skip means &quot;update every\n\\f$N{+}1\\f$ iterations&quot; `"code"`.\n'
    )
    # Paragraphs without math keep their quotes.
    assert _conv('Say "hi".\n') == 'Say "hi".\n'


def test_main_filters_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    md = tmp_path / "a.md"
    md.write_text("Math $x$.\n", encoding="utf-8")
    assert dmm.main([str(md)]) == 0
    assert capsys.readouterr().out == "Math \\f$x\\f$.\n"
    assert dmm.main([]) == 2


def test_main_passes_through_without_deps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom() -> None:
        raise ImportError("No module named 'mdit_py_plugins'")

    monkeypatch.setattr(dmm.clm, "_make_parsers", boom)
    md = tmp_path / "a.md"
    md.write_text("Math $x$.\n", encoding="utf-8")
    assert dmm.main([str(md)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Math $x$.\n"
    assert "math left as is" in captured.err
