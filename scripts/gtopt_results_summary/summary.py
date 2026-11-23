# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Summary computation logic for gtopt_results_summary.

The public entry points are:

- :func:`summarize_results` — operates on a directory or ZIP file path.
- :func:`summarize_output_dict` — operates on a parsed results dict with the
  shape produced by :func:`guiservice.app._parse_results_zip`.
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

from gtopt_shared.solution_csv import parse_solution_csv, resolve_objective


# Known output file patterns (relative to the output directory) that the
# summary aggregator understands.  All other files are ignored — callers
# always get a well-defined KPI dict even when the output is incomplete.
_GEN_PATH = "Generator/generation_sol"
_GEN_COST_PATH = "Generator/generation_cost"
_LOAD_PATH = "Demand/load_sol"
_FAIL_PATH = "Demand/fail_sol"
_BAL_DUAL = "Bus/balance_dual"
_FLOW_PATH = "Line/flow_sol"
_SOC_PATH = "Battery/vfin_sol"
_SOLUTION = "solution"

# Group labels used when breaking down totals by technology.  Since the
# C++ solver does not tag generators with a technology, the GUI Plus
# frontend may provide a per-generator-uid technology mapping via the
# optional ``tech_map`` argument.
_DEFAULT_TECH = "other"


def _sum_numeric(df: pd.DataFrame, skip_cols: set[str]) -> dict[str, float]:
    """Return column-wise sums for every numeric column not in skip_cols."""
    totals: dict[str, float] = {}
    for col in df.columns:
        if col in skip_cols:
            continue
        try:
            totals[col] = float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())
        except (TypeError, ValueError):
            continue
    return totals


def _is_long(df: pd.DataFrame) -> bool:
    """True for the long output layout (bare ``uid`` + ``value`` columns,
    no per-element ``uid:N`` columns).  Mirrors
    ``gtopt_check_output._reader.dataset_layout``: gtopt switched the
    solution output to long (``output_layout: "long"``) in 2026-05, so the
    summary must read both shapes.  A wide dataset always has ``uid:N``
    columns and never a bare ``uid`` column; a long one is the reverse."""
    cols = {str(c) for c in df.columns}
    return (
        "uid" in cols
        and "value" in cols
        and not any(c.startswith("uid:") for c in cols)
    )


def _per_uid_totals(df: pd.DataFrame, skip_cols: set[str]) -> dict[str, float]:
    """Per-element column sums, layout-aware.

    Wide: sum each ``uid:N`` column (keyed by that column name).  Long:
    group the single ``value`` column by ``uid`` (keyed by the bare uid
    string).  Both feed ``_extract_tech_total`` and the element counts.
    """
    if _is_long(df):
        vals = pd.to_numeric(df["value"], errors="coerce").fillna(0)
        grouped = vals.groupby(df["uid"]).sum()
        return {str(k): float(v) for k, v in grouped.items()}
    return _sum_numeric(df, skip_cols)


def _extract_tech_total(
    per_uid_totals: dict[str, float], tech_map: dict[str, str] | None
) -> dict[str, float]:
    """Aggregate per-UID totals into per-technology totals.

    Column names are expected to follow the ``uid:<N>`` convention used by
    the gtopt output files.  When no technology map is provided, everything
    falls under ``_DEFAULT_TECH``.
    """
    by_tech: dict[str, float] = {}
    for col, val in per_uid_totals.items():
        uid_str = col.split(":", 1)[1] if col.startswith("uid:") else col
        tech = (tech_map or {}).get(uid_str, _DEFAULT_TECH)
        by_tech[tech] = by_tech.get(tech, 0.0) + val
    return by_tech


def _find_path(outputs: dict[str, Any], relpath: str) -> dict[str, Any] | None:
    """Return the outputs entry matching a parent/basename path.

    The outputs dict produced by _parse_results_zip uses keys with the
    pattern ``<parent>/<basename>``.  We look up ``Generator/generation_sol``
    directly and also under a bare ``generation_sol`` key for robustness.
    """
    if relpath in outputs:
        return outputs[relpath]
    # Fall back to just the basename
    base = relpath.split("/")[-1]
    if base in outputs:
        return outputs[base]
    return None


