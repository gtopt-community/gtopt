# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""End to end: run gtopt with ``output_usage_metrics`` and derive θ.

Two runs of one 4-block day (1 h blocks, load 60 MW):

* ``base``: G1 (gas_turbine, 10 $/MWh, 100 MW) serves the load; the
  peaker G2 (gas_engine, 80 $/MWh, committed pmin 20) stays off.
* ``sscc``: a CPF zone asks 30 MW of up reserve that only G2 offers
  (gated by its commitment), so G2 must run at its 20 MW floor every
  block — 4 extra operating hours and one start for the gas engines.

θ(gas_engine, cpf) = Δ maintenance cost / reserve MW·h is then known in
closed form from the maintenance parameters.  Skipped without a gtopt
binary that knows ``output_usage_metrics``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gtopt_compare_runs.reader import Run
from gtopt_usage import (
    ParamSet,
    WearParamSet,
    derive_theta,
    incremental_maintenance,
    usage_table,
)

pytestmark = pytest.mark.integration

LOAD = 60.0
NBLOCKS = 4


def _gtopt() -> str:
    env_bin = os.environ.get("GTOPT_BIN")
    if env_bin and Path(env_bin).exists():
        return env_bin
    found = shutil.which("gtopt")
    if found is None:
        pytest.skip("gtopt binary not found (set GTOPT_BIN)")
    return found


def _case(with_reserve: bool, wear: dict[str, Any] | None = None) -> dict[str, Any]:
    system: dict[str, Any] = {
        "name": "usage_e2e",
        "bus_array": [{"uid": 1, "name": "b1"}],
        "demand_array": [{"uid": 1, "name": "d1", "bus": 1, "capacity": LOAD}],
        "generator_array": [
            {
                "uid": 1,
                "name": "G1",
                "type": "gas_turbine",
                "bus": 1,
                "gcost": 10,
                "capacity": 100,
            },
            {
                "uid": 2,
                "name": "G2",
                "type": "gas_engine",
                "bus": 1,
                "gcost": 80,
                "capacity": 50,
            },
        ],
        "commitment_array": [
            {"uid": 1, "name": "uc2", "generator": "G2", "pmin": 20, **(wear or {})}
        ],
    }
    if with_reserve:
        system["reserve_zone_array"] = [
            {"uid": 1, "name": "cpf", "urreq": 30, "urcost": 100000}
        ]
        system["reserve_provision_array"] = [
            {
                "uid": 1,
                "name": "rp2",
                "generator": "G2",
                "reserve_zones": ["cpf"],
                "urmax": 30,
                "ur_provision_factor": 1,
                "urcost": 1,
                "ur_activation_factor": 0.1,
            }
        ]
    return {
        "options": {
            "output_format": "parquet",
            "output_usage_metrics": True,
            "model_options": {
                "use_single_bus": True,
                "scale_objective": 1,
                "demand_fail_cost": 100000,
            },
        },
        "simulation": {
            "block_array": [{"uid": b + 1, "duration": 1} for b in range(NBLOCKS)],
            "stage_array": [
                {
                    "uid": 1,
                    "first_block": 0,
                    "count_block": NBLOCKS,
                    "chronological": True,
                }
            ],
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        },
        "system": system,
    }


