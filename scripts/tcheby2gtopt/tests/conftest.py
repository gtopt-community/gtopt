# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Fixtures: tiny SQLite databases with the tcheby test-DB schemas.

The real databases live in the Chebyshev scheduling research code (Matus et al.)
(``tests/test_dbs``), outside gtopt; these fixtures copy their schema and
the rows of ``system1_caso1emb_fact`` / ``simu1_caso1emb_fact`` (date
keyed schedules) and ``system1_reszone`` (hour keyed schedules,
passthrough, overspill, commitment, reserve zone).  Set
``TCHEBY_TEST_DBS`` to the real directory to also run the tests that
read the originals.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

Tables = dict[str, tuple[list[str], list[tuple[Any, ...]]]]

REPO_CASES = Path(__file__).resolve().parents[3] / "cases" / "cheby_small"


def make_db(path: Path, tables: Tables) -> Path:
    con = sqlite3.connect(path)
    for name, (cols, rows) in tables.items():
        con.execute(f'CREATE TABLE "{name}" ({", ".join(cols)})')
        if rows:
            marks = ", ".join("?" * len(cols))
            con.executemany(f'INSERT INTO "{name}" VALUES ({marks})', rows)
    con.commit()
    con.close()
    return path


CASO1EMB_SYSTEM: Tables = {
    "Bus": (
        ["id", "name", "active", "longitude", "latitude"],
        [
            (1, "Antuco220               ", 1, None, None),
            (2, "Valdivia220", 1, 47.1, 35.0),
        ],
    ),
    "Line": (
        [
            "id",
            "name",
            "capacity",
            "bus_a_id",
            "bus_b_id",
            "active",
            "r",
            "x",
            "tmin",
            "tmax",
            "voltage",
        ],
        [
            (
                1,
                "Antuco220->Valdivia220",
                600.0,
                1,
                2,
                1,
                10.0,
                40.0,
                -600.0,
                600.0,
                220.0,
            )
        ],
    ),
    "Demand": (
        ["id", "name", "load", "bus_id", "active"],
        [(1, "Dem1", 100.0, 1, 1)],
    ),
    "Demand.load_sched": (
        ["key", "id", "date", "value"],
        [
            (1, 1, "2016-01-01 00:00:00", 100.0),
            (2, 1, "2016-01-04 04:00:00", 50.0),
            (3, 1, "2016-01-08 08:00:00", 80.0),
        ],
    ),
    "Junction": (
        ["id", "name", "longitude", "latitude", "active"],
        [(0, "SEA", "", "", 1), (1, "J1", None, None, 1), (2, "J2", 21.0, 80.0, 1)],
    ),
    "Waterway": (
        ["id", "name", "junc_a_id", "junc_b_id", "fmin", "fmax", "delay", "active"],
        [
            (1, "CH_WW", 1, 2, 0.0, 9999.0, 0.0, 1),
            (3, "OCEAN", 2, 0, 0.0, 9999.0, "", 1),
        ],
    ),
    "Inflow": (
        ["id", "name", "junc_id", "flow", "active"],
        [(1, "CH_Inf", 1, 1.0, 1)],
    ),
    "Reservoir": (
        ["id", "name", "junc_id", "vini", "vmin", "vmax", "vscale", "active"],
        [(1, "CH_Emb", 1, 3.6, 0.0, 100.0, 1000.0, 1)],
    ),
    "PowerSystem": (["id", "name", "fail_cost", "psys_id"], [(1, "SIC", 500.0, 1)]),
    "Generator": (
        [
            "id",
            "name",
            "capacity",
            "bus_id",
            "pmin",
            "pmax",
            "active",
            "longitude",
            "latitude",
            "cvar",
            "tmin",
        ],
        [
            (1, "CT", 60.0, 1, 0.0, 60.0, 1, None, None, 10.0, None),
            (2, "CF", 100.0, 2, 0.0, 100.0, 1, None, None, 100.0, None),
            (3, "CH", 450.0, 1, 20.0, 450.0, 1, "", "", 0.0, None),
        ],
    ),
    "Generator.pmax_sched": (["key", "id", "date", "value"], []),
    "HidroTurbine": (
        ["id", "name", "gen_id", "wway_id", "efficiency", "active"],
        [(6, "CH_Turb", 3, 1, 1.0, 1)],
    ),
}

