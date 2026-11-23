# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge cases of the ladder, zones, R3 reconstruction, LP-dual reader and
dataset writer."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gtopt_canonical_feed import Bus, Generator, Line, Topology
from gtopt_marginal_units._io import write_dataset
from gtopt_marginal_units._ladder import build_ladder
from gtopt_marginal_units._lp_duals import _read_long
from gtopt_marginal_units._reconstruct import (
    ZoneR3Result,
    reconstruct_all_zones,
    reconstruct_zone_lambda,
)
from gtopt_marginal_units._zones import build_ptdf, partition_zones
from gtopt_marginal_units.constants import Confidence
from gtopt_marginal_units.errors import InputValidationError

_CELL = (1, 1, 1, None, None, "simulated")


def _gen(uid: int, mc: float | None = 10.0, kind: str = "thermal", bus: int = 1):
    return Generator(
        uid=uid,
        name=f"g{uid}",
        bus_uid=bus,
        pmin=0.0,
        pmax=100.0,
        declared_MC=mc,
        kind=kind,
        emission_rate=0.0,
    )


def _zres(kind: str, uids: list[int]) -> ZoneR3Result:
    return ZoneR3Result(
        zone_id=0,
        lambda_z=30.0,
        formula_kind=kind,
        marginal_gen_uids=uids,
        confidence=Confidence.MERIT_ORDER,
        degenerate=False,
        reason="t",
        clamped=False,
    )


# ---------------------------------------------------------------------------
# _ladder
# ---------------------------------------------------------------------------


def test_ladder_without_anchor_or_ineligible_anchor():
    gens = [_gen(1), _gen(2, mc=20.0)]
    disp = {1: 50.0, 2: 50.0}
    assert not build_ladder(
        cell_key=_CELL,
        zone_id=0,
        generators_in_zone=gens,
        dispatch_by_uid=disp,
        zone_result=_zres("single_unit", []),
    )
    # anchor excluded from the eligible ladder but present in the zone
    rungs = build_ladder(
        cell_key=_CELL,
        zone_id=0,
        generators_in_zone=gens,
        dispatch_by_uid=disp,
        zone_result=_zres("single_unit", [2]),
        merit_eligible_by_uid={1: True, 2: False},
    )
    assert [(r.rank, r.gen_uid, r.is_actual_marginal) for r in rungs] == [(0, 2, True)]
    # anchor uid unknown to the zone -> no rungs
    assert not build_ladder(
        cell_key=_CELL,
        zone_id=0,
        generators_in_zone=gens,
        dispatch_by_uid=disp,
        zone_result=_zres("single_unit", [99]),
    )
    # depth 0 -> only the anchor
    rungs = build_ladder(
        cell_key=_CELL,
        zone_id=0,
        generators_in_zone=gens,
        dispatch_by_uid=disp,
        zone_result=_zres("single_unit", [1]),
        depth=0,
    )
    assert [r.rank for r in rungs] == [0]


# ---------------------------------------------------------------------------
# _zones
# ---------------------------------------------------------------------------


def test_partition_skips_lines_to_unknown_buses():
    topo = Topology(
        buses=[Bus(1, "a"), Bus(2, "b")],
        lines=[Line(uid=1, bus_a_uid=1, bus_b_uid=9, tmax_ab=1, tmax_ba=1)],
    )
    assert partition_zones(topo) == {1: 0, 2: 1}


def test_ptdf_unknown_bus_line_and_uidless_component():
    topo = Topology(
        buses=[Bus(1, "a"), Bus(2, "b"), Bus(3, "c"), Bus(4, "d")],
        lines=[
            Line(uid=1, bus_a_uid=1, bus_b_uid=2, tmax_ab=1, tmax_ba=1, reactance=0.1),
            Line(uid=2, bus_a_uid=2, bus_b_uid=9, tmax_ab=1, tmax_ba=1, reactance=0.1),
            # uid-less line: joins 3-4 for the partition but is not a PTDF row
            Line(
                uid=None,  # type: ignore[arg-type]
                bus_a_uid=3,
                bus_b_uid=4,
                tmax_ab=1,
                tmax_ba=1,
                reactance=0.1,
            ),
        ],
    )
    ptdf, line_uids, _ = build_ptdf(topo)
    assert line_uids == [1, 2]
    assert not ptdf[1].any()
    assert ptdf[0, 1] == pytest.approx(-1.0)


