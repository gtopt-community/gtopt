# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Read a gtopt output directory at full time resolution, for exporters.

The results half of :mod:`gtopt_shared.case_reader`: the ``gtopt2*``
exporters write a solved run in an external tool's own output format, so
they need every solution / dual stream ``<Class>/<field>`` as one value
per **scenario x block** (or **scenario x stage** for stage quantities
such as ``Reservoir/efin_sol``).

gtopt writes each stream in one of these layouts, all handled here:

* a hive-partitioned Parquet dataset ``<field>.parquet/scene=N/phase=M/
  part.parquet`` (the default) or a single ``<field>.parquet`` file,
* per-(scene, phase) shards ``<field>_s<N>_p<M>.{parquet,csv,csv.zst,
  csv.gz}`` (``output_format = csv``) or a single ``<field>.csv[.zst]``,

with the long schema ``scenario, stage, block, uid, value`` (exact zeros of
``_sol`` / ``_cost`` streams are dropped; ``_dual`` streams keep them) or the
legacy wide ``uid:<N>`` columns.  Scenario, stage and block columns carry
uids, which are global across scenes and phases, so the shards are simply
concatenated.  Missing cells read as ``0.0``.

Units are gtopt's: power MW, flows m3/s, volumes hm3 (reservoir) or MWh
(battery), and duals already back-scaled to undiscounted per-unit prices
(``Bus/balance_dual`` in $/MWh, ``Reservoir/water_value_dual`` in $/hm3,
``Battery/energy_dual`` in $/MWh): gtopt divides each dual by the
probability, discount and (for power rows) block-duration factors and
multiplies by ``scale_objective`` before writing it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from gtopt_shared.case_reader import TimeGrid

logger = logging.getLogger(__name__)

_CSV_EXTS = (".csv", ".csv.zst", ".csv.gz")


def find_stream(class_dir: Path, field: str) -> list[Path]:
    """Files (or one Parquet dataset dir) holding ``<class_dir>/<field>``."""
    pq = class_dir / f"{field}.parquet"
    if pq.exists():
        return [pq]
    shards = sorted(class_dir.glob(f"{field}_s*_p*.parquet"))
    if shards:
        return shards
    for ext in _CSV_EXTS:
        shards = sorted(class_dir.glob(f"{field}_s*_p*{ext}"))
        if shards:
            return shards
    for ext in _CSV_EXTS:
        single = class_dir / f"{field}{ext}"
        if single.is_file():
            return [single]
    return []


def _read_one(path: Path) -> pd.DataFrame:
    if ".parquet" in path.name:
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    if "uid" not in df.columns or "value" not in df.columns:
        ids = [c for c in ("scenario", "stage", "block") if c in df.columns]
        wide = [c for c in df.columns if str(c).startswith("uid:")]
        df = df.melt(id_vars=ids, value_vars=wide, var_name="uid", value_name="value")
        df["uid"] = df["uid"].str.slice(4)
    keep = [c for c in ("scenario", "stage", "block", "uid", "value") if c in df]
    return df[keep]


