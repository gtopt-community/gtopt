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
from dlr2gtopt import _heights as hg
from dlr2gtopt import _history as hi
from dlr2gtopt import _lineparams as lp
from dlr2gtopt import _towers as tw
from dlr2gtopt import _typical as ty
from dlr2gtopt import _tz as tz
from dlr2gtopt import _weather as wx
from dlr2gtopt import _wind_angle as wa
from dlr2gtopt._ieee738 import CONDUCTORS, SOLAR_MODES, Conductor, conductor_from_args
from dlr2gtopt._spans import (
    Span,
    Tower,
    build_spans,
    read_spans,
    route_towers,
    read_towers,
    write_spans,
    write_towers,
)
from gtopt_shared import weather_source as ws

logger = logging.getLogger("dlr2gtopt")

WEATHER_SOURCES = ("explorador-eolico", "open-meteo", "ecmwf-opendata", "csv", "era5")
DEFAULT_CACHE = Path.home() / ".cache" / "gtopt" / "dlr2gtopt"


def _cache(args: argparse.Namespace) -> tw.PoliteCache:
    return tw.PoliteCache(Path(args.cache_dir), pause=args.pause, retries=args.retries)


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
    p.add_argument(
        "--retries",
        type=int,
        default=3,
        help="retries of a transient network failure, with backoff [%(default)s]",
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


def _read_ground(path: str | None) -> dict[tuple[str, int], float]:
    if path is None:
        return {}
    import csv

    with Path(path).open(newline="", encoding="utf-8") as f:
        return {
            (r["line"], int(r["span"])): float(r["elevation"])
            for r in csv.DictReader(f)
            if r.get("elevation", "").strip()
        }


def _derive_heights(
    args: argparse.Namespace, towers: list[Tower], spans: list[Span]
) -> list[Span]:
    """``spans --heights derive``: per-span effective conductor height."""
    line = _line_params(args)
    kv = args.kv if args.kv is not None else (line.voltage_kv if line else None)
    if kv is None:
        raise SystemExit("spans --heights derive needs --kv or --line-params")
    cls = lp.voltage_class(
        lp.load_defaults(Path(args.line_defaults) if args.line_defaults else None),
        kv,
    )
    class_height = line.conductor_height if line else float(cls["conductor_height"])
    geo = hg.TowerGeometry.from_json(Path(args.geometry)) if args.geometry else None
    ground = _read_ground(args.midspan_ground)
    by_line: dict[str, list[Span]] = {}
    for s in spans:
        by_line.setdefault(s.line, []).append(s)
    rows: list[tuple[str, hg.SpanHeight]] = []
    for name, route in route_towers(towers, args.min_length).items():
        hs = hg.line_heights(
            route,
            by_line.get(name, []),
            geometry=geo,
            phase=args.phase,
            class_attach=float(cls["attachment_height"]),
            class_height=class_height,
            midspan_ground={k[1]: v for k, v in ground.items() if k[0] == name} or None,
        )
        rows += [(name, h) for h in hs]
    height = {(n, h.span): h.height for n, h in rows}
    counts: dict[str, int] = {}
    for _, h in rows:
        counts[h.src] = counts.get(h.src, 0) + 1
    logger.info("heights by source: %s", counts)
    if args.heights_detail:
        import csv

        with Path(args.heights_detail).open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                ["line", "span", "height", "src", "sag_m", "sag_src", "h_flat"]
                + ["h_terrain", "clearance"]
            )
            for n, h in rows:
                w.writerow(
                    [n, h.span, f"{h.height:.2f}", h.src, f"{h.sag_m:.2f}"]
                    + [h.sag_src, f"{h.h_flat:.2f}"]
                    + ["" if h.h_terrain is None else f"{h.h_terrain:.2f}"]
                    + ["" if h.clearance is None else f"{h.clearance:.2f}"]
                )
    return [replace(s, height=height[s.line, s.span]) for s in spans]