def test_ptdf_singular_component_raises():
    topo = Topology(
        buses=[Bus(1, "a"), Bus(2, "b"), Bus(3, "c")],
        lines=[
            Line(uid=1, bus_a_uid=1, bus_b_uid=2, tmax_ab=1, tmax_ba=1, reactance=1.0),
            Line(uid=2, bus_a_uid=2, bus_b_uid=3, tmax_ab=1, tmax_ba=1, reactance=1.0),
            Line(uid=3, bus_a_uid=1, bus_b_uid=3, tmax_ab=1, tmax_ba=1, reactance=-2.0),
        ],
    )
    with pytest.raises(InputValidationError, match="singular"):
        build_ptdf(topo, require_reactance=False)


# ---------------------------------------------------------------------------
# _reconstruct
# ---------------------------------------------------------------------------


def test_interior_without_mc_falls_through_to_later_steps():
    res = reconstruct_zone_lambda(
        zone_id=0,
        zone_buses=[1],
        generators_in_zone=[_gen(1, mc=None, kind="thermal")],
        dispatch_by_uid={1: 50.0},
        zone_load=50.0,
        demand_fail_cost=1000.0,
    )
    assert res.formula_kind == "unattributed"


def test_reconstruct_all_zones_skips_generators_outside_zones():
    topo = Topology(
        buses=[Bus(1, "a")],
        generators=[_gen(1), _gen(2, bus=7)],
    )
    out = reconstruct_all_zones(
        topology=topo,
        zone_of={1: 0},
        dispatch_by_uid={1: 50.0, 2: 10.0},
        load_by_bus={1: 50.0},
        demand_fail_cost=1000.0,
    )
    assert out[0].marginal_gen_uids == [1]


# ---------------------------------------------------------------------------
# _lp_duals._read_long
# ---------------------------------------------------------------------------


def test_read_long_variants(tmp_path: Path):
    (tmp_path / "Gen").mkdir()
    pd.DataFrame({"stage": [1], "other": [2.0]}).to_csv(
        tmp_path / "Gen/a.csv", index=False
    )
    assert _read_long(tmp_path, "Gen/a", "gen_uid", "rc") is None
    pd.DataFrame(
        {
            "stage": [1],
            "uid": [3],
            "value": [1.5],
            "date_utc": ["2026-01-01"],
            "hour": [4],
            "data_source": ["real"],
        }
    ).to_csv(tmp_path / "Gen/b.csv", index=False)
    out = _read_long(tmp_path, "Gen/b", "gen_uid", "rc")
    assert out is not None
    # only the canonical columns survive; padding is always re-applied
    assert out["data_source"].tolist() == ["simulated"]
    assert out["hour"].isna().all()


# ---------------------------------------------------------------------------
# _io.write_dataset
# ---------------------------------------------------------------------------


def test_write_dataset_audit_tables(tmp_path: Path):
    frame = pd.DataFrame({"x": [1, 2]})
    summary = write_dataset(
        tmp_path / "ds",
        per_bus=frame,
        per_zone=frame,
        merit_ladder=[],
        price_recipe=[],
        emission_recipe=[],
        saturated_lines=pd.DataFrame({"line_uid": [1]}),
        unattributed=pd.DataFrame({"reason": ["r"]}),
        out_of_merit=pd.DataFrame({"gen_uid": [1, 2, 3]}),
    )
    assert summary.rows_audit_saturated == 1
    assert summary.has_unattributed_cells
    assert summary.rows_audit_out_of_merit == 3
    assert (tmp_path / "ds/audit/out_of_merit.parquet").exists()
    manifest = json.loads((tmp_path / "ds/manifest.json").read_text())
    assert manifest["producer"] == "gtopt_marginal_units"
    assert np.isfinite(summary.rows_per_bus)
