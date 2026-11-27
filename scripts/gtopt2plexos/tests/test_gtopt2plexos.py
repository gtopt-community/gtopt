# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for ``gtopt2plexos``: files, warnings, round trip and results."""

from __future__ import annotations

import copy
import csv
import json
import runpy
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from gtopt_shared.case_compare import compare_cases
from gtopt_shared.case_reader import GtoptCase
from gtopt_shared.testing.roundtrip import gtopt_binary, run_module, solve_cost
from gtopt_shared.testing.tiny_cases import (
    cascade_planning,
    tiny_planning,
    write_tiny_case,
)
from gtopt2plexos.main import main
from gtopt2plexos.results import PlexosResultsWriter, load_tables, read_output
from gtopt2plexos.writer import (
    BLOCK_LAYOUT,
    META_NAME,
    NO_CYCLE_LIMIT,
    XML_NAME,
    PlexosWriter,
    block_hours,
    sanitize,
)
from gtopt2plexos.xmldb import MasterDataSet, num_text
from plexos2gtopt.plexos_solution import PlexosSolution, csv_table_source
from plexos2gtopt.plexos_xml import load_xml

#: Volume factor CMD / hm3 of the tiny reservoir (fcr 0.0036).
K_TINY = (1.0 / 24.0) / 0.0036


def single_scenario(planning: dict[str, Any]) -> dict[str, Any]:
    """``planning`` cut to scenario 1, constant TERMO cost (PLEXOS-exact)."""
    p = copy.deepcopy(planning)
    p["simulation"]["scenario_array"] = [{"uid": 1, "probability_factor": 1.0}]
    sysm = p["system"]
    for fl in sysm["flow_array"]:
        if isinstance(fl.get("discharge"), list):
            fl["discharge"] = fl["discharge"][:1]
    for prof in sysm["capacity_profile_array"]:
        prof["profile"] = prof["profile"][:1]
    sysm["generator_array"][0]["gcost"] = 50.0
    return p


def _export(planning: dict[str, Any], tmp_path: Path, **kw: Any) -> PlexosWriter:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "c.json").write_text(json.dumps(planning), encoding="utf-8")
    writer = PlexosWriter(GtoptCase.load(src / "c.json"), tmp_path / "plexos", **kw)
    writer.write()
    return writer


