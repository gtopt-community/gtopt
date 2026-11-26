# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Converter tests: SQLite reading, element mapping, time grids, CLI,
and the reproducibility of ``cases/cheby_small``."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from gtopt_shared.point_sampling import commitment_period_class, point_storage_options
from tcheby2gtopt.convert import ConvertOptions, convert
from tcheby2gtopt.library import (
    TCHEBY_CASES,
    build_library,
    caso2emb_system,
    convert_case,
)
from tcheby2gtopt.main import main
from tcheby2gtopt.tcheby_db import (
    StepSchedule,
    parse_date,
    read_simulation,
    read_system,
)
from tcheby2gtopt.timegrid import TimeGrid, lobatto_grid

from tcheby2gtopt.tests.conftest import REPO_CASES

TZ = datetime(2016, 1, 1)


# -- schedules / reading ------------------------------------------------------


def test_parse_date_formats():
    assert parse_date("2015-5-17") == datetime(2015, 5, 17)
    assert parse_date("2016-01-04 04:00:00") == datetime(2016, 1, 4, 4)
    assert parse_date("2016-01-04 04:30") == datetime(2016, 1, 4, 4, 30)


def test_step_schedule_zero_order_hold():
    s = StepSchedule(default=7.0, hours=[2.0, 5.0], values=[1.0, 3.0])
    np.testing.assert_array_equal(
        s.at([0.0, 1.99, 2.0, 4.0, 5.0, 99.0]), [7, 7, 1, 1, 3, 3]
    )
    np.testing.assert_array_equal(s.hourly(0.0, 6), [7, 7, 1, 1, 1, 3])
    assert StepSchedule(4.0).is_constant
    np.testing.assert_array_equal(StepSchedule(4.0).hourly(10.0, 3), [4, 4, 4])


def test_read_caso1emb(caso1emb_dbs):
    sys_db, sim_db = caso1emb_dbs
    sim = read_simulation(sim_db)
    assert sim.time_zero == TZ
    assert sim.stages == [100.0, 100.0, 100.0]
    assert sim.inflows == {"CH_Inf": [30.0, 10.0, 15.0]}
    sysm = read_system(sys_db, sim.time_zero)
    assert [b.name for b in sysm.buses] == ["Antuco220", "Valdivia220"]
    load = sysm.demands[0].load
    # 2016-01-04 04:00 = 76 h, 2016-01-08 08:00 = 176 h
    assert load.hours == [0.0, 76.0, 176.0]
    assert load.values == [100.0, 50.0, 80.0]
    ch = next(g for g in sysm.generators if g.name == "CH")
    assert ch.pmin.default == 20.0 and ch.pmax.default == 450.0
    assert sysm.waterways[1].junc_b_id == 0  # the sea
    assert sysm.reservoirs[0].vini == pytest.approx(3.6)
    assert sysm.turbines[0].efficiency == 1.0


def test_read_reszone_hour_keys_and_extras(reszone_db):
    sysm = read_system(reszone_db, TZ)
    fmin = sysm.waterways[0].fmin
    assert fmin.hours == [0.0, 1.0, 4.0] and fmin.values == [1.0, 2.0, 3.3]
    gen1 = sysm.generators[0]
    assert gen1.active  # NULL active means active
    assert gen1.pmax.default == 300.0  # NULL pmax -> capacity
    assert gen1.pmin.default == 0.0
    assert len(sysm.passthroughs) == 1 and len(sysm.overspills) == 1
    assert sysm.commitments[0].up_time == 4.5
    assert any(t.startswith("ReserveZone") for t in sysm.dropped)
    assert any(t.startswith("Demand.active_sched") for t in sysm.dropped)


def test_read_only_and_missing(tmp_path, caso1emb_dbs):
    with pytest.raises(FileNotFoundError):
        read_system(tmp_path / "nope.db", TZ)
    sys_db, _ = caso1emb_dbs
    before = sys_db.read_bytes()
    read_system(sys_db, TZ)
    assert sys_db.read_bytes() == before


# -- mapping ------------------------------------------------------------------


def _conv(sys_db, sim_db, mode, **kw):
    sim = read_simulation(sim_db)
    sysm = read_system(sys_db, sim.time_zero, name="caso1emb")
    grid = TimeGrid(mode, sim.stages, nodes=24)
    return convert(sysm, grid, sim, ConvertOptions(**kw))


