# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Compare two gtopt runs stream by stream.

For every ``<Class>/<field>`` stream present in either run:

* elements are matched by name -- exactly, else by a normalised key that
  folds case, blanks, one converter prefix (``d_``, ``load_``, ``r_`` ...)
  and a trailing ``_<code>`` (see :func:`match_names`); elements found in one
  run only are listed;
* cells are aligned by scenario / stage / block **position** and compared
  with ``|a - b| <= abs_tol + rel_tol * max(|a|, |b|)`` (missing cells are
  zero, as gtopt drops zero ``_sol`` values);
* per element, the aggregate is the probability-weighted time integral
  (``sum`` streams: MW -> MWh, m3/s -> m3/s x h) or the time-weighted
  mean (``mean`` streams: duals / prices, volumes and other states).

Plus the expected total cost of each run (``solution.csv``), generation by
technology (the generators' ``type``) and the reservoir end-of-stage
volumes.  The report is plain data (:class:`Report`); :mod:`.report` renders
and writes it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
import dataclasses
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from gtopt_compare_runs.reader import Run, field_kind

logger = logging.getLogger(__name__)

#: Name prefixes converters give derived elements (stripped for matching).
PREFIXES = (
    "d_",
    "dem_",
    "load_",
    "r_",
    "rs_",
    "tb_",
    "turbine_",
    "j_",
    "in_",
    "inflow_",
    "s_",
    "sp_",
)
_CODE = re.compile(r"_[a-z]*\d+$")

#: Streams whose names are not element names (skipped by default).
SKIP_CLASSES = frozenset({"Commitment", "Converter", "UserConstraint"})


@dataclass(frozen=True)
class CompareOptions:
    """Tolerances and selection of a comparison."""

    abs_tol: float = 1e-3
    rel_tol: float = 1e-3
    streams: tuple[str, ...] = ()  # "Class/field" or "Class" filters
    top: int = 10


@dataclass
class StreamResult:
    """Comparison of one ``Class/field`` stream."""

    cls: str
    field: str
    kind: str
    n_a: int = 0
    n_b: int = 0
    only_a: list[str] = dataclasses.field(default_factory=list)
    only_b: list[str] = dataclasses.field(default_factory=list)
    total_a: float = 0.0
    total_b: float = 0.0
    max_abs: float = 0.0
    max_rel: float = 0.0
    rmse: float = 0.0
    n_cells: int = 0
    n_fail: int = 0
    elements: pd.DataFrame = dataclasses.field(default_factory=pd.DataFrame)

    @property
    def key(self) -> str:
        """``Class/field``."""
        return f"{self.cls}/{self.field}"

    @property
    def passed(self) -> bool:
        """No cell beyond tolerance (elements in one run only do not fail)."""
        return self.n_fail == 0

    def summary(self) -> dict[str, Any]:
        """JSON-ready summary (no per-element table)."""
        return {
            "stream": self.key,
            "kind": self.kind,
            "n_a": self.n_a,
            "n_b": self.n_b,
            "only_a": self.only_a,
            "only_b": self.only_b,
            "total_a": self.total_a,
            "total_b": self.total_b,
            "diff": self.total_b - self.total_a,
            "max_abs": self.max_abs,
            "max_rel": self.max_rel,
            "rmse": self.rmse,
            "n_cells": self.n_cells,
            "n_fail": self.n_fail,
            "passed": self.passed,
        }


@dataclass
class Report:
    """Everything :func:`compare_runs` found."""

    label_a: str
    label_b: str
    options: CompareOptions
    objective_a: float | None = None
    objective_b: float | None = None
    streams: list[StreamResult] = dataclasses.field(default_factory=list)
    technology: pd.DataFrame = dataclasses.field(default_factory=pd.DataFrame)
    reservoirs: pd.DataFrame = dataclasses.field(default_factory=pd.DataFrame)

    @property
    def passed(self) -> bool:
        """Every compared stream within tolerance."""
        return all(s.passed for s in self.streams)

    def stream(self, key: str) -> StreamResult | None:
        """The result of ``Class/field``."""
        return next((s for s in self.streams if s.key == key), None)

    def summary(self) -> dict[str, Any]:
        """JSON-ready report."""
        return {
            "run_a": self.label_a,
            "run_b": self.label_b,
            "abs_tol": self.options.abs_tol,
            "rel_tol": self.options.rel_tol,
            "objective_a": self.objective_a,
            "objective_b": self.objective_b,
            "passed": self.passed,
            "streams": [s.summary() for s in self.streams],
            "technology": self.technology.to_dict("records"),
            "reservoirs": self.reservoirs.to_dict("records"),
        }


def name_key(name: str) -> str:
    """Normalised matching key: case, blanks, one prefix, a ``_<code>`` tail."""
    key = re.sub(r"\s+", "_", str(name).strip()).casefold()
    for p in PREFIXES:
        if key.startswith(p) and len(key) > len(p):
            key = key[len(p) :]
            break
    return _CODE.sub("", key)


def match_names(names_a: Iterable[str], names_b: Iterable[str]) -> dict[str, str]:
    """``{name in A: name in B}``: exact first, then a unique normalised key."""
    a = list(dict.fromkeys(names_a))
    b = list(dict.fromkeys(names_b))
    out = {n: n for n in a if n in set(b)}
    rest_b = [n for n in b if n not in out.values()]
    by_key: dict[str, list[str]] = {}
    for n in rest_b:
        by_key.setdefault(name_key(n), []).append(n)
    a_keys: dict[str, list[str]] = {}
    for n in a:
        if n not in out:
            a_keys.setdefault(name_key(n), []).append(n)
    for key, an in a_keys.items():
        bn = by_key.get(key, [])
        if len(an) == 1 and len(bn) == 1:
            out[an[0]] = bn[0]
    return out


def _selected(key: tuple[str, str], filters: Sequence[str]) -> bool:
    if not filters:
        return key[0] not in SKIP_CLASSES
    return any(f in (key[0], f"{key[0]}/{key[1]}") for f in filters)


def element_aggregates(
    run: Run, cls: str, fld: str, kind: str | None = None
) -> dict[str, float]:
    """Per-element aggregate of one stream: ``{name: value}``.

    ``kind="sum"``: Σ value x hours x probability (MW -> MWh);
    ``kind="mean"``: that sum over the horizon hours (time-weighted mean);
    default :func:`~gtopt_compare_runs.reader.field_kind`.
    """
    kind = kind or field_kind(fld)
    cells = run.cells(cls, fld)
    per_stage = bool((cells["b"] < 0).all()) if not cells.empty else False
    agg = _aggregate(cells, kind, run.total_weight(per_stage))
    return {str(k): float(v) for k, v in agg.items()}


def _aggregate(df: pd.DataFrame, kind: str, total_weight: float) -> pd.Series:
    """Per-name aggregate (time integral or time-weighted mean)."""
    if df.empty:
        return pd.Series(dtype=float)
    agg = (df["value"] * df["weight"]).groupby(df["name"]).sum()
    if kind == "mean" and total_weight > 0:
        agg = agg / total_weight
    return agg


def compare_stream(
    a: Run, b: Run, cls: str, fld: str, opts: CompareOptions
) -> StreamResult:
    """Compare one stream of two runs (see module doc)."""
    kind = field_kind(fld)
    res = StreamResult(cls, fld, kind)
    ca, cb = a.cells(cls, fld), b.cells(cls, fld)
    names_a = sorted(set(ca["name"]))
    names_b = sorted(set(cb["name"]))
    match = match_names(names_a, names_b)
    res.n_a, res.n_b = len(names_a), len(names_b)
    res.only_a = [n for n in names_a if n not in match]
    res.only_b = sorted(set(names_b) - set(match.values()))
    per_stage = bool((ca["b"] < 0).all()) if not ca.empty else bool((cb["b"] < 0).all())
    agg_a = _aggregate(ca, kind, a.total_weight(per_stage))
    agg_b = _aggregate(cb, kind, b.total_weight(per_stage))
    res.total_a, res.total_b = float(agg_a.sum()), float(agg_b.sum())
    inv = {v: k for k, v in match.items()}
    cb = cb[cb["name"].isin(inv)].assign(name=lambda d: d["name"].map(inv))
    ca = ca[ca["name"].isin(match)]
    ca = ca.astype({"name": str, "s": int, "k": int, "b": int})
    cb = cb.astype({"name": str, "s": int, "k": int, "b": int})
    keys = ["name", "s", "k", "b"]
    joined = pd.merge(
        ca[[*keys, "value"]],
        cb[[*keys, "value"]],
        on=keys,
        how="outer",
        suffixes=("_a", "_b"),
    ).fillna({"value_a": 0.0, "value_b": 0.0})
    if not joined.empty:
        va = joined["value_a"].to_numpy(dtype=float)
        vb = joined["value_b"].to_numpy(dtype=float)
        diff = np.abs(va - vb)
        scale = np.maximum(np.abs(va), np.abs(vb))
        rel = np.where(scale > 0, diff / np.where(scale > 0, scale, 1.0), 0.0)
        fail = diff > opts.abs_tol + opts.rel_tol * scale
        res.n_cells = int(len(joined))
        res.n_fail = int(fail.sum())
        res.max_abs = float(diff.max())
        res.max_rel = float(rel.max())
        res.rmse = float(np.sqrt(np.mean(diff**2)))
        joined["abs"] = diff
        joined["fail"] = fail
        cell = joined.groupby("name").agg(
            max_abs_cell=("abs", "max"), n_fail=("fail", "sum")
        )
    else:
        cell = pd.DataFrame(columns=["max_abs_cell", "n_fail"])
    rows = []
    for na, nb in sorted(match.items()):
        x, y = float(agg_a.get(na, 0.0)), float(agg_b.get(nb, 0.0))
        scale_e = max(abs(x), abs(y))
        rows.append(
            {
                "stream": res.key,
                "name_a": na,
                "name_b": nb,
                "agg_a": x,
                "agg_b": y,
                "diff": y - x,
                "abs_diff": abs(y - x),
                "rel_diff": abs(y - x) / scale_e if scale_e > 0 else 0.0,
                "max_abs_cell": float(cell["max_abs_cell"].get(na, 0.0))
                if na in cell.index
                else 0.0,
                "n_fail": int(cell["n_fail"].get(na, 0)) if na in cell.index else 0,
            }
        )
    res.elements = pd.DataFrame(rows)
    if not res.elements.empty:
        res.elements = res.elements.sort_values(
            "abs_diff", ascending=False, kind="stable"
        ).reset_index(drop=True)
    return res


def _technology(a: Run, b: Run) -> pd.DataFrame:
    """Generation [MWh, probability-weighted] by technology of each run."""
    out: dict[str, dict[str, float]] = {}
    for side, run in (("a", a), ("b", b)):
        cells = run.cells("Generator", "generation_sol")
        if cells.empty:
            continue
        types = run.types()
        energy = (cells["value"] * cells["weight"]).groupby(cells["name"]).sum()
        for name, mwh in energy.items():
            tech = types.get(str(name), "other")
            out.setdefault(tech, {"a": 0.0, "b": 0.0})[side] += float(mwh)
    rows = [
        {"technology": t, "mwh_a": v["a"], "mwh_b": v["b"], "diff": v["b"] - v["a"]}
        for t, v in sorted(out.items())
    ]
    return pd.DataFrame(rows, columns=["technology", "mwh_a", "mwh_b", "diff"])


def _reservoirs(a: Run, b: Run) -> pd.DataFrame:
    """Expected end-of-stage volume per reservoir and stage of each run."""
    cols = ["reservoir", "stage", "efin_a", "efin_b", "diff"]
    ca, cb = a.cells("Reservoir", "efin_sol"), b.cells("Reservoir", "efin_sol")
    if ca.empty and cb.empty:
        return pd.DataFrame(columns=cols)

    def expected(run: Run, c: pd.DataFrame) -> pd.Series:
        if c.empty:
            return pd.Series(dtype=float)
        w = run.weights[c["s"].to_numpy()]
        return (c["value"] * w).groupby([c["name"], c["k"]]).sum()

    ea, eb = expected(a, ca), expected(b, cb)
    match = match_names(sorted(set(ca["name"])), sorted(set(cb["name"])))
    rows = []
    for (name, k), va in ea.items():
        nb = match.get(str(name))
        vb = float(eb.get((nb, k), np.nan)) if nb is not None else np.nan
        rows.append(
            {
                "reservoir": name,
                "stage": int(k) + 1,
                "efin_a": float(va),
                "efin_b": vb,
                "diff": vb - float(va),
            }
        )
    return pd.DataFrame(rows, columns=cols)


def compare_runs(a: Run, b: Run, opts: CompareOptions | None = None) -> Report:
    """Compare two runs (see module doc)."""
    opts = opts or CompareOptions()
    report = Report(a.label, b.label, opts, a.objective(), b.objective())
    keys = sorted(set(a.streams()) | set(b.streams()))
    for cls, fld in keys:
        if not _selected((cls, fld), opts.streams):
            continue
        report.streams.append(compare_stream(a, b, cls, fld, opts))
    report.technology = _technology(a, b)
    report.reservoirs = _reservoirs(a, b)
    return report
