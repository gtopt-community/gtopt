# SPDX-License-Identifier: BSD-3-Clause
"""Compact summary of a CEN PCP PLEXOS solution (``RES<date>.zip``).

Used by ``cen2gtopt-pcp-solution summary``.  Produces two small,
comparison-friendly artefacts from the PLEXOS solution database:

``plexos_summary.json``
    Region cost totals of the short-term (ST, ``t_phase_3``) phase over
    the whole horizon and over day 1 (``Total Generation Cost``,
    ``Generation Cost``, ``Generator Start & Shutdown Cost``), the
    block/interval layout and day-1 marginal-cost statistics (reference
    bus and all buses).  PLEXOS cost totals are NOT a gtopt objective:
    they exclude the end-of-horizon water value / future cost and the
    penalty terms, so compare trends, not levels.

``plexos_cmg_by_bus.csv``
    Day-1 hourly Node ``Price`` per bus: ``bus, hours, mean, min,
    median, max, hours_negative`` (negative = below -0.05 $/MWh).

Only solution values are summarised (no run times or solver logs).
The table reader is injected (``table(name) -> DataFrame``) so the
logic is testable without ``mdbtools``.
"""

from __future__ import annotations

import collections
import hashlib
import json
import lzma
import shutil
import statistics
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

SYSTEM_CLASS_ID = "1"
ST_PHASE = "3"
NEG_TOL = -0.05
COSTS = (
    "Total Generation Cost",
    "Generation Cost",
    "Generator Start & Shutdown Cost",
)
DEFAULT_REF_BUS = "Polpaico220"

TableReader = Callable[[str], pd.DataFrame]


def extract_accdb(res: Path, workdir: Path) -> Path:
    """Extract the solution ``.accdb`` of ``RES<date>.zip[.xz]`` into ``workdir``."""
    plain = res
    if res.suffix == ".xz":
        plain = workdir / res.with_suffix("").name
        with lzma.open(res, "rb") as fi, plain.open("wb") as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
    with zipfile.ZipFile(plain) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith((".accdb", ".mdb"))]
        if not names:
            raise FileNotFoundError(f"{res}: no .accdb/.mdb inside")
        out = workdir / Path(names[0]).name
        with zf.open(names[0]) as fi, out.open("wb") as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
    return out


def _rows(table: TableReader, name: str) -> list[dict[str, str]]:
    """Table rows with every value as a string (ids compare as text)."""
    df = table(name)
    return [{k: str(v) for k, v in r.items()} for r in df.to_dict("records")]


def _norm_id(v: str) -> str:
    """``'3.0'`` → ``'3'`` (pandas may read integer ids as floats)."""
    return v[:-2] if v.endswith(".0") else v


class _Db:
    """Thin cached view over the PLEXOS solution schema."""

    def __init__(self, table: TableReader):
        self._table = table
        self._cache: dict[str, list[dict[str, str]]] = {}

    def rows(self, name: str) -> list[dict[str, str]]:
        if name not in self._cache:
            self._cache[name] = [
                {k: _norm_id(v) if k.endswith("_id") else v for k, v in r.items()}
                for r in _rows(self._table, name)
            ]
        return self._cache[name]

    def props(self, collection: str, names: tuple[str, ...]) -> dict[str, str]:
        """``{property_id: name}`` for a System child collection."""
        cols = {
            c["collection_id"]
            for c in self.rows("t_collection")
            if c["name"] == collection and c["parent_class_id"] == SYSTEM_CLASS_ID
        }
        return {
            p["property_id"]: p["name"]
            for p in self.rows("t_property")
            if p["collection_id"] in cols and p["name"] in names
        }

    def unit(self, prop_id: str) -> str:
        units = {u["unit_id"]: u["value"] for u in self.rows("t_unit")}
        for p in self.rows("t_property"):
            if p["property_id"] == prop_id:
                return units.get(p["unit_id"], "?")
        return "?"

    def series(self, props: dict[str, str]) -> dict[str, dict[str, dict[str, float]]]:
        """``{prop: {object: {period_id: value}}}`` (ST phase, sample 0)."""
        objs = {o["object_id"]: o["name"] for o in self.rows("t_object")}
        mem = {
            m["membership_id"]: m["child_object_id"] for m in self.rows("t_membership")
        }
        keys = {
            k["key_id"]: (props[k["property_id"]], objs[mem[k["membership_id"]]])
            for k in self.rows("t_key")
            if k["property_id"] in props
            and k["phase_id"] == ST_PHASE
            and k["sample_id"] == "0"
        }
        out: dict[str, dict[str, dict[str, float]]] = collections.defaultdict(
            lambda: collections.defaultdict(dict)
        )
        for r in self.rows("t_data_0"):
            k = keys.get(r["key_id"])
            if k:
                out[k[0]][k[1]][r["period_id"]] = float(r["value"])
        return out

    def layout(self) -> tuple[dict[str, list[int]], int]:
        """``{period_id: [day-1 hours]}`` and the number of intervals."""
        i2p = {r["interval_id"]: r["period_id"] for r in self.rows("t_phase_3")}
        per = self.rows("t_period_0")
        stamps = pd.to_datetime([r["datetime"] for r in per], format="mixed")
        day0 = stamps[0].date()
        hours: dict[str, list[int]] = collections.defaultdict(list)
        for r, ts in zip(per, stamps, strict=True):
            if ts.date() == day0 and r["interval_id"] in i2p:
                hours[i2p[r["interval_id"]]].append(int(ts.hour))
        return hours, len(per)


