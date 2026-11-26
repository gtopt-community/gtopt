# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Forward model of every plant over a weather history.

For each plant: size the equipment to the reported Pmax
(:mod:`re2gtopt._plants`), run the wind or PV model, and keep the
*driver* series used by the calibration (density-normalised hub speed
for wind, DC output before the inverter for PV) and the Pmax
consistency check (modelled peak before availability and cap).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from re2gtopt import _equipment as eq
from re2gtopt import _plants as pl
from re2gtopt._pv import PVOptions, pv_plant_cf
from re2gtopt._sources import PlantWeather
from re2gtopt._typical import CFHistory
from re2gtopt._wind import (
    PowerCurve,
    WindOptions,
    air_density,
    density_normalise,
    farm_curve,
    smooth_curve,
    atlite_smooth,
    turbine_curve,
    wind_farm_cf,
)

logger = logging.getLogger("re2gtopt")
FloatArray = npt.NDArray[np.float64]
EQUIPMENT_SOURCES = ("cen", "generic")


@dataclass
class ModelRun:
    """CF and driver histories plus the per-plant sizing / peak table."""

    cf: CFHistory
    driver: CFHistory
    table: list[dict[str, Any]] = field(default_factory=list)


def resolve_curve(
    spec: str, opt: WindOptions, base_dir: Path | None = None
) -> PowerCurve:
    """The farm curve of a turbine spec (``oedb:<type>`` or a ``_wind``
    spec), smoothed as ``opt.smoothing`` says."""
    if spec.startswith("oedb:"):
        curve = eq.oedb_curve(spec[5:], opt.cut_out)
    else:
        curve = turbine_curve(
            spec, cut_in=opt.cut_in, cut_out=opt.cut_out, base_dir=base_dir
        )
    if opt.smoothing == "none":
        return curve
    if opt.smoothing == "ninja":
        return smooth_curve(curve, 0.6, 0.2)
    if opt.smoothing == "atlite":
        return atlite_smooth(curve)
    return smooth_curve(curve, opt.smoothing_a, opt.smoothing_b)


def run_model(
    plants: list[pl.Plant],
    weather: PlantWeather,
    wind_opt: WindOptions,
    pv_opt: PVOptions,
    *,
    equipment_source: str = "cen",
    tolerance: float = 0.05,
    base_dir: Path | None = None,
    dc_ac_auto: bool = True,
    dc_ac_table: tuple[tuple[int, float], ...] = eq.GENERIC_DC_AC,
) -> ModelRun:
    """Forward model of every plant on the weather's hours.  The generic
    PV DC/AC is ``pv_opt.dc_ac_ratio`` or, with ``dc_ac_auto``, the
    ``dc_ac_table`` value of the plant's commissioning year."""
    if equipment_source not in EQUIPMENT_SOURCES:
        raise ValueError(f"equipment source must be one of {EQUIPMENT_SOURCES}")
    n_t = len(weather.times)
    cf = np.full((n_t, len(plants)), np.nan)
    drv = np.full((n_t, len(plants)), np.nan)
    table: list[dict[str, Any]] = []
    for j, p in enumerate(plants):
        if p.name not in weather.plants:
            raise ValueError(f"plant {p.name} has no weather")
        elev = weather.elevation.get(p.name, p.elevation or 0.0)
        t_air = weather.column("temperature", p.name)
        pres = weather.column("pressure", p.name)
        if pres is not None and not np.isfinite(pres).all():
            pres = None
        if p.type == "wind":
            # the turbine comes from the sizing (CEN model or generic), never
            # as a free-text override of the options
            wo = wind_opt.with_overrides(
                {k: v for k, v in p.attrs.items() if k != "turbine"}
            )
            v = weather.column("wind_speed_hub", p.name)
            if v is None or not np.isfinite(v).any():
                raise ValueError(f"plant {p.name}: no hub-height wind")
            sz = pl.size_wind(
                p, wo.turbine, wo.electrical_loss, tolerance, equipment_source
            )
            if sz.turbine == "auto":
                sz.turbine = eq.auto_turbine(p.commissioning, float(np.nanmean(v)))
            curve = resolve_curve(str(sz.turbine), wo, base_dir)
            res = wind_farm_cf(
                v,
                wo,
                temperature_c=t_air,
                pressure_pa=pres,
                elevation_m=elev,
                curve=curve,
                installed_ratio=sz.installed_ratio,
                pmax_basis=p.pmax_basis,
            )
            res.cf = np.minimum(res.cf, sz.ac_limit)
            cf[:, j] = res.cf
            rho = (
                air_density(t_air, pres, elev)
                if t_air is not None
                else air_density(15.0)
            )
            drv[:, j] = density_normalise(v, rho if wo.density != "none" else 1.225)
            row = pl.check_peak(
                sz, min(res.diagnostics["peak_pu"], sz.ac_limit), tolerance
            )
            row.update(
                {
                    "mean_cf": round(float(np.nanmean(res.cf)), 4),
                    "mean_hub_speed": round(res.diagnostics["mean_hub_speed"], 2),
                    "mean_rho": round(res.diagnostics["mean_rho"], 3),
                    "wake_deficit": round(res.wake_deficit, 4),
                    "cap": wo.cap,
                }
            )
        else:
            sz = pl.size_pv(
                p,
                None if dc_ac_auto else pv_opt.dc_ac_ratio,
                tolerance,
                equipment_source,
                dc_ac_table,
            )
            over: dict[str, Any] = dict(p.attrs)
            over.update(
                {
                    "dc_ac_ratio": sz.dc_ac_ratio,
                    "gamma_pdc": sz.gamma_pdc,
                    "ac_limit": sz.ac_limit,
                }
            )
            po = pv_opt.with_overrides(over)
            ghi = weather.column("ghi", p.name)
            if ghi is None:
                raise ValueError(f"plant {p.name}: no GHI")
            dni = weather.column("dni", p.name)
            dhi = weather.column("dhi", p.name)
            res_pv = pv_plant_cf(
                weather.times,
                np.nan_to_num(ghi),
                p.lat,
                p.lon,
                po,
                elevation_m=elev,
                temperature_c=t_air,
                wind_speed=weather.column("wind_speed_10m", p.name),
                dni=None if dni is None else np.nan_to_num(dni),
                dhi=None if dhi is None else np.nan_to_num(dhi),
                pressure_pa=pres,
            )
            cf[:, j] = res_pv.cf
            drv[:, j] = res_pv.dc
            row = pl.check_peak(sz, res_pv.diagnostics["peak_pu"], tolerance)
            row.update(
                {
                    "mean_cf": round(float(np.mean(res_pv.cf)), 4),
                    "clipped_hours": res_pv.diagnostics["clipped_hours"],
                    "tracking": po.tracking,
                    "cap": po.cap,
                }
            )
        for w in sz.warnings:
            logger.warning("plant %s: %s", p.name, w)
        table.append(row)
    names = [p.name for p in plants]
    uids = [p.uid for p in plants]
    return ModelRun(
        CFHistory(list(weather.times), names, uids, cf),
        CFHistory(list(weather.times), names, uids, drv),
        table,
    )


__all__ = ["EQUIPMENT_SOURCES", "ModelRun", "farm_curve", "resolve_curve", "run_model"]