def cmd_spans(args: argparse.Namespace) -> int:
    """Spans from towers."""
    towers = read_towers(Path(args.towers), args.line)
    spans = build_spans(towers, args.min_length)
    if args.heights == "derive":
        spans = _derive_heights(args, towers, spans)
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
    span_heights = args.height_source == "spans"
    if span_heights:
        missing = sum(1 for s in spans if s.height is None)
        logger.info(
            "per-span heights: %d of %d spans set; the rest use %g m",
            len(spans) - missing,
            len(spans),
            height,
        )
    kw: dict[str, Any] = {
        "height": height,
        "profile": args.wind_profile,
        "span_heights": span_heights,
    }
    if args.source in ("explorador-eolico", "explorador"):
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
            snap=args.snap,
            start_date=args.start_date,
            end_date=args.end_date,
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
            "weather --source era5: use --source open-meteo --model era5 "
            "--start-date ... --end-date ... (Open-Meteo archive), or download "
            "ERA5 from the Copernicus CDS and pass it as --source csv"
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


def cmd_history(args: argparse.Namespace) -> int:
    """Multi-year per-span weather: recon/ERA5 wind + ERA5 T/GHI (+ WRF
    calibration)."""
    spans = _select_spans(read_spans(Path(args.spans)), args.critical)
    if not spans:
        raise SystemExit("history: no spans selected")
    years = hi.parse_years(args.years)
    cache = _cache(args)
    if args.weather_source is not None:
        return _history_from_source(args, spans, years, cache)
    if args.height_source == "spans":
        raise SystemExit(
            "history --height-source spans needs --weather-source "
            "(the default recon/ERA5 path uses one --height)"
        )
    t_era, pts = hi.era5_points(
        cache,
        [(s.lat, s.lon) for s in spans],
        years,
        model=args.era5_model,
        api_key=args.api_key,
        base_url=args.base_url,
        snap=args.era5_snap,
        retries=args.retries,
        ghi_lead=args.era5_ghi_lead,
    )
    logger.info(hi.ERA5_ATTRIBUTION)
    wind = None
    if args.wind == "recon":
        client = wx.Explorador(cache, "recon")
        wind = hi.recon_wind(
            client,
            spans,
            method=args.interp,
            height=args.height,
            profile=args.wind_profile,
            progress=lambda k, n: logger.info("recon node %d/%d", k, n),
        )
    calib = None
    if args.calibrate == "wrf":
        if not args.calib_weather:
            raise SystemExit("history --calibrate wrf needs --calib-weather")
        calib = wx.read_weather(Path(args.calib_weather), spans)
    times = list(hi.year_hours(years))
    era_idx = {t: i for i, t in enumerate(t_era)}
    times = [t for t in times if t in era_idx]
    rows = np.array([era_idx[t] for t in times])
    pts = [
        hi.Era5Point(
            p.temperature[rows], p.ghi[rows], p.wind_speed[rows], p.wind_direction[rows]
        )
        for p in pts
    ]
    if wind is not None:
        have = set(wind.times)
        times_w = [t for t in times if t in have]
        if len(times_w) < len(times):
            logger.warning(
                "history: %d hours outside the wind record dropped",
                len(times) - len(times_w),
            )
            keep = np.array([t in have for t in times])
            pts = [
                hi.Era5Point(
                    p.temperature[keep],
                    p.ghi[keep],
                    p.wind_speed[keep],
                    p.wind_direction[keep],
                )
                for p in pts
            ]
            times = times_w
    w = hi.combine_history(
        times, spans, pts, wind=wind, calib=calib, height=args.height
    )
    w.write(Path(args.output))
    print(
        f"{len(w.times)} h x {len(spans)} spans ({years[0]}-{years[-1]}) -> {args.output}"
    )
    return 0


_NO_DIRECTION = (
    "history: --weather-source {source} has no wind direction; pass "
    "--wind-angle-deg A (fixed attack angle to the conductor: 0 = parallel, "
    "the conservative bound; 45 = the study value for an unknown "
    "direction; 90 = perpendicular, the most cooling, optimistic) or "
    "--perpendicular-wind (90 deg); there is no default"
)


