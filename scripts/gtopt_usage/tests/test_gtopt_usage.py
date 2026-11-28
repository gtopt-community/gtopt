# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests of gtopt_usage on synthetic gtopt output directories.

Grid: 2 scenarios (p = 0.5 each) x 2 stages x 2 blocks of 2 h; with
``hours_per_year = 4`` stage 1 is year 1 and stage 2 is year 2.
Every expected value below is hand-computed.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from gtopt_compare_runs.reader import Run
from gtopt_usage import (
    MaintenanceParams,
    ParamSet,
    derive_theta,
    load_params,
    maintenance_table,
    reserve_table,
    usage_table,
)
from gtopt_usage.accounting import (
    _energy_capacity,
    _ref_name,
    known_cost_table,
    per_year,
    provision_info,
    stage_years,
)
from gtopt_usage.main import main
from gtopt_usage.maintenance import by_technology
from gtopt_usage.theta import ThetaReport, horizon_reserve, horizon_usage
from gtopt_usage.wear_keys import parse_factors, resolve

HPY = 4.0  # hours per year of the synthetic grid

PLANNING: dict[str, Any] = {
    "simulation": {
        "block_array": [{"uid": b, "duration": 2.0} for b in (1, 2, 3, 4)],
        "stage_array": [
            {"uid": 1, "first_block": 0, "count_block": 2},
            {"uid": 2, "first_block": 2, "count_block": 2},
        ],
        "scenario_array": [
            {"uid": 1, "probability_factor": 0.5},
            {"uid": 2, "probability_factor": 0.5},
        ],
    },
    "system": {
        "generator_array": [
            {"uid": 1, "name": "G1", "type": "gas_turbine"},
            {"uid": 2, "name": "G2", "type": "hydro"},
        ],
        "battery_array": [{"uid": 1, "name": "B1", "type": "bess", "capacity": 100}],
        "commitment_array": [{"uid": 1, "name": "uc1", "generator": "G1"}],
        "reserve_zone_array": [
            {"uid": 1, "name": "cpf"},
            {"uid": 2, "name": "csf"},
        ],
        "reserve_provision_array": [
            {"uid": 1, "name": "p1", "generator": 1, "reserve_zones": [1]},
            {"uid": 2, "name": "p2", "generator": "G1", "reserve_zones": ["csf"]},
            {"uid": 3, "name": "p3", "generator": 2, "reserve_zones": [1, 2]},
        ],
    },
}

STAGE_OF = {1: 1, 2: 1, 3: 2, 4: 2}


def _rows(uid: int, per_block: list[float], scen: tuple[int, ...] = (1, 2)) -> list:
    return [
        (s, STAGE_OF[b], b, uid, v)
        for s in scen
        for b, v in zip((1, 2, 3, 4), per_block, strict=True)
    ]


def _write(out: Path, cls: str, fld: str, rows: list) -> None:
    df = pd.DataFrame(rows, columns=["scenario", "stage", "block", "uid", "value"])
    d = out / cls
    d.mkdir(parents=True, exist_ok=True)
    df.to_parquet(d / f"{fld}.parquet", index=False)