CASO1EMB_SIMU: Tables = {
    "Scene": (["id", "name"], [(1, "s1"), (2, "s2"), (3, "s3")]),
    "Scenario": (["id", "name", "active"], [(1, "s1", 1)]),
    "ScheduleH": (
        ["id", "name", "default_value"],
        [(1, "A1_S1", 30.0), (2, "A1_S2", 10.0), (3, "A1_S3", 15.0)],
    ),
    "ScheduleH.values": (["key", "id", "hour", "value"], []),
    "Field": (
        ["id", "field_name", "class_name", "element_name"],
        [(1, "flow_sched", "Inflow", "CH_Inf")],
    ),
    "Scene.field_scheds": (
        ["key", "id", "field_id", "schedule_id"],
        [(1, 1, 1, 1), (2, 2, 1, 2), (3, 3, 1, 3)],
    ),
    "Simulation": (
        ["id", "nscenarios", "nstages", "ncoeffs", "discount_rate", "time_zero"],
        [(1, 1, 3, 2, 0.1, "2016-01-01 00:00:00")],
    ),
    "Stage": (["id", "duration"], [(1, 100.0), (2, 100.0), (3, 100.0)]),
    "Scenario.scenes": (
        ["key", "id", "stage_id", "scene_id"],
        [(1, 1, 1, 1), (2, 1, 2, 2), (3, 1, 3, 3)],
    ),
    "Study": (
        [
            "simulation_id",
            "name",
            "time_zero",
            "discount_rate",
            "internal_rate",
            "ncoeffs",
            "msamples",
        ],
        [(1, "s1", "2016-01-01 00:00:00", 0.1, 0.0, 2, 2)],
    ),
}

