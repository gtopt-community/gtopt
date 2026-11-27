# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for :mod:`gtopt_shared.case_reader` and :mod:`gtopt_shared.case_compare`."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gtopt_shared.case_compare import compare_cases, summarize
from gtopt_shared.case_reader import GtoptCase, TimeGrid, per_stage
from gtopt_shared.testing.tiny_cases import tiny_planning, write_tiny_case


def _case(planning: dict, tmp_path: Path) -> GtoptCase:
    path = tmp_path / "c.json"
    path.write_text(json.dumps(planning), encoding="utf-8")
    return GtoptCase.load(path)


def test_time_grid_defaults() -> None:
    grid = TimeGrid.from_planning({})
    assert grid.n_scenarios == grid.n_stages == grid.n_blocks == 1
    assert grid.durations == (1.0,)
    assert list(grid.stage_of_block()) == [0]


def test_time_grid_tiny() -> None:
    grid = TimeGrid.from_planning(tiny_planning())
    assert (grid.n_scenarios, grid.n_stages, grid.n_blocks) == (2, 2, 4)
    assert list(grid.stage_of_block()) == [0, 0, 1, 1]
    assert list(grid.stages[1].blocks) == [2, 3]
    assert grid.stages[0].month == "april"


def test_field_inline_forms(tmp_path: Path) -> None:
    case = GtoptCase.load(write_tiny_case(tmp_path))
    gens = case.ref_index(case.elements("generator_array"))
    termo = gens["TERMO"]
    # scalar
    assert np.all(case.field("Generator", gens["LAGO"], "pmax") == 100.0)
    # stage x block (TB)
    pmax = case.field("Generator", termo, "pmax")
    assert pmax.shape == (2, 4)
    assert list(pmax[0]) == [150.0, 150.0, 150.0, 75.0]
    assert np.array_equal(pmax[0], pmax[1])
    # scenario x stage x block (STB)
    flow = case.elements("flow_array")[0]
    q = case.field("Flow", flow, "discharge")
    assert list(q[0]) == [20.0, 20.0, 25.0, 25.0]
    assert list(q[1]) == [15.0, 15.0, 10.0, 10.0]
    # absent field -> default
    assert np.all(case.field("Generator", termo, "nope", default=-1.0) == -1.0)
    assert np.isnan(case.field("Generator", termo, "nope")).all()


def test_field_per_stage_array_and_bad_shape(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    planning = tiny_planning()
    planning["system"]["generator_array"][0]["pmin"] = [1.0, 2.0]
    planning["system"]["generator_array"][0]["gcost"] = [1.0, 2.0, 3.0]
    case = _case(planning, tmp_path)
    termo = case.elements("generator_array")[0]
    assert list(case.field("Generator", termo, "pmin")[0]) == [1.0, 1.0, 2.0, 2.0]
    out = case.field("Generator", termo, "gcost", default=0.0)
    assert np.all(out == 0.0)
    assert "shape not recognised" in caplog.text


def test_field_tables(tmp_path: Path) -> None:
    planning = tiny_planning()
    gens = planning["system"]["generator_array"]
    gens[0]["pmax"] = "pmax"
    gens[1]["pmax"] = "Other@cap"
    gens[2]["pmax"] = "wide"
    (tmp_path / "Generator").mkdir()
    (tmp_path / "Other").mkdir()
    # long, per block, no scenario
    pd.DataFrame(
        {"block": [1, 2, 3, 4], "uid": [4] * 4, "value": [1.0, 2.0, 3.0, 4.0]}
    ).to_parquet(tmp_path / "Generator" / "pmax.parquet")
    # long, per scenario and stage (csv, redirected class)
    pd.DataFrame(
        {
            "scenario": [1, 1, 2, 2],
            "stage": [1, 2, 1, 2],
            "uid": [1] * 4,
            "value": [10.0, 20.0, 30.0, 40.0],
        }
    ).to_csv(tmp_path / "Other" / "cap.csv", index=False)
    # legacy wide
    pd.DataFrame({"stage": [1, 2], "uid:8": [5.0, 6.0]}).to_parquet(
        tmp_path / "Generator" / "wide.parquet"
    )
    case = _case(planning, tmp_path)
    g = case.elements("generator_array")
    assert list(case.field("Generator", g[0], "pmax")[1]) == [1.0, 2.0, 3.0, 4.0]
    cap = case.field("Generator", g[1], "pmax")
    assert list(cap[0]) == [10.0, 10.0, 20.0, 20.0]
    assert list(cap[1]) == [30.0, 30.0, 40.0, 40.0]
    assert list(case.field("Generator", g[2], "pmax")[0]) == [5.0, 5.0, 6.0, 6.0]
    assert not case.missing


def test_field_table_missing_and_uid(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    planning = tiny_planning()
    planning["system"]["generator_array"][0]["pmax"] = "absent"
    planning["system"]["generator_array"][1]["pmax"] = "pmax"
    (tmp_path / "Generator").mkdir()
    pd.DataFrame({"uid": [99], "value": [1.0]}).to_parquet(
        tmp_path / "Generator" / "pmax.parquet"
    )
    case = _case(planning, tmp_path)
    g = case.elements("generator_array")
    assert np.all(case.field("Generator", g[0], "pmax", default=7.0) == 7.0)
    assert case.missing
    assert np.all(case.field("Generator", g[1], "pmax", default=3.0) == 3.0)
    assert "not in table" in caplog.text


def test_per_stage_reductions() -> None:
    grid = TimeGrid.from_planning(tiny_planning())
    arr = np.array([[1.0, 2.0, 3.0, 5.0]])
    assert per_stage(grid, arr, "max").tolist() == [[2.0, 5.0]]
    assert per_stage(grid, arr, "min").tolist() == [[1.0, 3.0]]
    assert per_stage(grid, arr, "first").tolist() == [[1.0, 3.0]]
    mean = per_stage(grid, arr, "mean")[0]
    assert mean[0] == pytest.approx((1 * 10 + 2 * 14) / 24)
    assert mean[1] == pytest.approx(4.0)


def test_compare_identical_and_changed(tmp_path: Path) -> None:
    a = GtoptCase.load(write_tiny_case(tmp_path / "a"))
    assert not compare_cases(a, a)
    summary = summarize(a)
    # profile caps the solar availability: 0.8 * 50 in block 1
    assert summary["gen.pmax@PV1"][0, 0] == pytest.approx(40.0)
    assert summary["inflow@LAGO"][1, 2] == pytest.approx(10.0)
    planning = copy.deepcopy(tiny_planning())
    planning["system"]["demand_array"][0]["lmax"] = [[61.0, 80.0], [65.0, 85.0]]
    del planning["system"]["battery_array"]
    b = _case(planning, tmp_path)
    diffs = compare_cases(a, b)
    assert any(d.startswith("demand@1") for d in diffs)
    assert any("only in first" in d for d in diffs)
