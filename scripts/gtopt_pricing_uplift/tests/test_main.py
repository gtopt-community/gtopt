# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI of gtopt_pricing_uplift."""

from __future__ import annotations

import csv
import json
import runpy
import sys
from pathlib import Path

import pytest

from gtopt_pricing_uplift.main import main


def test_single_dir_json(toy_results, tmp_path: Path, capsys) -> None:
    rd = toy_results("fixed")
    out = tmp_path / "r.json"
    assert main([str(rd), "--json-out", str(out), "--top", "1"]) == 0
    text = capsys.readouterr().out
    assert "g_fast_uc" in text
    assert "lowest total uplift" in text
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["total_uplift"] == pytest.approx(3200.0)


def test_compare_modes_csv(toy_results, tmp_path: Path, capsys) -> None:
    dirs = [str(toy_results(m)) for m in ("fixed", "fast_start", "relaxed")]
    jout = tmp_path / "all.json"
    cout = tmp_path / "units.csv"
    assert (
        main([*dirs, "--json-out", str(jout), "--csv-out", str(cout), "--top", "0"])
        == 0
    )
    text = capsys.readouterr().out
    assert "TOTAL UPLIFT" in text
    assert "(relaxed)" in text.splitlines()[-1]
    data = json.loads(jout.read_text(encoding="utf-8"))
    assert [d["total_uplift"] for d in data] == pytest.approx([3200.0, 1900.0, 1700.0])
    with open(cout, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 6
    assert {r["pricing_mode"] for r in rows} == {"fixed", "fast_start", "relaxed"}


def test_quiet_and_error(toy_results, tmp_path: Path, capsys) -> None:
    assert main([str(toy_results("fixed")), "-q"]) == 0
    assert capsys.readouterr().out == ""
    assert main([str(tmp_path / "missing")]) == 2
    assert "error" in capsys.readouterr().err


def test_module_entry_point(toy_results, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys, "argv", ["gtopt_pricing_uplift", str(toy_results("relaxed")), "-q"]
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("gtopt_pricing_uplift", run_name="__main__")
    assert exc.value.code == 0
