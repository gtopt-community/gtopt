# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests of :mod:`gtopt_shared.results_writer` (gtopt output layout)."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from gtopt_results_summary.summary import summarize_results
from gtopt_shared.results_reader import GtoptResults
from gtopt_shared.results_writer import (
    RESULTS_AUTO,
    ElementIndex,
    ImportedResults,
    add_results_arguments,
    find_planning_json,
    load_planning,
    per_hour_to_mw,
    planning_cells,
    report_import,
    results_output_dir,
    uid_of,
)
from gtopt_shared.testing.results_roundtrip import check_layout
from gtopt_shared.testing.tiny_cases import tiny_planning


def _phased() -> dict[str, Any]:
    """tiny_planning with one scene per scenario and one phase per stage."""
    p = tiny_planning()
    p["simulation"]["scene_array"] = [
        {"uid": 1, "first_scenario": 0, "count_scenario": 1},
        {"uid": 2, "first_scenario": 1, "count_scenario": 1},
    ]
    p["simulation"]["phase_array"] = [
        {"uid": 1, "first_stage": 0, "count_stage": 1},
        {"uid": 2, "first_stage": 1, "count_stage": 1},
    ]
    return p


def _filled(planning: dict[str, Any]) -> ImportedResults:
    r = ImportedResults(planning, "test")
    gen = np.array([[0.0, 1.5, 2.0, 0.0], [3.0, 0.0, 4.0, 5.0]])
    r.add_blocks("Generator", "generation_sol", 4, gen)
    r.add_blocks("Bus", "balance_dual", 1, np.zeros((2, 4)))
    r.add_stages("Reservoir", "efin_sol", 1, np.array([[400.0, 300.0], [450.0, 0.0]]))
    return r


def test_planning_cells_default_and_phased() -> None:
    """No scene/phase arrays -> one cell (0, 0); else scene x phase."""
    p = tiny_planning()
    r = ImportedResults(p, "t")
    cells = planning_cells(p, r.grid)
    assert [(c.scene_uid, c.phase_uid) for c in cells] == [(0, 0)]
    assert cells[0].scenarios == (0, 1) and cells[0].stages == (0, 1)
    cells = planning_cells(_phased(), r.grid)
    assert [(c.scene_uid, c.phase_uid) for c in cells] == [
        (1, 1),
        (1, 2),
        (2, 1),
        (2, 2),
    ]
    assert cells[3].scenarios == (1,) and cells[3].stages == (1,)


def test_write_layout_and_values(tmp_path: Path) -> None:
    """Schema, partitions, zero dropping and row order match gtopt."""
    r = _filled(_phased())
    r.cost = np.ones((2, 4))
    files = r.write(tmp_path)
    assert tmp_path / "solution.csv" in files
    parts = check_layout(tmp_path)
    assert len(parts) == 3 * 4
    gen = pd.read_parquet(
        tmp_path / "Generator/generation_sol.parquet/scene=2/phase=2/part.parquet"
    )
    assert gen.to_dict("list") == {
        "scenario": [2, 2],
        "stage": [2, 2],
        "block": [3, 4],
        "uid": [4, 4],
        "value": [4.0, 5.0],
    }
    # _dual streams keep their zeros, _sol streams drop them
    dual = pd.read_parquet(tmp_path / "Bus/balance_dual.parquet")
    assert len(dual) == 8 and (dual["value"] == 0).all()
    efin = pd.read_parquet(tmp_path / "Reservoir/efin_sol.parquet")
    assert set(efin["block"]) == {0} and len(efin) == 3
    # readers of gtopt output load it unchanged
    back = GtoptResults(tmp_path, r.grid)
    assert back.block_of("Generator", "generation_sol", 4)[1].tolist() == [
        3.0,
        0.0,
        4.0,
        5.0,
    ]
    assert back.stage_of("Reservoir", "efin_sol", 1)[0].tolist() == [400.0, 300.0]
    sol = pd.read_csv(tmp_path / "solution.csv")
    # cost-to-go per (scene, phase): weights 0.5, phase 1 holds all 4 blocks
    assert sol["obj_value"].tolist() == [2.0, 1.0, 2.0, 1.0]
    status = json.loads((tmp_path / "solver_status.json").read_text())
    assert status["method"] == "imported" and status["source"] == "test"
    plan = json.loads((tmp_path / "planning.json").read_text())
    names = [g["name"] for g in plan["system"]["generator_array"]]
    assert "BESS1_gen" in names
    summary = summarize_results(tmp_path)
    assert summary["n_generators"] >= 1


