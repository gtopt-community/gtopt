"""Tests for tools/check_latex_math.py (the Docs workflow's math lint)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_latex_math as clm  # pylint: disable=wrong-import-position


def _diags(tmp_path: Path, text: str) -> list[str]:
    md = tmp_path / "doc.md"
    md.write_text(text, encoding="utf-8")
    return clm.check_file(md)


def test_env_var_in_code_span_is_not_math(tmp_path: Path) -> None:
    assert not _diags(tmp_path, "Set `$GTOPT_CONFIG` first.\n")


def test_code_span_wrapping_onto_next_line_is_ignored(tmp_path: Path) -> None:
    text = (
        "A warning\n(`$GTOPT_CONFIG='…' does not exist; falling back to\n"
        "./.gtopt.conf`) is logged.\n"
    )
    assert not _diags(tmp_path, text)


def test_double_backtick_code_span(tmp_path: Path) -> None:
    assert not _diags(tmp_path, "Run ``echo $HOME`` now.\n")


def test_code_span_does_not_cross_paragraphs(tmp_path: Path) -> None:
    # The backtick of the first paragraph is unmatched; the $ of the
    # second paragraph must still be reported.
    diags = _diags(tmp_path, "a ` b\n\nprice 5 $ per unit\n")
    assert len(diags) == 1
    assert ":3:" in diags[0]


def test_inline_math_split_across_lines_is_reported(tmp_path: Path) -> None:
    diags = _diags(tmp_path, "the bound $x + y\n\\le z$ holds\n")
    assert len(diags) == 2


def test_inline_math_and_escaped_currency_pass(tmp_path: Path) -> None:
    assert not _diags(tmp_path, "Cost $c_g$ in \\$/MWh, about 2.1 G\\$.\n")


def test_unescaped_currency_is_reported(tmp_path: Path) -> None:
    diags = _diags(tmp_path, "about 2.1 G$ of reward\n")
    assert len(diags) == 1


def test_fenced_code_is_ignored(tmp_path: Path) -> None:
    assert not _diags(tmp_path, "```bash\necho $HOME\n```\n")


def test_display_math_leading_operator(tmp_path: Path) -> None:
    diags = _diags(tmp_path, "$$\na\n+ b\n$$\n")
    assert len(diags) == 1
    assert "Line starts with '+'" in diags[0]


def test_main_exit_codes(tmp_path: Path) -> None:
    good = tmp_path / "good.md"
    good.write_text("`$X` and $y$\n", encoding="utf-8")
    bad = tmp_path / "bad.md"
    bad.write_text("5 $ each\n", encoding="utf-8")
    assert clm.main([str(good)]) == 0
    assert clm.main([str(bad)]) == 1
