# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``tcheby2gtopt convert --commitment-breaks`` (daily commitment grid)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tcheby2gtopt.convert import ConvertOptions, convert
from tcheby2gtopt.main import main
from tcheby2gtopt.tcheby_db import read_system
from tcheby2gtopt.timegrid import TimeGrid

TZ = datetime(2016, 1, 1)
STUDY = [3.5, 7.5, 10.5, 14.5, 17.5, 23.5]


def test_default_unchanged(reszone_db: Path) -> None:
    """Without the option the output keeps the per-unit class grids and
    no start_hour / commitment_breaks."""
    sysm = read_system(reszone_db, TZ, name="reszone")
    pt = convert(sysm, TimeGrid("point", [24.0], nodes=24))
    assert "commitment_breaks" not in pt.planning["options"]["model_options"]
    st = pt.planning["simulation"]["stage_array"][0]
    assert "start_hour" not in st
    assert "commitment_breaks" not in st
    assert "commitment_period" in pt.planning["system"]["commitment_array"][0]


def test_daily_grid_point(reszone_db: Path) -> None:
    """The daily grid replaces the per-unit class grids on point stages;
    start_hour is the clock hour of every stage start."""
    sysm = read_system(reszone_db, TZ, name="reszone")
    pt = convert(
        sysm,
        TimeGrid("point", [24.0, 24.0], nodes=24),
        None,
        ConvertOptions(commitment_breaks=STUDY, start_hour=12.0),
    )
    mo = pt.planning["options"]["model_options"]
    assert mo["commitment_breaks"] == STUDY
    for st in pt.planning["simulation"]["stage_array"]:
        assert st["start_hour"] == 12.0
        assert "commitment_breaks" not in st
    uc = pt.planning["system"]["commitment_array"][0]
    assert "commitment_period" not in uc
    assert uc["ramp_allowance"] == "boundary"
    assert any("daily grid" in n for n in pt.notes)


def test_daily_grid_explicit_period_override_wins(reszone_db: Path) -> None:
    sysm = read_system(reszone_db, TZ, name="reszone")
    name = sysm.commitments[0].name
    pt = convert(
        sysm,
        TimeGrid("point", [24.0], nodes=24),
        None,
        ConvertOptions(commitment_breaks=STUDY, commitment_periods={name: 0.5}),
    )
    uc = pt.planning["system"]["commitment_array"][0]
    assert uc["commitment_period"] == 1  # floored at 1 h on point stages


def test_daily_grid_block_stages(reszone_db: Path) -> None:
    """Block stages share the option (cells by block midpoint)."""
    sysm = read_system(reszone_db, TZ, name="reszone")
    blk = convert(
        sysm,
        TimeGrid("block", [24.0]),
        None,
        ConvertOptions(commitment_breaks=STUDY),
    )
    assert blk.planning["options"]["model_options"]["commitment_breaks"] == STUDY
    assert blk.planning["simulation"]["stage_array"][0]["start_hour"] == 0.0
    assert "commitment_period" not in blk.planning["system"]["commitment_array"][0]


def test_per_month_table(reszone_db: Path) -> None:
    """Stage k's month is the month of time_zero + its start offset;
    months missing from the table fall back to the default."""
    sysm = read_system(reszone_db, TZ, name="reszone")
    winter = [4.5, 8.5, 11.5, 14.5, 16.5, 23.5]
    # stages: Jan 31 (24 h) and Feb 1 (24 h) from 2016-01-31 00:00
    grid = TimeGrid("point", [24.0, 24.0], nodes=12)
    opts = ConvertOptions(
        commitment_breaks=STUDY,
        commitment_breaks_by_month={2: winter},
        time_zero=TZ,
        start_hour=30 * 24.0,
    )
    pt = convert(sysm, grid, None, opts)
    st = pt.planning["simulation"]["stage_array"]
    assert "commitment_breaks" not in st[0]  # January: the default
    assert st[1]["commitment_breaks"] == winter
    assert [s["start_hour"] for s in st] == [0.0, 0.0]
    # without a default a missing month is an error
    opts.commitment_breaks = None
    with pytest.raises(ValueError, match="no row for month 1"):
        convert(sysm, grid, None, opts)
    # the table needs the calendar
    with pytest.raises(ValueError, match="time_zero"):
        convert(
            sysm,
            grid,
            None,
            ConvertOptions(commitment_breaks_by_month={1: STUDY}),
        )


def test_cli_flags(reszone_db: Path, tmp_path: Path) -> None:
    table = tmp_path / "months.csv"
    table.write_text("month,breaks\njan,4.5 8.5 11.5 14.5 16.5 23.5\n")
    rc = main(
        [
            "convert",
            str(reszone_db),
            "--name",
            "rz",
            "--out-dir",
            str(tmp_path),
            "--hours",
            "24",
            "--commitment-breaks",
            "study",
            "--commitment-breaks-file",
            str(table),
            "--start-hour",
            "6",
        ]
    )
    assert rc == 0
    pnt = json.loads((tmp_path / "rz_point.json").read_text())
    assert pnt["options"]["model_options"]["commitment_breaks"] == STUDY
    st = pnt["simulation"]["stage_array"][0]
    assert st["start_hour"] == 6.0
    assert st["commitment_breaks"] == [4.5, 8.5, 11.5, 14.5, 16.5, 23.5]


def test_cli_rejects_bad_breaks(reszone_db: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "convert",
                str(reszone_db),
                "--name",
                "rz",
                "--out-dir",
                str(tmp_path),
                "--commitment-breaks",
                "6,3",
            ]
        )