def _sscc_run(out: Path, *, usage: bool = True) -> Path:
    """Run B (with SSCC) — see the module doc for the hand values."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "planning.json").write_text(json.dumps(PLANNING), encoding="utf-8")
    _write(out, "Generator", "generation_sol", _rows(1, [10, 20, 0, 30]))
    if usage:
        _write(out, "Generator", "usage_on_sol", _rows(1, [1, 1, 0, 1]))
        _write(out, "Generator", "usage_ramp_sol", _rows(1, [0, 10, 0, 30]))
        _write(out, "Commitment", "usage_at_pmin_sol", _rows(1, [1, 0, 0, 0]))
        _write(out, "Commitment", "usage_pmin_sol", _rows(1, [5, 5, 5, 5]))
    _write(out, "Commitment", "status_sol", _rows(1, [1, 1, 0, 1]))
    _write(out, "Commitment", "startup_sol", _rows(1, [0, 0, 0, 1], scen=(1,)))
    _write(out, "Commitment", "shutdown_sol", _rows(1, [0, 0, 1, 0]))
    _write(
        out,
        "ReserveProvision",
        "uprovision_sol",
        _rows(1, [5, 5, 5, 5]) + _rows(2, [1, 1, 1, 1]),
    )
    _write(out, "ReserveProvision", "dprovision_sol", _rows(3, [2, 2, 2, 2]))
    _write(out, "ReserveProvision", "up_activation_cost_sol", _rows(1, [3, 3, 3, 3]))
    _write(out, "Battery", "fout_sol", _rows(1, [50, 0, 50, 0]))
    _write(out, "Battery", "finp_sol", _rows(1, [0, 60, 0, 60]))
    return out


def _base_run(out: Path) -> Path:
    """Run A (no SSCC): G1 runs 2 blocks, no starts, no reserve."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "planning.json").write_text(json.dumps(PLANNING), encoding="utf-8")
    _write(out, "Generator", "generation_sol", _rows(1, [10, 0, 0, 30]))
    _write(out, "Generator", "usage_on_sol", _rows(1, [1, 0, 0, 1]))
    _write(out, "Battery", "fout_sol", _rows(1, [50, 0, 50, 0]))
    _write(out, "Battery", "finp_sol", _rows(1, [0, 60, 0, 60]))
    return out


PARAMS = {
    "technologies": {
        "gas_turbine": {"interval_eoh": 100, "cost_per_event": 1000, "kd": 10},
        "bess": {"interval_cycles": 10, "cost_per_event": 500},
    }
}


def _row(df: pd.DataFrame, name: str, k: int) -> pd.Series:
    sel = df[(df["name"] == name) & (df["k"] == k)]
    assert len(sel) == 1, (name, k, df)
    return sel.iloc[0]


# ── accounting ─────────────────────────────────────────────────────────


def test_usage_table_hand_values(tmp_path: Path) -> None:
    run = Run.load(_sscc_run(tmp_path / "b"))
    df = usage_table(run, hours_per_year=HPY)
    g0, g1 = _row(df, "G1", 0), _row(df, "G1", 1)
    assert g0["hours"] == pytest.approx(4.0)
    assert g1["hours"] == pytest.approx(2.0)
    assert g0["energy_mwh"] == pytest.approx(60.0)
    assert g1["energy_mwh"] == pytest.approx(60.0)
    assert g0["pmin_hours"] == pytest.approx(2.0)
    assert g1["starts"] == pytest.approx(0.5)  # one start in scenario 1 only
    assert g1["shutdowns"] == pytest.approx(1.0)
    assert g0["ramp_mw"] == pytest.approx(10.0)
    assert g1["ramp_mw"] == pytest.approx(30.0)
    assert g0["reserve_up_mwh"] == pytest.approx(20.0 + 4.0)
    assert (g0["year"], g1["year"]) == (1, 2)
    assert (g0["stage"], g1["stage"]) == (1, 2)
    assert g0["kind"] == "generator" and g0["technology"] == "gas_turbine"
    b0 = _row(df, "B1", 0)
    assert b0["kind"] == "battery" and b0["technology"] == "bess"
    assert b0["discharge_mwh"] == pytest.approx(100.0)
    assert b0["charge_mwh"] == pytest.approx(120.0)
    assert b0["throughput_mwh"] == pytest.approx(110.0)
    assert b0["efc"] == pytest.approx(1.0)
    h0 = _row(df, "G2", 0)
    assert h0["reserve_dn_mwh"] == pytest.approx(8.0)


def test_usage_invariants(tmp_path: Path) -> None:
    run = Run.load(_sscc_run(tmp_path / "b"))
    df = usage_table(run, hours_per_year=HPY)
    stage_hours = 4.0
    assert (df["hours"] >= 0).all() and (df["hours"] <= stage_hours).all()
    assert (df["pmin_hours"] <= df["hours"] + 1e-12).all()
    assert (df[["starts", "shutdowns", "ramp_mw", "efc"]] >= 0).all().all()
    yearly = per_year(df)
    assert yearly["energy_mwh"].sum() == pytest.approx(df["energy_mwh"].sum())
    assert set(yearly["year"]) == {1, 2}


