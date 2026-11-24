# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for the static LP analyzer and its report."""

from __future__ import annotations

from pathlib import Path

from gtopt_check_lp import _colors
from gtopt_check_lp import _lp_analyzer as la


def _lp(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "m.lp"
    path.write_text(text, encoding="utf-8")
    return path


def test_coeff_entry_equality() -> None:
    a = la.CoeffEntry(1.0, "x", "c")
    assert a == la.CoeffEntry(1.0, "y", "d")
    assert a != "not an entry"


def test_parse_pairs_skips_exponent_fragment() -> None:
    pairs = la._parse_coeff_var_pairs("3e + 2 x >= 1")  # noqa: SLF001
    assert all(v != "e" for _, v in pairs)
    assert (2.0, "x") in pairs


def test_find_var_term_missing() -> None:
    assert la._find_var_term("+ 2 x", "y") is None  # noqa: SLF001


def _build_lp() -> str:
    lines = ["\\ comment", "Maximize", " obj: x0 + y0", "Subject To"]
    # continuation line + zero coefficient
    lines += [" c_main: 2 x0", "   + 0 y0 + 3 z0 >= 1"]
    # 11 "empty" constraints (no numeric coefficients)
    lines += [f" empty{i}: alpha - beta >= gamma" for i in range(11)]
    # long constraint with the flagged variable far beyond the display cut
    long_terms = " + ".join(f"1 pad{i}" for i in range(40))
    lines += [f" longc: {long_terms} + 1e12 bigvar >= 0"]
    # long constraint whose flagged variable is early
    lines += [f" early: 1e11 firstvar + {long_terms} >= 0"]
    lines += ["Bounds"]
    # 21 conflicting bounds, 11 fixed variables, free variables
    lines += [f" 5 <= bad{i} <= 1" for i in range(21)]
    lines += [f" 2 <= fix{i} <= 2" for i in range(11)]
    lines += [" freev free", " minf >= -inf"]
    lines += ["Generals", " gi1 gi2", "Binaries", " b1"]
    # no End: constraints flushed at EOF is exercised by a separate test
    lines += ["End", " after_end: x >= 1"]
    return "\n".join(lines) + "\n"


def test_analyze_and_report(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(_colors, "USE_COLOR", False)
    stats = la.analyze_lp_file(_lp(tmp_path, _build_lp()))
    assert not stats.is_minimization
    assert stats.n_integers == 2
    assert stats.n_binary == 1
    assert len(stats.infeasible_bounds) == 21
    assert len(stats.fixed_variables) == 11
    assert {"freev", "minf"} <= set(stats.unbounded_variables)
    assert len(stats.empty_constraints) == 11
    assert "after_end" not in stats.constraint_texts

    report = la.format_static_report(tmp_path / "m.lp", stats)
    assert "Integer vars:   2" in report
    assert "Binary vars:    1" in report
    assert "… and 1 more" in report  # 21 conflicting bounds, 20 shown
    assert "… and 1 more" in report.split("Empty constraints")[1]
    assert "Fixed variables (11)" in report
    assert "1e12 bigvar …" in report
    assert "firstvar" in report


def test_constraints_flushed_at_eof(tmp_path: Path) -> None:
    stats = la.analyze_lp_file(_lp(tmp_path, "Minimize\n obj: x\nst\n c1: 2 x >= 1\n"))
    assert stats.n_constraints == 1
    assert stats.constraint_texts["c1"] == "c1: 2 x >= 1"


def test_report_with_missing_constraint_text(tmp_path: Path) -> None:
    stats = la.LPStats()
    entry = la.CoeffEntry(5.0, "x", "ghost")
    stats.top_max_coeffs = [entry]
    stats.top_min_coeffs = [entry]
    stats.has_objective = True
    report = la.format_static_report(tmp_path / "x.lp", stats)
    assert "con=ghost" in report
    assert report.count("con=ghost") == 2


def test_coeff_stats_ignore_digits_in_names_and_rhs(tmp_path: Path) -> None:
    """Digits inside names and the RHS are not coefficients (2.0e9 bug)."""
    lp = tmp_path / "names.lp"
    lp.write_text(
        "Minimize\n obj: x1\n"
        "Subject To\n"
        " sd_2024092283_gx_1: 0.5 sd_2024092283_flow_1\n"
        "   - 0.25 x1 <= 29.6364\n"
        "Bounds\n x1 >= 0\nEnd\n"
    )
    stats = la.analyze_lp_file(lp)
    assert stats.max_abs_coeff == 0.5
    assert stats.min_abs_nonzero_coeff == 0.25
    assert not stats.large_coeff_constraints
