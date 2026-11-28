# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Incremental maintenance cost on the synthetic runs of test_gtopt_usage.

G1 (gas_turbine, Pmin 10, Pmax 30), blocks of 2 h, p = 0.5 per scenario.
With ``load_weight = {a: 1, b: -0.5}`` (low-load penalty): w(10) = 1,
w(20) = 0.75, w(30) = 0.5.

* run B: dispatch [10, 20, 0, 30] -> weighted hours 2·1 + 2·0.75 + 2·0.5
  = 4.5; starts 0.5 (Kd 10 -> 5 EOH); cpf reserve 40 MW·h, α_cpf 0.6
  -> 0.6·40/30 = 0.8 EOH.  EOH_B = 10.3.
* run A: dispatch [10, 0, 0, 30] -> 2 + 1 = 3 EOH.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from gtopt_compare_runs.reader import Run
from gtopt_usage.counters import CalendarEvent
from gtopt_usage.incremental import (
    WearParams,
    WearParamSet,
    calendar_from_outages,
    default_types,
    incremental_maintenance,
    load_wear_params,
    read_calendar_csv,
    read_initial_csv,
    unit_periods,
)
from gtopt_usage.main import main
from gtopt_usage.tests.test_gtopt_usage import (
    PLANNING,
    _base_run,
    _rows,
    _sscc_run,
    _write,
)

TYPES = [
    {"name": "CI", "interval_eoh": 10, "cost": 100},
    {"name": "major", "interval_eoh": 100, "cost": 1000},
]

COUNTERS: dict[str, Any] = {
    "discount_rate": 0.0,
    "technologies": {
        "gas_turbine": {
            "kd": 10,
            "load_weight": {"a": 1.0, "b": -0.5},
            "alpha_reserve": {"cpf": 0.6},
            "types": TYPES,
        },
        "bess": {"types": [{"name": "cell", "interval_cycles": 4, "cost": 80}]},
    },
    "units": {"G1": {"pmin": 10, "pmax": 30}},
}


def _params(data: dict[str, Any] | None = None) -> WearParamSet:
    return WearParamSet.from_dict(data or COUNTERS)


def _runs(tmp_path: Path) -> tuple[Run, Run]:
    return Run.load(_base_run(tmp_path / "a")), Run.load(_sscc_run(tmp_path / "b"))


def _unit(rep_units: pd.DataFrame, name: str) -> pd.Series:
    return rep_units.set_index("name").loc[name]


def test_unit_periods_eoh_hand_values(tmp_path: Path) -> None:
    a, b = _runs(tmp_path)
    ub = unit_periods(b, _params())["G1"]
    ua = unit_periods(a, _params())["G1"]
    assert [p.eoh for p in ub.periods] == pytest.approx([3.5 + 0.4, 1.0 + 5 + 0.4])
    assert ub.eoh == pytest.approx(10.3)
    assert ua.eoh == pytest.approx(3.0)
    assert ub.starts == pytest.approx(0.5)
    assert [p.t0 for p in ub.periods] == [0.0, 4.0]
    assert [p.hours for p in ub.periods] == [4.0, 4.0]
    assert ub.periods[0].energy_mwh == pytest.approx(60.0)


def test_rate_method_and_theta(tmp_path: Path) -> None:
    a, b = _runs(tmp_path)
    rep = incremental_maintenance(a, b, _params())
    g1 = _unit(rep.units, "G1")
    assert g1["delta_eoh"] == pytest.approx(7.3)
    assert g1["rate_delta"] == pytest.approx(7.3 * (100 / 10 + 1000 / 100))
    th = rep.theta.set_index("service")
    # ΔR: cpf 40, csf 8 (weights 1) -> one θ for both services.
    assert th.loc["cpf", "delta_reserve_mwh"] == pytest.approx(40.0)
    assert th.loc["cpf", "theta_rate"] == pytest.approx(146.0 / 48.0)
    assert th.loc["csf", "theta_rate"] == pytest.approx(146.0 / 48.0)
    assert th["rate_cost"].sum() == pytest.approx(146.0)
    tech = rep.technologies.set_index("technology").loc["gas_turbine"]
    assert tech["rate_net"] == pytest.approx(146.0)
    assert tech["rate_gross"] == pytest.approx(146.0)
    assert tech["rate_savings"] == 0.0


