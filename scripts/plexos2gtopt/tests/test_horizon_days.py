# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``--horizon-days N``: end-of-horizon targets, multi-day rules, FCF cut.

Unit tests per rule of :mod:`plexos2gtopt._horizon` and the solution
reader :mod:`plexos2gtopt.plexos_solution`, plus end-to-end tests on the
7-day mini bundle of :mod:`plexos2gtopt.tests.horizon_fixture`: cut to 1
day it must convert with the PLEXOS (fake) end-of-day states, drop the
FCF cut, and -- when a ``gtopt`` binary is available -- solve with CLP
(LP relaxation).  The PCP 2024-12-15 day-1 infeasibility (a battery that
self-discharges and cannot recharge, left on gtopt's hard daily-cycle
close by ``--horizon-mode hourly``) is reproduced on the same fixture.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from plexos2gtopt import _horizon
from plexos2gtopt._horizon import (
    HorizonOptions,
    HorizonReport,
    apply_constraint_rules,
    apply_end_targets,
    apply_horizon,
    lng_tank_names,
    parse_constraint_rules,
    resolve_boundary_cut,
    resolve_end_targets,
)
from plexos2gtopt.entities import BatterySpec
from plexos2gtopt.gtopt_writer import _BATTERY_EFIN_COST, build_battery_array
from plexos2gtopt.main import make_parser
from plexos2gtopt.plexos2gtopt import convert_plexos_bundle
from plexos2gtopt.plexos_solution import (
    PlexosSolution,
    csv_table_source,
    mdb_table_source,
    open_solution,
)
from plexos2gtopt.tests import horizon_fixture as fx

# ── options ──────────────────────────────────────────────────────────


def test_parse_constraint_rules_defaults_and_overrides() -> None:
    assert parse_constraint_rules(None) == _horizon.DEFAULT_CONSTRAINT_RULES
    rules = parse_constraint_rules(" fuel_max_offtake=full, period_rhs=drop ,")
    assert rules["fuel_max_offtake"] == "full"
    assert rules["period_rhs"] == "drop"
    assert rules["day_indexed"] == "keep"


@pytest.mark.parametrize(
    "spec", ["bogus=drop", "fuel_max_offtake", "fuel_min_offtake=full"]
)
def test_parse_constraint_rules_rejects(spec: str) -> None:
    with pytest.raises(ValueError, match="horizon-constraint-rules"):
        parse_constraint_rules(spec)


def test_horizon_options() -> None:
    opts = HorizonOptions.from_options({}, horizon_days=1, full_days=7)
    assert opts.truncated and opts.needs_solution
    assert not HorizonOptions(horizon_days=7, full_days=7).truncated
    assert not HorizonOptions(horizon_days=1, full_days=None).needs_solution
    assert HorizonOptions(
        horizon_days=7, full_days=7, end_targets="plexos"
    ).needs_solution
    with pytest.raises(ValueError, match="horizon_end_targets"):
        HorizonOptions.from_options(
            {"horizon_end_targets": "x"}, horizon_days=1, full_days=7
        )
    with pytest.raises(ValueError, match="horizon_boundary_cut"):
        HorizonOptions.from_options(
            {"horizon_boundary_cut": "x"}, horizon_days=1, full_days=7
        )


def test_resolve_end_targets_and_boundary_cut() -> None:
    cut = HorizonOptions(horizon_days=1, full_days=7)
    full = HorizonOptions(horizon_days=7, full_days=7)
    assert resolve_end_targets(cut, True) == "plexos"
    assert resolve_end_targets(cut, False) == "eini"
    assert resolve_end_targets(full, True) == "input"
    free = HorizonOptions(horizon_days=1, full_days=7, end_targets="free")
    assert resolve_end_targets(free, True) == "free"
    # auto: drop on a truncated pinned horizon, keep with free targets
    # (then the only terminal valuation) and on a full horizon.
    assert resolve_boundary_cut(cut, "plexos") == "drop"
    assert resolve_boundary_cut(cut, "eini") == "drop"
    assert resolve_boundary_cut(cut, "free") == "keep"
    assert resolve_boundary_cut(full, "input") == "keep"
    forced = HorizonOptions(horizon_days=1, full_days=7, boundary_cut="keep")
    assert resolve_boundary_cut(forced, "plexos") == "keep"


