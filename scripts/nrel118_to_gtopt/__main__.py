# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI entry point for the NREL-118 → gtopt converter.

Usage::

    nrel118_to_gtopt -o nrel118/nrel118.json               # whole year
    nrel118_to_gtopt --hours 168+168 -o week2/nrel118.json  # one week
    nrel118_to_gtopt --from-state nrel118/nrel118/nrel118_to_gtopt_state.json

If the NREL-118 source CSVs are absent from ``--cache-dir``
(``~/.cache/gtopt/nrel118/``) the converter downloads them from the
NREL-Sienna ``PowerSystemsTestData`` GitHub repo at a pinned commit
(one-time, ~26 MB).  The data is never vendored in gtopt (the upstream
repo has no licence file).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

from gtopt_shared.source_versions import (
    add_source_version_argument,
    apply_source_version,
)
from gtopt_shared.time_grid import parse_hour_range
from nrel118_to_gtopt._converter import (
    COMMITMENT_MODES,
    FUEL_PRICE_TABLES,
    YEAR_HOURS,
    CaseOptions,
    build_case,
    convert,
    write_json,
)
from nrel118_to_gtopt._data import (
    CACHE_FILES,
    DEFAULT_CACHE_DIR,
    DEFAULT_REF,
    GITHUB_RAW_BASE,
    missing_files,
    populate_cache,
)

__all__ = [
    "CACHE_FILES",
    "GITHUB_RAW_BASE",
    "main",
    "make_parser",
    "populate_cache",
]

from nrel118_to_gtopt.source_version import SOURCE_FORMAT, detect_version

logger = logging.getLogger("nrel118_to_gtopt")

STATE_FILE = "nrel118_to_gtopt_state.json"
# argparse dests holding file paths: stored relative to the state file
# and resolved against it on ``--from-state`` reload.
_PATH_ARGS = ("output", "cache_dir")