class GtoptResults:
    """Lazy reader of one gtopt output directory on a case's time grid."""

    def __init__(self, out_dir: str | Path, grid: TimeGrid) -> None:
        """Bind the output directory to the case's :class:`TimeGrid`."""
        self.out_dir = Path(out_dir)
        self.grid = grid
        self._cache: dict[tuple[str, str], pd.DataFrame | None] = {}
        self._bcache: dict[tuple[str, str], dict[int, np.ndarray]] = {}
        self._scen_pos = {u: i for i, u in enumerate(grid.scenario_uids)}
        self._block_pos = {u: i for i, u in enumerate(grid.block_uids)}
        self._stage_pos = {st.uid: i for i, st in enumerate(grid.stages)}

    def table(self, class_name: str, field: str) -> pd.DataFrame | None:
        """Concatenated long frame of a stream, or ``None`` when absent."""
        key = (class_name, field)
        if key not in self._cache:
            paths = find_stream(self.out_dir / class_name, field)
            frames = [_read_one(p) for p in paths]
            frames = [f for f in frames if not f.empty]
            if not paths:
                self._cache[key] = None
            elif frames:
                df = pd.concat(frames, ignore_index=True)
                for col in ("scenario", "stage", "block", "uid"):
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col]).astype(np.int64)
                df["value"] = pd.to_numeric(df["value"]).astype(float)
                self._cache[key] = df
            else:
                self._cache[key] = pd.DataFrame(
                    columns=["scenario", "stage", "block", "uid", "value"]
                )
        return self._cache[key]

    def has(self, class_name: str, field: str) -> bool:
        """Whether the stream exists (possibly empty)."""
        return self.table(class_name, field) is not None

    def _scen_idx(self, sub: pd.DataFrame) -> np.ndarray | None:
        if "scenario" not in sub.columns:
            return None
        return sub["scenario"].map(self._scen_pos).to_numpy(dtype=float)

    def blocks(self, class_name: str, field: str) -> dict[int, np.ndarray]:
        """``uid -> (n_scenarios, n_blocks)`` of a per-block stream (0-filled)."""
        key = (class_name, field)
        if key not in self._bcache:
            self._bcache[key] = self._blocks(class_name, field)
        return self._bcache[key]

    def _blocks(self, class_name: str, field: str) -> dict[int, np.ndarray]:
        df = self.table(class_name, field)
        g = self.grid
        out: dict[int, np.ndarray] = {}
        if df is None or df.empty or "block" not in df.columns:
            return out
        for uid, sub in df.groupby("uid"):
            arr = np.zeros((g.n_scenarios, g.n_blocks))
            b = sub["block"].map(self._block_pos).to_numpy(dtype=float)
            s = self._scen_idx(sub)
            vals = sub["value"].to_numpy(dtype=float)
            ok = ~np.isnan(b)
            if s is None:
                arr[:, b[ok].astype(int)] = vals[ok]
            else:
                ok &= ~np.isnan(s)
                arr[s[ok].astype(int), b[ok].astype(int)] = vals[ok]
            out[int(str(uid))] = arr
        return out

    def stages(self, class_name: str, field: str) -> dict[int, np.ndarray]:
        """``uid -> (n_scenarios, n_stages)`` of a per-stage stream (0-filled).

        A stream that is per block (block uids present) gives the value of
        the last block of each stage (an end-of-stage reading).
        """
        df = self.table(class_name, field)
        g = self.grid
        out: dict[int, np.ndarray] = {}
        if df is None or df.empty:
            return out
        per_block = "block" in df.columns and df["block"].isin(self._block_pos).any()
        if per_block:
            last = [st.first_block + st.count_block - 1 for st in g.stages]
            return {
                uid: arr[:, last] for uid, arr in self.blocks(class_name, field).items()
            }
        if "stage" not in df.columns:
            logger.warning("%s/%s: no stage column", class_name, field)
            return out
        for uid, sub in df.groupby("uid"):
            arr = np.zeros((g.n_scenarios, g.n_stages))
            k = sub["stage"].map(self._stage_pos).to_numpy(dtype=float)
            s = self._scen_idx(sub)
            vals = sub["value"].to_numpy(dtype=float)
            ok = ~np.isnan(k)
            if s is None:
                arr[:, k[ok].astype(int)] = vals[ok]
            else:
                ok &= ~np.isnan(s)
                arr[s[ok].astype(int), k[ok].astype(int)] = vals[ok]
            out[int(str(uid))] = arr
        return out

    def block_of(self, class_name: str, field: str, uid: int | None) -> np.ndarray:
        """One element's ``(n_scenarios, n_blocks)`` values (zeros if absent)."""
        g = self.grid
        if uid is None:
            return np.zeros((g.n_scenarios, g.n_blocks))
        got = self.blocks(class_name, field).get(int(uid))
        return got if got is not None else np.zeros((g.n_scenarios, g.n_blocks))

    def stage_of(self, class_name: str, field: str, uid: int | None) -> np.ndarray:
        """One element's ``(n_scenarios, n_stages)`` values (zeros if absent)."""
        g = self.grid
        if uid is None:
            return np.zeros((g.n_scenarios, g.n_stages))
        got = self.stages(class_name, field).get(int(uid))
        return got if got is not None else np.zeros((g.n_scenarios, g.n_stages))