def _read_back(writer: PlexosWriter, out: Path) -> GtoptCase:
    proc = run_module(
        "plexos2gtopt", "-i", writer.out_dir, "-o", out, *writer.plexos2gtopt_flags()
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    return GtoptCase.load(out / f"{writer.out_dir.name}.json")


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _volume_diffs_only(diffs: list[str]) -> None:
    """Every difference is a reservoir volume (CMD vs hm3, warned by design)."""
    assert diffs
    assert all(d.startswith("reservoir.e") for d in diffs), diffs


# ── tier 1: helpers ─────────────────────────────────────────────────────
def test_helpers() -> None:
    assert sanitize('a,b"c') == "a_b_c"
    assert sanitize("") == "X"
    assert block_hours((1.0, 10.0, 2.0)) == [1, 10, 2]
    with pytest.raises(ValueError, match="whole number of hours"):
        block_hours((1.5,))
    with pytest.raises(ValueError, match="whole number of hours"):
        block_hours((0.0,))
    assert num_text(2.0) == "2"
    assert num_text(0.1) == "0.1"


def test_master_dataset(tmp_path: Path) -> None:
    db = MasterDataSet()
    db.add_object("Node", "N1")
    db.add_object("Generator", "G1")
    db.add_membership("Generator", "G1", "Node", "N1", "Nodes")
    db.set_property("Generator", "G1", "Max Capacity", 12.5)
    with pytest.raises(ValueError, match="duplicate"):
        db.add_object("Node", "N1")
    assert db.objects_of("Node") == ["N1"]
    pdb = load_xml(db.write(tmp_path / "x.xml"))
    gen = pdb.objects_of_class("Generator")[0]
    assert pdb.static_property("Generator", gen.object_id, "Max Capacity") == 12.5
    coll = pdb.collection_for_named("Generator", "Node", "Nodes")
    assert coll is not None
    assert pdb.parent_to_children(coll.collection_id) == {
        gen.object_id: [pdb.objects_of_class("Node")[0].object_id]
    }


def test_files_and_properties(tmp_path: Path) -> None:
    writer = _export(tiny_planning(), tmp_path)
    out = tmp_path / "plexos"
    for name in (
        XML_NAME,
        BLOCK_LAYOUT,
        META_NAME,
        "Gen_Rating.csv",
        "Nod_Load.csv",
        "Lin_MaxRating.csv",
        "Lin_MinRating.csv",
        "Hydro_WaterFlows.csv",
    ):
        assert (out / name).is_file(), name
    assert _rows(out / BLOCK_LAYOUT) == [
        {"block_uid": str(u), "duration": str(d)}
        for u, d in ((1, 10), (2, 14), (3, 12), (4, 12))
    ]
    db = load_xml(out / XML_NAME)
    assert [o.name for o in db.objects_of_class("Node")] == ["Norte220", "Sur220"]
    bat = db.objects_of_class("Battery")[0]
    assert db.static_property("Battery", bat.object_id, "Capacity") == 160.0
    assert db.static_property("Battery", bat.object_id, "Charge Efficiency") == 95.0
    assert (
        db.static_property("Battery", bat.object_id, "Max Cycles Day") == NO_CYCLE_LIMIT
    )
    sto = db.objects_of_class("Storage")[0]
    assert db.static_property("Storage", sto.object_id, "Max Volume") == pytest.approx(
        1000.0 * K_TINY
    )
    lago = next(g for g in db.objects_of_class("Generator") if g.name == "LAGO")
    assert db.static_property("Generator", lago.object_id, "Efficiency Incr") == 2.0
    # 48 hours -> two whole days of hourly Data File rows.
    load = _rows(out / "Nod_Load.csv")
    assert len(load) == 48
    assert load[9]["Sur220"] == "100" and load[10]["Sur220"] == "140"
    meta = json.loads((out / META_NAME).read_text())
    assert meta["names"]["Generator"]["TERMO"] == "TERMO"
    assert meta["volume_factor"]["LAGO"] == pytest.approx(K_TINY)
    assert meta["warnings"] == writer.warnings


def test_tiny_warnings(tmp_path: Path) -> None:
    writer = _export(tiny_planning(), tmp_path)
    text = "\n".join(writer.warnings)
    for needle in (
        "2 scenarios",
        "2 stages are written as one",
        "time-varying gcost",
        "discharge varies by scenario",
        "chronological battery",
    ):
        assert needle in text, needle


# ── tier 2: round trip through plexos2gtopt ─────────────────────────────
def test_roundtrip_single_scenario_lossless(tmp_path: Path) -> None:
    planning = single_scenario(tiny_planning())
    writer = _export(planning, tmp_path)
    back = _read_back(writer, tmp_path / "back")
    orig = GtoptCase.load(tmp_path / "src" / "c.json")
    diffs = compare_cases(orig, back)
    # Only the reservoir volumes differ: PLEXOS holds them in CMD.
    _volume_diffs_only(diffs)
    res = back.elements("reservoir_array")[0]
    assert res["eini"] == pytest.approx(500.0 * K_TINY)
    assert res["emax"] == pytest.approx(1000.0 * K_TINY)
    assert res["flow_conversion_rate"] == pytest.approx(1.0 / 24.0)
    bat = back.elements("battery_array")[0]
    assert bat["daily_cycle"] is False
    assert bat["annual_standing_loss"] == pytest.approx(0.48)


def test_roundtrip_tiny_only_warned_differences(tmp_path: Path) -> None:
    writer = _export(tiny_planning(), tmp_path)
    back = _read_back(writer, tmp_path / "back")
    diffs = compare_cases(GtoptCase.load(tmp_path / "src" / "c.json"), back)
    keys = sorted({d.split(":", 1)[0] for d in diffs})
    assert keys == [
        "gen.gcost@TERMO",
        "inflow@LAGO",
        "reservoir.eini@LAGO",
        "reservoir.emax@LAGO",
        "reservoir.emin@LAGO",
    ]
    # gcost comes back as the duration-weighted mean.
    termo = next(g for g in back.elements("generator_array") if g["name"] == "TERMO")
    assert termo["gcost"] == pytest.approx(52.5)


@pytest.mark.parametrize("case", ["ieee_9b", "ieee_14b"])
def test_roundtrip_ieee_cases(case: str, tmp_path: Path) -> None:
    src = Path(__file__).resolve().parents[3] / "cases" / case / f"{case}.json"
    if not src.is_file():
        pytest.skip(f"{src} not found")
    writer = PlexosWriter(GtoptCase.load(src), tmp_path / "plexos")
    writer.write()
    back = _read_back(writer, tmp_path / "back")
    diffs = compare_cases(GtoptCase.load(src), back)
    # ieee_9b: g1/g2 pmin = 10 is warned and lost.
    assert all(d.startswith("gen.pmin@") for d in diffs), diffs
    assert len(diffs) == sum("pmin > 0" in w for w in writer.warnings)


def test_lossy_features_warn(tmp_path: Path) -> None:
    planning = copy.deepcopy(tiny_planning())
    sysm = planning["system"]
    sysm["bus_array"].append({"uid": 7, "name": "Isla"})
    sysm["generator_array"][0].update(pmin=5.0, expcap=10.0)
    sysm["generator_array"].append({"uid": 9, "name": "NOBUS", "bus": 99})
    sysm["generator_array"].append({"uid": 10, "name": "FREE", "bus": 7})
    sysm["demand_array"].append(
        {"uid": 3, "name": "d3", "bus": 1, "lmax": 1.0, "fcost": 500.0, "emin": 1.0}
    )
    sysm["demand_array"].append({"uid": 4, "name": "dx", "bus": 99, "lmax": 1.0})
    sysm["demand_array"].append(
        {"uid": 5, "name": "isla", "bus": 7, "lmax": 1.0, "fcost": 300.0}
    )
    ln = sysm["line_array"][0]
    ln.update(
        resistance=1.0,
        tcost=1.0,
        active=[1, 0],
        voltage=[220.0, 110.0],
        expcap=5.0,
    )
    ln.pop("tmax_ba")
    sysm["line_array"].append(
        {
            "uid": 2,
            "name": "zero",
            "bus_a": 1,
            "bus_b": 2,
            "tmax_ab": 0.0,
            "tmax_ba": 0.0,
        }
    )
    sysm["line_array"].append({"uid": 3, "name": "free", "bus_a": 1, "bus_b": 7})
    sysm["line_array"].append({"uid": 4, "name": "dangling", "bus_a": 1, "bus_b": 99})
    bat = sysm["battery_array"][0]
    bat.update(
        emin=10.0,
        eini=80.0,
        efin=20.0,
        max_cycles_day=2.0,
        discharge_cost=1.0,
        emax=[100.0, 160.0],
        pmax_charge=[[40.0, 40.0], [20.0, 20.0]],
        annual_standing_loss=[0.1, 0.2],
    )
    bat.pop("pmax_discharge")
    sysm["battery_array"].append({"uid": 6, "name": "BESS2", "emax": 10.0})
    sysm["battery_array"].append({"uid": 7, "name": "TERMO", "bus": 2})
    res = sysm["reservoir_array"][0]
    res.update(emin=[100.0, 150.0], efin=400.0, fmax=50.0)
    res.pop("eini")
    sysm["junction_array"][0]["drain"] = False
    sysm["junction_array"].append({"uid": 2, "name": "loose"})
    sysm["reservoir_array"].append({"uid": 2, "name": "ghost", "junction": "nowhere"})
    sysm["turbine_array"][0].update(
        production_factor=[[2.0, 2.0], [2.5, 2.5]], capacity=50.0, junction_b="loose"
    )
    sysm["turbine_array"].append({"uid": 2, "name": "ror", "flow": "q"})
    sysm["flow_array"][0]["direction"] = -1
    sysm["flow_array"].append({"uid": 2, "name": "q", "discharge": 1.0})
    sysm["waterway_array"] = [{"uid": 1, "name": "w", "junction_a": 1, "junction_b": 2}]
    sysm["reservoir_production_factor_array"] = [{"uid": 1, "name": "pf"}]
    sysm["user_constraint_array"] = [{"uid": 1, "name": "uc", "expression": "x <= 1"}]
    planning["simulation"]["stage_array"][1]["discount_factor"] = 0.9
    writer = _export(planning, tmp_path)
    text = "\n".join(writer.warnings)
    for needle in (
        "bus uids are not 1..n",
        "TERMO: pmin > 0",
        "TERMO: expcap",
        "NOBUS: unknown bus",
        "FREE: no pmax/capacity",
        "different fail costs",
        "d3: emin is not exported",
        "dx: unknown bus",
        "resistance exported",
        "tcost is not exported",
        "active schedule",
        "time-varying voltage",
        "Norte_Sur: expcap",
        "zero: tmax_ab <= 0",
        "zero: tmax_ba = 0",
        "free: unbounded flow",
        "dangling: unknown bus",
        "BESS1: emin > 0",
        "BESS1: efin is not exported",
        "max_cycles_day makes",
        "BESS1: discharge_cost",
        "time-varying emax",
        "pmax_discharge unset",
        "time-varying pmax_charge",
        "time-varying annual_standing_loss",
        "BESS2: no bus",
        "reservoir LAGO: time-varying emin",
        "LAGO: efin is not exported",
        "LAGO: fmax is not exported",
        "drain = false",
        "loose: junctions without a reservoir",
        "ghost: unknown junction",
        "time-varying production factor",
        "flow capacity is not exported",
        "outlet without a reservoir",
        "ror: only reservoir-fed turbines",
        "direction -1",
        "q: only inflows into a reservoir",
        "waterway_array is not exported",
        "reservoir_production_factor_array",
        "user_constraint_array is not exported",
        "discount factors",
    ):
        assert needle in text, needle
    names = writer.names["Battery"]
    assert names["TERMO"] == "TERMO_2"
    db = load_xml(tmp_path / "plexos" / XML_NAME)
    regions = sorted(o.name for o in db.objects_of_class("Region"))
    assert regions == ["Region", "Region_1", "Region_2"]
    assert (tmp_path / "plexos" / "BESS_IniValue.csv").is_file()
    assert (tmp_path / "plexos" / "Hydro_MinVolume.csv").is_file()
    back = _read_back(writer, tmp_path / "back")
    ln_back = next(x for x in back.elements("line_array") if x["name"] == "Norte_Sur")
    assert ln_back["resistance"] == pytest.approx(100.0 / 220.0**2)


def test_rejects_unrepresentable(tmp_path: Path) -> None:
    planning = tiny_planning()
    planning["simulation"]["block_array"][0]["duration"] = 0.5
    with pytest.raises(ValueError, match="whole number of hours"):
        _export(planning, tmp_path / "a")
    planning = tiny_planning()
    planning["simulation"]["stage_array"][1]["first_block"] = 3
    with pytest.raises(ValueError, match="in order"):
        _export(planning, tmp_path / "b")
    planning = tiny_planning()
    for key in list(planning["system"]):
        if key.endswith("_array") and key != "bus_array":
            planning["system"][key] = []
    with pytest.raises(ValueError, match="no generator or battery"):
        _export(planning, tmp_path / "c")
    (tmp_path / "empty.json").write_text(json.dumps(planning), encoding="utf-8")
    assert main([str(tmp_path / "empty.json"), "-o", str(tmp_path / "o")]) == 1


# ── results ─────────────────────────────────────────────────────────────
def _fake_results(results: Path) -> None:
    """A gtopt output dir in the formats gtopt writes (parquet + csv)."""
    rows = []
    for scen in (1, 2):
        for block, stage in ((1, 1), (2, 1), (3, 2), (4, 2)):
            rows.append((scen, stage, block))

    def table(uids: list[int], f: Any) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"scenario": s, "stage": st, "block": b, "uid": u, "value": f(s, b, u)}
                for s, st, b in rows
                for u in uids
            ]
        )

    for cls, name, uids, f, fmt in (
        ("Generator", "generation_sol", [4, 1, 8], lambda s, b, u: 10 * u + b, "pq"),
        ("Demand", "load_sol", [1, 2], lambda s, b, u: 50.0 + b, "csv"),
        ("Demand", "fail_sol", [2], lambda s, b, u: 1.0 * s, "piece"),
        ("Bus", "balance_dual", [1, 2], lambda s, b, u: 40.0 + s, "pq"),
        ("Line", "flow_sol", [1], lambda s, b, u: -5.0 * b, "csv"),
        ("Reservoir", "energy_sol", [1], lambda s, b, u: 500.0 - b, "pqdir"),
        ("Battery", "energy_sol", [5], lambda s, b, u: 10.0 * b, "pq"),
    ):
        (results / cls).mkdir(parents=True, exist_ok=True)
        df = table(uids, f)
        if fmt == "pq":
            df.to_parquet(results / cls / f"{name}.parquet")
        elif fmt == "pqdir":
            (results / cls / f"{name}.parquet").mkdir()
            df.to_parquet(results / cls / f"{name}.parquet" / "part0.parquet")
        elif fmt == "piece":
            df.to_csv(results / cls / f"{name}_s0_p0.csv", index=False)
        else:
            df.to_csv(results / cls / f"{name}.csv", index=False)
    # Legacy wide layout.
    pd.DataFrame(
        [{"scenario": s, "stage": st, "block": b, "uid:5": 2.0} for s, st, b in rows]
    ).to_csv(results / "Battery" / "fout_sol.csv", index=False)