def test_fallback_hours_without_usage_streams(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run = Run.load(_sscc_run(tmp_path / "b", usage=False))
    df = usage_table(run, hours_per_year=HPY)
    assert _row(df, "G1", 0)["hours"] == pytest.approx(4.0)
    assert _row(df, "G1", 1)["hours"] == pytest.approx(2.0)
    assert _row(df, "G1", 0)["ramp_mw"] == 0.0
    assert _row(df, "G1", 0)["pmin_hours"] == 0.0
    assert "output_usage_metrics" in caplog.text


@pytest.mark.parametrize(("band", "hours"), [(1.0, 2.0), (3.0, 4.0)])
def test_pmin_band(tmp_path: Path, band: float, hours: float) -> None:
    # p <= 5·u·(1+band): band 1 -> p <= 10 (block 1); band 3 -> p <= 20.
    run = Run.load(_sscc_run(tmp_path / "b"))
    df = usage_table(run, hours_per_year=HPY, pmin_band=band)
    assert _row(df, "G1", 0)["pmin_hours"] == pytest.approx(hours)


def test_pmin_band_without_streams(tmp_path: Path) -> None:
    run = Run.load(_sscc_run(tmp_path / "b", usage=False))
    df = usage_table(run, hours_per_year=HPY, pmin_band=0.1)
    assert _row(df, "G1", 0)["pmin_hours"] == 0.0


def test_empty_run(tmp_path: Path) -> None:
    out = tmp_path / "empty"
    out.mkdir()
    (out / "planning.json").write_text(json.dumps(PLANNING), encoding="utf-8")
    run = Run.load(out)
    assert usage_table(run).empty
    assert reserve_table(run).empty
    assert known_cost_table(run).empty
    assert per_year(usage_table(run)).empty
    assert horizon_usage(usage_table(run)).empty
    assert horizon_reserve(reserve_table(run)).empty


def test_reserve_and_known_cost_tables(tmp_path: Path) -> None:
    run = Run.load(_sscc_run(tmp_path / "b"))
    res = reserve_table(run, hours_per_year=HPY)
    by = res.groupby(["name", "service", "direction"])["mwh"].sum()
    assert by[("G1", "cpf", "up")] == pytest.approx(40.0)
    assert by[("G1", "csf", "up")] == pytest.approx(8.0)
    assert by[("G2", "cpf+csf", "dn")] == pytest.approx(16.0)
    known = known_cost_table(run, hours_per_year=HPY)
    assert set(known["term"]) == {"activation"}
    assert known["cost"].sum() == pytest.approx(24.0)


def test_helpers(tmp_path: Path) -> None:
    assert _ref_name(True, {}) is None
    assert _ref_name(3, {}) == "uid:3"
    assert _ref_name(None, {}) is None
    assert _energy_capacity({"emax": [[10, 40]]}) == 40.0
    assert _energy_capacity({"capacity": 0}) is None
    assert _energy_capacity({}) is None
    plan = json.loads(json.dumps(PLANNING))
    plan["system"]["reserve_provision_array"].append(
        {"uid": 4, "name": "p4", "generator": 1, "reserve_zones": "1:csf"}
    )
    plan["system"]["reserve_provision_array"].append({"uid": 5, "name": "p5"})
    out = tmp_path / "h"
    out.mkdir()
    (out / "planning.json").write_text(json.dumps(plan), encoding="utf-8")
    run = Run.load(out)
    info = provision_info(run)
    assert info["p4"] == ("G1", "cpf+csf")
    assert "p5" not in info
    years = stage_years(run, 4.0)
    assert list(years) == [1, 2]
    assert list(stage_years(run)) == [1, 1]


# ── maintenance ────────────────────────────────────────────────────────


def test_params_parsing_and_validation(tmp_path: Path) -> None:
    ps = ParamSet.from_dict(
        {
            **PARAMS,
            "units": {"G1": {"kd": 20}, "X": {"technology": "bess"}},
            "service_weights": {"cpf": 2},
        }
    )
    g1 = ps.for_unit("G1", "gas_turbine")
    assert g1 is not None and g1.kd == 20 and g1.interval_eoh == 100
    assert ps.technology_of("X", "other") == "bess"
    assert ps.for_unit("nobody", "nuclear") is None
    assert ParamSet.from_dict({"units": {"Z": {"kd": 1}}}).for_unit("Z", "t")
    path = tmp_path / "p.json"
    path.write_text(json.dumps(PARAMS), encoding="utf-8")
    assert load_params(path).technologies["bess"].interval_cycles == 10
    bad = [
        {"technologies": {"t": {"interval_eoh": 0}}},
        {"technologies": {"t": {"kd": -1}}},
        {"technologies": {"t": {"bogus": 1}}},
        {"technologies": {"t": {"eoh_per_reserve_mwh": [1]}}},
        {"technologies": {"t": {"eoh_per_reserve_mwh": {"cpf": -1}}}},
        {"nope": {}},
        {"units": {"G1": 3}},
        {"service_weights": {"cpf": -1}},
    ]
    for data in bad:
        with pytest.raises(ValueError):
            ParamSet.from_dict(data)
    path.write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_params(path)
    mp = MaintenanceParams(eoh_per_reserve_mwh={"*": 0.5, "cpf": 1.0})
    assert mp.reserve_factor("cpf") == 1.0 and mp.reserve_factor("ctf") == 0.5


def test_maintenance_hand_values(tmp_path: Path) -> None:
    run = Run.load(_sscc_run(tmp_path / "b"))
    usage = horizon_usage(usage_table(run, hours_per_year=HPY))
    params = ParamSet.from_dict(PARAMS)
    m = maintenance_table(usage, params, None, HPY)
    g1 = m[m["name"] == "G1"].iloc[0]
    # EOH = hours 6 + kd 10 · starts 0.5 = 11 -> events 0.11 -> cost 110
    assert g1["eoh"] == pytest.approx(11.0)
    assert g1["events"] == pytest.approx(0.11)
    assert g1["cost"] == pytest.approx(110.0)
    assert g1["heom"] == pytest.approx(100.0 * 6.0 / 11.0)
    b1 = m[m["name"] == "B1"].iloc[0]
    # EFC = 200 MWh / 100 MWh = 2 -> events 0.2 -> cost 100
    assert b1["efc"] == pytest.approx(2.0)
    assert b1["events"] == pytest.approx(0.2)
    assert b1["cost"] == pytest.approx(100.0)
    g2 = m[m["name"] == "G2"].iloc[0]
    assert g2["cost"] == 0.0  # hydro: no parameters
    tech = by_technology(m).set_index("technology")
    assert tech.loc["gas_turbine", "cost"] == pytest.approx(110.0)
    assert tech.loc["gas_turbine", "units"] == 1
    assert by_technology(m.iloc[0:0]).empty


def test_maintenance_extra_wear_terms(tmp_path: Path) -> None:
    run = Run.load(_sscc_run(tmp_path / "b"))
    usage = horizon_usage(usage_table(run, hours_per_year=HPY))
    reserve = horizon_reserve(reserve_table(run, hours_per_year=HPY))
    params = ParamSet.from_dict(
        {
            "technologies": {
                "gas_turbine": {
                    "interval_eoh": 100,
                    "interval_starts": 1,
                    "cost_per_event": 1000,
                    "kf": 50,
                    "failures_per_year": 2,
                    "eoh_per_pmin_hour": 0.5,
                    "eoh_per_ramp_mw": 0.1,
                    "eoh_per_reserve_mwh": {"cpf": 0.25},
                    "outage_days": 1,
                }
            }
        }
    )
    m = maintenance_table(usage, params, reserve, HPY)
    g1 = m[m["name"] == "G1"].iloc[0]
    nf = 2 * 6.0 / HPY
    eoh = 6.0 + 50 * nf + 0.5 * 2.0 + 0.1 * 40.0 + 0.25 * 40.0
    assert g1["eoh"] == pytest.approx(eoh)
    # starts 0.5 / interval 1 = 0.5 > EOH events -> whichever first
    assert g1["events"] == pytest.approx(max(eoh / 100, 0.5))
    assert g1["outage_hours"] == pytest.approx(g1["events"] * 24)
    assert maintenance_table(usage.iloc[0:0], params).empty


# ── θ ──────────────────────────────────────────────────────────────────


def test_theta_hand_values(tmp_path: Path) -> None:
    a = Run.load(_base_run(tmp_path / "a"), label="A")
    b = Run.load(_sscc_run(tmp_path / "b"), label="B")
    rep = derive_theta(a, b, ParamSet.from_dict(PARAMS), hours_per_year=HPY)
    tech = rep.technologies.set_index("technology")
    # A: EOH 4 -> 40 $; B: EOH 11 -> 110 $
    assert tech.loc["gas_turbine", "delta_cost"] == pytest.approx(70.0)
    assert tech.loc["bess", "delta_cost"] == pytest.approx(0.0)
    th = rep.theta.set_index(["technology", "service"])
    assert th.loc[("gas_turbine", "cpf"), "theta_maintenance"] == pytest.approx(
        70.0 / 48.0
    )
    assert th.loc[("gas_turbine", "csf"), "theta_maintenance"] == pytest.approx(
        70.0 / 48.0
    )
    assert th.loc[("gas_turbine", "cpf"), "theta_known_activation"] == pytest.approx(
        24.0 / 40.0
    )
    assert th.loc[("gas_turbine", "cpf"), "theta_total"] == pytest.approx(
        70.0 / 48.0 + 0.6
    )
    assert th.loc[("hydro", "cpf+csf"), "theta_maintenance"] == pytest.approx(0.0)
    # invariant: the delta is fully allocated
    gt = rep.theta[rep.theta["technology"] == "gas_turbine"]
    assert float(
        (gt["theta_maintenance"] * gt["delta_reserve_mwh"]).sum()
    ) == pytest.approx(70.0)
    u = rep.units.set_index("name")
    assert u.loc["G1", "delta_hours"] == pytest.approx(2.0)


def test_theta_service_weights(tmp_path: Path) -> None:
    a = Run.load(_base_run(tmp_path / "a"))
    b = Run.load(_sscc_run(tmp_path / "b"))
    params = ParamSet.from_dict({**PARAMS, "service_weights": {"cpf": 2.0}})
    th = derive_theta(a, b, params, hours_per_year=HPY).theta
    th = th.set_index(["technology", "service"])["theta_maintenance"]
    assert th[("gas_turbine", "cpf")] == pytest.approx(70.0 * 2 / 88)
    assert th[("gas_turbine", "csf")] == pytest.approx(70.0 / 88)
    assert th[("gas_turbine", "cpf")] * 40 + th[
        ("gas_turbine", "csf")
    ] * 8 == pytest.approx(70.0)


def test_theta_self_comparison_is_zero(tmp_path: Path) -> None:
    a = Run.load(_sscc_run(tmp_path / "a"))
    b = Run.load(_sscc_run(tmp_path / "b"))
    rep = derive_theta(a, b, ParamSet.from_dict(PARAMS), hours_per_year=HPY)
    assert (rep.technologies["delta_cost"].abs() < 1e-12).all()
    # no reserve delta -> θ undefined
    assert rep.theta["theta_maintenance"].isna().all()


def test_theta_empty_runs(tmp_path: Path) -> None:
    for name in ("a", "b"):
        out = tmp_path / name
        out.mkdir()
        (out / "planning.json").write_text(json.dumps(PLANNING), encoding="utf-8")
    rep = derive_theta(Run.load(tmp_path / "a"), Run.load(tmp_path / "b"), ParamSet())
    assert rep.units.empty and rep.theta.empty and rep.technologies.empty


def test_report_writers(tmp_path: Path) -> None:
    a = Run.load(_base_run(tmp_path / "a"))
    b = Run.load(_sscc_run(tmp_path / "b"))
    rep = derive_theta(a, b, ParamSet.from_dict(PARAMS), hours_per_year=HPY)
    paths = rep.write_csv(tmp_path / "csv")
    assert [p.name for p in paths] == ["units.csv", "technologies.csv", "theta.csv"]
    data = json.loads(rep.write_json(tmp_path / "t.json").read_text(encoding="utf-8"))
    assert {"units", "technologies", "theta"} <= set(data)
    assert isinstance(rep, ThetaReport)
    assert not np.isnan(
        pd.read_csv(tmp_path / "csv" / "theta.csv")["theta_maintenance"].iloc[0]
    )


# ── CLI ────────────────────────────────────────────────────────────────


def test_cli_usage_and_theta(tmp_path: Path) -> None:
    a = _base_run(tmp_path / "a")
    b = _sscc_run(tmp_path / "b")
    params = tmp_path / "p.json"
    params.write_text(json.dumps(PARAMS), encoding="utf-8")
    for per in ("stage", "year", "horizon"):
        csv = tmp_path / f"u_{per}"
        rc = main(
            [
                "--hours-per-year",
                "4",
                "--width",
                "200",
                "usage",
                str(b),
                "--per",
                per,
                "--params",
                str(params),
                "--csv",
                str(csv),
            ]
        )
        assert rc == 0
        assert (csv / "usage.csv").is_file()
        assert (csv / "technologies.csv").is_file()
    assert main(["usage", str(b)]) == 0
    rc = main(
        [
            "--hours-per-year",
            "4",
            "--pmin-band",
            "0.5",
            "theta",
            str(a),
            str(b),
            "--params",
            str(params),
            "--csv",
            str(tmp_path / "t"),
            "--json",
            str(tmp_path / "t.json"),
        ]
    )
    assert rc == 0
    assert (tmp_path / "t" / "theta.csv").is_file()
    assert main(["theta", str(a), str(tmp_path / "nope"), "--params", str(params)]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"bogus": 1}), encoding="utf-8")
    assert main(["-v", "theta", str(a), str(b), "--params", str(bad)]) == 2


