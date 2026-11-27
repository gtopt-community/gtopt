# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests of :mod:`gtopt_shared.results_reader` on synthetic gtopt outputs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gtopt_shared.case_reader import TimeGrid
from gtopt_shared.results_reader import GtoptResults, find_stream
from gtopt_shared.testing.tiny_cases import tiny_planning, write_tiny_results

LAYOUTS = ("parquet", "hive", "csv", "csv.zst", "wide")


@pytest.fixture(name="grid")
def _grid() -> TimeGrid:
    return TimeGrid.from_planning(tiny_planning())


@pytest.mark.parametrize("layout", LAYOUTS)
def test_blocks_every_layout(tmp_path: Path, grid: TimeGrid, layout: str) -> None:
    """Each on-disk layout reads to the same (scenario, block) matrix."""
    res = GtoptResults(write_tiny_results(tmp_path, layout), grid)
    gen = res.blocks("Generator", "generation_sol")
    assert sorted(gen) == [1, 4, 8]
    expect = np.array([[10 * s + b for b in (1, 2, 3, 4)] for s in (1, 2)], float)
    np.testing.assert_allclose(gen[4], expect)
    # dropped zeros read back as 0.0
    np.testing.assert_allclose(gen[8][:, 2:], 0.0)
    assert res.has("Bus", "balance_dual")
    assert not res.has("Bus", "nothing_sol")


@pytest.mark.parametrize("layout", LAYOUTS)
def test_stages(tmp_path: Path, grid: TimeGrid, layout: str) -> None:
    """Stage streams (block 0) give one value per scenario x stage."""
    res = GtoptResults(write_tiny_results(tmp_path, layout), grid)
    efin = res.stage_of("Reservoir", "efin_sol", 1)
    np.testing.assert_allclose(efin, [[450.0, 400.0], [480.0, 300.0]])
    # a per-block stream read per stage = its last block of each stage
    wv = res.stage_of("Reservoir", "water_value_dual", 1)
    np.testing.assert_allclose(wv, [[1000.0, 1000.0], [2000.0, 2000.0]])


def test_missing_and_absent(tmp_path: Path, grid: TimeGrid) -> None:
    """Absent streams / uids read as zeros; ``None`` uid too."""
    res = GtoptResults(write_tiny_results(tmp_path, "parquet"), grid)
    assert res.table("Nope", "x_sol") is None
    assert not res.blocks("Nope", "x_sol")
    assert not res.stages("Nope", "x_sol")
    np.testing.assert_allclose(res.block_of("Generator", "generation_sol", 99), 0.0)
    np.testing.assert_allclose(res.block_of("Generator", "generation_sol", None), 0.0)
    np.testing.assert_allclose(res.stage_of("Reservoir", "efin_sol", None), 0.0)
    np.testing.assert_allclose(res.stage_of("Reservoir", "efin_sol", 7), 0.0)
    assert res.block_of("Generator", "generation_sol", None).shape == (2, 4)
    assert res.stage_of("Reservoir", "efin_sol", 7).shape == (2, 2)


def test_empty_stream_and_no_scenario(tmp_path: Path, grid: TimeGrid) -> None:
    """An empty file is present-but-empty; a frame without scenario
    applies to every scenario; a stage stream without a stage column
    is reported and skipped."""
    d = tmp_path / "Bus"
    d.mkdir()
    (d / "balance_dual_s0_p0.csv").write_text(
        "scenario,stage,block,uid,value\n", encoding="utf-8"
    )
    pd.DataFrame({"block": [1, 2], "uid": [3, 3], "value": [7.0, 8.0]}).to_csv(
        d / "x_sol.csv", index=False
    )
    pd.DataFrame({"stage": [2], "uid": [3], "value": [9.0]}).to_csv(
        d / "st_sol.csv", index=False
    )
    pd.DataFrame({"uid": [3], "value": [9.0]}).to_csv(d / "flat_sol.csv", index=False)
    res = GtoptResults(tmp_path, grid)
    assert res.has("Bus", "balance_dual")
    assert not res.blocks("Bus", "balance_dual")
    assert not res.stages("Bus", "balance_dual")
    x = res.block_of("Bus", "x_sol", 3)
    np.testing.assert_allclose(x, [[7.0, 8.0, 0.0, 0.0]] * 2)
    st = res.stage_of("Bus", "st_sol", 3)
    np.testing.assert_allclose(st, [[0.0, 9.0]] * 2)
    assert not res.stages("Bus", "flat_sol")
    assert not res.blocks("Bus", "flat_sol")


def test_find_stream_precedence(tmp_path: Path) -> None:
    """Parquet beats CSV shards; shards beat a single file; none -> []."""
    d = tmp_path / "C"
    d.mkdir()
    assert not find_stream(d, "f")
    (d / "f.csv").write_text("uid,value\n1,1\n", encoding="utf-8")
    assert find_stream(d, "f") == [d / "f.csv"]
    (d / "f_s0_p0.csv").write_text("uid,value\n1,1\n", encoding="utf-8")
    (d / "f_s1_p0.csv").write_text("uid,value\n1,1\n", encoding="utf-8")
    assert len(find_stream(d, "f")) == 2
    pd.DataFrame({"uid": [1], "value": [1.0]}).to_parquet(d / "f_s0_p0.parquet")
    assert find_stream(d, "f") == [d / "f_s0_p0.parquet"]
    pd.DataFrame({"uid": [1], "value": [1.0]}).to_parquet(d / "f.parquet")
    assert find_stream(d, "f") == [d / "f.parquet"]


def test_unknown_layout(tmp_path: Path) -> None:
    """The synthetic writer rejects an unknown layout."""
    with pytest.raises(ValueError, match="unknown layout"):
        write_tiny_results(tmp_path, "xml")
