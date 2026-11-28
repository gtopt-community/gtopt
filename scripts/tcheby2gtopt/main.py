# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Command line: ``python -m tcheby2gtopt``.

Convert one tcheby system DB::

    python -m tcheby2gtopt convert SYSTEM.db [--simu SIMU.db] \\
        --name caso1emb --out-dir DIR [--hours 24] [--nodes 24]

writes ``DIR/<name>_block.json`` and ``DIR/<name>_point.json``.

Rebuild the whole ``cases/cheby_small`` library::

    python -m tcheby2gtopt library --src TCHEBY/tests/test_dbs \\
        --out ../cases/cheby_small
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from gtopt_shared.point_sampling import (
    parse_commitment_breaks,
    read_commitment_breaks_file,
)

from .convert import ConvertOptions, convert
from .library import build_library, write_json
from .tcheby_db import parse_date, read_simulation, read_system
from .timegrid import TimeGrid


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="tcheby2gtopt",
        description="tcheby SQLite test systems -> gtopt JSON "
        "(hourly block and Chebyshev point-stage representations)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    cv = sub.add_parser("convert", help="convert one system DB")
    cv.add_argument("system_db", type=Path)
    cv.add_argument("--simu", type=Path, help="simulation DB (stages, inflows)")
    cv.add_argument("--name", required=True)
    cv.add_argument("--out-dir", type=Path, required=True)
    cv.add_argument(
        "--hours",
        type=float,
        nargs="+",
        default=[24.0],
        help="stage lengths [h] when there is no simulation DB",
    )
    cv.add_argument("--nodes", type=int, default=24, help="Lobatto N per point stage")
    cv.add_argument(
        "--time-zero",
        default="2016-01-01",
        help="time zero for date-keyed schedules without a simulation DB",
    )
    cv.add_argument("--start-hour", type=float, default=0.0)
    cv.add_argument("--point-values", choices=("wcell", "interp"), default="wcell")
    cv.add_argument("--drop-hydro-pmin", action="store_true")
    cv.add_argument(
        "--commitment-period-rule",
        choices=("class", "none"),
        default="class",
        help="point stages: per-unit commitment grids (class, default) or "
        "none (no commitment_period, source UC resolution dropped)",
    )
    cv.add_argument(
        "--point-storage-integration",
        default="cc",
        help="storage_integration of batteries / reservoirs on point "
        "stages (default cc = Clenshaw-Curtis, the sparsest; lg4_pos / "
        "trapezoid add envelope rows; none = gtopt's per-type defaults)",
    )
    cv.add_argument(
        "--commitment-period",
        action="append",
        default=[],
        metavar="NAME=H",
        help="point stages: commitment grid [h] of unit NAME (repeatable); "
        "default by class: min(MUT, MDT) >= 8 h -> 4 h, >= 4 h -> 3 h, else 1 h",
    )
    cv.add_argument(
        "--commitment-breaks",
        type=parse_commitment_breaks,
        default=None,
        metavar="H1,H2,...",
        help="daily commitment grid: cell boundaries in hours of the day "
        "(e.g. 3.5,7.5,10.5,14.5,17.5,23.5, or 'study' for that pattern); "
        "u on these cells, v / w at their boundaries (model_options."
        "commitment_breaks, replaces the per-unit class grids)",
    )
    cv.add_argument(
        "--commitment-breaks-file",
        type=Path,
        default=None,
        metavar="CSV",
        help="per-month daily grids (rows 'month,breaks', month 1..12 or "
        "jan..dec) written as Stage.commitment_breaks; months missing from "
        "the file use --commitment-breaks",
    )
    cv.add_argument("--pmin", choices=("hard", "drop"), default="hard")
    cv.add_argument(
        "--line-losses", action="store_true", help="emit line resistance (losses)"
    )

    lib = sub.add_parser("library", help="rebuild cases/cheby_small")
    lib.add_argument("--src", type=Path, help="tcheby tests/test_dbs")
    lib.add_argument("--out", type=Path, required=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "library":
        for p in build_library(args.src, args.out):
            print(p)
        return 0

    sim = read_simulation(args.simu) if args.simu else None
    tz: datetime = sim.time_zero if sim else parse_date(args.time_zero)
    sysm = read_system(args.system_db, tz, name=args.name)
    hours = sim.stages if sim else list(args.hours)
    opts = ConvertOptions(
        start_hour=args.start_hour,
        drop_hydro_pmin=args.drop_hydro_pmin,
        pmin_mode=args.pmin,
        line_losses=args.line_losses,
        commitment_period_rule=args.commitment_period_rule,
        point_storage_integration=args.point_storage_integration,
        commitment_periods={
            k: float(v)
            for k, _, v in (s.partition("=") for s in args.commitment_period)
        },
        commitment_breaks=args.commitment_breaks,
        commitment_breaks_by_month=(
            read_commitment_breaks_file(args.commitment_breaks_file)
            if args.commitment_breaks_file
            else {}
        ),
        time_zero=tz,
    )
    for mode in ("block", "point"):
        grid = TimeGrid(mode, hours, nodes=args.nodes, point_values=args.point_values)
        conv = convert(sysm, grid, sim, opts)
        path = args.out_dir / f"{args.name}_{mode}.json"
        write_json(path, conv.planning)
        print(path)
    for note in conv.notes:
        print(f"note: {note}")
    return 0
