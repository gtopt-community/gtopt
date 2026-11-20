# SPDX-License-Identifier: BSD-3-Clause
"""Tests for gtopt_shared.time_grid."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gtopt_shared.time_grid import (
    DEFAULT_START,
    BlockGrid,
    TimeGrid,
    calendar_stage_of_hour,
    chronological_groups,
    hour_of_day_groups,
    hour_timestamps,
    ldc_groups,
    parse_hour_range,
    write_block_schedule,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, (0, 100)),
        ("", (0, 100)),
        (":", (0, 100)),
        ("10:20", (10, 20)),
        ("90:", (90, 100)),
        (":5", (0, 5)),
        ("24+48", (24, 72)),
    ],
)
def test_parse_hour_range(text, expected) -> None:
    assert parse_hour_range(text, 100) == expected


@pytest.mark.parametrize("text", ["5", "a:b", "20:10", "0:101", "-1:4", "99+5"])
def test_parse_hour_range_errors(text) -> None:
    with pytest.raises(ValueError):
        parse_hour_range(text, 100)


def test_grid_last_stage_shorter() -> None:
    grid = TimeGrid(n_hours=8760, stage_hours=168)
    assert grid.n_stages == 53
    assert grid.stage_ranges()[-1] == (52 * 168, 24)
    sim = grid.simulation()
    assert len(sim["phase_array"]) == 53
    assert grid.block_stage_uids()[-1] == 53


def test_grid_single_stage_no_phases() -> None:
    sim = TimeGrid(n_hours=24).simulation()
    assert len(sim["stage_array"]) == 1
    assert "phase_array" not in sim
    assert "phase_array" not in TimeGrid(48, 1, 24).simulation(
        one_phase_per_stage=False
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"n_hours": 0},
        {"n_hours": 10, "block_hours": 0},
        {"n_hours": 10, "block_hours": 3},
        {"n_hours": 12, "block_hours": 4, "stage_hours": 6},
        {"n_hours": 12, "stage_hours": 0},
    ],
)
def test_grid_validation(kwargs) -> None:
    with pytest.raises(ValueError):
        TimeGrid(**kwargs)


def test_aggregate() -> None:
    grid = TimeGrid(n_hours=6, block_hours=3)
    np.testing.assert_allclose(grid.aggregate(np.arange(6.0)), [1.0, 4.0])
    two = grid.aggregate(np.stack([np.arange(6.0), np.ones(6)], axis=1))
    assert two.shape == (2, 2)
    with pytest.raises(ValueError):
        grid.aggregate(np.zeros(5))


def test_write_block_schedule(tmp_path: Path) -> None:
    grid = TimeGrid(n_hours=4, block_hours=1, stage_hours=2)
    path = write_block_schedule(
        tmp_path / "Demand", "lmax", {3: np.array([1.0, 2, 3, 4])}, grid
    )
    df = pd.read_parquet(path)
    assert list(df.columns) == ["stage", "block", "uid", "value"]
    assert df["stage"].tolist() == [1, 1, 2, 2]
    assert df["uid"].unique().tolist() == [3]
    path = write_block_schedule(
        tmp_path, "profile", {1: np.ones(4)}, grid, with_scenario=True
    )
    assert pd.read_parquet(path)["scenario"].unique().tolist() == [1]
    with pytest.raises(ValueError):
        write_block_schedule(tmp_path, "bad", {1: np.ones(3)}, grid)
    with pytest.raises(ValueError):
        write_block_schedule(tmp_path, "empty", {}, grid)


# ---------------------------------------------------------------------------
# BlockGrid + calendar / ldc / hour-of-day groupings
# ---------------------------------------------------------------------------


def test_calendar_months() -> None:
    times = hour_timestamps(8760, DEFAULT_START)
    stage = calendar_stage_of_hour(times, "month")
    assert np.bincount(stage).tolist()[:3] == [744, 672, 744]
    assert stage.max() == 11
    leap = calendar_stage_of_hour(hour_timestamps(24 * 60, "2024-02-01"), "month")
    assert np.bincount(leap).tolist() == [696, 744]
    assert calendar_stage_of_hour(times[:400], "week").max() == 2
    assert calendar_stage_of_hour(times[:48], "day").tolist()[24] == 1
    quarters = calendar_stage_of_hour(times, "quarter")
    assert np.bincount(quarters).tolist() == [2160, 2184, 2208, 2208]
    two_years = hour_timestamps(8760 + 8784, DEFAULT_START)
    assert np.bincount(calendar_stage_of_hour(two_years, "year")).tolist() == [
        8760,
        8784,
    ]
    with pytest.raises(ValueError):
        calendar_stage_of_hour(times, "fortnight")


def test_ldc_groups_hours_and_energy() -> None:
    stage = np.repeat([0, 1], 10)
    key = np.concatenate([np.arange(10.0), np.arange(10.0)[::-1]])
    grp = ldc_groups(stage, key, 5)
    assert grp[:10].tolist() == [4, 4, 3, 3, 2, 2, 1, 1, 0, 0]  # peak → group 0
    assert grp[10:].tolist() == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]
    energy = ldc_groups(np.zeros(10, dtype=int), np.arange(10.0), 3, split="energy")
    sizes = np.bincount(energy)
    assert sizes.sum() == 10 and sizes[0] < sizes[-1]
    assert ldc_groups(np.zeros(2, dtype=int), np.ones(2), 5).tolist() == [0, 1]
    with pytest.raises(ValueError):
        ldc_groups(stage, key, 0)
    with pytest.raises(ValueError):
        ldc_groups(stage, key, 2, split="area")


def test_hour_of_day_and_chrono_groups() -> None:
    assert hour_of_day_groups(np.arange(24), 12).tolist() == list(np.arange(24) // 2)
    with pytest.raises(ValueError):
        hour_of_day_groups(np.arange(24), 25)
    assert chronological_groups(np.repeat([0, 1], 4), 2).tolist() == [0, 0, 1, 1] * 2
    with pytest.raises(ValueError):
        chronological_groups(np.repeat([0, 1], 3), 2)


def test_block_grid_basics() -> None:
    stage = np.repeat([0, 1], 6)
    key = np.array([5, 1, 3, 2, 6, 4, 1, 2, 3, 4, 5, 6], dtype=float)
    grid = BlockGrid.from_groups(stage, ldc_groups(stage, key, 3))
    assert grid.n_blocks == 6 and grid.n_stages == 2 and grid.n_hours == 12
    assert grid.stage_ranges() == [(0, 3), (3, 3)]
    assert grid.block_durations().tolist() == [2.0] * 6
    agg = grid.aggregate(key)
    assert agg.tolist() == [5.5, 3.5, 1.5, 5.5, 3.5, 1.5]
    assert float((agg * grid.block_durations()).sum()) == pytest.approx(key.sum())
    two = grid.aggregate(np.stack([key, 2 * key], axis=1))
    np.testing.assert_allclose(two[:, 1], 2 * agg)
    sim = grid.simulation()
    assert [b["duration"] for b in sim["block_array"]] == [2.0] * 6
    assert len(sim["phase_array"]) == 2
    bhm = grid.hour_block_map(hour_timestamps(12, "2023-05-01"), hour_offset=100)
    assert bhm["hour_index"].tolist() == list(range(100, 112))
    assert bhm["hour"].tolist()[:2] == [1, 2] and bhm["month"].iloc[0] == 5
    with pytest.raises(ValueError):
        grid.aggregate(np.zeros(3))
    with pytest.raises(ValueError):
        grid.hour_block_map(hour_timestamps(3, "2023-01-01"))


@pytest.mark.parametrize(
    ("stage", "block"),
    [
        ([0, 0, 1], [0, 1, 1]),  # block 1 spans two stages
        ([0, 1, 0], [0, 1, 0]),  # stages not non-decreasing
        ([0, 0], [0, 2]),  # gap in block numbering
        ([0, 1], [1, 0]),  # blocks not ordered by stage
        ([], []),
    ],
)
def test_block_grid_validation(stage, block) -> None:
    with pytest.raises(ValueError):
        BlockGrid(np.asarray(stage, dtype=int), np.asarray(block, dtype=int))


def test_time_grid_to_block_grid() -> None:
    tgrid = TimeGrid(n_hours=48, block_hours=2, stage_hours=24)
    bgrid = tgrid.to_block_grid()
    assert bgrid.stage_ranges() == tgrid.stage_ranges()
    np.testing.assert_allclose(bgrid.block_durations(), tgrid.block_durations())
    values = np.arange(48.0)
    np.testing.assert_allclose(bgrid.aggregate(values), tgrid.aggregate(values))
    assert bgrid.simulation() == tgrid.simulation()
