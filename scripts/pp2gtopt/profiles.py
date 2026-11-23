# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Hourly load / renewable profiles for pp2gtopt.

Reads a profile table (CSV or Parquet, *wide* or *long*) plus an optional
mapping spec (JSON / YAML) and resolves every profile column to the
pandapower elements it drives:

* ``load``  → per-load MW series → ``Demand.lmax`` schedule.
* ``gen`` / ``sgen`` → per-unit availability (MW ÷ rated) →
  ``CapacityProfile`` (``GeneratorProfile/profile.parquet``).

Column naming without a spec
----------------------------
``load:<ref>`` / ``gen:<ref>`` / ``sgen:<ref>``
    ``<ref>`` is the element *name* when one matches, else the pandapower
    index.
``load@bus:<b>`` / ``sgen@bus:<b>`` / …
    every element of that kind on bus ``<b>`` (bus *name*, i.e. the
    MATPOWER bus number for ``from_mpc`` networks; falls back to the
    pandapower bus index when buses carry no names).
``load@zone:<z>`` / ``load@all``
    every element of that kind in bus zone ``<z>`` / in the network.
bare ``<name>``
    unique match against load, then sgen, then gen names.

Multi-element targets (bus / zone / all) split an MW value in
proportion to each element's base (``p_mw`` for loads, rated MW for
generators); a per-unit value multiplies each element's base.

Spec file (JSON or YAML; every key optional)::

    layout: auto            # auto | wide | long
    time_column: hour       # wide/long time column (auto-detected)
    id_column: id           # long layout: element id column
    element_column: element # long layout: element-kind column (optional)
    value_column: value     # long layout: value column
    units: {load: mw, gen: auto, sgen: auto}   # mw | pu | auto
    default: {element: load, by: bus}          # for unmapped columns
    ignore: [total]                            # columns to skip
    columns:
      R1: {element: load, by: zone, ref: "1"}
      Wind_7: {element: sgen, by: name, ref: "W7", units: mw, scale: 1.0}

``units: auto`` (default for generators) treats a column whose maximum
is ≤ 1 as per-unit and anything larger as MW.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ELEMENT_KINDS: tuple[str, ...] = ("load", "gen", "sgen")
SELECTORS: tuple[str, ...] = (
    "auto",
    "name",
    "index",
    "bus",
    "bus_index",
    "zone",
    "all",
)
UNITS: tuple[str, ...] = ("mw", "pu", "auto")
_DEFAULT_UNITS: dict[str, str] = {"load": "mw", "gen": "auto", "sgen": "auto"}
_TIME_COLUMNS: tuple[str, ...] = (
    "hour",
    "hours",
    "time",
    "timestamp",
    "datetime",
    "date",
    "t",
    "period",
    "snapshot",
)
_LONG_ID_COLUMNS: tuple[str, ...] = ("id", "element_id", "name", "column", "uid")
_PREFIX_RE = re.compile(r"^(load|gen|sgen)(?:@(bus|bus_index|zone|all))?(?::(.*))?$")
_PU_AUTO_THRESHOLD = 1.0 + 1e-9


class ProfileError(ValueError):
    """Raised for invalid profile data or specs (negative load, bad spec…)."""


@dataclass(frozen=True)
class ColumnTarget:
    """Which pandapower elements a profile column drives."""

    element: str
    by: str = "auto"
    ref: str | None = None
    units: str | None = None  # None → spec / element default
    scale: float = 1.0

    @classmethod
    def from_dict(cls, data: dict[str, Any], where: str) -> "ColumnTarget":
        """Build from a spec mapping entry; validates keys and values."""
        unknown = set(data) - {"element", "by", "ref", "units", "scale"}
        if unknown:
            raise ProfileError(f"{where}: unknown keys {sorted(unknown)}")
        element = str(data.get("element", "load")).lower()
        by = str(data.get("by", "auto")).lower()
        units = data.get("units")
        if element not in ELEMENT_KINDS:
            raise ProfileError(f"{where}: element must be one of {ELEMENT_KINDS}")
        if by not in SELECTORS:
            raise ProfileError(f"{where}: by must be one of {SELECTORS}")
        if units is not None and str(units).lower() not in UNITS:
            raise ProfileError(f"{where}: units must be one of {UNITS}")
        ref = data.get("ref")
        return cls(
            element=element,
            by=by,
            ref=None if ref is None else str(ref),
            units=None if units is None else str(units).lower(),
            scale=float(data.get("scale", 1.0)),
        )


