# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI: ``gtopt_cost_recovery RESULTS_DIR [RESULTS_DIR ...]``.

Prints, per solved case, the cost-recovery table of every expandable
project (revenue at the marginal prices, opex (variable + fixed O&M),
annualised capex, profit, marginal value vs cost per unit) and the market balance.  Several
directories (e.g. the LP and the MIP of one case, or ``--pricing-mode
fixed`` vs ``relaxed``) also print a side-by-side comparison.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

from .recovery import ProjectRecovery, RecoveryReport, compute_recovery

_COLUMNS = (
    "kind",
    "name",
    "unit",
    "existing",
    "new",
    "power_new",
    "revenue",
    "opex",
    "fixed_om",
    "total_opex",
    "margin_new",
    "variable_margin",
    "capacity_rent",
    "failure_rent",
    "capex",
    "power_capex",
    "total_capex",
    "profit",
    "profit_over_capex",
    "make_whole",
    "value_per_unit",
    "cost_per_unit",
    "net_value_per_unit",
)


def _fmt(v: float | None, digits: int = 0) -> str:
    return "-" if v is None else f"{v:,.{digits}f}"


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{100.0 * v:+.2f}%"


def _row(p: ProjectRecovery) -> str:
    new = _fmt(p.new, 1)
    if p.power_new is not None:
        new = f"{new}/{_fmt(p.power_new, 1)}MW"
    return (
        f"{p.kind[:4] + ':' + p.name:<22}{new:>16}{_fmt(p.revenue):>14}"
        f"{_fmt(p.total_opex):>14}{_fmt(p.total_capex):>14}{_fmt(p.profit):>12}"
        f"{_pct(p.profit_over_capex):>10}{_fmt(p.value_per_unit):>12}"
        f"{_fmt(p.cost_per_unit):>12}"
    )


def print_report(report: RecoveryReport) -> None:
    """Console table of one report."""
    unit = "$/yr" if report.scale != 1.0 else "$"
    print(
        f"\n== {report.results_dir}  (horizon {report.horizon_hours:g} h, "
        f"money in {unit}, pricing_mode={report.pricing_mode}"
        + (
            f", applied={','.join(report.applied_modes)}"
            if report.applied_modes
            else ""
        )
        + ")"
    )
    hdr = (
        f"{'project':<22}{'new':>16}{'revenue':>14}{'opex':>14}{'capex':>14}"
        f"{'profit':>12}{'π/capex':>10}{'value/unit':>12}{'cost/unit':>12}"
    )
    print(hdr)
    print("-" * len(hdr))
    for p in report.projects:
        print(_row(p))
    for p in report.projects:
        if abs(p.fixed_om) >= 0.5:
            print(
                f"  {p.name}: opex {_fmt(p.total_opex)} = variable "
                f"{_fmt(p.opex)} + fixed O&M {_fmt(p.fixed_om)}"
            )
    for p in report.projects:
        if p.kind != "Generator" or abs(p.margin) < 0.5:
            continue
        line = (
            f"  {p.name}: margin {_fmt(p.margin)} = variable-cost margin "
            f"{_fmt(p.variable_margin)} + capacity rent {_fmt(p.capacity_rent)}"
        )
        if abs(p.failure_rent) >= 0.5:
            line += f" + FAILURE rent {_fmt(p.failure_rent)}"
        print(line)
    print(
        f"{'TOTAL':<22}{'':>16}{'':>14}{'':>14}{_fmt(report.total_capex):>14}"
        f"{_fmt(report.total_profit):>12}"
    )
    if report.total_make_whole > 0.5:
        print(
            f"make-whole uplift (Σ max(0, −profit) of built): "
            f"{_fmt(report.total_make_whole)}"
        )
    for p in report.existing:
        print(
            f"  existing {p.name}: revenue {_fmt(p.revenue)}, opex {_fmt(p.opex)}, "
            f"rent {_fmt(p.margin)}"
        )
    m = report.market
    print(
        f"market: load pays {_fmt(m.load_payments)} = generators "
        f"{_fmt(m.generator_revenue)} + batteries {_fmt(m.battery_net_revenue)} "
        f"+ converters {_fmt(m.converter_net_revenue)} "
        f"+ congestion rent {_fmt(m.congestion_rent)}  (residual "
        f"{_fmt(m.residual, 2)}); unserved {_fmt(m.unserved_mwh, 1)} MWh"
    )
    if report.obj_value is not None:
        print(f"objective (solution.csv): {_fmt(report.obj_value, 2)}")
    if report.blocks:
        _print_blocks(report)