def test_convert_caso1emb_block(caso1emb_dbs):
    conv = _conv(*caso1emb_dbs, "block")
    p = conv.planning
    blocks = p["simulation"]["block_array"]
    assert len(blocks) == 300 and all(b["duration"] == 1 for b in blocks)
    assert "time" not in blocks[0]
    sysd = p["system"]
    lmax = sysd["demand_array"][0]["lmax"]
    flat = sum(lmax, [])
    assert flat[75] == 100 and flat[76] == 50 and flat[176] == 80
    assert sum(flat) == pytest.approx(100 * 76 + 50 * 100 + 80 * 124)
    # junctions: SEA is a drain, J1 holds the reservoir, J2 drains via OCEAN
    jun = {j["name"]: j for j in sysd["junction_array"]}
    assert jun["SEA"].get("drain") is True
    assert "drain" not in jun["J1"] and "drain" not in jun["J2"]
    res = sysd["reservoir_array"][0]
    assert res["spillway_capacity"] == 0 and res["eini"] == 3.6
    assert sysd["flow_array"][0]["discharge"][0][1][0] == 10
    line = sysd["line_array"][0]
    assert line["tmax_ab"] == 600 and line["tmax_ba"] == 600
    assert "resistance" not in line and line["reactance"] == 40
    gen = {g["name"]: g for g in sysd["generator_array"]}
    assert gen["CH"]["pmin"] == 20 and gen["CT"]["gcost"] == 10
    assert sysd["turbine_array"][0]["generator"] == gen["CH"]["uid"]
    assert p["options"]["model_options"]["demand_fail_cost"] == 500


def test_convert_point_storage_integration(caso1emb_dbs):
    """Point conversions integrate batteries and reservoirs with cc."""
    mo_p = _conv(*caso1emb_dbs, "point").planning["options"]["model_options"]
    mo_b = _conv(*caso1emb_dbs, "block").planning["options"]["model_options"]
    assert mo_p["battery_storage_integration"] == "cc"
    assert mo_p["reservoir_storage_integration"] == "cc"
    assert "reservoir_storage_integration" not in mo_b


def test_convert_options(caso1emb_dbs):
    conv = _conv(*caso1emb_dbs, "block", drop_hydro_pmin=True, line_losses=True)
    gen = {g["name"]: g for g in conv.planning["system"]["generator_array"]}
    assert "pmin" not in gen["CH"]
    assert any("pmin dropped" in n for n in conv.notes)
    assert conv.planning["system"]["line_array"][0]["resistance"] == 10


def test_convert_caso1emb_point_energy(caso1emb_dbs):
    blk = _conv(*caso1emb_dbs, "block").planning
    pnt = _conv(*caso1emb_dbs, "point").planning
    stages = pnt["simulation"]["stage_array"]
    assert all(s["sampling"] == "point" and s["count_block"] == 25 for s in stages)
    blocks = pnt["simulation"]["block_array"]
    l_b = blk["system"]["demand_array"][0]["lmax"]
    l_p = pnt["system"]["demand_array"][0]["lmax"]
    for s, st in enumerate(stages):
        bl = blocks[st["first_block"] : st["first_block"] + st["count_block"]]
        w = np.array([b["duration"] for b in bl])
        t = np.array([b["time"] for b in bl])
        assert w.sum() == pytest.approx(100.0, rel=1e-12)
        assert t[0] == 0 and t[-1] == pytest.approx(100.0) and np.all(np.diff(t) > 0)
        # wcell sampling keeps the energy and the data range
        assert np.dot(w, l_p[s]) == pytest.approx(sum(l_b[s]), rel=1e-10)
        assert min(l_b[s]) - 1e-9 <= min(l_p[s]) and max(l_p[s]) <= max(l_b[s]) + 1e-9