def _rows_to_df(entry: dict[str, Any]) -> pd.DataFrame:
    """Turn a ``{columns, data}`` entry into a pandas DataFrame."""
    cols = entry.get("columns", [])
    data = entry.get("data", [])
    if not cols or not data:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(data, columns=cols)


# ---------------------------------------------------------------------------
# Public API (dict-based)
# ---------------------------------------------------------------------------


def summarize_output_dict(
    results: dict[str, Any],
    tech_map: dict[str, str] | None = None,
    scale_objective: float | None = None,
) -> dict[str, Any]:
    """Compute KPI summary from a parsed results dict.

    The ``results`` dict has the shape produced by
    :func:`guiservice.app._parse_results_zip`:

    - ``solution``: flat dict (e.g. ``obj_value``, ``status``)
    - ``outputs``: ``{"<parent>/<basename>": {"columns": [...], "data": [...]}}``

    Args:
        results: The parsed results dict.
        tech_map: Optional dict mapping generator UIDs (as strings) to a
            technology label.  When provided, generation is broken down by
            technology; otherwise, everything falls under ``other``.
        scale_objective: Optional override / check of the objective scale
            factor.  ``solution.csv`` already reports ``obj_value`` in
            physical units and records ``scale_objective`` in its own column,
            so this is only needed for legacy outputs written before that
            column existed (see :mod:`gtopt_shared.solution_csv`).  When it
            disagrees with the column a
            :class:`~gtopt_shared.solution_csv.ScaleObjectiveMismatchError`
            is raised.

    Returns:
        A dict with the following keys (all values are plain Python types):

        - ``status`` (int | str | None) — solver status from ``solution.csv``
        - ``obj_value`` (float | None) — objective in physical cost units
        - ``obj_value_raw`` (float | None) — LP-raw objective
          (``obj_value / scale_objective``; ``None`` if the factor is unknown)
        - ``scale_objective`` (float | None) — objective scale factor
        - ``obj_convention`` (str | None) — how ``obj_value`` was read
          (``column`` / ``physical`` / ``legacy-raw``)
        - ``total_generation`` (float) — sum of Generator/generation_sol
        - ``generation_by_tech`` (dict[str, float])
        - ``total_cost_generation`` (float) — sum of Generator/generation_cost
        - ``total_load`` (float) — sum of Demand/load_sol
        - ``total_unserved`` (float) — sum of Demand/fail_sol
        - ``peak_unserved`` (float) — max single-block unserved load
        - ``n_generators`` (int)
        - ``n_buses`` (int)
        - ``n_lines`` (int)
        - ``lmp_min`` / ``lmp_max`` / ``lmp_mean`` (float | None) — from Bus/balance_dual
        - ``n_blocks`` (int) — number of (scenario, stage, block) rows in Generator/generation_sol
    """
    outputs = results.get("outputs", {})
    solution = results.get("solution", {})

    summary: dict[str, Any] = {
        "status": solution.get("status"),
        "obj_value": None,
        "obj_value_raw": None,
        "scale_objective": None,
        "obj_convention": None,
        "total_generation": 0.0,
        "generation_by_tech": {},
        "total_cost_generation": 0.0,
        "total_load": 0.0,
        "total_unserved": 0.0,
        "peak_unserved": 0.0,
        "n_generators": 0,
        "n_buses": 0,
        "n_lines": 0,
        "lmp_min": None,
        "lmp_max": None,
        "lmp_mean": None,
        "n_blocks": 0,
    }

    objective = resolve_objective(solution, scale_objective=scale_objective)
    if objective is not None:
        summary["obj_value"] = objective.value
        summary["obj_value_raw"] = objective.raw
        summary["scale_objective"] = objective.scale_objective
        summary["obj_convention"] = objective.convention

    # Indexing columns present in every per-block CSV.
    skip_cols = {"scenario", "stage", "block", "phase", "scene"}

    def _n_blocks(df: pd.DataFrame) -> int:
        """Distinct block count (long) or row count (wide fallback)."""
        if _is_long(df) and "block" in df.columns:
            return int(df["block"].nunique())
        return len(df)

    def _n_elements(df: pd.DataFrame, per_uid: dict[str, float]) -> int:
        """Element count — distinct ``uid`` (long) or ``uid:N`` column
        count (wide, == len(per_uid))."""
        if _is_long(df):
            return int(df["uid"].nunique())
        return len(per_uid)

    def _value_series(df: pd.DataFrame):
        """Numeric data series for min/max/mean stats, layout-aware: the
        ``value`` column (long) or every non-index column flattened (wide)."""
        if _is_long(df):
            return pd.to_numeric(df["value"], errors="coerce").dropna()
        cols = [c for c in df.columns if c not in skip_cols]
        if not cols:
            return pd.Series(dtype=float)
        return pd.to_numeric(df[cols].stack(), errors="coerce").dropna()

    # Generation
    gen = _find_path(outputs, _GEN_PATH)
    if gen is not None:
        df = _rows_to_df(gen)
        totals = _per_uid_totals(df, skip_cols)
        summary["total_generation"] = float(sum(totals.values()))
        summary["generation_by_tech"] = _extract_tech_total(totals, tech_map)
        summary["n_generators"] = _n_elements(df, totals)
        summary["n_blocks"] = _n_blocks(df)

    # Generation cost
    gen_cost = _find_path(outputs, _GEN_COST_PATH)
    if gen_cost is not None:
        df = _rows_to_df(gen_cost)
        summary["total_cost_generation"] = float(
            sum(_per_uid_totals(df, skip_cols).values())
        )

    # Load
    load = _find_path(outputs, _LOAD_PATH)
    if load is not None:
        df = _rows_to_df(load)
        summary["total_load"] = float(sum(_per_uid_totals(df, skip_cols).values()))

    # Unserved demand
    fail = _find_path(outputs, _FAIL_PATH)
    if fail is not None:
        df = _rows_to_df(fail)
        per_col = _per_uid_totals(df, skip_cols)
        summary["total_unserved"] = float(sum(per_col.values()))
        # Peak single-cell unserved across all cells
        vals = _value_series(df)
        summary["peak_unserved"] = float(vals.max()) if not vals.empty else 0.0

    # LMPs from balance_dual
    bal = _find_path(outputs, _BAL_DUAL)
    if bal is not None:
        df = _rows_to_df(bal)
        vals = _value_series(df)
        if not vals.empty:
            summary["lmp_min"] = float(vals.min())
            summary["lmp_max"] = float(vals.max())
            summary["lmp_mean"] = float(vals.mean())
        summary["n_buses"] = _n_elements(df, _per_uid_totals(df, skip_cols))

    # Line count (from flow_sol) if available.  Falls back to the legacy
    # ``flowp_sol`` stem when the case was produced by a pre-unified-flow
    # gtopt build.
    flow = _find_path(outputs, _FLOW_PATH) or _find_path(outputs, "Line/flowp_sol")
    if flow is not None:
        df = _rows_to_df(flow)
        summary["n_lines"] = _n_elements(df, _per_uid_totals(df, skip_cols))

    return summary


