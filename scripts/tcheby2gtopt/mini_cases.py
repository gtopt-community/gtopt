# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Hand-checkable mini cases (``cases/cheby_small/mini``).

Every builder takes a :class:`~tcheby2gtopt.timegrid.TimeGrid` and
returns a gtopt planning dict.  Loads are analytic functions of the
stage time ``t`` [h]: block grids take their hourly cell means, point
grids their values AT the node times (see
:meth:`TimeGrid.sample_function`).  The expected objectives and duals
are derived in each case's README and re-derived by
:func:`expected_block_objective` / :func:`expected_point_objective`
where a closed form exists.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from gtopt_shared.point_sampling import (
    commitment_period_class,
    point_storage_options,
)

from .timegrid import TimeGrid


def _options(**extra: Any) -> dict[str, Any]:
    mo: dict[str, Any] = {
        "use_single_bus": True,
        "demand_fail_cost": 10000,
        "scale_objective": 1,
        # lossless storage: the hand / closed-form objectives assume no
        # battery self-discharge (gtopt's Li-ion default is 0.48 p.u./yr)
        "battery_annual_standing_loss": 0.0,
    }
    mo.update(extra)
    return {
        "annual_discount_rate": 0.0,
        "output_format": "csv",
        "output_compression": "uncompressed",
        "model_options": mo,
    }


def _load(grid: TimeGrid, func: Callable[[np.ndarray], np.ndarray]) -> Any:
    return grid.per_stage([grid.sample_function(0, func)])


def _planning(
    grid: TimeGrid, name: str, system: dict[str, Any], **opts: Any
) -> dict[str, Any]:
    if grid.mode == "point":
        opts = {**point_storage_options(), **opts}
        for uc in system.get("commitment_array", []):
            uc.setdefault(
                "commitment_period",
                commitment_period_class(uc.get("min_up_time"), uc.get("min_down_time"))[
                    1
                ],
            )
            # SU / SD allowance only where a ramp row crosses a cell edge
            uc.setdefault("ramp_allowance", "boundary")
    return {
        "options": _options(**opts),
        "simulation": grid.simulation(),
        "system": {"name": name, **system},
    }


# -- a) battery + linear load ramp --------------------------------------------


def battery_ramp_load(t: np.ndarray) -> np.ndarray:
    return 10.0 + np.asarray(t, dtype=float)


