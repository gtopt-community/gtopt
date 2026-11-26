# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``re2gtopt`` -- wind and PV plant profiles for gtopt, consistent with
the DLR typical year of ``dlr2gtopt``.

Pipeline (each step a subcommand reading the previous one's files)::

    re2gtopt history   --plants plants.csv --years 1998-2017 -o weather.parquet
    re2gtopt model     --plants plants.csv --weather weather.parquet -o run/
    re2gtopt calibrate --plants plants.csv --model run/ \\
                       --observed obs.parquet -o cal/
    re2gtopt typical   --cf cal/cf_calibrated.parquet --plants plants.csv \\
                       --dlr-typical typ/typical_mean.parquet --case mean -o re_typ/
    re2gtopt to-blocks re_typ/re_typical_mean.parquet --plants plants.csv \\
                       --hour-bands 1-7,8-18,19-24 -o tpl/
    re2gtopt repeat    tpl/re_profile_blocks.parquet --plants plants.csv \\
                       --stages 240 -o case/
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from dlr2gtopt import _history as dhi
from dlr2gtopt import _typical as dty
from dlr2gtopt import _tz as dtz
from dlr2gtopt import _weather as dwx
from dlr2gtopt._towers import PoliteCache
from gtopt_shared import weather_source as ws
from re2gtopt import _blocks as bl
from re2gtopt import _calib as cb
from re2gtopt import _cen as cen
from re2gtopt import _model as md
from re2gtopt import _observed as ob
from re2gtopt import _plants as pl
from re2gtopt import _sources as src
from re2gtopt import _typical as rt
from re2gtopt._pv import (
    CAPACITY_BASES,
    DECOMPOSITIONS,
    IAM_MODELS,
    TEMPERATURE_MODELS,
    TIME_LABELS,
    TRACKINGS,
    TRANSPOSITIONS,
    PVOptions,
)
from re2gtopt._wind import (
    CUTOUT_MODES,
    DENSITY_MODES,
    SHEAR_METHODS,
    SMOOTHING_MODES,
    WAKE_MODES,
    WindOptions,
)

logger = logging.getLogger("re2gtopt")
DEFAULT_CACHE = Path.home() / ".cache" / "gtopt" / "re2gtopt"
CALIBRATION_CHOICES = ("auto", *cb.CALIBRATIONS)
FALLBACKS = ("none", "nearest")
ENERGY_TARGETS = ("history", "observed")


def _json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=_jsonable) + "\n", "utf-8")


def _jsonable(x: Any) -> Any:
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, np.ndarray):
        return x.tolist()
    return str(x)


def _write_table(rows: list[dict[str, Any]], path: Path) -> None:
    import pandas as pd  # pylint: disable=import-outside-toplevel

    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _onoff(text: str) -> bool:
    t = text.strip().lower()
    if t not in ("on", "off"):
        raise argparse.ArgumentTypeError("use on or off")
    return t == "on"


def _cap(text: str) -> float | None:
    return None if text.strip().lower() in ("none", "off") else float(text)


# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------


def _add_wind(p: argparse.ArgumentParser) -> None:
    d = WindOptions()
    g = p.add_argument_group("wind model (defaults documented in the README)")
    g.add_argument(
        "--turbine",
        default=d.turbine,
        help="auto (generic by year/site), oedb:<type>, iec1|iec2|iec3|iec3s, "
        "sp<W/m2> or a speed,power CSV [%(default)s]",
    )
    g.add_argument("--cut-in", type=float, default=d.cut_in)
    g.add_argument("--cut-out", type=float, default=d.cut_out)
    g.add_argument("--restart", type=float, default=d.restart)
    g.add_argument("--cutout-mode", choices=CUTOUT_MODES, default=d.cutout_mode)
    g.add_argument("--soft-width", type=float, default=d.soft_width)
    g.add_argument("--smoothing", choices=SMOOTHING_MODES, default=d.smoothing)
    g.add_argument("--smoothing-a", type=float, default=d.smoothing_a)
    g.add_argument("--smoothing-b", type=float, default=d.smoothing_b)
    g.add_argument("--density", choices=DENSITY_MODES, default=d.density)
    g.add_argument("--wake-loss", type=float, default=d.wake_loss)
    g.add_argument("--wake-mode", choices=WAKE_MODES, default=d.wake_mode)
    g.add_argument("--availability", type=float, default=d.availability)
    g.add_argument("--electrical-loss", type=float, default=d.electrical_loss)
    g.add_argument(
        "--wind-cap", type=_cap, default=d.cap, help="p.u. or none [%(default)s]"
    )


def _wind_opt(a: argparse.Namespace) -> WindOptions:
    o = WindOptions(
        turbine=a.turbine,
        cut_in=a.cut_in,
        cut_out=a.cut_out,
        restart=a.restart,
        cutout_mode=a.cutout_mode,
        soft_width=a.soft_width,
        smoothing=a.smoothing,
        smoothing_a=a.smoothing_a,
        smoothing_b=a.smoothing_b,
        density=a.density,
        wake_loss=a.wake_loss,
        wake_mode=a.wake_mode,
        availability=a.availability,
        electrical_loss=a.electrical_loss,
        cap=a.wind_cap,
    )
    o.validate()
    return o