def test_read_output_layouts(tmp_path: Path) -> None:
    _fake_results(tmp_path)
    assert len(read_output(tmp_path, "Generator", "generation_sol")) == 24
    assert len(read_output(tmp_path, "Demand", "fail_sol")) == 8
    wide = read_output(tmp_path, "Battery", "fout_sol")
    assert set(wide["uid"]) == {5}
    assert read_output(tmp_path, "Nothing", "x").empty
    assert read_output(tmp_path, "Battery", "finp_sol").empty


def test_results_tables_read_by_plexos_solution(tmp_path: Path) -> None:
    case_path = write_tiny_case(tmp_path / "src")
    _fake_results(tmp_path / "out")
    case = GtoptCase.load(case_path)
    res = PlexosResultsWriter(case, tmp_path / "out", tmp_path / "sol")
    res.write()
    sol = PlexosSolution(csv_table_source(load_tables(tmp_path / "sol")))
    assert sol.n_intervals == 48
    assert sol.interval_period[10] == 1 and sol.interval_period[11] == 2
    got = {
        (k.class_name, k.object_name, k.property_name): v
        for k, v in sol.fetch(
            [
                ("Generator", "Generation"),
                ("Node", "Load"),
                ("Node", "Unserved Energy"),
                ("Node", "Price"),
                ("Line", "Flow"),
                ("Storage", "End Volume"),
                ("Battery", "Energy"),
                ("Battery", "Generation"),
            ]
        ).items()
    }
    # Sample 0 is the scenario mean (probabilities 0.5 / 0.5).
    assert got[("Generator", "TERMO", "Generation")] == {1: 41, 2: 42, 3: 43, 4: 44}
    assert got[("Node", "Sur220", "Load")][1] == pytest.approx(51.0 + 1.5)
    assert got[("Node", "Sur220", "Unserved Energy")][4] == pytest.approx(1.5)
    assert got[("Node", "Norte220", "Price")][2] == pytest.approx(41.5)
    assert got[("Line", "Norte_Sur", "Flow")][3] == pytest.approx(-15.0)
    vol = got[("Storage", "LAGO", "End Volume")]
    assert vol[4] == pytest.approx(496.0 * K_TINY)
    assert sol.end_of_day(vol, 1) == pytest.approx(498.0 * K_TINY)
    assert got[("Battery", "BESS1", "Energy")][3] == pytest.approx(30.0)
    assert got[("Battery", "BESS1", "Generation")][1] == pytest.approx(2.0)
    # Per-scenario samples are kept too.
    keys = _rows(tmp_path / "sol" / "t_key.csv")
    assert {k["sample_id"] for k in keys} == {"0", "1", "2"}
    assert (tmp_path / "sol" / "t_period_0.csv").is_file()