def _print_blocks(report: RecoveryReport) -> None:
    print(
        f"{'scen':>5}{'stage':>6}{'block':>6}{'bus':>5}{'price':>12}"
        f"{'set cost':>12}{'cap. rent':>12}{'failure':>10}  setter"
    )
    for b in report.blocks:
        print(
            f"{b['scenario']:>5}{b['stage']:>6}{b['block']:>6}{b['bus']:>5}"
            f"{_fmt(b['price'], 2):>12}{_fmt(b['set_cost'], 2):>12}"
            f"{_fmt(b['capacity_rent'], 2):>12}{_fmt(b['failure_rent'], 2):>10}"
            f"  {b['setter']}"
        )


def _print_comparison(reports: list[RecoveryReport]) -> None:
    width = 18
    print("\n== comparison")
    print(
        f"{'':<26}"
        + "".join(f"{Path(r.results_dir).name[: width - 1]:>{width}}" for r in reports)
    )
    rows = [
        ("objective", lambda r: _fmt(r.obj_value)),
        ("total capex", lambda r: _fmt(r.total_capex)),
        ("total profit", lambda r: _fmt(r.total_profit)),
        ("make-whole uplift", lambda r: _fmt(r.total_make_whole)),
        ("congestion rent", lambda r: _fmt(r.market.congestion_rent)),
        ("load payments", lambda r: _fmt(r.market.load_payments)),
    ]
    for label, get in rows:
        print(f"{label:<26}" + "".join(f"{get(r):>{width}}" for r in reports))


def _write_csv(path: Path, reports: list[RecoveryReport]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["results_dir", *_COLUMNS])
        writer.writeheader()
        for r in reports:
            for p in r.projects:
                d = p.to_dict()
                writer.writerow(
                    {"results_dir": r.results_dir, **{k: d[k] for k in _COLUMNS}}
                )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gtopt_cost_recovery",
        description=(
            "Per-project cost recovery of solved gtopt expansion runs: revenue "
            "at the marginal prices vs opex + annualised capex."
        ),
    )
    p.add_argument(
        "results_dirs", nargs="+", type=Path, help="gtopt output directories"
    )
    p.add_argument(
        "--planning",
        type=Path,
        help="planning JSON (default: <results_dir>/planning.json)",
    )
    p.add_argument(
        "--case-dir",
        type=Path,
        help="directory a relative input_directory is resolved against",
    )
    p.add_argument(
        "--per-year",
        action="store_true",
        help="rescale money to one year (x 8760 / horizon hours)",
    )
    p.add_argument(
        "--include-existing",
        action="store_true",
        help="also list the inframarginal rent of non-expandable generators",
    )
    p.add_argument(
        "--price-blocks",
        action="store_true",
        help=(
            "print the per-block price decomposition λ = set cost + "
            "capacity rent (+ failure)"
        ),
    )
    p.add_argument("--json-out", type=Path, help="write the full report(s) as JSON")
    p.add_argument("--csv-out", type=Path, help="write the per-project table as CSV")
    p.add_argument("-q", "--quiet", action="store_true", help="no console tables")
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        reports = [
            compute_recovery(
                d,
                planning=args.planning,
                case_dir=args.case_dir,
                per_year=args.per_year,
                include_existing=args.include_existing,
                price_blocks=args.price_blocks,
            )
            for d in args.results_dirs
        ]
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"gtopt_cost_recovery: error: {exc}", file=sys.stderr)
        return 2
    if not args.quiet:
        for r in reports:
            print_report(r)
        if len(reports) > 1:
            _print_comparison(reports)
    if args.json_out is not None:
        payload = [r.to_dict() for r in reports]
        args.json_out.write_text(
            json.dumps(payload[0] if len(payload) == 1 else payload, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.csv_out is not None:
        _write_csv(args.csv_out, reports)
    return 0
