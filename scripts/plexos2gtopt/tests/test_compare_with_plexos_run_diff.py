# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``compare_with_plexos`` over the generic ``gtopt_compare_runs`` core."""

from __future__ import annotations

import io
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from rich.console import Console

from gtopt_shared.testing.tiny_cases import tiny_planning, write_tiny_results
from plexos2gtopt import compare_with_plexos as cwp
from plexos2gtopt.tests.test_cov_compare_with_plexos import (  # noqa: F401  # pylint: disable=unused-import
    FakeMdb,
    _mdb,
    _run_main,
    write_case,
    write_res_zip,
)
from plexos2gtopt.tests.test_results_import import (
    _convert,
    _export,
    single_scenario,
)


def _plexos_case(tmp_path: Path) -> tuple[Path, Path]:
    """A plexos2gtopt case whose ``output`` is its own imported solution."""
    res = tmp_path / "res"
    write_tiny_results(res, "hive")
    vol = pd.DataFrame(
        [(1, 1 + (b > 2), b, 1, 500.0 - 10 * b) for b in (1, 2, 3, 4)],
        columns=["scenario", "stage", "block", "uid", "value"],
    )
    vol.to_parquet(res / "Reservoir" / "energy_sol.parquet", index=False)
    _, px = _export(single_scenario(tiny_planning()), tmp_path, res)
    case = tmp_path / "case"
    _convert(px, case, "--results")
    shutil.copytree(case / "results_plexos", case / "output")
    return case, px / "plexos_solution"


def test_run_generic_diff_self(tmp_path: Path) -> None:
    case, solution = _plexos_case(tmp_path)
    buf = io.StringIO()
    rep = cwp.run_generic_diff(
        case, solution, Console(file=buf, width=200), out_dir=tmp_path / "diff"
    )
    assert rep.passed and rep.streams
    assert rep.label_a == "PLEXOS" and rep.label_b == "gtopt"
    assert "PASS" in buf.getvalue()
    assert (tmp_path / "diff" / "plexos" / "solution.csv").is_file()
    assert (tmp_path / "diff" / "report.json").is_file()
    assert (tmp_path / "diff" / "elements.csv").is_file()
    # the gtopt-side helpers read through the same core
    bat = cwp.compute_gtopt_battery_operation(case)
    assert bat["BESS1"]["charge_mwh"] == pytest.approx(30.0)
    assert bat["BESS1"]["net_mwh"] == pytest.approx(42.0 - 30.0)
    assert cwp.compute_gtopt_reservoir_water_value(case) == {}
    uid = cwp._load_case_bundle(case)["system"]["reservoir_array"][0]["uid"]
    pd.DataFrame(
        {
            "scenario": [1, 1],
            "stage": [1, 2],
            "block": [1, 3],
            "uid": [uid, uid],
            "value": [10.0, 20.0],
        }
    ).to_parquet(case / "output" / "Reservoir" / "water_value_dual.parquet")
    wv = cwp.compute_gtopt_reservoir_water_value(case)
    assert wv["LAGO"] == pytest.approx((10.0 * 10 + 20.0 * 12) / 48)


def test_run_generic_diff_errors(tmp_path: Path) -> None:
    con = Console(file=io.StringIO())
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="no PLEXOS-source JSON"):
        cwp.run_generic_diff(empty, tmp_path, con)
    case = write_case(tmp_path / "case")
    with pytest.raises(FileNotFoundError):
        cwp.run_generic_diff(case, tmp_path / "missing.accdb", con)
    assert cwp._gtopt_run(empty) is None
    assert not cwp.compute_gtopt_reservoir_water_value(empty)
    assert not cwp.compute_gtopt_battery_operation(empty)


def test_main_run_diff(
    tmp_path: Path,
    mdb: FakeMdb,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del mdb
    case = write_case(tmp_path / "case")
    res = write_res_zip(tmp_path / "RES.zip")
    calls: list[dict[str, Any]] = []

    def _fake(case_dir: Path, accdb: Path, console: Console, **kw: Any) -> None:
        calls.append({"case": case_dir, "accdb": accdb, **kw})
        console.print("generic diff ran")

    monkeypatch.setattr(cwp, "run_generic_diff", _fake)
    argv = ["--gtopt-case", str(case), "--plexos-res-zip", str(res)]
    argv += ["--cache-dir", str(tmp_path / "c"), "--no-uc-drilldown"]
    rc, out = _run_main([*argv, "--run-diff-dir", str(tmp_path / "d")], capsys)
    assert rc == 0 and "generic diff ran" in out
    assert calls[0]["out_dir"] == tmp_path / "d"
    assert calls[0]["accdb"].suffix == ".accdb"

    def _boom(*_a: Any, **_k: Any) -> None:
        raise ValueError("bad solution")

    monkeypatch.setattr(cwp, "run_generic_diff", _boom)
    rc, out = _run_main([*argv, "--run-diff"], capsys)
    assert rc == 0 and "Run diff skipped: bad solution" in out


def test_main_run_diff_needs_solution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = write_case(tmp_path / "case")
    log = tmp_path / "Log.txt"
    log.write_text("Best Integer Solution: 1000\n")
    rc, out = _run_main(
        [
            "--gtopt-case",
            str(case),
            "--plexos-log",
            str(log),
            "--no-uc-drilldown",
            "--run-diff",
        ],
        capsys,
    )
    assert rc == 0
    assert "Run diff skipped: needs the PLEXOS solution" in out
