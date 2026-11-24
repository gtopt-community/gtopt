# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI: ``gtopt_pricing_uplift RESULTS_DIR [RESULTS_DIR ...]``.

One directory prints its per-unit uplift table; several (e.g. the same
case solved with ``--pricing-mode fixed``, ``relaxed`` and ``fast_start``)
also print a side-by-side comparison — the method with the lowest total
uplift fits best.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

from .uplift import UpliftReport, compute_uplift


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:,.2f}"


def _print_units(report: UpliftReport, top: int) -> None:
    print(f"\n== {report.results_dir}  (pricing_mode={report.pricing_mode})")
    hdr = f"{'unit':<28}{'MWh':>12}{'profit':>14}{'make-whole':>14}{'LOC':>14}{'uplift':>14}"
    print(hdr)
    print("-" * len(hdr))
    units = sorted(report.units, key=lambda u: u.total, reverse=True)
    for u in units[:top] if top > 0 else units:
        print(
            f"{u.commitment[:27]:<28}{_fmt(u.energy_mwh):>12}{_fmt(u.profit):>14}"
            f"{_fmt(u.make_whole):>14}{_fmt(u.lost_opportunity):>14}"
            f"{_fmt(u.total):>14}"
        )
    print(
        f"{'TOTAL':<28}{'':>12}{'':>14}{_fmt(report.make_whole):>14}"
        f"{_fmt(report.lost_opportunity):>14}{_fmt(report.total):>14}"
    )


def _print_comparison(reports: list[UpliftReport]) -> None:
    rows = [
        ("pricing_mode", lambda r: r.pricing_mode),
        ("applied", lambda r: ",".join(r.applied_modes) or "-"),
        ("fallback cells", lambda r: str(r.pricing_fallbacks)),
        ("make-whole", lambda r: _fmt(r.make_whole)),
        ("lost opportunity", lambda r: _fmt(r.lost_opportunity)),
        ("TOTAL UPLIFT", lambda r: _fmt(r.total)),
        ("node-h λ<0", lambda r: _fmt(r.prices.negative_node_hours)),
        ("node-h λ<-0.1", lambda r: _fmt(r.prices.deep_negative_node_hours)),
        ("λ_ref mean", lambda r: _fmt(r.prices.ref_mean)),
        ("λ_ref min", lambda r: _fmt(r.prices.ref_min)),
        ("λ_ref std", lambda r: _fmt(r.prices.ref_std)),
        ("λ_ref mean |Δ|", lambda r: _fmt(r.prices.ref_mean_abs_change)),
        ("congestion rent", lambda r: _fmt(r.prices.congestion_rent)),
    ]
    width = 18
    print("\n== comparison (lowest TOTAL UPLIFT fits best)")
    print(
        f"{'':<20}"
        + "".join(f"{Path(r.results_dir).name[: width - 1]:>{width}}" for r in reports)
    )
    for label, get in rows:
        print(f"{label:<20}" + "".join(f"{get(r):>{width}}" for r in reports))
    best = min(reports, key=lambda r: r.total)
    print(f"\nlowest total uplift: {best.results_dir} ({best.pricing_mode})")


def _write_csv(path: Path, reports: list[UpliftReport]) -> None:
    fields = [
        "results_dir",
        "pricing_mode",
        "commitment",
        "generator",
        "bus",
        "energy_mwh",
        "energy_margin",
        "commitment_cost",
        "profit",
        "make_whole",
        "self_schedule_profit",
        "lost_opportunity",
        "total",
    ]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in reports:
            for u in r.to_dict()["units"]:
                writer.writerow(
                    {
                        "results_dir": r.results_dir,
                        "pricing_mode": r.pricing_mode,
                        **{k: u[k] for k in fields[2:]},
                    }
                )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gtopt_pricing_uplift",
        description=(
            "Make-whole + lost-opportunity uplift and price statistics of "
            "solved gtopt unit-commitment runs (compare pricing modes)."
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
        help="directory a relative input_directory is resolved against "
        "(file-scheduled costs / limits); default: the planning file's dir",
    )
    p.add_argument(
        "--ref-bus",
        help="reference bus (name or uid) for the λ_ref statistics "
        "(default: first bus of bus_array)",
    )
    p.add_argument(
        "--top", type=int, default=15, help="units listed per case (0 = all)"
    )
    p.add_argument("--json-out", type=Path, help="write the full report(s) as JSON")
    p.add_argument("--csv-out", type=Path, help="write the per-unit table as CSV")
    p.add_argument("-q", "--quiet", action="store_true", help="no console tables")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        reports = [
            compute_uplift(
                d,
                planning=args.planning,
                case_dir=args.case_dir,
                ref_bus=args.ref_bus,
            )
            for d in args.results_dirs
        ]
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"gtopt_pricing_uplift: error: {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        for r in reports:
            _print_units(r, args.top)
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
