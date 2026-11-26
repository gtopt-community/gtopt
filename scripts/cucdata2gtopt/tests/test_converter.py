# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Unit tests of the CUC-data reader, GAMS pre-processing and converter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cucdata2gtopt import (
    CaseOptions,
    build_case,
    case_name,
    default_reserve_pct,
    derive_unit,
    published_scale,
    read_workbook,
    unit_constraints,
    write_case,
)
from cucdata2gtopt._converter import build_units
from cucdata2gtopt._model import UnitParams
from cucdata2gtopt._reader import CucData, RawUnit, _find_cell, _key_col

from ._synthetic import DEMAND, PERIODS, make_workbook


def _raw(**over: float) -> RawUnit:
    values = {
        "Bus": 1,
        "MaxProd": 100,
        "MinProd": 20,
        "IniProd": 0,
        "IniState": -2,
        "SUcap": 20,
        "SDcap": 20,
        "RampUp": 50,
        "RampDw": 50,
        "FuelCost": 2,
        "SlopeVarCost": 10,
        "InterVarCost": 50,
        "OMVarCost": 1,
        "MinTU": 3,
        "MinTD": 2,
        "SDduration": 1,
        "ShutdownCost": 4,
        "SUduration1": 1,
        "DownTtimeforSU1": 2,
        "SUcost1": 70,
    }
    values.update(over)
    return RawUnit("g", {k: float(v) for k, v in values.items()})


def test_read_workbook(workbook: Path) -> None:
    data = read_workbook(workbook)
    assert data.periods == PERIODS
    assert data.demand == DEMAND
    assert data.reserve_up == pytest.approx([d * 0.1 for d in DEMAND])
    assert data.reserve_dn == data.reserve_up
    assert data.buses == [1, 2, 3]
    assert data.bus_factor == {1: 0.5, 2: 0.0, 3: 0.5}
    assert data.params["pENSCost"] == 5000
    assert data.params["pWindCurtCost"] == 100
    assert data.scenario_prob == {"sc01": 1.0, "sc02": 0.0}
    assert data.active_scenarios() == ["sc01"]
    assert data.intermittent["sc01"][2] == [10.0, 20.0, 30.0, 40.0]
    assert data.intermittent["sc02"][2] == [0.0] * 4
    assert [u.name for u in data.units] == ["gen1", "gen2", "gen3", "gen4"]
    assert [c.name for c in data.clusters] == ["genC01", "genC02"]
    assert data.unit_cluster == {
        "gen1": "genC01",
        "gen2": "genC01",
        "gen3": "genC02",
        "gen4": "genC02",
    }
    assert data.units_per_cluster() == {"genC01": 2, "genC02": 2}
    assert len(data.lines) == 4
    assert data.lines[2].circuit == 2
    assert not data.lines[3].in_service
    assert data.lines[0].x == pytest.approx(0.1)
    assert data.units[0]["Missing"] == 0.0


def test_reader_errors(tmp_path: Path) -> None:
    import openpyxl  # noqa: PLC0415

    with pytest.raises(ValueError, match="not found"):
        _find_cell([[None, "a"]], "b")
    with pytest.raises(ValueError, match="period label"):
        _key_col([["Energy"], [None]], 0, 0)
    wb = openpyxl.Workbook()
    wb.active.title = "Other"
    path = tmp_path / "bad.xlsx"
    wb.save(path)
    with pytest.raises(ValueError, match="sheet 'Indices&Param' missing"):
        read_workbook(path)


def test_reader_rejects_bad_tables(tmp_path: Path) -> None:
    import openpyxl  # noqa: PLC0415

    path = make_workbook(tmp_path / "w.xlsx")
    wb = openpyxl.load_workbook(path)
    wb["Generation"].cell(row=8, column=6).value = "Renamed"
    wb.save(path)
    with pytest.raises(ValueError, match="lacks columns"):
        read_workbook(path)

    path = make_workbook(tmp_path / "w2.xlsx")
    wb = openpyxl.load_workbook(path)
    wb["DemandReserveIG"].cell(row=9, column=8).value = "h99"
    wb.save(path)
    with pytest.raises(ValueError, match="different periods"):
        read_workbook(path)