def test_convert_reszone_mapping(reszone_db):
    sysm = read_system(reszone_db, TZ, name="reszone")
    conv = convert(sysm, TimeGrid("block", [24.0]))
    sysd = conv.planning["system"]
    gen = {g["name"]: g for g in sysd["generator_array"]}
    assert gen["pt1"]["capacity"] == 75  # min(100, 0.5 * 150)
    line = sysd["line_array"][0]
    assert line["tmax_ab"] == 67 and line["tmax_ba"] == 900  # positive tmin
    assert any("positive tmin" in n for n in conv.notes)
    res = {r["name"]: r for r in sysd["reservoir_array"]}
    assert res["Laja"]["spillway_cost"] == 10 and "spillway_capacity" not in res["Laja"]
    assert res["Laja"]["spill_junction"] == 2
    assert res["Maule"]["spillway_capacity"] == 0
    jun = {j["name"]: j for j in sysd["junction_array"]}
    assert jun["maule_junct"]["drain"] and jun["colbun_junct"]["drain"]
    assert "drain" not in jun["laja_junct"]
    fmin = sysd["waterway_array"][0]["fmin"][0]
    assert fmin[:5] == [1, 2, 2, 2, 3.3]
    uc = sysd["commitment_array"][0]
    assert uc["min_up_time"] == 4.5 and uc["min_down_time"] == 4
    assert any("delay" in n for n in conv.notes)
    assert any("ReserveZone" in n for n in conv.notes)


def test_caso2emb_builtin():
    sysm, sim = caso2emb_system()
    conv = convert(sysm, TimeGrid("point", sim.stages, nodes=24), sim)
    sysd = conv.planning["system"]
    assert [f["name"] for f in sysd["flow_array"]] == ["CH_Inf", "CH2_Inf"]
    d = sysd["flow_array"][0]["discharge"][0]
    assert [v[0] for v in d] == [30, 10, 15]
    assert sysd["demand_array"][0]["lmax"] == [[100] * 25, [50] * 25, [80] * 25]


# -- time grid ----------------------------------------------------------------