@dataclass
class ProfileSpec:
    """Parsed ``--profiles-spec`` file (all fields optional)."""

    layout: str = "auto"
    time_column: str | None = None
    id_column: str | None = None
    element_column: str | None = None
    value_column: str | None = None
    units: dict[str, str] = field(default_factory=dict)
    default: ColumnTarget | None = None
    ignore: list[str] = field(default_factory=list)
    columns: dict[str, ColumnTarget] = field(default_factory=dict)

    def units_for(self, target: ColumnTarget) -> str:
        """Effective units of a column: column > spec per-element > default."""
        if target.units is not None:
            return target.units
        return self.units.get(target.element, _DEFAULT_UNITS[target.element])


def load_spec(path: Path | None) -> ProfileSpec:
    """Load a JSON / YAML profile spec; ``None`` → empty default spec."""
    if path is None:
        return ProfileSpec()
    text = path.read_text(encoding="utf-8")
    data: Any
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml  # noqa: PLC0415  pylint: disable=import-outside-toplevel

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ProfileError(f"{path}: spec must be a mapping")
    known = {
        "layout",
        "time_column",
        "id_column",
        "element_column",
        "value_column",
        "units",
        "default",
        "ignore",
        "columns",
    }
    unknown = set(data) - known
    if unknown:
        raise ProfileError(f"{path}: unknown spec keys {sorted(unknown)}")
    layout = str(data.get("layout", "auto")).lower()
    if layout not in ("auto", "wide", "long"):
        raise ProfileError(f"{path}: layout must be auto, wide or long")
    units = {
        str(k).lower(): str(v).lower() for k, v in (data.get("units") or {}).items()
    }
    for kind, unit in units.items():
        if kind not in ELEMENT_KINDS or unit not in UNITS:
            raise ProfileError(f"{path}: bad units entry {kind}: {unit}")
    default = data.get("default")
    return ProfileSpec(
        layout=layout,
        time_column=data.get("time_column"),
        id_column=data.get("id_column"),
        element_column=data.get("element_column"),
        value_column=data.get("value_column"),
        units=units,
        default=(
            ColumnTarget.from_dict(default, f"{path}: default")
            if default is not None
            else None
        ),
        ignore=[str(c) for c in data.get("ignore") or []],
        columns={
            str(col): ColumnTarget.from_dict(tgt, f"{path}: columns.{col}")
            for col, tgt in (data.get("columns") or {}).items()
        },
    )


# ---------------------------------------------------------------------------
# Reading the profile table → wide (rows = hours, columns = profile names)
# ---------------------------------------------------------------------------


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in (".csv", ".txt", ".gz"):
        return pd.read_csv(path)
    raise ProfileError(f"{path}: unsupported profile format (use .csv or .parquet)")


def _find_time_column(df: pd.DataFrame, spec: ProfileSpec) -> str | None:
    if spec.time_column is not None:
        if spec.time_column not in df.columns:
            raise ProfileError(f"time_column {spec.time_column!r} not in table")
        return spec.time_column
    lowered = {str(c).lower(): c for c in df.columns}
    for cand in _TIME_COLUMNS:
        if cand in lowered:
            return str(lowered[cand])
    return None


def _is_long(df: pd.DataFrame, spec: ProfileSpec) -> bool:
    if spec.layout != "auto":
        return spec.layout == "long"
    cols = {str(c).lower() for c in df.columns}
    value_col = (spec.value_column or "value").lower()
    has_id = spec.id_column is not None or any(c in cols for c in _LONG_ID_COLUMNS)
    return value_col in cols and has_id and len(cols) <= 5


def _sort_by_time(df: pd.DataFrame, time_col: str | None) -> pd.DataFrame:
    if time_col is None:
        return df.reset_index(drop=True)
    return df.sort_values(time_col, kind="stable").reset_index(drop=True)


