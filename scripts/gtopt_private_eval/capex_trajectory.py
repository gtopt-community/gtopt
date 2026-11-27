# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Capex trajectories: local base level × an international cost index.

``capex(tech, year) = base(tech) × I(tech, scenario, year) / I(tech,
scenario, base_year)``

* **base** — local overnight cost of the base year, e.g. Chile: CNE
  *Informe de Costos de Tecnologías de Generación y Almacenamiento*
  (ICTGA, May 2026; :data:`LOCAL_BASES`).
* **I** — a dimensionless index from a projection: NREL ATB 2024
  (v4.0.0, OCC, Market case, 2022 USD) Conservative / Moderate /
  Advanced, normalised to 2025 (:data:`ATB_2024_INDEX`, a handful of
  derived points, linear in between).  The full ATB CSV can be read at
  run time (:func:`indices_from_atb_csv`, local path or URL, not stored
  in the repo).  IEA WEO (STEPS / APS / NZE) indices are passed the same
  way, as a user table ``{tech: {scenario: {year: index}}}``.

The index carries only the *shape* (learning, declines); the level
stays local (grid connection, logistics, labour and the Chilean premium
or discount are in the base).  Real terms: the index is in constant
dollars; escalate separately if the model is nominal.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

IndexTable = dict[str, dict[str, dict[int, float]]]

ATB_URL = (
    "https://oedi-data-lake.s3.amazonaws.com/ATB/electricity/csv/2024/v4.0.0/ATBe.csv"
)

_YEARS = (2025, 2027, 2030, 2035, 2040, 2045, 2050)

#: NREL ATB 2024 v4.0.0 OCC (Market case) / OCC 2025, derived from
#: ATBe.csv (UtilityPV Class5, LandbasedWind Class4, Li-ion 4 h,
#: NG CT F-frame, NG 2-on-1 CC F-frame, Coal-new).
_ATB_RAW: dict[str, dict[str, tuple[float, ...]]] = {
    "solar_pv": {
        "conservative": (1.0, 0.9466, 0.8664, 0.7328, 0.6634, 0.5939, 0.5245),
        "moderate": (1.0, 0.9155, 0.7889, 0.5777, 0.5261, 0.4744, 0.4227),
        "advanced": (1.0, 0.8939, 0.7348, 0.4697, 0.4398, 0.4100, 0.3801),
    },
    "wind": {
        "conservative": (1.0, 0.9855, 0.9638, 0.9276, 0.8914, 0.8553, 0.8191),
        "moderate": (1.0, 0.9559, 0.8899, 0.8398, 0.7897, 0.7396, 0.6896),
        "advanced": (1.0, 0.9437, 0.8592, 0.8015, 0.7439, 0.6863, 0.6287),
    },
    "bess": {
        "conservative": (1.0, 0.9251, 0.8128, 0.7849, 0.7571, 0.7293, 0.7015),
        "moderate": (1.0, 0.9353, 0.8382, 0.7736, 0.7090, 0.6444, 0.5798),
        "advanced": (1.0, 0.9151, 0.7878, 0.7187, 0.6496, 0.5806, 0.5115),
    },
    "ocgt": {
        s: (1.0, 0.9838, 0.9595, 0.9189, 0.8784, 0.8379, 0.7973)
        for s in ("conservative", "moderate", "advanced")
    },
    "ccgt": {
        "conservative": (1.0, 0.9864, 0.9661, 0.9322, 0.8982, 0.8643, 0.8305),
        "moderate": (1.0, 0.9823, 0.9557, 0.9113, 0.8737, 0.8362, 0.7987),
        "advanced": (1.0, 0.9779, 0.9450, 0.8900, 0.8489, 0.8077, 0.7665),
    },
    "coal": {
        "conservative": (1.0, 0.9835, 0.9587, 0.9174, 0.8761, 0.8348, 0.7936),
        "moderate": (1.0, 0.9889, 0.9722, 0.9444, 0.8991, 0.8537, 0.8084),
        "advanced": (1.0, 0.9942, 0.9855, 0.9710, 0.9217, 0.8723, 0.8230),
    },
}

