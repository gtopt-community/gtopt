# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Workbook with EVERY element class -> igtopt -> gtopt.

A small but coherent case holding one element of every ``Simulation`` /
``System`` array gtopt accepts (gas network, LNG terminal, carriers,
allowance pool, line commitment, inertia, decision variables, hydro
chain, rights, ...) is authored as an Excel workbook, converted with
igtopt, walked against the C++ contract and — when a gtopt binary is
available — loaded by gtopt itself (``--lp-only``: parse, validate and
assemble the LP of every element, no solve).

``test_kitchen_sink_covers_every_array`` fails when a new array is added
to the C++ contract without a row here, so a new element class cannot
ship without an end-to-end igtopt test.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gtopt_shared import json_contracts as jc
from igtopt.igtopt import _run
from igtopt.template_builder import TEMPLATE_EXCLUDED_ARRAYS

openpyxl = pytest.importorskip("openpyxl")

#: Flat options-sheet rows (routed by igtopt into the option sub-objects).
OPTIONS: dict[str, Any] = {
    "demand_fail_cost": 1000,
    "use_kirchhoff": True,
    "scale_objective": 1000,
    "gas_temperature": 288.15,
    "slp_iterations": 0,
    "sanity_enable": True,
    "solver_time_limit": 60,
    "max_iterations": 2,
    "output_format": "csv",
}

_J = json.dumps