def test_module_entry_point(tmp_path: Path) -> None:
    b = _sscc_run(tmp_path / "b")
    with mock.patch.object(sys, "argv", ["gtopt_usage", "usage", str(b)]):
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("gtopt_usage", run_name="__main__")
    assert exc.value.code == 0


# ── per-direction products (CPF / CSF / CTF, up and dn) ────────────────


def _sscc_run_updn(out: Path) -> Path:
    """Run B plus 1 MW of cpf down on G1 (p1): cpf dn = 8 MW·h."""
    _sscc_run(out)
    _write(
        out,
        "ReserveProvision",
        "dprovision_sol",
        _rows(1, [1, 1, 1, 1]) + _rows(3, [2, 2, 2, 2]),
    )
    return out


UPDN_PARAMS: dict[str, Any] = {
    "technologies": {
        "gas_turbine": {
            "interval_eoh": 100,
            "cost_per_event": 1000,
            "kd": 10,
            "eoh_per_reserve_mwh": {"cpf": {"up": 0.25, "dn": 0.5}},
        }
    },
    "service_weights": {"cpf": {"up": 2.0, "dn": 1.0}},
}


def test_wear_keys_parse_and_resolve() -> None:
    f = parse_factors(
        {"cpf": {"up": 3, "dn": 1}, "csf_dn": 2, "ctf": 4, "*_up": 5, "*": 6},
        "x",
    )
    assert f == {
        "cpf_up": 3.0,
        "cpf_dn": 1.0,
        "csf_dn": 2.0,
        "ctf": 4.0,
        "*_up": 5.0,
        "*": 6.0,
    }
    assert resolve(f, "cpf", "up") == 3.0
    assert resolve(f, "cpf", "dn") == 1.0
    assert resolve(f, "cpf") == 6.0  # no direction: service, then "*"
    assert resolve(f, "csf", "dn") == 2.0
    assert resolve(f, "csf", "up") == 5.0  # "*_up" before "*"
    assert resolve(f, "ctf", "dn") == 4.0  # flat per service
    assert resolve(f, "zzz", "dn") == 6.0
    assert resolve({}, "cpf", "up", 1.0) == 1.0
    for bad in ({"cpf": {"left": 1}}, {"cpf": {"up": -1}}, [1]):
        with pytest.raises(ValueError):
            parse_factors(bad, "x")


