# SPDX-License-Identifier: BSD-3-Clause
"""Hourly time-grid helpers shared by time-series converters.

Converters that turn an hourly series (load, renewable availability, …)
into a gtopt case all need the same three things:

* pick an hour window out of the series (``--hours A:B``),
* group consecutive hours into blocks (``--block-hours N``, duration N)
  and blocks into stages (``--stage-hours H``, e.g. 24 → daily stages),
* write per-block schedules as long-layout Parquet field tables
  (``<Class>/<field>.parquet`` with ``[scenario,] stage, block, uid,
  value``) that gtopt reads through a string field reference.

:class:`TimeGrid` covers the first two and builds the ``simulation``
section (``block_array`` / ``stage_array`` / ``phase_array`` /
``scenario_array``); :func:`write_block_schedule` covers the third.

:class:`BlockGrid` is the general (possibly non-chronological) layout:
every hour is assigned to one stage and one block, blocks can have
different durations, and a block's hours need not be consecutive.  It is
built from calendar stages (:func:`calendar_stage_of_hour`, e.g. months)
and an in-stage grouping — :func:`chronological_groups`,
:func:`ldc_groups` (load-duration curve) or :func:`hour_of_day_groups`
(typical day).  :meth:`BlockGrid.hour_block_map` gives the
``BlockHourMap`` table that records which hours fall in which block.

Used by :mod:`pp2gtopt` (pandapower / MATPOWER networks + hourly
profiles).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from gtopt_shared.dataframe import to_long_layout
from gtopt_shared.output_format import write_dataframe
from gtopt_shared.parquet import DEFAULT_COMPRESSION


def parse_hour_range(text: str | None, n_total: int) -> tuple[int, int]:
    """Parse an hour-window selector into a ``[start, stop)`` pair.

    Accepted forms (0-based hour positions in the series):

    * ``None`` / ``""`` / ``":"`` — the whole series.
    * ``"A:B"`` — hours ``A`` .. ``B-1`` (Python slice semantics);
      either side may be omitted (``"A:"``, ``":B"``).
    * ``"A+N"`` — ``N`` hours starting at ``A`` (e.g. ``"168+168"`` =
      the second week).

    Raises ``ValueError`` on malformed input or an empty / out-of-range
    window.
    """
    if text is None or text.strip() in ("", ":"):
        return 0, n_total
    spec = text.strip()
    try:
        if "+" in spec:
            a_txt, n_txt = spec.split("+", 1)
            start = int(a_txt) if a_txt else 0
            stop = start + int(n_txt)
        elif ":" in spec:
            a_txt, b_txt = spec.split(":", 1)
            start = int(a_txt) if a_txt else 0
            stop = int(b_txt) if b_txt else n_total
        else:
            raise ValueError("expected 'A:B' or 'A+N'")
    except ValueError as exc:
        raise ValueError(f"invalid hour range {text!r}: {exc}") from exc
    if start < 0 or stop > n_total or stop <= start:
        raise ValueError(
            f"hour range {text!r} -> [{start}, {stop}) is empty or outside "
            f"the available series [0, {n_total})"
        )
    return start, stop


@dataclass(frozen=True)
class TimeGrid:
    """Hour → block → stage layout of an hourly horizon.

    Parameters
    ----------
    n_hours:
        Number of hours in the selected window.
    block_hours:
        Consecutive hours folded into one block (block duration).
        ``n_hours`` must be a multiple of it.
    stage_hours:
        Hours per stage (``None`` → one stage spanning the window).  Must
        be a multiple of ``block_hours``; the last stage may be shorter.
    """

    n_hours: int
    block_hours: int = 1
    stage_hours: int | None = None

    def __post_init__(self) -> None:
        if self.n_hours <= 0:
            raise ValueError("TimeGrid: n_hours must be positive")
        if self.block_hours <= 0:
            raise ValueError("TimeGrid: block_hours must be positive")
        if self.n_hours % self.block_hours:
            raise ValueError(
                f"TimeGrid: {self.n_hours} hours is not a multiple of "
                f"block_hours={self.block_hours}; adjust --hours or "
                "--block-hours"
            )
        if self.stage_hours is not None:
            if self.stage_hours <= 0:
                raise ValueError("TimeGrid: stage_hours must be positive")
            if self.stage_hours % self.block_hours:
                raise ValueError(
                    f"TimeGrid: stage_hours={self.stage_hours} is not a "
                    f"multiple of block_hours={self.block_hours}"
                )

    @property
    def n_blocks(self) -> int:
        """Total number of blocks."""
        return self.n_hours // self.block_hours

    @property
    def blocks_per_stage(self) -> int:
        """Blocks per (full) stage."""
        if self.stage_hours is None:
            return self.n_blocks
        return min(self.stage_hours // self.block_hours, self.n_blocks)

    @property
    def n_stages(self) -> int:
        """Number of stages (the last one may hold fewer blocks)."""
        return -(-self.n_blocks // self.blocks_per_stage)

    def stage_ranges(self) -> list[tuple[int, int]]:
        """``(first_block, count_block)`` per stage (0-based first block)."""
        bps = self.blocks_per_stage
        return [
            (first, min(bps, self.n_blocks - first))
            for first in range(0, self.n_blocks, bps)
        ]

    def block_stage_uids(self) -> np.ndarray:
        """1-based stage uid of every block (length ``n_blocks``)."""
        return np.arange(self.n_blocks, dtype=np.int32) // self.blocks_per_stage + 1

    def block_durations(self) -> np.ndarray:
        """Duration [h] of every block (length ``n_blocks``)."""
        return np.full(self.n_blocks, float(self.block_hours))

    def to_block_grid(self) -> "BlockGrid":
        """The same layout as a general :class:`BlockGrid`."""
        hours = np.arange(self.n_hours)
        block = hours // self.block_hours
        return BlockGrid(
            stage_of_hour=(block // self.blocks_per_stage).astype(np.int64),
            block_of_hour=block.astype(np.int64),
        )

    def aggregate(self, hourly: np.ndarray) -> np.ndarray:
        """Average consecutive hours into blocks.

        ``hourly`` has shape ``(n_hours,)`` or ``(n_hours, k)``.  A
        block's value is the mean over its hours, so ``value × duration``
        preserves energy for MW series (and the mean availability for
        per-unit series).
        """
        arr = np.asarray(hourly, dtype=np.float64)
        if arr.shape[0] != self.n_hours:
            raise ValueError(
                f"TimeGrid.aggregate: expected {self.n_hours} rows, got {arr.shape[0]}"
            )
        if self.block_hours == 1:
            return arr
        new_shape = (self.n_blocks, self.block_hours, *arr.shape[1:])
        return arr.reshape(new_shape).mean(axis=1)

    def simulation(self, *, one_phase_per_stage: bool = True) -> dict[str, Any]:
        """Build the gtopt ``simulation`` section for this grid.

        One scenario (probability 1).  When ``one_phase_per_stage`` and
        there is more than one stage, emits a ``phase_array`` with one
        phase per stage (the SDDP / cascade decomposition unit; monolithic
        solves the phases in sequence).
        """
        duration = float(self.block_hours)
        sim: dict[str, Any] = {
            "block_array": [
                {"uid": b + 1, "duration": duration} for b in range(self.n_blocks)
            ],
            "stage_array": [
                {
                    "uid": s + 1,
                    "first_block": first,
                    "count_block": count,
                    "active": 1,
                }
                for s, (first, count) in enumerate(self.stage_ranges())
            ],
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        }
        if one_phase_per_stage and self.n_stages > 1:
            sim["phase_array"] = [
                {"uid": s + 1, "first_stage": s, "count_stage": 1}
                for s in range(self.n_stages)
            ]
        return sim


# ---------------------------------------------------------------------------
# General (non-chronological) block layouts
# ---------------------------------------------------------------------------

#: Calendar periods accepted by :func:`calendar_stage_of_hour`.
STAGE_PERIODS: tuple[str, ...] = ("year", "quarter", "month", "week", "day")

#: Default calendar origin for hour-indexed series: a non-leap year.
DEFAULT_START = "2023-01-01"


def hour_timestamps(n_hours: int, start: str | pd.Timestamp) -> pd.DatetimeIndex:
    """Hourly timestamps ``start, start + 1 h, …`` (``n_hours`` of them)."""
    return pd.date_range(pd.Timestamp(start), periods=n_hours, freq="h")


def calendar_stage_of_hour(times: pd.DatetimeIndex, period: str) -> np.ndarray:
    """0-based stage index of every hour for calendar ``period`` stages.

    ``year`` / ``quarter`` / ``month`` — calendar years, quarters
    (Jan–Mar, Apr–Jun, …) or months (a partial first / last period is its
    own stage); ``week`` — consecutive 7-day chunks from the first hour;
    ``day`` — consecutive 24-h chunks.  ``times`` must be increasing.
    """
    n = len(times)
    if n == 0:
        raise ValueError("calendar_stage_of_hour: no hours")
    if period in ("year", "quarter", "month"):
        year = np.asarray(times.year, dtype=np.int64)
        month = np.asarray(times.month, dtype=np.int64) - 1
        per_year = {"year": 1, "quarter": 4, "month": 12}[period]
        key = year * per_year + month * per_year // 12
        change = np.concatenate([[0], (np.diff(key) != 0).astype(np.int64)])
        return np.cumsum(change)
    if period in ("week", "day"):
        span = 168 if period == "week" else 24
        return np.arange(n, dtype=np.int64) // span
    raise ValueError(f"unknown stage period {period!r}; expected {STAGE_PERIODS}")


def chronological_groups(stage_of_hour: np.ndarray, block_hours: int) -> np.ndarray:
    """In-stage group of every hour: consecutive ``block_hours`` chunks.

    Every stage must hold a multiple of ``block_hours`` hours.
    """
    stage_of_hour = np.asarray(stage_of_hour)
    groups = np.empty(len(stage_of_hour), dtype=np.int64)
    for stage in np.unique(stage_of_hour):
        idx = np.flatnonzero(stage_of_hour == stage)
        if len(idx) % block_hours:
            raise ValueError(
                f"stage {int(stage) + 1} has {len(idx)} hours, not a multiple "
                f"of block_hours={block_hours}"
            )
        groups[idx] = np.arange(len(idx)) // block_hours
    return groups


def _equal_energy_cuts(sorted_vals: np.ndarray, n_groups: int) -> np.ndarray:
    """Group sizes splitting a descending curve into equal-area pieces.

    The curve is shifted to start at 0 (``v - min(v)``) so net-load
    curves with negative values still split sensibly; every group gets at
    least one hour.
    """
    n = len(sorted_vals)
    area = sorted_vals - sorted_vals.min() + 1e-9
    cum = np.cumsum(area)
    targets = cum[-1] * np.arange(1, n_groups) / n_groups
    cuts = np.searchsorted(cum, targets, side="left") + 1
    # strictly increasing cut points, each group ≥ 1 hour
    prev = 0
    for k, cut in enumerate(cuts):
        hi = n - (n_groups - 1 - k)
        cuts[k] = prev = min(max(int(cut), prev + 1), hi)
    return np.diff(np.concatenate([[0], cuts, [n]]))


def ldc_groups(
    stage_of_hour: np.ndarray,
    key: np.ndarray,
    n_blocks: int,
    *,
    split: str = "hours",
) -> np.ndarray:
    """Load-duration-curve grouping: in-stage group of every hour.

    Inside every stage the hours are sorted by ``key`` (e.g. net load),
    highest first, and cut into ``n_blocks`` groups — group 0 is the
    peak.  ``split="hours"`` gives (nearly) equal hour counts;
    ``split="energy"`` gives equal area under the (shifted) duration
    curve, so peak blocks are shorter.  The same hour → group map is used
    for every element, which keeps the hour-by-hour correlation between
    load and renewables inside each block.  A stage with fewer hours than
    ``n_blocks`` gets one block per hour.
    """
    if n_blocks <= 0:
        raise ValueError("ldc_groups: n_blocks must be positive")
    if split not in ("hours", "energy"):
        raise ValueError("ldc_groups: split must be 'hours' or 'energy'")
    stage_of_hour = np.asarray(stage_of_hour)
    key = np.asarray(key, dtype=np.float64)
    groups = np.empty(len(stage_of_hour), dtype=np.int64)
    for stage in np.unique(stage_of_hour):
        idx = np.flatnonzero(stage_of_hour == stage)
        order = idx[np.argsort(-key[idx], kind="stable")]
        n_grp = min(n_blocks, len(idx))
        if split == "energy":
            sizes = _equal_energy_cuts(key[order], n_grp)
        else:
            sizes = np.array([len(c) for c in np.array_split(order, n_grp)])
        groups[order] = np.repeat(np.arange(n_grp), sizes)
    return groups


def hour_of_day_groups(hour_of_day: np.ndarray, n_blocks: int) -> np.ndarray:
    """Typical-day grouping: hour-of-day ``h`` (0-23) → ``h * n_blocks // 24``.

    ``n_blocks = 24`` is a typical hourly day, ``12`` gives 2-h blocks,
    ``1`` one block per stage.  Values in a block are averaged over all
    days of the stage.
    """
    if not 1 <= n_blocks <= 24:
        raise ValueError("hour_of_day_groups: n_blocks must be in 1..24")
    return np.asarray(hour_of_day, dtype=np.int64) * n_blocks // 24


@dataclass(frozen=True, eq=False)
class BlockGrid:
    """General hour → (stage, block) layout; blocks need not be chronological.

    Parameters
    ----------
    stage_of_hour:
        0-based stage index of every hour (non-decreasing).
    block_of_hour:
        0-based *global* block index of every hour; blocks are numbered
        stage by stage (all blocks of stage 0 first) and every block lies
        in exactly one stage.  Use :meth:`from_groups` to build it from
        in-stage group ids.
    """

    stage_of_hour: np.ndarray
    block_of_hour: np.ndarray

    def __post_init__(self) -> None:
        stage = np.asarray(self.stage_of_hour)
        block = np.asarray(self.block_of_hour)
        if stage.shape != block.shape or stage.ndim != 1 or stage.size == 0:
            raise ValueError("BlockGrid: need equal-length, non-empty 1-D arrays")
        if (np.diff(stage) < 0).any():
            raise ValueError("BlockGrid: stage_of_hour must be non-decreasing")
        n_blocks = int(block.max()) + 1
        if set(np.unique(block)) != set(range(n_blocks)):
            raise ValueError("BlockGrid: block indices must be 0..n_blocks-1")
        lo = np.full(n_blocks, np.iinfo(np.int64).max, dtype=np.int64)
        hi = np.full(n_blocks, np.iinfo(np.int64).min, dtype=np.int64)
        np.minimum.at(lo, block, stage)
        np.maximum.at(hi, block, stage)
        if (lo != hi).any() or (np.diff(lo) < 0).any():
            raise ValueError(
                "BlockGrid: every block must lie in one stage, blocks ordered by stage"
            )

    @classmethod
    def from_groups(cls, stage_of_hour: np.ndarray, group: np.ndarray) -> "BlockGrid":
        """Number ``(stage, in-stage group)`` pairs into global blocks."""
        stage = np.asarray(stage_of_hour, dtype=np.int64)
        grp = np.asarray(group, dtype=np.int64)
        span = int(grp.max()) + 1
        _uniq, block = np.unique(stage * span + grp, return_inverse=True)
        return cls(stage_of_hour=stage, block_of_hour=block.astype(np.int64))

    @property
    def n_hours(self) -> int:
        """Number of hours."""
        return int(len(self.block_of_hour))

    @property
    def n_blocks(self) -> int:
        """Number of blocks."""
        return int(np.max(self.block_of_hour)) + 1

    def block_durations(self) -> np.ndarray:
        """Duration [h] of every block = hours assigned to it."""
        return np.bincount(self.block_of_hour, minlength=self.n_blocks).astype(
            np.float64
        )

    def block_stage_uids(self) -> np.ndarray:
        """1-based stage uid of every block."""
        bstage = np.zeros(self.n_blocks, dtype=np.int64)
        bstage[self.block_of_hour] = self.stage_of_hour
        # renumber stages densely (stage indices may have gaps)
        _u, dense = np.unique(bstage, return_inverse=True)
        return (dense + 1).astype(np.int32)

    @property
    def n_stages(self) -> int:
        """Number of stages."""
        return int(len(np.unique(self.stage_of_hour)))

    def stage_ranges(self) -> list[tuple[int, int]]:
        """``(first_block, count_block)`` per stage (0-based first block)."""
        uids = self.block_stage_uids()
        counts = np.bincount(uids)[1:]
        firsts = np.concatenate([[0], np.cumsum(counts)[:-1]])
        return [(int(f), int(c)) for f, c in zip(firsts, counts)]

    def aggregate(self, hourly: np.ndarray) -> np.ndarray:
        """Mean of ``hourly`` over each block's hours (shape ``(n_hours[, k])``).

        ``mean × duration`` equals the hourly sum, so energy is preserved
        per element and per stage.
        """
        arr = np.asarray(hourly, dtype=np.float64)
        if arr.shape[0] != self.n_hours:
            raise ValueError(
                f"BlockGrid.aggregate: expected {self.n_hours} rows, got {arr.shape[0]}"
            )
        dur = self.block_durations()
        if arr.ndim == 1:
            return np.bincount(self.block_of_hour, arr, self.n_blocks) / dur
        out = np.zeros((self.n_blocks, *arr.shape[1:]))
        np.add.at(out, self.block_of_hour, arr)
        return out / dur.reshape(-1, *([1] * (arr.ndim - 1)))

    def simulation(self, *, one_phase_per_stage: bool = True) -> dict[str, Any]:
        """gtopt ``simulation`` section (see :meth:`TimeGrid.simulation`)."""
        dur = self.block_durations()
        sim: dict[str, Any] = {
            "block_array": [
                {"uid": b + 1, "duration": float(d)} for b, d in enumerate(dur)
            ],
            "stage_array": [
                {"uid": s + 1, "first_block": first, "count_block": count, "active": 1}
                for s, (first, count) in enumerate(self.stage_ranges())
            ],
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        }
        if one_phase_per_stage and self.n_stages > 1:
            sim["phase_array"] = [
                {"uid": s + 1, "first_stage": s, "count_stage": 1}
                for s in range(self.n_stages)
            ]
        return sim

    def hour_block_map(
        self, times: pd.DatetimeIndex, hour_offset: int = 0
    ) -> pd.DataFrame:
        """``BlockHourMap`` table: one row per hour.

        Columns (int32): ``year, month, day, hour`` (1-24, the
        plp2gtopt ``indhor`` convention), ``stage`` and ``block`` (1-based
        uids) and ``hour_index`` (0-based position in the source series,
        ``hour_offset`` + row).
        """
        if len(times) != self.n_hours:
            raise ValueError("hour_block_map: times length != n_hours")
        stage_uid = self.block_stage_uids()[self.block_of_hour]
        return pd.DataFrame(
            {
                "year": np.asarray(times.year, dtype=np.int32),
                "month": np.asarray(times.month, dtype=np.int32),
                "day": np.asarray(times.day, dtype=np.int32),
                "hour": np.asarray(times.hour, dtype=np.int32) + 1,
                "stage": stage_uid.astype(np.int32),
                "block": (self.block_of_hour + 1).astype(np.int32),
                "hour_index": np.arange(
                    hour_offset, hour_offset + self.n_hours, dtype=np.int32
                ),
            }
        )


def write_block_schedule(
    out_dir: Path,
    field: str,
    values: Mapping[int, np.ndarray],
    grid: TimeGrid | BlockGrid,
    *,
    with_scenario: bool = False,
    compression: str | None = DEFAULT_COMPRESSION,
) -> Path:
    """Write a per-block schedule as a long-layout Parquet field table.

    ``values`` maps element uid → array of ``grid.n_blocks`` values.  The
    file is ``out_dir/<field>.parquet`` with columns ``[scenario,] stage,
    block, uid, value`` (int32 index columns, float64 value) — the layout
    gtopt's input reader consumes for a ``"<field>"`` string reference.
    ``with_scenario`` adds a ``scenario = 1`` column (needed for
    scenario-indexed fields such as ``CapacityProfile.profile``).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    wide: dict[str, Any] = {}
    if with_scenario:
        wide["scenario"] = np.ones(grid.n_blocks, dtype=np.int32)
    wide["stage"] = grid.block_stage_uids()
    wide["block"] = np.arange(1, grid.n_blocks + 1, dtype=np.int32)
    for uid, arr in values.items():
        col = np.asarray(arr, dtype=np.float64)
        if col.shape != (grid.n_blocks,):
            raise ValueError(
                f"write_block_schedule({field}): uid {uid} has shape "
                f"{col.shape}, expected ({grid.n_blocks},)"
            )
        wide[f"uid:{int(uid)}"] = col
    long_df = to_long_layout(pd.DataFrame(wide))
    if long_df is None:
        raise ValueError(f"write_block_schedule({field}): no values to write")
    return write_dataframe(long_df, out_dir, field, compression=compression)