def test_cli_flags() -> None:
    args = make_parser().parse_args(
        [
            "x",
            "--horizon-end-targets",
            "eini",
            "--horizon-boundary-cut",
            "keep",
            "--horizon-constraint-rules",
            "period_rhs=drop",
            "--horizon-pin-lng-tanks",
        ]
    )
    assert args.horizon_end_targets == "eini"
    assert args.horizon_boundary_cut == "keep"
    assert args.horizon_constraint_rules == "period_rhs=drop"
    assert args.horizon_pin_lng_tanks is True
    defaults = make_parser().parse_args(["x"])
    assert defaults.horizon_end_targets == "auto"
    assert defaults.horizon_boundary_cut == "auto"


# ── end targets ──────────────────────────────────────────────────────


def _system() -> dict[str, Any]:
    return {
        "generator_array": [{"name": "TG_GNL", "heat_rate": 8.0}, {"name": "H"}],
        "turbine_array": [
            {"generator": "TG_GNL", "main_reservoir": "TANK"},
            {"generator": "H", "main_reservoir": "RES"},
        ],
        "reservoir_array": [
            {"name": "RES", "eini": 50.0, "emin": 5.0, "emax": 100.0, "efin": 20.0},
            {"name": "FULL", "eini": 99.0, "emax": [[90.0, 100.0]], "description": ""},
            {"name": "TANK", "eini": 7.0, "efin": 3.0},
            {"name": "X_GNL_INF", "eini": 1.0},
        ],
        "lng_terminal_array": [{"name": "LNGT", "eini": 9.0, "efin": 2.0}],
        "battery_array": [
            {"name": "B", "eini": 10.0, "emax": 40.0, "efin": 10.0},
            {"name": "E", "eini": 0.0, "emax": 40.0},
        ],
    }


def test_lng_tank_names() -> None:
    assert lng_tank_names(_system()) == {"TANK", "X_GNL_INF", "LNGT"}


def _targets(report: HorizonReport) -> dict[str, dict[str, Any]]:
    return {r["element"]: r for r in report.targets}


def test_end_targets_plexos_with_clamp_and_fallback() -> None:
    system = _system()
    report = HorizonReport(horizon_days=1, full_days=7, truncated=True)
    states = {
        ("Storage", "RES"): 140.0,  # above emax: clamped to 100
        ("Storage", "FULL"): 95.0,  # above the LAST block emax (100)? no
        ("Battery", "B"): 30.0,
        ("Storage", "TANK"): 4.0,
    }
    apply_end_targets(system, "plexos", states, report, day=1)
    res = {r["name"]: r for r in system["reservoir_array"]}
    assert res["RES"]["efin"] == pytest.approx(100.0)
    assert res["FULL"]["efin"] == pytest.approx(95.0)
    assert "--horizon-days 1" in res["FULL"]["description"]
    t = _targets(report)
    assert t["RES"]["clamped"] is True and t["RES"]["source"] == "plexos"
    # LNG tanks stay unpinned by default.
    assert res["TANK"]["efin"] == 3.0 and t["TANK"]["source"] == "lng-unpinned"
    assert system["lng_terminal_array"][0]["efin"] == 2.0
    bats = {b["name"]: b for b in system["battery_array"]}
    assert bats["B"]["efin"] == pytest.approx(30.0)
    assert bats["B"]["efin_cost"] == pytest.approx(_BATTERY_EFIN_COST)
    # No PLEXOS state: eini fallback, warned.
    assert bats["E"]["efin"] == 0.0 and "efin_cost" not in bats["E"]
    assert t["E"]["source"] == "eini-fallback"
    assert any("E: no PLEXOS" in w for w in report.warnings)
    for b in bats.values():
        assert b["daily_cycle"] is False and b["use_state_variable"] is True


def test_end_targets_pin_lng_tanks() -> None:
    system = _system()
    report = HorizonReport(horizon_days=1, full_days=7, truncated=True)
    apply_end_targets(
        system, "plexos", {("Storage", "TANK"): 4.0}, report, day=1, pin_lng_tanks=True
    )
    res = {r["name"]: r for r in system["reservoir_array"]}
    assert res["TANK"]["efin"] == pytest.approx(4.0)
    assert res["X_GNL_INF"]["efin"] == pytest.approx(1.0)  # eini fallback
    assert system["lng_terminal_array"][0]["efin"] == pytest.approx(9.0)