def test_lobatto_grid_matches_cc_formula():
    g = lobatto_grid(8, 24.0)
    n = 8
    theta = np.pi * np.arange(n + 1) / n
    t = 12.0 * (1 - np.cos(theta))
    w = []
    for k in range(n + 1):
        s = sum(
            (1.0 if 2 * j == n else 2.0) / (4 * j * j - 1) * np.cos(2 * j * theta[k])
            for j in range(1, n // 2 + 1)
        )
        w.append((1.0 if k in (0, n) else 2.0) / n * (1 - s) * 12.0)
    np.testing.assert_allclose(g.times, t, atol=1e-12)
    np.testing.assert_allclose(g.durations, w, atol=1e-12)


def test_block_grid_requires_whole_hours():
    with pytest.raises(ValueError):
        TimeGrid("block", [10.5])


def test_sample_hourly_interp_and_length_check():
    g = TimeGrid("point", [24.0], nodes=12, point_values="interp")
    y = np.arange(24, dtype=float)
    v = g.sample_hourly(0, y)
    assert len(v) == 13
    with pytest.raises(ValueError):
        g.sample_hourly(0, y[:10])


# -- CLI / library -------------------------------------------------------------


def test_cli_convert(caso1emb_dbs, tmp_path, capsys):
    sys_db, sim_db = caso1emb_dbs
    rc = main(
        [
            "convert",
            str(sys_db),
            "--simu",
            str(sim_db),
            "--name",
            "c1",
            "--out-dir",
            str(tmp_path / "o"),
            "--nodes",
            "8",
        ]
    )
    assert rc == 0
    pnt = json.loads((tmp_path / "o" / "c1_point.json").read_text())
    assert len(pnt["simulation"]["block_array"]) == 27
    assert (tmp_path / "o" / "c1_block.json").is_file()
    assert "c1_point.json" in capsys.readouterr().out


def test_cli_convert_without_simulation(reszone_db, tmp_path):
    rc = main(
        [
            "convert",
            str(reszone_db),
            "--name",
            "rz",
            "--out-dir",
            str(tmp_path),
            "--hours",
            "12",
            "--line-losses",
        ]
    )
    assert rc == 0
    blk = json.loads((tmp_path / "rz_block.json").read_text())
    assert len(blk["simulation"]["block_array"]) == 12
    assert blk["system"]["line_array"][0]["resistance"] == 30


def _canon(path: Path) -> str:
    return json.dumps(json.loads(path.read_text()), sort_keys=True)


def test_library_mini_and_caso2emb_reproducible(tmp_path):
    """The committed JSON equals a fresh build (mini cases, caso2emb)."""
    written = build_library(None, tmp_path)
    jsons = [p for p in written if p.suffix == ".json"]
    assert len(jsons) == 12
    for p in jsons:
        rel = p.relative_to(tmp_path)
        assert _canon(p) == _canon(REPO_CASES / rel), rel


def test_library_tcheby_cases_reproducible(real_dbs, tmp_path):
    """With TCHEBY_TEST_DBS: the DB-derived cases equal a fresh build."""
    for spec in TCHEBY_CASES:
        for mode in ("block", "point"):
            conv = convert_case(spec, real_dbs, mode)
            committed = REPO_CASES / spec.name / f"{spec.name}_{mode}.json"
            got = json.dumps(conv.planning, sort_keys=True)
            assert got == _canon(committed), committed


def test_point_commitment_period_per_unit(reszone_db):
    """Point stages: each unit gets its own uniform commitment grid — the
    source model's UC resolution when it has one (2.5 h here), else the
    class rule — overridable per unit; block stages keep the per-block
    status."""
    sysm = read_system(reszone_db, TZ, name="reszone")
    pt = convert(sysm, TimeGrid("point", [24.0], nodes=24))
    uc = pt.planning["system"]["commitment_array"][0]
    assert uc["commitment_period"] == 2.5
    assert uc["ramp_allowance"] == "boundary"
    assert any(f"{uc['name']}: source unit, 2.5 h grid" in n for n in pt.notes)
    sysm.commitments[0].time_resolution = None
    cls = convert(sysm, TimeGrid("point", [24.0], nodes=24))
    assert cls.planning["system"]["commitment_array"][0]["commitment_period"] == 3
    blk = convert(sysm, TimeGrid("block", [24.0]))
    assert "commitment_period" not in blk.planning["system"]["commitment_array"][0]
    assert "ramp_allowance" not in blk.planning["system"]["commitment_array"][0]
    ov = convert(
        sysm,
        TimeGrid("point", [24.0], nodes=24),
        None,
        ConvertOptions(commitment_periods={uc["name"]: 2.0}),
    )
    assert ov.planning["system"]["commitment_array"][0]["commitment_period"] == 2


def test_commitment_period_class_rule():
    assert commitment_period_class(None, None) == ("fast", 1.0)
    assert commitment_period_class(6.0, 2.0) == ("fast", 1.0)
    assert commitment_period_class(4.5, 4.0) == ("medium", 3.0)
    assert commitment_period_class(12.0, 8.0) == ("slow", 4.0)
    assert commitment_period_class(None, 10.0) == ("slow", 4.0)
    assert commitment_period_class(12.0, 8.0, time_resolution=2.0) == ("source", 2.0)
    assert commitment_period_class(12.0, 8.0, 0.25) == ("source", 1.0)
    assert commitment_period_class(1.0, 1.0, 2.0, override=0.5) == ("override", 1.0)


def test_point_previous_behaviour_options(reszone_db):
    """commitment_period_rule="none" and point_storage_integration="none"
    restore the previous point-stage output."""
    sysm = read_system(reszone_db, TZ, name="reszone")
    grid = TimeGrid("point", [24.0], nodes=24)
    new = convert(sysm, grid)
    mo = new.planning["options"]["model_options"]
    assert mo["battery_storage_integration"] == "cc"
    assert mo["reservoir_storage_integration"] == "cc"
    old = convert(
        sysm,
        grid,
        None,
        ConvertOptions(commitment_period_rule="none", point_storage_integration="none"),
    )
    uc = old.planning["system"]["commitment_array"][0]
    assert "commitment_period" not in uc
    assert "ramp_allowance" not in uc
    mo = old.planning["options"]["model_options"]
    assert "battery_storage_integration" not in mo
    assert "reservoir_storage_integration" not in mo


def test_point_storage_options_rules():
    assert point_storage_options() == {
        "battery_storage_integration": "cc",
        "reservoir_storage_integration": "cc",
    }
    # the previous default stays selectable
    assert point_storage_options("lg4_pos")["reservoir_storage_integration"] == (
        "lg4_pos"
    )
    assert point_storage_options("trapezoid")["battery_storage_integration"] == (
        "trapezoid"
    )
    assert not point_storage_options("none")
    # Clenshaw-Curtis rule (alias of gtopt's block recursion on CC grids)
    assert point_storage_options("cc") == {
        "battery_storage_integration": "cc",
        "reservoir_storage_integration": "cc",
    }