RESZONE_SYSTEM: Tables = {
    "Bus": (
        ["id", "name", "active", "longitude", "latitude"],
        [(1, "bar1", 1, None, None), (2, "bar2", 1, 47.1, 35.0)],
    ),
    "Line": (
        [
            "id",
            "name",
            "capacity",
            "bus_a_id",
            "bus_b_id",
            "active",
            "r",
            "x",
            "tmin",
            "tmax",
            "voltage",
        ],
        [(1, "Line1", 500.0, 1, 2, 1, 30.0, 10.0, None, None, 220.0)],
    ),
    "Line.tmin_sched": (
        ["id", "date", "value"],
        [(1, "2015-5-16", 1000.0), (1, "2016-1-1", 900.0)],
    ),
    "Line.tmax_sched": (
        ["id", "date", "value"],
        [(1, "2015-5-16", 100.0), (1, "2016-1-1", 67.0)],
    ),
    "Demand": (["id", "name", "load", "bus_id", "active"], [(1, "Dem1", 100.0, 2, 1)]),
    "Demand.active_sched": (["id", "date", "value"], [(1, "2015-5-16", 1)]),
    "Junction": (
        ["id", "name", "longitude", "latitude", "active"],
        [
            (1, "laja_junct", None, None, 1),
            (2, "maule_junct", 21.0, 80.0, 1),
            (3, "colbun_junct", 32.3, 74.0, 1),
        ],
    ),
    "Waterway": (
        ["id", "name", "junc_a_id", "junc_b_id", "fmin", "fmax", "delay", "active"],
        [
            (1, "canal_machicura", 1, 2, 0.0, 200.0, 0.0, 1),
            (2, "descarga_abanico", 1, 3, 0.0, 1000.0, 20.0, 1),
        ],
    ),
    "Waterway.fmin_sched": (
        ["id", "hour", "value"],
        [(1, 0.0, 1.0), (1, 1.0, 2.0), (1, 4.0, 3.3), (2, 6.0, 10.0)],
    ),
    "HidroTurbine": (
        ["id", "name", "gen_id", "wway_id", "efficiency", "active"],
        [(1, "t1", 1, 1, 0.5, 1)],
    ),
    "Inflow": (
        ["id", "name", "junc_id", "flow", "active"],
        [(1, "af_polcura", 1, 100.0, 1), (2, "af_abanico", 2, 80.0, 1)],
    ),
    "Reservoir": (
        ["id", "name", "junc_id", "vini", "vmin", "vmax", "vscale", "active"],
        [
            (1, "Laja", 1, 2.2, 0.0, 100.0, 10.0, 1),
            (2, "Maule", 1, 10.0, 10.0, 30.0, 1.0, 1),
        ],
    ),
    "PowerSystem": (["id", "name", "fail_cost", "psys_id"], [(1, "SIC", 500.0, None)]),
    "Commitment": (
        [
            "id",
            "name",
            "gen_id",
            "time_resolution",
            "down_time",
            "up_time",
            "last_state",
            "last_state_period",
            "start_cost",
            "stop_cost",
            "start_ramp",
            "stop_ramp",
            "state",
        ],
        [(1, "c1", 1, 2.5, 4.0, 4.5, None, None, None, None, None, None, None)],
    ),
    "Passthrough": (
        [
            "id",
            "name",
            "type",
            "active",
            "capacity",
            "bus_id",
            "efficiency",
            "flow",
            "longitude",
            "latitude",
            "cvar",
        ],
        [(1, "pt1", None, 1, 100.0, 1, 0.5, 150.0, None, None, 0.0)],
    ),
    "Overspill": (
        [
            "id",
            "name",
            "reserv_id",
            "wway_id",
            "spillvol",
            "spillcost",
            "delay",
            "active",
            "rmin",
            "rmax",
        ],
        [(1, "Laja_reb", 1, 1, 100.0, 10.0, 0.0, 1, -10.0, 10.0)],
    ),
    "ReserveZone": (
        [
            "id",
            "name",
            "active",
            "purmin",
            "pdrmin",
            "surmin",
            "sdrmin",
            "turmin",
            "tdrmin",
        ],
        [(1, "Norte", 1, 100.0, 80.0, 50.0, 40.0, 30.0, 20.0)],
    ),
    "Generator": (
        [
            "id",
            "name",
            "capacity",
            "bus_id",
            "pmin",
            "pmax",
            "active",
            "longitude",
            "latitude",
            "cvar",
        ],
        [
            (1, "Gen1", 300.0, 1, None, None, None, None, None, 0.0),
            (2, "Gen2", 100.1, 2, 10.0, 90.0, 1, 33.5, 40.0, 80.0),
            (3, "Gen3", 250.0, 1, None, None, None, None, None, 0.0),
        ],
    ),
}


@pytest.fixture
def caso1emb_dbs(tmp_path: Path) -> tuple[Path, Path]:
    return (
        make_db(tmp_path / "system1_caso1emb_fact.db", CASO1EMB_SYSTEM),
        make_db(tmp_path / "simu1_caso1emb_fact.db", CASO1EMB_SIMU),
    )


@pytest.fixture
def reszone_db(tmp_path: Path) -> Path:
    return make_db(tmp_path / "system1_reszone.db", RESZONE_SYSTEM)


@pytest.fixture
def real_dbs() -> Path:
    """The tcheby ``tests/test_dbs`` directory (skip when absent)."""
    p = Path(os.environ.get("TCHEBY_TEST_DBS", ""))
    if not os.environ.get("TCHEBY_TEST_DBS") or not p.is_dir():
        pytest.skip("TCHEBY_TEST_DBS not set (tcheby test_dbs directory)")
    return p