def test_end_targets_eini_and_free_and_input() -> None:
    system = _system()
    report = HorizonReport(horizon_days=1, full_days=7, truncated=True)
    apply_end_targets(system, "eini", {}, report, day=1)
    res = {r["name"]: r for r in system["reservoir_array"]}
    assert res["RES"]["efin"] == pytest.approx(50.0)
    assert res["FULL"]["efin"] == pytest.approx(99.0)  # emax last block 100

    system = _system()
    system["reservoir_array"][0]["efin_cost"] = 5.0
    apply_end_targets(system, "free", {}, report, day=1)
    res = {r["name"]: r for r in system["reservoir_array"]}
    assert "efin" not in res["RES"] and "efin_cost" not in res["RES"]
    bats = {b["name"]: b for b in system["battery_array"]}
    assert "efin" not in bats["B"] and bats["B"]["daily_cycle"] is False

    system = _system()
    before = json.dumps(system, sort_keys=True)
    apply_end_targets(system, "input", {}, report, day=1)
    assert json.dumps(system, sort_keys=True) == before


def test_last_value_helper() -> None:
    assert _horizon._last_value([]) is None
    assert _horizon._last_value([[1.0, 2.0]]) == 2.0
    assert _horizon._last_value("x") is None


# ── multi-day constraint rules ───────────────────────────────────────


def _uc_system() -> dict[str, Any]:
    return {
        "fuel_array": [
            {"name": "G", "max_offtake": 100.0, "max_offtake_cost": 1.0},
            {"name": "T", "min_offtake": 50.0, "min_offtake_cost": 1000.0},
            {"name": "Z", "max_offtake": 0.0},
            {"name": "N"},
        ],
        "user_constraint_array": [
            {"name": "Week", "scope": "stage"},
            {"name": "Gas_MaxOpDay_X", "directive": {"kind": "daily_budget"}},
            {"name": "Daily", "daily_sum": True, "rhs": [1.0, 2.0]},
            {"name": "Plain"},
        ],
    }


def test_constraint_rules_defaults_prorate_and_keep() -> None:
    system = _uc_system()
    opts = HorizonOptions(horizon_days=1, full_days=7)
    report = HorizonReport(horizon_days=1, full_days=7, truncated=True)
    apply_constraint_rules(system, opts, report)
    assert system["fuel_array"][0]["max_offtake"] == 100.0
    assert system["fuel_array"][1]["min_offtake"] == 50.0
    fams = {(r["family"], r["element"]) for r in report.constraints}
    assert ("fuel_max_offtake", "G") in fams
    assert ("fuel_max_offtake", "Z") not in fams  # zero cap, untouched
    assert ("period_rhs", "Week") in fams
    assert ("day_indexed", "Gas_MaxOpDay_X") in fams
    assert ("day_indexed", "Daily") in fams
    assert all(uc.get("active") is not False for uc in system["user_constraint_array"])


def test_constraint_rules_full_and_drop() -> None:
    system = _uc_system()
    opts = HorizonOptions(
        horizon_days=2,
        full_days=7,
        rules=parse_constraint_rules(
            "fuel_max_offtake=full,fuel_min_offtake=drop,period_rhs=drop,"
            "day_indexed=drop"
        ),
    )
    report = HorizonReport(horizon_days=2, full_days=7, truncated=True)
    apply_constraint_rules(system, opts, report)
    fuels = {f["name"]: f for f in system["fuel_array"]}
    assert fuels["G"]["max_offtake"] == pytest.approx(350.0)
    assert "min_offtake" not in fuels["T"] and "min_offtake_cost" not in fuels["T"]
    ucs = {u["name"]: u for u in system["user_constraint_array"]}
    assert ucs["Week"]["active"] is False
    assert ucs["Gas_MaxOpDay_X"]["active"] is False
    assert "active" not in ucs["Plain"]


def test_constraint_rules_noop_on_full_horizon() -> None:
    system = _uc_system()
    opts = HorizonOptions(
        horizon_days=7, full_days=7, rules=parse_constraint_rules("period_rhs=drop")
    )
    report = HorizonReport(horizon_days=7, full_days=7, truncated=False)
    apply_constraint_rules(system, opts, report)
    assert not report.constraints


