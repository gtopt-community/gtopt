# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Checks on the committed ``cases/cheby_small`` JSON and sampler /
reconstruction round trips (``plexos2gtopt.week_sampler`` /
``week_reconstruct``) on them:

* the block and point files describe the same stages (hours), the point
  grids are Chebyshev–Lobatto (t_0 = 0, t_N = H, increasing) with CC
  weights summing to H;
* sampled series conserve energy (Σ w·v = Σ hourly) and stay in the
  hourly data range (tcheby cases, ``wcell`` sampling); the mini cases
  hold their analytic load at the nodes;
* :func:`rebuild_energy` of the node values conserves Σ w·v and recovers
  linear loads exactly; the end prices of a periodic week merge;
* the hand-derived objectives of the READMEs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from plexos2gtopt.week_reconstruct import (
    NodeGrid,
    cumulative_integration_matrix,
    rebuild_energy,
)
from tcheby2gtopt.timegrid import lobatto_grid
from tcheby2gtopt.mini_cases import (
    MINI_CASES,
    build_mini,
    battery_ramp_load,
    congested_line_cost,
    expected_block_objective,
    expected_point_objective,
    hydro_load,
    line_load,
    uc_load,
    week_load,
)
from tcheby2gtopt.tests.conftest import REPO_CASES

TCHEBY = ["caso1emb", "caso2emb", "losses", "system2_week"]
MINI = list(MINI_CASES)


