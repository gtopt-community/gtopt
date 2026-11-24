# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Read the planning + solved outputs the uplift metrics need."""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from gtopt_check_output._reader import dataset_layout, open_dataset
from gtopt_shared.field_sched import (
    build_table_path,
    find_table_file,
    planning_input_root,
)

log = logging.getLogger(__name__)

#: (scenario, stage, block, uid) -> value
SeriesMap = dict[tuple[int, int, int, int], float]

_KEY = ["scenario", "stage", "block", "uid"]


@dataclass(frozen=True)
class Period:
    """One chronological block of one scenario."""

    scenario: int
    stage: int
    block: int
    duration: float
    stage_index: int
    block_index: int  #: position of the block inside its stage


@dataclass
class Case:
    """Planning-side description of the solved case."""

    planning: dict[str, Any]
    input_dir: Path
    #: scenario uid -> normalised probability weight
    scenario_weight: dict[int, float] = field(default_factory=dict)
    #: scenario uid -> chronological periods
    periods: dict[int, list[Period]] = field(default_factory=dict)
    bus_uid: dict[str, int] = field(default_factory=dict)
    bus_name: dict[int, str] = field(default_factory=dict)


def _uid_of(ref: Any, by_name: Mapping[str, int]) -> int | None:
    """Resolve an element reference (uid or name) to a uid."""
    if isinstance(ref, bool):
        return None
    if isinstance(ref, (int, float)):
        return int(ref)
    if isinstance(ref, str):
        if ref in by_name:
            return by_name[ref]
        if ref.lstrip("-").isdigit():
            return int(ref)
    return None


def name_index(items: list[dict[str, Any]]) -> dict[str, int]:
    """``name -> uid`` of a planning element array."""
    return {
        str(it["name"]): int(it["uid"])
        for it in items
        if "name" in it and isinstance(it.get("uid"), (int, float))
    }


def resolve_ref(ref: Any, items: list[dict[str, Any]]) -> int | None:
    """Resolve a uid-or-name reference against an element array."""
    return _uid_of(ref, name_index(items))


def load_case(planning_path: Path, case_dir: Path | None = None) -> Case:
    """Load the planning JSON and derive the chronological period list."""
    with open(planning_path, encoding="utf-8") as fh:
        planning = json.load(fh)
    base = case_dir if case_dir is not None else planning_path.parent
    case = Case(planning=planning, input_dir=planning_input_root(planning, base))

    sim = planning.get("simulation", {}) or {}
    blocks = sim.get("block_array", []) or []
    stages = sim.get("stage_array", []) or []
    if not stages:
        stages = [{"uid": 1, "first_block": 0, "count_block": len(blocks)}]
    scenarios = sim.get("scenario_array", []) or [{"uid": 1}]

    chrono: list[tuple[int, int, float, int, int]] = []
    for s_idx, st in enumerate(stages):
        first = int(st.get("first_block", 0))
        count = int(st.get("count_block", len(blocks) - first))
        for b_idx, blk in enumerate(blocks[first : first + count]):
            chrono.append(
                (
                    int(st.get("uid", s_idx + 1)),
                    int(blk.get("uid", first + b_idx + 1)),
                    float(blk.get("duration", 1.0)),
                    s_idx,
                    b_idx,
                )
            )

    total_p = sum(float(sc.get("probability_factor", 1.0)) for sc in scenarios)
    for sc in scenarios:
        suid = int(sc.get("uid", 1))
        prob = float(sc.get("probability_factor", 1.0))
        case.scenario_weight[suid] = prob / total_p if total_p > 0 else 1.0
        case.periods[suid] = [
            Period(suid, stage, block, dur, s_idx, b_idx)
            for stage, block, dur, s_idx, b_idx in chrono
        ]

    buses = planning.get("system", {}).get("bus_array", []) or []
    case.bus_uid = name_index(buses)
    case.bus_name = {int(b["uid"]): str(b.get("name", b["uid"])) for b in buses}
    return case


def load_series(results_dir: Path, stem: str) -> SeriesMap | None:
    """Load an output stream as ``{(scenario, stage, block, uid): value}``.

    Accepts the long and the wide (``uid:N``) layouts, parquet or CSV
    shards.  Returns ``None`` when the stream is absent.  Entries the
    writer elided (zeros) are simply missing — callers default to 0.
    """
    dataset = open_dataset(results_dir, stem)
    if dataset is None:
        return None
    names = set(dataset.schema.names)
    id_cols = [c for c in ("scenario", "stage", "block") if c in names]
    if dataset_layout(dataset) == "long":
        df = dataset.to_table(columns=id_cols + ["uid", "value"]).to_pandas()
    else:
        uid_cols = [n for n in dataset.schema.names if n.startswith("uid:")]
        df = dataset.to_table(columns=id_cols + uid_cols).to_pandas()
        df = df.melt(
            id_vars=id_cols, value_vars=uid_cols, var_name="uid", value_name="value"
        )
        df["uid"] = df["uid"].str.split(":").str[1].astype("int64")
    for col in ("scenario", "stage", "block"):
        if col not in df.columns:
            df[col] = 1
    df = df.dropna(subset=["value"])
    out: SeriesMap = {}
    for row in df[_KEY + ["value"]].itertuples(index=False):
        out[(int(row[0]), int(row[1]), int(row[2]), int(row[3]))] = float(row[4])
    return out


@cache
def _read_table(path: Path) -> pd.DataFrame:
    if ".parquet" in path.name:
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    if "uid" not in df.columns or "value" not in df.columns:
        uid_cols = [c for c in df.columns if str(c).startswith("uid:")]
        index_cols = [c for c in df.columns if c in ("scenario", "stage", "block")]
        df = df.melt(
            id_vars=index_cols, value_vars=uid_cols, var_name="uid", value_name="value"
        )
        df["uid"] = df["uid"].str.slice(4).astype(int)
    return df


def sched_value(
    value: Any,
    period: Period,
    uid: int,
    *,
    class_name: str,
    input_dir: Path,
) -> float | None:
    """Resolve one ``FieldSched`` value at ``period`` (``None`` if unset).

    Scalars broadcast; a 1-D array is per stage, a 2-D array per
    ``[stage][block]``; a string names an input table
    ``<input_dir>/<Class>/<name>`` (long or wide layout).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list):
        if not value:
            return None
        row = value[min(period.stage_index, len(value) - 1)]
        if isinstance(row, list):
            if not row:
                return None
            return float(row[min(period.block_index, len(row) - 1)])
        return float(row)
    if isinstance(value, str):
        path = find_table_file(build_table_path(input_dir, class_name, value))
        if path is None:
            log.warning(
                "schedule table %s/%s/%s not found", input_dir, class_name, value
            )
            return None
        df = _read_table(path)
        sel = df[df["uid"] == uid]
        for col, key in (
            ("scenario", period.scenario),
            ("stage", period.stage),
            ("block", period.block),
        ):
            if col in sel.columns and (sel[col] == key).any():
                sel = sel[sel[col] == key]
        if sel.empty:
            return None
        return float(sel["value"].iloc[0])
    return None


def read_pricing_csv(results_dir: Path) -> list[dict[str, str]]:
    """Rows of ``pricing.csv`` (empty when the run wrote none)."""
    path = results_dir / "pricing.csv"
    if not path.is_file():
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))