def _eolico_angle(
    args: argparse.Namespace,
    spans: Sequence[Span],
    cache: tw.PoliteCache,
    pw: ws.PointWeather,
    basis: ws.CalendarBasis,
) -> None:
    """``--wind-angle-source explorador-eolico``: the attack angle per span
    and local (month, hour) from the Eolico WRF dominant direction; only
    ``pw.data["wind_direction"]`` changes (speed etc. stay the source's)."""
    models = [m.strip() for m in args.wind_angle_wrf.split(",") if m.strip()]
    points = [(s.lat, s.lon) for s in spans]
    heights = [s.height for s in spans] if args.height_source == "spans" else None
    loc: list[datetime] = []
    spd, wdir = [], []
    try:
        for m in models:
            ep = ws.explorador_points(
                cache,
                points,
                model=m,
                height=args.height,
                heights=heights,
                method=args.interp,
                profile=args.wind_profile,
            )
            loc += ws.local_times(ep.times, basis)
            spd.append(ep.data["wind_speed"])
            wdir.append(ep.data["wind_direction"])
    except ws.WeatherSourceError as e:
        raise SystemExit(
            f"history: --wind-angle-source explorador-eolico: {e}"
        ) from None
    table = wa.month_hour_dominant(loc, np.vstack(spd), np.vstack(wdir))
    az = [s.azimuth for s in spans]
    phi = wa.angle_table(table, az)
    try:
        pw.data["wind_direction"] = wa.wind_direction_series(
            ws.local_times(pw.times, basis), phi, az
        )
    except ValueError as e:
        raise SystemExit(f"history: {e}") from None
    pw.provenance["wind_direction"] = (
        "attack angle per span and local month x hour from the Explorador "
        "Eolico WRF dominant direction (speed-weighted modal 22.5 deg sector, "
        "circular mean inside it); a rating parameter, the weather fields "
        "stay the source's"
    )
    pw.provenance["wind_angle"] = {
        "source": "explorador-eolico",
        "wrf_models": models,
        "wrf_years": sorted({ws.coverage("explorador", m).first_year for m in models}),
        "local_calendar": ws.describe_basis(basis),
        "height_m": args.height,
        "per_span": {
            str(s.span): {
                "azimuth_deg": round(s.azimuth, 3),
                "phi_deg": np.round(phi[j], 2).tolist(),
                "dominant_direction_deg": np.round(table.direction[j], 2).tolist(),
                "sector_share": np.round(table.share[j], 3).tolist(),
                "R": np.round(table.r[j], 3).tolist(),
            }
            for j, s in enumerate(spans)
        },
        "table_layout": "12 months x 24 local hours",
    }


def _history_from_source(
    args: argparse.Namespace,
    spans: Sequence[Span],
    years: Sequence[int],
    cache: tw.PoliteCache,
) -> int:
    """``history --weather-source``: every field from one source (shared
    :mod:`gtopt_shared.weather_source` layer), climatology outside its years."""
    if args.calibrate == "wrf" and args.weather_source == "explorador":
        raise SystemExit(
            "history: --calibrate wrf calibrates ERA5 on the Explorador WRF; "
            f"with --weather-source {args.weather_source} the wind already is "
            "the WRF: nothing to calibrate"
        )
    solar = args.weather_source == "explorador-solar"
    angle = 90.0 if args.perpendicular_wind else args.wind_angle_deg
    if args.perpendicular_wind and args.wind_angle_deg is not None:
        raise SystemExit(
            "history: --perpendicular-wind and --wind-angle-deg exclude each other"
        )
    from_eolico = args.wind_angle_source == "explorador-eolico"
    if from_eolico and not solar:
        raise SystemExit(
            "history: --wind-angle-source explorador-eolico is for "
            "--weather-source explorador-solar (a source without direction)"
        )
    if from_eolico and angle is not None:
        raise SystemExit(
            "history: --wind-angle-source explorador-eolico takes the angle from "
            "the Eolico direction climatology; drop --wind-angle-deg / "
            "--perpendicular-wind (they belong to --wind-angle-source fixed)"
        )
    if solar and angle is None and not from_eolico:
        raise SystemExit(_NO_DIRECTION.format(source=args.weather_source))
    if solar:
        logger.warning(
            "history: the Explorador Solar wind (vel) has no direction and a "
            "single level (5.5 m per the site's SOLEVAPP report, "
            "--explorador-solar-wind-height); it is brought to the conductor "
            "height by a log law"
        )
    basis = ws.calendar_basis(
        args,
        None if args.climatology_utc_offset is not None else _time_basis(args, spans),
    )
    try:
        pw = ws.point_weather(
            args.weather_source,
            cache,
            [(s.lat, s.lon) for s in spans],
            years,
            model=ws.model_for(args),
            climatology=args.climatology,
            stat=args.climatology_stat,
            utc_offset=basis,
            source_years=ws.parse_years(args.climatology_years),
            need=("temperature", "wind_speed", "ghi")
            if solar
            else ("temperature", "wind_speed", "wind_direction", "ghi"),
            height=args.height,
            heights=[s.height for s in spans]
            if args.height_source == "spans"
            else None,
            method=args.interp,
            profile=args.wind_profile,
            api_key=args.api_key,
            base_url=args.base_url,
            snap=args.era5_snap,
            solar=ws.solar_options(args),
        )
    except ws.WeatherSourceError as e:
        raise SystemExit(f"history: {e}") from None
    if from_eolico:
        _eolico_angle(args, spans, cache, pw, basis)
    if "wind_direction" not in pw.data:
        if angle is None:
            raise SystemExit(_NO_DIRECTION.format(source=args.weather_source))
        if not 0.0 <= angle <= 90.0:
            raise SystemExit("history: --wind-angle-deg must be in [0, 90]")
        pw.data["wind_direction"] = np.tile(
            np.mod(np.array([s.azimuth for s in spans]) + angle, 360.0),
            (len(pw.times), 1),
        )
        pw.provenance["wind_direction"] = (
            f"fixed attack angle {angle:g} deg to each span (no source direction)"
        )
    calib = None
    if args.calibrate == "wrf":
        if not args.calib_weather:
            raise SystemExit("history --calibrate wrf needs --calib-weather")
        calib = wx.read_weather(Path(args.calib_weather), spans)
    d = pw.data
    pts = [
        hi.Era5Point(
            d["temperature"][:, j],
            d["ghi"][:, j],
            d["wind_speed"][:, j],
            d["wind_direction"][:, j],
        )
        for j in range(len(spans))
    ]
    wind = wx.Weather(
        pw.times,
        list(spans),
        {"wind_speed": d["wind_speed"], "wind_direction": d["wind_direction"]},
    )
    w = hi.combine_history(
        pw.times, spans, pts, wind=wind, calib=calib, height=args.height
    )
    out = Path(args.output)
    w.write(out)
    pw.provenance["tool"] = "dlr2gtopt history"
    pw.provenance["calibrate"] = args.calibrate
    prov = pw.write_provenance(out)
    print(
        f"{len(w.times)} h x {len(spans)} spans ({years[0]}-{years[-1]}, "
        f"{args.weather_source}) -> {out} (+ {prov.name})"
    )
    return 0