ATB_2024_INDEX: IndexTable = {
    tech: {sc: dict(zip(_YEARS, vals)) for sc, vals in scen.items()}
    for tech, scen in _ATB_RAW.items()
}
ATB_2024_INDEX["diesel"] = ATB_2024_INDEX["ocgt"]

#: local base levels [$/kW] (BESS: $/kW of a 4-h system)
LOCAL_BASES: dict[str, dict[str, Any]] = {
    "cl_cne_2026": {
        "year": 2026,
        "source": "https://www.cne.cl/wp-content/uploads/2026/06/ICTGA-2026.pdf",
        "values": {
            "solar_pv": 732.0,
            "wind": 1433.0,
            "bess": 722.0,
            "ccgt": 1144.0,
            "ocgt": 860.0,
            "diesel": 576.0,
        },
    },
    "atb_2024": {
        "year": 2025,
        "source": "https://atb.nlr.gov/electricity/2024/data",
        "values": {
            "solar_pv": 1323.1,
            "wind": 1380.3,
            "bess": 1551.1,
            "ccgt": 1213.1,
            "ocgt": 1084.0,
            "coal": 3148.7,
        },
    },
}


def index_at(table: IndexTable, tech: str, scenario: str, year: float) -> float:
    """Index at ``year``, linear between points, flat outside the range."""
    try:
        pts = sorted(table[tech][scenario].items())
    except KeyError as exc:
        raise KeyError(f"no index for {tech}/{scenario}") from exc
    if year <= pts[0][0]:
        return pts[0][1]
    for (y0, v0), (y1, v1) in zip(pts, pts[1:]):
        if year <= y1:
            return v0 + (v1 - v0) * (year - y0) / (y1 - y0)
    return pts[-1][1]


def capex_factor(
    tech: str,
    scenario: str,
    year: float,
    base_year: float,
    table: IndexTable | None = None,
) -> float:
    """``I(year) / I(base_year)``; 1 for a technology without an index."""
    tab = ATB_2024_INDEX if table is None else table
    if tech not in tab:
        return 1.0
    return index_at(tab, tech, scenario, year) / index_at(
        tab, tech, scenario, base_year
    )


def trajectory(
    tech: str,
    scenario: str,
    years: Sequence[int],
    base_value: float,
    base_year: int,
    table: IndexTable | None = None,
) -> list[dict[str, float]]:
    """Rows ``{year, index, capex}`` of one technology."""
    return [
        {
            "year": y,
            "index": capex_factor(tech, scenario, y, base_year, table),
            "capex": base_value * capex_factor(tech, scenario, y, base_year, table),
        }
        for y in years
    ]


def indices_from_atb_csv(
    source: str | Path,
    selection: Mapping[str, tuple[str, str]],
    base_year: int = 2025,
) -> IndexTable:
    """Compute the index table from an ATB ``ATBe.csv`` (path or URL).

    ``selection``: ``{tech: (technology, techdetail)}``; OCC, Market case.
    """
    import pandas as pd  # noqa: PLC0415

    df = pd.read_csv(source, low_memory=False)
    df = df[
        (df["core_metric_parameter"] == "OCC") & (df["core_metric_case"] == "Market")
    ]
    out: IndexTable = {}
    for tech, (techno, detail) in selection.items():
        sub = df[(df["technology"] == techno) & (df["techdetail"] == detail)]
        out[tech] = {}
        for scen, grp in sub.groupby("scenario"):
            years = pd.to_numeric(grp["core_metric_variable"], errors="coerce")
            vals = pd.to_numeric(grp["value"], errors="coerce")
            series = vals.groupby(years).mean().dropna()
            if base_year not in series.index:
                continue
            base = float(series.loc[base_year])
            out[tech][str(scen).lower()] = {
                int(y): float(v) / base for y, v in series.items()
            }
    return out


def apply_to_planning(
    planning: dict[str, Any],
    techs: Mapping[str, str],
    stage_years: Sequence[int],
    scenario: str,
    base: Mapping[str, Any],
    table: IndexTable | None = None,
) -> list[str]:
    """Write per-stage ``overnight_capcost`` [$/MW] into the candidates.

    ``techs``: element name → technology.  Returns the names changed.
    """
    system = planning.setdefault("system", {})
    changed: list[str] = []
    values = base["values"]
    for arr in ("generator_array", "battery_array", "line_array"):
        for elem in system.get(arr, []) or []:
            name = str(elem.get("name"))
            tech = techs.get(name)
            if tech is None or tech not in values:
                continue
            per_mw = float(values[tech]) * 1000.0
            elem["overnight_capcost"] = [
                round(per_mw * capex_factor(tech, scenario, y, base["year"], table), 2)
                for y in stage_years
            ]
            changed.append(name)
    return changed


