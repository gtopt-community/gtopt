# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Small gtopt plannings for exporter round-trip tests.

:func:`tiny_planning` builds a case that both external dialects
(PLP, PSR SDDP) can carry without loss: 2 stages x 2 blocks, 2 equally
likely scenarios, 2 buses joined by one line, a thermal unit with
per-block capacity and per-stage cost, a hydro plant with a reservoir
and a stochastic inflow, a solar unit with a profile, two demands and a
battery.  All schedules are inline arrays, so the case needs no input
tables.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def tiny_planning() -> dict[str, Any]:
    """Return the planning dict (see module docstring)."""
    return {
        "options": {
            "method": "sddp",
            "input_directory": ".",
            "input_format": "parquet",
            "output_directory": "results",
            "model_options": {"demand_fail_cost": 1000.0, "use_kirchhoff": True},
        },
        "simulation": {
            "block_array": [
                {"uid": 1, "duration": 10.0},
                {"uid": 2, "duration": 14.0},
                {"uid": 3, "duration": 12.0},
                {"uid": 4, "duration": 12.0},
            ],
            "stage_array": [
                {"uid": 1, "first_block": 0, "count_block": 2, "month": "april"},
                {"uid": 2, "first_block": 2, "count_block": 2, "month": "may"},
            ],
            "scenario_array": [
                {"uid": 1, "probability_factor": 0.5},
                {"uid": 2, "probability_factor": 0.5},
            ],
        },
        "system": {
            "name": "tiny",
            "bus_array": [
                {"uid": 1, "name": "Norte220", "voltage": 220.0},
                {"uid": 2, "name": "Sur220", "voltage": 220.0},
            ],
            "line_array": [
                {
                    "uid": 1,
                    "name": "Norte_Sur",
                    "bus_a": 1,
                    "bus_b": 2,
                    "voltage": 220.0,
                    "reactance": 20.0,
                    "tmax_ab": [[100.0, 100.0], [80.0, 80.0]],
                    "tmax_ba": [[100.0, 100.0], [80.0, 80.0]],
                }
            ],
            "generator_array": [
                {
                    "uid": 4,
                    "name": "TERMO",
                    "bus": 2,
                    "capacity": 150.0,
                    "pmax": [[150.0, 150.0], [150.0, 75.0]],
                    "pmin": 0.0,
                    "gcost": [[50.0, 50.0], [55.0, 55.0]],
                    "type": "thermal",
                },
                {
                    "uid": 1,
                    "name": "LAGO",
                    "bus": 1,
                    "capacity": 100.0,
                    "pmax": 100.0,
                    "gcost": 0.0,
                    "type": "hydro_reservoir",
                },
                {
                    "uid": 8,
                    "name": "PV1",
                    "bus": 2,
                    "capacity": 50.0,
                    "pmax": 50.0,
                    "gcost": 0.0,
                    "type": "solar",
                },
            ],
            "capacity_profile_array": [
                {
                    "uid": 8,
                    "name": "PV1",
                    "owner_kind": "generator",
                    "owner": "PV1",
                    "profile": [[[0.8, 0.0], [0.6, 0.1]], [[0.8, 0.0], [0.6, 0.1]]],
                }
            ],
            "demand_array": [
                {
                    "uid": 1,
                    "name": "Norte220",
                    "bus": 1,
                    "lmax": [[60.0, 80.0], [65.0, 85.0]],
                },
                {
                    "uid": 2,
                    "name": "Sur220",
                    "bus": 2,
                    "lmax": [[100.0, 140.0], [110.0, 150.0]],
                },
            ],
            "junction_array": [{"uid": 1, "name": "LAGO", "drain": True}],
            "reservoir_array": [
                {
                    "uid": 1,
                    "name": "LAGO",
                    "junction": "LAGO",
                    "eini": 500.0,
                    "emin": 100.0,
                    "emax": 1000.0,
                    "capacity": 1000.0,
                    "flow_conversion_rate": 0.0036,
                }
            ],
            "turbine_array": [
                {
                    "uid": 1,
                    "name": "LAGO",
                    "generator": "LAGO",
                    "junction_a": "LAGO",
                    "production_factor": 2.0,
                }
            ],
            "flow_array": [
                {
                    "uid": 1,
                    "name": "LAGO",
                    "junction": "LAGO",
                    "discharge": [
                        [[20.0, 20.0], [25.0, 25.0]],
                        [[15.0, 15.0], [10.0, 10.0]],
                    ],
                }
            ],
            "battery_array": [
                {
                    "uid": 5,
                    "name": "BESS1",
                    "bus": 2,
                    "input_efficiency": 0.95,
                    "output_efficiency": 0.95,
                    "emin": 0.0,
                    "emax": 160.0,
                    "capacity": 160.0,
                    "pmax_charge": 40.0,
                    "pmax_discharge": 40.0,
                }
            ],
        },
    }


