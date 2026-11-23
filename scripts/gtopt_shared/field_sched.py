# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Per-stage resolution of gtopt schedule fields (``FieldSched``).

Any scheduled field of a gtopt element (``reactance``, ``tmax_ab``,
``active``, ``pmax``, ``lmax``, ...) may be written in the planning JSON
as

* a scalar (``0.05``, ``true``),
* an inline array -- per stage (``[1, 1, 0]``) or per stage x block
  (``[[100, 90], [100, 80]]``),
* a string naming an input table ``<input_directory>/<Class>/<name>``
  (``.parquet`` / ``.csv``, optionally ``.gz``).  ``"Other@name"``
  redirects to ``<input_directory>/Other/name``, mirroring the C++
  ``build_table_path``.  Tables are in the long layout
  ``[scenario,] [stage,] [block,] uid, value`` (legacy wide ``uid:<N>``
  columns are also accepted).

:class:`StageSchedResolver` collapses any of those forms to ONE value
per stage (in ``simulation.stage_array`` order), reducing over blocks
and scenarios with ``min`` or ``max``.  It is the shared building block
for preflight tools that reason stage-by-stage (topology, capacity
checks) without running the solver.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

logger = logging.getLogger(__name__)

Reduce = Literal["min", "max"]

_TABLE_EXTENSIONS_PARQUET = (".parquet", ".parquet.gz", ".csv", ".csv.gz")
_TABLE_EXTENSIONS_CSV = (".csv", ".csv.gz", ".parquet", ".parquet.gz")


def build_table_path(input_dir: str | Path, class_name: str, field_name: str) -> str:
    """Return the input-table base path (no extension) of a file field.

    Mirrors the C++ ``build_table_path`` in ``array_index_traits.cpp``:
    ``"Class@name"`` overrides the class directory, otherwise the table
    lives at ``<input_dir>/<class_name>/<field_name>``.
    """
    if "@" in field_name:
        cls, name = field_name.split("@", 1)
        return str(Path(input_dir) / cls / name)
    return str(Path(input_dir) / class_name / field_name)


def find_table_file(base_path: str, preferred_format: str = "parquet") -> Path | None:
    """Return the first existing ``base_path`` + extension, or ``None``."""
    exts = (
        _TABLE_EXTENSIONS_CSV
        if preferred_format == "csv"
        else _TABLE_EXTENSIONS_PARQUET
    )
    for ext in exts:
        cand = Path(base_path + ext)
        if cand.exists():
            return cand
    return None


def planning_input_root(planning: Mapping[str, Any], case_dir: str | Path) -> Path:
    """Resolve ``options.input_directory`` against the case directory."""
    options = planning.get("options", {}) or {}
    raw = options.get("input_directory") or "."
    path = Path(str(raw))
    if path.is_absolute():
        return path
    return Path(case_dir) / path


def planning_stage_uids(planning: Mapping[str, Any]) -> list[int]:
    """Return the stage UIDs in ``simulation.stage_array`` order.

    Falls back to ``[1]`` (one implicit stage) when the planning has no
    stage array, so callers can always iterate at least once.
    """
    sim = planning.get("simulation", {}) or {}
    out: list[int] = []
    for idx, stage in enumerate(sim.get("stage_array", []) or []):
        uid = stage.get("uid") if isinstance(stage, dict) else None
        out.append(int(uid) if isinstance(uid, (int, float)) else idx + 1)
    return out or [1]


@dataclass
class _UidTable:
    """One input table reduced to ``uid -> stage -> (min, max)``."""

    per_stage: dict[int, dict[int, tuple[float, float]]] = field(default_factory=dict)
    # Tables without a ``stage`` column carry one value per uid.
    per_uid: dict[int, tuple[float, float]] = field(default_factory=dict)


