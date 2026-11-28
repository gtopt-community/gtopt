# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI entry-point for ``gtopt2opendss``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from gtopt_config import add_color_argument

from .convert import planning_to_dss, read_dispatch

__version__ = "0.1.0"

_DESCRIPTION = """\
Write a gtopt planning JSON (and optionally its solved dispatch) as an
OpenDSS script, so OpenDSS can run an AC power flow of the network and
dispatch gtopt optimised.

Lines become Line or Transformer objects (ohm, tap_ratio), demands
constant-PQ Loads, generators fixed Generators, bus
shunts (line charging, capacitor banks) Capacitors; the slack bus is the Vsource.  With --results the
solved load/generation/qgen/tap values of --block are used;
--solve runs the AC power flow through opendssdirect.py (optional
dependency) and, with --results from a LinDistFlow run, prints the
voltage and loss errors of gtopt against OpenDSS.
"""

_EPILOG = """\
Examples:
  gtopt2opendss ieee13/ieee13.json -o ieee13.dss
  gtopt2opendss ieee13/ieee13.json --results ieee13/output --block 1 --solve
"""


def make_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for ``gtopt2opendss``."""
    parser = argparse.ArgumentParser(
        prog="gtopt2opendss",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument("planning", type=Path, help="gtopt planning JSON")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="output .dss file (default: <planning stem>.dss)",
    )
    parser.add_argument(
        "-r",
        "--results",
        type=Path,
        default=None,
        help="gtopt output directory with the solved dispatch",
    )
    parser.add_argument(
        "-b",
        "--block",
        type=int,
        default=1,
        help="1-based flat block index to export (default: 1)",
    )
    parser.add_argument(
        "--frequency",
        type=float,
        default=60.0,
        help="base frequency [Hz] (default: 60)",
    )
    parser.add_argument(
        "--solve",
        action="store_true",
        help="solve the AC power flow with opendssdirect.py",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="logging level (default: INFO)",
    )
    add_color_argument(parser)
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def gtopt_vsq(planning: dict[str, Any], results: Path, block: int) -> dict[str, float]:
    """Bus squared voltages ``w`` of a LinDistFlow run (empty if absent)."""
    from gtopt_shared.case_reader import TimeGrid  # noqa: PLC0415
    from gtopt_shared.results_reader import GtoptResults  # noqa: PLC0415

    res = GtoptResults(results, TimeGrid.from_planning(planning))
    if not res.has("Bus", "vsq_sol"):
        return {}
    out = {}
    for bus in planning["system"].get("bus_array", []):
        out[str(bus["name"])] = float(
            res.block_of("Bus", "vsq_sol", bus["uid"])[0, block]
        )
    return out


def run(args: argparse.Namespace) -> int:
    """Execute the export for parsed *args*; return the exit code."""
    try:
        planning = json.loads(args.planning.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    block = args.block - 1
    dispatch = read_dispatch(planning, args.results, block) if args.results else None
    script = planning_to_dss(
        planning, block=block, dispatch=dispatch, frequency=args.frequency
    )
    out = args.output or args.planning.with_suffix(".dss")
    out.write_text(script, encoding="utf-8")
    logging.getLogger(__name__).info("wrote %s", out)
    if not args.solve:
        return 0
    from . import ac  # noqa: PLC0415

    try:
        engine = ac.load_engine()
    except ImportError:
        print(
            "error: --solve needs opendssdirect.py (pip install opendssdirect.py)",
            file=sys.stderr,
        )
        return 1
    sol = ac.solve_file(out, engine)
    print(f"converged    : {sol.converged}")
    print(f"losses       : {sol.losses_kw:.4f} kW  {sol.losses_kvar:.4f} kvar")
    print(f"source       : {sol.source_kw:.4f} kW  {sol.source_kvar:.4f} kvar")
    print(
        f"vmin / vmax  : {min(sol.vpu.values()):.5f} / {max(sol.vpu.values()):.5f} pu"
    )
    if args.results:
        vsq = gtopt_vsq(planning, args.results, block)
        if vsq:
            cmp = ac.compare_voltages(ac.vpu_from_vsq(vsq), sol)
            print(
                f"gtopt vs AC  : max |dV| {cmp.max_abs_dv:.5f} pu at {cmp.worst_bus}, "
                f"mean {cmp.mean_abs_dv:.5f} pu over {cmp.n_buses} buses"
            )
    return 0 if sol.converged else 1


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and run."""
    args = make_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level), format="%(levelname)s %(message)s"
    )
    sys.exit(run(args))


if __name__ == "__main__":
    main()