def write_tiny_case(directory: Path, name: str = "tiny") -> Path:
    """Write :func:`tiny_planning` as ``<directory>/<name>.json``."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(tiny_planning(), indent=2) + "\n", encoding="utf-8")
    return path


def cascade_planning() -> dict[str, Any]:
    """:func:`tiny_planning` grown into a lossy hydro cascade.

    Adds what the exporters handle only partly, so tests can reach their
    warning and side-file paths: a series plant below the reservoir fed
    by a spill, an extraction and a seepage waterway, a run-of-river
    turbine with its own flow, a reservoir production-factor curve,
    time-varying reservoir limits, a lossy line with maintenance, a DC
    line, a bus without kV in its name, a battery without bus and a
    demand on an unknown bus.
    """
    planning = tiny_planning()
    sysm = planning["system"]
    sysm["bus_array"].append({"uid": 3, "name": "Centro", "voltage": 110.0})
    sysm["line_array"] += [
        {
            "uid": 2,
            "name": "Norte_Centro",
            "bus_a": 1,
            "bus_b": 3,
            "voltage": 110.0,
            "reactance": 10.0,
            "resistance": 1.0,
            "use_line_losses": True,
            "loss_segments": 4,
            "loss_allocation_mode": "sender",
            "tmax_ab": [[50.0, 40.0], [50.0, 50.0]],
            "tmax_ba": [[50.0, 40.0], [50.0, 50.0]],
            "active": [1.0, 0.0],
            "tcost": 1.0,
        },
        {
            "uid": 3,
            "name": "Centro_Sur",
            "bus_a": 3,
            "bus_b": 2,
            "tmax_ab": 30.0,
            "tmax_ba": 30.0,
        },
    ]
    sysm["generator_array"] += [
        {
            "uid": 2,
            "name": "BAJO",
            "bus": 1,
            "capacity": 60.0,
            "pmax": [[60.0, 60.0], [60.0, 30.0]],
            "gcost": 1.0,
            "type": "hydro_series",
        },
        {
            "uid": 3,
            "name": "PAS",
            "bus": 2,
            "capacity": 20.0,
            "pmax": 20.0,
            "gcost": 0.0,
            "type": "hydro_ror",
        },
    ]
    sysm["junction_array"].append({"uid": 2, "name": "BAJO", "drain": True})
    sysm["waterway_array"] = [
        {
            "uid": 1,
            "name": "LAGO_ver_1",
            "junction_a": "LAGO",
            "junction_b": "BAJO",
            "fmax": 500.0,
            "fcost": 0.5,
        },
        {
            "uid": 2,
            "name": "LAGO_ext",
            "junction_a": "LAGO",
            "junction_b": "BAJO",
            "fmax": 5.0,
            "fcost": 1.0,
        },
        {
            "uid": 3,
            "name": "LAGO_filt",
            "junction_a": "LAGO",
            "junction_b": "BAJO",
        },
    ]
    sysm["reservoir_seepage_array"] = [
        {
            "uid": 1,
            "name": "LAGO_filt",
            "waterway": "LAGO_filt",
            "reservoir": "LAGO",
            "slope": 0.001,
            "constant": 1.0,
        }
    ]
    sysm["reservoir_production_factor_array"] = [
        {
            "uid": 1,
            "name": "LAGO_pf",
            "turbine": "LAGO",
            "reservoir": "LAGO",
            "mean_production_factor": 2.0,
            "segments": [{"volume": 0.0, "slope": 0.001, "constant": 1.9}],
        }
    ]
    sysm["reservoir_array"][0]["emin"] = [100.0, 150.0]
    sysm["turbine_array"][0]["junction_b"] = "BAJO"
    sysm["turbine_array"] += [
        {
            "uid": 2,
            "name": "BAJO",
            "generator": "BAJO",
            "junction_a": "BAJO",
            "production_factor": [[1.0, 1.0], [1.2, 1.2]],
            "capacity": 50.0,
        },
        {"uid": 3, "name": "PAS", "generator": "PAS", "flow": "PAS_Q"},
    ]
    sysm["flow_array"].append(
        {
            "uid": 2,
            "name": "PAS_Q",
            "discharge": [[[5.0, 5.0], [6.0, 6.0]], [[4.0, 4.0], [3.0, 3.0]]],
        }
    )
    sysm["battery_array"][0]["emin"] = 10.0
    sysm["battery_array"].append(
        {"uid": 6, "name": "BESS2", "emax": 10.0, "pmax_charge": 5.0}
    )
    sysm["demand_array"].append({"uid": 3, "name": "lost", "bus": 99, "lmax": 1.0})
    return planning


def tiny_results() -> dict[tuple[str, str], list[tuple[int, int, int, int, float]]]:
    """Synthetic gtopt results of :func:`tiny_planning` (no solver needed).

    ``{(Class, field): [(scenario, stage, block, uid, value), ...]}`` in
    gtopt's long layout (stage streams use ``block = 0``).  Values are
    chosen so each one identifies its cell: generation of TERMO is
    ``10 * scenario + block`` MW, the bus duals ``50 + block`` (bus 1) and
    ``60 + block`` (bus 2) $/MWh, and the bus balance holds in every block
    (generation + discharge + fail = load + charge + line flow out).
    """
    stage_of = {1: 1, 2: 1, 3: 2, 4: 2}
    rows: dict[tuple[str, str], list[tuple[int, int, int, int, float]]] = {}

    def put(cls: str, fld: str, uid: int, fn: Any) -> None:
        for s in (1, 2):
            for b in (1, 2, 3, 4):
                rows.setdefault((cls, fld), []).append(
                    (s, stage_of[b], b, uid, float(fn(s, b)))
                )

    put("Generator", "generation_sol", 4, lambda s, b: 10 * s + b)  # TERMO, bus 2
    put("Generator", "generation_sol", 1, lambda s, b: 50.0)  # LAGO, bus 1
    put("Generator", "generation_sol", 8, lambda s, b: 5.0 if b < 3 else 0.0)
    put("Turbine", "flow_sol", 1, lambda s, b: 25.0)
    put("Bus", "balance_dual", 1, lambda s, b: 50.0 + b)
    put("Bus", "balance_dual", 2, lambda s, b: 60.0 + b)
    put("Demand", "load_sol", 1, lambda s, b: 30.0)
    put("Demand", "load_sol", 2, lambda s, b: 10 * s + b + 15.0)
    put("Demand", "fail_sol", 2, lambda s, b: 2.0 if b == 4 else 0.0)
    put("Line", "flow_sol", 1, lambda s, b: 20.0)  # bus 1 -> bus 2
    put("Battery", "fout_sol", 5, lambda s, b: 3.0 if b == 2 else 0.0)
    put("Battery", "finp_sol", 5, lambda s, b: 3.0 if b == 1 else 0.0)
    put("Battery", "energy_sol", 5, lambda s, b: 30.0 if b == 1 else 0.0)
    put("Battery", "energy_dual", 5, lambda s, b: 36.0)
    put("Reservoir", "water_value_dual", 1, lambda s, b: 1000.0 * s)
    put("Flow", "flow_sol", 1, lambda s, b: 20.0 if s == 1 else 15.0)
    put("Junction", "drain_sol", 1, lambda s, b: 1.0 if b == 3 else 0.0)
    # fix the bus-2 balance: TERMO + PV + discharge + fail + flow = load + charge
    rows[("Demand", "load_sol")] = [
        r
        if r[3] != 2
        else (
            r[0],
            r[1],
            r[2],
            2,
            (10 * r[0] + r[2])
            + (5.0 if r[2] < 3 else 0.0)
            + (3.0 if r[2] == 2 else 0.0)
            + 20.0
            - (3.0 if r[2] == 1 else 0.0),
        )
        for r in rows[("Demand", "load_sol")]
    ]
    vols = {(1, 1): (500.0, 450.0), (1, 2): (450.0, 400.0)}
    vols |= {(2, 1): (500.0, 480.0), (2, 2): (480.0, 300.0)}
    for (s, k), (vi, vf) in vols.items():
        rows.setdefault(("Reservoir", "eini_sol"), []).append((s, k, 0, 1, vi))
        rows.setdefault(("Reservoir", "efin_sol"), []).append((s, k, 0, 1, vf))
    return rows


def write_tiny_results(directory: Path, layout: str = "parquet") -> Path:
    """Write :func:`tiny_results` as a gtopt output directory.

    ``layout``: ``parquet`` (one ``<field>.parquet`` file), ``hive``
    (``<field>.parquet/scene=0/phase=0/part.parquet``), ``csv`` / ``csv.zst``
    (``<field>_s0_p0.csv[.zst]`` shards) or ``wide`` (legacy ``uid:<N>``
    columns in ``<field>.csv``).  Exact zeros of ``_sol`` streams are
    dropped, as gtopt does.
    """
    import pandas as pd  # pylint: disable=import-outside-toplevel

    for (cls, fld), data in tiny_results().items():
        df = pd.DataFrame(data, columns=["scenario", "stage", "block", "uid", "value"])
        if fld.endswith("_sol"):
            df = df[df["value"] != 0.0]
        d = directory / cls
        d.mkdir(parents=True, exist_ok=True)
        if layout == "parquet":
            df.to_parquet(d / f"{fld}.parquet", index=False)
        elif layout == "hive":
            part = d / f"{fld}.parquet" / "scene=0" / "phase=0"
            part.mkdir(parents=True, exist_ok=True)
            df.to_parquet(part / "part.parquet", index=False)
        elif layout in ("csv", "csv.zst"):
            df.to_csv(d / f"{fld}_s0_p0.{layout}", index=False)
        elif layout == "wide":
            wide = df.pivot_table(
                index=["scenario", "stage", "block"],
                columns="uid",
                values="value",
                fill_value=0.0,
            )
            wide.columns = [f"uid:{c}" for c in wide.columns]
            wide.reset_index().to_csv(d / f"{fld}.csv", index=False)
        else:
            raise ValueError(f"unknown layout {layout!r}")
    (directory / "solution.csv").write_text(
        "scene,phase,status,obj_value\n0,0,0,1.0\n", encoding="utf-8"
    )
    return directory
