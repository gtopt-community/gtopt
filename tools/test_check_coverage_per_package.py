# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for tools/check_coverage_per_package.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_coverage_per_package",
    Path(__file__).with_name("check_coverage_per_package.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
ccp = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = ccp
_SPEC.loader.exec_module(ccp)


def _summary(stmts: int, covered: int, branches: int = 0, cbr: int = 0) -> dict:
    return {
        "summary": {
            "num_statements": stmts,
            "covered_lines": covered,
            "num_branches": branches,
            "covered_branches": cbr,
        }
    }


def _report(files: dict[str, dict]) -> dict:
    return {"files": files}


def test_package_of_variants() -> None:
    assert ccp.package_of("plp2gtopt/parsers.py") == "plp2gtopt"
    assert ccp.package_of("./a/b/c.py") == "a"
    assert ccp.package_of("a\\b.py") == "a"
    assert ccp.package_of("scripts/x/y.py", prefix="scripts/") == "x"
    assert ccp.package_of("setup.py") == "<top-level>"


def test_tally_percentages() -> None:
    t = ccp.Tally()
    assert t.percent == 100.0
    assert t.line_percent == 100.0
    assert t.branch_percent == 100.0
    t.add(_summary(10, 8, 10, 5)["summary"])
    assert t.percent == pytest.approx(65.0)
    assert t.line_percent == pytest.approx(80.0)
    assert t.branch_percent == pytest.approx(50.0)


def test_tally_reports_groups_by_package() -> None:
    rep = _report(
        {
            "a/x.py": _summary(10, 10),
            "a/y.py": _summary(10, 0),
            "b/z.py": _summary(4, 4, 2, 2),
        }
    )
    packages, total = ccp.tally_reports([rep, _report({"c/w.py": {}})])
    assert set(packages) == {"a", "b", "c"}
    assert packages["a"].percent == pytest.approx(50.0)
    assert packages["b"].percent == pytest.approx(100.0)
    assert packages["c"].percent == pytest.approx(100.0)
    assert total.statements == 24


def test_evaluate_package_and_total_failures() -> None:
    packages, total = ccp.tally_reports(
        [_report({"a/x.py": _summary(10, 8), "b/y.py": _summary(10, 10)})]
    )
    assert ccp.evaluate(packages, total, 80.0, 90.0, {}) == []
    fails = ccp.evaluate(packages, total, 85.0, 95.0, {})
    assert fails == ["a: 80.0% < 85.0%", "TOTAL: 90.0% < 95.0%"]
    # A floor only lowers the bar for that package.
    assert ccp.evaluate(packages, total, 85.0, 90.0, {"a": 75.0}) == []
    assert ccp.evaluate(packages, total, 85.0, 90.0, {"a": 99.0}) == [
        "a: 80.0% < 85.0%"
    ]


def test_load_thresholds(tmp_path: Path) -> None:
    assert ccp.load_thresholds(None) == (90.0, 95.0, {})
    cfg = tmp_path / "pyproject.toml"
    cfg.write_text(
        "[tool.gtopt.coverage]\npackage_min = 80\ntotal_min = 85.5\n"
        "[tool.gtopt.coverage.package_floor]\nfoo = 70\n",
        encoding="utf-8",
    )
    assert ccp.load_thresholds(cfg) == (80.0, 85.5, {"foo": 70.0})
    empty = tmp_path / "empty.toml"
    empty.write_text("[tool.other]\nx = 1\n", encoding="utf-8")
    assert ccp.load_thresholds(empty) == (90.0, 95.0, {})


def test_format_table_orders_worst_first() -> None:
    packages, total = ccp.tally_reports(
        [_report({"good/x.py": _summary(10, 10), "bad/y.py": _summary(10, 1)})]
    )
    lines = ccp.format_table(packages, total).splitlines()
    assert lines[0].startswith("package")
    assert lines[1].startswith("bad")
    assert lines[2].startswith("good")
    assert lines[3].startswith("TOTAL")


def test_main_pass_and_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rep = tmp_path / "coverage.json"
    rep.write_text(
        json.dumps(_report({"a/x.py": _summary(10, 9), "b/y.py": _summary(10, 10)})),
        encoding="utf-8",
    )
    assert ccp.main([str(rep), "--package-min", "90", "--total-min", "95"]) == 0
    assert "Coverage gate OK" in capsys.readouterr().out

    assert ccp.main([str(rep), "--package-min", "95"]) == 1
    err = capsys.readouterr().err
    assert "Coverage gate FAILED" in err
    assert "a: 90.0% < 95.0%" in err

    cfg = tmp_path / "pyproject.toml"
    cfg.write_text(
        "[tool.gtopt.coverage]\npackage_min = 99\ntotal_min = 50\n"
        "[tool.gtopt.coverage.package_floor]\na = 90\n",
        encoding="utf-8",
    )
    # a (90 %) passes thanks to its floor; b (100 %) meets package_min.
    assert ccp.main([str(rep), "--config", str(cfg)]) == 0
    capsys.readouterr()
    assert ccp.main([str(rep), "--config", str(cfg), "--total-min", "99"]) == 1
    assert "TOTAL: 95.0% < 99.0%" in capsys.readouterr().err