def _long_to_wide(
    df: pd.DataFrame, spec: ProfileSpec
) -> tuple[pd.DataFrame, pd.Series]:
    lowered = {str(c).lower(): str(c) for c in df.columns}
    time_col = _find_time_column(df, spec)
    if time_col is None:
        raise ProfileError("long-layout profiles need a time column (hour/time/…)")
    value_col = spec.value_column or lowered.get("value", "value")
    id_col = spec.id_column
    if id_col is None:
        id_col = next((lowered[c] for c in _LONG_ID_COLUMNS if c in lowered), None)
    if id_col is None or value_col not in df.columns:
        raise ProfileError("long-layout profiles need id and value columns")
    elem_col = spec.element_column
    if elem_col is None and "element" in lowered:
        elem_col = lowered["element"]
    keys = df[id_col].astype(str)
    if elem_col is not None:
        keys = df[elem_col].astype(str).str.lower() + ":" + keys
    work = pd.DataFrame(
        {"_t": df[time_col], "_k": keys, "_v": pd.to_numeric(df[value_col])}
    )
    if work.duplicated(["_t", "_k"]).any():
        raise ProfileError("long-layout profiles have duplicate (time, id) rows")
    wide = work.pivot(index="_t", columns="_k", values="_v").sort_index()
    wide.columns = [str(c) for c in wide.columns]
    return wide.reset_index(drop=True), pd.Series(wide.index)


def _as_timestamps(values: pd.Series | None) -> pd.DatetimeIndex | None:
    """Calendar timestamps of the time column, or ``None`` (hour index)."""
    if values is None or pd.api.types.is_numeric_dtype(values):
        return None
    times = pd.to_datetime(values, errors="coerce")
    if times.isna().any():
        logger.warning("profiles: time column is not parseable as dates; ignored")
        return None
    return pd.DatetimeIndex(times)


def read_profile_table(path: Path, spec: ProfileSpec) -> pd.DataFrame:
    """Read a profile file into a wide float table (rows = hours in order).

    The time column (if any) only orders the rows; hour *positions*
    (0-based) are what ``--hours`` selects.
    """
    return read_profile_table_with_times(path, spec)[0]


def read_profile_table_with_times(
    path: Path, spec: ProfileSpec
) -> tuple[pd.DataFrame, pd.DatetimeIndex | None]:
    """Like :func:`read_profile_table`, plus the row timestamps.

    The timestamps are returned when the time column holds dates /
    datetimes (used for calendar stages such as months); an integer hour
    column or no time column gives ``None``.
    """
    df = _read_table(path)
    times_col: pd.Series | None
    if _is_long(df, spec):
        wide, times_col = _long_to_wide(df, spec)
    else:
        time_col = _find_time_column(df, spec)
        df = _sort_by_time(df, time_col)
        times_col = df[time_col] if time_col is not None else None
        wide = df.drop(columns=[time_col]) if time_col is not None else df
        wide.columns = [str(c) for c in wide.columns]
    times = _as_timestamps(times_col)
    drop = [c for c in wide.columns if c in set(spec.ignore)]
    wide = wide.drop(columns=drop)
    non_numeric = [
        c for c in wide.columns if not pd.api.types.is_numeric_dtype(wide[c])
    ]
    if non_numeric:
        logger.warning(
            "profiles: ignoring %d non-numeric column(s): %s",
            len(non_numeric),
            ", ".join(non_numeric[:10]),
        )
        wide = wide.drop(columns=non_numeric)
    if wide.isna().any().any():
        bad = [c for c in wide.columns if wide[c].isna().any()]
        raise ProfileError(
            f"profiles: missing values in column(s) {', '.join(bad[:10])}"
        )
    return wide.astype(np.float64), times


