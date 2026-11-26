# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Towers -> spans geometry and CSV round trips."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from dlr2gtopt._spans import (
    Tower,
    bearing_deg,
    build_spans,
    haversine_m,
    read_spans,
    read_towers,
    write_spans,
    write_towers,
)
from dlr2gtopt.tests._synthetic import make_towers


def test_haversine_and_bearing() -> None:
    assert haversine_m(0, 0, 0, 1) == pytest.approx(111_195, rel=1e-3)
    assert bearing_deg(0, 0, 1, 0) == pytest.approx(0.0)
    assert bearing_deg(0, 0, 0, 1) == pytest.approx(90.0)
    assert bearing_deg(0, 0, -1, 0) == pytest.approx(180.0)
    assert bearing_deg(0, 0, 0, -1) == pytest.approx(270.0)


def test_build_spans_folds_azimuth_and_merges_duplicates() -> None:
    towers = [
        Tower("A", 2, -31.0, -71.0),
        Tower("A", 0, -31.01, -71.0, 10.0),
        Tower("A", 1, -31.005, -71.0, 20.0),
        Tower("A", 3, -31.0, -71.0),  # duplicate of seq 2
        Tower("B", 0, 0.0, 0.0),
        Tower("B", 1, 0.0, -0.01),
    ]
    spans = build_spans(towers)
    a = [s for s in spans if s.line == "A"]
    b = [s for s in spans if s.line == "B"]
    assert len(a) == 2
    assert a[0].azimuth == pytest.approx(0.0, abs=1e-6)
    assert a[0].elevation == pytest.approx(15.0)
    assert a[1].elevation is None
    assert a[1].chainage_km == pytest.approx((a[0].length_m + a[1].length_m / 2) / 1000)
    assert b[0].azimuth == pytest.approx(90.0)  # westward folded to 90


def test_towers_spans_csv_round_trip(tmp_path: Path) -> None:
    towers = make_towers(6)
    write_towers(tmp_path / "t.csv", towers)
    back = read_towers(tmp_path / "t.csv")
    assert [t.seq for t in back] == [t.seq for t in towers]
    spans = build_spans(back)
    write_spans(tmp_path / "s.csv", spans)
    again = read_spans(tmp_path / "s.csv")
    assert len(again) == 5
    assert again[2].azimuth == pytest.approx(spans[2].azimuth, abs=0.01)
    assert again[0].elevation == pytest.approx(100.5)


def test_read_towers_line_argument(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text("tower_seq,lat,lon\n0,-31,-71\n1,-31.003,-71\n", encoding="utf-8")
    assert read_towers(p, "X")[0].line == "X"
    with pytest.raises(ValueError, match="no 'line'"):
        read_towers(p)


def test_spans_height_column_round_trip(tmp_path: Path) -> None:
    """The optional per-span ``height`` column survives write/read; old
    files without it read as ``None``."""
    spans = build_spans(make_towers(4))
    spans[1] = dataclasses.replace(spans[1], height=21.5)
    write_spans(tmp_path / "s.csv", spans)
    back = read_spans(tmp_path / "s.csv")
    assert back[1].height == pytest.approx(21.5)
    assert back[0].height is None
    old = tmp_path / "old.csv"
    old.write_text(
        "line,span,lat,lon,azimuth,length_m,chainage_km\nL,1,-33,-70,0,300,0\n",
        encoding="utf-8",
    )
    assert read_spans(old)[0].height is None