def test_event_equals_rate_without_events_at_zero_rate(tmp_path: Path) -> None:
    data = json.loads(json.dumps(COUNTERS))
    data["technologies"]["gas_turbine"]["types"] = [
        {"name": "major", "interval_eoh": 100, "cost": 1000}
    ]
    a, b = _runs(tmp_path)
    rep = incremental_maintenance(a, b, _params(data))
    g1 = _unit(rep.units, "G1")
    assert g1["event_delta"] == pytest.approx(73.0)
    assert g1["rate_delta"] == pytest.approx(73.0)
    assert g1["terminal_delta"] == pytest.approx(73.0)
    th = rep.theta.set_index("service")
    assert th.loc["cpf", "theta_event"] == pytest.approx(th.loc["cpf", "theta_rate"])


def test_event_components_add_up(tmp_path: Path) -> None:
    data = json.loads(json.dumps(COUNTERS))
    data["discount_rate"] = 0.06
    data["technologies"]["gas_turbine"]["types"][0]["interval_eoh"] = 4
    data["technologies"]["gas_turbine"]["replacement_price"] = 50
    data["technologies"]["gas_turbine"]["types"][0]["outage_days"] = 1
    a, b = _runs(tmp_path)
    rep = incremental_maintenance(a, b, _params(data), hours_per_year=4.0)
    g1 = _unit(rep.units, "G1")
    parts = ["extra_delta", "advance_delta", "replacement_delta", "terminal_delta"]
    assert g1["event_delta"] == pytest.approx(sum(g1[c] for c in parts))
    assert g1["event_delta"] == pytest.approx(g1["event_b"] - g1["event_a"])
    assert g1["replacement_delta"] > 0
    assert (rep.events["source"] == "extra").any()


def test_identical_runs_cost_nothing(tmp_path: Path) -> None:
    b1 = Run.load(_sscc_run(tmp_path / "b1"))
    b2 = Run.load(_sscc_run(tmp_path / "b2"))
    cal = [CalendarEvent("G1", "CI", 3.0), CalendarEvent("G1", "major", 6.0)]
    rep = incremental_maintenance(b1, b2, _params(), calendar=cal)
    assert (rep.units[["rate_delta", "event_delta"]].abs() < 1e-9).all().all()
    assert (rep.technologies[["rate_net", "event_net"]].abs() < 1e-9).all().all()
    # Sunk calendar: both runs see the same scheduled events.
    cal_ev = rep.events[rep.events["source"] == "calendar"]
    assert len(cal_ev) == 4


def test_negative_delta_is_a_saving(tmp_path: Path) -> None:
    a, b = _runs(tmp_path)
    rep = incremental_maintenance(b, a, _params())  # B used more: saving
    tech = rep.technologies.set_index("technology").loc["gas_turbine"]
    assert tech["rate_net"] == pytest.approx(-146.0)
    assert tech["rate_savings"] == pytest.approx(-146.0)
    assert tech["rate_gross"] == 0.0
    # ΔR < 0 for every service: θ is undefined (NaN), not a fake number.
    assert rep.theta["theta_rate"].isna().all()


