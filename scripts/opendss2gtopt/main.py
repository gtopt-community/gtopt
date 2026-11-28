# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI entry-point for ``opendss2gtopt``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from gtopt_config import add_color_argument

from .convert import NETWORK_MODELS, TIME_MODES, build_planning
from .dss_parser import parse_dss
from .dss_values import DssValueError
from .network import SCALINGS, Network, build_network
from .expansion import (
    DEFAULT_BESS_USD_KWH,
    STUDIES,
    add_hosting_candidates,
    add_vad_candidates,
)

__version__ = "0.1.0"

_DESCRIPTION = """\
Convert an OpenDSS distribution circuit (.dss script) to a gtopt planning.

The script is parsed directly (Redirect/Compile, New/Edit, like=, ~
continuations, RPN values); no OpenDSS engine is needed.  The
phase-coordinate circuit is reduced to a balanced single-phase
equivalent: Line/LineCode/LineGeometry -> Line (positive-sequence ohm,
3/n scaled for n-phase laterals), Transformer -> Line type=transformer
(tap_ratio), RegControl -> optimised taps, Load -> Demand, Generator/
PVSystem -> Generator, Storage -> Battery, Capacitor -> Capacitor.
The per-phase data is written to <name>_phases.json for later use.

--network-model lindistflow writes the distribution fields (bus voltage
limits, reactive power, taps, capacitors) for gtopt's LinDistFlow
network model; the default 'dc' output is a plain DC OPF case.
"""

_EPILOG = """\
Examples:
  opendss2gtopt --info IEEE13Nodeckt.dss
  opendss2gtopt IEEE13Nodeckt.dss -o ieee13 --network-model lindistflow
  opendss2gtopt IEEE123Master.dss -o ieee123 --time-mode daily
"""