def profiles_from_net_controllers(net: Any) -> pd.DataFrame:
    """Extract ``ConstControl`` p_mw profiles carried by a pandapower net.

    Every controller whose ``variable`` is ``p_mw`` / ``max_p_mw`` on a
    load / gen / sgen and whose data source exposes a DataFrame (``DFData``)
    contributes ``<element>:<index>`` MW columns.  Returns an empty frame
    when the net has no usable controllers.
    """
    columns: dict[str, np.ndarray] = {}
    ctrl_df = getattr(net, "controller", None)
    if ctrl_df is None or len(ctrl_df) == 0 or "object" not in ctrl_df:
        return pd.DataFrame()
    for ctrl in ctrl_df["object"]:
        element = str(getattr(ctrl, "element", ""))
        variable = str(getattr(ctrl, "variable", ""))
        source = getattr(getattr(ctrl, "data_source", None), "df", None)
        if element not in ELEMENT_KINDS or variable not in ("p_mw", "max_p_mw"):
            continue
        if source is None:
            continue
        indices = np.atleast_1d(getattr(ctrl, "element_index", []))
        names = np.atleast_1d(getattr(ctrl, "profile_name", []))
        scale = float(getattr(ctrl, "scale_factor", 1.0) or 1.0)
        for idx, pname in zip(indices, names):
            if pname not in source.columns:
                logger.warning("ConstControl profile %r not in data source", pname)
                continue
            columns[f"{element}:{int(idx)}"] = (
                source[pname].to_numpy(dtype=np.float64) * scale
            )
    if not columns:
        return pd.DataFrame()
    lengths = {len(v) for v in columns.values()}
    if len(lengths) != 1:
        raise ProfileError("ConstControl data sources have different lengths")
    logger.info("profiles: %d column(s) taken from net.controller", len(columns))
    return pd.DataFrame(columns)


# ---------------------------------------------------------------------------
# Column → element resolution
# ---------------------------------------------------------------------------


def _element_table(net: Any, kind: str) -> pd.DataFrame:
    table = getattr(net, kind)
    if "in_service" in table:
        table = table[table["in_service"].fillna(True).astype(bool)]
    return table


def element_base_mw(net: Any, kind: str, idx: int) -> float:
    """Base MW of an element: load ``p_mw``; generator rated MW."""
    row = getattr(net, kind).loc[idx]
    if kind == "load":
        return float(row["p_mw"])
    pmax = row.get("max_p_mw", np.nan)
    if pmax is not None and np.isfinite(pmax) and float(pmax) > 0:
        return float(pmax)
    return float(row.get("p_mw", 0.0) or 0.0)


def _norm_key(value: Any) -> str:
    """Stringify an identifier; integral floats lose their ``.0``."""
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    return str(value).strip()


def _norm_series(series: pd.Series) -> pd.Series:
    return series.map(_norm_key)


def _bus_lookup(net: Any) -> tuple[dict[str, int], bool]:
    names = net.bus["name"] if "name" in net.bus else None
    if names is None or names.isna().all():
        return {str(i): int(i) for i in net.bus.index}, False
    return {_norm_key(n): int(i) for i, n in names.items() if n is not None}, True


def _str_index_match(table: pd.DataFrame, ref: str) -> list[int]:
    try:
        idx = int(float(ref))
    except ValueError:
        return []
    return [idx] if idx in table.index else []


def _resolve_target(net: Any, target: ColumnTarget) -> list[int]:
    """pandapower indices of ``target.element`` selected by ``target``."""
    table = _element_table(net, target.element)
    ref = target.ref
    by = target.by
    if by == "all":
        return [int(i) for i in table.index]
    if ref is None:
        return []
    if by in ("auto", "name"):
        names = table["name"].astype(str).str.strip() if "name" in table else None
        hits = [int(i) for i in table.index[names == ref]] if names is not None else []
        if hits or by == "name":
            return hits
        return _str_index_match(table, ref)
    if by == "index":
        return _str_index_match(table, ref)
    if by in ("bus", "bus_index"):
        if by == "bus":
            lookup, _named = _bus_lookup(net)
            bus = lookup.get(_norm_key(ref))
        else:
            hits = _str_index_match(net.bus, ref)
            bus = hits[0] if hits else None
        if bus is None:
            return []
        return [int(i) for i in table.index[table["bus"] == bus]]
    # by == "zone"
    if "zone" not in net.bus:
        return []
    zone_buses = net.bus.index[_norm_series(net.bus["zone"]) == _norm_key(ref)]
    return [int(i) for i in table.index[table["bus"].isin(zone_buses)]]