def _run(
    tmp: Path, name: str, with_reserve: bool, wear: dict[str, Any] | None = None
) -> Run:
    case = tmp / f"{name}.json"
    case.write_text(json.dumps(_case(with_reserve, wear)), encoding="utf-8")
    out = tmp / name
    proc = subprocess.run(
        [_gtopt(), str(case), "--output-directory", str(out)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp,
        env={**os.environ, "TMPDIR": str(tmp)},
    )
    if "output_usage_metrics" in proc.stderr and proc.returncode != 0:
        pytest.skip("gtopt binary predates output_usage_metrics")
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    return Run.load(out, planning=case, label=name)


PARAMS = {
    "technologies": {
        "gas_turbine": {"interval_eoh": 1000, "cost_per_event": 5000},
        "gas_engine": {"interval_eoh": 100, "cost_per_event": 1000, "kd": 5},
    }
}


def test_usage_and_theta_from_two_gtopt_runs(tmp_path: Path) -> None:
    base = _run(tmp_path, "base", with_reserve=False)
    sscc = _run(tmp_path, "sscc", with_reserve=True)

    units = ["G1", "G2"]  # a unit that never runs may have no rows
    ub = (
        usage_table(base)
        .groupby("name")
        .sum(numeric_only=True)
        .reindex(units, fill_value=0.0)
    )
    us = (
        usage_table(sscc)
        .groupby("name")
        .sum(numeric_only=True)
        .reindex(units, fill_value=0.0)
    )
    # base: only G1 runs, at the load every block (no ramps).
    assert ub.loc["G1", "hours"] == pytest.approx(NBLOCKS)
    assert ub.loc["G1", "ramp_mw"] == pytest.approx(0.0)
    assert ub.loc["G2", "hours"] == pytest.approx(0.0)
    # sscc: G2 sits at its committed floor every block.
    assert us.loc["G2", "hours"] == pytest.approx(NBLOCKS)
    assert us.loc["G2", "pmin_hours"] == pytest.approx(NBLOCKS)
    assert us.loc["G1", "hours"] == pytest.approx(NBLOCKS)
    # Invariant: energy balance — G1 + G2 serve the load every block.
    energy = us.loc["G1", "energy_mwh"] + us.loc["G2", "energy_mwh"]
    assert energy == pytest.approx(LOAD * NBLOCKS)
    assert us.loc["G2", "energy_mwh"] == pytest.approx(20.0 * NBLOCKS)

    report = derive_theta(base, sscc, ParamSet.from_dict(PARAMS))
    theta = report.theta.set_index(["technology", "service", "direction"]).loc[
        ("gas_engine", "cpf", "up")
    ]
    # Δ EOH(G2) = 4 h + kd 5 · starts (1 when the first block counts as a
    # start) → events ΔEOH/100 → cost ×1000; reserve 30 MW · 4 h = 120.
    d_eoh = NBLOCKS + 5.0 * us.loc["G2", "starts"]
    assert theta["reserve_mwh_b"] == pytest.approx(120.0)
    assert theta["theta_maintenance"] == pytest.approx(d_eoh / 100 * 1000 / 120)
    # Known activation cost: γ · SRMC = 0.1 · 80 $/MWh per MW·h held.
    assert theta["theta_known_activation"] == pytest.approx(8.0)
    assert theta["theta_total"] == pytest.approx(
        theta["theta_maintenance"] + theta["theta_known"]
    )
    # The base gas turbine is unaffected (same hours, no reserve).
    tech = report.technologies.set_index("technology")
    assert tech.loc["gas_turbine", "delta_cost"] == pytest.approx(0.0)


WEAR = {"startup_wear_cost": 50, "wear_hour_cost": 4, "wear_load_cost": -0.1}


def test_operation_wear_adders_end_to_end(tmp_path: Path) -> None:
    """Wear adders: split cost streams that add up to the objective delta."""
    plain = _run(tmp_path, "plain", with_reserve=True)
    worn = _run(tmp_path, "worn", with_reserve=True, wear=WEAR)
    op = worn.cells("Commitment", "operation_wear_cost_sol")
    if op.empty:
        pytest.skip("gtopt binary predates the Commitment wear adders")
    start = worn.cells("Commitment", "startup_wear_cost_sol")
    # G2 sits at its 20 MW floor: a + b·(p − Pmin) = 4 every block.
    assert op["value"].to_numpy() == pytest.approx([4.0] * NBLOCKS)
    starts = usage_table(worn).groupby("name")["starts"].sum().get("G2", 0.0)
    assert start["value"].sum() == pytest.approx(50.0 * starts)
    obj_plain, obj_worn = plain.objective(), worn.objective()
    assert obj_plain is not None and obj_worn is not None
    assert obj_worn - obj_plain == pytest.approx(4.0 * NBLOCKS + 50.0 * starts)


COUNTERS = {
    "discount_rate": 0.0,
    "technologies": {
        "gas_engine": {
            "kd": 5,
            "types": [
                {"name": "minor", "interval_eoh": 10, "cost": 100},
                {"name": "major", "interval_eoh": 100, "cost": 1000},
            ],
        }
    },
}


def test_incremental_maintenance_from_two_gtopt_runs(tmp_path: Path) -> None:
    """Counters on real gtopt output: rate = ΔEOH · Σ C/HOM; θ per MW·h."""
    base = _run(tmp_path, "base", with_reserve=False)
    sscc = _run(tmp_path, "sscc", with_reserve=True)
    rep = incremental_maintenance(base, sscc, WearParamSet.from_dict(COUNTERS))
    g2 = rep.units.set_index("name").loc["G2"]
    starts = usage_table(sscc).groupby("name")["starts"].sum().get("G2", 0.0)
    d_eoh = NBLOCKS + 5.0 * starts
    assert g2["delta_eoh"] == pytest.approx(d_eoh)
    assert g2["rate_delta"] == pytest.approx(d_eoh * (100 / 10 + 1000 / 100))
    th = rep.theta.set_index(["technology", "service", "direction"]).loc[
        ("gas_engine", "cpf", "up")
    ]
    assert th["theta_rate"] == pytest.approx(g2["rate_delta"] / 120.0)
    # identical runs: nothing is charged
    same = incremental_maintenance(sscc, sscc, WearParamSet.from_dict(COUNTERS))
    assert same.units["event_delta"].abs().max() == pytest.approx(0.0)