def summarize(
    table: TableReader, ref_bus: str = DEFAULT_REF_BUS
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Return ``(summary dict, per-bus day-1 CMg table)``."""
    db = _Db(table)
    day1, n_intervals = db.layout()
    cost_props = db.props("Regions", COSTS)
    costs = db.series(cost_props)
    price_props = db.props("Nodes", ("Price",))
    if not price_props:
        raise ValueError("no Nodes.Price property in the solution database")
    price = db.series(price_props).get("Price", {})
    units = {name: db.unit(pid) for pid, name in cost_props.items()}

    summary: dict[str, Any] = {
        "phase": "ST (t_phase_3), sample 0",
        "horizon": {
            "intervals": n_intervals,
            "blocks": len({p for s in price.values() for p in s}),
        },
        "costs": {},
        "note": (
            "PLEXOS region cost totals; not comparable in level with a "
            "gtopt objective (no future-cost/water value, no penalties)."
        ),
    }
    for name, by_obj in sorted(costs.items()):
        tot = sum(v for s in by_obj.values() for v in s.values())
        d1 = sum(v for s in by_obj.values() for p, v in s.items() if p in day1)
        summary["costs"][name] = {
            "horizon": round(tot, 2),
            "day1": round(d1, 2),
            "unit": units.get(name, "?"),
        }

    # Day-1 hourly prices: expand each block to the hours it covers.
    hourly = {
        bus: [v for p, v in s.items() for _ in day1.get(p, [])]
        for bus, s in price.items()
    }
    recs = [
        {
            "bus": bus,
            "hours": len(v),
            "mean": round(statistics.fmean(v), 4),
            "min": round(min(v), 4),
            "median": round(statistics.median(v), 4),
            "max": round(max(v), 4),
            "hours_negative": sum(1 for x in v if x < NEG_TOL),
        }
        for bus, v in sorted(hourly.items())
        if v
    ]
    allv = [x for v in hourly.values() for x in v]
    ref = hourly.get(ref_bus, [])
    summary["cmg_day1"] = {
        "unit": db.unit(next(iter(price_props))),
        "buses": len(recs),
        "ref_bus": ref_bus,
        "ref_mean": round(statistics.fmean(ref), 4) if ref else None,
        "ref_min": round(min(ref), 4) if ref else None,
        "ref_max": round(max(ref), 4) if ref else None,
        "all_min": round(min(allv), 4) if allv else None,
        "all_max": round(max(allv), 4) if allv else None,
        "bus_hours_negative": sum(1 for x in allv if x < NEG_TOL),
    }
    return summary, pd.DataFrame.from_records(
        recs,
        columns=["bus", "hours", "mean", "min", "median", "max", "hours_negative"],
    )


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_summary(
    summary: dict[str, Any], cmg: pd.DataFrame, out_dir: Path, source: Path | None
) -> None:
    """Write ``plexos_summary.json`` and ``plexos_cmg_by_bus.csv`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if source is not None:
        summary = {
            "source": {"file": source.name, "sha256": sha256(source)},
            **summary,
        }
    (out_dir / "plexos_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    cmg.to_csv(out_dir / "plexos_cmg_by_bus.csv", index=False)
