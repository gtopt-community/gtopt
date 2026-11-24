# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for tools/check_rts_gmlc_reserves.py (synthetic gtopt outputs)."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_rts_gmlc_reserves as ck  # pylint: disable=wrong-import-position


def _case() -> dict:
    return {
        "simulation": {"block_array": [{"uid": 1}, {"uid": 2}]},
        "system": {
            "generator_array": [
                {"uid": 1, "capacity": 100.0},
                {"uid": 2, "capacity": 50.0, "pmin": 25.0},
            ],
            "reserve_zone_array": [
                {"uid": 1, "name": "Spin", "urreq": [[10.0, 20.0]]},
                {"uid": 2, "name": "RegDn", "drreq": [[5.0, 5.0]]},
            ],
            "reserve_provision_array": [
                {"uid": 1, "reserve_zones": [1], "urmax": 30.0},
                {"uid": 2, "reserve_zones": [1], "urmax": 30.0},
                {"uid": 3, "reserve_zones": [2], "drmax": 30.0},
            ],
            "inertia_zone_array": [{"uid": 1, "requirement": 400.0}],
            "inertia_provision_array": [
                {"uid": 1, "generator": 1, "inertia_constant": 4.0, "rated_power": 100.0},
                {"uid": 2, "generator": 2, "inertia_constant": 2.0, "rated_power": 50.0},
            ],
        },
    }  # fmt: skip


def _write(path: Path, rows: list[tuple[int, int, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["scenario", "stage", "block", "uid", "value"])
        w.writerows([0, 1, b, u, v] for b, u, v in rows)


def _outputs(out: Path, status: str = "optimal", up2: float = 10.0) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "solution.csv").write_text(f"scene,status_name\n0,{status}\n")
    _write(
        out / "ReserveProvision" / f"uprovision_sol{ck.SUFFIX}",
        [(1, 1, 6.0), (1, 2, 4.0), (2, 1, 10.0), (2, 2, up2)],
    )
    _write(
        out / "ReserveProvision" / f"dprovision_sol{ck.SUFFIX}",
        [(1, 3, 5.0), (2, 3, 5.0)],
    )
    # pf: unit 1 = 4*100/100 = 4, unit 2 = 2*50/25 = 4
    _write(
        out / "InertiaProvision" / f"provision_sol{ck.SUFFIX}",
        [(1, 1, 80.0), (1, 2, 20.0), (2, 1, 100.0)],
    )


def test_inertia_factors() -> None:
    assert ck.inertia_factors(_case()["system"]) == {1: 4.0, 2: 4.0}


def test_check_pass(tmp_path: Path) -> None:
    _outputs(tmp_path)
    assert not ck.check(_case(), tmp_path)


def test_check_reserve_and_inertia_shortfall(tmp_path: Path) -> None:
    _outputs(tmp_path, up2=9.0)
    case = _case()
    case["system"]["inertia_zone_array"][0]["requirement"] = 401.0
    errors = ck.check(case, tmp_path)
    assert errors == [
        "Spin block 2: provided 19.000 < requirement 20.000",
        "inertia block 1: 400.0 < requirement 401.0 MWs",
        "inertia block 2: 400.0 < requirement 401.0 MWs",
    ]


def test_check_not_optimal(tmp_path: Path) -> None:
    _outputs(tmp_path, status="infeasible")
    assert ck.check(_case(), tmp_path) == [
        "solve status ['infeasible'], expected ['optimal']"
    ]


def test_check_missing_outputs_read_as_zero(tmp_path: Path) -> None:
    tmp_path.joinpath("solution.csv").write_text("status_name\noptimal\n")
    case = _case()
    del case["system"]["reserve_zone_array"][1]
    errors = ck.check(case, tmp_path)
    assert errors[0] == "Spin block 1: provided 0.000 < requirement 10.000"
    assert errors[-1] == "inertia block 2: 0.0 < requirement 400.0 MWs"


def test_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], check: bool, cwd: Path) -> None:
        assert check
        calls.append(cmd)
        if "rts_gmlc_to_gtopt" in cmd:
            out_json = Path(cmd[cmd.index("-o") + 1])
            out_json.write_text(json.dumps(_case()), encoding="utf-8")
        else:
            _outputs(Path(cwd) / "output")

    monkeypatch.setattr(ck.subprocess, "run", fake_run)
    args = ["--gtopt", "gt", "--work-dir", str(tmp_path), "--no-download"]
    assert ck.main([*args, "--solver", "highs"]) == 0
    assert "--no-download" in calls[0]
    assert calls[1][-2:] == ["--solver", "highs"]

    def failing_run(cmd: list[str], check: bool, cwd: Path) -> None:
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(ck.subprocess, "run", failing_run)
    assert ck.main(args) == 2


def test_main_check_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], check: bool, cwd: Path) -> None:
        del check
        if "rts_gmlc_to_gtopt" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_text(
                json.dumps(_case()), encoding="utf-8"
            )
        else:
            _outputs(Path(cwd) / "output", up2=0.0)

    monkeypatch.setattr(ck.subprocess, "run", fake_run)
    assert ck.main(["--gtopt", "gt", "--work-dir", str(tmp_path)]) == 1