def _add_pv(p: argparse.ArgumentParser) -> None:
    d = PVOptions()
    g = p.add_argument_group("PV model (pvlib; defaults documented in the README)")
    g.add_argument("--tracking", choices=TRACKINGS, default=d.tracking)
    g.add_argument("--tilt", type=float, help="fixed tilt [|lat|]")
    g.add_argument("--azimuth", type=float, help="fixed azimuth [equator-facing]")
    g.add_argument("--max-angle", type=float, default=d.max_angle)
    g.add_argument("--backtrack", type=_onoff, default=d.backtrack, help="on|off")
    g.add_argument("--gcr", type=float, default=d.gcr)
    g.add_argument("--decomposition", choices=DECOMPOSITIONS, default=d.decomposition)
    g.add_argument("--transposition", choices=TRANSPOSITIONS, default=d.transposition)
    g.add_argument("--albedo", type=float, default=d.albedo)
    g.add_argument("--iam", choices=IAM_MODELS, default=d.iam)
    g.add_argument("--soiling", type=float, default=d.soiling)
    g.add_argument(
        "--temperature-model", choices=TEMPERATURE_MODELS, default=d.temperature_model
    )
    g.add_argument("--gamma-pdc", type=float, default=d.gamma_pdc)
    g.add_argument(
        "--dc-ac-ratio",
        type=_dc_ac,
        default=None,
        help="generic PV DC/AC: auto = by commissioning year (--dc-ac-table) "
        "or a fixed value [auto]",
    )
    g.add_argument(
        "--dc-ac-table",
        type=pl.eq.parse_dc_ac_table,
        default=pl.eq.GENERIC_DC_AC,
        help="auto DC/AC by commissioning year, LASTYEAR:RATIO,... "
        "[2015:1.05,2019:1.2,9999:1.3]",
    )
    g.add_argument("--dc-loss", type=float, default=d.dc_loss)
    g.add_argument("--inverter-efficiency", type=float, default=d.inverter_efficiency)
    g.add_argument("--ac-loss", type=float, default=d.ac_loss)
    g.add_argument("--pv-availability", type=float, default=d.availability)
    g.add_argument("--pv-cap", type=_cap, default=d.cap)
    g.add_argument("--time-label", choices=TIME_LABELS, default=d.time_label)
    g.add_argument("--capacity-basis", choices=CAPACITY_BASES, default=d.capacity_basis)


def _pv_opt(a: argparse.Namespace) -> PVOptions:
    o = PVOptions(
        tracking=a.tracking,
        tilt=a.tilt,
        azimuth=a.azimuth,
        max_angle=a.max_angle,
        backtrack=a.backtrack,
        gcr=a.gcr,
        decomposition=a.decomposition,
        transposition=a.transposition,
        albedo=a.albedo,
        iam=a.iam,
        soiling=a.soiling,
        temperature_model=a.temperature_model,
        gamma_pdc=a.gamma_pdc,
        dc_ac_ratio=a.dc_ac_ratio or PVOptions().dc_ac_ratio,
        dc_loss=a.dc_loss,
        inverter_efficiency=a.inverter_efficiency,
        ac_loss=a.ac_loss,
        availability=a.pv_availability,
        cap=a.pv_cap,
        time_label=a.time_label,
        capacity_basis=a.capacity_basis,
    )
    o.validate()
    return o