def test_bess_cycles_and_dod(tmp_path: Path) -> None:
    a, b = _runs(tmp_path)
    p = _params()
    ub = unit_periods(b, p)["B1"]
    assert [x.cycles for x in ub.periods] == pytest.approx([1.0, 1.0])
    assert ub.eoh == 0.0
    data = json.loads(json.dumps(COUNTERS))
    data["technologies"]["bess"].update({"dod_exponent": 1.0, "dod_ref": 0.5})
    # SoC 20..80 in scenario 1, 50..50 in scenario 2 -> E[DoD/0.5] = 0.6
    _write(
        tmp_path / "b",
        "Battery",
        "energy_sol",
        _rows(1, [20, 80, 20, 80], scen=(1,)) + _rows(1, [50, 50, 50, 50], scen=(2,)),
    )
    ub = unit_periods(Run.load(tmp_path / "b"), _params(data))["B1"]
    assert [x.cycles for x in ub.periods] == pytest.approx([0.6, 0.6])
    rep = incremental_maintenance(a, b, p)
    assert _unit(rep.units, "B1")["delta_cycles"] == pytest.approx(0.0)


def test_bus_price_for_replacement(tmp_path: Path) -> None:
    plan = json.loads(json.dumps(PLANNING))
    plan["system"]["bus_array"] = [{"uid": 1, "name": "N1"}]
    plan["system"]["generator_array"][0]["bus"] = 1
    out = _sscc_run(tmp_path / "b")
    (out / "planning.json").write_text(json.dumps(plan), encoding="utf-8")
    _write(out, "Bus", "balance_dual", _rows(1, [10, 30, 50, 50]))
    ub = unit_periods(Run.load(out), _params())["G1"]
    assert [x.price for x in ub.periods] == pytest.approx([20.0, 50.0])


def test_load_weight_sign_and_validation() -> None:
    p = WearParams(load_a=1.0, load_b=-0.5)
    assert list(p.load_weight([0.0, 10.0, 20.0, 40.0], 10.0, 30.0)) == (
        pytest.approx([1.0, 1.0, 0.75, 0.5])
    )
    up = WearParams(load_a=0.5, load_b=1.0)  # wear grows with load
    assert list(up.load_weight([30.0], 10.0, 30.0)) == pytest.approx([1.5])
    flat = WearParams()
    assert list(flat.load_weight([5.0], 0.0, 0.0)) == [1.0]
    with pytest.raises(ValueError, match="load_weight"):
        WearParams.from_dict({"load_weight": {"a": 0.2, "b": -0.5}})
    with pytest.raises(ValueError, match="load_weight"):
        WearParams.from_dict({"load_weight": {"a": -0.1}})
    with pytest.raises(ValueError, match="pmax > pmin"):
        p.load_weight([1.0], 10.0, 10.0)


