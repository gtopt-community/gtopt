# SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI entry point for gtopt_results_summary."""

from __future__ import annotations

import argparse
import json
import sys

from gtopt_shared.solution_csv import ScaleObjectiveMismatchError

from .summary import summarize_results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gtopt_results_summary",
        description=(
            "Produce a KPI summary (JSON) of a GTOpt optimization "
            "result directory or ZIP."
        ),
    )
    parser.add_argument(
        "source",
        help="Path to the output directory or a results .zip file.",
    )
    parser.add_argument(
        "--scale-objective",
        type=float,
        default=None,
        help=(
            "Legacy outputs only. solution.csv reports obj_value in physical "
            "units and records scale_objective in its own column; for such "
            "outputs this value is only checked against the column (a "
            "mismatch is an error). For old outputs without the column it is "
            "the factor used to convert the LP-raw obj_value to physical "
            "units (default: 1000, the monolithic default)."
        ),
    )
    parser.add_argument(
        "--tech-map",
        default=None,
        help=(
            "Optional path to a JSON file mapping generator UIDs (as strings) "
            "to a technology label (hydro/solar/wind/thermal/battery/other)."
        ),
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output with indent=2.",
    )
    args = parser.parse_args(argv)

    tech_map = None
    if args.tech_map:
        with open(args.tech_map, "r", encoding="utf-8") as fh:
            tech_map = json.load(fh)

    try:
        summary = summarize_results(
            args.source,
            tech_map=tech_map,
            scale_objective=args.scale_objective,
        )
    except ScaleObjectiveMismatchError as exc:
        print(f"gtopt_results_summary: error: {exc}", file=sys.stderr)
        return 2
    indent = 2 if args.pretty else None
    json.dump(summary, sys.stdout, indent=indent, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