def _onoff(text: str) -> bool:
    t = text.strip().lower()
    if t in ("on", "true", "1", "yes"):
        return True
    if t in ("off", "false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError("use on|off")


def _add_time_basis(q: argparse.ArgumentParser) -> None:
    q.add_argument(
        "--timezone",
        default="auto",
        help="IANA zone of the local calendar; auto = from the span "
        "coordinates (Chile: America/Santiago) [auto]",
    )
    q.add_argument(
        "--dst",
        choices=tz.DST_MODES,
        default="off",
        help="off: fixed standard time all year, as PLP indhor (Chile "
        "UTC-4); on: civil time with daylight saving [off]",
    )
    q.add_argument(
        "--utc-offset",
        type=float,
        default=None,
        help="explicit fixed offset [h]; overrides --timezone/--dst",
    )


def _time_basis(args: argparse.Namespace, spans: Sequence[Any]) -> tz.TimeBasis:
    return tz.resolve(
        args.timezone,
        args.dst,
        args.utc_offset,
        [s.lat for s in spans],
        [s.lon for s in spans],
    )


def _cases(args: argparse.Namespace) -> list[ty.Case]:
    return [ty.parse_case(c) for c in (args.case or ty.DEFAULT_CASES)]


def cmd_typical_year(args: argparse.Namespace) -> int:
    """8760 h typical years of critical-span weather (mean, P90, ...)."""
    spans = _select_spans(read_spans(Path(args.spans)), args.critical)
    if not spans:
        raise SystemExit("typical-year: no spans selected")
    weather = wx.read_weather(Path(args.history), spans)
    params = _params(args)
    fit_years = hi.parse_years(args.years) if args.years else None
    test_years = hi.parse_years(args.test_years) if args.test_years else None
    basis = _time_basis(args, spans)
    logger.info("typical-year: calendar %s", basis.describe())
    if test_years and fit_years is None:
        cal = ty.local_calendar(weather.times, basis.spec)
        ys, counts = np.unique(cal.year, return_counts=True)
        # years with at least a month of hours (drops the few hours that the
        # UTC offset pushes into the neighbouring calendar year)
        fit_years = sorted(
            {int(y) for y, c in zip(ys, counts) if c >= 720} - set(test_years)
        )
    if fit_years and test_years and set(fit_years) & set(test_years):
        raise SystemExit("typical-year: --years and --test-years overlap")
    h = ty.History.build(weather, params, basis.spec, fit_years)
    opt = ty.TypicalOptions(
        mean_method=args.mean_method,
        quantile_method=args.p90_method,
        selection=args.selection,
        analogues=args.analogues,
        tolerance=args.tolerance,
        fs_weights=ty.parse_weights(args.fs_weights),
        tmy_candidates=args.tmy_candidates,
        blend_hours=args.blend_hours,
        source_times=(
            ty.read_source_times(Path(args.source_hours)) if args.source_hours else None
        ),
        align=args.align,
        condition_k=args.condition_k,
    )
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    stats = ty.month_hour_stats(h)
    import pandas as pd

    ty.write_frame(pd.DataFrame(stats), out / f"rating_stats_month_hour.{args.format}")
    full = ty.History.build(weather, params, basis.spec) if test_years else None
    years_built = []
    report: dict[str, Any] = {
        "fit_years": fit_years,
        "test_years": test_years,
        "calendar": basis.describe(),
    }
    for case in _cases(args):
        typ = ty.build_typical(h, case, opt)
        years_built.append(typ)
        path = out / f"typical_{case.label}.{args.format}"
        ty.write_frame(ty.typical_frame(typ, sources=args.source_columns), path)
        rep = ty.verify(typ, h)
        if test_years and full is not None:
            assert typ.line_mw is not None
            rep["holdout"] = ty.holdout_errors(
                typ.line_mw, full, test_years, args.static_mw
            )
        report[case.label] = rep
        exc = rep["exceedance"]
        print(
            f"{case.label:>6} ({typ.method}): mean {rep['typical_mean_mw']:.1f} MW "
            f"(history {rep['history_mean_mw']:.1f}), exceedance {exc:.3f} -> {path}"
        )
    (out / "typical_report.json").write_text(
        json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8"
    )
    if args.plot:
        ty.plot_typical(out, h, years_built, stats, args.static_mw)
    return 0


def _stages(args: argparse.Namespace) -> list[tuple[int, int]]:
    if args.stage_months:
        return ty.read_stage_months(Path(args.stage_months))
    return ty.stage_months(args.first_month, args.stages, args.first_stage)


def _write(
    args: argparse.Namespace,
    bw: ty.BlockWeather,
    keys: list[tuple[str, int]],
    spans: list[Span],
    out: Path,
) -> None:
    by_key = {(s.line, s.span): s for s in spans}
    missing = [k for k in keys if k not in by_key]
    if missing:
        raise SystemExit(f"spans file lacks {missing[0]}")
    ty.write_schedules(
        out,
        bw,
        [by_key[k] for k in keys],
        _params(args),
        mode=args.mode,
        line_uid=args.line_uid,
        uid_base=args.uid_base,
        prefix=args.prefix,
        fmt=args.format,
        scenarios=[int(x) for x in args.scenarios.split(",")],
        tmax_profile=args.tmax_profile,
    )
    ty.write_frame(
        ty.block_frame(bw, keys, sources=getattr(args, "source_columns", True)),
        out / f"{args.prefix}_blocks.{args.format}",
    )


def cmd_to_blocks(args: argparse.Namespace) -> int:
    """Typical year -> the case's blocks (+ Line.dlr schedules)."""
    spans = read_spans(Path(args.spans))
    tt = ty.read_typical(Path(args.typical))
    if args.block_map:
        sob = None
        if args.planning:
            sob = ty.stage_of_block_from_planning(
                json.loads(Path(args.planning).read_text(encoding="utf-8"))
            )
        specs = ty.blocks_from_map(
            ty.read_frame(Path(args.block_map)), sob, args.map_hour_base
        )
    elif args.hour_bands:
        specs = ty.blocks_from_bands(
            _stages(args), ty.parse_bands(args.hour_bands), args.first_block
        )
    else:
        specs = ty.blocks_chronological(
            _stages(args), args.block_hours, args.first_block
        )
    case = ty.parse_case(args.block_stat or Path(args.typical).stem.split("_", 1)[-1])
    by_key = {(s.line, s.span): s for s in spans}
    if args.history:
        hist_spans = [by_key[k] for k in tt.keys if k in by_key]
        h = ty.History.build(
            wx.read_weather(Path(args.history), hist_spans),
            _params(args),
            _time_basis(args, hist_spans).spec,
            hi.parse_years(args.years) if args.years else None,
        )
        bw = ty.history_to_blocks(h, specs, case)
        _write(args, bw, tt.keys, spans, Path(args.output))
        print(f"{len(specs)} blocks ({case.label}, from history) -> {args.output}")
        return 0
    bw = ty.to_blocks(
        tt,
        specs,
        case,
        selection=args.selection,
        analogues=args.analogues,
        tolerance=args.tolerance,
        spans=[by_key[k] for k in tt.keys if k in by_key],
        params=_params(args),
    )
    _write(args, bw, tt.keys, spans, Path(args.output))
    print(f"{len(specs)} blocks ({case.label}) -> {args.output}")
    return 0


def cmd_repeat(args: argparse.Namespace) -> int:
    """Tile a month-template block table over a long horizon."""
    spans = read_spans(Path(args.spans))
    tpl, keys = ty.read_block_frame(Path(args.template))
    bw = ty.repeat(tpl, _stages(args), args.first_block)
    _write(args, bw, keys, spans, Path(args.output))
    print(
        f"{len(bw.specs)} blocks over {len({s.stage for s in bw.specs})} stages "
        f"-> {args.output}"
    )
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
    s.add_argument(
        "--heights",
        choices=("none", "derive"),
        default="none",
        help="derive: per-span effective conductor height in the 'height'"
        " column (attachment - 2/3 sag; see README); none: no column",
    )
    s.add_argument("--geometry", help="tower geometry JSON (memoria de calculo)")
    s.add_argument("--phase", choices=hg.PHASES, default="mid")
    s.add_argument("--kv", type=float, help="voltage class for the defaults")
    s.add_argument("--line-params", help="line-params JSON (voltage, height)")
    s.add_argument("--line-defaults", help="per-class defaults JSON")
    s.add_argument(
        "--midspan-ground",
        help="CSV line,span,elevation: ground at mid-span [m] (terrain;"
        " needs tower elevations)",
    )
    s.add_argument("--heights-detail", help="per-span height breakdown CSV")
    s.add_argument("-o", "--output", required=True)
    s.set_defaults(func=cmd_spans)

    w = sub.add_parser("weather", help="per-span hourly weather")
    w.add_argument("--spans", required=True)
    w.add_argument("--critical", help="only the spans of a critical-spans JSON")
    w.add_argument(
        "--source",
        "--weather-source",
        dest="source",
        choices=(*WEATHER_SOURCES, "explorador"),
        required=True,
        help="explorador = explorador-eolico (the history --weather-source name)",
    )
    w.add_argument("--interp", choices=wx.INTERP_METHODS, default="bilinear")
    w.add_argument(
        "--height",
        type=float,
        help="conductor height [m] (default: --line-params, else 15)",
    )
    w.add_argument("--line-params", help="line-params JSON (conductor height)")
    w.add_argument(
        "--height-source",
        choices=("scalar", "spans"),
        default="scalar",
        help="scalar: one height for every span (--height / --line-params / 15);"
        " spans: each span's 'height' column, the scalar where it is blank",
    )
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
    w.add_argument(
        "--start-date",
        help="Open-Meteo historical archive from this day (YYYY-MM-DD, UTC)",
    )
    w.add_argument("--end-date", help="Open-Meteo archive last day (YYYY-MM-DD)")
    w.add_argument(
        "--snap",
        type=float,
        help="Open-Meteo: round span midpoints to this grid [deg] and request "
        "each distinct point once (e.g. 0.25 for ERA5)",
    )
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
    hs = sub.add_parser(
        "history", help="multi-year per-span weather (recon/ERA5 + calibration)"
    )
    hs.add_argument("--spans", required=True)
    hs.add_argument("--critical", help="only the spans of a critical-spans JSON")
    hs.add_argument("--years", required=True, help="e.g. 1998-2017 or 2008,2010")
    hs.add_argument(
        "--wind",
        choices=hi.WIND_SOURCES,
        default="recon",
        help="recon: Explorador 1980-2017 (1 km); era5: ERA5 10 m",
    )
    hs.add_argument("--era5-model", choices=hi.ERA5_MODELS, default="era5")
    hs.add_argument(
        "--calibrate",
        choices=hi.CALIBRATIONS,
        default="none",
        help="wrf: month x hour T shift and GHI ratio fitted on "
        "--calib-weather (WRF 2010/2015 per span)",
    )
    hs.add_argument("--calib-weather", help="WRF weather table of the same spans")
    hs.add_argument("--interp", choices=wx.INTERP_METHODS, default="bilinear")
    hs.add_argument("--height", type=float, default=15.0, help="conductor height [m]")
    hs.add_argument("--wind-profile", choices=wx.WIND_PROFILES, default="log")
    hs.add_argument(
        "--wind-angle-deg",
        type=float,
        default=None,
        help="fixed wind attack angle to the conductor [deg, 0-90] for a "
        "source without wind direction (explorador-solar): 45 = study value "
        "for an unknown direction (see README), 0 = parallel "
        "(conservative variant), 90 = perpendicular (most cooling, "
        "optimistic); no default: such a source fails without it",
    )
    hs.add_argument(
        "--perpendicular-wind",
        action="store_true",
        help="assume wind perpendicular to every span (90 deg, the static-"
        "rating convention; the most cooling angle, not conservative)",
    )
    hs.add_argument(
        "--wind-angle-source",
        choices=("fixed", "explorador-eolico"),
        default="fixed",
        help="attack angle for a source without wind direction "
        "(explorador-solar): fixed = --wind-angle-deg / --perpendicular-wind; "
        "explorador-eolico = per span and local month x hour from the "
        "dominant direction of the Explorador Eolico WRF (a rating "
        "parameter: speed, temperature and irradiance stay the source's) "
        "[%(default)s]",
    )
    hs.add_argument(
        "--wind-angle-wrf",
        default="wrf2010,wrf2015",
        help="Eolico WRF models pooled for --wind-angle-source "
        "explorador-eolico (comma list of wrf2010, wrf2015) [%(default)s]",
    )
    hs.add_argument(
        "--height-source",
        choices=("scalar", "spans"),
        default="scalar",
        help="with --weather-source: scalar = --height for every span; spans "
        "= each span's 'height' column, --height where it is blank",
    )
    hs.add_argument(
        "--era5-snap",
        type=float,
        default=0.25,
        help="request ERA5 once per grid cell of this size [deg] (0 = per span)",
    )
    hs.add_argument(
        "--era5-ghi-lead",
        type=int,
        default=1,
        help="hours to move ERA5 GHI back (its label ends the averaging hour) "
        "[%(default)s]",
    )
    hs.add_argument(
        "--api-key",
        help="Open-Meteo customer API key (the free API is non-commercial only)",
    )
    hs.add_argument("--base-url", help="Open-Meteo archive base URL (own server)")
    hs.add_argument("-o", "--output", required=True, help=".parquet or .csv")
    ws.add_weather_source_arguments(hs, with_open_meteo_keys=False)
    _add_time_basis(hs)
    _add_common_net(hs)
    hs.set_defaults(func=cmd_history)

    ty_ = sub.add_parser(
        "typical-year", help="8760 h typical years of critical-span weather"
    )
    ty_.add_argument("--history", required=True, help="multi-year weather table")
    ty_.add_argument("--spans", required=True)
    ty_.add_argument("--critical", help="only the spans of a critical-spans JSON")
    ty_.add_argument(
        "--case",
        action="append",
        help="p90 (default; rating exceeded 90%% of the time = 10th "
        "percentile) | mean | pNN (p50 = median) | quantile:<q> | min; "
        "repeat for several [default: p90 and mean, side by side]",
    )
    ty_.add_argument("--mean-method", choices=ty.MEAN_METHODS, default="tmy")
    ty_.add_argument(
        "--source-hours",
        help="align with another typical year (e.g. the renewable P50 year): "
        "a table with hour_of_year and source_time; overrides the methods",
    )
    ty_.add_argument(
        "--align",
        choices=ty.ALIGN_MODES,
        default="conditional",
        help="with --source-hours: exact = same hours; conditional = the case "
        "statistic among the hours with the same month, hour and wind",
    )
    ty_.add_argument(
        "--condition-k",
        type=int,
        default=60,
        help="conditional alignment: pool size [%(default)s]",
    )
    ty_.add_argument(
        "--p90-method",
        choices=ty.QUANTILE_METHODS,
        default="analogue",
        help="method of every quantile case (p90, quantile:q)",
    )
    ty_.add_argument("--selection", choices=ty.SELECTIONS, default="nearest")
    ty_.add_argument("--analogues", type=int, default=5, help="K (rotate, knn-mean)")
    ty_.add_argument(
        "--tolerance",
        type=float,
        default=0.02,
        help="knn-mean: max relative rating error [%(default)s]",
    )
    ty_.add_argument(
        "--fs-weights",
        help="TMY weights, e.g. rating_mean=0.4,rating_min=0.4,"
        "wind_speed=0.1,temperature=0.1 (the default)",
    )
    ty_.add_argument("--tmy-candidates", type=int, default=5)
    ty_.add_argument(
        "--blend-hours",
        type=int,
        default=6,
        help="month-boundary blend, hours each side (0 = off)",
    )
    _add_time_basis(ty_)
    ty_.add_argument(
        "--source-columns",
        type=_onoff,
        default=True,
        help="write source_time_2 (blend partner) and knn_sources [on]",
    )
    ty_.add_argument("--years", help="fit years (default: all)")
    ty_.add_argument("--test-years", help="hold-out years for validation")
    ty_.add_argument("--static-mw", type=float, help="static rating, for comparison")
    ty_.add_argument("--plot", action="store_true", help="PNG plots in the output dir")
    ty_.add_argument("--format", choices=("csv", "parquet"), default="parquet")
    ty_.add_argument("--voltage", type=float, default=220.0, help="kV")
    ty_.add_argument("--v-pu", type=float, default=1.0)
    ty_.add_argument("--power-factor", type=float, default=1.0)
    ty_.add_argument("-o", "--output", required=True, help="output directory")
    _add_conductor(ty_)
    ty_.set_defaults(func=cmd_typical_year)

    def _writer_opts(q: argparse.ArgumentParser) -> None:
        q.add_argument("--spans", required=True)
        q.add_argument("--mode", choices=ty.WRITE_MODES, default="weather")
        q.add_argument(
            "--tmax-profile",
            action="store_true",
            help="also Line/<prefix>_tmax (per-line MW, Line.tmax_ab/ba)",
        )
        q.add_argument("--scenarios", default="1", help="scenario uids, e.g. 1,2,3")
        q.add_argument("--stage-months", help="CSV stage,month")
        q.add_argument("--first-month", type=int, default=1)
        q.add_argument("--stages", type=int, default=12)
        q.add_argument("--first-stage", type=int, default=1)
        q.add_argument("--first-block", type=int, default=1)
        q.add_argument("--voltage", type=float, default=220.0, help="kV")
        q.add_argument("--v-pu", type=float, default=1.0)
        q.add_argument("--power-factor", type=float, default=1.0)
        q.add_argument("--safety-factor", type=float, default=1.0)
        q.add_argument("--line-uid", type=int, default=1)
        q.add_argument("--uid-base", type=int)
        q.add_argument("--prefix", default="dlr")
        q.add_argument("--format", choices=("csv", "parquet"), default="parquet")
        q.add_argument("-o", "--output", required=True, help="case directory")
        _add_conductor(q)

    tb = sub.add_parser("to-blocks", help="typical year -> blocks + Line.dlr schedules")
    tb.add_argument("typical", help="typical_<case>.parquet|csv")
    tb.add_argument(
        "--block-map", help="hour->block membership (plp2gtopt BlockHourMap)"
    )
    tb.add_argument("--planning", help="planning JSON (block -> stage)")
    tb.add_argument("--map-hour-base", type=int, choices=(0, 1), default=1)
    tb.add_argument(
        "--hour-bands", help="month x hour-band blocks, e.g. 1-7,8-18,19-24"
    )
    tb.add_argument(
        "--block-hours", type=int, default=1, help="chronological blocks of N hours"
    )
    tb.add_argument(
        "--block-stat", help="statistic within a block (default: the file's case)"
    )
    tb.add_argument(
        "--history",
        help="take each block's statistic and weather from this multi-year "
        "history (exact block P90) instead of the typical year's hours",
    )
    _add_time_basis(tb)
    tb.add_argument(
        "--source-columns",
        type=_onoff,
        default=True,
        help="write source_time / source_year per block [on]",
    )
    tb.add_argument("--years", help="with --history: years to use")
    tb.add_argument("--selection", choices=ty.SELECTIONS, default="nearest")
    tb.add_argument("--analogues", type=int, default=5)
    tb.add_argument("--tolerance", type=float, default=0.02)
    _writer_opts(tb)
    tb.set_defaults(func=cmd_to_blocks)

    rp = sub.add_parser("repeat", help="tile a month template over a horizon")
    rp.add_argument("template", help="<prefix>_blocks table from to-blocks")
    _writer_opts(rp)
    rp.set_defaults(func=cmd_repeat)
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