def test_reader_without_intermittent(tmp_path: Path) -> None:
    import openpyxl  # noqa: PLC0415

    path = make_workbook(tmp_path / "w.xlsx", ig=False)
    wb = openpyxl.load_workbook(path)
    ws = wb["DemandReserveIG"]
    ws.cell(row=5, column=22).value = None
    ws.cell(row=5, column=23).value = None
    wb.save(path)
    assert not read_workbook(path).intermittent


def test_derive_unit_gams_formulas() -> None:
    u = derive_unit(_raw())
    var = 1 + 10 * 2
    fix = 50 * 2
    assert u.var_cost == pytest.approx(var)
    assert u.fix_cost == pytest.approx(fix)
    assert u.startup_cost == pytest.approx(70 * 2 + fix + 20 * var / 2)
    assert u.shutdown_cost == pytest.approx(4 * 2 + fix + 20 * var / 2)
    assert u.ramp_up == 50 and u.ramp_dn == 50
    assert u.reserve_max == 80
    assert u.reserve_up_cost == pytest.approx(0.4 * fix * 1e-3)
    assert u.reserve_dn_cost == pytest.approx(0.2 * fix * 1e-3)
    assert u.ini_uc == 0
    assert u.ini_up == 0.0
    assert u.ini_dn == 0.0
    assert u.pinned_periods == 0
    assert not u.is_t1
    assert u.min_up_periods == 3 and u.min_dn_periods == 2
    assert u.capacity_rows() == [(80.0, 80.0)]


def test_derive_unit_clamps_and_initial_state() -> None:
    u = derive_unit(
        _raw(RampUp=500, SUcap=5, SDcap=500, MinTU=0, MinTD=0, IniProd=60, IniState=0)
    )
    assert u.ramp_up == 80  # pmax - pmin
    assert u.su_cap == 20 and u.sd_cap == 100
    assert u.min_up == 1 and u.min_dn == 1 and u.is_t1
    assert u.ini_uc == 1
    assert u.ini_up == 1.0  # TU 1 - IniState 0
    assert u.pinned_periods == 1
    assert u.capacity_rows() == [(80.0, 0.0), (80.0, 0.0)]
    off = derive_unit(_raw(IniProd=0, IniState=-1, MinTD=4))
    assert off.ini_dn == 3.0 and off.pinned_periods == 3
    frac = derive_unit(_raw(MinTU=2.75, MinTD=1.5))
    assert frac.min_up_periods == 2 and frac.min_dn_periods == 1
    t1 = derive_unit(_raw(MinTU=1, SUcap=30, SDcap=50))
    assert t1.capacity_rows() == [(70.0, 0.0), (20.0, 50.0)]


def test_unit_constraints_text() -> None:
    u = derive_unit(_raw(IniProd=60, IniState=5))
    rows = dict(unit_constraints(u, 4))
    assert set(rows) == {
        "g_maxout1",
        "g_maxout1_last",
        "g_minout",
        "g_rampup",
        "g_rampup_first",
        "g_rampdn",
        "g_rampdn_first",
    }
    assert rows["g_maxout1"] == (
        "generator('g').generation_prev + reserve_provision('g_res').up_prev"
        " - 100 * commitment('g_uc').status_prev"
        " + 80 * commitment('g_uc').startup_prev"
        " + 80 * commitment('g_uc').shutdown <= 0, for(block in 2..4)"
    )
    assert rows["g_maxout1_last"].endswith("<= 0, for(block = 4)")
    assert rows["g_minout"] == (
        "generator('g').generation - reserve_provision('g_res').dn"
        " - 20 * commitment('g_uc').status >= 0"
    )
    assert rows["g_rampup"].startswith("generator('g').generation - generator")
    assert "- 70 * commitment('g_uc').status" in rows["g_rampup"]
    assert rows["g_rampup_first"].endswith("<= 40, for(block = 1)")
    assert "+ 70 * commitment('g_uc').status_prev" in rows["g_rampdn"]
    assert rows["g_rampdn_first"].endswith(">= -10, for(block = 1)")