def test_min_offtake_week_prorated_not_floored() -> None:
    """A weekly take-or-pay over a 1-day horizon is 1/7 of the week."""
    from plexos2gtopt.parsers import (  # pylint: disable=import-outside-toplevel
        _extract_fuel_min_offtake_horizon,
    )

    class _Db:
        @staticmethod
        def static_property(_cls: str, _oid: int, name: str) -> float:
            return {"Min Offtake Week": 700.0, "Min Offtake Penalty": 5.0}.get(
                name, 0.0
            )

    total, cost = _extract_fuel_min_offtake_horizon(_Db(), 1, "F", 24.0)  # type: ignore[arg-type]
    assert total == pytest.approx(100.0)
    assert cost == pytest.approx(5.0)


# ── apply_horizon ────────────────────────────────────────────────────


def test_apply_horizon_with_solution_drops_cut() -> None:
    planning: dict[str, Any] = {
        "system": {
            "reservoir_array": [{"name": "RES1", "eini": 500.0, "emax": 1000.0}],
            "battery_array": [{"name": "BAT1", "eini": 100.0, "emax": 200.0}],
            "future_cost_array": [{"name": "fcf"}],
        }
    }
    sol = PlexosSolution(csv_table_source(fx.solution_tables()))
    report = apply_horizon(planning, HorizonOptions(horizon_days=1, full_days=7), sol)
    sys_ = planning["system"]
    assert sys_["reservoir_array"][0]["efin"] == pytest.approx(fx.RES1_END[1])
    assert sys_["battery_array"][0]["efin"] == pytest.approx(fx.BAT1_END[1])
    assert "future_cost_array" not in sys_
    assert report.boundary_cut == "drop" and report.end_targets == "plexos"
    assert report.as_dict()["truncated"] is True


def test_apply_horizon_plexos_without_solution_warns() -> None:
    planning: dict[str, Any] = {
        "system": {"reservoir_array": [{"name": "R", "eini": 3.0}]}
    }
    opts = HorizonOptions(horizon_days=1, full_days=7, end_targets="plexos")
    report = apply_horizon(planning, opts, None)
    assert planning["system"]["reservoir_array"][0]["efin"] == pytest.approx(3.0)
    assert any("without a PLEXOS solution" in w for w in report.warnings)


def test_apply_horizon_full_horizon_is_noop() -> None:
    planning: dict[str, Any] = {
        "system": {"reservoir_array": [{"name": "R", "eini": 3.0, "efin": 1.0}]}
    }
    report = apply_horizon(planning, HorizonOptions(horizon_days=7, full_days=7), None)
    assert planning["system"]["reservoir_array"][0]["efin"] == 1.0
    assert report.end_targets == "input" and report.boundary_cut == "keep"


# ── solution reader ──────────────────────────────────────────────────


def test_solution_reader_series() -> None:
    sol = PlexosSolution(csv_table_source(fx.solution_tables(n_days=2)))
    assert sol.n_intervals == 48
    data = sol.fetch([("Storage", "End Volume"), ("Battery", "Energy")])
    series = {k.object_name: v for k, v in data.items()}
    assert sol.end_of_day(series["RES1"], 1) == pytest.approx(fx.RES1_END[1])
    assert sol.end_of_day(series["BAT1"], 1) == pytest.approx(fx.BAT1_END[1])
    assert sol.end_of_day(series["RES1"], 3) is None
    sen = sol.fetch([("Decision Variable", "Value")])
    (gen_sen,) = sen.values()
    assert sol.hourly_sum(gen_sen, 24) == pytest.approx(24 * 0.105)
    assert sol.hourly_sum(gen_sen, 100) == pytest.approx(48 * 0.105)
    assert not sol.fetch([("Line", "Flow")])


def test_solution_reader_skips_foreign_keys() -> None:
    tables = fx.solution_tables(n_days=1)
    tables["t_key"] += "4,100,4,10,1,0\n5,100,3,10,1,1\n6,100,3,10,2,0\n"
    tables["t_data_0"] += "4,1,9\n5,1,9\n6,1,9\n1,x,9\n"
    sol = PlexosSolution(csv_table_source(tables))
    (bat,) = sol.fetch([("Battery", "Energy")]).values()
    assert 9.0 not in bat.values()


