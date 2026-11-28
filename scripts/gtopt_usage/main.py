# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Command line of ``gtopt_usage``.

Examples::

    # usage per unit and year (+ maintenance cost with parameters)
    gtopt_usage usage case/output --per year --params maint.json --csv usage/

    # SSCC wear cost θ per technology, service and direction from two runs
    gtopt_usage theta no_sscc/output sscc/output --params maint.json \\
        --csv theta/ --json theta.json

    # incremental maintenance cost (counters + calendar), θ by two methods
    gtopt_usage maintenance no_sscc/output sscc/output --counters wear.json \\
        --calendar calendar.csv --start-date 2027-01-01 --csv maint/

Exit codes: 0 ok, 2 bad input (missing directory, bad parameter file).
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

from gtopt_compare_runs.reader import Run
from gtopt_usage.accounting import (
    HOURS_PER_YEAR,
    per_year,
    reserve_table,
    usage_table,
)
from gtopt_usage.maintenance import (
    ParamSet,
    by_technology,
    load_params,
    maintenance_table,
)
from gtopt_usage.incremental import (
    calendar_from_outages,
    default_types,
    incremental_maintenance,
    load_wear_params,
    read_calendar_csv,
    read_initial_csv,
    unit_periods,
)
from gtopt_usage.theta import derive_theta, horizon_reserve, horizon_usage