def battery_ramp(grid: TimeGrid) -> dict[str, Any]:
    """1 bus, load 10 + t (24 h), g1 10 $/MWh ≤ 22 MW, g2 100 $/MWh,
    lossless battery emax 50 MWh (eini = efin = 0)."""
    return _planning(
        grid,
        "mini_battery_ramp",
        {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "demand_array": [
                {
                    "uid": 1,
                    "name": "d1",
                    "bus": 1,
                    "lmax": _load(grid, battery_ramp_load),
                }
            ],
            "generator_array": [
                {"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 22},
                {"uid": 2, "name": "g2", "bus": 1, "gcost": 100, "capacity": 1000},
            ],
            "battery_array": [
                {
                    "uid": 1,
                    "name": "bess",
                    "bus": 1,
                    "input_efficiency": 1,
                    "output_efficiency": 1,
                    "emin": 0,
                    "emax": 50,
                    "eini": 0,
                    "efin": 0,
                    "daily_cycle": False,
                    "use_state_variable": True,
                    "pmax_charge": 100,
                    "pmax_discharge": 100,
                    "capacity": 50,
                }
            ],
        },
    )


# -- b) reservoir + turbine ----------------------------------------------------


def hydro_load(t: np.ndarray) -> np.ndarray:
    return 20.0 + np.asarray(t, dtype=float)


def hydro(grid: TimeGrid) -> dict[str, Any]:
    """1 bus, load 20 + t (24 h); reservoir eini 0.36 hm³ (= 100 m³/s·h),
    inflow 5 m³/s, turbine 1 MW/(m³/s) ≤ 100 m³/s; thermal 50 $/MWh."""
    return _planning(
        grid,
        "mini_hydro",
        {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "demand_array": [
                {"uid": 1, "name": "d1", "bus": 1, "lmax": _load(grid, hydro_load)}
            ],
            "generator_array": [
                {"uid": 1, "name": "hydro", "bus": 1, "gcost": 0, "capacity": 100},
                {"uid": 2, "name": "thermal", "bus": 1, "gcost": 50, "capacity": 1000},
            ],
            "junction_array": [
                {"uid": 1, "name": "j_up"},
                {"uid": 2, "name": "j_down", "drain": True},
            ],
            "waterway_array": [
                {
                    "uid": 1,
                    "name": "ww1",
                    "junction_a": 1,
                    "junction_b": 2,
                    "fmin": 0,
                    "fmax": 100,
                }
            ],
            "reservoir_array": [
                {
                    "uid": 1,
                    "name": "rsv",
                    "junction": 1,
                    "capacity": 10,
                    "emin": 0,
                    "emax": 10,
                    "eini": 0.36,
                    "spillway_capacity": 0,
                }
            ],
            "flow_array": [
                {
                    "uid": 1,
                    "name": "inflow",
                    "direction": 1,
                    "junction": 1,
                    "discharge": 5,
                }
            ],
            "turbine_array": [
                {
                    "uid": 1,
                    "name": "tur",
                    "waterway": 1,
                    "generator": 1,
                    "production_factor": 1,
                }
            ],
        },
    )


# -- c) 2 buses + congested line -------------------------------------------------


def line_load(t: np.ndarray) -> np.ndarray:
    return 50.0 + 5.0 * np.asarray(t, dtype=float)


def congested_line(grid: TimeGrid) -> dict[str, Any]:
    """b1: cheap 10 $/MWh; b2: dear 50 $/MWh and load 50 + 5t; line
    b1→b2 limited to 100 MW (congested for t > 10 h)."""
    return _planning(
        grid,
        "mini_congested_line",
        {
            "bus_array": [{"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"}],
            "demand_array": [
                {"uid": 1, "name": "d2", "bus": 2, "lmax": _load(grid, line_load)}
            ],
            "generator_array": [
                {"uid": 1, "name": "cheap", "bus": 1, "gcost": 10, "capacity": 200},
                {"uid": 2, "name": "dear", "bus": 2, "gcost": 50, "capacity": 200},
            ],
            "line_array": [
                {
                    "uid": 1,
                    "name": "l12",
                    "bus_a": 1,
                    "bus_b": 2,
                    "reactance": 0.1,
                    "tmax_ab": 100,
                    "tmax_ba": 100,
                }
            ],
        },
        use_single_bus=False,
        use_kirchhoff=True,
    )


# -- d) unit commitment: min up + ramps ------------------------------------------


def uc_load(t: np.ndarray) -> np.ndarray:
    # right-continuous steps; node times are rounded first so a node that
    # falls on a jump (t = 6, 18 for N = 24) takes the value AFTER it
    tt = np.round(np.asarray(t, dtype=float), 9)
    on = ((tt >= 6.0) & (tt < 18.0)) | ((tt >= 20.0) & (tt < 22.0))
    return np.where(on, 80.0, 0.0)


def uc_minup_ramp(grid: TimeGrid) -> dict[str, Any]:
    """g1 10 $/MWh ≤ 100 MW committed (pmin 40, MUT 6 h, ramps 20 MW/h,
    startup_ramp 40, shutdown_ramp 100, start 500 $), g2 100 $/MWh;
    load 80 MW on [6, 18) and [20, 22)."""
    return _planning(
        grid,
        "mini_uc_minup_ramp",
        {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "demand_array": [
                {"uid": 1, "name": "d1", "bus": 1, "lmax": _load(grid, uc_load)}
            ],
            "generator_array": [
                {"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 100},
                {"uid": 2, "name": "g2", "bus": 1, "gcost": 100, "capacity": 1000},
            ],
            "commitment_array": [
                {
                    "uid": 1,
                    "name": "g1_uc",
                    "generator": 1,
                    "pmin": 40,
                    "min_up_time": 6,
                    "min_down_time": 2,
                    "ramp_up": 20,
                    "ramp_down": 20,
                    "startup_ramp": 40,
                    "shutdown_ramp": 100,
                    "startup_cost": 500,
                    "initial_status": 0,
                    "initial_hours": 24,
                    "initial_power": 0,
                    "relax": False,
                }
            ],
        },
    )


# -- e) periodic week (N nodes: node N ≡ node 0 dropped) ---------------------------


def week_load(t: np.ndarray) -> np.ndarray:
    return 100.0 + 50.0 * np.sin(2.0 * np.pi * np.asarray(t, dtype=float) / 24.0)


def periodic_week(grid: TimeGrid) -> dict[str, Any]:
    """168 h, load 100 + 50 sin(2πt/24) on a periodic grid (N blocks,
    node 0 carries w_0 + w_N), g1
    10 $/MWh ≤ 110 MW, g2 100 $/MWh, lossless battery closing the
    cycle (eini = efin = 500 MWh, emax 1000)."""
    return _planning(
        grid,
        "mini_periodic_week",
        {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "demand_array": [
                {"uid": 1, "name": "d1", "bus": 1, "lmax": _load(grid, week_load)}
            ],
            "generator_array": [
                {"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 110},
                {"uid": 2, "name": "g2", "bus": 1, "gcost": 100, "capacity": 1000},
            ],
            "battery_array": [
                {
                    "uid": 1,
                    "name": "bess",
                    "bus": 1,
                    "input_efficiency": 1,
                    "output_efficiency": 1,
                    "emin": 0,
                    "emax": 1000,
                    "eini": 500,
                    "efin": 500,
                    "daily_cycle": False,
                    "use_state_variable": True,
                    "pmax_charge": 100,
                    "pmax_discharge": 100,
                    "capacity": 1000,
                }
            ],
        },
    )


#: name -> (builder, stage hours, point N)
MINI_CASES: dict[str, tuple[Callable[[TimeGrid], dict[str, Any]], float, int]] = {
    "battery_ramp": (battery_ramp, 24.0, 24),
    "hydro": (hydro, 24.0, 24),
    "congested_line": (congested_line, 24.0, 24),
    "uc_minup_ramp": (uc_minup_ramp, 24.0, 24),
    "periodic_week": (periodic_week, 168.0, 48),
}


#: mini cases on a periodic point grid (N nodes)
PERIODIC_MINI = frozenset({"periodic_week"})


def build_mini(name: str, mode: str) -> dict[str, Any]:
    builder, hours, n = MINI_CASES[name]
    grid = TimeGrid(
        mode,  # type: ignore[arg-type]
        [hours],
        nodes=n,
        periodic=name in PERIODIC_MINI,
    )
    return builder(grid)


# -- closed-form references ---------------------------------------------------------


def congested_line_cost(times_w: tuple[np.ndarray, np.ndarray] | None = None) -> float:
    """Exact LP optimum of ``congested_line`` (no inter-temporal coupling):
    ``Σ w [10·min(d, 100) + 50·max(d − 100, 0)]``.  ``None``: hourly
    blocks (d = the hourly cell mean 52.5 + 5h)."""
    if times_w is None:
        d = 52.5 + 5.0 * np.arange(24)
        w = np.ones(24)
    else:
        d = line_load(times_w[0])
        w = times_w[1]
    return float(
        np.sum(w * (10.0 * np.minimum(d, 100.0) + 50.0 * np.maximum(d - 100.0, 0.0)))
    )


def merit_cost(load: np.ndarray, w: np.ndarray, price: float) -> float:
    """``price · Σ w·load`` (cases whose price is uniform)."""
    return float(price * np.sum(np.asarray(w) * np.asarray(load)))


def expected_block_objective(name: str) -> float:
    """Hand-derived optimum on hourly blocks (see the READMEs)."""
    return {
        "battery_ramp": 10.0 * (528.0 - 22.0) + 100.0 * 22.0,
        "hydro": 50.0 * (768.0 - 220.0),
        "congested_line": congested_line_cost(),
        "uc_minup_ramp": 10.0 * 900.0 + 500.0 + 100.0 * (60.0 + 160.0),
        "periodic_week": 10.0 * 16800.0,
    }[name]


def expected_point_objective(name: str) -> float | None:
    """Closed-form optimum on the point grid where one exists."""
    builder, hours, n = MINI_CASES[name]
    del builder
    grid = TimeGrid("point", [hours], nodes=n, periodic=name in PERIODIC_MINI)
    st = grid.stages[0]
    assert st.times is not None
    if name == "congested_line":
        return congested_line_cost((st.times, st.durations))
    if name == "hydro":
        return 50.0 * (float(np.sum(st.durations * hydro_load(st.times))) - 220.0)
    if name == "periodic_week":
        return merit_cost(week_load(st.times), st.durations, 10.0)
    return None


__all__ = [
    "MINI_CASES",
    "build_mini",
    "expected_block_objective",
    "expected_point_objective",
]