def test_theta_per_direction_hand_values(tmp_path: Path) -> None:
    a = Run.load(_base_run(tmp_path / "a"))
    b = Run.load(_sscc_run_updn(tmp_path / "b"))
    rep = derive_theta(a, b, ParamSet.from_dict(UPDN_PARAMS), hours_per_year=HPY)
    # B: EOH = 6 + 10·0.5 + 0.25·40 (cpf up) + 0.5·8 (cpf dn) = 25 -> 250 $
    # A: EOH 4 -> 40 $; ΔC = 210
    tech = rep.technologies.set_index("technology")
    assert tech.loc["gas_turbine", "delta_cost"] == pytest.approx(210.0)
    gt = rep.theta[rep.theta["technology"] == "gas_turbine"]
    th = gt.set_index(["service", "direction"])
    assert len(gt) == 3  # cpf up, cpf dn, csf up
    assert th.loc[("cpf", "up"), "delta_reserve_mwh"] == pytest.approx(40.0)
    assert th.loc[("cpf", "dn"), "delta_reserve_mwh"] == pytest.approx(8.0)
    # weights cpf up 2, cpf dn 1, csf up 1 (default): denom 2·40 + 8 + 8 = 96
    assert th.loc[("cpf", "up"), "theta_maintenance"] == pytest.approx(210 * 2 / 96)
    assert th.loc[("cpf", "dn"), "theta_maintenance"] == pytest.approx(210 / 96)
    assert th.loc[("csf", "up"), "theta_maintenance"] == pytest.approx(210 / 96)
    # the activation adder is an up cost only
    assert th.loc[("cpf", "up"), "theta_known_activation"] == pytest.approx(0.6)
    assert th.loc[("cpf", "dn"), "theta_known_activation"] == 0.0
    assert float(
        (gt["theta_maintenance"] * gt["delta_reserve_mwh"]).sum()
    ) == pytest.approx(210.0)


