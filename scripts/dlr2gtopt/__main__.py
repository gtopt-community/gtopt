# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``dlr2gtopt`` -- dynamic line rating inputs for gtopt.

Pipeline (each step a subcommand, each reading the previous one's file)::

    dlr2gtopt towers   --source cen --section-id 1352 --line LVLP -o towers.csv
    dlr2gtopt spans    towers.csv -o spans.csv
    dlr2gtopt weather  --spans spans.csv --source explorador-eolico -o weather.parquet
    dlr2gtopt ampacity --spans spans.csv --weather weather.parquet \\
                       --conductor flint --tmax 55 -o ampacity.parquet
    dlr2gtopt critical-spans ampacity.parquet --target-rho 0.9 -o critical.json
    dlr2gtopt emit     --spans spans.csv --weather weather.parquet \\
                       --critical critical.json --conductor flint --tmax 55 \\
                       --voltage 220 --block-hours 1 --mode weather -o case/
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from dlr2gtopt import _critical as crit
from dlr2gtopt import _emit as em
from dlr2gtopt import _lineparams as lp
from dlr2gtopt import _towers as tw
from dlr2gtopt import _weather as wx
from dlr2gtopt._ieee738 import CONDUCTORS, SOLAR_MODES, Conductor, conductor_from_args
from dlr2gtopt._spans import (
    Span,
    build_spans,
    read_spans,
    read_towers,
    write_spans,
    write_towers,
)

logger = logging.getLogger("dlr2gtopt")

WEATHER_SOURCES = ("explorador-eolico", "open-meteo", "ecmwf-opendata", "csv", "era5")
DEFAULT_CACHE = Path.home() / ".cache" / "gtopt" / "dlr2gtopt"


def _cache(args: argparse.Namespace) -> tw.PoliteCache:
    return tw.PoliteCache(Path(args.cache_dir), pause=args.pause)


def _add_common_net(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE),
        help="download cache (never commit it) [%(default)s]",
    )
    p.add_argument(
        "--pause",
        type=float,
        default=3.0,
        help="seconds between requests to a server [%(default)s]",
    )


