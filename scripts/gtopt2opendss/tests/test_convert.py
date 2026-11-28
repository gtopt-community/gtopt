# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for gtopt2opendss.convert."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from gtopt2opendss.convert import (
    Dispatch,
    _sched,
    planning_to_dss,
    read_dispatch,
    tan_phi,
)


def _planning() -> dict[str, Any]:
    return {
        "simulation": {
            "block_array": [{"uid": 1, "duration": 1}, {"uid": 2, "duration": 1}],
            "stage_array": [{"uid": 1, "first_block": 0, "count_block": 2}],
            "scenario_array": [{"uid": 1}],
        },
        "system": {
            "name": "t c",
            "bus_array": [
                {"uid": 1, "name": "s", "voltage": 12.47, "reference_voltage": 1.03},
                {"uid": 2, "name": "a", "voltage": 12.47, "shunt_q": 0.1},
                {"uid": 3, "name": "b", "voltage": 4.16},
                {"uid": 4, "name": "c", "voltage": 4.16},
            ],
            "line_array": [
                {
                    "uid": 1,
                    "name": "l1",
                    "bus_a": 1,
                    "bus_b": 2,
                    "resistance": 0.5,
                    "reactance": 1.0,
                },
                {
                    "uid": 2,
                    "name": "t1",
                    "bus_a": "a",
                    "bus_b": "b",
                    "resistance": 0.01,
                    "reactance": 0.05,
                    "type": "transformer",
                    "tmax_ab": 5.0,
                },
                {
                    "uid": 3,
                    "name": "r1",
                    "bus_a": "b",
                    "bus_b": "c",
                    "resistance": 0.0,
                    "reactance": 0.0,
                    "tap_ratio": 0.95,
                    "tap_ratio_min": 0.9,
                    "tap_ratio_max": 1.1,
                },
            ],
            "demand_array": [
                {
                    "uid": 1,
                    "name": "d1",
                    "bus": "c",
                    "lmax": [[1.0, 2.0]],
                    "power_factor": 0.8,
                },
                {"uid": 2, "name": "d2", "bus": 2, "lmax": 0.5},
            ],
            "generator_array": [
                {"uid": 1, "name": "source", "bus": "s", "pmax": 100},
                {
                    "uid": 2,
                    "name": "pv",
                    "bus": "c",
                    "pmax": [[0.3, 0.4]],
                    "power_factor": -0.9,
                },
            ],
            "battery_array": [{"uid": 1, "name": "bat", "bus": "c"}],
        },
    }


def test_render_without_dispatch() -> None:
    text = planning_to_dss(_planning(), block=1)
    assert "New Circuit.t_c basekv=12.47 pu=1.03" in text
    assert "New Line.l1 bus1=s bus2=a phases=3 r1=0.5 x1=1" in text
    assert "New Transformer.t1" in text and "kVAs=[5000 5000]" in text
    assert "taps=[1 1.05263158]" in text  # r1 tap 0.95
    assert "New Load.d1" in text and "kW=2000" in text and "kvar=1500" in text
    assert "kW=500" in text  # scalar lmax
    assert "New Generator.pv" in text and "kvar=-193.7" in text
    assert "Generator.source" not in text
    assert "New Generator.bat_bat" in text and "kW=0" in text
    assert "New Capacitor.shunt_a" in text
    assert "Set VoltageBases=[4.16 12.47]" in text
    assert "Set ControlMode=OFF" in text


def test_render_with_dispatch() -> None:
    d = Dispatch(
        load={"d1": 0.7},
        qload={"d1": 0.1},
        gen={"pv": 0.2},
        qgen={"pv": 0.05},
        battery={"bat": -0.25},
        tap={"t1": 1.0},
        line_capacity={"t1": 8.0},
    )
    text = planning_to_dss(_planning(), dispatch=d)
    assert "kW=700" in text and "kvar=100" in text
    assert "kW=200" in text and "kvar=50" in text
    assert "New Load.bat_bat" in text and "kW=250" in text
    assert "kVAs=[8000 8000]" in text


def test_slack_fallbacks() -> None:
    p = _planning()
    del p["system"]["bus_array"][0]["reference_voltage"]
    p["system"]["bus_array"][1]["reference_theta"] = 0.0
    assert "bus1=a R1=0" in planning_to_dss(p)
    del p["system"]["bus_array"][1]["reference_theta"]
    assert "bus1=s R1=0" in planning_to_dss(p)


def test_helpers() -> None:
    assert _sched(None, 0) is None
    assert _sched("file", 0) is None
    assert _sched(3, 5) == 3.0
    assert _sched([[1, 2], [3]], 2) == 3.0
    assert _sched([[1, 2]], 9) == 2.0
    assert _sched([], 0) is None
    assert tan_phi(None) == 0.0
    assert tan_phi(1.0) == 0.0
    assert tan_phi(0.0) == 0.0
    assert tan_phi(-0.8) == pytest.approx(-0.75)
    assert tan_phi(0.6) == pytest.approx(math.sqrt(1 / 0.36 - 1))


def _write(path: Path, rows: list[tuple[int, int, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"1,1,{b},{u},{v}" for b, u, v in rows)
    path.write_text('"scenario","stage","block","uid","value"\n' + body + "\n")


def test_read_dispatch(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _write(out / "Demand" / "load_sol_s0_p0.csv", [(1, 1, 0.9), (2, 1, 1.5)])
    _write(out / "Generator" / "generation_sol_s0_p0.csv", [(2, 2, 0.3)])
    _write(out / "Generator" / "qgen_sol_s0_p0.csv", [(2, 2, 0.1)])
    _write(out / "Line" / "vsqtap_sol_s0_p0.csv", [(2, 3, 1.0)])
    _write(out / "Bus" / "vsq_sol_s0_p0.csv", [(2, 3, 0.9409), (2, 1, 1.0)])
    _write(out / "Battery" / "fout_sol_s0_p0.csv", [(2, 1, 0.2)])
    _write(out / "Battery" / "finp_sol_s0_p0.csv", [(2, 1, 0.05)])
    cap = out / "Line" / "capainst_sol_s0_p0.csv"
    cap.write_text('"scenario","stage","uid","value"\n1,1,2,7.5\n')
    d = read_dispatch(_planning(), out, 1)
    assert d.load["d1"] == pytest.approx(1.5)
    assert d.qload["d1"] == pytest.approx(1.5 * 0.75)
    assert d.gen["pv"] == pytest.approx(0.3) and d.qgen["pv"] == pytest.approx(0.1)
    assert d.tap["r1"] == pytest.approx(0.97)
    assert d.battery["bat"] == pytest.approx(0.15)
    assert d.line_capacity == {"t1": 7.5}
    empty = read_dispatch(_planning(), tmp_path / "none", 0)
    assert not empty.load and not empty.battery


def test_unbuilt_candidate_lines_are_skipped() -> None:
    p = _planning()
    p["system"]["line_array"].append(
        {
            "uid": 9,
            "name": "cand",
            "bus_a": "s",
            "bus_b": "a",
            "resistance": 0.5,
            "reactance": 1.0,
            "capacity": 0,
            "expcap": 5,
        }
    )
    assert "Line.cand" not in planning_to_dss(p)
    built = Dispatch(line_capacity={"cand": 5.0})
    assert "New Line.cand" in planning_to_dss(p, dispatch=built)