def test_battery_expansion(tmp_path: Path) -> None:
    """A battery with a bus gets <name>_gen / _dem streams as gtopt writes."""
    p = tiny_planning()
    r = ImportedResults(p, "t")
    fout = np.array([[0.0, 3.0, 0.0, 0.0], [0.0, 3.0, 0.0, 0.0]])
    finp = np.array([[2.0, 0.0, 0.0, 0.0], [2.0, 0.0, 0.0, 0.0]])
    r.add_blocks("Battery", "fout_sol", 5, fout)
    r.add_blocks("Battery", "finp_sol", 5, finp)
    r.write(tmp_path)
    plan = json.loads((tmp_path / "planning.json").read_text())
    gens = {g["name"]: g["uid"] for g in plan["system"]["generator_array"]}
    dems = {d["name"]: d["uid"] for d in plan["system"]["demand_array"]}
    assert gens["BESS1_gen"] == 9 and dems["BESS1_dem"] == 3
    assert plan["system"]["converter_array"][0]["battery"] == "BESS1"
    back = GtoptResults(tmp_path, r.grid)
    assert back.block_of("Generator", "generation_sol", 9).tolist() == fout.tolist()
    assert back.block_of("Demand", "load_sol", 3).tolist() == finp.tolist()
    # a second expansion (planning already expanded) adds nothing
    r2 = ImportedResults(plan, "t")
    r2.write(tmp_path / "again")
    plan2 = json.loads((tmp_path / "again/planning.json").read_text())
    assert len(plan2["system"]["generator_array"]) == len(
        plan["system"]["generator_array"]
    )


@pytest.mark.parametrize(
    ("opts", "pattern"),
    [
        ({"output_format": "csv", "output_compression": "zstd"}, "*_s0_p0.csv.zst"),
        ({"output_format": "csv", "output_compression": "gzip"}, "*_s0_p0.csv.gz"),
        ({"output_format": "csv", "output_compression": "none"}, "*_s0_p0.csv"),
    ],
)
def test_csv_output(tmp_path: Path, opts: dict[str, str], pattern: str) -> None:
    """output_format = csv writes gtopt's per-(scene, phase) shards."""
    p = tiny_planning()
    p["options"].update(opts)
    r = _filled(p)
    r.write(tmp_path)
    shards = list((tmp_path / "Generator").glob(pattern))
    assert len(shards) == 1
    back = GtoptResults(tmp_path, r.grid)
    assert back.block_of("Generator", "generation_sol", 4)[0, 1] == 1.5


@pytest.mark.parametrize(("digits", "dtype"), [(0, "double"), (9, "double")])
def test_round_decimals(tmp_path: Path, digits: int, dtype: str) -> None:
    """output_round_decimals outside 1..7 keeps float64 values."""
    p = tiny_planning()
    p["options"]["output_round_decimals"] = digits
    p["options"]["output_compression"] = "none"
    _filled(p).write(tmp_path)
    part = tmp_path / "Generator/generation_sol.parquet/scene=0/phase=0/part.parquet"
    assert str(pq.read_schema(part).field("value").type) == dtype


