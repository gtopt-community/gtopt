"""Readers for CEN PCP per-class CSV time-series.

Two file layouts cover everything in ``DATOS{date}.zip``:

* **Long format**: ``NAME, YEAR, MONTH, DAY, PERIOD, BAND, VALUE`` —
  used by ``Gen_*.csv`` and ``Hydro_*.csv``. ``BAND`` carries
  piecewise segments (heat-rate breakpoints, multi-step bid curves).
* **Wide format**: ``YEAR, MONTH, DAY, PERIOD, <obj1>, <obj2>, …`` —
  used by ``Nod_Load.csv``.  One column per object, one row per
  period.

``PERIOD`` is 1-based hour-of-day (1..24 for the PCP daily horizon).

This module returns ``dict[name -> list[float]]`` rather than pandas
DataFrames so the parsers stay zero-dep at import time and pylint
doesn't have to chase pandas's signature changes across versions.
The conversion functions are intentionally narrow — anything that
needs DataFrame-grade processing should consume the dict and build
its own frame downstream.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import TextIO


logger = logging.getLogger(__name__)


# Canonical PCP horizon: 24 hourly periods (1..24).
DEFAULT_PERIODS = 24


def sniff_delimiter(fh: TextIO) -> str:
    """The field delimiter of an open CSV, from its header line.

    PLEXOS accepts ``;``- as well as ``,``-separated Data Files, and the
    CEN 2021 (PLEXOS 8.100) bundles ship ``Nod_Load.csv`` with ``;``
    (decimal point unchanged).  The header decides: more ``;`` than
    ``,`` means ``;``.  The stream position is restored.
    """
    pos = fh.tell()
    header = fh.readline()
    fh.seek(pos)
    return ";" if header.count(";") > header.count(",") else ","


def read_long(
    path: Path | str,
    *,
    band: int = 1,
    periods: int = DEFAULT_PERIODS,
    n_days: int = 1,
    day_offset: int = 0,
    fill_forward: bool = False,
) -> dict[str, list[float]]:
    """Parse a long-format CEN CSV into ``{name -> [v_t1, v_t2, …]}``.

    Args:
        path: CSV path (``Gen_Rating.csv``, ``Hydro_WaterFlows.csv``, …).
        band: Which BAND segment to return. Defaults to ``1`` (the
            first / primary segment); multi-band readers should call
            this once per band and merge.
        periods: Periods PER DAY (typically 24 for hourly CEN PCP).
        n_days: How many consecutive days to extract; same semantics as
            in :func:`read_wide`.  ``n_days = 1`` (default) keeps the
            legacy per-day behaviour.
        day_offset: 0-indexed offset of the first day to extract.
        fill_forward: If True, undefined slots (no row in the CSV)
            inherit the value of the last defined earlier slot.  This
            matches PLEXOS's "value carries forward until the next
            defined period" semantics — required for DLR (Dynamic
            Line Rating) sparse files like ``Lin_MaxRating.csv``
            that ship only the periods where the rating changes
            (e.g. periods 1, 7, 24 each day).  When False (default,
            legacy behaviour) undefined slots stay 0.0.

    Returns:
        Dict keyed by ``NAME``; values are lists of length
        ``periods * n_days``.  Multi-day rows are concatenated in
        calendar order (first (Y,M,D) tuple seen = day-0).
    """
    out_len = periods * n_days
    out: dict[str, list[float]] = {}
    # Defined-slot bookkeeping for fill_forward: track which (name, slot)
    # actually had a row in the CSV so we can fill the gaps later.
    defined: dict[str, list[bool]] = {}
    day_keys_seen: dict[tuple[str, str, str], int] = {}
    with Path(path).open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=sniff_delimiter(fh))
        # CEN CSVs sometimes ship a BOM on the first header column.
        if reader.fieldnames:
            reader.fieldnames = [h.lstrip("﻿") for h in reader.fieldnames]
        for row in reader:
            try:
                row_band = int(row.get("BAND", "1") or "1")
            except ValueError:
                continue
            if row_band != band:
                continue
            name = (row.get("NAME") or "").strip()
            if not name:
                continue
            try:
                period = int(row.get("PERIOD", "0") or "0")
                value = float(row.get("VALUE", "0") or "0")
            except ValueError:
                continue
            if period < 1 or period > periods:
                logger.debug("%s: skipping out-of-range period %d", path, period)
                continue
            day_key = (
                row.get("YEAR", "") or "",
                row.get("MONTH", "") or "",
                row.get("DAY", "") or "",
            )
            if day_key not in day_keys_seen:
                day_keys_seen[day_key] = len(day_keys_seen)
            day_idx = day_keys_seen[day_key] - day_offset
            if day_idx < 0 or day_idx >= n_days:
                continue
            slot = day_idx * periods + (period - 1)
            series = out.setdefault(name, [0.0] * out_len)
            series[slot] = value
            if fill_forward:
                defined.setdefault(name, [False] * out_len)[slot] = True

    if fill_forward:
        # Forward-fill: each undefined slot inherits the most recent
        # defined slot to its left.  Leading undefined slots (no
        # defined predecessor) stay at 0.0 — caller's problem if that
        # matters.
        for name, series in out.items():
            flags = defined[name]
            last_defined = 0.0
            seen_first = False
            for i in range(out_len):
                if flags[i]:
                    last_defined = series[i]
                    seen_first = True
                elif seen_first:
                    series[i] = last_defined
    return out


def read_long_stepped(
    path: Path | str,
    *,
    start: date | None,
    n_hours: int,
    periods: int = DEFAULT_PERIODS,
) -> dict[str, list[float]]:
    """Long-format Data File as an hourly step function from ``start``.

    PLEXOS applies a dated Data File value from its ``(date, PERIOD)``
    until the next row of the same ``NAME`` ("carries forward").  Each
    returned list has ``n_hours`` values: hour ``h`` (0-based) of the
    horizon takes the value of the LATEST row at or before
    ``(start + h // periods, h % periods + 1)``; hours before the first
    row take the first row.  Rows dated before ``start`` therefore only
    seed the opening value (CEN ships the day before the horizon first,
    e.g. ``Hydro_AntucoBounds.csv`` 2021-10-23 for a 2021-10-24 run), and
    intra-day ``PERIOD`` rows step within their day.  ``start=None``
    falls back to the first date in the file.
    """
    rows: dict[str, list[tuple[date, int, float]]] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=sniff_delimiter(fh))
        for row in reader:
            row = {(k or "").strip().upper(): v for k, v in row.items()}
            name = (row.get("NAME") or "").strip()
            if not name:
                continue
            try:
                when = date(int(row["YEAR"]), int(row["MONTH"]), int(row["DAY"]))
                period = int(row.get("PERIOD") or 1)
                value = float(row.get("VALUE") or "")
            except (KeyError, TypeError, ValueError):
                continue
            rows.setdefault(name, []).append((when, period, value))
    if not rows:
        return {}
    if start is None:
        start = min(r[0] for rs in rows.values() for r in rs)
    out: dict[str, list[float]] = {}
    for name, rs in rows.items():
        rs.sort(key=lambda r: (r[0], r[1]))
        vals: list[float] = []
        i = 0
        cur = rs[0][2]
        for h in range(n_hours):
            key = (start + timedelta(days=h // periods), h % periods + 1)
            while i < len(rs) and (rs[i][0], rs[i][1]) <= key:
                cur = rs[i][2]
                i += 1
            vals.append(cur)
        out[name] = vals
    return out


def read_wide(
    path: Path | str,
    *,
    periods: int = DEFAULT_PERIODS,
    band: int = 1,
    drop_zero_cols: bool = True,
    n_days: int = 1,
    day_offset: int = 0,
) -> dict[str, list[float]]:
    """Parse a wide-format CEN CSV into ``{column -> [v_t1, v_t2, …]}``.

    Args:
        path: CSV path (``Nod_Load.csv``, ``Lin_Units.csv``, …).
        periods: Periods PER DAY (typically 24 for hourly CEN PCP).
        band: When the CSV has a ``BAND`` meta column (e.g.
            ``Lin_Units.csv``), keep only rows matching this band index.
        drop_zero_cols: When True (default), columns that end up all-
            zero are dropped from the result.
        n_days: How many consecutive days to extract.  ``n_days = 1``
            (default) keeps the legacy day-1-only behavior; ``n_days = 7``
            concatenates a full PCP week into a 168-element profile per
            column.  Rows beyond ``day_offset + n_days`` are ignored.
        day_offset: 0-indexed offset of the first day to extract.  The
            FIRST distinct ``(YEAR, MONTH, DAY)`` tuple in the CSV is
            day-0; ``day_offset = 0`` starts at the bundle's first day.

    Returns:
        Dict keyed by column name (bus / reservoir / line); values are
        lists of length ``periods * n_days``.

    The first four-or-five columns
    (``YEAR``, ``MONTH``, ``DAY``, ``PERIOD``, optional ``BAND``) are
    interpreted as metadata; everything else is a data column.  When
    ``n_days > 1`` the per-day rows are concatenated in calendar order
    (day-0 fills slots 0..periods-1, day-1 fills periods..2·periods-1,
    etc.).
    """
    out_len = periods * n_days
    out: dict[str, list[float]] = {}
    meta_cols = {"YEAR", "MONTH", "DAY", "PERIOD", "BAND"}
    day_keys_seen: dict[tuple[str, str, str], int] = {}
    with Path(path).open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=sniff_delimiter(fh))
        if reader.fieldnames is None:
            return out
        # Strip BOM and identify data columns once.
        reader.fieldnames = [h.lstrip("﻿") for h in reader.fieldnames]
        has_band = "BAND" in reader.fieldnames
        data_cols = [h for h in reader.fieldnames if h not in meta_cols]
        for col in data_cols:
            out[col] = [0.0] * out_len
        for row in reader:
            if has_band:
                try:
                    row_band = int(row.get("BAND", "1") or "1")
                except ValueError:
                    continue
                if row_band != band:
                    continue
            try:
                period = int(row.get("PERIOD", "0") or "0")
            except ValueError:
                continue
            if period < 1 or period > periods:
                continue
            # Identify which day this row belongs to.  CSV row order is
            # taken as authoritative — the first (Y,M,D) seen is day-0.
            day_key = (
                row.get("YEAR", "") or "",
                row.get("MONTH", "") or "",
                row.get("DAY", "") or "",
            )
            if day_key not in day_keys_seen:
                day_keys_seen[day_key] = len(day_keys_seen)
            day_idx = day_keys_seen[day_key] - day_offset
            if day_idx < 0 or day_idx >= n_days:
                continue
            slot = day_idx * periods + (period - 1)
            for col in data_cols:
                raw = row.get(col, "")
                if raw is None or raw == "":
                    continue
                try:
                    out[col][slot] = float(raw)
                except ValueError:
                    continue
    if drop_zero_cols:
        return {k: v for k, v in out.items() if any(x != 0.0 for x in v)}
    return out


__all__ = ["DEFAULT_PERIODS", "read_long", "read_wide"]


_DATE_COLS = ("YEAR", "MONTH", "DAY", "PERIOD")


def densify_dated_csv(
    src: Path | str,
    dst: Path | str,
    *,
    start: date,
    n_days: int,
    periods: int = DEFAULT_PERIODS,
) -> bool:
    """Rewrite a change-point Data File as a dense, horizon-aligned one.

    PLEXOS reads a dated Data File row as "this value from (date,
    period) on, until the next row of the same series".  The CEN 2021
    (PLEXOS 8.100) bundles rely on that: ``Gen_Rating.csv`` lists only
    the change points, and every file starts the day BEFORE the horizon.
    :func:`read_long` / :func:`read_wide` instead take the first date in
    the file as day 0 and leave missing periods at 0, which is right for
    the dense, horizon-aligned 2024+ files only.

    This writes ``dst`` with one row per series and horizon slot
    (``start`` .. ``start + n_days - 1``, periods ``1..periods``) holding
    the value in force at that slot (the last change point at or before
    it); a series with no change point yet at a slot has no row there.
    Rows are slot-major, so the first date in ``dst`` is ``start``.
    Series are keyed by ``NAME`` (long format) and ``BAND``; a wide
    file's rows are one series.  ``dst`` is ``,``-delimited whatever the
    source used.

    Returns ``False`` (and writes nothing) for a file without the
    ``YEAR, MONTH, DAY, PERIOD`` columns.
    """
    with Path(src).open("r", encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.reader(fh, delimiter=sniff_delimiter(fh)) if r]
    if not rows:
        return False
    head = [h.strip() for h in rows[0]]
    upper = [h.upper() for h in head]
    if not set(_DATE_COLS) <= set(upper):
        return False
    iy, im, iday, iper = (upper.index(c) for c in _DATE_COLS)
    key_idx = [upper.index(c) for c in ("NAME", "BAND") if c in upper]
    series: dict[tuple[str, ...], list[tuple[tuple[date, int], list[str]]]] = {}
    for row in rows[1:]:
        row = row + [""] * (len(head) - len(row))
        try:
            when = (date(int(row[iy]), int(row[im]), int(row[iday])), int(row[iper]))
        except ValueError:
            continue
        key = tuple(row[i].strip() for i in key_idx)
        series.setdefault(key, []).append((when, row))
    slots = [
        (start + timedelta(days=d), p)
        for d in range(n_days)
        for p in range(1, periods + 1)
    ]
    out: list[tuple[int, int, list[str]]] = []
    for order, points in enumerate(series.values()):
        points.sort(key=lambda t: t[0])  # stable: last duplicate wins
        j = -1
        for s, (day, period) in enumerate(slots):
            while j + 1 < len(points) and points[j + 1][0] <= (day, period):
                j += 1
            if j < 0:
                continue
            row = list(points[j][1])
            row[iy], row[im], row[iday] = str(day.year), str(day.month), str(day.day)
            row[iper] = str(period)
            out.append((s, order, row))
    out.sort(key=lambda t: (t[0], t[1]))
    with Path(dst).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(head)
        writer.writerows(r for _s, _o, r in out)
    return True