#: One sheet per array; values typed as an Excel user would type them
#: (JSON lists / objects as text cells).
KITCHEN_SINK: dict[str, list[dict[str, Any]]] = {
    # ── simulation ──────────────────────────────────────────────────────
    "block_array": [{"uid": 1, "duration": 1}, {"uid": 2, "duration": 1}],
    "stage_array": [{"uid": 1, "first_block": 0, "count_block": 2}],
    "scenario_array": [{"uid": 1, "probability_factor": 1}],
    "phase_array": [{"uid": 1, "first_stage": 0, "count_stage": 1}],
    "scene_array": [{"uid": 1, "first_scenario": 0, "count_scenario": 1}],
    "aperture_array": [{"uid": 1, "source_scenario": 1, "probability_factor": 1}],
    "iteration_array": [{"index": 0, "update_lp": 1}],
    # ── electric network ────────────────────────────────────────────────
    "bus_array": [{"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"}],
    "fuel_array": [{"uid": 1, "name": "gas", "price": 5}],
    "generator_array": [
        {
            "uid": 1,
            "name": "g1",
            "bus": "b1",
            "pmax": 200,
            "gcost": 20,
            "capacity": 200,
            "initial_generation": 50,
        },
        {"uid": 2, "name": "g_gas", "bus": "b2", "pmax": 50, "gcost": 0},
        {"uid": 3, "name": "g_lng", "bus": "b2", "pmax": 50, "gcost": 0},
        {"uid": 4, "name": "g_hyd", "bus": "b1", "pmax": 50, "gcost": 0},
        {
            "uid": 5,
            "name": "g_sc",
            "bus": "b2",
            "pmax": 30,
            "gcost": 40,
            "capacity": 30,
        },
        {"uid": 6, "name": "g_bat2", "bus": "b1", "pmax": 10, "gcost": 0},
    ],
    "demand_array": [
        {"uid": 1, "name": "d1", "bus": "b2", "lmax": 60, "capacity": 60},
        {"uid": 2, "name": "d_pump", "bus": "b1", "lmax": 5},
        {"uid": 3, "name": "d_bat2", "bus": "b1", "lmax": 10},
    ],
    "line_array": [
        {
            "uid": 1,
            "name": "l1",
            "bus_a": "b1",
            "bus_b": "b2",
            "reactance": 0.1,
            "tmax_ab": 300,
            "tmax_ba": 300,
        }
    ],
    "generator_profile_array": [
        {"uid": 1, "name": "gp1", "generator": "g1", "profile": 1.0}
    ],
    "demand_profile_array": [{"uid": 2, "name": "dp1", "demand": "d1", "profile": 1.0}],
    "capacity_profile_array": [
        {
            "uid": 3,
            "name": "cp1",
            "owner_kind": "generator",
            "owner": "g_sc",
            "profile": 1.0,
        }
    ],
    "battery_array": [
        {
            "uid": 1,
            "name": "bat1",
            "bus": "b1",
            "emax": 10,
            "eini": 5,
            "pmax_charge": 5,
            "pmax_discharge": 5,
            "exclusive_charge_discharge": False,
        },
        {"uid": 2, "name": "bat2", "emax": 10, "eini": 5},
    ],
    "converter_array": [
        {
            "uid": 1,
            "name": "conv2",
            "battery": "bat2",
            "generator": "g_bat2",
            "demand": "d_bat2",
            "capacity": 10,
        }
    ],
    # ── carriers ────────────────────────────────────────────────────────
    "thermal_node_array": [{"uid": 1, "name": "th1"}],
    "thermal_storage_array": [
        {"uid": 1, "name": "ths1", "thermal_node": "th1", "emax": 10, "eini": 5}
    ],
    "hydrogen_node_array": [{"uid": 1, "name": "h2"}],
    "hydrogen_storage_array": [
        {"uid": 1, "name": "h2s", "hydrogen_node": "h2", "emax": 10, "eini": 5}
    ],
    "hydrogen_demand_array": [
        {"uid": 1, "name": "h2d", "hydrogen_node": "h2", "lmax": 0.5}
    ],
    "ammonia_node_array": [{"uid": 1, "name": "nh3"}],
    "ammonia_storage_array": [
        {"uid": 1, "name": "nh3s", "ammonia_node": "nh3", "emax": 10, "eini": 5}
    ],
    "carrier_converter_array": [
        {
            "uid": 1,
            "name": "elz",
            "from_carrier": "electric",
            "to_carrier": "hydrogen",
            "from_node": "b1",
            "to_node": "h2",
            "efficiency": 0.7,
            "capacity": 5,
        }
    ],
    # ── emissions ───────────────────────────────────────────────────────
    "emission_array": [{"uid": 1, "name": "co2"}],
    "allowance_pool_array": [
        {"uid": 1, "name": "ets", "emission": "co2", "emax": 1000, "eini": 100}
    ],
    "emission_zone_array": [{"uid": 1, "name": "ez1", "emission": "co2", "price": 10}],
    "emission_source_array": [
        {
            "uid": 1,
            "name": "es1",
            "generator": "g1",
            "zone": "ez1",
            "emission": "co2",
            "rate": 0.5,
        }
    ],
    # ── LNG and gas network ─────────────────────────────────────────────
    "lng_terminal_array": [
        {
            "uid": 1,
            "name": "lng1",
            "emax": 100,
            "eini": 50,
            "generators": _J([{"generator": "g_lng", "heat_rate": 0.18}]),
        }
    ],
    "gas_node_array": [
        {"uid": 1, "name": "n1", "pmin": 30, "pmax": 70},
        {"uid": 2, "name": "n2", "pmin": 30, "pmax": 70},
    ],
    "gas_supply_array": [
        {"uid": 1, "name": "field1", "gas_node": "n1", "fmax": 50, "cost": 1}
    ],
    "gas_demand_array": [
        {"uid": 1, "name": "gd1", "gas_node": "n2", "load": 1, "fail_cost": 100}
    ],
    "gas_pipe_array": [
        {
            "uid": 1,
            "name": "p1",
            "gas_node_a": "n1",
            "gas_node_b": "n2",
            "fmax": 40,
            "weymouth_k": 1.0,
        }
    ],
    "gas_compressor_array": [
        {
            "uid": 1,
            "name": "c1",
            "gas_node_in": "n1",
            "gas_node_out": "n2",
            "fmax": 20,
            "max_ratio": 1.5,
        }
    ],
    "gas_turbine_array": [
        {
            "uid": 1,
            "name": "gt1",
            "generator": "g_gas",
            "gas_node": "n2",
            "heat_rate": 0.2,
        }
    ],
    # ── reserves, commitment, inertia ───────────────────────────────────
    "reserve_zone_array": [{"uid": 1, "name": "rz1", "urreq": 5, "urcost": 100}],
    "reserve_provision_array": [
        {
            "uid": 1,
            "name": "rp1",
            "generator": "g1",
            "reserve_zones": _J(["rz1"]),
            "urmax": 10,
        }
    ],
    "commitment_array": [{"uid": 1, "name": "cm1", "generator": "g1", "relax": True}],
    "simple_commitment_array": [
        {"uid": 1, "name": "sc1", "generator": "g_sc", "relax": True}
    ],
    "line_commitment_array": [{"uid": 1, "name": "lc1", "line": "l1", "relax": True}],
    "inertia_zone_array": [{"uid": 1, "name": "iz1", "requirement": 10, "cost": 50}],
    "inertia_provision_array": [
        {
            "uid": 1,
            "name": "ip1",
            "generator": "g1",
            "inertia_zones": _J(["iz1"]),
            "inertia_constant": 4,
            "rated_power": 200,
        }
    ],
    # ── hydro ───────────────────────────────────────────────────────────
    "junction_array": [
        {"uid": 1, "name": "j1"},
        {"uid": 2, "name": "j2", "drain": True},
    ],
    "waterway_array": [
        {"uid": 1, "name": "w1", "junction_a": "j1", "junction_b": "j2", "fmax": 100},
        {"uid": 2, "name": "w2", "junction_a": "j2", "junction_b": "j1", "fmax": 10},
    ],
    "flow_array": [{"uid": 1, "name": "inflow1", "junction": "j1", "discharge": 5}],
    "reservoir_array": [
        {"uid": 1, "name": "r1", "junction": "j1", "emax": 1000, "eini": 500}
    ],
    "reservoir_seepage_array": [
        {"uid": 1, "name": "rs1", "waterway": "w1", "reservoir": "r1", "slope": 0}
    ],
    "reservoir_discharge_limit_array": [
        {"uid": 1, "name": "rdl1", "waterway": "w1", "reservoir": "r1"}
    ],
    "turbine_array": [
        {
            "uid": 1,
            "name": "t1",
            "waterway": "w1",
            "generator": "g_hyd",
            "production_factor": 1,
        }
    ],
    "pump_array": [
        {
            "uid": 1,
            "name": "pump1",
            "waterway": "w2",
            "demand": "d_pump",
            "pump_factor": 1,
        }
    ],
    "reservoir_production_factor_array": [
        {
            "uid": 1,
            "name": "rpf1",
            "turbine": "t1",
            "reservoir": "r1",
            "mean_production_factor": 1,
        }
    ],
    "flow_right_array": [
        {"uid": 1, "name": "fr1", "junction_a": "j1", "fmax": 1, "fcost": 1}
    ],
    "volume_right_array": [
        {"uid": 1, "name": "vr1", "reservoir": "r1", "emax": 100, "eini": 0}
    ],
    # ── user modelling ──────────────────────────────────────────────────
    "user_param_array": [{"name": "k_cap", "value": 1000}],
    "decision_variable_array": [
        {"uid": 1, "name": "dv1", "lower_bound": 0, "upper_bound": 10, "cost": 1}
    ],
    "user_constraint_array": [
        {
            "uid": 1,
            "name": "uc1",
            "expression": "generator('g1').generation <= 1000",
        }
    ],
    "plant_array": [
        {"uid": 1, "name": "plant1", "generator_names": _J(["g_sc"]), "pmax": 30}
    ],
    "future_cost_array": [{"uid": 1, "name": "fcf1", "mode": "noload"}],
}