def test_add_shape_checks_and_accumulate() -> None:
    """Shapes are validated; ``add=True`` accumulates."""
    r = ImportedResults(tiny_planning(), "t")
    with pytest.raises(ValueError, match="bad shape"):
        r.add_blocks("Generator", "generation_sol", 1, np.zeros(3))
    with pytest.raises(ValueError, match="bad shape"):
        r.add_stages("Reservoir", "efin_sol", 1, np.zeros(3))
    r.add_blocks("Line", "flow_sol", 1, np.ones((2, 4)), add=True)
    r.add_blocks("Line", "flow_sol", 1, np.ones((2, 4)), add=True)
    got = r.get_blocks("Line", "flow_sol", 1)
    assert got is not None and got.max() == 2.0
    assert r.has("Line", "flow_sol") and not r.has("Line", "loss_sol")
    r.warn("x")
    r.warn("x")
    assert r.warnings == ["x"]


def test_element_index() -> None:
    """Exact, normalised, prefixed and code-suffixed name matching."""
    p = tiny_planning()
    p["system"]["line_array"][0]["name"] = "Norte Sur_7"
    p["system"]["reservoir_array"][0]["name"] = "r_LAGO"
    p["system"]["generator_array"].append({"uid": 99})  # no name
    idx = ElementIndex(p)
    assert uid_of(idx.find("generator_array", "TERMO")) == 4
    assert uid_of(idx.find("generator_array", "termo")) == 4
    assert uid_of(idx.find("line_array", "Norte Sur")) == 1
    assert uid_of(idx.find("reservoir_array", "LAGO", prefixes=("r_",))) == 1
    assert idx.find("generator_array", "NONE") is None
    assert uid_of(idx.ref("junction_array", "LAGO")) == 1
    assert idx.ref("junction_array", None) is None
    bus = idx.find("bus_array", "Sur220")
    assert bus is not None
    assert [d["uid"] for d in idx.by_bus("demand_array", bus)] == [2]
    assert uid_of(None) is None and uid_of({"uid": "x"}) is None


def test_assign_bus_load_variants() -> None:
    """One demand, lmax shares, first-demand fallback, no demand."""
    p = tiny_planning()
    sysm = p["system"]
    sysm["demand_array"] += [
        {"uid": 7, "name": "a", "bus": 1, "lmax": 30.0},
        {"uid": 8, "name": "b", "bus": "Norte220", "lmax": 10.0},
    ]
    sysm["demand_array"][0]["lmax"] = 60.0  # bus 1: shares 60/30/10
    sysm["bus_array"].append({"uid": 3, "name": "Empty"})
    r = ImportedResults(p, "t")
    ones = np.ones((2, 4))
    r.assign_bus_load(sysm["bus_array"][0], 100 * ones, 10 * ones)
    load = r.block_data[("Demand", "load_sol")]
    assert load[1][0, 0] == pytest.approx(60.0) and load[8][0, 0] == pytest.approx(10.0)
    r.assign_bus_load(sysm["bus_array"][1], ones, 0 * ones)
    assert load[2][0, 0] == 1.0
    sysm["demand_array"][3]["lmax"] = "file"
    r2 = ImportedResults(p, "t")
    r2.assign_bus_load(sysm["bus_array"][0], ones, ones)
    assert any("3 demands" in w for w in r2.warnings)
    r2.assign_bus_load(sysm["bus_array"][2], ones, ones)
    assert any("no demand" in w for w in r2.warnings)
    r2.assign_bus_load(sysm["bus_array"][2], 0 * ones, 0 * ones)


