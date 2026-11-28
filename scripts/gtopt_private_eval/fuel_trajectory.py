# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Fuel-cost trajectories: declared base cost × an IEA WEO price index.

Same method as :mod:`capex_trajectory`:

``fuel_cost(unit, year) = fuel_base(unit) × I(fuel, scenario, year) /
I(fuel, scenario, base_year)``

* **base** — the variable cost **declared** to the CEN (Chile: *costo
  variable combustible* + *no combustible* of each unit, the cost the
  CEN dispatches with and that sets the CMg).  In the demo cases the
  case's ``gcost`` stands in for it.  The index applies to the **fuel**
  component only (``projects.<name>.declared_cost = {"fuel": .., "nonfuel":
  ..}`` $/MWh); when the declared cost is not split, the index applies to
  the whole cost and the report flags the approximation.
* **I** — the IEA World Energy Outlook fossil-fuel price projections by
  scenario (:data:`IEA_WEO_INDEX`, a handful of derived points, linear in
  between, flat outside).  STEPS is the default.  Other indices (CNE
  PELP, EIA AEO, World Bank) are passed the same way, as a user table
  ``{fuel: {scenario: {year: index}}}``.

``cl`` never rebuilds the cost as heat rate × an estimated fuel price:
a ``heat_rate`` / ``fuel_price`` pair is accepted only in the other
markets.

The CMg of a solved case was computed with base-year costs.  With
``price_scaling: "thermal_marginal"`` (default) the price of every
period whose marginal unit is a fuel-burning unit is scaled by that
unit's cost factor of the year; periods set by other units (renewables,
storage, scarcity) keep their price.  This is an approximation: the
dispatch and the merit order stay those of the base year.  The
consistent alternative is to rerun gtopt with the same trajectory
(``gtopt_private_eval fuel-trajectory --planning``).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capex_trajectory import IndexTable, index_at

#: technology → fuel key of the index table
FUEL_OF_TECH: dict[str, str] = {
    "coal": "coal",
    "ccgt": "gas",
    "ocgt": "gas",
    "gas": "gas",
    "lng": "gas",
    "diesel": "oil",
    "oil": "oil",
    "fuel_oil": "oil",
}

#: IEA WEO edition of the default index
IEA_WEO: dict[str, Any] = {
    "edition": "IEA WEO 2026",
    "base_year": 2025,
    "default_scenario": "steps",
    "url": "https://www.iea.org/reports/world-energy-outlook-2026",
    "table": 'Table 2.2 "Wholesale fossil fuel prices by scenario", ch. 2, p. 117',
    "units": "USD (2025, MER); CC BY 4.0",
    "note": (
        "gas = Japan LNG import price, coal = Japan steam coal (6000 kcal/kg),"
        " oil = IEA crude import price (index of diesel / fuel oil)"
    ),
}

#: IEA WEO 2026 Table 2.2 prices (gas USD/MBtu, coal USD/t, oil USD/bbl):
#: 2025 and the 2035 / 2050 points of each scenario.  ARC (Additional
#: Response Case) replaces the APS of earlier editions.
_WEO_2026: dict[str, dict[str, tuple[float, float, float]]] = {
    "gas": {
        "cps": (12.6, 10.9, 12.4),
        "steps": (12.6, 6.8, 9.9),
        "arc": (12.6, 7.1, 5.8),
        "nze": (12.6, 4.9, 4.9),
    },
    "coal": {
        "cps": (117.0, 124.0, 105.0),
        "steps": (117.0, 109.0, 94.0),
        "arc": (117.0, 104.0, 87.0),
        "nze": (117.0, 63.0, 51.0),
    },
    "oil": {
        "cps": (68.0, 85.0, 106.0),
        "steps": (68.0, 75.0, 70.0),
        "arc": (68.0, 72.0, 62.0),
        "nze": (68.0, 34.0, 26.0),
    },
}

#: fuel → scenario → year → price / price of 2025 (linear in between,
#: flat after 2050)
IEA_WEO_INDEX: IndexTable = {
    fuel: {
        sc: {year: price / prices[0] for year, price in zip((2025, 2035, 2050), prices)}
        for sc, prices in scen.items()
    }
    for fuel, scen in _WEO_2026.items()
}


def fuel_of(params: Any, name: str, tech: str) -> str | None:
    """Fuel key of a unit: project / technology ``fuel``, else by technology."""
    explicit = params.value(name, tech, "fuel")
    if explicit:
        return str(explicit)
    return FUEL_OF_TECH.get(tech)