def make_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for ``opendss2gtopt``."""
    parser = argparse.ArgumentParser(
        prog="opendss2gtopt",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "positional_input",
        nargs="?",
        type=Path,
        default=None,
        help="OpenDSS master .dss script (alternative to -i)",
    )
    parser.add_argument(
        "-i", "--input", type=Path, default=None, help="OpenDSS master .dss script"
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="output directory (default: the circuit name)",
    )
    parser.add_argument(
        "--name", default=None, help="planning name (default: the circuit name)"
    )
    parser.add_argument(
        "--network-model",
        choices=NETWORK_MODELS,
        default="dc",
        help="gtopt network model to target (default: dc)",
    )
    parser.add_argument(
        "--scaling",
        choices=SCALINGS,
        default="phase-count",
        help="n-phase branch impedance scaling (default: "
        "phase-count = 3/n x Z1; pos-seq = Z1)",
    )
    parser.add_argument(
        "--time-mode",
        choices=TIME_MODES,
        default="snapshot",
        help="snapshot (1 block, nominal loads) or daily "
        "(24 hourly blocks from the 'daily' LoadShapes)",
    )
    parser.add_argument(
        "--no-line-charging",
        action="store_true",
        help="drop line shunt capacitance (Bus shunt_q)",
    )
    parser.add_argument(
        "--loss-segments",
        type=int,
        default=None,
        metavar="N",
        help="piecewise segments of the quadratic line losses "
        "(default: 16 with lindistflow, gtopt's default otherwise)",
    )
    parser.add_argument(
        "--no-source-impedance",
        action="store_true",
        help="put the slack directly on the source bus",
    )
    parser.add_argument(
        "--fixed-taps",
        action="store_true",
        help="keep regulator taps fixed (no tap variables)",
    )
    parser.add_argument(
        "--line-rating",
        choices=("norm", "emerg", "none"),
        default="norm",
        help="branch flow limit: normamps (default), emergamps or none",
    )
    parser.add_argument(
        "--import-cost",
        type=float,
        default=50.0,
        help="substation energy price [$/MWh] (default: 50)",
    )
    parser.add_argument(
        "--der-cost",
        type=float,
        default=0.0,
        help="generator / PV energy cost [$/MWh] (default: 0)",
    )
    parser.add_argument(
        "--demand-fail-cost",
        type=float,
        default=1000.0,
        help="unserved-energy penalty [$/MWh] (default: 1000)",
    )
    parser.add_argument(
        "--vmin",
        type=float,
        default=None,
        help="bus voltage lower limit [pu] (default: the "
        "circuit's normvminpu, else 0.95)",
    )
    parser.add_argument(
        "--vmax",
        type=float,
        default=None,
        help="bus voltage upper limit [pu] (default: the "
        "circuit's normvmaxpu, else 1.05)",
    )
    study = parser.add_argument_group(
        "expansion studies (imply --network-model lindistflow --time-mode daily)"
    )
    study.add_argument(
        "--study",
        choices=STUDIES,
        default="none",
        help="vad = Chilean VAD sizing core (parallel circuits, substation "
        "upgrade, BESS non-wires alternative at public CNE costs); hosting "
        "= PV hosting capacity (default: none)",
    )
    study.add_argument(
        "--growth",
        type=float,
        default=1.5,
        help="vad: demand growth factor of the dimensioning year (default: 1.5)",
    )
    study.add_argument(
        "--mip",
        action="store_true",
        help="integer expansion modules (binary circuits / BESS / PV modules)",
    )
    study.add_argument(
        "--bess-usd-kwh",
        type=float,
        default=DEFAULT_BESS_USD_KWH,
        help="vad: BESS overnight cost [USD/kWh] (assumption; default: "
        f"{DEFAULT_BESS_USD_KWH:g})",
    )
    study.add_argument(
        "--hosting-buses",
        default=None,
        help="hosting: comma-separated PV candidate buses (default: feeder end)",
    )
    parser.add_argument(
        "--info", action="store_true", help="print a summary of the circuit and exit"
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


def summary(net: Network) -> str:
    """One-screen summary of the equivalent network."""
    kinds: dict[str, int] = {}
    for br in net.branches:
        kinds[br.kind] = kinds.get(br.kind, 0) + 1
    kvs = sorted({b.kv for b in net.buses.values()})
    lines = [
        f"circuit      : {net.name}",
        f"source       : {net.source_bus} {net.source_kv:g} kV, {net.source_pu:g} pu",
        f"buses        : {len(net.buses)} (kV bases {', '.join(f'{v:g}' for v in kvs)})",
        "branches     : " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())),
        f"regulators   : {sum(1 for b in net.branches if b.regulator)}",
        f"loads        : {len(net.loads)}  "
        f"P={sum(x.p_mw for x in net.loads):.4g} MW  "
        f"Q={sum(x.q_mvar for x in net.loads):.4g} MVAr",
        f"generators   : {len(net.generators)}",
        f"storage      : {len(net.storages)}",
        f"capacitors   : {len(net.capacitors)}",
        f"loadshapes   : {len(net.loadshapes)}",
    ]
    lines += [f"warning      : {w}" for w in net.warnings]
    return "\n".join(lines)


def _limits(args: argparse.Namespace, options: dict[str, str]) -> tuple[float, float]:
    vmin = (
        args.vmin if args.vmin is not None else float(options.get("normvminpu", 0.95))
    )
    vmax = (
        args.vmax if args.vmax is not None else float(options.get("normvmaxpu", 1.05))
    )
    return vmin, vmax


def run(args: argparse.Namespace) -> int:
    """Execute the conversion for parsed *args*; return the exit code."""
    src = args.input or args.positional_input
    if src is None:
        print(
            "error: input is required: pass it positionally or via -i", file=sys.stderr
        )
        return 2
    try:
        circuit = parse_dss(src)
        net = build_network(
            circuit, scaling=args.scaling, line_charging=not args.no_line_charging
        )
    except (FileNotFoundError, ValueError, DssValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.info:
        print(summary(net))
        return 0
    if args.line_rating != "norm":
        for br in net.branches:
            br.rating_mva = br.emerg_mva if args.line_rating == "emerg" else None
    vmin, vmax = _limits(args, circuit.options)
    name = args.name or net.name
    if args.study != "none":
        args.network_model = "lindistflow"
        args.time_mode = "daily"
    planning = build_planning(
        net,
        name=name,
        network_model=args.network_model,
        time_mode=args.time_mode,
        import_cost=args.import_cost,
        der_cost=args.der_cost,
        demand_fail_cost=args.demand_fail_cost,
        vmin=vmin,
        vmax=vmax,
        optimize_taps=not args.fixed_taps,
        source_impedance=not args.no_source_impedance,
        loss_segments=args.loss_segments,
    )
    if args.study == "vad":
        add_vad_candidates(
            planning,
            net,
            growth=args.growth,
            integer=args.mip,
            bess_usd_kwh=args.bess_usd_kwh,
        )
    elif args.study == "hosting":
        add_hosting_candidates(
            planning,
            buses=args.hosting_buses.split(",") if args.hosting_buses else None,
            integer=args.mip,
        )
    out_dir = args.output_dir or Path(name)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.json"
    out.write_text(json.dumps(planning, indent=2) + "\n", encoding="utf-8")
    net.phase_data["branches"] = [
        {
            "name": b.name,
            "kind": b.kind,
            "bus_a": b.bus_a,
            "bus_b": b.bus_b,
            "nphases": b.nphases,
            "members": b.members,
        }
        for b in net.branches
    ]
    (out_dir / f"{name}_phases.json").write_text(
        json.dumps(net.phase_data, indent=1, default=str) + "\n", encoding="utf-8"
    )
    logging.getLogger(__name__).info("wrote %s", out)
    return 0


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and run."""
    args = make_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level), format="%(levelname)s %(message)s"
    )
    sys.exit(run(args))


if __name__ == "__main__":
    main()