def test_results_single_scenario_and_fractional_blocks(tmp_path: Path) -> None:
    planning = single_scenario(tiny_planning())
    planning["simulation"]["block_array"][0]["duration"] = 0.5
    (tmp_path / "c.json").write_text(json.dumps(planning), encoding="utf-8")
    _fake_results(tmp_path / "out")
    case = GtoptCase.load(tmp_path / "c.json")
    good = single_scenario(tiny_planning())
    (tmp_path / "g.json").write_text(json.dumps(good), encoding="utf-8")
    writer = PlexosWriter(GtoptCase.load(tmp_path / "g.json"), tmp_path / "px")
    writer.build()
    res = PlexosResultsWriter(case, tmp_path / "out", tmp_path / "sol", writer=writer)
    assert "one interval per block" in res.warnings[0]
    res.write()
    keys = _rows(tmp_path / "sol" / "t_key.csv")
    assert {k["sample_id"] for k in keys} == {"0"}
    assert len(_rows(tmp_path / "sol" / "t_phase_3.csv")) == 4


# ── CLI ─────────────────────────────────────────────────────────────────
def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = write_tiny_case(tmp_path / "src")
    assert (
        main([str(case), "-o", str(tmp_path / "out"), "--start-date", "2031-02-03"])
        == 0
    )
    assert "read back with: plexos2gtopt" in capsys.readouterr().out
    first = _rows(tmp_path / "out" / "Gen_Rating.csv")[0]
    assert (first["YEAR"], first["MONTH"], first["DAY"]) == ("2031", "2", "3")
    assert main([str(case), "-o", str(tmp_path / "o2"), "--strict"]) == 2
    _fake_results(tmp_path / "res")
    assert (
        main(
            [str(case), "-o", str(tmp_path / "o3"), "--results", str(tmp_path / "res")]
        )
        == 0
    )
    assert (tmp_path / "o3" / "plexos_solution" / "t_data_0.csv").is_file()
    assert (
        main(
            [
                str(case),
                "-o",
                str(tmp_path / "o4"),
                "--results",
                str(tmp_path / "res"),
                "--results-only",
            ]
        )
        == 0
    )
    assert not (tmp_path / "o4" / XML_NAME).exists()
    assert (tmp_path / "o4" / "plexos_solution" / "t_key.csv").is_file()
    with pytest.raises(SystemExit):
        main([str(case), "-o", str(tmp_path / "o5"), "--results-only"])


