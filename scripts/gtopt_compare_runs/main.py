# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Command line of ``gtopt_compare_runs``.

Examples::

    gtopt_compare_runs runA/output runB/output
    gtopt_compare_runs gtopt_case/results_plp gtopt_case/results \\
        --label-a PLP --label-b gtopt --rel-tol 0.01 --json report.json
    gtopt_compare_runs A B --streams Generator,Bus/balance_dual --csv diffs/

Exit codes: 0 every compared stream within tolerance, 1 some stream beyond
tolerance, 2 bad input (missing directory, nothing to compare).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console

from gtopt_compare_runs.engine import CompareOptions, compare_runs
from gtopt_compare_runs.reader import Run
from gtopt_compare_runs.report import render, write_csv, write_json, write_plots


def build_parser() -> argparse.ArgumentParser:
    """Argument parser of ``gtopt_compare_runs``."""
    p = argparse.ArgumentParser(
        prog="gtopt_compare_runs",
        description=(
            "Compare two gtopt output directories (A vs B): objective, every "
            "<Class>/<field> stream (totals, max abs / rel differences, top "
            "differing elements, elements in one run only), generation by "
            "technology and reservoir volumes.  Elements are matched by name, "
            "time by scenario / stage / block position, so a PLP / PSR SDDP / "
            "PLEXOS solution imported with plp2gtopt / sddp2gtopt / plexos2gtopt "
            "--results compares against a gtopt run of the converted case."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="exit: 0 within tolerance, 1 beyond tolerance, 2 bad input",
    )
    p.add_argument("run_a", type=Path, help="output directory of run A")
    p.add_argument("run_b", type=Path, help="output directory of run B")
    p.add_argument(
        "--planning-a",
        type=Path,
        metavar="JSON",
        help="planning of A (default: <A>/planning.json, else the case JSON above A)",
    )
    p.add_argument("--planning-b", type=Path, metavar="JSON", help="planning of B")
    p.add_argument("--label-a", default="", help="name of run A in the report")
    p.add_argument("--label-b", default="", help="name of run B in the report")
    p.add_argument(
        "--streams",
        default="",
        metavar="LIST",
        help=(
            "comma-separated Class or Class/field filters "
            "(default: every stream but Commitment, Converter, UserConstraint)"
        ),
    )
    p.add_argument(
        "--abs-tol", type=float, default=1e-3, help="absolute tolerance per cell"
    )
    p.add_argument(
        "--rel-tol", type=float, default=1e-3, help="relative tolerance per cell"
    )
    p.add_argument("--top", type=int, default=10, help="elements listed per stream")
    p.add_argument(
        "--all-streams",
        action="store_true",
        help="list the top elements of passing streams too",
    )
    p.add_argument("--csv", type=Path, metavar="DIR", help="write the CSV tables")
    p.add_argument("--json", type=Path, metavar="PATH", help="write the JSON summary")
    p.add_argument(
        "--plot", type=Path, metavar="DIR", help="write PNG charts (matplotlib)"
    )
    p.add_argument("--width", type=int, default=None, help="console width")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    try:
        a = Run.load(args.run_a, args.planning_a, args.label_a or "A")
        b = Run.load(args.run_b, args.planning_b, args.label_b or "B")
    except FileNotFoundError as exc:
        print(f"gtopt_compare_runs: {exc}", file=sys.stderr)
        return 2
    opts = CompareOptions(
        abs_tol=args.abs_tol,
        rel_tol=args.rel_tol,
        streams=tuple(s.strip() for s in args.streams.split(",") if s.strip()),
        top=args.top,
    )
    report = compare_runs(a, b, opts)
    if not report.streams and report.objective_a is None and report.objective_b is None:
        print("gtopt_compare_runs: nothing to compare", file=sys.stderr)
        return 2
    render(report, Console(width=args.width), all_streams=args.all_streams)
    if args.csv is not None:
        write_csv(report, args.csv)
    if args.json is not None:
        write_json(report, args.json)
    if args.plot is not None:
        write_plots(report, args.plot)
    return 0 if report.passed else 1
