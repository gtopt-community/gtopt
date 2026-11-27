# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Command line of ``gtopt2plexos``.

Example::

    gtopt2plexos case/my_case.json -o plexos_case
    plexos2gtopt -i plexos_case -o back --block-layout plexos_case/block_layout.csv
    gtopt2plexos case/my_case.json -o plexos_case --results case/output
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from gtopt_shared.case_reader import GtoptCase
from gtopt2plexos.results import SOLUTION_DIR, PlexosResultsWriter
from gtopt2plexos.writer import PlexosWriter


def build_parser() -> argparse.ArgumentParser:
    """Argument parser of ``gtopt2plexos``."""
    p = argparse.ArgumentParser(
        prog="gtopt2plexos",
        description=(
            "Write a gtopt planning (JSON + input tables) as a PLEXOS dataset "
            "(MasterDataSet XML + Data Files readable by plexos2gtopt), and "
            "optionally its gtopt results as PLEXOS solution tables. What "
            "PLEXOS / plexos2gtopt cannot carry is reported as warnings and in "
            "gtopt2plexos.json."
        ),
    )
    p.add_argument("case", type=Path, help="gtopt planning JSON")
    p.add_argument("-o", "--output", type=Path, required=True, help="output directory")
    p.add_argument(
        "--start-date",
        type=date.fromisoformat,
        default=date(2030, 1, 1),
        metavar="YYYY-MM-DD",
        help="calendar date of the first hour (default: 2030-01-01)",
    )
    p.add_argument(
        "--results",
        type=Path,
        metavar="DIR",
        help=(
            "gtopt output directory of the case: also write its solution as "
            f"PLEXOS solution tables in <output>/{SOLUTION_DIR}/"
        ),
    )
    p.add_argument(
        "--results-only",
        action="store_true",
        help="with --results: write only the solution tables, not the dataset",
    )
    p.add_argument(
        "--strict", action="store_true", help="exit 2 when any warning is raised"
    )
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.results_only and args.results is None:
        parser.error("--results-only needs --results DIR")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    case = GtoptCase.load(args.case)
    writer = PlexosWriter(case, args.output, start_date=args.start_date)
    warnings: list[str] = []
    try:
        if args.results_only:
            writer.build()
        else:
            writer.write()
            flags = " ".join(writer.plexos2gtopt_flags())
            print(
                f"wrote {args.output} ({len(writer.warnings)} warning(s)); read back "
                f"with: plexos2gtopt -i {args.output} -o <out> {flags}"
            )
        warnings += writer.warnings
        if args.results is not None:
            res = PlexosResultsWriter(
                case, args.results, args.output / SOLUTION_DIR, writer=writer
            )
            res.write()
            warnings += res.warnings
            print(
                f"wrote PLEXOS solution tables {args.output / SOLUTION_DIR} "
                "(t_*.csv; read with plexos2gtopt.plexos_solution.csv_table_source)"
            )
    except ValueError as exc:
        print(f"gtopt2plexos: {exc}", file=sys.stderr)
        return 1
    return 2 if args.strict and warnings else 0
