# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI tests of gtopt_cost_recovery."""

from __future__ import annotations

import csv
import json
import runpy
from pathlib import Path
from typing import Callable

import pytest

from gtopt_cost_recovery.main import main


def test_cli_table_json_csv(
    gen_case: Callable[..., Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rdir = gen_case(capacost_rate=750.0)
    jout, cout = tmp_path / "r.json", tmp_path / "r.csv"
    rc = main(
        [
            str(rdir),
            "--include-existing",
            "--json-out",
            str(jout),
            "--csv-out",
            str(cout),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Gene:g" in out and "make-whole" in out and "existing old" in out
    assert "objective (solution.csv)" in out
    data = json.loads(jout.read_text(encoding="utf-8"))
    assert data["total_make_whole"] == pytest.approx(500.0)
    with open(cout, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["name"] == "g"
    assert float(rows[0]["profit"]) == pytest.approx(-500.0)


def test_cli_comparison_and_per_year(
    gen_case: Callable[..., Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rdir = gen_case()
    jout = tmp_path / "two.json"
    assert main([str(rdir), str(rdir), "--per-year", "--json-out", str(jout)]) == 0
    out = capsys.readouterr().out
    assert "== comparison" in out and "$/yr" in out
    assert isinstance(json.loads(jout.read_text(encoding="utf-8")), list)


def test_cli_quiet_and_error(
    gen_case: Callable[..., Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(gen_case()), "-q"]) == 0
    assert capsys.readouterr().out == ""
    assert main([str(tmp_path / "missing")]) == 2
    assert "error" in capsys.readouterr().err


def test_module_entry_point(
    gen_case: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", ["gtopt_cost_recovery", str(gen_case()), "-q"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("gtopt_cost_recovery", run_name="__main__")
    assert exc.value.code == 0


def test_cli_price_blocks(
    gen_case: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(gen_case()), "--price-blocks"]) == 0
    out = capsys.readouterr().out
    assert "cap. rent" in out and "variable-cost margin" in out
