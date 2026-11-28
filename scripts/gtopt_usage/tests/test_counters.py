# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Hand-computed tests of the maintenance counter engine.

Unless stated: ``hours_per_year = 100``, four periods of 10 h with 6 EOH
each (0.6 EOH/h), types ``CI`` (10 EOH, $100) < ``major`` (30 EOH,
$1000), discount rate 0.
"""

from __future__ import annotations

import pytest

from gtopt_usage.counters import (
    CalendarEvent,
    CounterSim,
    MaintType,
    Period,
    advancement_cost,
)

HPY = 100.0
CI = MaintType("CI", interval_eoh=10.0, cost=100.0, outage_days=1.0)
MAJOR = MaintType("major", interval_eoh=30.0, cost=1000.0)


def _periods(eoh: float = 6.0, n: int = 4, **kw: float) -> list[Period]:
    return [Period(10.0 * i, 10.0, eoh=eoh, **kw) for i in range(n)]


def _sim(calendar=(), rate: float = 0.0, **kw) -> CounterSim:
    return CounterSim(
        "G",
        kw.pop("types", (CI, MAJOR)),
        calendar=calendar,
        discount_rate=rate,
        hours_per_year=HPY,
        **kw,
    )


def test_extra_events_without_calendar() -> None:
    res = _sim().run(_periods())
    extra = [e for e in res.events if e.source == "extra"]
    # CI reaches 10 EOH at 16.67 h and again at 33.33 h; major never.
    assert [e.type for e in extra] == ["CI", "CI"]
    assert extra[0].time == pytest.approx(10.0 / 0.6)
    assert extra[1].time == pytest.approx(20.0 / 0.6)
    assert res.terminal_ratio["CI"] == pytest.approx(0.4)
    assert res.terminal_ratio["major"] == pytest.approx(0.8)
    assert res.extra_pv == pytest.approx(200.0)
    assert res.terminal_pv == pytest.approx(0.4 * 100 + 0.8 * 1000)
    assert res.total_pv == pytest.approx(1040.0)


def test_hierarchical_reset_and_late_classification() -> None:
    cal = [CalendarEvent("G", "major", 20.0)]
    res = _sim(cal).run(_periods())
    ev = res.events
    # CI due at 16.67 h -> major at 20 h is advanced (free at r = 0).
    assert ev[0].source == "calendar" and ev[0].status == "late"
    assert ev[0].advance_years == pytest.approx((20.0 - 10.0 / 0.6) / HPY)
    assert ev[0].ratio == pytest.approx(12.0 / 30.0)
    # The major event reset CI too: CI is due again 16.67 h later.
    assert ev[1].source == "extra" and ev[1].type == "CI"
    assert ev[1].time == pytest.approx(20.0 + 10.0 / 0.6)
    assert res.terminal_ratio["major"] == pytest.approx(12.0 / 30.0)
    assert res.terminal_ratio["CI"] == pytest.approx(0.2)


def test_lower_event_does_not_reset_higher() -> None:
    cal = [CalendarEvent("G", "CI", 15.0)]
    res = _sim(cal).run(_periods())
    assert res.events[0].status == "early"
    assert res.events[0].life_wasted == pytest.approx(1.0 - 9.0 / 10.0)
    assert res.terminal_ratio["major"] == pytest.approx(24.0 / 30.0)


def test_consistent_event_at_the_interval() -> None:
    t = 10.0 / 0.6
    res = _sim([CalendarEvent("G", "CI", t)]).run(_periods())
    assert res.events[0].status == "consistent"
    assert res.events[0].ratio == pytest.approx(1.0)


def test_advancement_pv_formula() -> None:
    rate = 0.1
    cal = [CalendarEvent("G", "major", 20.0)]
    res = _sim(cal, rate).run(_periods())
    tc = 10.0 / 0.6
    dt = (20.0 - tc) / HPY
    expected = 1000.0 * (1.0 - 1.1 ** (-dt)) * 1.1 ** (-tc / HPY)
    assert res.events[0].cost_pv == pytest.approx(expected)
    assert res.advance_pv == pytest.approx(expected)
    assert advancement_cost(1000.0, 1.0, 0.1) == pytest.approx(1000 * (1 - 1 / 1.1))
    assert advancement_cost(1000.0, 0.0, 0.1) == 0.0


def test_extra_cheaper_than_advancement() -> None:
    # Advancing a $1000 major by ~83 % of a 1-year interval at r = 100 %
    # costs more than an extra $100 CI: the engine inserts the CI.
    cal = [CalendarEvent("G", "major", 100.0)]
    res = _sim(cal, 1.0).run(_periods(n=10))
    first = res.events[0]
    assert first.source == "extra" and first.type == "CI"
    assert first.cost_pv == pytest.approx(100.0 * 2.0 ** (-(10.0 / 0.6) / HPY))


def test_late_beyond_horizon_is_priced() -> None:
    cal = [CalendarEvent("G", "major", 1000.0)]
    res = _sim(cal, 0.001).run(_periods())
    late = [e for e in res.events if e.status == "late"]
    assert len(late) == 1 and late[0].time == 1000.0
    assert late[0].advance_years == pytest.approx((1000.0 - 10.0 / 0.6) / HPY)


def test_whichever_comes_first_starts_and_years() -> None:
    typ = MaintType("CI", interval_eoh=1000.0, interval_starts=3.0, cost=10.0)
    res = _sim(types=(typ,)).run(_periods(starts=2.0))
    assert res.events[0].time == pytest.approx(15.0)  # 3 starts at 0.2/h
    yr = MaintType("CI", max_years=0.25, cost=10.0)
    res = _sim(types=(yr,)).run(_periods(eoh=0.0))
    assert [e.time for e in res.events] == pytest.approx([25.0])


def test_cycles_counter_for_bess() -> None:
    typ = MaintType("cell", interval_cycles=5.0, cost=50.0)
    res = _sim(types=(typ,)).run(_periods(eoh=0.0, cycles=2.0))
    assert [e.time for e in res.events] == pytest.approx([25.0])
    assert res.terminal_ratio["cell"] == pytest.approx(3.0 / 5.0)


def test_imperfect_maintenance_and_initial_counters() -> None:
    res = _sim(
        types=(CI,), reset_fraction=0.5, initial={"CI": {"eoh": 8.0, "years": 0.0}}
    ).run(_periods())
    times = [e.time for e in res.events]
    # 8 -> 10 after 3.33 h; reset to 5 -> 10 after 8.33 h more; then 5 ...
    assert times[0] == pytest.approx(2.0 / 0.6)
    assert times[1] == pytest.approx(2.0 / 0.6 + 5.0 / 0.6)
    assert times[2] == pytest.approx(2.0 / 0.6 + 10.0 / 0.6)


def test_initial_over_interval_is_due_at_once() -> None:
    res = _sim(types=(CI,), initial={"CI": {"eoh": 12.0}}).run(_periods())
    assert res.events[0].time == 0.0
    assert res.events[0].ratio == pytest.approx(1.2)


def test_replacement_energy_of_extra_outage() -> None:
    per = [Period(0.0, 10.0, eoh=20.0, energy_mwh=500.0, price=40.0)]
    res = _sim(types=(CI,)).run(per)
    # outage 1 day = 24 h x mean 50 MW x 40 $/MWh
    assert res.events[0].replacement_pv == pytest.approx(24 * 50 * 40)
    assert res.replacement_pv == pytest.approx(24 * 50 * 40)


def test_calendar_before_horizon_is_skipped() -> None:
    res = _sim([CalendarEvent("G", "CI", -5.0)]).run(_periods(eoh=0.0))
    assert not res.events


@pytest.mark.parametrize(
    ("kwargs", "msg"),
    [
        ({"types": ()}, "no maintenance types"),
        ({"reset_fraction": 1.0}, "reset_fraction"),
        ({"rate": -1.0}, "discount_rate"),
        ({"types": (CI, CI)}, "duplicate"),
        ({"calendar": [CalendarEvent("G", "HGP", 1.0)]}, "not defined"),
        ({"initial": {"X": {}}}, "unknown type"),
        ({"initial": {"CI": {"bad": 1.0}}}, "measure"),
        ({"initial": {"CI": {"eoh": -1.0}}}, ">= 0"),
    ],
)
def test_engine_guard_rails(kwargs: dict, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        _sim(**kwargs)


@pytest.mark.parametrize(
    ("data", "msg"),
    [
        ({"name": "x"}, "no interval"),
        ({"name": "x", "interval_eoh": 0}, "> 0"),
        ({"name": "x", "interval_eoh": 1, "cost": -1}, ">= 0"),
        ({"interval_eoh": 1}, "without 'name'"),
        ({"name": "x", "interval_eoh": 1, "foo": 1}, "unknown"),
    ],
)
def test_type_validation(data: dict, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        MaintType.from_dict(data)


def test_type_from_dict_ok() -> None:
    t = MaintType.from_dict({"name": 1, "interval_starts": 300, "max_years": 2})
    assert t.name == "1" and t.interval("starts") == 300 and t.interval("years") == 2
