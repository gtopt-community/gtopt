# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Callable

import pytest

from gtopt_private_eval.main import main

from .conftest import CONFIGS, golden


def test_cli_markets_json_csv(
    gen_case: Callable[..., Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rdir = gen_case()
    jout, cout = tmp_path / "r.json", tmp_path / "r.csv"
    rc = main(
        [
            str(rdir),
            "--market",
            "cl",
            "--market",
            "cfd",
            "--set",
            "finance.wacc=0.09",
            "--set",
            "projects.g.technology=ocgt",
            "--json-out",
            str(jout),
            "--csv-out",
            str(cout),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "market=cl" in out and "market=cfd" in out and "NPV" in out
    data = json.loads(jout.read_text())
    assert [d["market"] for d in data] == ["cl", "cfd"]
    assert data[0]["projects"][0]["wacc"] == 0.09
    rows = list(csv.DictReader(cout.open()))
    assert rows[0]["project"] == "g" and "ebit" in rows[0]
    assert "rev_sufficiency" in rows[0]


def test_cli_single_json_quiet(
    gen_case: Callable[..., Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    jout = tmp_path / "one.json"
    assert main([str(gen_case()), "-q", "--json-out", str(jout)]) == 0
    assert capsys.readouterr().out == ""
    assert json.loads(jout.read_text())["market"] == "energy_only"


def test_cli_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(tmp_path / "missing")]) == 2
    assert "error" in capsys.readouterr().err
    assert main([str(tmp_path), "--set", "novalue"]) == 2


def test_cli_trajectory_dispatch(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["capex-trajectory", "--tech", "bess", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["tech"] == "bess"


def test_cli_battery_golden_with_mef(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rdir = golden("invest_bat_1b/golden/lp")
    mef = tmp_path / "mef.csv"
    mef.write_text("scenario,stage,block,bus,mef\n1,1,20,1,0.56\n", encoding="utf-8")
    rc = main(
        [
            str(rdir),
            "--config",
            str(CONFIGS / "invest_bat.json"),
            "--market",
            "cl",
            "--mef-file",
            str(mef),
            "--include-unbuilt",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Battery:bess" in out and "MWh" in out