def test_python_m_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = write_tiny_case(tmp_path / "src")
    monkeypatch.setattr(
        "sys.argv", ["gtopt2plexos", str(case), "-o", str(tmp_path / "out"), "-v"]
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("gtopt2plexos", run_name="__main__")
    assert exc.value.code == 0


def test_cascade_exports(tmp_path: Path) -> None:
    writer = _export(cascade_planning(), tmp_path, start_date=date(2030, 4, 1))
    assert "waterway_array is not exported" in "\n".join(writer.warnings)
    assert np.isclose(writer.volume_factor["LAGO"], K_TINY)


# ── tier 3: solve both ──────────────────────────────────────────────────
@pytest.mark.integration
def test_roundtrip_same_cost(tmp_path: Path) -> None:
    gtopt = gtopt_binary()
    if gtopt is None:
        pytest.skip("gtopt binary not found (set GTOPT_BIN)")
    writer = _export(single_scenario(tiny_planning()), tmp_path)
    _read_back(writer, tmp_path / "back")
    cost_a = solve_cost(
        gtopt, tmp_path / "src" / "c.json", tmp_path / "sa", "monolithic"
    )
    cost_b = solve_cost(gtopt, tmp_path / "back" / "plexos.json", tmp_path / "sb")
    assert cost_b == pytest.approx(cost_a, rel=1e-7)
    # The gtopt results of A, as PLEXOS solution tables, read back by plexos2gtopt.
    res = PlexosResultsWriter(
        GtoptCase.load(tmp_path / "src" / "c.json"), tmp_path / "sa", tmp_path / "sol"
    )
    res.write()
    sol = PlexosSolution(csv_table_source(load_tables(tmp_path / "sol")))
    gen = read_output(tmp_path / "sa", "Generator", "generation_sol")
    termo = gen[gen["uid"] == 4].sort_values("block")["value"].tolist()
    series = sol.fetch([("Generator", "Generation")])
    got = next(v for k, v in series.items() if k.object_name == "TERMO")
    assert [got[p] for p in (1, 2, 3, 4)] == pytest.approx(termo)
