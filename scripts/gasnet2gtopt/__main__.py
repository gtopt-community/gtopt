# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI of the gas-network converter.

Usage::

    gasnet2gtopt case.gasnet.json -o out/planning.json [--legacy]
        [--season winter|summer] [--compressor off|series] [--linepack]
        [--nblocks N] [--segments N] [--fuel-model fixed_fraction|saldarriaga]
        [--cs-gamma G] [--booster off|on] [--booster-drive gas|electric]
        [--plant-pmin GEN=BAR ...] [--derate] [--no-plant-pressure]

Writes ``planning.json`` and a ``planning.meta.json`` sidecar (which also
records the resolved description schema version under ``source_format``).
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from gasnet2gtopt.common import BuildOptions
from gasnet2gtopt.legacy import build_legacy
from gasnet2gtopt.native import build_native
from gasnet2gtopt.source_version import SOURCE_FORMAT, detect_version
from gtopt_shared.source_versions import (
    add_source_version_argument,
    apply_source_version,
)

logger = logging.getLogger("gasnet2gtopt")


def make_parser() -> argparse.ArgumentParser:
    """The argparse parser (exposed for tests)."""
    ap = argparse.ArgumentParser(
        prog="gasnet2gtopt",
        description="Convert a gas-network description to a gtopt planning.",
    )
    ap.add_argument("gasnet", type=Path, help="*.gasnet.json description")
    ap.add_argument("-o", "--out", type=Path, required=True, help="planning JSON")
    ap.add_argument(
        "--legacy",
        action="store_true",
        help="emit the prototype's DecisionVariable/UserConstraint form",
    )
    ap.add_argument("--season", choices=["summer", "winter"], default="winter")
    ap.add_argument("--compressor", choices=["off", "series"], default="off")
    ap.add_argument("--linepack", action="store_true", help="model pipe line pack")
    ap.add_argument("--nblocks", type=int, default=24, help="hourly blocks")
    ap.add_argument(
        "--segments", type=int, default=None, help="Weymouth tangents on every pipe"
    )
    ap.add_argument(
        "--fuel-model",
        choices=["fixed_fraction", "saldarriaga"],
        default=None,
        help="override the compressor fuel model",
    )
    ap.add_argument(
        "--cs-gamma", type=float, default=None, help="override Saldarriaga gamma"
    )
    ap.add_argument(
        "--booster",
        choices=["off", "on"],
        default="off",
        help="fuel-gas booster stations (role=fuel_gas_booster)",
    )
    ap.add_argument("--booster-drive", choices=["gas", "electric"], default=None)
    ap.add_argument(
        "--plant-pmin",
        action="append",
        default=[],
        metavar="GEN=BAR",
        help="override a plant's minimum fuel-gas pressure [bar abs]",
    )
    ap.add_argument(
        "--derate",
        action="store_true",
        help="pressure derate up to derate_nominal_factor * min_pressure",
    )
    ap.add_argument(
        "--no-plant-pressure",
        action="store_true",
        help="ignore the plants' minimum fuel-gas pressure",
    )
    add_source_version_argument(ap, SOURCE_FORMAT)
    return ap


def options_from_args(args: argparse.Namespace) -> BuildOptions:
    """Maps parsed CLI arguments to :class:`BuildOptions`."""
    pmin = {k: float(v) for k, v in (x.split("=", 1) for x in args.plant_pmin)}
    return BuildOptions(
        season=args.season,
        compressor=args.compressor,
        linepack=args.linepack,
        segments=args.segments,
        nblocks=args.nblocks,
        fuel_model=args.fuel_model,
        cs_gamma=args.cs_gamma,
        booster=args.booster,
        booster_drive=args.booster_drive,
        plant_pmin=pmin,
        derate=args.derate,
        plant_pressure=not args.no_plant_pressure,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    args = make_parser().parse_args(argv)
    desc = json.loads(args.gasnet.read_text(encoding="utf-8"))
    opts = options_from_args(args)
    build = build_legacy if args.legacy else build_native
    planning, meta = build(desc, opts)
    apply_source_version(
        SOURCE_FORMAT,
        detect_version(desc),
        args.source_version,
        provenance=meta,
        log=logger,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(planning, indent=1), encoding="utf-8")
    args.out.with_suffix(".meta.json").write_text(
        json.dumps(meta, indent=1), encoding="utf-8"
    )
    sysd = planning["system"]
    counts = ", ".join(
        f"{len(v)} {k.removesuffix('_array')}"
        for k, v in sysd.items()
        if k.endswith("_array") and k.startswith(("gas_", "decision", "user_"))
    )
    print(f"wrote {args.out}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
