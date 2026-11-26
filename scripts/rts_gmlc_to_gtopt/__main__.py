"""CLI entry point for the RTS-GMLC → gtopt converter.

Usage:

    python -m rts_gmlc_to_gtopt --day 1 -o rts_gmlc_day1.json
"""

from __future__ import annotations

import argparse
import logging
import sys
import urllib.request
from pathlib import Path

from gtopt_shared.net import require_http_url
from gtopt_shared.source_versions import (
    add_source_version_argument,
    apply_source_version,
)

from rts_gmlc_to_gtopt._converter import (
    LOAD_SERIES,
    convert,
    parse_reserves,
    reserve_series_file,
    to_gtopt_json,
    write_json,
)
from rts_gmlc_to_gtopt.source_version import (
    SOURCE_FORMAT,
    detect_version,
    release_tag,
    write_marker,
)

logger = logging.getLogger("rts_gmlc_to_gtopt")

RTS_DATA_TEMPLATE = "https://raw.githubusercontent.com/GridMod/RTS-GMLC/{ref}/RTS_Data"
RTS_DATA_BASE = RTS_DATA_TEMPLATE.format(ref=release_tag(SOURCE_FORMAT.default))
GITHUB_RAW_BASE = f"{RTS_DATA_BASE}/SourceData"
TIMESERIES_BASE = f"{RTS_DATA_BASE}/timeseries_data_files"
CACHE_FILES = ("gen.csv", "bus.csv", "branch.csv", "reserves.csv")


def _bases(version: str | None) -> tuple[str, str]:
    """(SourceData, timeseries) URL bases of RTS-GMLC release *version*."""
    if version is None:
        return GITHUB_RAW_BASE, TIMESERIES_BASE
    base = RTS_DATA_TEMPLATE.format(ref=release_tag(version))
    return f"{base}/SourceData", f"{base}/timeseries_data_files"


def _download(url: str, local: Path) -> None:
    if local.exists() and local.stat().st_size > 0:
        return
    checked = require_http_url(url)
    with urllib.request.urlopen(checked) as resp, local.open("wb") as out:  # noqa: S310
        out.write(resp.read())


def timeseries_files(
    cache_dir: Path, version: str | None = None
) -> list[tuple[str, str]]:
    """(url, cache name) of the day-ahead load and reserve series.

    Needs ``reserves.csv`` in the cache for the product names.
    """
    ts_base = _bases(version)[1]
    files = [(f"{ts_base}/Load/{LOAD_SERIES}", LOAD_SERIES)]
    for product in parse_reserves(cache_dir / "reserves.csv"):
        name = reserve_series_file(product.name)
        files.append((f"{ts_base}/Reserves/{name}", name))
    return files


def populate_cache(
    cache_dir: Path, timeseries: bool = False, version: str | None = None
) -> None:
    """Download the missing source CSVs of release *version* (default pin).

    A cache that held none of :data:`CACHE_FILES` before is stamped with
    the release (:func:`rts_gmlc_to_gtopt.source_version.write_marker`).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    fresh = not any((cache_dir / name).exists() for name in CACHE_FILES)
    src_base = _bases(version)[0]
    for name in CACHE_FILES:
        _download(f"{src_base}/{name}", cache_dir / name)
    if fresh:
        write_marker(cache_dir, version or SOURCE_FORMAT.default)
    if timeseries:
        for url, name in timeseries_files(cache_dir, version):
            _download(url, cache_dir / name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rts_gmlc_to_gtopt",
        description=(
            "Convert the RTS-GMLC (GridMod 2019) test system to a GTOpt JSON "
            "single-day dispatch fixture with per-generator multi-pollutant "
            "emission accounting."
        ),
    )
    parser.add_argument(
        "--day",
        type=int,
        default=1,
        help="Day index: scales the synthetic load shape, or the day of "
        "year (1-366, 2020) of the --timeseries series.",
    )
    parser.add_argument(
        "--pollutants",
        type=str,
        default="co2",
        help="Comma-separated pollutant tags to track (co2,so2,nox,...).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("rts_gmlc.json"),
        help="Output GTOpt JSON path.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / ".cache" / "gtopt" / "rts_gmlc",
        help="Directory holding cached RTS-GMLC source CSVs.",
    )
    parser.add_argument(
        "--timeseries",
        action="store_true",
        help="Use the day-ahead regional load (and reserve requirement) "
        "series of day-of-year --day instead of the synthetic load shape.",
    )
    parser.add_argument(
        "--reserves",
        action="store_true",
        help="Add reserves.csv products as ReserveZones with one provision "
        "per eligible generator (min(pmax, ramp x timeframe)).",
    )
    parser.add_argument(
        "--inertia",
        action="store_true",
        help="Add an InertiaZone and InertiaProvisions from Inertia MJ/MW "
        "and Base MVA.",
    )
    parser.add_argument(
        "--inertia-requirement",
        type=float,
        default=0.0,
        help="System inertia requirement [MWs] (with --inertia).",
    )
    parser.add_argument(
        "--shortfall-cost",
        type=float,
        default=1000.0,
        help="Reserve / inertia shortfall price [$/MW(s)].",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Skip the network round-trip; require the cache to be primed.",
    )
    add_source_version_argument(parser, SOURCE_FORMAT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    detected = detect_version(args.cache_dir)
    version = apply_source_version(
        SOURCE_FORMAT, detected, args.source_version, log=logger
    )
    if not args.no_download:
        populate_cache(args.cache_dir, timeseries=args.timeseries, version=version)

    pollutants = tuple(p.strip() for p in args.pollutants.split(",") if p.strip())
    conversion = convert(
        args.cache_dir,
        day=args.day,
        timeseries=args.timeseries,
        reserves=args.reserves,
    )
    payload = to_gtopt_json(
        conversion,
        include_pollutants=pollutants,
        inertia=args.inertia,
        inertia_requirement_mws=args.inertia_requirement,
        shortfall_cost=args.shortfall_cost,
    )
    write_json(args.output, payload)

    print(
        f"RTS-GMLC {version} day {args.day}: {conversion.gen_count} gens / "
        f"{conversion.bus_count} buses / {conversion.line_count} lines / "
        f"{conversion.n_hours} h.  Peak load = "
        f"{max(conversion.total_load_mw):.0f} MW.  "
        f"pollutants = {pollutants}.  load = {conversion.load_source}, "
        f"reserves = {len(conversion.reserves)}.  Written: {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