def test_set_volumes_interpolates() -> None:
    """eini defaults to the previous efin / planning eini; blocks are linear."""
    r = ImportedResults(tiny_planning(), "t")
    vfin = np.array([[400.0, 300.0], [450.0, 200.0]])
    r.set_volumes(1, None, vfin)
    eini = r.stage_data[("Reservoir", "eini_sol")][1]
    assert eini.tolist() == [[500.0, 400.0], [500.0, 450.0]]
    vol = r.block_data[("Reservoir", "energy_sol")][1]
    # stage 1: blocks of 10 h and 14 h, 500 -> 400
    assert vol[0, 0] == pytest.approx(500.0 - 100.0 * 10 / 24)
    assert vol[0, 1] == pytest.approx(400.0)
    p = tiny_planning()
    p["system"]["reservoir_array"][0]["eini"] = "file"
    r2 = ImportedResults(p, "t")
    r2.set_volumes(1, None, vfin)
    assert r2.stage_data[("Reservoir", "eini_sol")][1][0, 0] == 400.0
    r2.set_volumes(1, vfin, vfin)


def test_estimate_cost(tmp_path: Path) -> None:
    """generation x gcost + deficit x fail cost, per block hours."""
    r = ImportedResults(tiny_planning(), "t", case_dir=tmp_path)
    r.add_blocks("Generator", "generation_sol", 4, np.ones((2, 4)))
    r.add_blocks("Demand", "fail_sol", 2, np.ones((2, 4)))
    r.estimate_cost()
    assert r.cost is not None
    assert r.cost[0, 0] == pytest.approx((50.0 + 1000.0) * 10.0)
    assert r.cost[0, 2] == pytest.approx((55.0 + 1000.0) * 12.0)
    assert any("estimated" in w for w in r.warnings)
    before = r.cost.copy()
    r.estimate_cost()  # no-op once set
    assert (r.cost == before).all()


def test_find_and_load_planning(tmp_path: Path) -> None:
    """The converter's JSON: <dir>/<dir>.json, else the only planning JSON."""
    case = tmp_path / "case"
    case.mkdir()
    (case / "x.json").write_text(json.dumps(tiny_planning()))
    (case / "other.json").write_text("{}")
    (case / "broken.json").write_text("{")
    assert find_planning_json(case) == case / "x.json"
    (case / "case.json").write_text(json.dumps(tiny_planning()))
    assert find_planning_json(case) == case / "case.json"
    (case / "case.json").unlink()
    (case / "y.json").write_text(json.dumps(tiny_planning()))
    with pytest.raises(FileNotFoundError):
        find_planning_json(case)
    assert load_planning(case / "x.json")["system"]["name"] == "tiny"
    (case / "list.json").write_text("[]")
    with pytest.raises(ValueError, match="not a planning"):
        load_planning(case / "list.json")


def test_cli_glue(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--results [PATH], --results-only, --results-output."""
    parser = argparse.ArgumentParser()
    add_results_arguments(parser, source="X", what="files", auto="input dir")
    args = parser.parse_args(["--results"])
    assert args.results == RESULTS_AUTO and not args.results_only
    args = parser.parse_args(
        ["--results", "d", "--results-only", "--results-output", "o"]
    )
    assert args.results == "d" and args.results_only
    assert results_output_dir(tmp_path, "x", None) == tmp_path / "results_x"
    assert results_output_dir(tmp_path, "x", Path("o")) == Path("o")
    r = _filled(tiny_planning())
    report_import(r, tmp_path)
    assert "imported test results: 3 stream(s)" in capsys.readouterr().out
    assert per_hour_to_mw(np.array([1.0, 1.0]), [10.0, 0.0]).tolist() == [100.0, 0.0]


def test_no_cost_and_empty_streams(tmp_path: Path) -> None:
    """No cost -> obj_value 0; an empty stream is skipped; warnings listed."""
    p = copy.deepcopy(tiny_planning())
    r = ImportedResults(p, "t")
    r.block_data[("Line", "flow_sol")] = {}
    r.add_blocks("Line", "loss_sol", 1, np.zeros((2, 4)))
    r.warn("something")
    r.write(tmp_path)
    assert not (tmp_path / "Line/flow_sol.parquet").exists()
    assert pd.read_csv(tmp_path / "solution.csv")["obj_value"].tolist() == [0.0]
    assert "something" in (tmp_path / "README.md").read_text()