def test_theta_flat_factor_is_backward_compatible(tmp_path: Path) -> None:
    a = Run.load(_base_run(tmp_path / "a"))
    b = Run.load(_sscc_run_updn(tmp_path / "b"))
    flat = {
        "technologies": {
            "gas_turbine": {
                **UPDN_PARAMS["technologies"]["gas_turbine"],
                "eoh_per_reserve_mwh": {"cpf": 0.25},
            }
        },
        "service_weights": {"cpf": 2.0},
    }
    rep = derive_theta(a, b, ParamSet.from_dict(flat), hours_per_year=HPY)
    # flat cpf factor on both directions: EOH = 11 + 0.25·48 = 23 -> ΔC 190
    tech = rep.technologies.set_index("technology")
    assert tech.loc["gas_turbine", "delta_cost"] == pytest.approx(190.0)
    th = rep.theta.set_index(["technology", "service", "direction"])
    # flat weight 2 on both cpf directions: denom 2·40 + 2·8 + 8 = 104
    up = th.loc[("gas_turbine", "cpf", "up"), "theta_maintenance"]
    dn = th.loc[("gas_turbine", "cpf", "dn"), "theta_maintenance"]
    assert up == pytest.approx(190 * 2 / 104) and dn == pytest.approx(up)
    # the flat key form "cpf_dn" equals the nested one
    mp = MaintenanceParams.from_dict({"eoh_per_reserve_mwh": {"cpf_dn": 0.5}})
    assert mp.reserve_factor("cpf", "dn") == 0.5
    assert mp.reserve_factor("cpf", "up") == 0.0
    assert ParamSet.from_dict(flat).service_weight("csf", "up") == 1.0