def test_mdb_table_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "mdb-export"
    script.write_text(
        '#!/bin/sh\nif [ "$2" = bad ]; then exit 3; fi\nprintf "a,b\\n1,2\\n"\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    read = mdb_table_source(tmp_path / "x.accdb")
    assert list(read("t")) == [{"a": "1", "b": "2"}]
    with pytest.raises(OSError, match="failed"):
        list(read("bad"))
    assert PlexosSolution.from_accdb(tmp_path / "x.accdb") is not None
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    assert PlexosSolution.from_accdb(tmp_path / "x.accdb") is None


def test_open_solution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert open_solution() is None
    assert open_solution(accdb=tmp_path / "missing.accdb") is None
    acc = tmp_path / "s.accdb"
    acc.write_bytes(b"")
    monkeypatch.setattr(
        PlexosSolution, "from_accdb", classmethod(lambda cls, p: cls(lambda t: []))
    )
    assert open_solution(accdb=acc) is not None
    import plexos2gtopt.plexos_block_layout as pbl  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(pbl, "auto_discover_res_zip", lambda p: tmp_path / "RES.zip")
    monkeypatch.setattr(pbl, "extract_accdb_from_res_zip", lambda p: acc)
    assert open_solution(input_bundle=tmp_path) is not None


# ── writer: battery cycle anchor without a block layout ──────────────


def test_battery_cycle_anchor_without_block_layout() -> None:
    """Hourly mode (no layout) emits the same anchor flags as plexos mode."""
    bat = BatterySpec(
        object_id=1,
        name="B",
        bus_name="N",
        emax=50.0,
        eini=10.0,
        efin=10.0,
        max_cycles_day=1.0,
    )
    hourly = build_battery_array((bat,), ())[0]
    plexos = build_battery_array((bat,), tuple((h,) for h in range(1, 25)))[0]
    for entry in (hourly, plexos):
        assert entry["daily_cycle"] is False
        assert entry["use_state_variable"] is True
        assert entry["efin_cost"] == pytest.approx(_BATTERY_EFIN_COST)


# ── end to end on the mini bundle ────────────────────────────────────


def _convert(
    tmp_path: Path, days: int, tag: str, **opts: Any
) -> tuple[dict[str, Any], Path]:
    bundle = tmp_path / "bundle"
    if not bundle.exists():
        fx.write_bundle(bundle)
    out = tmp_path / tag
    options: dict[str, Any] = {
        "input_bundle": bundle,
        "output_dir": out,
        "output_file": out / "mini.json",
        "horizon_mode": "hourly",
        "horizon_days": days,
        "lp_relax": True,
        "run_check": False,
    }
    options.update(opts)
    assert convert_plexos_bundle(options) == 0
    return json.loads((out / "mini.json").read_text()), out


@pytest.fixture(name="fake_solution")
def _fake_solution(monkeypatch: pytest.MonkeyPatch) -> None:
    import plexos2gtopt.plexos2gtopt as conv  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(
        conv,
        "open_solution",
        lambda **_kw: PlexosSolution(csv_table_source(fx.solution_tables())),
    )


def test_e2e_hourly_and_plexos_modes_same_battery_flags(tmp_path: Path) -> None:
    hourly, _ = _convert(tmp_path, 7, "hourly")
    plexos, _ = _convert(
        tmp_path,
        7,
        "plexos",
        horizon_mode="plexos",
        block_layout=",".join(["1"] * 168),
    )
    for name in ("BAT1", "BAT_VR2"):
        h = next(b for b in hourly["system"]["battery_array"] if b["name"] == name)
        p = next(b for b in plexos["system"]["battery_array"] if b["name"] == name)
        for key in ("daily_cycle", "use_state_variable", "efin", "efin_cost"):
            assert h.get(key) == p.get(key), (name, key)
        assert h["daily_cycle"] is False


def test_e2e_full_week_keeps_cut(tmp_path: Path) -> None:
    planning, out = _convert(tmp_path, 7, "week")
    assert (out / "boundary_cuts.csv").exists()
    assert planning["system"]["future_cost_array"]
    prov = json.loads((out / "mini.provenance.json").read_text())
    assert prov["horizon"]["truncated"] is False
    assert "reserve_load_factor" not in prov


def test_e2e_day1_plexos_targets(tmp_path: Path, fake_solution: None) -> None:
    del fake_solution
    planning, out = _convert(tmp_path, 1, "d1", reserve_load_factor="auto")
    sys_ = planning["system"]
    res = {r["name"]: r for r in sys_["reservoir_array"]}
    bats = {b["name"]: b for b in sys_["battery_array"]}
    assert res["RES1"]["efin"] == pytest.approx(fx.RES1_END[1])
    assert bats["BAT1"]["efin"] == pytest.approx(fx.BAT1_END[1])
    # BAT_VR2 has no PLEXOS state: efin = eini, soft.
    assert bats["BAT_VR2"]["efin"] == pytest.approx(bats["BAT_VR2"]["eini"])
    assert bats["BAT_VR2"]["efin_cost"] == pytest.approx(_BATTERY_EFIN_COST)
    assert "future_cost_array" not in sys_
    assert not (out / "boundary_cuts.csv").exists()
    # Weekly fuel cap prorated to one day (700 TJ/week -> 100 TJ).
    assert sys_["fuel_array"][0]["max_offtake"] == pytest.approx(100_000.0)
    prov = json.loads((out / "mini.provenance.json").read_text())
    assert prov["horizon"]["end_targets"] == "plexos"
    assert prov["horizon"]["boundary_cut"] == "drop"
    assert prov["reserve_load_factor"]["source"] == "plexos:Generation_SEN"
    assert prov["reserve_load_factor"]["factor"] == pytest.approx(1.05)


def test_e2e_day1_without_solution_uses_eini(tmp_path: Path) -> None:
    planning, out = _convert(
        tmp_path, 1, "d1e", horizon_constraint_rules="fuel_max_offtake=drop"
    )
    res = planning["system"]["reservoir_array"][0]
    assert res["efin"] == pytest.approx(res["eini"])
    assert "max_offtake" not in planning["system"]["fuel_array"][0]
    prov = json.loads((out / "mini.provenance.json").read_text())
    assert prov["horizon"]["end_targets"] == "eini"
    assert prov["horizon"]["warnings"]


def test_e2e_day1_free_keeps_cut(tmp_path: Path) -> None:
    planning, out = _convert(tmp_path, 1, "free", horizon_end_targets="free")
    assert "efin" not in planning["system"]["reservoir_array"][0]
    assert (out / "boundary_cuts.csv").exists()


# ── solves (CLP, LP relaxation) ──────────────────────────────────────


def _gtopt_binary() -> Path | None:
    env = os.environ.get("GTOPT_BIN")
    if env and Path(env).is_file() and os.access(env, os.X_OK):
        return Path(env)
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / "build" / "standalone" / "gtopt"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    on_path = shutil.which("gtopt")
    return Path(on_path) if on_path else None


_NEEDS_GTOPT = pytest.mark.skipif(
    _gtopt_binary() is None,
    reason="gtopt binary not available (set GTOPT_BIN or build standalone)",
)


def _solve(out: Path, json_name: str = "mini.json") -> subprocess.CompletedProcess[str]:
    binary = _gtopt_binary()
    assert binary is not None
    return subprocess.run(
        [str(binary), json_name, "-d", "output"],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


@_NEEDS_GTOPT
def test_solve_day1_cut_is_feasible(tmp_path: Path, fake_solution: None) -> None:
    del fake_solution
    _planning, out = _convert(tmp_path, 1, "d1")
    proc = _solve(out)
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-4000:]


#: gtopt's lithium self-discharge default (``battery_annual_standing_loss``,
#: 0.48 p.u./year = 4 %/month).
_GTOPT_LITHIUM_STANDING_LOSS = 0.48


@_NEEDS_GTOPT
def test_solve_reproduces_hard_daily_cycle_infeasibility(tmp_path: Path) -> None:
    """PCP 2024-12-15 day 1: without the anchor flags BAT_VR2 (self-
    discharge, no way to recharge) cannot meet gtopt's HARD daily-cycle
    close and the LP is infeasible; with them it solves.

    plexos2gtopt now writes the PLEXOS ``Self Discharge Rate`` (default 0)
    instead of leaving gtopt's 4 %/month lithium default, so the legacy
    variant sets that self-discharge back explicitly."""
    planning, out = _convert(tmp_path, 1, "d1")
    proc = _solve(out)
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-4000:]
    for bat in planning["system"]["battery_array"]:
        for key in ("daily_cycle", "use_state_variable", "efin_cost"):
            bat.pop(key, None)
        bat["annual_standing_loss"] = _GTOPT_LITHIUM_STANDING_LOSS
    (out / "legacy.json").write_text(json.dumps(planning))
    proc = _solve(out, "legacy.json")
    assert proc.returncode != 0
    # Primal infeasible (CLP), not some other failure.
    logs = "".join(p.read_text() for p in (out / "output" / "logs").glob("*.log"))
    assert "infeasible" in logs.lower()