@pytest.mark.parametrize(
    ("data", "msg"),
    [
        ({"kd": -1}, "kd"),
        ({"foo": 1}, "unknown"),
        ({"load_weight": 1}, "load_weight"),
        ({"alpha_reserve": 1}, "alpha_reserve"),
        ({"alpha_reserve": {"cpf": -1}}, "alpha_reserve"),
        ({"reset_fraction": 1.0}, "reset_fraction"),
        ({"dod_ref": 0}, "dod_ref"),
        ({"replacement_price": -1}, "replacement_price"),
        ({"initial": 1}, "initial"),
        ({"initial": {"CI": {"x": 1}}}, "measures"),
    ],
)
def test_wear_params_validation(data: dict, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        WearParams.from_dict(data)


@pytest.mark.parametrize(
    ("data", "msg"),
    [
        ({"foo": {}}, "section"),
        ({"service_weights": {"cpf": -1}}, "service_weights"),
        ({"discount_rate": -1}, "discount_rate"),
        ({"units": {"G1": {"kd": -2}}}, "kd"),
    ],
)
def test_param_set_validation(data: dict, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        WearParamSet.from_dict(data)


def test_param_set_lookup_and_initial(tmp_path: Path) -> None:
    ps = _params()
    assert ps.for_unit("X", "unknown") is None
    assert ps.for_unit("X", "gas_turbine") is ps.technologies["gas_turbine"]
    g1 = ps.for_unit("G1", "gas_turbine")
    assert g1 is not None and g1.pmax == 30 and g1.kd == 10
    path = tmp_path / "init.csv"
    path.write_text("unit,type,eoh,starts\nG1,CI,9.5,\n", encoding="utf-8")
    init = read_initial_csv(path)
    assert init == {"G1": {"CI": {"eoh": 9.5}}}
    ps2 = ps.with_initial(init)
    g1b = ps2.for_unit("G1", "gas_turbine")
    assert g1b is not None and g1b.initial == {"CI": {"eoh": 9.5}}
    assert default_types(ps, {"G1": "gas_turbine", "Z": "none"}) == {"G1": "major"}
    bad = tmp_path / "bad.csv"
    bad.write_text("unit,type\n,CI\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unit and type"):
        read_initial_csv(bad)
    jf = tmp_path / "c.json"
    jf.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_wear_params(jf)


def test_alpha_needs_pmax(tmp_path: Path) -> None:
    data = json.loads(json.dumps(COUNTERS))
    data["units"] = {}
    with pytest.raises(ValueError, match="pmax"):
        unit_periods(Run.load(_sscc_run(tmp_path / "b")), _params(data))


def test_calendar_csv(tmp_path: Path) -> None:
    path = tmp_path / "cal.csv"
    path.write_text(
        "unit,type,start,duration\nG1,CI,48,2\nG1,major,2027-01-03,\n",
        encoding="utf-8",
    )
    cal = read_calendar_csv(path, start_date="2027-01-01")
    assert cal[0] == CalendarEvent("G1", "CI", 48.0, 48.0)
    assert cal[1] == CalendarEvent("G1", "major", 48.0, 0.0)
    with pytest.raises(ValueError, match="start-date"):
        read_calendar_csv(path)
    bad = tmp_path / "bad.csv"
    bad.write_text("unit,type,start\nG1,CI,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="needs unit"):
        read_calendar_csv(bad)


def test_calendar_from_outages(tmp_path: Path) -> None:
    out = _sscc_run(tmp_path / "b")
    _write(out, "Generator", "outage_fraction_sol", _rows(1, [0.5, 0.5, 0, 1]))
    cal = calendar_from_outages(Run.load(out), {"G1": "major"})
    assert cal == [
        CalendarEvent("G1", "major", 0.0, 2.0),
        CalendarEvent("G1", "major", 6.0, 2.0),
    ]
    assert not calendar_from_outages(Run.load(out), {})
    assert calendar_from_outages(Run.load(out), {}, "CI")[0].type == "CI"
    assert not calendar_from_outages(Run.load(_base_run(tmp_path / "a")), {})


def test_cli_maintenance(tmp_path: Path) -> None:
    a, b = _base_run(tmp_path / "a"), _sscc_run(tmp_path / "b")
    _write(a, "Generator", "outage_fraction_sol", _rows(1, [0, 0, 1, 0]))
    cfg = tmp_path / "w.json"
    cfg.write_text(json.dumps(COUNTERS), encoding="utf-8")
    csv_dir, js = tmp_path / "csv", tmp_path / "m.json"
    args = ["maintenance", str(a), str(b), "--counters", str(cfg)]
    assert main([*args, "--csv", str(csv_dir), "--json", str(js)]) == 0
    assert (csv_dir / "maintenance_theta.csv").is_file()
    data = json.loads(js.read_text(encoding="utf-8"))
    cal = [e for e in data["events"] if e["source"] == "calendar"]
    assert cal and cal[0]["type"] == "major" and cal[0]["time_h"] == 4.0
    init = tmp_path / "i.csv"
    init.write_text("unit,type,eoh\nG1,CI,9\n", encoding="utf-8")
    calf = tmp_path / "cal.csv"
    calf.write_text("unit,type,start\nG1,CI,2\n", encoding="utf-8")
    extra = ["--initial", str(init), "--discount-rate", "0.1", "--calendar", str(calf)]
    assert main([*args, *extra]) == 0
    assert main([*args, "--no-outage-calendar", "--outage-type", "CI"]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"technologies": {"x": {"kd": -1}}}), encoding="utf-8")
    assert main(["maintenance", str(a), str(b), "--counters", str(bad)]) == 2


# ── per-direction products ─────────────────────────────────────────────


def _updn_runs(tmp_path: Path) -> tuple[Run, Run]:
    """Run B plus 1 MW of cpf down on G1: ΔR cpf up 40, cpf dn 8, csf up 8."""
    b = _sscc_run(tmp_path / "b")
    _write(
        b,
        "ReserveProvision",
        "dprovision_sol",
        _rows(1, [1, 1, 1, 1]) + _rows(3, [2, 2, 2, 2]),
    )
    return Run.load(_base_run(tmp_path / "a")), Run.load(b)


def _updn_params(alpha: Any, weights: Any) -> WearParamSet:
    data = json.loads(json.dumps(COUNTERS))
    data["technologies"]["gas_turbine"]["alpha_reserve"] = alpha
    data["service_weights"] = weights
    return WearParamSet.from_dict(data)


def test_theta_per_direction_rate_and_event(tmp_path: Path) -> None:
    a, b = _updn_runs(tmp_path)
    params = _updn_params({"cpf": {"up": 0.6, "dn": 1.5}}, {"cpf_up": 2, "cpf_dn": 1})
    rep = incremental_maintenance(a, b, params)
    # EOH_B = 10.3 + 1.5·8/30 = 10.7; ΔEOH = 7.7 -> rate 7.7·20 = 154
    g1 = _unit(rep.units, "G1")
    assert g1["delta_eoh"] == pytest.approx(7.7)
    assert g1["rate_delta"] == pytest.approx(154.0)
    gt = rep.theta[rep.theta["technology"] == "gas_turbine"]
    th = gt.set_index(["service", "direction"])
    assert sorted(th.index) == [("cpf", "dn"), ("cpf", "up"), ("csf", "up")]
    assert th.loc[("cpf", "dn"), "delta_reserve_mwh"] == pytest.approx(8.0)
    # weights 2 / 1 / 1 (default): denom 2·40 + 8 + 8 = 96
    assert th.loc[("cpf", "up"), "theta_rate"] == pytest.approx(154 * 2 / 96)
    assert th.loc[("cpf", "dn"), "theta_rate"] == pytest.approx(154 / 96)
    assert th.loc[("csf", "up"), "theta_rate"] == pytest.approx(154 / 96)
    assert gt["rate_cost"].sum() == pytest.approx(154.0)
    up = th.loc[("cpf", "up"), "theta_event"]
    dn = th.loc[("cpf", "dn"), "theta_event"]
    assert up == pytest.approx(2 * dn)
    net = rep.technologies.set_index("technology").loc["gas_turbine", "event_net"]
    assert float(gt["event_cost"].sum()) == pytest.approx(float(net))


def test_alpha_flat_per_service_is_backward_compatible(tmp_path: Path) -> None:
    a, b = _updn_runs(tmp_path)
    params = _updn_params({"cpf": 0.6}, {})
    rep = incremental_maintenance(a, b, params)
    # flat α on both cpf directions: 0.6·48/30 = 0.96 EOH -> EOH_B 10.46
    assert _unit(rep.units, "G1")["delta_eoh"] == pytest.approx(7.46)
    th = rep.theta.set_index(["service", "direction"])["theta_rate"]
    # default weights: one θ for every product
    assert th[("cpf", "up")] == pytest.approx(th[("cpf", "dn")])
    assert th[("cpf", "up")] == pytest.approx(7.46 * 20 / 56)
    p = WearParams.from_dict({"alpha_reserve": {"cpf": {"dn": 2}, "*": 1}})
    assert p.alpha("cpf", "dn") == 2.0 and p.alpha("cpf", "up") == 1.0
    assert p.alpha("cpf") == 1.0
    assert params.service_weight("csf", "dn") == 1.0
