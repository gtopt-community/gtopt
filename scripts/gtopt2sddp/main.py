# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Command line of ``gtopt2sddp``.

Example::

    gtopt2sddp case/my_case.json -o psr_case --start-year 2030
    sddp2gtopt -i psr_case -o back          # round trip
    gtopt2sddp case/my_case.json -o psr_case --results case/results  # + gerter.csv, ...
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from gtopt_shared.case_reader import GtoptCase
from gtopt2sddp.results import write_results
from gtopt2sddp.writer import SddpWriter


def build_parser() -> argparse.ArgumentParser:
    """Argument parser of ``gtopt2sddp``."""
    p = argparse.ArgumentParser(
        prog="gtopt2sddp",
        description=(
            "Write a gtopt planning (JSON + input tables) as a PSR SDDP block "
            "study (.dat files readable by sddp2gtopt). What PSR cannot "
            "express is reported as warnings and in gtopt2sddp.json."
        ),
    )
    p.add_argument("case", type=Path, help="gtopt planning JSON")
    p.add_argument("-o", "--output", type=Path, required=True, help="output directory")
    p.add_argument("--sfx", default="gt", help="2-letter PSR system id (default: gt)")
    p.add_argument(
        "--start-year", type=int, default=2030, help="year of stage 1 (default: 2030)"
    )
    p.add_argument(
        "--start-month",
        type=int,
        choices=range(1, 13),
        metavar="1-12",
        help="calendar month of stage 1 (default: stage 1 'month', else 1)",
    )
    p.add_argument(
        "--hydro-year",
        type=int,
        default=2000,
        help="hydrology year of scenario 1 in hinflw.dat (default: 2000)",
    )
    p.add_argument(
        "--results",
        type=Path,
        metavar="DIR",
        help=(
            "gtopt output directory of the solved case: also write its results as "
            "PSR SDDP graf CSV files (gerter.csv, gerhid.csv, cmgbus.csv, "
            "cirflw.csv, volfin.csv, ...)"
        ),
    )
    p.add_argument(
        "--results-only",
        action="store_true",
        help="with --results: write only the result files, not the .dat inputs",
    )
    p.add_argument(
        "--strict", action="store_true", help="exit 2 when any warning is raised"
    )
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    if len(args.sfx) != 2:
        print("gtopt2sddp: --sfx must have 2 characters", file=sys.stderr)
        return 1
    case = GtoptCase.load(args.case)
    writer = SddpWriter(
        case,
        args.output,
        sfx=args.sfx.lower(),
        start_year=args.start_year,
        start_month=args.start_month,
        hydro_year=args.hydro_year,
        title=f"gtopt2sddp export of {args.case.name}",
    )
    if args.results_only and args.results is None:
        print("gtopt2sddp: --results-only needs --results DIR", file=sys.stderr)
        return 1
    try:
        if not args.results_only:
            writer.write()
        if args.results is not None:
            files = write_results(case, args.results, args.output, writer)
            print(f"wrote {len(files)} PSR result file(s) from {args.results}")
    except ValueError as exc:
        print(f"gtopt2sddp: {exc}", file=sys.stderr)
        return 1
    if args.results_only:
        return 2 if args.strict and writer.warnings else 0
    print(f"wrote {args.output} ({len(writer.warnings)} warning(s))")
    return 2 if args.strict and writer.warnings else 0