def test_unit_constraints_t1_rows() -> None:
    # t1 with SU == SD: eMaxOut2 has no z term (one unlagged row),
    # eMaxOut3 has no y term (lagged row, no last-block row).
    u = derive_unit(_raw(MinTU=1))
    rows = dict(unit_constraints(u, 3))
    assert rows["g_maxout1"] == (
        "generator('g').generation + reserve_provision('g_res').up"
        " - 100 * commitment('g_uc').status"
        " + 80 * commitment('g_uc').startup <= 0"
    )
    assert "g_maxout2" in rows and "g_maxout2_last" not in rows
    # SU == SD == Pmax and t1: no capacity row at all.
    full = derive_unit(_raw(MinTU=1, SUcap=100, SDcap=100))
    names = [n for n, _ in unit_constraints(full, 3)]
    assert not any(n.startswith("g_maxout") for n in names)


def _data(workbook: Path) -> CucData:
    return read_workbook(workbook)


def test_build_case_default(workbook: Path) -> None:
    data = _data(workbook)
    assert published_scale(data) == 2
    assert default_reserve_pct(data) == pytest.approx(10.0)
    case = build_case(data, CaseOptions(system=39))
    system = case["system"]
    assert system["name"] == "cuc39_r10_s2"
    assert [b["name"] for b in system["bus_array"]] == ["b1", "b2", "b3"]
    assert system["bus_array"][0]["reference_theta"] == 0.0
    assert len(system["line_array"]) == 3  # out-of-service circuit dropped
    assert system["line_array"][0]["tmax_ab"] == 150
    assert [d["name"] for d in system["demand_array"]] == ["d1", "d3"]
    assert system["demand_array"][0]["lmax"] == [[d * 0.5 for d in DEMAND]]
    assert system["demand_array"][0]["fcost"] == 5000
    gens = {g["name"]: g for g in system["generator_array"]}
    assert set(gens) == {"gen1", "gen2", "gen3", "gen4", "ig2"}
    assert gens["gen1"]["gcost"] == pytest.approx(11.0)
    assert gens["gen1"]["pmin"] == 0.0
    com = {c["name"]: c for c in system["commitment_array"]}
    assert com["gen1_uc"]["pmin"] == 20
    assert com["gen1_uc"]["initial_status"] == 1
    assert com["gen1_uc"]["initial_power"] == 60
    assert com["gen1_uc"]["min_up_time"] == 3
    assert "fixed_status" not in com["gen1_uc"]
    assert "ramp_up" not in com["gen1_uc"]
    assert com["gen3_uc"]["initial_status"] == 0
    assert com["gen3_uc"]["initial_power"] == 0.0
    assert com["gen3_uc"]["shutdown_cost"] == pytest.approx(5 * 2)
    zone = system["reserve_zone_array"][0]
    assert zone["urreq"] == [[d * 0.1 for d in DEMAND]]
    prov = system["reserve_provision_array"][0]
    assert prov["ur_provision_factor"] == 1.0
    assert prov["urcost"] == pytest.approx(0.4 * 50 * 1e-3)
    prof = system["capacity_profile_array"][0]
    assert prof["owner"] == "ig2"
    assert prof["scost"] == 100
    assert prof["profile"] == [[[0.25, 0.5, 0.75, 1.0]]]
    assert len(system["user_constraint_array"]) > 0
    stage = case["simulation"]["stage_array"][0]
    assert stage["chronological"] is True and stage["count_block"] == 4
    assert case["options"]["model_options"]["demand_fail_cost"] == 5000