def _add_conductor(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("conductor (IEEE 738)")
    g.add_argument("--conductor", choices=sorted(CONDUCTORS), help="named conductor")
    for name, hlp in (
        ("diameter", "outside diameter [m]"),
        ("r20", "DC resistance at 20 C [ohm/m]"),
        ("alpha-r", "temperature coefficient of r20 [1/K]"),
        ("ac-factor", "AC/DC resistance ratio"),
        ("r25", "AC resistance at 25 C [ohm/m]"),
        ("r75", "AC resistance at 75 C [ohm/m]"),
        ("emissivity", "emissivity"),
        ("absorptivity", "solar absorptivity"),
    ):
        g.add_argument(f"--{name}", type=float, help=hlp)
    g.add_argument("--subconductors", type=int, help="conductors per phase")
    g.add_argument(
        "--tmax",
        type=float,
        help="max conductor temperature [C] (default: --line-params, else 75)",
    )
    g.add_argument(
        "--line-params",
        help="line-params JSON (CEN sheet + per-kV defaults); explicit "
        "conductor options override it",
    )
    g.add_argument(
        "--solar-mode",
        choices=SOLAR_MODES,
        default="ghi",
        help="ghi: q_s = alpha*GHI*D (as gtopt); none: q_s = 0",
    )
    g.add_argument(
        "--low-wind-speed",
        type=float,
        default=0.5,
        help="below it the attack angle is --low-wind-angle (0 = off)",
    )
    g.add_argument("--low-wind-angle", type=float, default=45.0)


def _line_params(args: argparse.Namespace) -> lp.LineParams | None:
    path = getattr(args, "line_params", None)
    return None if path is None else lp.read_line_params(Path(path))


def _conductor(args: argparse.Namespace) -> Conductor:
    own = {
        "diameter": args.diameter,
        "r20": args.r20,
        "alpha_r": args.alpha_r,
        "ac_factor": args.ac_factor,
        "r25": args.r25,
        "r75": args.r75,
        "emissivity": args.emissivity,
        "absorptivity": args.absorptivity,
        "subconductors": args.subconductors,
    }
    line = _line_params(args)
    if args.conductor is None and line is not None:
        base = line.conductor()
        if args.r25 is not None or args.r75 is not None:
            own.update(r20=None, alpha_r=None)
            base = Conductor(
                diameter=base.diameter,
                r25=args.r25,
                r75=args.r75,
                emissivity=base.emissivity,
                absorptivity=base.absorptivity,
                subconductors=base.subconductors,
            )
        return replace(base, **{k: v for k, v in own.items() if v is not None})
    if args.conductor is None and args.diameter is None:
        raise ValueError("give --conductor, --diameter/... or --line-params")
    return conductor_from_args(args.conductor, **own)


def _params(args: argparse.Namespace) -> em.RatingParams:
    line = _line_params(args)
    tmax = args.tmax
    if tmax is None:
        tmax = line.tmax_conductor if line is not None else 75.0
    volt = getattr(args, "voltage", None)
    if volt is None:
        volt = line.voltage_kv if line is not None else 220.0
    return em.RatingParams(
        conductor=_conductor(args),
        tmax_conductor=tmax,
        voltage_kv=volt,
        v_pu=getattr(args, "v_pu", 1.0),
        power_factor=getattr(args, "power_factor", 1.0),
        safety_factor=getattr(args, "safety_factor", 1.0),
        solar_mode=args.solar_mode,
        low_wind_speed=args.low_wind_speed,
        low_wind_angle=args.low_wind_angle,
    )


def cmd_line_params(args: argparse.Namespace) -> int:
    """IEEE 738 line parameters: CEN technical sheet + per-kV defaults."""
    ficha = None
    if args.ficha is not None:
        ficha = lp.parse_ficha_general(
            json.loads(Path(args.ficha).read_text(encoding="utf-8"))
        )
    elif args.section_id is not None:
        client = tw.Infotecnica(_cache(args))
        ficha = lp.parse_ficha_general(client.ficha(args.section_id))
    params = lp.resolve(
        ficha,
        lp.load_defaults(Path(args.line_defaults) if args.line_defaults else None),
        args.kv,
        {
            "tmax_conductor": args.tmax,
            "conductor_height": args.conductor_height,
            "subconductors": args.subconductors,
        },
    )
    Path(args.output).write_text(
        json.dumps(params.to_json(), indent=2) + "\n", encoding="utf-8"
    )
    assumed = params.uses_assumptions()
    print(
        f"{params.voltage_kv:g} kV {params.conductor_type or '?'} x{params.subconductors}, "
        f"D={params.diameter * 1000:.1f} mm, Tmax={params.tmax_conductor:g} C, "
        f"h={params.conductor_height:g} m; assumed: {', '.join(assumed) or 'none'} "
        f"-> {args.output}"
    )
    return 0


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------


def cmd_towers(args: argparse.Namespace) -> int:
    """Tower coordinates of one line."""
    line = args.line
    if args.source == "manual":
        if args.file is None:
            raise SystemExit("towers --source manual needs --file (CSV or KML)")
        path = Path(args.file)
        towers = (
            tw.read_kml(path, line)
            if path.suffix.lower() == ".kml"
            else read_towers(path, line)
        )
    elif args.source == "cen":
        if args.file is not None:
            path = Path(args.file)
        else:
            client = tw.Infotecnica(_cache(args))
            sid = args.section_id
            if sid is None:
                if not args.search:
                    raise SystemExit(
                        "towers --source cen needs --section-id, --search or --file"
                    )
                hits = client.search_sections(args.search)
                for h in hits:
                    print(f"{h['id']:6d}  {h.get('nombre', '')}")
                if len(hits) != 1:
                    print("pick one with --section-id", file=sys.stderr)
                    return 1
                sid = int(hits[0]["id"])
            docs = client.structure_documents(sid)
            if args.doc_id is not None:
                docs = [d for d in docs if int(d["id"]) == args.doc_id]
            if not docs:
                print(f"section {sid}: no structure list annex", file=sys.stderr)
                return 1
            path = client.download(sid, docs[0])
        towers = tw.read_structure_file(path, line, args.circuit, args.utm_zone)
    else:
        bbox = (
            (args.bbox[0], args.bbox[1], args.bbox[2], args.bbox[3])
            if args.bbox
            else None
        )
        q = tw.overpass_query(args.way_id, bbox, args.voltage_regex, args.name_regex)
        data = tw.fetch_osm(_cache(args), q)
        towers = tw.towers_from_osm(data, line, args.towers_only)
        if not tw.osm_tower_traced(towers):
            logger.warning("OSM way does not look tower-traced (vertex spacing)")
        logger.info(tw.OSM_ATTRIBUTION)
    write_towers(Path(args.output), towers)
    print(f"{len(towers)} towers -> {args.output}")
    return 0


def cmd_spans(args: argparse.Namespace) -> int:
    """Spans from towers."""
    spans = build_spans(read_towers(Path(args.towers), args.line), args.min_length)
    write_spans(Path(args.output), spans)
    km = sum(s.length_m for s in spans) / 1000
    print(f"{len(spans)} spans, {km:.2f} km -> {args.output}")
    return 0


def _select_spans(spans: list[Span], critical: str | None) -> list[Span]:
    if critical is None:
        return spans
    data = json.loads(Path(critical).read_text(encoding="utf-8"))
    keys = {(c["line"], int(c["span"])) for c in data["critical_spans"]}
    return [s for s in spans if (s.line, s.span) in keys]


def cmd_weather(args: argparse.Namespace) -> int:
    """Per-span hourly weather."""
    spans = _select_spans(read_spans(Path(args.spans)), args.critical)
    height = args.height
    if height is None:
        line = _line_params(args)
        height = line.conductor_height if line is not None else 15.0
    kw: dict[str, Any] = {"height": height, "profile": args.wind_profile}
    if args.source == "explorador-eolico":
        client = wx.Explorador(_cache(args), args.model or "wrf2010")
        weather = wx.explorador_weather(
            client,
            spans,
            method=args.interp,
            progress=lambda k, n: logger.info("node %d/%d", k, n),
            **kw,
        )
    elif args.source == "open-meteo":
        weather = wx.open_meteo_weather(
            _cache(args),
            spans,
            model=args.model or "best_match",
            forecast_days=args.forecast_days,
            past_days=args.past_days,
            api_key=args.api_key,
            base_url=args.base_url,
            batch=args.batch,
            **kw,
        )
        logger.info(wx.OPEN_METEO_ATTRIBUTION)
    elif args.source == "ecmwf-opendata":
        if args.grib is None:
            raise SystemExit(
                "weather --source ecmwf-opendata needs --grib (see fetch-ecmwf)"
            )
        fields: dict[str, wx.GridField] = {}
        for g in args.grib:
            fields.update(wx.read_grib(Path(g)))
        weather = wx.ecmwf_weather(fields, spans, method=args.interp, **kw)
        logger.info(wx.ECMWF_ATTRIBUTION)
    elif args.source == "csv":
        if args.file is None:
            raise SystemExit("weather --source csv needs --file")
        weather = wx.read_weather(Path(args.file), spans)
    else:
        raise SystemExit(
            "weather --source era5: not implemented yet; download ERA5 from the "
            "Copernicus CDS and pass it as --source csv"
        )
    weather = wx.apply_correction(weather, args.correction)
    weather.write(Path(args.output))
    print(f"{len(weather.times)} h x {len(spans)} spans -> {args.output}")
    return 0


def cmd_fetch_ecmwf(args: argparse.Namespace) -> int:
    """Download the latest ECMWF open-data run into an archive directory."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H")
    target = Path(args.archive) / f"{args.model}_{stamp}.grib2"
    if target.exists():
        print(f"exists: {target}")
        return 0
    steps = list(range(0, args.max_step + 1, args.step))
    wx.ecmwf_fetch(target, model=args.model, source=args.source, steps=steps)
    print(f"-> {target}")
    return 0


def _write_matrix(
    path: Path, times: Sequence[datetime], spans: Sequence[Span], m: np.ndarray
) -> None:
    import pandas as pd

    df = pd.DataFrame(m, columns=[f"{s.line}:{s.span}" for s in spans])
    df.insert(0, "time", pd.to_datetime(list(times)))
    if path.suffix == ".parquet":
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False, float_format="%.2f")


def _read_matrix(
    path: Path,
) -> tuple[list[datetime], list[tuple[str, int]], np.ndarray]:
    import pandas as pd

    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    times = [t.to_pydatetime() for t in pd.to_datetime(df["time"])]
    cols = [c for c in df.columns if c != "time"]
    keys = [(c.rsplit(":", 1)[0], int(c.rsplit(":", 1)[1])) for c in cols]
    return times, keys, df[cols].to_numpy(dtype=float)


def cmd_ampacity(args: argparse.Namespace) -> int:
    """Hourly IEEE 738 ampacity of every span."""
    spans = read_spans(Path(args.spans))
    weather = wx.read_weather(Path(args.weather), spans)
    amps = em.span_ampacity(weather, _params(args))
    _write_matrix(Path(args.output), weather.times, spans, amps)
    print(
        f"ampacity [A]: min {np.nanmin(amps):.0f}, median {np.nanmedian(amps):.0f} "
        f"-> {args.output}"
    )
    return 0


def cmd_critical(args: argparse.Namespace) -> int:
    """Matus 2012 critical-span selection."""
    times, keys, m = _read_matrix(Path(args.ampacity))
    if args.months:
        months = {int(x) for x in args.months.split(",")}
        rows = [i for i, t in enumerate(times) if t.month in months]
        m = m[rows]
    steps = crit.greedy(m, args.target_rho, args.max_spans)
    chosen = [s.span for s in steps]
    res = crit.residual(m, chosen, args.eta_quantile or 0.05)
    out: dict[str, Any] = {
        "method": "Matus et al. 2012, greedy max Pearson correlation",
        "target_rho": args.target_rho,
        "max_spans": args.max_spans,
        "hours": int(m.shape[0]),
        "critical_spans": [
            {"line": keys[s.span][0], "span": keys[s.span][1], "rho": round(s.rho, 4)}
            for s in steps
        ],
        "rho": round(res.rho, 4),
        "bias_a": round(res.bias, 2),
        "mae_a": round(res.mae, 2),
        "p_over": round(res.p_over, 4),
        "ratio_median": round(res.ratio_median, 4),
        "equidistant_needed": crit.equidistant_needed(m, args.target_rho),
    }
    pts = crit.trace(m, args.trace_rho, args.trace_max)
    out["n_needed"] = {
        f"{t:.2f}": crit.n_needed(pts, t) for t in (0.8, 0.85, 0.9, 0.95)
    }
    out["trace"] = [
        {
            "n": p.n,
            "line": keys[p.span][0],
            "span": keys[p.span][1],
            "rho": round(p.rho, 4),
            "mae_a": round(p.mae, 2),
            "bias_a": round(p.bias, 2),
            "p_over": round(p.p_over, 4),
            "rho_equidistant": round(p.rho_equidistant, 4),
        }
        for p in pts
    ]
    if args.plot:
        crit.plot_trace(pts, args.plot, args.max_spans, Path(args.ampacity).stem)
    if args.eta_quantile is not None:
        out["eta"] = round(min(res.ratio_q, 1.0), 4)
    if args.segments:
        n = m.shape[1]
        bounds = np.linspace(0, n, args.segments + 1).astype(int)
        segs = [list(range(a, b)) for a, b in zip(bounds, bounds[1:]) if b > a]
        out["segments"] = [
            {
                "spans": [keys[seg[0]][1], keys[seg[-1]][1]],
                "critical": [keys[c][1] for c in sel],
                "n": len(sel),
                "rho": round(r.rho, 4),
                "bias_a": round(r.bias, 2),
                "mae_a": round(r.mae, 2),
            }
            for seg, (sel, r) in zip(
                segs, crit.segment_errors(m, segs, args.segment_spans, args.target_rho)
            )
        ]
    Path(args.output).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    sel = ", ".join(str(keys[s.span][1]) for s in steps)
    print(f"{len(steps)} critical spans [{sel}] rho={res.rho:.3f} -> {args.output}")
    return 0


def cmd_emit(args: argparse.Namespace) -> int:
    """gtopt Line.dlr JSON + per-block schedules."""
    spans = _select_spans(read_spans(Path(args.spans)), args.critical)
    if not spans:
        raise SystemExit("emit: no spans selected")
    weather = wx.read_weather(Path(args.weather), spans)
    blocks = (
        em.read_blocks(Path(args.blocks))
        if args.blocks
        else em.uniform_blocks(len(weather.times), args.block_hours)
    )
    dlr = em.emit(
        Path(args.output),
        weather,
        _params(args),
        blocks,
        line_uid=args.line_uid,
        mode=args.mode,
        rating_agg=args.rating_agg,
        weather_agg=args.weather_agg,
        prefix=args.prefix,
        fmt=args.format,
        uid_base=args.uid_base,
    )
    print(
        f"{len(dlr['critical_spans'])} spans x {len(blocks)} blocks ({args.mode}) "
        f"-> {args.output}"
    )
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def make_parser() -> argparse.ArgumentParser:
    """Argument parser of the ``dlr2gtopt`` console script."""
    p = argparse.ArgumentParser(
        prog="dlr2gtopt",
        description="Dynamic line rating inputs for gtopt (towers -> spans -> "
        "weather -> IEEE 738 -> critical spans -> Line.dlr).",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser(
        "line-params", help="conductor/voltage/height from CEN sheet + defaults"
    )
    q.add_argument("--section-id", type=int, help="CEN Infotecnica section id")
    q.add_argument("--ficha", help="saved fichas-tecnicas/general JSON")
    q.add_argument("--kv", type=float, help="voltage class when there is no sheet")
    q.add_argument("--line-defaults", help="per-kV defaults JSON (default: bundled)")
    q.add_argument("--tmax", type=float, help="override design temperature [C]")
    q.add_argument("--conductor-height", type=float, help="override height [m]")
    q.add_argument("--subconductors", type=int, help="override per-phase count")
    q.add_argument("-o", "--output", required=True)
    _add_common_net(q)
    q.set_defaults(func=cmd_line_params)

    t = sub.add_parser("towers", help="tower coordinates of a line")
    t.add_argument("--source", choices=("cen", "osm", "manual"), required=True)
    t.add_argument("--line", required=True, help="line name written to the CSV")
    t.add_argument("--file", help="local structure list (cen) or CSV/KML (manual)")
    t.add_argument("--section-id", type=int, help="CEN Infotecnica section id")
    t.add_argument("--search", help="CEN: search sections by name")
    t.add_argument("--doc-id", type=int, help="CEN: annex document id")
    t.add_argument("--circuit", help="keep one circuit (e.g. C1)")
    t.add_argument("--utm-zone", type=int, help="UTM zone of E/N coordinates (south)")
    t.add_argument("--way-id", type=int, action="append", help="OSM way id (repeat)")
    t.add_argument("--bbox", type=float, nargs=4, metavar=("S", "W", "N", "E"))
    t.add_argument("--voltage-regex", help="OSM voltage tag regex (e.g. 220000)")
    t.add_argument("--name-regex", help="OSM name tag regex")
    t.add_argument(
        "--towers-only",
        action="store_true",
        help="OSM: keep only nodes tagged power=tower/pole",
    )
    t.add_argument("-o", "--output", required=True)
    _add_common_net(t)
    t.set_defaults(func=cmd_towers)

    s = sub.add_parser("spans", help="spans (midpoint, azimuth, length) from towers")
    s.add_argument("towers")
    s.add_argument("--line", help="line name if the CSV has no 'line' column")
    s.add_argument(
        "--min-length",
        type=float,
        default=1.0,
        help="merge towers closer than this [m]",
    )
    s.add_argument("-o", "--output", required=True)
    s.set_defaults(func=cmd_spans)

    w = sub.add_parser("weather", help="per-span hourly weather")
    w.add_argument("--spans", required=True)
    w.add_argument("--critical", help="only the spans of a critical-spans JSON")
    w.add_argument("--source", choices=WEATHER_SOURCES, required=True)
    w.add_argument("--interp", choices=wx.INTERP_METHODS, default="bilinear")
    w.add_argument(
        "--height",
        type=float,
        help="conductor height [m] (default: --line-params, else 15)",
    )
    w.add_argument("--line-params", help="line-params JSON (conductor height)")
    w.add_argument("--wind-profile", choices=wx.WIND_PROFILES, default="log")
    w.add_argument(
        "--model",
        help="explorador: wrf2010; open-meteo: " + "|".join(wx.OPEN_METEO_MODELS),
    )
    w.add_argument("--forecast-days", type=int, default=3)
    w.add_argument("--past-days", type=int, default=0)
    w.add_argument("--api-key", help="Open-Meteo customer API key (commercial use)")
    w.add_argument("--base-url", help="Open-Meteo base URL (self-hosted server)")
    w.add_argument("--batch", type=int, default=50, help="Open-Meteo points/request")
    w.add_argument("--grib", action="append", help="ECMWF open-data GRIB2 file")
    w.add_argument("--file", help="csv source: long weather CSV")
    w.add_argument("--correction", choices=wx.CORRECTIONS, default="none")
    w.add_argument("-o", "--output", required=True, help=".parquet or .csv")
    _add_common_net(w)
    w.set_defaults(func=cmd_weather)

    f = sub.add_parser("fetch-ecmwf", help="archive the latest ECMWF open-data run")
    f.add_argument("--archive", required=True)
    f.add_argument("--model", default="ifs", choices=("ifs", "aifs-single"))
    f.add_argument(
        "--source", default="ecmwf", choices=("ecmwf", "aws", "azure", "google")
    )
    f.add_argument("--max-step", type=int, default=144)
    f.add_argument("--step", type=int, default=3)
    f.set_defaults(func=cmd_fetch_ecmwf)

    a = sub.add_parser("ampacity", help="hourly IEEE 738 ampacity per span")
    a.add_argument("--spans", required=True)
    a.add_argument("--weather", required=True)
    a.add_argument("-o", "--output", required=True)
    _add_conductor(a)
    a.set_defaults(func=cmd_ampacity)

    c = sub.add_parser("critical-spans", help="Matus 2012 critical-span selection")
    c.add_argument("ampacity")
    c.add_argument(
        "--target-rho",
        type=float,
        default=0.9,
        help="stop when rho reaches this value [%(default)s]",
    )
    c.add_argument(
        "--max-spans",
        type=int,
        help="optional cap on the span count (default: none, target-rho"
        " decides; 1 = single representative span)",
    )
    c.add_argument("--months", help="restrict to months, e.g. 1,4,7,10")
    c.add_argument(
        "--eta-quantile",
        type=float,
        help="report eta = quantile of truth/estimate (off by default)",
    )
    c.add_argument("--segments", type=int, help="also select per N equal segments")
    c.add_argument(
        "--segment-spans",
        type=int,
        help="optional cap per segment with --segments (default: none,"
        " each segment stops at --target-rho)",
    )
    c.add_argument(
        "--trace-rho",
        type=float,
        default=0.99,
        help="the rho(n) trace runs until this rho [%(default)s]",
    )
    c.add_argument(
        "--trace-max", type=int, default=50, help="... or this many spans [%(default)s]"
    )
    c.add_argument("--plot", help="PNG of the rho(n) trace")
    c.add_argument("-o", "--output", required=True)
    c.set_defaults(func=cmd_critical)

    e = sub.add_parser("emit", help="gtopt Line.dlr JSON + schedules")
    e.add_argument("--spans", required=True)
    e.add_argument("--weather", required=True)
    e.add_argument("--critical", help="critical-spans JSON (default: all spans)")
    e.add_argument("--mode", choices=em.MODES, default="weather")
    e.add_argument("--block-hours", type=int, default=1)
    e.add_argument("--blocks", help="CSV [stage,]block,hours")
    e.add_argument("--rating-agg", choices=em.RATING_AGG, default="min")
    e.add_argument("--weather-agg", choices=em.WEATHER_AGG, default="min-rating")
    e.add_argument(
        "--voltage", type=float, help="kV (default: --line-params, else 220)"
    )
    e.add_argument("--v-pu", type=float, default=1.0)
    e.add_argument("--power-factor", type=float, default=1.0)
    e.add_argument("--safety-factor", type=float, default=1.0, help="eta (1 = off)")
    e.add_argument("--line-uid", type=int, default=1)
    e.add_argument("--uid-base", type=int, help="span uid base (default 1000*line uid)")
    e.add_argument("--prefix", default="dlr")
    e.add_argument("--format", choices=("csv", "parquet"), default="parquet")
    e.add_argument("-o", "--output", required=True, help="case directory")
    _add_conductor(e)
    e.set_defaults(func=cmd_emit)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point."""
    args = make_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    try:
        return int(args.func(args))
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"dlr2gtopt: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
