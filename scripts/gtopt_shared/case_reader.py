# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Read a gtopt case at full time resolution, for exporters.

The ``gtopt2*`` exporters (``gtopt2plp``, ``gtopt2sddp``) write a gtopt
planning back into an external dialect.  They need every scheduled
field (``pmax``, ``lmax``, ``tmax_ab``, ``profile``, ``discharge``, ...)
as one value per **scenario x block**, whatever form the planning JSON
uses for it:

* a scalar (``100.0``, ``true``),
* an inline array per stage (``[100, 90]``), per stage x block
  (``[[100, 90], [80, 70]]``) or per scenario x stage x block,
* a string naming an input table ``<input_directory>/<Class>/<name>``
  (``"Other@name"`` redirects the class directory, as the C++
  ``build_table_path``), long layout ``[scenario,] [stage,] [block,]
  uid, value`` or legacy wide ``uid:<N>`` columns.

:class:`GtoptCase` loads the JSON, exposes the time grid
(:class:`TimeGrid`) and resolves fields with :meth:`GtoptCase.field`
into a ``numpy`` array of shape ``(n_scenarios, n_blocks)``; blocks are
the global ``simulation.block_array`` (block uids in table columns are
global too, as gtopt writes them).  Values a table does not cover take
the caller's default.  :class:`StageSchedResolver`
(:mod:`gtopt_shared.field_sched`) remains the per-stage reduction used
by preflight checks.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from gtopt_shared.field_sched import (
    build_table_path,
    find_table_file,
    planning_input_root,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Stage:
    """One ``simulation.stage_array`` entry."""

    uid: int
    first_block: int
    count_block: int
    discount_factor: float = 1.0
    month: str | None = None

    @property
    def blocks(self) -> range:
        """Global block indices (0-based) of the stage."""
        return range(self.first_block, self.first_block + self.count_block)


@dataclass(frozen=True, slots=True)
class TimeGrid:
    """Scenarios, stages and blocks of a planning, in simulation order."""

    scenario_uids: tuple[int, ...]
    probabilities: tuple[float, ...]
    hydrologies: tuple[int | None, ...]
    stages: tuple[Stage, ...]
    block_uids: tuple[int, ...]
    durations: tuple[float, ...]

    @property
    def n_scenarios(self) -> int:
        """Number of scenarios."""
        return len(self.scenario_uids)

    @property
    def n_stages(self) -> int:
        """Number of stages."""
        return len(self.stages)

    @property
    def n_blocks(self) -> int:
        """Number of blocks (all stages)."""
        return len(self.block_uids)

    def stage_of_block(self) -> np.ndarray:
        """Stage index (0-based) of every global block."""
        out = np.zeros(self.n_blocks, dtype=np.int64)
        for k, st in enumerate(self.stages):
            out[st.first_block : st.first_block + st.count_block] = k
        return out

    @classmethod
    def from_planning(cls, planning: Mapping[str, Any]) -> TimeGrid:
        """Build the grid from ``planning["simulation"]``.

        Missing arrays fall back to gtopt's defaults: one block of one
        hour, one stage holding every block, one scenario of probability 1.
        """
        sim = planning.get("simulation", {}) or {}
        blocks = sim.get("block_array") or [{"uid": 1, "duration": 1.0}]
        block_uids = tuple(int(b.get("uid", i + 1)) for i, b in enumerate(blocks))
        durations = tuple(float(b.get("duration", 1.0)) for b in blocks)
        raw_stages = sim.get("stage_array") or [
            {"uid": 1, "first_block": 0, "count_block": len(blocks)}
        ]
        stages = tuple(
            Stage(
                uid=int(s.get("uid", i + 1)),
                first_block=int(s.get("first_block", 0)),
                count_block=int(s.get("count_block", len(blocks))),
                discount_factor=float(s.get("discount_factor", 1.0)),
                month=s.get("month"),
            )
            for i, s in enumerate(raw_stages)
        )
        scen = sim.get("scenario_array") or [{"uid": 1, "probability_factor": 1.0}]
        return cls(
            scenario_uids=tuple(int(s.get("uid", i + 1)) for i, s in enumerate(scen)),
            probabilities=tuple(float(s.get("probability_factor", 1.0)) for s in scen),
            hydrologies=tuple(s.get("hydrology") for s in scen),
            stages=stages,
            block_uids=block_uids,
            durations=durations,
        )


@dataclass
class _Table:
    """One input table: ``uid -> array (n_scenarios, n_blocks)`` (NaN = gap)."""

    values: dict[int, np.ndarray] = field(default_factory=dict)


def _nesting_depth(value: Any) -> int:
    depth = 0
    while isinstance(value, list) and value:
        depth += 1
        value = value[0]
    return depth


def _as_float(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if value is None:
        return float("nan")
    return float(value)


class GtoptCase:
    """A gtopt planning (JSON + input tables) with full-resolution fields.

    Parameters
    ----------
    planning
        The planning dict (``options`` / ``simulation`` / ``system``).
    case_dir
        Directory ``options.input_directory`` is relative to (the JSON's
        directory).
    """

    def __init__(self, planning: dict[str, Any], case_dir: str | Path) -> None:
        self.planning = planning
        self.case_dir = Path(case_dir)
        self.input_root = planning_input_root(planning, case_dir)
        options = planning.get("options", {}) or {}
        self.input_format = str(options.get("input_format") or "parquet")
        self.grid = TimeGrid.from_planning(planning)
        self._tables: dict[str, _Table | None] = {}
        self.missing: set[str] = set()

    @classmethod
    def load(cls, json_path: str | Path) -> GtoptCase:
        """Load a planning JSON file."""
        path = Path(json_path)
        with path.open(encoding="utf-8") as fh:
            planning = json.load(fh)
        return cls(planning, path.parent)

    # ── system access ──────────────────────────────────────────────────
    @property
    def system(self) -> dict[str, Any]:
        """The ``system`` block."""
        return self.planning.get("system", {}) or {}

    @property
    def options(self) -> dict[str, Any]:
        """The ``options`` block."""
        return self.planning.get("options", {}) or {}

    def elements(self, array_name: str) -> list[dict[str, Any]]:
        """Elements of ``system.<array_name>`` (e.g. ``"generator_array"``)."""
        return list(self.system.get(array_name, []) or [])

    @staticmethod
    def ref_index(elements: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
        """Map both uid and name of every element to the element."""
        out: dict[Any, dict[str, Any]] = {}
        for elem in elements:
            if "uid" in elem:
                out[elem["uid"]] = elem
            if "name" in elem:
                out[elem["name"]] = elem
        return out

    # ── field resolution ───────────────────────────────────────────────
    def field(
        self,
        class_name: str,
        element: Mapping[str, Any],
        name: str,
        default: float = float("nan"),
    ) -> np.ndarray:
        """Return ``element[name]`` as an array ``(n_scenarios, n_blocks)``.

        ``class_name`` is the input-table class directory (``Generator``,
        ``Demand``, ``Line``, ...).  Cells the field does not define hold
        ``default``.
        """
        g = self.grid
        out = np.full((g.n_scenarios, g.n_blocks), default, dtype=float)
        value = element.get(name)
        if value is None:
            return out
        if isinstance(value, str):
            table = self._table(class_name, value)
            uid = element.get("uid")
            arr = (
                None
                if table is None
                else table.values.get(int(uid))
                if uid is not None
                else None
            )
            if arr is None:
                if table is not None:
                    logger.warning(
                        "%s %s: uid %s not in table %r", class_name, name, uid, value
                    )
                return out
            return np.where(np.isnan(arr), default, arr)
        if isinstance(value, list):
            self._fill_inline(out, value, f"{class_name}.{name}")
            return out
        out[:] = _as_float(value)
        return out

    def _fill_inline(self, out: np.ndarray, value: list[Any], what: str) -> None:
        # The nesting depth fixes the axes, as in the C++ FieldSched
        # variants: 1 = per stage (T), 2 = stage x block (TB),
        # 3 = scenario x stage x block (STB).  No exported field is ST.
        g = self.grid
        depth = _nesting_depth(value)
        if depth == 1 and len(value) == g.n_stages:
            for k, st in enumerate(g.stages):
                out[:, st.blocks.start : st.blocks.stop] = _as_float(value[k])
        elif depth == 2 and len(value) == g.n_stages:
            for k, st in enumerate(g.stages):
                row = value[k]
                for j, b in enumerate(st.blocks):
                    if j < len(row):
                        out[:, b] = _as_float(row[j])
        elif depth == 3 and len(value) == g.n_scenarios:
            for s, per_stage in enumerate(value):
                for k, st in enumerate(g.stages):
                    if k >= len(per_stage):
                        continue
                    row = per_stage[k]
                    for j, b in enumerate(st.blocks):
                        if j < len(row):
                            out[s, b] = _as_float(row[j])
        else:
            logger.warning("%s: inline array shape not recognised; ignored", what)

    def _table(self, class_name: str, name: str) -> _Table | None:
        base = build_table_path(self.input_root, class_name, name)
        if base not in self._tables:
            path = find_table_file(base, self.input_format)
            if path is None:
                logger.warning("input table %s not found", base)
                self.missing.add(base)
                self._tables[base] = None
            else:
                self._tables[base] = self._load_table(path)
        return self._tables[base]

    def _load_table(self, path: Path) -> _Table:
        import pandas as pd  # pylint: disable=import-outside-toplevel

        df = pd.read_parquet(path) if ".parquet" in path.name else pd.read_csv(path)
        index_cols = [c for c in ("scenario", "stage", "block") if c in df.columns]
        if "uid" not in df.columns or "value" not in df.columns:
            uid_cols = [c for c in df.columns if str(c).startswith("uid:")]
            df = df.melt(
                id_vars=index_cols,
                value_vars=uid_cols,
                var_name="uid",
                value_name="value",
            )
            df["uid"] = df["uid"].str.slice(4).astype(int)
        g = self.grid
        scen_pos = {u: i for i, u in enumerate(g.scenario_uids)}
        block_pos = {u: i for i, u in enumerate(g.block_uids)}
        stage_pos = {st.uid: i for i, st in enumerate(g.stages)}
        table = _Table()
        for uid, sub in df.groupby("uid"):
            arr = np.full((g.n_scenarios, g.n_blocks), np.nan)
            s_idx = (
                sub["scenario"].map(scen_pos).to_numpy()
                if "scenario" in sub.columns
                else None
            )
            vals = sub["value"].to_numpy(dtype=float)
            if "block" in sub.columns:
                b_idx = sub["block"].map(block_pos).to_numpy()
                ok = ~np.isnan(b_idx.astype(float))
                if s_idx is None:
                    arr[:, b_idx[ok].astype(int)] = vals[ok]
                else:
                    ok &= ~np.isnan(s_idx.astype(float))
                    arr[s_idx[ok].astype(int), b_idx[ok].astype(int)] = vals[ok]
            elif "stage" in sub.columns:
                for row_k, (stage_uid, val) in enumerate(
                    zip(sub["stage"].to_numpy(), vals, strict=True)
                ):
                    k = stage_pos.get(int(stage_uid))
                    if k is None:
                        continue
                    blocks = g.stages[k].blocks
                    if s_idx is None:
                        arr[:, blocks.start : blocks.stop] = val
                    elif not np.isnan(float(s_idx[row_k])):
                        arr[int(s_idx[row_k]), blocks.start : blocks.stop] = val
            elif s_idx is not None:
                for row_k, val in enumerate(vals):
                    if not np.isnan(float(s_idx[row_k])):
                        arr[int(s_idx[row_k]), :] = val
            else:
                arr[:] = vals[-1]
            table.values[int(uid)] = arr
        return table


def per_stage(grid: TimeGrid, arr: np.ndarray, how: str = "mean") -> np.ndarray:
    """Collapse ``(n_scenarios, n_blocks)`` to ``(n_scenarios, n_stages)``.

    ``how`` is ``"mean"`` (duration-weighted), ``"max"``, ``"min"`` or
    ``"first"``.
    """
    dur = np.asarray(grid.durations)
    out = np.empty((arr.shape[0], grid.n_stages))
    for k, st in enumerate(grid.stages):
        blk = arr[:, st.blocks.start : st.blocks.stop]
        if how == "max":
            out[:, k] = blk.max(axis=1)
        elif how == "min":
            out[:, k] = blk.min(axis=1)
        elif how == "first":
            out[:, k] = blk[:, 0]
        else:
            w = dur[st.blocks.start : st.blocks.stop]
            out[:, k] = (blk * w).sum(axis=1) / max(w.sum(), 1e-300)
    return out