def make_parser() -> argparse.ArgumentParser:
    """Build the ``nrel118_to_gtopt`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="nrel118_to_gtopt",
        description=(
            "Convert the NREL-118 (Pena, Martinez-Anido, Hodge 2018) test "
            "system to a full-network, multi-block, multi-stage gtopt case: "
            "118 buses / 186 lines (DC OPF), every generator at its bus, "
            "regional hourly load distributed by bus participation factors "
            "(Demand/lmax.parquet), hourly wind/solar/hydro availability "
            "(GeneratorProfile/profile.parquet) and IPCC AR6 CO2 factors."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("nrel118.json"),
        help="output gtopt JSON; schedules go to <dir>/<stem>/ (default: nrel118.json)",
    )
    time = parser.add_argument_group("time structure")
    time.add_argument(
        "--hours",
        default=None,
        metavar="RANGE",
        help="hour window of the 8784-h year: 'A:B' (hours A..B-1) or 'A+N' "
        "(default: the whole year)",
    )
    time.add_argument(
        "--week",
        type=int,
        default=None,
        help="legacy shortcut for --hours (N-1)*168+168 (week N, 1-based)",
    )
    time.add_argument(
        "--block-hours",
        type=int,
        default=1,
        metavar="N",
        help="hours averaged into one block (default: 1)",
    )
    time.add_argument(
        "--stage-hours",
        type=int,
        default=24,
        metavar="H",
        help="hours per stage; 24 = daily stages, 0 = one stage for the whole "
        "window (default: 24)",
    )
    time.add_argument(
        "--single-phase",
        action="store_true",
        help="one phase for all stages (default: one phase per stage)",
    )
    model = parser.add_argument_group("model")
    model.add_argument(
        "--single-bus",
        action="store_true",
        help="copper-plate: set use_single_bus (network data kept in the JSON)",
    )
    model.add_argument(
        "--commitment",
        choices=COMMITMENT_MODES,
        default="none",
        help="unit commitment for thermals with a min stable level: none "
        "(LP economic dispatch, pmin = 0), relaxed (LP-relaxed UC with "
        "ramps, min up/down, start costs) or mip (default: none)",
    )
    model.add_argument(
        "--fuel-prices",
        choices=sorted(FUEL_PRICE_TABLES),
        default="sienna",
        help="fuel price table for the thermal SRMC: sienna (NREL-Sienna "
        "loader: NG 5.4, coal 1.8, oil 21, biomass 2.4 $/MMBtu) or typical "
        "(EIA-style US prices) (default: sienna)",
    )
    model.add_argument(
        "--renewables-share",
        type=float,
        default=None,
        metavar="S",
        help="rescale every wind + solar unit so their available energy "
        "equals S x load energy over the window (0 removes them; default: "
        "dataset as published)",
    )
    model.add_argument(
        "--demand-fail-cost",
        type=float,
        default=1000.0,
        help="value of lost load [$/MWh] (default: 1000)",
    )
    model.add_argument(
        "--line-losses-mode",
        default="none",
        help="model_options.line_losses_mode (default: none)",
    )
    model.add_argument(
        "--method",
        choices=("monolithic", "sddp", "cascade"),
        default="monolithic",
        help="options.method (default: monolithic)",
    )
    model.add_argument(
        "--output-format",
        choices=("csv", "parquet"),
        default=None,
        help="gtopt solution output format (default: gtopt's own)",
    )
    data = parser.add_argument_group("source data")
    data.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="directory holding the NREL-118 CSVs: flat cache, a copy of "
        "118-Bus/ or a PowerSystemsTestData checkout (default: %(default)s)",
    )
    data.add_argument(
        "--ref",
        default=DEFAULT_REF,
        help="PowerSystemsTestData git ref to download from (default: the "
        "pinned, validated commit)",
    )
    data.add_argument(
        "--no-download",
        action="store_true",
        help="skip the network round-trip; require the cache to be primed",
    )
    add_source_version_argument(data, SOURCE_FORMAT)
    parser.add_argument(
        "--from-state",
        dest="from_state",
        type=Path,
        metavar="PATH",
        default=None,
        help="replay a nrel118_to_gtopt_state.json snapshot; flags given "
        "alongside override the stored values",
    )
    return parser


def _apply_from_state(
    parser: argparse.ArgumentParser, args: argparse.Namespace, argv: list[str]
) -> argparse.Namespace:
    """Rebuild *args* from a ``--from-state`` snapshot (CLI flags win)."""
    from gtopt_shared.state_snapshot import (  # noqa: PLC0415  pylint: disable=import-outside-toplevel
        apply_state_to_args,
        load_state_snapshot,
    )

    state_path = Path(args.from_state)
    try:
        payload = load_state_snapshot(state_path)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(f"--from-state: {exc}")
    state_args = dict(payload["args"])
    base = state_path.resolve().parent
    for key in _PATH_ARGS:
        val = state_args.get(key)
        if isinstance(val, str) and not os.path.isabs(val):
            state_args[key] = os.path.normpath(base / val)
    state_args["from_state"] = None
    return apply_state_to_args(parser, state_args, argv)


def _write_state(
    args: argparse.Namespace,
    state_dir: Path,
    summary: Any,
    provenance: dict[str, Any] | None = None,
) -> None:
    """Write the state snapshot + README with paths relative to *state_dir*.

    *provenance* keys (the ``source_format`` record) are merged into the
    snapshot's ``extra`` next to ``summary``.
    """
    from gtopt_shared.state_snapshot import (  # noqa: PLC0415  pylint: disable=import-outside-toplevel
        write_nrel118_to_gtopt_readme,
        write_state_snapshot,
    )

    snap = argparse.Namespace(**vars(args))
    snap.from_state = None
    for key in _PATH_ARGS:
        val = getattr(snap, key, None)
        if val is not None:
            setattr(
                snap, key, os.path.relpath(Path(val).resolve(), state_dir.resolve())
            )
    try:
        write_state_snapshot(
            output_dir=state_dir,
            tool_name="nrel118_to_gtopt",
            args=snap,
            extra={"summary": summary, **(provenance or {})},
        )
        write_nrel118_to_gtopt_readme(state_dir)
    except (OSError, ValueError) as exc:
        logger.warning("failed to write state snapshot: %s", exc)


def _hour_window(args: argparse.Namespace) -> tuple[int, int]:
    if args.week is not None:
        if args.hours is not None:
            raise ValueError("--week and --hours are mutually exclusive")
        if args.week < 1:
            raise ValueError("--week must be >= 1")
        return (args.week - 1) * 168, args.week * 168
    return parse_hour_range(args.hours, YEAR_HOURS)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the conversion.

    Returns 0 on success, 1 on a conversion error, 2 when the source
    data is unavailable (cache empty and download disabled / failed).
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = make_parser()
    cli_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(cli_argv)
    if args.from_state is not None:
        args = _apply_from_state(parser, args, cli_argv)

    cache_dir = Path(args.cache_dir)
    if not args.no_download:
        try:
            populate_cache(cache_dir, ref=args.ref)
        except OSError as exc:
            logger.error("cannot download NREL-118 data: %s", exc)
            return 2
    missing = missing_files(cache_dir)
    if missing:
        logger.error("NREL-118 cache %s lacks %s", cache_dir, ", ".join(missing))
        return 2
    provenance: dict[str, Any] = {}
    apply_source_version(
        SOURCE_FORMAT,
        detect_version(cache_dir),
        args.source_version,
        provenance=provenance,
        log=logger,
    )

    output = Path(args.output)
    try:
        hours = _hour_window(args)
        conversion = convert(cache_dir, hours, renewables_share=args.renewables_share)
        opts = CaseOptions(
            block_hours=args.block_hours,
            stage_hours=args.stage_hours if args.stage_hours > 0 else None,
            single_bus=args.single_bus,
            single_phase=args.single_phase,
            commitment=args.commitment,
            demand_fail_cost=args.demand_fail_cost,
            line_losses_mode=args.line_losses_mode,
            method=args.method,
            fuel_prices=args.fuel_prices,
            output_format=args.output_format,
        )
        planning, summary = build_case(conversion, output, opts)
    except (ValueError, OSError) as exc:
        logger.error("%s", exc)
        return 1
    write_json(output, planning)
    _write_state(args, output.parent / output.stem, summary, provenance)

    for note in summary["notes"]:
        logger.warning("%s", note)
    print(
        f"NREL-118 hours [{hours[0]}, {hours[1]}): {summary['buses']} buses / "
        f"{summary['lines']} lines / {summary['generators']} gens "
        f"({summary['profiled_generators']} profiled) / {summary['demands']} "
        f"demands; {summary['blocks']} blocks x {summary['block_hours']} h in "
        f"{summary['stages']} stage(s).  Load {summary['load_energy_mwh']:.0f} "
        f"MWh, peak {summary['peak_load_mw']:.0f} MW.  Written: {output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
