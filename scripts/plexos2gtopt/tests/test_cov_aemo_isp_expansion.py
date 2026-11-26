"""Branch tests for :mod:`plexos2gtopt.aemo_isp_expansion`.

The monthly dispatch windows are replaced by synthetic ``IspCase``
objects (``build_dispatch_case`` is monkeypatched), so the merge logic
of :func:`build_expansion_case` — lines, flows, decision variables,
skipped user constraints, file-backed fields — is checked without
building twelve real windows.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from plexos2gtopt import aemo_isp_expansion as exp
from plexos2gtopt.aemo_isp import IspCase, IspCaseOptions, IspResolver
from plexos2gtopt.isp_traces import TraceLocator
from plexos2gtopt.plexos_xml import load_xml
from plexos2gtopt.tests.test_aemo_isp_constraints import _CXml

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def test_feasible_cycle_eini_scalar_bounds() -> None:
    assert exp._feasible_cycle_eini("b", 5.0, {"emax": 10.0, "emin": 0.0}) == 5.0
    assert exp._feasible_cycle_eini("b", 5.0, {"emax": [], "emin": []}) == 5.0


def test_crf_zero_life() -> None:
    assert exp.crf(0.07, 0.0) == 1.0


def test_net_load_skips_missing_traces() -> None:
    system = {
        "demand_array": [{"uid": 1}, {"uid": 2}],
        "generator_array": [
            {"uid": 1, "type": "solar"},
            {"uid": 2, "type": "wind"},
            {"uid": 3, "type": "thermal"},
        ],
    }
    ws = {
        ("Demand", "lmax"): {1: np.full(48, 10.0)},
        ("Generator", "pmax"): {1: np.full(48, 4.0)},
    }
    # one value per trace step (hourly by default): 10 - 4 = 6 MW
    net = exp._net_load(system, ws, 2)
    assert net.shape == (48,) and np.allclose(net, 6.0)
    assert np.allclose(exp._net_load(system, {}, 2), 0.0)


def test_lt_plan_discount_rate(tmp_path: Path) -> None:
    xml = tmp_path / "m.xml"
    xml.write_text(
        "<MasterDataSet>"
        "<t_class><class_id>1</class_id><name>LT Plan</name></t_class>"
        "<t_object><object_id>5</object_id><class_id>1</class_id></t_object>"
        "<t_attribute><attribute_id>7</attribute_id><class_id>1</class_id>"
        "<name>Discount Rate</name></t_attribute>"
        "<t_attribute_data><object_id>9</object_id><attribute_id>7</attribute_id>"
        "<value>1</value></t_attribute_data>"
        "<t_attribute_data><object_id>5</object_id><attribute_id>7</attribute_id>"
        "<value>5</value></t_attribute_data>"
        "</MasterDataSet>"
    )
    assert exp.lt_plan_discount_rate(xml) == pytest.approx(0.05)


def test_git_revision_outside_git(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise OSError("no git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert exp._git_revision() is None


def test_write_expansion_case_skips_empty_schedules(tmp_path: Path) -> None:
    case = IspCase(
        planning={"system": {}},
        schedules={
            ("Line", "tmax_ab"): {},
            ("Flow", "discharge"): {1: np.ones((1, 2))},
        },
    )
    path = exp.write_expansion_case(case, tmp_path, "c")
    assert path.exists()
    assert not (tmp_path / "Line").exists()
    assert (tmp_path / "Flow" / "discharge.parquet").exists()


# ---------------------------------------------------------------------------
# candidate discovery
# ---------------------------------------------------------------------------


def _xml(tmp_path: Path) -> Path:
    x = _CXml()
    n1 = x.obj("Node", "N1")
    gas = x.obj("Fuel", "Gas")
    x.data("Fuel", gas, "Price", 4.0)
    g1 = x.cobj("Generator", "G1", "Wind NSW")
    x.member("Generator", "Node", "Nodes", g1, n1)
    x.data("Generator", g1, "Units", 1)
    x.data("Generator", g1, "Max Capacity", 10)

    def gen(name: str, cat: str, *, units: float = 0, node: bool = True) -> int:
        o = x.cobj("Generator", name, cat)
        if node:
            x.member("Generator", "Node", "Nodes", o, n1)
        x.data("Generator", o, "Units", units)
        x.data("Generator", o, "Max Capacity", 10)
        x.data("Generator", o, "Max Units Built", 5)
        return o

    c1 = gen("C1", "New Entrant Gas NSW")
    x.member("Generator", "Fuel", "Fuels", c1, gas)
    x.data("Generator", c1, "Heat Rate", 10)
    x.data("Generator", c1, "Rating", 5)
    x.data("Generator", c1, "Rating Factor", 50)
    gen("LS1", "Load Subtracters NSW")
    gen("EXIST", "New Entrant Gas NSW", units=1)
    gen("NONODE", "New Entrant Gas NSW", node=False)
    gen("OTHER", "Other Category")

    def bat(name: str, cat: str, *, units: float = 0, node: bool = True) -> None:
        o = x.cobj("Battery", name, cat)
        if node:
            x.member("Battery", "Node", "Nodes", o, n1)
        x.data("Battery", o, "Units", units)
        x.data("Battery", o, "Max Power", 5)
        x.data("Battery", o, "Capacity", 10)
        x.data("Battery", o, "Max Units Built", 3)

    bat("BC", "New Entrant Battery NSW")
    bat("BEXIST", "New Entrant Battery NSW", units=1)
    bat("BNONODE", "New Entrant Battery NSW", node=False)
    bat("BOTHER", "Other Category")
    bat("BZERO", "New Entrant Battery NSW")
    st = x.obj("Storage", "St")
    x.data("Storage", st, "End Effects Method", 2)
    path = tmp_path / "m.xml"
    x.write(path)
    return path


def test_candidate_filters(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    res = IspResolver(load_xml(_xml(tmp_path)), TraceLocator([tmp_path]))
    months = exp.month_starts(2028, 1)
    with caplog.at_level(logging.WARNING):
        gens = exp.find_candidates(res, months)
        bats = exp.find_battery_candidates(res, months)
    assert [c.name for c in gens] == ["C1", "OTHER"]
    assert [c.name for c in bats] == ["BC", "BOTHER", "BZERO"]
    assert "candidate NONODE has no node" in caplog.text
    assert "battery candidate BNONODE has no node" in caplog.text
    pre = ("New Entrant",)
    assert [c.name for c in exp.find_candidates(res, months, pre)] == ["C1"]
    names = [c.name for c in exp.find_battery_candidates(res, months, pre)]
    assert names == ["BC", "BZERO"]


# ---------------------------------------------------------------------------
# build_expansion_case with synthetic monthly windows
# ---------------------------------------------------------------------------


def _window(opts: IspCaseOptions, *, minimal: bool) -> IspCase:
    nh = 24 * opts.days
    system: dict[str, Any] = {
        "bus_array": [{"uid": 1, "name": "N1"}],
        "generator_array": [
            {
                "uid": 1,
                "name": "G1",
                "bus": "N1",
                "type": "wind",
                "gcost": 0.0,
                "pmax": "pmax",
                "capacity": 10.0,
                "pmin": "pmin",
            }
        ],
        "demand_array": [
            {"uid": 1, "name": "D1", "bus": "N1", "lmax": "lmax"},
            {"uid": 2, "name": "PUMP", "bus": "N1", "capacity": 5.0},
        ],
        "line_array": [
            {
                "uid": 1,
                "name": "L1",
                "bus_a": "N1",
                "bus_b": "N1",
                "tmax_ab": "tmax_ab",
                "tmax_ba": "tmax_ba",
            }
        ],
    }
    ws: dict[tuple[str, str], dict[int, np.ndarray]] = {
        ("Demand", "lmax"): {1: np.full(nh, 8.0)},
        ("Generator", "pmax"): {1: np.full(nh, 6.0)},
        ("Line", "tmax_ab"): {1: np.full(nh, 100.0)},
        ("Line", "tmax_ba"): {1: np.full(nh, 90.0)},
    }
    if not minimal:
        first = opts.start.month == 7
        ucs = [
            {"uid": 1, "name": "prof", "expression": "[1, 2] * x <= 3"},
            {"uid": 2, "name": "bad", "expression": "x"},
            {"uid": 4, "name": "never", "expression": "x <= 2000000000"},
            {"uid": 5, "name": "varying", "expression": "x <= 1"},
        ]
        if first:
            ucs.append({"uid": 3, "name": "eq", "expression": "x = 1"})
        system.update(
            {
                "battery_array": [
                    {
                        "uid": 1,
                        "name": "BC",
                        "bus": "N1",
                        "emin": 0.0,
                        "emax": 10.0,
                        "pmax_charge": 5.0,
                        "pmax_discharge": 5.0,
                        "eini": 5.0,
                    }
                ],
                "flow_array": [{"uid": 1, "name": "F1"}, {"uid": 2, "name": "F2"}],
                "reservoir_array": [
                    {"uid": 1, "name": "St", "efin": 1.0},
                    {"uid": 2, "name": "Unknown"},
                ],
                "decision_variable_array": [{"uid": 1, "name": "dv1"}],
                "user_constraint_array": ucs,
            }
        )
        if not first:
            # commissioned after the first month: no initial level
            late = dict(system["battery_array"][0], uid=2, name="BEXIST")
            system["battery_array"].append(late)
        ws[("Flow", "discharge")] = {1: np.full(nh, 3.0)}
        ws[("UserConstraint", "rhs")] = {5: np.arange(nh, dtype=float)}
    return IspCase(
        planning={
            "system": system,
            "options": {"model_options": {"demand_fail_cost": 500.0}},
        },
        schedules=ws,
        stats={"constraint_groups": {"g": {"r": 1}}},
    )


def _expand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, minimal: bool, **kw: Any
) -> IspCase:
    monkeypatch.setattr(
        exp,
        "build_dispatch_case",
        lambda wopts, db=None: _window(wopts, minimal=minimal),
    )
    opts = exp.ExpansionOptions(
        xml=_xml(tmp_path), trace_roots=[], first_fy=2028, years=1, **kw
    )
    return exp.build_expansion_case(opts)


def test_expansion_merges_synthetic_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _expand(tmp_path, monkeypatch, minimal=False)
    sysm = case.planning["system"]
    st = case.stats
    assert st["dropped_file_fields"] == 1
    assert "pmin" not in sysm["generator_array"][0]
    assert st["uc_skipped_profile_coefficient"] == 12
    assert st["uc_skipped_unparsed"] == 12
    assert st["uc_skipped_equality_partial"] == 1
    assert st["uc_skipped_never_active"] == 1
    ucs = {uc["name"]: uc for uc in sysm["user_constraint_array"]}
    assert set(ucs) == {"varying"}
    assert ucs["varying"]["rhs"] == "rhs"
    assert [ln["name"] for ln in sysm["line_array"]] == ["L1"]
    assert case.schedules[("Line", "tmax_ba")][1][0, 0] == pytest.approx(90.0)
    # the trace-less pump demand keeps its capacity as a flat lmax
    assert case.schedules[("Demand", "lmax")][2][0, 0] == pytest.approx(5.0)
    assert [f["name"] for f in sysm["flow_array"]] == ["F1"]
    assert [dv["name"] for dv in sysm["decision_variable_array"]] == ["dv1"]
    res = {r["name"]: r for r in sysm["reservoir_array"]}
    assert res["St"]["daily_cycle"] and "efin" not in res["St"]
    assert "daily_cycle" not in res["Unknown"]
    # BC is both an existing battery and a candidate: the existing one wins
    bats = {b["name"]: b for b in sysm["battery_array"]}
    assert [b["name"] for b in sysm["battery_array"]].count("BC") == 1
    assert bats["BC"]["eini"] == 5.0 and "eini" not in bats["BEXIST"]
    gens = {g["name"]: g for g in sysm["generator_array"]}
    # candidate gas: VOM 0 + HR 10 × 4 $/GJ; rating 5 × 50 % of 10 MW units
    assert gens["C1"]["gcost"] == pytest.approx(40.0)
    prof = case.schedules[("GeneratorProfile", "profile")][1]
    assert prof[0, 0] == pytest.approx(0.25)


def test_expansion_minimal_windows_without_batteries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _expand(
        tmp_path, monkeypatch, minimal=True, include_batteries=False, discount_rate=0.05
    )
    sysm = case.planning["system"]
    assert "battery_array" not in sysm
    assert "user_constraint_array" not in sysm
    assert "decision_variable_array" not in sysm
    assert case.stats["discount_rate"] == 0.05
    assert all(not c.startswith("B") for c in case.stats["candidate_categories"])


def test_expansion_months_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[date] = []

    def fake(wopts: IspCaseOptions, db: object = None) -> IspCase:
        seen.append(wopts.start)
        return _window(wopts, minimal=True)

    monkeypatch.setattr(exp, "build_dispatch_case", fake)
    exp.build_expansion_case(
        exp.ExpansionOptions(
            xml=_xml(tmp_path),
            trace_roots=[],
            first_fy=2028,
            years=1,
            battery_candidates=False,
        )
    )
    assert seen[0] == date(2027, 7, 1) and len(seen) == 12