def _pair(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    d = REPO_CASES / name if name in TCHEBY else REPO_CASES / "mini" / name
    base = Path(name).name
    return (
        json.loads((d / f"{base}_block.json").read_text()),
        json.loads((d / f"{base}_point.json").read_text()),
    )


def _stages(p: dict[str, Any]) -> list[tuple[np.ndarray, np.ndarray | None]]:
    sim = p["simulation"]
    out = []
    for st in sim["stage_array"]:
        bl = sim["block_array"][
            st["first_block"] : st["first_block"] + st["count_block"]
        ]
        w = np.array([b["duration"] for b in bl], dtype=float)
        t = np.array([b["time"] for b in bl], dtype=float) if "time" in bl[0] else None
        out.append((w, t))
    return out


def _per_stage(value: Any, stages: list[Any]) -> list[np.ndarray]:
    if not isinstance(value, list):
        return [np.full(len(w), float(value)) for w, _ in stages]
    if isinstance(value[0], list) and isinstance(value[0][0], list):  # scenario axis
        value = value[0]
    return [np.asarray(v, dtype=float) for v in value]


def _series(p: dict[str, Any]) -> dict[str, Any]:
    """Every per-block field of the system: ``class.name.field``."""
    keys = {
        "demand_array": ["lmax"],
        "generator_array": ["pmax", "gcost", "pmin"],
        "line_array": ["tmax_ab", "tmax_ba"],
        "flow_array": ["discharge"],
        "waterway_array": ["fmin", "fmax"],
        "reservoir_array": ["emin", "emax"],
    }
    out = {}
    for cls, fields in keys.items():
        for e in p["system"].get(cls, []):
            for f in fields:
                if f in e:
                    out[f"{cls}.{e['name']}.{f}"] = e[f]
    return out


@pytest.mark.parametrize("name", TCHEBY + MINI)
def test_point_grid_is_lobatto_cc(name):
    blk, pnt = _pair(name)
    sb, sp = _stages(blk), _stages(pnt)
    assert len(sb) == len(sp)
    for (wb, tb), (wp, tp), st in zip(sb, sp, pnt["simulation"]["stage_array"]):
        assert tb is None and np.all(wb == 1.0)
        hours = wb.sum()
        assert st["sampling"] == "point"
        assert tp is not None
        assert wp.sum() == pytest.approx(hours, rel=1e-12)
        assert np.all(np.diff(tp) > 0) and np.all(wp > 0)
        periodic = tp[-1] < hours - 1e-9  # node N ≡ node 0 dropped
        n = len(tp) if periodic else len(tp) - 1
        x = -np.cos(np.pi * np.arange(n + 1) / n)
        np.testing.assert_allclose(
            tp, ((x + 1) * hours / 2)[: len(tp)], atol=1e-12 * hours
        )
        assert tp[0] == 0.0


@pytest.mark.parametrize("name", TCHEBY)
def test_tcheby_series_energy_and_range(name):
    blk, pnt = _pair(name)
    sb, sp = _stages(blk), _stages(pnt)
    fb, fp = _series(blk), _series(pnt)
    assert fb.keys() == fp.keys()
    checked = 0
    for key, vb in fb.items():
        for yb, yp, (wp, _) in zip(_per_stage(vb, sb), _per_stage(fp[key], sp), sp):
            assert np.dot(wp, yp) == pytest.approx(yb.sum(), rel=1e-10, abs=1e-8), key
            assert yp.min() >= yb.min() - 1e-9 and yp.max() <= yb.max() + 1e-9, key
            checked += 1
    assert checked > 0


@pytest.mark.parametrize(
    "name,func",
    [
        ("battery_ramp", battery_ramp_load),
        ("hydro", hydro_load),
        ("congested_line", line_load),
        ("uc_minup_ramp", uc_load),
        ("periodic_week", week_load),
    ],
)
def test_mini_loads(name, func):
    blk, pnt = _pair(name)
    (wb, _), (wp, tp) = _stages(blk)[0], _stages(pnt)[0]
    lb = _per_stage(blk["system"]["demand_array"][0]["lmax"], _stages(blk))[0]
    lp = _per_stage(pnt["system"]["demand_array"][0]["lmax"], _stages(pnt))[0]
    np.testing.assert_allclose(lp, func(tp), rtol=1e-11, atol=1e-9)
    # block = hourly cell means (exact for linear / piecewise constant)
    if name in ("battery_ramp", "hydro", "congested_line", "uc_minup_ramp"):
        h = np.arange(len(wb))
        np.testing.assert_allclose(lb, func(h + 0.5), atol=1e-9)
    # energy: linear loads integrate exactly with CC
    if name in ("battery_ramp", "hydro", "congested_line"):
        assert np.dot(wp, lp) == pytest.approx(lb.sum(), rel=1e-12)


@pytest.mark.parametrize("name", TCHEBY + MINI)
def test_rebuild_energy_round_trip(name):
    """Hourly rebuild of the node load: energy = Σ w·v (exact)."""
    _, pnt = _pair(name)
    stages = _stages(pnt)
    for (w, t), lp in zip(
        stages, _per_stage(pnt["system"]["demand_array"][0]["lmax"], stages)
    ):
        assert t is not None
        grid = NodeGrid(times=t, durations=w, window=float(w.sum()))
        hourly = rebuild_energy(lp, grid, step=1.0, interp="poly")
        assert len(hourly) == int(round(w.sum()))
        assert hourly.sum() == pytest.approx(np.dot(w, lp), rel=1e-9)


@pytest.mark.parametrize("name", ["battery_ramp", "hydro", "congested_line"])
def test_rebuild_linear_load_exact(name):
    blk, pnt = _pair(name)
    (w, t) = _stages(pnt)[0]
    lp = _per_stage(pnt["system"]["demand_array"][0]["lmax"], _stages(pnt))[0]
    lb = _per_stage(blk["system"]["demand_array"][0]["lmax"], _stages(blk))[0]
    hourly = rebuild_energy(
        lp, NodeGrid(times=t, durations=w, window=24.0), interp="poly"
    )
    np.testing.assert_allclose(hourly, lb, atol=1e-9)


def test_rebuild_periodic_week_close_to_block():
    blk, pnt = _pair("periodic_week")
    (w, t) = _stages(pnt)[0]
    lp = _per_stage(pnt["system"]["demand_array"][0]["lmax"], _stages(pnt))[0]
    lb = _per_stage(blk["system"]["demand_array"][0]["lmax"], _stages(blk))[0]
    hourly = rebuild_energy(
        lp, NodeGrid(times=t, durations=w, window=168.0), interp="poly"
    )
    assert np.max(np.abs(hourly - lb)) < 0.05 * 50.0  # N = 48 on 7 sine periods
    assert hourly.sum() == pytest.approx(lb.sum(), rel=1e-6)


def test_periodic_week_has_n_nodes():
    """Periodic grid: N = 48 blocks, no duplicated end instant; node 0
    carries w_0 + w_N."""
    _, pnt = _pair("periodic_week")
    (w, t) = _stages(pnt)[0]
    assert len(w) == 48 and t[-1] < 168.0
    assert np.sum(w) == pytest.approx(168.0)
    full = lobatto_grid(48, 168.0).durations  # N + 1 nodes
    assert len(full) == 49
    assert w[0] == pytest.approx(full[0] + full[-1], rel=1e-12)


def test_storage_level_dense_reference_hydro():
    """Mini hydro: with the turbine following the load, the dense
    cumulative integral of the node flows reproduces the exact level of
    the linear net outflow (S = V J V⁻¹ is exact for polynomials)."""
    _, pnt = _pair("hydro")
    (w, t) = _stages(pnt)[0]
    grid = NodeGrid(times=t, durations=w, window=24.0)
    s = cumulative_integration_matrix(grid)
    q = 5.0 - 0.3 * t  # a linear net inflow [m³/s]
    np.testing.assert_allclose(s @ q, 5.0 * t - 0.15 * t * t, atol=1e-9)


def test_expected_objectives():
    assert expected_block_objective("battery_ramp") == 7260.0
    assert expected_block_objective("hydro") == 27400.0
    assert expected_block_objective("congested_line") == 46000.0
    assert expected_block_objective("uc_minup_ramp") == 31500.0
    assert expected_block_objective("periodic_week") == 168000.0
    assert expected_point_objective("hydro") == pytest.approx(27400.0, rel=1e-12)
    assert expected_point_objective("congested_line") == pytest.approx(46008.0745048467)
    assert expected_point_objective("periodic_week") == pytest.approx(
        168000.0, rel=1e-12
    )
    assert expected_point_objective("battery_ramp") is None
    assert congested_line_cost() == 46000.0


def test_point_cases_emit_cc_storage_integration() -> None:
    """Point-sampled (Chebyshev) cases integrate batteries AND reservoirs
    with cc; block cases leave the default."""
    for name in MINI_CASES:
        point = build_mini(name, "point")["options"]["model_options"]
        block = build_mini(name, "block")["options"]["model_options"]
        assert point["battery_storage_integration"] == "cc"
        assert point["reservoir_storage_integration"] == "cc"
        assert "battery_storage_integration" not in block
        assert "reservoir_storage_integration" not in block
