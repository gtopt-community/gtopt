# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI of the CUC-data -> gtopt converter.

Usage::

    cucdata2gtopt --system 39 --reserve 10 -o out/      # IUC, 10 % reserve
    cucdata2gtopt --system 118 --reserve 2.5 --scale 10 -o out/
    cucdata2gtopt --fetch-only                          # just fill the cache

The workbook is read from ``--input`` when given, else from the download
cache (``--cache-dir``, ``$GTOPT_CUCDATA_DIR`` or
``~/.cache/gtopt/cucdata``), downloading it from the pinned CUC-data
commit when missing (``--offline`` / ``GTOPT_OFFLINE=1`` forbid that).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from cucdata2gtopt._converter import (
    FORMULATIONS,
    MODES,
    CaseOptions,
    build_case,
    default_reserve_pct,
    published_scale,
    write_case,
)
from cucdata2gtopt._reader import read_workbook
from cucdata2gtopt._source import (
    SYSTEM_FILES,
    UPSTREAM_COMMIT,
    UPSTREAM_URL,
    DataUnavailableError,
    fetch_all,
    system_workbook,
)

logger = logging.getLogger("cucdata2gtopt")


def make_parser() -> argparse.ArgumentParser:
    """Argument parser of the ``cucdata2gtopt`` console script."""
    p = argparse.ArgumentParser(
        prog="cucdata2gtopt",
        description=(
            "Convert the clustered-UC benchmark data of Morales-Espana & "
            "Tejada-Arango (IEEE TPWRS 2019; "
            f"{UPSTREAM_URL} @ {UPSTREAM_COMMIT[:10]}) into the gtopt "
            "individual-unit UC case."
        ),
    )
    p.add_argument(
        "--system",
        type=int,
        choices=sorted(SYSTEM_FILES),
        default=39,
        help="test system: 39 (90 units) or 118 (540 units) (default: 39)",
    )
    p.add_argument(
        "--reserve",
        type=float,
        metavar="PCT",
        default=None,
        help=(
            "up and down reserve requirement as %% of the hourly demand "
            "(default: the workbook's: 10 for 39-bus, 2.5 for 118-bus)"
        ),
    )
    p.add_argument(
        "--scale",
        type=int,
        metavar="N",
        default=None,
        help=(
            "identical units per cluster; demand, reserve, wind and line "
            "limits scale by N/10 (default: 10 = the published, paper data)"
        ),
    )
    p.add_argument(
        "--formulation",
        choices=FORMULATIONS,
        default="paper",
        help=(
            "paper: GAMS startup/shutdown capability and reserve-coupled "
            "ramps as user constraints (default); native: gtopt Commitment "
            "ramp fields only (reserve not in the ramps)"
        ),
    )
    p.add_argument(
        "--mode",
        choices=MODES,
        default="chronological",
        help=(
            "chronological: full IUC on one chronological stage (default); "
            "block: same hours as non-chronological blocks (per-block "
            "commitment, no intertemporal constraints or start costs)"
        ),
    )
    p.add_argument(
        "--name", default=None, help="case name (default: cuc<sys>_r<pct>_s<N>)"
    )
    p.add_argument(
        "-o", "--output-dir", type=Path, default=Path("."), help="output directory"
    )
    p.add_argument(
        "--input", type=Path, default=None, help="workbook to read instead of the cache"
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="download cache (default: $GTOPT_CUCDATA_DIR or ~/.cache/gtopt/cucdata)",
    )
    p.add_argument(
        "--fetch-only",
        action="store_true",
        help="download every CUC-data file into the cache and exit",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    args = make_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.fetch_only:
            for path in fetch_all(args.cache_dir):
                print(path)
            return 0
        workbook = args.input or system_workbook(args.system, args.cache_dir)
    except DataUnavailableError as exc:
        logger.error("%s", exc)
        return 2
    data = read_workbook(workbook)
    opts = CaseOptions(
        system=args.system,
        reserve_pct=args.reserve,
        scale=args.scale,
        formulation=args.formulation,
        mode=args.mode,
        name=args.name,
    )
    case = build_case(data, opts)
    path = write_case(case, args.output_dir)
    system = case["system"]
    logger.info(
        "%s: %d buses, %d lines, %d units (%d per cluster published), "
        "reserve %.6g %% (workbook %.6g %%), %d user constraints",
        path,
        len(system["bus_array"]),
        len(system["line_array"]),
        len(system["commitment_array"]),
        published_scale(data),
        args.reserve if args.reserve is not None else default_reserve_pct(data),
        default_reserve_pct(data),
        len(system.get("user_constraint_array", [])),
    )
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