def fuel_ratio(
    fuel: str,
    scenario: str,
    year: float,
    base_year: float,
    table: IndexTable | None = None,
) -> float:
    """``I(year) / I(base_year)`` of one fuel and scenario."""
    tab = table if table is not None else IEA_WEO_INDEX
    return index_at(tab, fuel, scenario, year) / index_at(
        tab, fuel, scenario, base_year
    )


@dataclass
class DeclaredCost:
    """Base variable cost of a unit [$/MWh] and its fuel component."""

    total: float
    fuel: float | None  #: None: not split (the index applies to the total)
    source: str

    def factor(self, ratio: float) -> float:
        """Cost multiplier for a fuel-price ratio."""
        if self.total <= 0.0:
            return 1.0
        if self.fuel is None:
            return ratio
        return (self.total - self.fuel + self.fuel * ratio) / self.total


def declared_cost(
    params: Any, market: str, name: str, tech: str, case_cost: float
) -> DeclaredCost:
    """Base variable cost of a fuel-burning unit.

    Order: ``projects.<name>.declared_cost`` (a number, or ``{"fuel",
    "nonfuel"}``); outside ``cl`` also ``heat_rate × fuel_price (+ vom)``
    and a ``fuel_share`` of the case cost; else the case cost (``gcost``
    / ``srmc``).  ``cl`` raises when a unit has neither a declared cost
    nor a case cost, and when an estimate (``heat_rate`` / ``fuel_price``)
    is given instead of the declared cost.
    """
    proj = params.project(name)
    decl = proj.get("declared_cost")
    if isinstance(decl, Mapping):
        fuel = float(decl.get("fuel", 0.0) or 0.0)
        nonfuel = float(decl.get("nonfuel", 0.0) or 0.0)
        return DeclaredCost(fuel + nonfuel, fuel, "declared (fuel + non-fuel)")
    if isinstance(decl, (int, float)) and not isinstance(decl, bool):
        return DeclaredCost(float(decl), None, "declared (not split)")
    estimate = proj.get("heat_rate") is not None or proj.get("fuel_price") is not None
    if market == "cl":
        if estimate:
            raise ValueError(
                f"{name}: market cl uses the variable cost declared to the CEN;"
                " heat_rate x fuel_price is an estimate -- give"
                " projects.<name>.declared_cost instead"
            )
        if case_cost > 0.0:
            return DeclaredCost(
                case_cost, None, "case gcost (stand-in for the declared cost)"
            )
        raise ValueError(
            f"{name}: market cl needs the variable cost declared to the CEN"
            " (projects.<name>.declared_cost, or the case gcost); none found"
        )
    if estimate:
        hr = float(proj.get("heat_rate", 0.0) or 0.0)
        price = float(proj.get("fuel_price", 0.0) or 0.0)
        vom = float(proj.get("nonfuel_cost", 0.0) or 0.0)
        return DeclaredCost(hr * price + vom, hr * price, "heat_rate x fuel_price")
    share = params.value(name, tech, "fuel_share")
    if share is not None and case_cost > 0.0:
        return DeclaredCost(case_cost, case_cost * float(share), "case cost x share")
    return DeclaredCost(case_cost, None, "case cost (not split)")


def trajectory_config(finance: Mapping[str, Any]) -> dict[str, Any] | None:
    """The ``finance.fuel_trajectory`` block when active, else ``None``."""
    cfg = dict(finance.get("fuel_trajectory") or {})
    if not cfg or cfg.get("enabled") is False:
        return None
    mode = cfg.get("price_scaling", "thermal_marginal")
    if mode not in ("thermal_marginal", "none"):
        raise ValueError(
            f"fuel_trajectory.price_scaling {mode!r}: expected thermal_marginal or none"
        )
    return cfg


def index_table(cfg: Mapping[str, Any]) -> IndexTable:
    """The index of a trajectory config (default: IEA WEO)."""
    user = cfg.get("index")
    if isinstance(user, Mapping):
        return {
            str(f): {
                str(s): {int(y): float(v) for y, v in pts.items()}
                for s, pts in sc.items()
            }
            for f, sc in user.items()
        }
    return IEA_WEO_INDEX


def scenarios(table: IndexTable) -> list[str]:
    """All scenarios of a table, in first-seen order."""
    out: list[str] = []
    for sc in table.values():
        for s in sc:
            if s not in out:
                out.append(s)
    return out


# ── gtopt stage schedules ──────────────────────────────────────────


