# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for :mod:`plexos2gtopt.plexos_csv`."""

from __future__ import annotations

from pathlib import Path

from plexos2gtopt.plexos_csv import read_long, read_wide


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_read_long_empty_file(tmp_path: Path) -> None:
    assert not read_long(_write(tmp_path / "e.csv", ""))


def test_read_long_fill_forward_leading_gap(tmp_path: Path) -> None:
    csv = _write(
        tmp_path / "l.csv",
        "﻿NAME,YEAR,MONTH,DAY,PERIOD,BAND,VALUE\n"
        "L1,2025,1,1,2,1,5\n"
        "L1,2025,1,1,4,1,7\n"
        "L1,2025,1,1,9,1,1\n"  # out of range
        "L1,2025,1,1,x,1,1\n"  # bad period
        ",2025,1,1,1,1,1\n"  # no name
        "L1,2025,1,1,1,z,1\n"  # bad band
        "L1,2025,1,1,1,2,99\n",  # other band
    )
    out = read_long(csv, periods=5, fill_forward=True)
    assert out == {"L1": [0.0, 5.0, 5.0, 7.0, 7.0]}


def test_read_long_day_window(tmp_path: Path) -> None:
    csv = _write(
        tmp_path / "l.csv",
        "NAME,YEAR,MONTH,DAY,PERIOD,BAND,VALUE\n"
        "G,2025,1,1,1,1,1\n"
        "G,2025,1,2,1,1,2\n"
        "G,2025,1,3,1,1,3\n",
    )
    assert read_long(csv, periods=1, n_days=1, day_offset=1) == {"G": [2.0]}


def test_read_wide_empty_file(tmp_path: Path) -> None:
    assert not read_wide(_write(tmp_path / "e.csv", ""))


def test_read_wide_skips_bad_rows(tmp_path: Path) -> None:
    csv = _write(
        tmp_path / "w.csv",
        "YEAR,MONTH,DAY,PERIOD,BAND,A,B,Z\n"
        "2025,1,1,1,1,1.5,,0\n"
        "2025,1,1,2,x,9,9,0\n"  # bad band
        "2025,1,1,2,2,9,9,0\n"  # other band
        "2025,1,1,y,1,9,9,0\n"  # bad period
        "2025,1,1,7,1,9,9,0\n"  # out-of-range period
        "2025,1,1,2,1,abc,4,0\n"  # bad value
        "2025,1,2,1,1,8,8,0\n",  # beyond n_days
    )
    out = read_wide(csv, periods=2)
    assert out == {"A": [1.5, 0.0], "B": [0.0, 4.0]}
    kept = read_wide(csv, periods=2, drop_zero_cols=False)
    assert kept["Z"] == [0.0, 0.0]


def test_read_wide_no_band_column(tmp_path: Path) -> None:
    csv = _write(
        tmp_path / "w.csv",
        "YEAR,MONTH,DAY,PERIOD,A\n2025,1,1,1,3\n2025,1,2,1,4\n",
    )
    assert read_wide(csv, periods=1, n_days=2) == {"A": [3.0, 4.0]}
