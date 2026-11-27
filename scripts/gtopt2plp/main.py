# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Command line of ``gtopt2plp``.

Example::

    gtopt2plp case/my_case.json -o plp_case
    plp2gtopt -i plp_case -o back -y all     # round trip (flags in gtopt2plp.json)
    gtopt2plp case/my_case.json -o plp_case --results case/results  # + plpcen.csv, ...
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from gtopt_shared.case_reader import GtoptCase
from gtopt2plp.results import write_results
from gtopt2plp.writer import PlpWriter


def build_parser() -> argparse.ArgumentParser:
    """Argument parser of ``gtopt2plp``."""
    p = argparse.ArgumentParser(
        prog="gtopt2plp",
        description=(
            "Write a gtopt planning (JSON + input tables) as a PLP case (.dat "
            "files readable by plp2gtopt). What PLP cannot express is reported "
            "as warnings; gtopt2plp.json lists them and the plp2gtopt flags "
            "(-y, -p, -d) that carry what the files cannot."
        ),
    )
    p.add_argument("case", type=Path, help="gtopt planning JSON")
    p.add_argument("-o", "--output", type=Path, required=True, help="output directory")
    p.add_argument(
        "--start-month",
        type=int,
        choices=range(1, 13),
        metavar="1-12",
        help="calendar month of stage 1 when the stages carry no 'month' (default: 4, April)",
    )
    p.add_argument(
        "--results",
        type=Path,
        metavar="DIR",
        help=(
            "gtopt output directory of the solved case: also write its results as "
            "PLP output files (plpcen.csv, plpbar.csv, plplin.csv, plpemb.csv, "
            "plpess.csv, plpfal.csv, plpcostop.csv)"
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
    if args.results_only and args.results is None:
        print("gtopt2plp: --results-only needs --results DIR", file=sys.stderr)
        return 1
    case = GtoptCase.load(args.case)
    writer = PlpWriter(case, args.output, start_month=args.start_month)
    try:
        if args.results_only:
            args.output.mkdir(parents=True, exist_ok=True)
            writer.collect()
        else:
            writer.write()
        if args.results is not None:
            files = write_results(case, args.results, args.output, writer)
            print(f"wrote {len(files)} PLP result file(s) from {args.results}")
    except ValueError as exc:
        print(f"gtopt2plp: {exc}", file=sys.stderr)
        return 1
    if args.results_only:
        return 2 if args.strict and writer.warnings else 0
    flags = " ".join(writer.plp2gtopt_flags())
    print(
        f"wrote {args.output} ({len(writer.warnings)} warning(s)); read back with: "
        f"plp2gtopt -i {args.output} -o <out> {flags}"
    )
    sets = " ".join(f"--set {x}" for x in writer.gtopt_set())
    if sets:
        print(f"solve the read-back case with: gtopt <out>/<out>.json {sets}")
    return 2 if args.strict and writer.warnings else 0