def apply_to_planning(
    planning: dict[str, Any],
    fuels: Mapping[str, str],
    stage_years: Sequence[int],
    scenario: str,
    base_year: int,
    table: IndexTable | None = None,
    declared: Mapping[str, tuple[float, float]] | None = None,
) -> list[str]:
    """Write the trajectory into a planning as per-stage schedules.

    ``fuels``: element name → fuel key.  A ``Fuel`` element of
    ``fuel_array`` gets ``price`` per stage (the fuel component of every
    generator that references it scales exactly; ``gcost`` stays the
    non-fuel part).  A generator gets ``gcost`` per stage: ``nonfuel +
    fuel × ratio`` with ``declared[name] = (fuel, nonfuel)``, else the
    whole ``gcost`` × ratio.  Returns the names changed.
    """
    system = planning.setdefault("system", {})
    changed: list[str] = []
    declared = declared or {}

    def ratios(fuel: str) -> list[float]:
        return [fuel_ratio(fuel, scenario, y, base_year, table) for y in stage_years]

    for elem in system.get("fuel_array", []) or []:
        name = str(elem.get("name"))
        price = elem.get("price")
        if name not in fuels or not isinstance(price, (int, float)):
            continue
        elem["price"] = [round(float(price) * r, 6) for r in ratios(fuels[name])]
        changed.append(name)
    for gen in system.get("generator_array", []) or []:
        name = str(gen.get("name"))
        if name not in fuels:
            continue
        if name in declared:
            fuel, nonfuel = declared[name]
        else:
            cost = gen.get("gcost")
            if not isinstance(cost, (int, float)):
                continue
            fuel, nonfuel = float(cost), 0.0
        gen["gcost"] = [round(nonfuel + fuel * r, 6) for r in ratios(fuels[name])]
        changed.append(name)
    return changed


# ── CLI: gtopt_private_eval fuel-trajectory ─────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gtopt_private_eval fuel-trajectory",
        description="Declared fuel cost x IEA WEO price index (STEPS default).",
    )
    p.add_argument("--fuel", action="append", default=[], help="gas / coal / oil")
    p.add_argument(
        "--scenario", action="append", default=[], help="index scenario (repeatable)"
    )
    p.add_argument("--base-year", type=int, help="year of the declared cost")
    p.add_argument("--years", type=int, nargs="+", default=[2026, 2030, 2035, 2040])
    p.add_argument("--index-json", type=Path, help="user index table")
    p.add_argument("--planning", type=Path, help="planning JSON to rewrite")
    p.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="NAME=FUEL",
        help="Fuel element or generator name -> fuel key (with --planning)",
    )
    p.add_argument(
        "--declared",
        action="append",
        default=[],
        metavar="NAME=FUEL,NONFUEL",
        help="declared fuel and non-fuel cost [$/MWh] of a generator",
    )
    p.add_argument("--stage-years", type=int, nargs="+", help="year of each stage")
    p.add_argument("-o", "--output", type=Path, help="output planning JSON")
    p.add_argument("--json", action="store_true", help="print JSON")
    return p


def _load_table(path: Path | None) -> IndexTable:
    if path is None:
        return IEA_WEO_INDEX
    return index_table({"index": json.loads(path.read_text(encoding="utf-8"))})


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    table = _load_table(args.index_json)
    base_year = args.base_year or int(IEA_WEO.get("base_year", 2024))
    scens = args.scenario or [str(IEA_WEO.get("default_scenario", "steps"))]
    try:
        if args.planning is not None:
            if not args.stage_years:
                print("fuel-trajectory: --stage-years is required", file=sys.stderr)
                return 2
            planning = json.loads(args.planning.read_text(encoding="utf-8"))
            fuels = dict(m.split("=", 1) for m in args.map)
            declared: dict[str, tuple[float, float]] = {}
            for item in args.declared:
                name, _, vals = item.partition("=")
                fuel, nonfuel = (float(v) for v in vals.split(","))
                declared[name] = (fuel, nonfuel)
            changed = apply_to_planning(
                planning,
                fuels,
                args.stage_years,
                scens[0],
                base_year,
                table,
                declared,
            )
            out = args.output or args.planning.with_name(
                args.planning.stem + "_fuel.json"
            )
            out.write_text(json.dumps(planning, indent=2) + "\n", encoding="utf-8")
            print(f"wrote {out}: fuel cost per stage for {', '.join(changed)}")
            return 0
        rows = [
            {
                "fuel": f,
                "scenario": s,
                "year": y,
                "ratio": fuel_ratio(f, s, y, base_year, table),
            }
            for f in (args.fuel or sorted(table))
            for s in scens
            for y in args.years
        ]
    except (KeyError, ValueError) as exc:
        print(f"fuel-trajectory: error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    src = IEA_WEO.get("url", "user index") if args.index_json is None else "user"
    print(f"index base year {base_year}: {src}")
    print(f"{'fuel':<6}{'scenario':<10}{'year':>6}{'ratio':>8}")
    for r in rows:
        print(f"{r['fuel']:<6}{r['scenario']:<10}{r['year']:>6}{r['ratio']:>8.3f}")
    return 0
