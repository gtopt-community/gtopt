# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for :mod:`plexos2gtopt.isp_traces`."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from plexos2gtopt.isp_traces import (
    TimesliceCalendar,
    TraceLocator,
    _nearest,
    _shift_year,
    _to_hourly,
    parse_month_pattern,
    read_interval_trace,
    read_yearly_column,
)


def test_locator_empty_and_bare_traces(tmp_path: Path) -> None:
    loc = TraceLocator([tmp_path])
    assert loc.resolve("   ") is None
    (tmp_path / "Traces").write_text("x")
    # A single "Traces" part: no stripped tail is added, the file is found.
    assert loc.resolve("Traces") == tmp_path / "Traces"
    assert loc.resolve("Traces\\solar\\missing.csv") is None


def test_locator_layouts(tmp_path: Path) -> None:
    (tmp_path / "solar").mkdir()
    (tmp_path / "solar" / "a.csv").write_text("x")
    (tmp_path / "b.csv").write_text("x")
    loc = TraceLocator([str(tmp_path)])
    assert loc.resolve("Traces\\solar\\a.csv") == tmp_path / "solar" / "a.csv"
    assert loc.resolve("Traces\\wind\\b.csv") == tmp_path / "b.csv"


def test_interval_trace_short_rows_and_missing_day(tmp_path: Path) -> None:
    p = tmp_path / "daily.csv"
    p.write_text("Year,Month,Day,Value\n2024,1,1,5\n2024,1\n2024,1,2,\n")
    out = read_interval_trace(p, date(2024, 1, 1), 2)
    assert out[:24].tolist() == [5.0] * 24
    assert out[24:].tolist() == [0.0] * 24
    with pytest.raises(KeyError, match="no row"):
        read_interval_trace(p, date(2024, 1, 3), 1, fill="none")


def test_interval_trace_nearest_year_shift(tmp_path: Path) -> None:
    p = tmp_path / "hh.csv"
    vals = ",".join(str(i) for i in range(48))
    p.write_text(
        "Year,Month,Day," + ",".join(f"{i:02d}" for i in range(1, 49)) + "\n"
        f"2020,2,28,{vals}\n2021,3,1,{vals}\n"
    )
    # 2019-02-28 -> shifted forward to 2020-02-28.
    early = read_interval_trace(p, date(2019, 2, 28), 1)
    assert early[0] == pytest.approx(0.5)
    # 2024-02-29 -> shifted back (29 Feb clamps to 28) to 2021-02-28: absent.
    with pytest.raises(KeyError):
        read_interval_trace(p, date(2024, 2, 29), 1)


def test_nearest_empty() -> None:
    assert _nearest({}, date(2024, 1, 1)) is None


def test_shift_year_leap() -> None:
    assert _shift_year(date(2024, 2, 29), 1) == date(2025, 2, 28)


def test_to_hourly_shapes() -> None:
    assert _to_hourly([1.0] * 24).shape == (24,)
    np.testing.assert_allclose(_to_hourly([2.0]), np.full(24, 2.0))
    with pytest.raises(ValueError, match="unsupported interval"):
        _to_hourly([1.0, 2.0])


def test_yearly_column(tmp_path: Path) -> None:
    p = tmp_path / "y.csv"
    p.write_text("Year, G1, G2\n2025,1,\n2027,3,4\n\n")
    assert read_yearly_column(p, 2026, "G1") == 1.0
    assert read_yearly_column(p, 2030, "G2") == 4.0
    assert read_yearly_column(p, 2020, "G1") == 1.0
    assert read_yearly_column(p, 2025, "G2") == 0.0
    assert read_yearly_column(p, 2025, "nope") is None
    empty = tmp_path / "e.csv"
    empty.write_text("Year,G1\n")
    assert read_yearly_column(empty, 2025, "G1") is None


def test_month_pattern() -> None:
    assert parse_month_pattern("M1-3,,11") == {1, 2, 3, 11}
    assert parse_month_pattern("D1,H1") is None


def test_calendar(tmp_path: Path) -> None:
    cal = TimesliceCalendar()
    assert cal.add_month_name("M3")
    assert not cal.add_month_name("M13")
    assert not cal.add_month_name("Peak")
    p = tmp_path / "ts.csv"
    p.write_text(
        "DATETIME,NAME,TIMESLICE\n"
        "01/06/2024 00:00,Hot,-1\n"
        "10/06/2024 00:00,Hot,0\n"
        "01/06/2024 00:00,Cold,-1\n"
    )
    cal.add_state_csv(p, names=["Hot"])
    assert cal.is_active("M3", date(2024, 3, 5))
    assert not cal.is_active("Hot", date(2024, 5, 31))
    assert cal.is_active("Hot", date(2024, 6, 5))
    assert not cal.is_active("Hot", date(2024, 6, 15))
    assert not cal.is_active("Cold", date(2024, 6, 5))
    assert cal.known("Hot") and not cal.known("Cold")
    cal2 = TimesliceCalendar()
    cal2.add_state_csv(p)
    assert cal2.known("Cold")
