"""CLI entry point for the Sienna → gtopt converter.

Usage::

    python -m sienna2gtopt cascading_hydro    -o cascading.json
    python -m sienna2gtopt monitored_line     -o mline.json
    python -m sienna2gtopt hvdc               -o hvdc.json
    python -m sienna2gtopt pumped_storage     -o phes.json
    python -m sienna2gtopt battery_degeneracy --subcase b -o batt_b.json
    python -m sienna2gtopt interruptible_load -o il.json
    python -m sienna2gtopt fuel_cost_ts       -o fuel_ts.json
    python -m sienna2gtopt wecc_240           -o wecc240.json
    python -m sienna2gtopt reserves --golden tools/sienna_goldens/reserves_uc.json \
        [--ordc auto|stacked|omit] -o reserves_uc.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from gtopt_shared.source_versions import (
    add_source_version_argument,
    apply_source_version,
)
from sienna2gtopt import VARIANTS
from sienna2gtopt._battery_degeneracy import VALID_SUBCASES
from sienna2gtopt._bundle import (
    CSV_BUNDLES,
    MANIFEST,
    BundleUnavailableError,
    extract_bundle,
)
from sienna2gtopt._reader import load_case
from sienna2gtopt._reserves import ORDC_MODES
from sienna2gtopt.source_version import SOURCE_FORMAT, detect_version

logger = logging.getLogger("sienna2gtopt")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sienna2gtopt",
        description=(
            "Port a NREL-Sienna PowerSimulations.jl test case to a gtopt planning JSON."
        ),
    )
    parser.add_argument(
        "variant",
        choices=sorted(VARIANTS.keys()),
        help="Which variant builder to invoke.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output JSON path (default: <variant>.json in cwd).",
    )
    parser.add_argument(
        "--bundle",
        default="5bus",
        choices=sorted(MANIFEST),
        help=(
            "Which Sienna data bundle to use (default: %(default)s).  It is "
            "downloaded from the pinned upstream commit on first use and "
            "cached; set GTOPT_OFFLINE=1 to forbid downloads.  Only "
            f"{', '.join(CSV_BUNDLES)} carries the CSV tables the variants "
            "read; the other bundles hold PowerSystems.jl data only."
        ),
    )
    parser.add_argument(
        "--subcase",
        choices=list(VALID_SUBCASES),
        default="b",
        help=(
            "battery_degeneracy subcase selector "
            "(b/c/d/e/f) — only consumed by the battery_degeneracy variant."
        ),
    )
    add_source_version_argument(parser, SOURCE_FORMAT)
    parser.add_argument(
        "--golden",
        type=Path,
        default=None,
        help=(
            "reserves only: a tools/sienna_solve_golden.jl reserve golden "
            "(its system_data block is the input; no CSV bundle is read)."
        ),
    )
    parser.add_argument(
        "--ordc",
        choices=list(ORDC_MODES),
        default="auto",
        help=(
            "reserves only: ORDC handling — stacked priced zones, omitted, "
            "or auto (stacked when the golden modelled the ORDC)."
        ),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    builder = VARIANTS[args.variant]
    if args.variant == "reserves":
        if args.golden is None:
            parser.error("the reserves variant needs --golden")
        golden = json.loads(args.golden.read_text())
        payload = builder(golden, ordc=args.ordc)
        return _write(
            payload,
            args.output or Path(f"{golden.get('slug', 'reserves')}.json"),
            args.variant,
        )

    if args.bundle not in CSV_BUNDLES:
        files = ", ".join(Path(path).name for path, _ in MANIFEST[args.bundle])
        parser.error(
            f"--bundle {args.bundle} holds only PowerSystems.jl data ({files}), "
            f"not the CSV tables the {args.variant} variant reads; use "
            f"--bundle {' or '.join(CSV_BUNDLES)}"
        )
    try:
        case_dir = extract_bundle(args.bundle)
    except BundleUnavailableError as exc:
        print(f"sienna2gtopt: error: {exc}", file=sys.stderr)
        return 2
    apply_source_version(
        SOURCE_FORMAT,
        detect_version(case_dir, args.bundle),
        args.source_version,
        log=logger,
    )
    case = load_case(case_dir)
    if args.variant == "battery_degeneracy":
        payload = builder(case, subcase=args.subcase)
        default_name = f"{args.variant}_{args.subcase}.json"
    else:
        payload = builder(case)
        default_name = f"{args.variant}.json"

    return _write(payload, args.output or Path(default_name), args.variant)


def _write(payload: dict, output: Path, variant: str) -> int:
    """Write ``payload`` to ``output`` and print the element summary."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as fh:
        json.dump(payload, fh, indent=2)

    sys_block = payload.get("system", {})
    print(
        f"Sienna {variant}: "
        f"buses={len(sys_block.get('bus_array', []))} "
        f"gens={len(sys_block.get('generator_array', []))} "
        f"lines={len(sys_block.get('line_array', []))} "
        f"junctions={len(sys_block.get('junction_array', []))} "
        f"waterways={len(sys_block.get('waterway_array', []))} "
        f"reservoirs={len(sys_block.get('reservoir_array', []))} "
        f"turbines={len(sys_block.get('turbine_array', []))} "
        f"batteries={len(sys_block.get('battery_array', []))} "
        f"fuels={len(sys_block.get('fuel_array', []))} "
        f"commitments={len(sys_block.get('commitment_array', []))} "
        f"reserve_zones={len(sys_block.get('reserve_zone_array', []))} "
        f"reserve_provisions={len(sys_block.get('reserve_provision_array', []))} "
        f"=> {output}"
    )
    return 0


def legacy_main(argv: list[str] | None = None) -> int:
    """Deprecated ``sienna_to_gtopt`` console script (renamed in 1.2.0).

    Runs :func:`main` unchanged after a one-line deprecation note on
    stderr; the alias will be removed in a future release.
    """
    print(
        "sienna_to_gtopt is deprecated and will be removed in a future "
        "release; use sienna2gtopt instead.",
        file=sys.stderr,
    )
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())