def _add_net(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    p.add_argument("--pause", type=float, default=3.0, help="s between requests")


# ---------------------------------------------------------------------------
# subcommands
# ---------------------------------------------------------------------------


def _dc_ac(text: str) -> float | None:
    if text.strip().lower() == "auto":
        return None
    try:
        return float(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError("use auto or a number") from e


def _add_time_basis(q: argparse.ArgumentParser) -> None:
    """Local calendar options, as in ``dlr2gtopt``: the zone (``auto`` =
    from the plant coordinates), fixed standard time unless ``--dst on``,
    ``--utc-offset`` as an explicit fixed override."""
    q.add_argument(
        "--timezone",
        default="auto",
        help="IANA zone of the local calendar; auto = from the plant "
        "coordinates (Chile: America/Santiago) [auto]",
    )
    q.add_argument(
        "--dst",
        choices=dtz.DST_MODES,
        default="off",
        help="off: fixed standard time (Chile UTC-4, as PLP indhor and the "
        "dlr2gtopt default); on: civil time [off]",
    )
    q.add_argument(
        "--utc-offset",
        type=float,
        default=None,
        help="explicit fixed offset [h]; overrides --timezone/--dst",
    )


def _time_basis(a: argparse.Namespace, plants: list[pl.Plant]) -> float | str:
    """``local_calendar`` spec (offset or zone) of the CLI options."""
    b = dtz.resolve(
        a.timezone,
        a.dst,
        a.utc_offset,
        [p.lat for p in plants],
        [p.lon for p in plants],
    )
    logger.info("calendar: %s", b.describe())
    return b.spec


def cmd_history(a: argparse.Namespace) -> int:
    """Hourly weather at the plants (same sources/years as the DLR)."""
    plants = pl.read_inventory(Path(a.plants))
    cache = PoliteCache(Path(a.cache_dir), pause=a.pause)
    years = dhi.parse_years(a.years)
    hubs = {}
    for p in plants:
        if p.type == "wind":
            h = p.get("hub_height") if a.equipment_source == "cen" else None
            hubs[p.name] = (
                float(h)
                if h
                else float(a.hub_height or pl.eq.generic_hub_height(p.commissioning))
            )
    basis = _time_basis(a, plants)
    ratios = None
    if a.shear == "ratio":
        wrf = dwx.Explorador(cache, a.ratio_model)
        ratios = {
            p.name: src.fit_ratio_from_wrf(
                wrf, p, a.ratio_ref, hubs[p.name], basis, a.interp
            )
            for p in plants
            if p.type == "wind"
        }
    wind_source, met = a.wind_source, None
    if a.weather_source is not None:
        wind_source, met = _source_weather(a, plants, years, cache)
    w = src.plant_history(
        plants,
        hubs,
        years,
        cache=cache,
        wind_source=wind_source,
        explorador_model=a.explorador_model,
        levels=[float(x) for x in a.levels.split(",")],
        shear=a.shear,
        alpha=a.alpha,
        z0=a.z0,
        shear_ratio=ratios,
        utc_offset=basis,
        method=a.interp,
        era5_kw={"snap": a.era5_snap, "model": a.era5_model},
        met=met,
    )
    out = Path(a.output)
    w.write(out)
    if met is not None:
        met.provenance["tool"] = "re2gtopt history"
        met.provenance["wind"] = (
            f"Explorador Eolico {a.explorador_model} (levels {a.levels})"
            if wind_source == "explorador"
            else f"open-meteo {met.provenance.get('model')} 10/100 m"
        )
        met.write_provenance(out)
    print(f"{len(w.plants)} plants x {len(w.times)} h -> {a.output}")
    return 0


def _source_weather(
    a: argparse.Namespace,
    plants: list[pl.Plant],
    years: list[int],
    cache: PoliteCache,
) -> tuple[str, ws.PointWeather]:
    """``--weather-source``: the shared layer's weather at the plants.

    ``explorador``: temperature and GHI from the Explorador WRF (no DNI,
    DHI or pressure: the PV model decomposes GHI, density uses the ISA
    pressure); wind stays the Explorador ``--explorador-model`` levels.
    ``explorador-solar``: temperature, GHI, DNI and DHI from the Explorador
    Solar (hourly 2004-2016, or month x hour means); wind stays the
    Explorador Eolico levels.
    ``open-meteo``: every field, wind included, from the archive."""
    explorador = a.weather_source != "open-meteo"
    try:
        met = ws.point_weather(
            a.weather_source,
            cache,
            [(p.lat, p.lon) for p in plants],
            years,
            model=ws.model_for(a),
            climatology=a.climatology,
            stat=a.climatology_stat,
            utc_offset=ws.calendar_basis(
                a,
                None
                if a.climatology_utc_offset is not None
                else dtz.resolve(
                    a.timezone,
                    a.dst,
                    a.utc_offset,
                    [p.lat for p in plants],
                    [p.lon for p in plants],
                ),
            ),
            source_years=ws.parse_years(a.climatology_years),
            need=("temperature", "ghi")
            if explorador
            else ("temperature", "ghi", "wind_speed_10m", "wind_speed_100m"),
            height=10.0,
            method=a.interp,
            api_key=a.api_key,
            base_url=a.base_url,
            snap=a.era5_snap,
            solar=ws.solar_options(a),
        )
    except ws.WeatherSourceError as e:
        raise SystemExit(f"history: {e}") from None
    return ("explorador" if explorador else "era5"), met


def cmd_model(a: argparse.Namespace) -> int:
    """Forward model: per-unit output and calibration drivers."""
    plants = pl.read_inventory(Path(a.plants))
    w = src.read_plant_weather(Path(a.weather))
    run = md.run_model(
        plants,
        w,
        _wind_opt(a),
        _pv_opt(a),
        equipment_source=a.equipment_source,
        tolerance=a.tolerance,
        base_dir=Path(a.plants).parent,
        dc_ac_auto=a.dc_ac_ratio is None,
        dc_ac_table=a.dc_ac_table,
    )
    out = Path(a.output)
    rt.write_cf_history(run.cf, out / "cf_model.parquet")
    rt.write_cf_history(run.driver, out / "driver.parquet", "driver")
    _write_table(run.table, out / "sizing.csv")
    bad = [r["plant"] for r in run.table if not r["peak_ok"]]
    print(
        f"{len(plants)} plants -> {out} (Pmax check: {len(plants) - len(bad)} ok"
        + (f", mismatch: {', '.join(bad)})" if bad else ")")
    )
    return 0


def _nearest_k(
    p: pl.Plant, plants: list[pl.Plant], ks: dict[str, float]
) -> tuple[str, float] | None:
    cands = [
        ((q.lat - p.lat) ** 2 + (q.lon - p.lon) ** 2, q.name)
        for q in plants
        if q.type == p.type and q.name in ks
    ]
    if not cands:
        return None
    name = min(cands)[1]
    return name, ks[name]


def cmd_calibrate(a: argparse.Namespace) -> int:  # pylint: disable=too-many-locals
    """Fit each plant's conversion on observed output; calibrated history."""
    plants = pl.read_inventory(Path(a.plants))
    run_dir = Path(a.model)
    model = rt.read_cf_history(run_dir / "cf_model.parquet")
    driver = rt.read_cf_history(run_dir / "driver.parquet", "driver")
    # the CEN day-ahead forecast is uncensored (no curtailment): with
    # --calibration-target forecast it replaces the observed output
    obs = ob.read_observed(
        Path(a.observed),
        plants,
        model.times,
        utc_offset=a.obs_utc_offset,
        price_path=Path(a.prices) if a.prices else None,
        column="forecast_mwh" if a.calibration_target == "forecast" else None,
    )
    if a.calibration_target == "forecast":
        a.censoring = "naive"
        obs.curtailed = None
    fcst = (
        ob.read_observed(
            Path(a.observed),
            plants,
            model.times,
            utc_offset=a.obs_utc_offset,
            column="forecast_mwh",
        )
        if a.calibration_target == "both"
        else None
    )
    yr, mo, _ = ob.utc_calendar(model.times, _time_basis(a, plants))
    fopt = cb.FilterOptions(ceiling_quantile=a.ceiling_quantile, min_hours=a.min_hours)
    out = Path(a.output)
    calibrated = model.cf.copy()
    rows: list[dict[str, Any]] = []
    cv: dict[str, Any] = {}
    ks: dict[str, float] = {}
    pending: list[int] = []
    for j, p in enumerate(plants):
        o = obs.cf[:, j].copy()
        cur_mask = None
        if a.censoring == "mask" and obs.curtailed is not None:
            c = np.nan_to_num(obs.curtailed[:, j])
            o = np.where(np.isfinite(o), o + c, o)  # potential = obs + curtailed
        elif obs.curtailed is not None:
            cur_mask = np.nan_to_num(obs.curtailed[:, j]) > 1e-6
        price = obs.price[:, j] if obs.price is not None else None
        valid, counts = cb.valid_hours(
            o, model.cf[:, j], yr, mo, fopt, cur_mask, price, a.price_eps
        )
        method = a.calibration
        if method == "auto":
            method = "curve"
        pmax_ceiling = p.ceiling_mw / p.pmax_mw
        fit_sel = cb.fit_hours(
            a.censoring,
            o,
            model.cf[:, j],
            valid,
            ceiling=pmax_ceiling,
            unconstrained_frac=a.unconstrained_frac,
            price=price,
            price_eps=a.price_eps,
        )
        row: dict[str, Any] = {"plant": p.name, "type": p.type, **counts}
        if int(fit_sel.sum()) < a.min_hours or method == "none":
            row.update({"method": "none" if method == "none" else "fallback"})
            rows.append(row)
            pending.append(j)
            continue
        env = a.envelope_q if a.censoring == "envelope" else None
        kw: dict[str, Any] = {
            "pmax_ceiling": pmax_ceiling,
            "ceiling_quantile": a.ceiling_quantile,
            "envelope_q": env,
        }
        fit = cb.fit_calibration(
            p.name,
            p.type,
            method,
            model.cf[:, j],
            driver.cf[:, j],
            o,
            mo,
            fit_sel,
            **kw,
        )
        sc = cb.fit_calibration(
            p.name,
            p.type,
            "scalar",
            model.cf[:, j],
            driver.cf[:, j],
            o,
            mo,
            fit_sel,
            **kw,
        )
        ks[p.name] = sc.k
        calibrated[:, j] = fit.apply(model.cf[:, j], driver.cf[:, j], mo)
        naive = cb.fit_calibration(
            p.name,
            p.type,
            method,
            model.cf[:, j],
            driver.cf[:, j],
            obs.cf[:, j],
            mo,
            np.isfinite(obs.cf[:, j]),
            pmax_ceiling=pmax_ceiling,
            ceiling_quantile=a.ceiling_quantile,
        )
        flagged = ~valid & np.isfinite(obs.cf[:, j])
        row.update(fit.table_row())
        row.update(
            {
                "censoring": a.censoring,
                "fit_hours": int(fit_sel.sum()),
                "years": ",".join(
                    str(y) for y in sorted({int(y) for y in yr[fit_sel]})
                ),
                "curtailed_share_est": round(
                    cb.curtailed_share(calibrated[:, j], obs.cf[:, j], flagged), 4
                ),
                "naive_cf": round(
                    float(np.nanmean(naive.apply(model.cf[:, j], driver.cf[:, j], mo))),
                    4,
                ),
                "calibrated_cf": round(float(np.nanmean(calibrated[:, j])), 4),
                "uncalibrated_cf": round(float(np.nanmean(model.cf[:, j])), 4),
            }
        )
        if fcst is not None:
            f_ok = np.isfinite(fcst.cf[:, j]) & np.isfinite(model.cf[:, j])
            if int(f_ok.sum()) >= a.min_hours:
                ff = cb.fit_calibration(
                    p.name,
                    p.type,
                    method,
                    model.cf[:, j],
                    driver.cf[:, j],
                    fcst.cf[:, j],
                    mo,
                    f_ok,
                    pmax_ceiling=pmax_ceiling,
                    ceiling_quantile=a.ceiling_quantile,
                )
                fr = ff.table_row()
                row.update(
                    {
                        "forecast_hours": int(f_ok.sum()),
                        "forecast_k": fr.get("k"),
                        "forecast_ceiling": fr.get("fitted_ceiling"),
                        "forecast_cf": round(
                            float(
                                np.nanmean(
                                    ff.apply(model.cf[:, j], driver.cf[:, j], mo)
                                )
                            ),
                            4,
                        ),
                    }
                )
        rows.append(row)
        _json(out / "curves" / f"{p.uid}.json", fit.__dict__)
        if a.cv:
            cv[p.name] = cb.leave_one_year_out(
                p.name,
                p.type,
                ["none", "scalar", method, "quantile-map"],
                model.cf[:, j],
                driver.cf[:, j],
                o,
                yr,
                mo,
                fit_sel,
                pmax_ceiling=pmax_ceiling,
                ceiling_quantile=a.ceiling_quantile,
            )
    for j in pending:
        p = plants[j]
        if a.fallback == "nearest":
            nk = _nearest_k(p, plants, ks)
            if nk is not None:
                calibrated[:, j] = np.minimum(nk[1] * model.cf[:, j], 1.0)
                for r in rows:
                    if r["plant"] == p.name:
                        r.update({"fallback_from": nk[0], "k": round(nk[1], 4)})
    rt.write_cf_history(
        rt.CFHistory(model.times, model.plants, model.uids, calibrated),
        out / "cf_calibrated.parquet",
    )
    _write_table(rows, out / "calibration.csv")
    if cv:
        _json(out / "cross_validation.json", cv)
    print(f"{len(plants)} plants calibrated ({a.calibration}, {a.censoring}) -> {out}")
    return 0


def cmd_typical(a: argparse.Namespace) -> int:
    """Typical year aligned to the DLR typical year's source hours."""
    hist = rt.read_cf_history(Path(a.cf))
    srch = rt.read_source_hours(Path(a.dlr_typical))
    case = rt.parse_energy_case(a.case)
    aligned = rt.align(hist, srch)
    years = dhi.parse_years(a.years) if a.years else None
    plants = pl.read_inventory(Path(a.plants)) if a.plants else []
    basis = _time_basis(a, plants)
    if a.energy_target == "observed":
        if not a.observed or not a.plants:
            raise ValueError("--energy-target observed needs --observed and --plants")
        obs = ob.read_observed(
            Path(a.observed), plants, hist.times, utc_offset=a.obs_utc_offset
        )
        tgt_hist = rt.CFHistory(hist.times, hist.plants, hist.uids, obs.cf)
    else:
        tgt_hist = hist
    targets, stats = rt.energy_targets(
        tgt_hist,
        case,
        utc_offset=basis,
        years=years,
        p_method=a.p_method,
        uncertainty=a.uncertainty,
    )
    if a.energy_preserve:
        ceilings = np.nanmax(hist.cf, axis=0)
        final, fac = rt.preserve_energy(aligned, targets, ceilings, a.energy_scope)
    else:
        if not case.is_mean:
            raise ValueError(f"case {case.label} needs --energy-preserve on")
        final, fac = aligned, np.ones((1, aligned.shape[1]))
    out = Path(a.output)
    df = rt.typical_frame(hist.plants, hist.uids, final, srch)
    dty.write_frame(df, out / f"re_typical_{case.label}.{a.format}")
    rep = rt.typical_report(hist, aligned, final, targets, fac, stats, basis)
    rep.update(
        {
            "case": case.label,
            "energy_preserve": a.energy_preserve,
            "energy_scope": a.energy_scope,
            "energy_target": a.energy_target,
            "dlr_typical": str(a.dlr_typical),
            "p_method": a.p_method,
            "uncertainty": a.uncertainty,
        }
    )
    _json(out / f"re_typical_{case.label}_report.json", rep)
    print(f"{len(hist.plants)} plants, case {case.label} -> {out}")
    return 0


def _stages(a: argparse.Namespace) -> list[tuple[int, int]]:
    if a.stage_months:
        return dty.read_stage_months(Path(a.stage_months))
    return dty.stage_months(a.first_month, a.stages, a.first_stage)


def _generators(a: argparse.Namespace, plants: list[str]) -> list[str] | None:
    if not getattr(a, "plants", None):
        return None
    by = {p.name: p.generator for p in pl.read_inventory(Path(a.plants))}
    return [by.get(n, n) for n in plants]


def _emit(
    a: argparse.Namespace,
    specs: list[dty.BlockSpec],
    vals: Any,
    plants: list[str],
    uids: list[int],
) -> None:
    out = Path(a.output)
    dty.write_frame(
        bl.block_frame(specs, vals, plants, uids), out / f"{a.prefix}_blocks.{a.format}"
    )
    bl.write_generator_profiles(
        out,
        specs,
        vals,
        plants,
        uids,
        _generators(a, plants),
        prefix=a.prefix,
        fmt=a.format,
        scenarios=[int(x) for x in a.scenarios.split(",")],
    )


def cmd_to_blocks(a: argparse.Namespace) -> int:
    """RE typical year -> blocks (same hour membership as dlr2gtopt)."""
    tcf = rt.read_typical_cf(Path(a.typical))
    if a.block_map:
        sob = None
        if a.planning:
            sob = dty.stage_of_block_from_planning(
                json.loads(Path(a.planning).read_text(encoding="utf-8"))
            )
        specs = dty.blocks_from_map(
            dty.read_frame(Path(a.block_map)), sob, a.map_hour_base
        )
    elif a.hour_bands:
        specs = dty.blocks_from_bands(
            _stages(a), dty.parse_bands(a.hour_bands), a.first_block
        )
    else:
        specs = dty.blocks_chronological(_stages(a), a.block_hours, a.first_block)
    dlr_src = bl.read_dlr_source_hours(Path(a.dlr_blocks)) if a.dlr_blocks else None
    dlr_t = bl.read_dlr_source_times(Path(a.dlr_blocks)) if a.dlr_blocks else None
    vals, info = bl.block_values(
        tcf.cf,
        specs,
        a.block_stat,
        dlr_src,
        dlr_source_time=dlr_t,
        cf_history=_history_like(Path(a.cf_history), tcf.plants)
        if a.cf_history
        else None,
    )
    _emit(a, specs, vals, tcf.plants, tcf.uids)
    print(f"{info['blocks']} blocks ({a.block_stat}) -> {a.output}")
    return 0


def _history_like(path: Path, plants: list[str]) -> rt.CFHistory:
    """CF history with its plant columns in ``plants`` order."""
    h = rt.read_cf_history(path)
    missing = [p for p in plants if p not in h.plants]
    if missing:
        raise ValueError(f"{path}: no CF for {', '.join(missing)}")
    col = [h.plants.index(p) for p in plants]
    return rt.CFHistory(h.times, list(plants), [h.uids[i] for i in col], h.cf[:, col])


def cmd_repeat(a: argparse.Namespace) -> int:
    """Tile a month template over a horizon (dlr2gtopt numbering)."""
    specs, vals, plants, uids = bl.read_block_frame(Path(a.template))
    specs2, vals2 = bl.repeat(specs, vals, _stages(a), a.first_block)
    _emit(a, specs2, vals2, plants, uids)
    print(f"{len(specs2)} blocks -> {a.output}")
    return 0


def cmd_validate(a: argparse.Namespace) -> int:
    """Modelled vs observed; CF vs DLR rating cross-correlation."""
    plants = pl.read_inventory(Path(a.plants))
    model = rt.read_cf_history(Path(a.cf))
    extra = {}
    for item in a.compare or []:
        name, _, path = item.partition("=")
        extra[name] = rt.read_cf_history(Path(path))
    obs = None
    rep: dict[str, Any] = {}
    if a.observed:
        obs = ob.read_observed(
            Path(a.observed), plants, model.times, utc_offset=a.obs_utc_offset
        )
        rep["validation"] = ob.validate(
            model, obs, plants, utc_offset=_time_basis(a, plants), extra=extra
        )
    if a.rating:
        rt_t, rating = ob.read_rating(Path(a.rating))
        rep["cross_correlation"] = ob.cross_correlation(model, rt_t, rating, obs)
    _json(Path(a.output), rep)
    print(f"validation -> {a.output}")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def _block_opts(q: argparse.ArgumentParser) -> None:
    q.add_argument("--plants", help="inventory (generator names for the JSON)")
    q.add_argument("--first-month", type=int, default=1)
    q.add_argument("--stages", type=int, default=12)
    q.add_argument("--first-stage", type=int, default=1)
    q.add_argument("--stage-months", help="CSV stage,month")
    q.add_argument("--first-block", type=int, default=1)
    q.add_argument("--prefix", default="re_profile")
    q.add_argument("--format", choices=("parquet", "csv"), default="parquet")
    q.add_argument("--scenarios", default="1", help="comma list of scenario uids")
    q.add_argument("-o", "--output", required=True)


def _name_map(plants: list[pl.Plant], path: str | None) -> dict[str, str]:
    """:func:`cen.plant_key` of every CEN name -> inventory plant name."""
    out = {cen.plant_key(p.name): p.name for p in plants}
    for p in plants:
        for k in ("cen_name", "cen_key", "cen_code"):
            alias = p.get(k)
            if alias:
                out[cen.plant_key(str(alias))] = p.name
    if path:
        df = dty.read_frame(Path(path))
        for plant, alias in zip(df["plant"].astype(str), df["cen_name"].astype(str)):
            out[cen.plant_key(alias)] = plant
    return out


def cmd_inventory(a: argparse.Namespace) -> int:
    """Wind / PV inventory from CEN Infotécnica (cached, polite)."""
    cache = PoliteCache(Path(a.cache_dir), pause=a.pause)
    bbox = None
    if a.bbox:
        la0, la1, lo0, lo1 = (float(x) for x in a.bbox.split(","))
        bbox = (min(la0, la1), max(la0, la1), min(lo0, lo1), max(lo0, lo1))
    opt = cen.InventoryOptions(
        types=tuple(a.types.split(",")),
        regions=tuple(r for r in (a.regions or "").split(",") if r),
        bbox=bbox,
        names=tuple(n for n in (a.names or "").split(";") if n),
        min_mw=a.min_mw,
        equipment=a.equipment_source == "cen",
        skip_rejected=not a.keep_rejected,
    )
    rows = cen.build_inventory(cen.Infotecnica(cache, a.api), opt)
    import pandas as pd  # pylint: disable=import-outside-toplevel

    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix == ".json":
        out.write_text(
            json.dumps({"plants": rows}, indent=1, default=str), encoding="utf-8"
        )
    else:
        pd.DataFrame(rows).to_csv(out, index=False)
    cov = cen.coverage(rows)
    Path(str(out) + ".coverage.json").write_text(
        json.dumps(cov, indent=1), encoding="utf-8"
    )
    print(f"{len(rows)} plants -> {out}")
    print(json.dumps(cov, indent=1))
    return 0


def _download(cache: PoliteCache, url: str) -> Path:
    key = "cen/" + url.rsplit("/", 1)[-1]
    cache.fetch(key, url)
    return cache.cache_dir / key


def _sources(cache: PoliteCache, items: list[str], index: dict[str, str]) -> list[Path]:
    out = []
    for it in items:
        if it in index:
            out.append(_download(cache, index[it]))
        elif it.startswith("http"):
            out.append(_download(cache, it))
        else:
            out.append(Path(it))
    return out


def cmd_fetch_cen(a: argparse.Namespace) -> int:
    """Observed hourly generation / curtailment / forecast per plant, from
    CEN public files (downloaded to the cache, never to the repository)."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    plants = pl.read_inventory(Path(a.plants))
    wanted = _name_map(plants, a.name_map)
    cache = PoliteCache(Path(a.cache_dir), pause=a.pause)
    frames = []
    for path in _sources(cache, a.generation or [], cen.CEN_GEN_FILES):
        rows = cen.read_generation_xlsx(
            path, wanted, types={p.name: p.type for p in plants}
        )
        frames.append(pd.DataFrame(rows, columns=["utc", "plant", "mwh"]))
    obs = (
        pd.concat(frames).groupby(["utc", "plant"], as_index=False).sum()
        if frames
        else pd.DataFrame(columns=["utc", "plant", "mwh"])
    )
    for col, items, reader in (
        ("curtailed_mwh", a.curtailment, cen.read_curtailment_xlsx),
        ("forecast_mwh", a.forecast, None),
    ):
        parts = []
        for path in _sources(cache, items or [], {}):
            if reader is not None:
                rows = reader(path, wanted)
            else:
                df = dty.read_frame(path)
                rows = cen.parse_long_rows(
                    [list(df.columns)] + df.astype(object).values.tolist(), wanted
                )
            parts.append(pd.DataFrame(rows, columns=["utc", "plant", col]))
        if parts:
            extra = pd.concat(parts).groupby(["utc", "plant"], as_index=False).sum()
            obs = obs.merge(extra, on=["utc", "plant"], how="outer")
    obs["time"] = pd.to_datetime(obs["utc"]) + pd.Timedelta(hours=a.utc_offset)
    obs = obs.drop(columns="utc").sort_values(["plant", "time"])
    missing = sorted({p.name for p in plants} - set(obs["plant"]))
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    dty.write_frame(obs.reset_index(drop=True), out)
    print(f"{len(obs)} rows, {obs['plant'].nunique()} plants -> {out}")
    if missing:
        print("no CEN rows for: " + ", ".join(missing) + " (use --name-map)")
    return 0


def make_parser() -> argparse.ArgumentParser:  # pylint: disable=too-many-statements
    """Argument parser of the ``re2gtopt`` console script."""
    p = argparse.ArgumentParser(
        prog="re2gtopt",
        description="Wind / PV plant profiles (GeneratorProfile) consistent with "
        "the dlr2gtopt DLR typical year.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("inventory", help="wind/PV inventory from CEN Infotécnica")
    i.add_argument("--types", default="wind,pv")
    i.add_argument("--regions", help="comma-separated region names")
    i.add_argument("--bbox", help="lat0,lat1,lon0,lon1")
    i.add_argument("--names", help="semicolon-separated plant names")
    i.add_argument("--min-mw", type=float, default=0.0)
    i.add_argument("--equipment-source", choices=md.EQUIPMENT_SOURCES, default="cen")
    i.add_argument(
        "--keep-rejected",
        action="store_true",
        help="also use ficha rows certified 'Rechazado'",
    )
    i.add_argument("--api", default=cen.INFOTECNICA_API)
    i.add_argument("-o", "--output", required=True, help=".csv or .json")
    _add_net(i)
    i.set_defaults(func=cmd_inventory)

    f = sub.add_parser("fetch-cen", help="CEN hourly generation/curtailment/forecast")
    f.add_argument("--plants", required=True)
    f.add_argument(
        "--generation",
        nargs="*",
        help=f"workbooks, URLs or keys {sorted(cen.CEN_GEN_FILES)}",
    )
    f.add_argument("--curtailment", nargs="*", help="monthly Reducciones xlsx/URLs")
    f.add_argument("--forecast", nargs="*", help="long Fecha/Nombre/Valor tables")
    f.add_argument("--name-map", help="CSV plant,cen_name")
    f.add_argument(
        "--utc-offset",
        type=float,
        default=0.0,
        help="output time = UTC + offset (default UTC, as --obs-utc-offset 0 expects)",
    )
    f.add_argument("-o", "--output", required=True)
    _add_net(f)
    f.set_defaults(func=cmd_fetch_cen)

    h = sub.add_parser("history", help="hourly weather at the plants")
    h.add_argument("--plants", required=True)
    h.add_argument("--years", required=True, help="e.g. 1998-2017 (as the DLR)")
    h.add_argument("--wind-source", choices=src.WIND_SOURCES, default="explorador")
    h.add_argument("--explorador-model", default="recon")
    h.add_argument(
        "--levels",
        default="10,20,40,60,80,100,120,140",
        help="Explorador heights [m] (recon has 10..140)",
    )
    h.add_argument(
        "--shear",
        choices=SHEAR_METHODS,
        default="none",
        help="none = interpolate between the bracketing levels (default)",
    )
    h.add_argument(
        "--ratio-model", default="wrf2010", help="WRF year for --shear ratio"
    )
    h.add_argument("--ratio-ref", type=float, default=20.0)
    h.add_argument("--alpha", type=float, default=1 / 7)
    h.add_argument("--z0", type=float, default=0.03)
    h.add_argument("--hub-height", type=float, help="override the generic hub")
    h.add_argument("--equipment-source", choices=md.EQUIPMENT_SOURCES, default="cen")
    h.add_argument("--interp", choices=dwx.INTERP_METHODS, default="bilinear")
    _add_time_basis(h)
    h.add_argument("--era5-snap", type=float, default=0.0)
    h.add_argument("--era5-model", default="era5")
    h.add_argument("-o", "--output", required=True)
    ws.add_weather_source_arguments(h)
    _add_net(h)
    h.set_defaults(func=cmd_history)

    m = sub.add_parser("model", help="forward model per plant")
    m.add_argument("--plants", required=True)
    m.add_argument("--weather", required=True)
    m.add_argument("--equipment-source", choices=md.EQUIPMENT_SOURCES, default="cen")
    m.add_argument("--tolerance", type=float, default=0.05, help="Pmax check")
    m.add_argument("-o", "--output", required=True)
    _add_wind(m)
    _add_pv(m)
    m.set_defaults(func=cmd_model)

    c = sub.add_parser("calibrate", help="fit plant conversions on observed output")
    c.add_argument("--plants", required=True)
    c.add_argument("--model", required=True, help="directory of `re2gtopt model`")
    c.add_argument("--observed", required=True, help="time,plant,mwh|cf[,curtailed]")
    c.add_argument("--prices", help="time,plant,price (zonal marginal cost)")
    c.add_argument("--price-eps", type=float, default=1.0)
    c.add_argument("--obs-utc-offset", type=float, default=0.0)
    _add_time_basis(c)
    c.add_argument("--calibration", choices=CALIBRATION_CHOICES, default="auto")
    c.add_argument("--censoring", choices=cb.CENSORINGS, default="filter")
    c.add_argument(
        "--calibration-target",
        choices=("observed", "forecast", "both"),
        default="observed",
        help="fit on observed output, on the CEN day-ahead forecast "
        "(forecast_mwh, uncensored), or on observed and report the "
        "forecast fit beside it",
    )
    c.add_argument("--envelope-q", type=float, default=0.75)
    c.add_argument("--unconstrained-frac", type=float, default=0.5)
    c.add_argument("--ceiling-quantile", type=float, default=0.995)
    c.add_argument("--min-hours", type=int, default=2000)
    c.add_argument("--fallback", choices=FALLBACKS, default="nearest")
    c.add_argument("--cv", type=_onoff, default=True, help="leave-one-year-out on|off")
    c.add_argument("-o", "--output", required=True)
    c.set_defaults(func=cmd_calibrate)

    t = sub.add_parser("typical", help="typical year on the DLR source hours")
    t.add_argument("--cf", required=True, help="CF history (calibrated or model)")
    t.add_argument("--dlr-typical", required=True, help="dlr2gtopt typical_<case>")
    t.add_argument("--case", default="mean", help="mean | p50 | pNN | quantile:<q>")
    t.add_argument("--energy-preserve", type=_onoff, default=True, help="on|off")
    t.add_argument("--energy-scope", choices=rt.ENERGY_SCOPES, default="monthly")
    t.add_argument("--energy-target", choices=ENERGY_TARGETS, default="history")
    t.add_argument("--p-method", choices=rt.P_METHODS, default="empirical")
    t.add_argument("--uncertainty", type=float, default=0.0)
    t.add_argument("--years", help="history years for the targets [all]")
    t.add_argument("--observed")
    t.add_argument("--plants")
    t.add_argument("--obs-utc-offset", type=float, default=0.0)
    _add_time_basis(t)
    t.add_argument("--format", choices=("parquet", "csv"), default="parquet")
    t.add_argument("-o", "--output", required=True)
    t.set_defaults(func=cmd_typical)

    b = sub.add_parser("to-blocks", help="typical year -> blocks + GeneratorProfile")
    b.add_argument("typical", help="re_typical_<case>.parquet|csv")
    b.add_argument("--block-map", help="hour->block membership (BlockHourMap)")
    b.add_argument("--planning", help="planning JSON (block -> stage)")
    b.add_argument("--map-hour-base", type=int, choices=(0, 1), default=1)
    b.add_argument("--hour-bands", help="e.g. 1-7,8-18,19-24")
    b.add_argument("--block-hours", type=int, default=1)
    b.add_argument("--block-stat", default="mean", help=bl.BLOCK_STATS_HELP)
    b.add_argument("--dlr-blocks", help="dlr2gtopt <prefix>_blocks (for dlr-hour)")
    b.add_argument(
        "--cf-history",
        help="dlr-hour: CF history; take the CF of the DLR block's exact "
        "source_time (needed for DLR blocks built --history)",
    )
    _block_opts(b)
    b.set_defaults(func=cmd_to_blocks)

    r = sub.add_parser("repeat", help="tile a month template over a horizon")
    r.add_argument("template", help="<prefix>_blocks table from to-blocks")
    _block_opts(r)
    r.set_defaults(func=cmd_repeat)

    v = sub.add_parser("validate", help="model vs observed; CF vs DLR rating")
    v.add_argument("--plants", required=True)
    v.add_argument("--cf", required=True)
    v.add_argument("--compare", action="append", help="name=cf_history (variants)")
    v.add_argument("--observed")
    v.add_argument("--obs-utc-offset", type=float, default=0.0)
    _add_time_basis(v)
    v.add_argument("--rating", help="time,line_rating_mw (DLR history rating)")
    v.add_argument("-o", "--output", required=True)
    v.set_defaults(func=cmd_validate)
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
        print(f"re2gtopt: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
