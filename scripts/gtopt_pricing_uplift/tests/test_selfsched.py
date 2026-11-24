# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Price-taker self-schedule DP."""

from __future__ import annotations

import pytest

from gtopt_pricing_uplift import SelfScheduleUnit, self_schedule_profit


def _unit(prices: list[float], **kw) -> SelfScheduleUnit:
    n = len(prices)
    base = {
        "price": prices,
        "cost": [10.0] * n,
        "duration": [1.0] * n,
        "pmin": [50.0] * n,
        "pmax": [100.0] * n,
    }
    base.update(kw)
    return SelfScheduleUnit(**base)


def test_profitable_unit_runs_at_pmax() -> None:
    # (60 - 10) * 100 per hour, no commitment costs.
    assert self_schedule_profit(_unit([60.0, 60.0])) == pytest.approx(10_000.0)


def test_unprofitable_unit_stays_off() -> None:
    assert self_schedule_profit(_unit([5.0, 5.0])) == pytest.approx(0.0)


def test_startup_cost_trades_off_against_margin() -> None:
    # One hour at margin 50·100 = 5000 vs start-up 6000 → stay off.
    u = _unit([60.0], startup_cost=[6000.0])
    assert self_schedule_profit(u) == pytest.approx(0.0)
    u = _unit([60.0, 60.0], startup_cost=[6000.0, 6000.0])
    assert self_schedule_profit(u) == pytest.approx(4000.0)


def test_noload_and_pmin_when_price_below_cost() -> None:
    # Initially on, min-up not yet met: must run 2 h at pmin with a loss.
    u = _unit(
        [5.0, 5.0, 5.0],
        noload_cost=100.0,
        min_up=3.0,
        initial_on=True,
        initial_hours=1.0,
    )
    # Hours 1-2 forced on: each (5-10)*50 - 100 = -350; hour 3 off.
    assert self_schedule_profit(u) == pytest.approx(-700.0)


def test_min_down_blocks_a_restart() -> None:
    # Off for 1 h, min-down 3 h: cannot start before hour 3.
    u = _unit(
        [60.0, 60.0, 60.0],
        min_down=3.0,
        initial_on=False,
        initial_hours=1.0,
    )
    assert self_schedule_profit(u) == pytest.approx(5000.0)


def test_shutdown_cost_and_initial_hours_default() -> None:
    # Unset initial_hours = long enough: free to shut down, paying SD.
    u = _unit([5.0], initial_on=True, shutdown_cost=[30.0])
    # Stay on: (5-10)*50 = -250; shut down: -30.
    assert self_schedule_profit(u) == pytest.approx(-30.0)


def test_must_run() -> None:
    u = _unit([5.0, 60.0], must_run=True, initial_on=True)
    assert self_schedule_profit(u) == pytest.approx(-250.0 + 5000.0)


def test_must_run_infeasible_returns_zero() -> None:
    u = _unit(
        [60.0],
        must_run=True,
        initial_on=False,
        initial_hours=0.0,
        min_down=5.0,
    )
    assert self_schedule_profit(u) == pytest.approx(0.0)


def test_missing_cost_vectors_default_to_zero() -> None:
    u = _unit([60.0, 5.0], startup_cost=[], shutdown_cost=[])
    assert self_schedule_profit(u) == pytest.approx(5000.0)