def test_build_case_reserve_scale_native(workbook: Path) -> None:
    data = _data(workbook)
    case = build_case(
        data, CaseOptions(system=118, reserve_pct=2.5, scale=4, formulation="native")
    )
    system = case["system"]
    assert system["name"] == "cuc118_r2p5_s4_native"
    names = [g["name"] for g in system["generator_array"]]
    assert names[:5] == ["genC01_1", "genC01_2", "genC01_3", "genC01_4", "genC02_1"]
    assert len(system["commitment_array"]) == 8
    factor = 4 / 2
    assert system["line_array"][0]["tmax_ab"] == 150 * factor
    zone = system["reserve_zone_array"][0]
    assert zone["urreq"][0] == pytest.approx([d * factor * 0.025 for d in DEMAND])
    assert zone["drreq"] == zone["urreq"]
    assert system["demand_array"][0]["lmax"][0][0] == pytest.approx(100 * factor * 0.5)
    com = system["commitment_array"][0]
    assert com["ramp_up"] == 50 and com["startup_ramp"] == 20
    assert "user_constraint_array" not in system
    ig = [g for g in system["generator_array"] if g["name"] == "ig2"][0]
    assert ig["pmax"] == 40 * factor


def test_build_case_power_based(workbook: Path) -> None:
    data = _data(workbook)
    case = build_case(data, CaseOptions(system=39, formulation="power_based"))
    system = case["system"]
    assert system["name"].endswith("_power_based")
    assert case["options"]["model_options"]["uc_formulation"] == "power_based"
    com = system["commitment_array"][0]
    assert "ramp_up" in com and "startup_ramp" in com
    assert all("initial_power" not in c for c in system["commitment_array"])
    assert "user_constraint_array" not in system
    native = build_case(data, CaseOptions(system=39, formulation="native"))
    assert "uc_formulation" not in native["options"]["model_options"]


def test_build_case_pins_initial_commitment(workbook: Path) -> None:
    data = _data(workbook)
    raw = data.clusters[1]
    vals = dict(raw.values, MinTD=3.0, IniState=-1.0)
    data.units[2] = RawUnit("gen3", vals)
    case = build_case(data, CaseOptions(name="pinned"))
    com = {c["name"]: c for c in case["system"]["commitment_array"]}
    assert com["gen3_uc"]["fixed_status"] == [[0.0, 0.0, -1.0, -1.0]]
    assert case["system"]["name"] == "pinned"


def test_build_case_block_mode(workbook: Path) -> None:
    case = build_case(_data(workbook), CaseOptions(mode="block"))
    system = case["system"]
    assert system["name"] == "cuc39_r10_s2_block"
    assert case["simulation"]["stage_array"][0]["chronological"] is False
    assert "user_constraint_array" not in system
    assert len(system["commitment_array"]) == 4


def test_build_case_errors(workbook: Path) -> None:
    data = _data(workbook)
    with pytest.raises(ValueError, match="formulation"):
        build_case(data, CaseOptions(formulation="bad"))
    with pytest.raises(ValueError, match="mode"):
        build_case(data, CaseOptions(mode="bad"))
    with pytest.raises(ValueError, match="scale"):
        build_case(data, CaseOptions(scale=0))
    data.scenario_prob["sc02"] = 1.0
    with pytest.raises(ValueError, match="one active scenario"):
        build_case(data, CaseOptions())
    data.scenario_prob["sc02"] = 0.0
    data.unit_cluster.pop("gen4")
    with pytest.raises(ValueError, match="different unit counts"):
        published_scale(data)


def test_zero_intermittent_and_bus_skipped(workbook: Path) -> None:
    data = _data(workbook)
    data.intermittent["sc01"][2] = [0.0] * 4
    system = build_case(data, CaseOptions())["system"]
    assert "capacity_profile_array" not in system


def test_build_units_published(workbook: Path) -> None:
    data = _data(workbook)
    units = build_units(data, 2)
    assert [u.name for u in units] == ["gen1", "gen2", "gen3", "gen4"]
    assert isinstance(units[0], UnitParams)


def test_case_name_and_write(workbook: Path, tmp_path: Path) -> None:
    data = _data(workbook)
    assert case_name(CaseOptions(reserve_pct=5), data) == "cuc39_r5_s2"
    case = build_case(data, CaseOptions())
    path = write_case(case, tmp_path / "out")
    assert path.name == "cuc39_r10_s2.json"
    assert json.loads(path.read_text())["system"]["name"] == "cuc39_r10_s2"