def _load_uid_table(path: Path) -> _UidTable:
    import pandas as pd  # pylint: disable=import-outside-toplevel

    if ".parquet" in path.name:
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)

    if "uid" not in df.columns or "value" not in df.columns:
        # Legacy wide layout: ``[index cols...], uid:<N>, ...``.
        uid_cols = [c for c in df.columns if str(c).startswith("uid:")]
        index_cols = [c for c in df.columns if c in ("scenario", "stage", "block")]
        df = df.melt(
            id_vars=index_cols, value_vars=uid_cols, var_name="uid", value_name="value"
        )
        df["uid"] = df["uid"].str.slice(4).astype(int)

    out = _UidTable()
    df = df.dropna(subset=["value"])
    if "stage" in df.columns:
        agg = df.groupby(["uid", "stage"])["value"].agg(["min", "max"])
        for (uid, stage), row in agg.iterrows():
            out.per_stage.setdefault(int(uid), {})[int(stage)] = (
                float(row["min"]),
                float(row["max"]),
            )
    else:
        agg = df.groupby("uid")["value"].agg(["min", "max"])
        for uid, row in agg.iterrows():
            out.per_uid[int(uid)] = (float(row["min"]), float(row["max"]))
    return out


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _reduce_leaves(value: Any, reduce: Reduce) -> float | None:
    """Reduce every numeric leaf of a (possibly nested) list."""
    leaves: list[float] = []
    stack: list[Any] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        else:
            num = _as_number(item)
            if num is not None:
                leaves.append(num)
    if not leaves:
        return None
    return min(leaves) if reduce == "min" else max(leaves)


class StageSchedResolver:
    """Resolve schedule fields to one value per stage.

    Parameters
    ----------
    input_root
        Directory holding the ``<Class>/<name>`` input tables
        (``options.input_directory`` resolved against the case dir).
    stage_uids
        Stage UIDs in simulation order; the returned lists follow it.
    input_format
        ``"parquet"`` (default) or ``"csv"`` -- the extension tried first.
    """

    def __init__(
        self,
        input_root: str | Path,
        stage_uids: Sequence[int],
        input_format: str = "parquet",
    ) -> None:
        self.input_root = Path(input_root)
        self.stage_uids = list(stage_uids)
        self.input_format = input_format
        self._tables: dict[str, _UidTable | None] = {}
        self.missing_files: set[str] = set()
        self.missing_uids: set[tuple[str, int]] = set()

    @classmethod
    def from_planning(
        cls, planning: Mapping[str, Any], case_dir: str | Path
    ) -> StageSchedResolver:
        options = planning.get("options", {}) or {}
        return cls(
            planning_input_root(planning, case_dir),
            planning_stage_uids(planning),
            str(options.get("input_format") or "parquet"),
        )

    @property
    def n_stages(self) -> int:
        return len(self.stage_uids)

    def _table(self, class_name: str, name: str) -> _UidTable | None:
        base = build_table_path(self.input_root, class_name, name)
        if base not in self._tables:
            path = find_table_file(base, self.input_format)
            if path is None:
                self.missing_files.add(base)
                self._tables[base] = None
            else:
                try:
                    self._tables[base] = _load_uid_table(path)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning("cannot read %s: %s", path, exc)
                    self.missing_files.add(base)
                    self._tables[base] = None
        return self._tables[base]

    def per_stage(
        self,
        class_name: str,
        value: Any,
        uid: Any,
        *,
        reduce: Reduce = "max",
        default: float | None = None,
    ) -> list[float | None]:
        """Return the field value for every stage (``None`` = unknown).

        ``reduce`` collapses blocks / scenarios inside one stage.  An
        absent field, a missing table, a uid missing from its table or a
        stage missing from the table all yield ``default``.
        """
        n = self.n_stages
        if value is None:
            return [default] * n
        num = _as_number(value)
        if num is not None:
            return [num] * n
        if isinstance(value, list):
            return self._from_inline(value, reduce, default)
        if isinstance(value, str):
            return self._from_file(class_name, value, uid, reduce, default)
        return [default] * n

    def _from_inline(
        self, value: list[Any], reduce: Reduce, default: float | None
    ) -> list[float | None]:
        n = self.n_stages
        if len(value) == n:
            # Per stage (scalars) or per stage x block (nested lists).
            out = [_reduce_leaves(v, reduce) for v in value]
            return [default if v is None else v for v in out]
        # Unknown shape (e.g. flat per-block list): collapse globally.
        whole = _reduce_leaves(value, reduce)
        return [default if whole is None else whole] * n

    def _from_file(
        self,
        class_name: str,
        name: str,
        uid: Any,
        reduce: Reduce,
        default: float | None,
    ) -> list[float | None]:
        n = self.n_stages
        table = self._table(class_name, name)
        if table is None or not isinstance(uid, (int, float)):
            return [default] * n
        key = int(uid)
        pick = 0 if reduce == "min" else 1
        if key in table.per_uid:
            return [table.per_uid[key][pick]] * n
        stages = table.per_stage.get(key)
        if stages is None:
            self.missing_uids.add((build_table_path("", class_name, name), key))
            return [default] * n
        out: list[float | None] = []
        for suid in self.stage_uids:
            pair = stages.get(suid)
            out.append(default if pair is None else pair[pick])
        return out