def _target_for_column(net: Any, column: str, spec: ProfileSpec) -> ColumnTarget | None:
    if column in spec.columns:
        return spec.columns[column]
    match = _PREFIX_RE.match(column)
    if match:
        kind, sel, ref = match.group(1), match.group(2), match.group(3)
        return ColumnTarget(element=kind, by=sel or "auto", ref=ref)
    if spec.default is not None:
        return ColumnTarget(
            element=spec.default.element,
            by=spec.default.by,
            ref=column,
            units=spec.default.units,
            scale=spec.default.scale,
        )
    for kind in ("load", "sgen", "gen"):
        table = _element_table(net, kind)
        if "name" not in table:
            continue
        hits = table.index[table["name"].astype(str).str.strip() == column]
        if len(hits) == 1:
            return ColumnTarget(element=kind, by="name", ref=column)
    return None


@dataclass
class ResolvedProfiles:
    """Hourly MW per element, after column → element resolution."""

    n_hours: int
    load_mw: dict[int, np.ndarray] = field(default_factory=dict)
    gen_mw: dict[tuple[str, int], np.ndarray] = field(default_factory=dict)
    unmatched_columns: list[str] = field(default_factory=list)
    column_energy_mwh: dict[str, float] = field(default_factory=dict)

    def generator_factors(self, net: Any) -> dict[tuple[str, int], np.ndarray]:
        """Availability factor per generator (MW ÷ rated, clipped to [0, 1])."""
        factors: dict[tuple[str, int], np.ndarray] = {}
        for (kind, idx), mw in self.gen_mw.items():
            base = element_base_mw(net, kind, idx)
            if base <= 0:
                logger.warning(
                    "profiles: %s %d has no rated MW; profile ignored", kind, idx
                )
                continue
            factor = mw / base
            peak = float(factor.max(initial=0.0))
            if peak > 1.0 + 1e-6:
                logger.warning(
                    "profiles: %s %d availability peaks at %.3f × rated "
                    "(%.1f MW); clipped to 1",
                    kind,
                    idx,
                    peak,
                    base,
                )
            factors[(kind, idx)] = np.clip(factor, 0.0, 1.0)
        return factors


def _column_units(values: np.ndarray, target: ColumnTarget, spec: ProfileSpec) -> str:
    units = spec.units_for(target)
    if units != "auto":
        return units
    return "pu" if float(np.max(values, initial=0.0)) <= _PU_AUTO_THRESHOLD else "mw"


def resolve_profiles(
    net: Any, wide: pd.DataFrame, spec: ProfileSpec
) -> ResolvedProfiles:
    """Map every profile column onto pandapower loads / generators (MW)."""
    n_hours = len(wide)
    out = ResolvedProfiles(n_hours=n_hours)
    for column in wide.columns:
        target = _target_for_column(net, column, spec)
        indices = _resolve_target(net, target) if target is not None else []
        if target is None or not indices:
            out.unmatched_columns.append(column)
            continue
        values = wide[column].to_numpy(dtype=np.float64) * target.scale
        if (values < 0).any():
            raise ProfileError(
                f"profiles: column {column!r} has negative values "
                f"(min {values.min():.4g}); loads / availabilities must be ≥ 0"
            )
        units = _column_units(values, target, spec)
        bases = np.array(
            [element_base_mw(net, target.element, i) for i in indices], dtype=float
        )
        if units == "pu":
            per_elem = [values * b for b in bases]
        else:
            total = bases.sum()
            shares = bases / total if total > 0 else np.full(len(bases), 1 / len(bases))
            per_elem = [values * s for s in shares]
        energy = 0.0
        for idx, mw in zip(indices, per_elem):
            energy += float(mw.sum())
            if target.element == "load":
                prev = out.load_mw.get(idx)
                out.load_mw[idx] = mw if prev is None else prev + mw
            else:
                key = (target.element, idx)
                prev = out.gen_mw.get(key)
                out.gen_mw[key] = mw if prev is None else prev + mw
        out.column_energy_mwh[column] = energy
        logger.debug(
            "profiles: %s → %s %s=%s (%d element(s), %s)",
            column,
            target.element,
            target.by,
            target.ref,
            len(indices),
            units,
        )
    if out.unmatched_columns:
        logger.warning(
            "profiles: %d column(s) matched no network element and were ignored: %s%s",
            len(out.unmatched_columns),
            ", ".join(out.unmatched_columns[:10]),
            " …" if len(out.unmatched_columns) > 10 else "",
        )
    if not out.load_mw and not out.gen_mw:
        raise ProfileError("profiles: no column matched any load / gen / sgen")
    return out
