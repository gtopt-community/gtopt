#!/usr/bin/env python3
"""CLI entry-point for ``sddp2gtopt``.

Mirrors :mod:`plp2gtopt.main` where it makes sense, dropping
PLP-specific flags (``--plp-legacy``, ``--pmin-as-flowright``,
``--reservoir-energy-scale``, …). v0 wires only ``--info`` and
``--validate``; the conversion path is stubbed in
:func:`sddp2gtopt.sddp2gtopt.convert_sddp_case` and will land in P1.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from gtopt_config import add_color_argument
from gtopt_shared.cli_flags import add_demand_tier_segments_argument
from gtopt_shared.cli_signals import (
    install_termination_handlers,
    signal_handler as _signal_handler,  # noqa: F401  re-export for back-compat / tests
)
from gtopt_shared.operator import add_operator_argument, select_operator
from gtopt_shared.results_writer import (
    RESULTS_AUTO,
    add_results_arguments,
    load_planning,
    report_import,
    results_output_dir,
)
from gtopt_shared.sanity_report import add_sanity_report_argument
from gtopt_shared.source_versions import add_source_version_argument

from .info_display import display_sddp_info
from .sddp2gtopt import _resolve_output_paths, convert_sddp_case, validate_sddp_case
from .source_version import SOURCE_FORMAT


__version__ = "0.1.0"


_DESCRIPTION = """\
Convert a PSR SDDP / NCP case directory to gtopt JSON format.

Auto-detects two input front-ends: the typed ``psrclasses.json``
snapshot the SDDP GUI writes, OR the raw fixed-format ``.dat`` collection
real PSR SDDP / NCP deployments ship (``sddp.dat`` + ``sistem``/
``ccombu``/``ctermi``/``chidro``/``cpde`` files; e.g. the Guatemalan AMM
cases).  The ``.dat`` path carries real fuel/thermal costs, so the
converted dispatch follows the PSR merit order.

``--info`` displays a case summary, ``--validate`` runs a light sanity
check, and ``-i / --input-dir`` + ``-o / --output-dir`` invoke the full
conversion (single-bus 1-scenario planning; richer network / reservoir
topology is on the roadmap — see DESIGN.md).
"""

_EPILOG = """\
Examples:
  sddp2gtopt --info  case0            Show a summary of the case
  sddp2gtopt --validate case0         Light schema sanity check
  sddp2gtopt -i case0 -o gtopt_case0  Convert to gtopt JSON
"""


# ``_signal_handler`` is re-exported from gtopt_shared.cli_signals at the
# top of this module (see the import block).  Existing test scaffolding
# that imports ``sddp2gtopt.main._signal_handler`` continues to work.


def make_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for ``sddp2gtopt``."""
    parser = argparse.ArgumentParser(
        prog="sddp2gtopt",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "positional_input",
        nargs="?",
        type=Path,
        default=None,
        help="SDDP case directory (alternative to -i)",
    )
    parser.add_argument(
        "-i",
        "--input-dir",
        type=Path,
        default=None,
        help="SDDP case directory containing psrclasses.json",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="output directory for the GTOpt planning JSON",
    )
    parser.add_argument(
        "--hydro-cost",
        type=float,
        default=0.0,
        help="uniform hydro opportunity cost [$/MWh] (water-value stand-in; "
        "default 0 = free hydro). Set near the PSR system marginal to match LMPs.",
    )
    parser.add_argument(
        "--import-limit",
        type=float,
        default=None,
        help="cap [MW] on aggregate interconnection import (Mexico/import-fuel "
        "generators) — the physical tie limit (GUA↔MEX ≈ 200); stops the $0 "
        "import fuels flooding the dispatch (.dat path only).",
    )
    parser.add_argument(
        "--blocks-per-stage",
        type=int,
        default=0,
        help="hourly blocks grouped into each GTOpt stage on the multi-bus .dat "
        "path.  Default 0 = single stage: a one-week NCP dispatch is "
        "deterministic and solves monolithic (like the plexos cases).  A "
        "positive divisor of the horizon (e.g. 24 = daily) splits it into ≥ 2 "
        "stages so the case runs SDDP / cascade — for genuine multi-week "
        "horizons.",
    )
    parser.add_argument(
        "--num-scenarios",
        type=int,
        default=None,
        help="SDDP block studies: keep only the first N historical inflow "
        "series as scenarios (default: all NUMERO DE SERIES).",
    )
    parser.add_argument(
        "--method",
        choices=("sddp", "monolithic", "cascade"),
        default="sddp",
        help="SDDP block studies: gtopt planning method written to the JSON "
        "(default: sddp; one phase per stage, one scene per scenario).",
    )
    # ``--deficit-segments`` (default ``first``): sddp.dat "Costo Deficit"
    # curve → one demand priced at the first segment, or (``all``) one
    # ``<demand>_deficit<k>`` sibling per segment.  Same split as
    # plp2gtopt's ``--falla-segments`` (gtopt_shared/demand_tiers.py).
    add_demand_tier_segments_argument(parser, dialect="sddp")
    parser.add_argument(
        "-l",
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="logging level (default: INFO)",
    )
    parser.add_argument(
        "--info",
        dest="show_info",
        action="store_true",
        help="show a summary of the case and exit",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="run schema sanity checks and exit (0 = ok)",
    )
    add_color_argument(parser)
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    add_source_version_argument(parser, SOURCE_FORMAT)
    add_operator_argument(parser)
    add_sanity_report_argument(parser)
    add_results_arguments(
        parser,
        source="SDDP",
        what="the directory of the PSR graf CSV files gerter.csv, gerhid.csv, "
        "gergnd.csv, gerbat.csv, cmgbus.csv, demxba.csv, defbus.csv, cirflw.csv, "
        "qturbi.csv, qverti.csv, volfin.csv, valagu.csv",
        auto="the input directory",
    )
    return parser