# ---------------------------------------------------------------------------
# Public API (filesystem-based)
# ---------------------------------------------------------------------------


# gtopt writes one CSV shard per (scene, phase): ``<stem>_s<N>_p<M>.csv``,
# and Parquet as a hive dataset ``<stem>.parquet/scene=N/phase=M/part.parquet``.
_SHARD_SUFFIX_RE = re.compile(r"_s\d+_p\d+$")
_CSV_EXTS = (".csv.gz", ".csv.zst", ".csv")
# Hive partition columns pandas materialises; they duplicate scenario/stage.
_PARTITION_COLS = ("scene", "phase")


def _output_key(parts: list[str]) -> str | None:
    """Map a relative output path (split on ``/``) to ``<Class>/<stem>``.

    Handles the three layouts gtopt has produced: legacy single file
    (``<Class>/<stem>.csv``), CSV shards (``<Class>/<stem>_s0_p0.csv``)
    and hive Parquet (``<Class>/<stem>.parquet/scene=0/phase=0/part.parquet``).
    Returns ``None`` for files that are not output tables.
    """
    for i, part in enumerate(parts[:-1]):
        if part.endswith(".parquet"):
            stem = part[: -len(".parquet")]
            parent = parts[i - 1] if i > 0 else ""
            return f"{parent}/{stem}" if parent else stem
    base = parts[-1]
    for ext in (*_CSV_EXTS, ".parquet"):
        if base.lower().endswith(ext):
            stem = _SHARD_SUFFIX_RE.sub("", base[: -len(ext)])
            parent = parts[-2] if len(parts) > 1 else ""
            return f"{parent}/{stem}" if parent else stem
    return None