# ── CLI: gtopt_private_eval capex-trajectory ─────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gtopt_private_eval capex-trajectory",
        description="Local capex x international index trajectory "
        "(NREL ATB 2024 scenarios by default).",
    )
    p.add_argument("--tech", action="append", default=[], help="technology key")
    p.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="conservative / moderate / advanced (repeatable)",
    )
    p.add_argument("--base", default="cl_cne_2026", choices=sorted(LOCAL_BASES))
    p.add_argument("--base-value", type=float, help="override base $/kW")
    p.add_argument("--years", type=int, nargs="+", default=[2026, 2030, 2035, 2040])
    p.add_argument("--index-json", type=Path, help="user index table (IEA, ...)")
    p.add_argument(
        "--atb-csv",
        help=f"compute the ATB index from ATBe.csv (path or URL, e.g. {ATB_URL})",
    )
    p.add_argument("--planning", type=Path, help="planning JSON to rewrite")
    p.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="NAME=TECH",
        help="candidate name -> technology (with --planning)",
    )
    p.add_argument("--stage-years", type=int, nargs="+", help="year of each stage")
    p.add_argument("-o", "--output", type=Path, help="output planning JSON")
    p.add_argument("--json", action="store_true", help="print JSON")
    return p


_ATB_SELECTION = {
    "solar_pv": ("UtilityPV", "Class5"),
    "wind": ("LandbasedWind", "Class4"),
    "bess": ("Utility-Scale Battery Storage", "4Hr Battery Storage"),
    "ocgt": ("NaturalGas_FE", "NG Combustion Turbine (F-Frame)"),
    "ccgt": ("NaturalGas_FE", "NG 2-on-1 Combined Cycle (F-Frame)"),
    "coal": ("Coal_FE", "Coal-new"),
}


def _load_table(args: argparse.Namespace) -> IndexTable | None:
    if args.index_json is not None:
        raw = json.loads(args.index_json.read_text(encoding="utf-8"))
        return {
            t: {s: {int(y): float(v) for y, v in pts.items()} for s, pts in sc.items()}
            for t, sc in raw.items()
        }
    if args.atb_csv:
        return indices_from_atb_csv(args.atb_csv, _ATB_SELECTION)
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    table = _load_table(args)
    base = dict(LOCAL_BASES[args.base])
    scenarios = args.scenario or ["moderate"]
    if args.planning is not None:
        if not args.stage_years:
            print("capex-trajectory: --stage-years is required", file=sys.stderr)
            return 2
        planning = json.loads(args.planning.read_text(encoding="utf-8"))
        mapping = dict(m.split("=", 1) for m in args.map)
        changed = apply_to_planning(
            planning, mapping, args.stage_years, scenarios[0], base, table
        )
        out = args.output or args.planning.with_name(args.planning.stem + "_capex.json")
        out.write_text(json.dumps(planning, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}: overnight_capcost per stage for {', '.join(changed)}")
        return 0
    techs = args.tech or sorted(base["values"])
    rows: list[dict[str, Any]] = []
    for tech in techs:
        value = (
            args.base_value if args.base_value is not None else base["values"].get(tech)
        )
        if value is None:
            print(f"capex-trajectory: no base value for {tech}", file=sys.stderr)
            return 2
        for scen in scenarios:
            for r in trajectory(
                tech, scen, args.years, float(value), base["year"], table
            ):
                rows.append({"tech": tech, "scenario": scen, **r})
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(f"base {args.base} ({base['year']}): {base['source']}")
    print(f"{'tech':<10}{'scenario':<14}{'year':>6}{'index':>8}{'$/kW':>10}")
    for r in rows:
        print(
            f"{r['tech']:<10}{r['scenario']:<14}{r['year']:>6}"
            f"{r['index']:>8.3f}{r['capex']:>10.0f}"
        )
    return 0
