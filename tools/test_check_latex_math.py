"""Tests for tools/check_latex_math.py (the Docs workflow's math lint).

Every GitHub-rendering expectation below was checked against GitHub's
own renderer (``gh api markdown -f mode=gfm``).  The MathJax tests run
only when ``node`` and ``tools/latex_lint/node_modules`` are present
(``npm ci --prefix tools/latex_lint``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("markdown_it")
pytest.importorskip("mdit_py_plugins")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_latex_math as clm  # pylint: disable=wrong-import-position

needs_mathjax = pytest.mark.skipif(
    clm.mathjax_available() is not None,
    reason=f"MathJax unavailable: {clm.mathjax_available()}",
)


def _diags(tmp_path: Path, text: str, *, mathjax: bool = False) -> list[str]:
    md = tmp_path / "doc.md"
    md.write_text(text, encoding="utf-8")
    return clm.check_file(md, use_mathjax=mathjax)


def _checks(diags: list[str]) -> list[str]:
    return [d.split("[", 1)[1].split("]", 1)[0] for d in diags]


# --- extraction: what is (not) math --------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Set `$GTOPT_CONFIG` first.\n",
        "Run ``echo $HOME`` now.\n",
        "A warning\n(`$GTOPT_CONFIG='x' does not\nexist`) is logged.\n",
        "```bash\necho $HOME $PATH\n```\n",
        "Cost $c_g$ in \\$/MWh, about 2.1 G\\$.\n",
        "about 2.1 G$ of reward\n",
        "price $20 per unit\n",
        "between $5 and $10 per MWh\n",
        "base 6701.1 $MM and 5058.9 $MM\n",
        "| N | Objective [$] |\n|---|---|\n| 1 | $x_1$ |\n",
    ],
)
def test_currency_and_code_are_not_findings(tmp_path: Path, text: str) -> None:
    assert not _diags(tmp_path, text)


def test_extracts_all_math_forms(tmp_path: Path) -> None:
    text = (
        "Inline $a_1$ and $$b$$ and $`c^2`$.\n\n"
        "$$\nd = e\n$$\n\n"
        "```math\nf \\, g\n```\n"
    )
    rep = clm.extract(text, tmp_path / "x.md", clm._make_parsers())
    got = [(s.line, s.tex.strip(), s.display, s.verbatim) for s in rep.spans]
    assert (1, "a_1", False, False) in got
    assert (1, "b", True, False) in got
    assert (1, "c^2", False, True) in got
    assert (3, "d = e", True, False) in got
    assert (8, "f \\, g", True, True) in got
    assert not rep.findings


def test_line_numbers_inside_paragraph(tmp_path: Path) -> None:
    rep = clm.extract("a\nb $x$\nc $y$\n", tmp_path / "x.md", clm._make_parsers())
    assert [s.line for s in rep.spans] == [2, 3]


# --- GitHub-rendering hazards ---------------------------------------------


def test_wrapped_inline_math_is_reported(tmp_path: Path) -> None:
    diags = _diags(tmp_path, "the bound $x + y\n\\le z$ holds\n")
    assert _checks(diags) == ["wrapped-inline-math"]
    assert ":1:" in diags[0]


def test_display_block_line_starting_with_marker(tmp_path: Path) -> None:
    text = "$$\na\n+ b\n- c\n* d\n> e\n1. f\n$$\n"
    diags = _diags(tmp_path, text)
    assert _checks(diags) == ["display-line-start"] * 5
    assert [int(d.split(":")[1]) for d in diags] == [3, 4, 5, 6, 7]


@pytest.mark.parametrize(
    "text",
    [
        "$$\na\n+c\n-d\n$$\n",  # no space: not a list item
        "$$\na\n{}+ c\n$$\n",
        "$$\na\n2. b\n$$\n",  # only "1." interrupts a paragraph
        "$$ -a\n= b $$\n",
    ],
)
def test_display_block_safe_line_starts(tmp_path: Path, text: str) -> None:
    assert not _diags(tmp_path, text)


def test_multiline_display_math_in_list_item(tmp_path: Path) -> None:
    text = "1. item:\n\n   $$\n   x\n   $$\n\n2. one line $$x$$ ok\n"
    diags = _diags(tmp_path, text)
    assert _checks(diags) == ["display-in-list"]
    fence = "1. item:\n\n   ```math\n   x\n   ```\n"
    assert not _diags(tmp_path, fence)


def test_emphasis_inside_inline_math(tmp_path: Path) -> None:
    assert _checks(_diags(tmp_path, "$x^*$ and $y^*$\n")) == ["emphasis-in-math"]
    text = "$[\\underline{V}_\\tau, \\overline{V}_\\tau]$\n"
    assert _checks(_diags(tmp_path, text)) == ["emphasis-in-math"]
    # Intraword '_' and a lone '*' are safe; escaped ones too.
    assert not _diags(tmp_path, "$a_i$ and $b_j$ and $x^*$\n")
    assert not _diags(tmp_path, "$x^\\*$ and $y^\\*$\n")


def test_currency_pair(tmp_path: Path) -> None:
    for text in ("cost $20 and $/MWh\n", "units ($/m3 or $/MWh)\n"):
        assert _checks(_diags(tmp_path, text)) == ["currency-pair"]
    assert not _diags(tmp_path, "units (\\$/m3 or \\$/MWh)\n")


def test_text_escape_in_verbatim_math(tmp_path: Path) -> None:
    diags = _diags(tmp_path, "```math\n\\text{a\\_b}\n```\n")
    assert _checks(diags) == ["text-escape"]
    # In $-math GitHub strips the backslash first: fine.
    assert not _diags(tmp_path, "$\\text{a\\_b}$\n")


@pytest.mark.parametrize(
    "text",
    [
        "Space $`a \\, b`$ here.\n",  # verbatim: GitHub keeps the backslash
        "```math\na \\; b \\{c\\}\n```\n",
        "Set $\\lbrace 0, 1\\rbrace$ and $\\Vert x \\Vert$.\n",
        "| a | $`\\Vert x\\Vert \\, y`$ |\n|---|---|\n",
    ],
)
def test_github_safe_forms(tmp_path: Path, text: str) -> None:
    md = tmp_path / "doc.md"
    md.write_text(text, encoding="utf-8")
    diags, warns = clm.check_files([md], use_mathjax=False)
    assert not diags
    assert not warns


def test_github_view_strips_escapes() -> None:
    assert clm.github_view(r"a \, b \{c\} \_ d") == "a , b {c} _ d"
    # "\\" survives right before a newline, not mid-line.
    assert clm.github_view("a \\\\\nb \\\\ c") == "a \\\\\nb \\ c"
    assert clm.silent_escapes(r"\left\{ x \, y \_") == ["\\{", "\\,"]


def test_github_escapes_modes(tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("Space $a \\, b$ here.\n", encoding="utf-8")
    diags, warns = clm.check_files([md], use_mathjax=False)  # default: error
    assert _checks(diags) == ["github-escape"]
    assert not warns
    diags, warns = clm.check_files([md], use_mathjax=False, github_escapes="warn")
    assert not diags
    assert len(warns) == 1
    assert "1 math span" in warns[0]
    diags, warns = clm.check_files([md], use_mathjax=False, github_escapes="off")
    assert not diags
    assert not warns


# --- MathJax ---------------------------------------------------------------


@needs_mathjax
@pytest.mark.parametrize(
    ("tex", "error"),
    [
        ("$\\frac{a}{b$", "Missing close brace"),
        ("$\\foo x$", "Undefined control sequence \\foo"),
        ("$$\n\\left( x\n$$", "Extra \\left or missing \\right"),
        ("$\\frac{a}$", "Missing argument for \\frac"),
        # valid TeX, but GitHub hands MathJax "\left{"
        ("$$\n\\left\\{ x \\right.\n$$", "delimiter for \\left"),
        # "\\[2pt]" mid-line becomes "\[2pt]" on GitHub
        ("$$\na \\\\[2pt] b\n$$", "Undefined control sequence \\["),
    ],
)
def test_mathjax_reports_tex_errors(tmp_path: Path, tex: str, error: str) -> None:
    diags = _diags(tmp_path, f"Text.\n\n{tex}\n", mathjax=True)
    assert _checks(diags) == ["tex-error"]
    assert error in diags[0]


@needs_mathjax
def test_mathjax_accepts_valid_math(tmp_path: Path) -> None:
    text = (
        "Inline $\\sum_{i} x_i \\le \\overline{X}$, $\\mathbb{R}^n$, "
        "$\\text{a_b}$, $\\boldsymbol{x}$, $\\cancel{y}$.\n\n"
        "$$\n\\begin{aligned}\na &= b \\\\\nc &= \\lbrace d \\rbrace\n"
        "\\end{aligned}\n$$\n\n"
        "```math\n\\left\\{ x \\right\\} \\\\[2pt] y\n```\n\n"
        "$$\\newcommand{\\R}{\\mathbb{R}} \\R$$ then $\\R^2$.\n"
    )
    assert not _diags(tmp_path, text, mathjax=True)


@needs_mathjax
def test_mathjax_wrapped_span_is_not_typeset(tmp_path: Path) -> None:
    # GitHub shows it as text: report the wrap, not a bogus TeX error.
    diags = _diags(tmp_path, "at 374 k$/hm3 and ~74\nk$/hm3 here\n", mathjax=True)
    assert _checks(diags) == ["wrapped-inline-math"]


# --- CLI -------------------------------------------------------------------


def test_main_exit_codes(tmp_path: Path) -> None:
    good = tmp_path / "good.md"
    good.write_text("`$X` and $y$\n", encoding="utf-8")
    bad = tmp_path / "bad.md"
    bad.write_text("$$\na\n+ b\n$$\n", encoding="utf-8")
    assert clm.main(["--no-mathjax", str(good)]) == 0
    assert clm.main(["--no-mathjax", str(bad)]) == 1
    assert clm.main([str(tmp_path / "missing_dir")]) == 0


def test_main_degrades_without_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    good = tmp_path / "good.md"
    good.write_text("$y$\n", encoding="utf-8")
    monkeypatch.setattr(clm.shutil, "which", lambda _name: None)
    assert clm.main([str(good)]) == 0
    assert "MathJax validation skipped" in capsys.readouterr().err
    assert clm.main(["--strict-deps", str(good)]) == 2


def test_main_skips_without_python_deps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> None:
        raise ImportError("No module named 'mdit_py_plugins'")

    monkeypatch.setattr(clm, "_make_parsers", boom)
    md = tmp_path / "a.md"
    md.write_text("$x$\n", encoding="utf-8")
    assert clm.main([str(md)]) == 0
    assert clm.main(["--strict-deps", str(md)]) == 2