def _contract_arrays() -> set[str]:
    arrays = set(jc.array_types("Simulation")) | set(jc.array_types("System"))
    return arrays - set(TEMPLATE_EXCLUDED_ARRAYS)


def _write_workbook(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "options"
    ws.append(["option", "value"])
    for key, value in OPTIONS.items():
        ws.append([key, value])
    for sheet, rows in KITCHEN_SINK.items():
        cols: list[str] = []
        for row in rows:
            cols += [c for c in row if c not in cols]
        data = wb.create_sheet(sheet)
        data.append(cols)
        for row in rows:
            data.append([row.get(c) for c in cols])
    wb.save(path)


@pytest.fixture(scope="module")
def planning_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("kitchen_sink")
    xlsx = tmp / "kitchen_sink.xlsx"
    _write_workbook(xlsx)
    args = argparse.Namespace(
        filenames=[str(xlsx)],
        json_file=tmp / "kitchen_sink.json",
        input_directory=tmp / "kitchen_sink",
        input_format="parquet",
        name="kitchen_sink",
        compression="gzip",
        skip_nulls=True,
        parse_unexpected_sheets=False,
        pretty=True,
        zip=False,
    )
    assert _run(args) == 0
    return tmp / "kitchen_sink.json"


def test_kitchen_sink_covers_every_array() -> None:
    """One sheet per Simulation / System array gtopt accepts."""
    missing = _contract_arrays() - set(KITCHEN_SINK)
    assert not missing, f"add a row for {sorted(missing)} to KITCHEN_SINK"
    assert not set(KITCHEN_SINK) - _contract_arrays()


def test_kitchen_sink_converts_to_accepted_json(planning_file: Path) -> None:
    planning = json.loads(planning_file.read_text())
    issues = jc.find_schema_problems(planning)
    assert not issues, [i.message for i in issues]
    for sheet, rows in KITCHEN_SINK.items():
        section = "simulation" if sheet in jc.array_types("Simulation") else "system"
        assert len(planning[section][sheet]) == len(rows), sheet
    opts = planning["options"]
    assert opts["gas_options"] == {"gas_temperature": 288.15, "slp_iterations": 0}
    assert opts["sanity_options"] == {"enable": True}
    assert opts["solver_options"] == {"time_limit": 60}
    lng = planning["system"]["lng_terminal_array"][0]
    assert lng["generators"] == [{"generator": "g_lng", "heat_rate": 0.18}]
    assert planning["system"]["reserve_provision_array"][0]["reserve_zones"] == ["rz1"]


def test_kitchen_sink_is_loaded_by_gtopt(
    planning_file: Path, gtopt_bin: str, tmp_path: Path
) -> None:
    """gtopt parses, validates and assembles the LP of every element."""
    if jc.gtopt_json_schema(gtopt_bin) is None:
        pytest.skip("gtopt binary predates the full --json-schema contract")
    env = {**os.environ, "TMPDIR": str(tmp_path)}
    proc = subprocess.run(
        [
            gtopt_bin,
            str(planning_file),
            "--lp-only",
            "--output-directory",
            str(tmp_path / "out"),
            "--log-directory",
            str(tmp_path / "logs"),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=planning_file.parent,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]


def test_bool_columns_are_written_as_json_booleans() -> None:
    """A FALSE cell in a column with blanks must not reach gtopt as 0."""
    # pylint: disable-next=import-outside-toplevel
    import pandas as pd

    # pylint: disable-next=import-outside-toplevel
    from igtopt.igtopt import _contract_bool_columns, df_to_str

    bools = _contract_bool_columns("battery_array")
    assert "exclusive_charge_discharge" in bools and "emax" not in bools
    assert not _contract_bool_columns("not_a_sheet")
    df = pd.DataFrame(
        {
            "uid": [1, 2, 3],
            "name": ["a", "b", "c"],
            "exclusive_charge_discharge": [0.0, None, 1.0],
            "daily_cycle": [True, "yes", None],
            "emax": [0.0, 1.0, 2.0],
        }
    )
    records = json.loads(df_to_str(df, skip_nulls=True, bool_columns=bools))
    assert records[0]["exclusive_charge_discharge"] is False
    assert "exclusive_charge_discharge" not in records[1]
    assert records[2]["exclusive_charge_discharge"] is True
    assert records[0]["daily_cycle"] is True
    assert records[1]["daily_cycle"] == "yes"
    assert records[0]["emax"] == 0.0