def _read_table(name: str, raw: bytes | Path) -> pd.DataFrame:
    """Read one CSV / Parquet output file (path or in-memory bytes)."""
    src: Any = raw if isinstance(raw, Path) else io.BytesIO(raw)
    if name.lower().endswith(".parquet"):
        df = pd.read_parquet(src)
        return df.drop(columns=[c for c in _PARTITION_COLS if c in df.columns])
    compression: Any = "infer"
    if not isinstance(raw, Path):
        if name.endswith(".gz"):
            compression = "gzip"
        elif name.endswith(".zst"):
            compression = "zstd"
    return pd.read_csv(src, compression=compression)


def _collect(
    results: dict[str, Any], frames: dict[str, list[pd.DataFrame]]
) -> dict[str, Any]:
    """Concatenate the per-shard frames into ``results['outputs']``."""
    for key, parts in sorted(frames.items()):
        df = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
        results["outputs"][key] = {
            "columns": list(df.columns),
            "data": df.values.tolist(),
        }
    return results


def _read_output_dir(path: Path) -> dict[str, Any]:
    """Walk an output directory and build a dict mimicking _parse_results_zip."""
    results: dict[str, Any] = {"solution": {}, "outputs": {}, "terminal_output": ""}
    frames: dict[str, list[pd.DataFrame]] = {}
    for full in sorted(path.rglob("*")):
        if not full.is_file():
            continue
        parts = list(full.relative_to(path).parts)
        try:
            if parts == ["solution.csv"]:
                results["solution"] = parse_solution_csv(
                    full.read_text(encoding="utf-8")
                )
                continue
            key = _output_key(parts)
            if key is None:
                continue
            # Read before registering the key: an unreadable shard must not
            # leave an empty frame list behind (``_collect`` indexes [0]).
            df = _read_table(full.name, full)
            frames.setdefault(key, []).append(df)
        except (OSError, ValueError, pd.errors.ParserError):
            continue
    return _collect(results, frames)


def _read_zip(path: Path) -> dict[str, Any]:
    """Parse a results ZIP into the same dict shape as _parse_results_zip."""
    # Defer the import to avoid creating a circular dependency at import time.
    try:
        # pylint: disable=import-outside-toplevel
        from guiservice.app import _parse_results_zip
    except ImportError:
        _parse_results_zip = None

    if _parse_results_zip is not None:
        with open(path, "rb") as fh:
            return _parse_results_zip(fh.read())

    # Fallback: do the parse inline
    results: dict[str, Any] = {"solution": {}, "outputs": {}, "terminal_output": ""}
    frames: dict[str, list[pd.DataFrame]] = {}
    with zipfile.ZipFile(path, "r") as zf:
        for name in sorted(zf.namelist()):
            parts = [p for p in name.split("/") if p]
            if not parts or name.endswith("/"):
                continue
            try:
                if parts[-1] == "solution.csv":
                    results["solution"] = parse_solution_csv(
                        zf.read(name).decode("utf-8")
                    )
                    continue
                key = _output_key(parts)
                if key is None:
                    continue
                df = _read_table(parts[-1], zf.read(name))
                frames.setdefault(key, []).append(df)
            except (OSError, ValueError, pd.errors.ParserError):
                continue
    return _collect(results, frames)


def summarize_results(
    source: str | os.PathLike[str],
    tech_map: dict[str, str] | None = None,
    scale_objective: float | None = None,
) -> dict[str, Any]:
    """Summarize results from a directory or ZIP file path.

    Args:
        source: Path to a directory or ZIP file containing gtopt output files.
        tech_map: Optional per-UID technology mapping (see
            :func:`summarize_output_dict`).
        scale_objective: Optional objective-scale override for legacy
            outputs / consistency check (see :func:`summarize_output_dict`).

    Returns:
        A KPI summary dict (see :func:`summarize_output_dict`).
    """
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"No such file or directory: {path}")
    if path.is_dir():
        results = _read_output_dir(path)
    elif path.suffix.lower() == ".zip":
        results = _read_zip(path)
    else:
        raise ValueError(f"Expected a directory or .zip file, got {path}")
    return summarize_output_dict(
        results, tech_map=tech_map, scale_objective=scale_objective
    )