def _import_results(args: argparse.Namespace, input_dir: Path) -> int:
    """``--results``: import PSR result files next to the converted case."""
    from .results_import import import_sddp_results  # noqa: PLC0415

    src = input_dir if args.results == RESULTS_AUTO else Path(args.results)
    out_dir, case_json, _name = _resolve_output_paths(
        input_dir, args.output_dir, None, None
    )
    out = results_output_dir(out_dir, "sddp", args.results_output)
    try:
        res = import_sddp_results(src, load_planning(case_json), out, out_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: --results: {exc}", file=sys.stderr)
        return 1
    report_import(res, out)
    return 0


def _resolve_input_dir(args: argparse.Namespace) -> Path | None:
    """Pick ``-i`` over the positional argument; ``None`` if neither set."""
    if args.input_dir is not None:
        return Path(args.input_dir)
    if args.positional_input is not None:
        return Path(args.positional_input)
    return None


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and dispatch to the right action."""
    install_termination_handlers()

    parser = make_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    input_dir = _resolve_input_dir(args)
    if input_dir is None:
        parser.error("input directory is required: pass it positionally or via -i")
    select_operator(args.operator, "sddp", input_dir, args)

    options = {
        "input_dir": input_dir,
        "output_dir": args.output_dir,
        "hydro_cost": args.hydro_cost,
        "import_limit": args.import_limit,
        "blocks_per_stage": args.blocks_per_stage,
        "num_scenarios": args.num_scenarios,
        "method": args.method,
        "deficit_segments": args.deficit_segments,
        "sanity_report": args.sanity_report,
        "source_version": args.source_version,
    }

    if args.show_info:
        try:
            display_sddp_info(options)
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    if args.validate:
        sys.exit(0 if validate_sddp_case(options) else 1)

    if getattr(args, "results_only", False):
        if getattr(args, "results", None) is None:
            parser.error("--results-only needs --results")
        if _import_results(args, input_dir):
            sys.exit(1)
        return

    # Default action: convert. v0 raises NotImplementedError loudly;
    # P1 will return a CRITICAL-finding count instead.
    try:
        critical = convert_sddp_case(options) or 0
    except NotImplementedError as exc:
        print(f"sddp2gtopt: {exc}", file=sys.stderr)
        sys.exit(2)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if critical > 0:
        print(
            f"error: conversion completed with {critical} CRITICAL "
            "finding(s) — fix the underlying issue before using the output.",
            file=sys.stderr,
        )
        sys.exit(1)

    if getattr(args, "results", None) is not None and _import_results(args, input_dir):
        sys.exit(1)


if __name__ == "__main__":
    main()