def build_parser() -> argparse.ArgumentParser:
    """Argument parser of ``gtopt_usage``."""
    p = argparse.ArgumentParser(
        prog="gtopt_usage",
        description=(
            "Usage accounting of gtopt runs (operating hours, starts, hours "
            "at pmin, ramp mileage, reserve MW·h per service, BESS cycles), "
            "maintenance cost from OEM intervals (EOH / HEOM, NTCyO Kf/Kd) "
            "and the SSCC wear cost θ per technology, service and direction from a run "
            "without and a run with SSCC.  Run gtopt with "
            "options.output_usage_metrics = true."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="exit: 0 ok, 2 bad input",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p.add_argument("--width", type=int, default=None, help="console width")
    p.add_argument(
        "--hours-per-year",
        type=float,
        default=HOURS_PER_YEAR,
        help="hours of an accounting year (default 8760)",
    )
    p.add_argument(
        "--pmin-band",
        type=float,
        default=None,
        metavar="FRAC",
        help=(
            "count hours with p <= Pmin*(1+FRAC) as 'near pmin' "
            "(default: gtopt's at-floor indicator)"
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)

    u = sub.add_parser("usage", help="usage (and maintenance) of one run")
    u.add_argument("run", type=Path, help="gtopt output directory")
    u.add_argument("--planning", type=Path, metavar="JSON", help="planning JSON")
    u.add_argument(
        "--per",
        choices=("stage", "year", "horizon"),
        default="year",
        help="aggregation level (default year)",
    )
    u.add_argument("--params", type=Path, metavar="JSON", help="maintenance params")
    u.add_argument("--csv", type=Path, metavar="DIR", help="write CSV tables")

    t = sub.add_parser("theta", help="θ per technology / service from two runs")
    t.add_argument("base", type=Path, help="output directory WITHOUT SSCC (A)")
    t.add_argument("sscc", type=Path, help="output directory WITH SSCC (B)")
    t.add_argument("--planning-a", type=Path, metavar="JSON", help="planning of A")
    t.add_argument("--planning-b", type=Path, metavar="JSON", help="planning of B")
    t.add_argument(
        "--params", type=Path, metavar="JSON", required=True, help="maintenance params"
    )
    t.add_argument("--csv", type=Path, metavar="DIR", help="write CSV tables")
    t.add_argument("--json", type=Path, metavar="PATH", help="write JSON summary")

    m = sub.add_parser(
        "maintenance",
        help="incremental maintenance cost (counters + calendar) and θ",
        description=(
            "Maintenance counters per unit and type (EOH / starts / cycles / "
            "years, hierarchical reset) against a fixed calendar; incremental "
            "cost B - A by the rate and the event method and θ per "
            "technology and service."
        ),
    )
    m.add_argument("base", type=Path, help="output directory WITHOUT SSCC (A)")
    m.add_argument("sscc", type=Path, help="output directory WITH SSCC (B)")
    m.add_argument("--planning-a", type=Path, metavar="JSON", help="planning of A")
    m.add_argument("--planning-b", type=Path, metavar="JSON", help="planning of B")
    m.add_argument(
        "--counters",
        type=Path,
        metavar="JSON",
        required=True,
        help="counter parameters (types, intervals, Kd/Kf, load weight, α)",
    )
    m.add_argument(
        "--calendar",
        type=Path,
        metavar="CSV",
        help="calendar unit,type,start,duration (default: outage_fraction of A)",
    )
    m.add_argument(
        "--start-date",
        metavar="DATE",
        help="ISO date of hour 0 (calendar starts given as dates)",
    )
    m.add_argument(
        "--outage-type",
        metavar="TYPE",
        help="type of outage_fraction events (default: the unit's highest)",
    )
    m.add_argument(
        "--no-outage-calendar",
        action="store_true",
        help="do not read Generator/outage_fraction_sol as calendar",
    )
    m.add_argument("--initial", type=Path, metavar="CSV", help="initial counters CSV")
    m.add_argument(
        "--discount-rate",
        type=float,
        default=None,
        help="discount rate per year (default: counters file, else 0.06)",
    )
    m.add_argument("--csv", type=Path, metavar="DIR", help="write CSV tables")
    m.add_argument("--json", type=Path, metavar="PATH", help="write JSON tables")
    return p


def _table(df: pd.DataFrame, title: str, top: int = 30) -> Table:
    tab = Table(title=title)
    for c in df.columns:
        tab.add_column(str(c), justify="left" if df[c].dtype == object else "right")
    for rec in df.head(top).itertuples(index=False):
        tab.add_row(*(f"{v:,.4g}" if isinstance(v, float) else str(v) for v in rec))
    return tab


def _usage(args: argparse.Namespace, console: Console) -> int:
    run = Run.load(args.run, args.planning)
    params = load_params(args.params) if args.params else ParamSet()
    usage = usage_table(
        run, hours_per_year=args.hours_per_year, pmin_band=args.pmin_band
    )
    reserve = reserve_table(run, hours_per_year=args.hours_per_year)
    if args.per == "year":
        # Key both tables by year so reserve EOH matches (name, year).
        usage = per_year(usage)
        usage["k"] = usage["year"] if not usage.empty else []
        if not reserve.empty:
            keys = ["name", "technology", "provision", "service", "direction"]
            reserve = reserve.groupby([*keys, "year"], as_index=False)["mwh"].sum()
            reserve["k"] = reserve["year"]
        table = maintenance_table(usage, params, reserve, args.hours_per_year)
    elif args.per == "horizon":
        usage = horizon_usage(usage)
        reserve = horizon_reserve(reserve)
        table = maintenance_table(usage, params, reserve, args.hours_per_year)
    else:
        table = maintenance_table(usage, params, reserve, args.hours_per_year)
    console.print(_table(by_technology(table), f"usage per technology ({args.per})"))
    if args.csv is not None:
        args.csv.mkdir(parents=True, exist_ok=True)
        table.to_csv(args.csv / "usage.csv", index=False)
        reserve.to_csv(args.csv / "reserve.csv", index=False)
        by_technology(table).to_csv(args.csv / "technologies.csv", index=False)
    return 0


def _theta(args: argparse.Namespace, console: Console) -> int:
    base = Run.load(args.base, args.planning_a, "A")
    sscc = Run.load(args.sscc, args.planning_b, "B")
    params = load_params(args.params)
    rep = derive_theta(
        base,
        sscc,
        params,
        pmin_band=args.pmin_band,
        hours_per_year=args.hours_per_year,
    )
    console.print(_table(rep.technologies, "maintenance cost delta (B - A)"))
    console.print(
        _table(rep.theta, "theta [$/MW·h] per technology, service and direction")
    )
    if args.csv is not None:
        rep.write_csv(args.csv)
    if args.json is not None:
        rep.write_json(args.json)
    return 0


def _maintenance(args: argparse.Namespace, console: Console) -> int:
    base = Run.load(args.base, args.planning_a, "A")
    sscc = Run.load(args.sscc, args.planning_b, "B")
    params = load_wear_params(args.counters)
    if args.initial is not None:
        params = params.with_initial(read_initial_csv(args.initial))
    if args.discount_rate is not None:
        params = dataclasses.replace(params, discount_rate=args.discount_rate)
    calendar = []
    if args.calendar is not None:
        calendar = read_calendar_csv(args.calendar, start_date=args.start_date)
    elif not args.no_outage_calendar:
        usage = unit_periods(base, params, hours_per_year=args.hours_per_year)
        types = default_types(params, {n: u.technology for n, u in usage.items()})
        calendar = calendar_from_outages(base, types, args.outage_type)
    rep = incremental_maintenance(
        base, sscc, params, calendar=calendar, hours_per_year=args.hours_per_year
    )
    console.print(_table(rep.technologies, "incremental maintenance cost (B - A)"))
    console.print(_table(rep.theta, "theta [$/MW·h]: rate vs event method"))
    cal = rep.events[rep.events["source"] == "calendar"]
    if not cal.empty:
        console.print(_table(cal, "calendar events (early / late / consistent)"))
    if args.csv is not None:
        rep.write_csv(args.csv)
    if args.json is not None:
        rep.write_json(args.json)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    console = Console(width=args.width)
    try:
        if args.command == "usage":
            return _usage(args, console)
        if args.command == "maintenance":
            return _maintenance(args, console)
        return _theta(args, console)
    except (FileNotFoundError, ValueError) as exc:
        print(f"gtopt_usage: {exc}", file=sys.stderr)
        return 2
