# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""PV plant model on pvlib: irradiance -> per-unit AC output.

Pipeline (every step an option, defaults in :class:`PVOptions`):

1. **Sun position** (pvlib SPA) at the representative instant of each
   hour: ``start`` (default; label t = mean over [t, t+1], the DLR
   history / CEN convention, ERA5 radiation shifted accordingly) at
   ``t + 30 min``; ``end`` at ``t − 30 min``; ``instant`` at ``t``.
2. **Decomposition** GHI -> DNI + DHI: ``erbs`` (Erbs et al. 1982),
   ``erbs-driesse`` (continuous Erbs, Driesse et al. 2024), ``disc``
   (Maxwell 1987), ``dirint`` (Perez et al. 1992); ``source`` uses the
   DNI/DHI of the weather source when it has them.
3. **Plane of array** (transposition): ``perez`` (Perez et al. 1990),
   ``haydavies``, ``isotropic``; albedo 0.25 (desert).
4. **Mounting**: ``fixed`` (tilt default ``|lat|``, facing the equator)
   or ``single-axis`` (N-S axis, ±``max_angle``, with backtracking at
   ``gcr``; Lorenzo et al. 2011) -- most Chilean utility PV tracks.
5. **Incidence-angle loss**: ``ashrae`` (b0 = 0.05) on the beam, or none.
6. **Soiling**: a fraction of the plane-of-array irradiance.  Atacama
   dust is severe (≈ 0.2-0.6 %/day uncleaned); with regular cleaning the
   annual loss is a few percent (default 3 %).
7. **Cell temperature**: ``faiman`` (U0 = 25, U1 = 6.84), ``sapm`` (open
   rack, glass/polymer), ``pvsyst``, or ``none`` (25 °C).
8. **DC power**: PVWatts ``P_dc = P_dc0 · E/1000 · (1 + γ (T_c − 25))``,
   γ = −0.35 %/K, with ``P_dc0 = dc_ac_ratio`` p.u. of AC nameplate,
   times ``1 − dc_loss`` (mismatch, wiring, LID, nameplate; 7 %).
9. **Inverter**: PVWatts efficiency curve (η_nom 0.98) and **clipping**
   at the AC nameplate: the DC/AC ratio (ILR, 1.3 default) sets how
   often the plant sits at its maximum output.
10. **Availability**, **AC losses** (transformer, collector), **cap**.

Per-unit of AC nameplate (``capacity_basis = ac``) or of DC nameplate
(``dc``: then the output never exceeds ``1 / dc_ac_ratio``).

pvlib is an optional dependency (``pip install pvlib``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

DECOMPOSITIONS = ("dirint", "erbs", "erbs-driesse", "disc", "source")
TRANSPOSITIONS = ("perez", "haydavies", "isotropic")
TRACKINGS = ("single-axis", "fixed")
TEMPERATURE_MODELS = ("faiman", "sapm", "pvsyst", "none")
IAM_MODELS = ("ashrae", "none")
TIME_LABELS = ("start", "end", "instant")
CAPACITY_BASES = ("ac", "dc")


def require_pvlib() -> Any:
    """Import pvlib or raise a clear error."""
    try:
        import pvlib  # pylint: disable=import-outside-toplevel
    except ImportError as exc:  # pragma: no cover - exercised without pvlib
        raise RuntimeError(
            "the PV model needs pvlib: pip install pvlib (optional dependency)"
        ) from exc
    return pvlib


@dataclass(frozen=True)
class PVOptions:
    """Every assumption of the PV model, with its documented default."""

    tracking: str = "single-axis"
    tilt: float | None = None  # fixed: default |latitude|
    azimuth: float | None = None  # fixed: default facing the equator
    axis_azimuth: float = 0.0  # single-axis: N-S axis
    max_angle: float = 60.0
    backtrack: bool = True
    gcr: float = 0.35
    decomposition: str = "dirint"
    transposition: str = "perez"
    albedo: float = 0.25
    iam: str = "ashrae"
    soiling: float = 0.03
    temperature_model: str = "faiman"
    gamma_pdc: float = -0.0035
    dc_ac_ratio: float = 1.3
    dc_loss: float = 0.07
    inverter_efficiency: float = 0.98
    ac_loss: float = 0.01
    availability: float = 0.98
    cap: float | None = None
    ac_limit: float = 1.0  # AC ceiling, p.u. of the reported Pmax
    time_label: str = "start"
    capacity_basis: str = "ac"

    def validate(self) -> None:
        """Raise on an invalid option."""
        for name, val, allowed in (
            ("tracking", self.tracking, TRACKINGS),
            ("decomposition", self.decomposition, DECOMPOSITIONS),
            ("transposition", self.transposition, TRANSPOSITIONS),
            ("temperature_model", self.temperature_model, TEMPERATURE_MODELS),
            ("iam", self.iam, IAM_MODELS),
            ("time_label", self.time_label, TIME_LABELS),
            ("capacity_basis", self.capacity_basis, CAPACITY_BASES),
        ):
            if val not in allowed:
                raise ValueError(f"pv {name} must be one of {allowed}")
        for name, frac in (
            ("soiling", self.soiling),
            ("dc_loss", self.dc_loss),
            ("ac_loss", self.ac_loss),
        ):
            if not 0.0 <= frac < 1.0:
                raise ValueError(f"pv {name} must be in [0, 1)")
        if self.dc_ac_ratio <= 0:
            raise ValueError("pv dc_ac_ratio must be > 0")
        if not 0.0 < self.availability <= 1.0:
            raise ValueError("pv availability must be in (0, 1]")
        if not 0.0 < self.inverter_efficiency <= 1.0:
            raise ValueError("pv inverter_efficiency must be in (0, 1]")
        if not 0.0 < self.gcr < 1.0:
            raise ValueError("pv gcr must be in (0, 1)")
        if self.cap is not None and not 0.0 < self.cap <= 1.0:
            raise ValueError("pv cap must be in (0, 1]")
        if not 0.0 < self.ac_limit <= 1.0:
            raise ValueError("pv ac_limit must be in (0, 1]")

    def with_overrides(self, over: Mapping[str, object]) -> PVOptions:
        """A copy with per-plant overrides (unknown keys ignored)."""
        known = {k: v for k, v in over.items() if k in _PV_FIELDS and v is not None}
        if "backtrack" in known and isinstance(known["backtrack"], str):
            known["backtrack"] = known["backtrack"].lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
        return replace(self, **known)  # type: ignore[arg-type]


_PV_FIELDS = frozenset(f.name for f in fields(PVOptions))


@dataclass
class PVResult:
    """Per-unit AC output and intermediate series."""

    cf: FloatArray
    poa: FloatArray
    cell_temperature: FloatArray
    dc: FloatArray
    diagnostics: dict[str, float]


def _sun_times(times: Sequence[datetime], label: str) -> Any:
    import pandas as pd  # pylint: disable=import-outside-toplevel

    idx = pd.DatetimeIndex(pd.to_datetime(list(times)), tz="UTC")
    if label == "end":
        return idx - pd.Timedelta(minutes=30)
    if label == "start":
        return idx + pd.Timedelta(minutes=30)
    return idx


def pv_plant_cf(
    times: Sequence[datetime],
    ghi: FloatArray,
    lat: float,
    lon: float,
    opt: PVOptions,
    *,
    elevation_m: float = 0.0,
    temperature_c: FloatArray | None = None,
    wind_speed: FloatArray | None = None,
    dni: FloatArray | None = None,
    dhi: FloatArray | None = None,
    pressure_pa: FloatArray | None = None,
    scale: float = 1.0,
) -> PVResult:
    """Per-unit output of a PV plant from hourly (UTC) irradiance.

    ``scale`` multiplies the irradiance (the bias-correction hook).
    """
    opt.validate()
    pvlib = require_pvlib()
    import pandas as pd  # pylint: disable=import-outside-toplevel

    n = len(times)
    t_sun = _sun_times(times, opt.time_label)
    ghi_s = pd.Series(np.maximum(np.asarray(ghi, dtype=float), 0.0) * scale, t_sun)
    t_air = (
        np.full(n, 20.0) if temperature_c is None else np.asarray(temperature_c, float)
    )
    wsp = np.full(n, 1.0) if wind_speed is None else np.asarray(wind_speed, float)
    pres = (
        pvlib.atmosphere.alt2pres(elevation_m)
        if pressure_pa is None
        else np.asarray(pressure_pa, float)
    )
    sp = pvlib.solarposition.get_solarposition(
        t_sun, lat, lon, altitude=elevation_m, pressure=pres, temperature=t_air
    )
    zen = sp["apparent_zenith"]
    dni_extra = pvlib.irradiance.get_extra_radiation(t_sun)

    # decomposition
    if opt.decomposition == "source":
        if dni is None or dhi is None:
            raise ValueError("pv decomposition 'source' needs DNI and DHI")
        dni_s = pd.Series(np.maximum(dni, 0.0) * scale, t_sun)
        dhi_s = pd.Series(np.maximum(dhi, 0.0) * scale, t_sun)
    elif opt.decomposition == "erbs":
        e = pvlib.irradiance.erbs(ghi_s, sp["zenith"], t_sun)
        dni_s, dhi_s = e["dni"], e["dhi"]
    elif opt.decomposition == "erbs-driesse":
        e = pvlib.irradiance.erbs_driesse(ghi_s, sp["zenith"], t_sun)
        dni_s, dhi_s = e["dni"], e["dhi"]
    elif opt.decomposition == "disc":
        dni_s = pvlib.irradiance.disc(ghi_s, sp["zenith"], t_sun, pres)["dni"]
        dhi_s = ghi_s - dni_s * np.cos(np.radians(sp["zenith"]))
    else:  # dirint
        dni_s = pvlib.irradiance.dirint(
            ghi_s, sp["zenith"], t_sun, pressure=pres, temp_dew=None
        ).fillna(0.0)
        dhi_s = ghi_s - dni_s * np.cos(np.radians(sp["zenith"]))
    dni_s = dni_s.fillna(0.0).clip(lower=0.0)
    dhi_s = dhi_s.fillna(0.0).clip(lower=0.0)
    night = zen >= 90.0
    dni_s[night] = 0.0

    # mounting
    if opt.tracking == "single-axis":
        trk = pvlib.tracking.singleaxis(
            zen,
            sp["azimuth"],
            axis_tilt=0.0,
            axis_azimuth=opt.axis_azimuth,
            max_angle=opt.max_angle,
            backtrack=opt.backtrack,
            gcr=opt.gcr,
        )
        tilt = trk["surface_tilt"].fillna(0.0)
        azim = trk["surface_azimuth"].fillna(0.0)
    else:
        tilt = pd.Series(abs(lat) if opt.tilt is None else opt.tilt, t_sun)
        default_az = 0.0 if lat < 0 else 180.0
        azim = pd.Series(default_az if opt.azimuth is None else opt.azimuth, t_sun)
    am = pvlib.atmosphere.get_relative_airmass(zen)
    poa = pvlib.irradiance.get_total_irradiance(
        tilt,
        azim,
        zen,
        sp["azimuth"],
        dni_s,
        ghi_s,
        dhi_s,
        dni_extra=dni_extra,
        airmass=am,
        albedo=opt.albedo,
        model=opt.transposition,
    ).fillna(0.0)
    beam = poa["poa_direct"].clip(lower=0.0)
    if opt.iam == "ashrae":
        aoi = pvlib.irradiance.aoi(tilt, azim, zen, sp["azimuth"])
        beam = beam * pvlib.iam.ashrae(aoi, b=0.05).fillna(0.0)
    eff = (beam + poa["poa_diffuse"].clip(lower=0.0)) * (1.0 - opt.soiling)
    poa_global = poa["poa_global"].clip(lower=0.0)

    # cell temperature
    if opt.temperature_model == "faiman":
        tc = pvlib.temperature.faiman(poa_global, t_air, wsp, u0=25.0, u1=6.84)
    elif opt.temperature_model == "sapm":
        prm = pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS["sapm"][
            "open_rack_glass_polymer"
        ]
        tc = pvlib.temperature.sapm_cell(poa_global, t_air, wsp, **prm)
    elif opt.temperature_model == "pvsyst":
        tc = pvlib.temperature.pvsyst_cell(poa_global, t_air, wsp)
    else:
        tc = pd.Series(25.0, t_sun)

    pdc0 = opt.dc_ac_ratio  # p.u. of AC nameplate
    dc = pvlib.pvsystem.pvwatts_dc(eff, tc, pdc0, opt.gamma_pdc) * (1.0 - opt.dc_loss)
    dc = dc.clip(lower=0.0)
    ac = pvlib.inverter.pvwatts(
        dc, pdc0=1.0 / opt.inverter_efficiency, eta_inv_nom=opt.inverter_efficiency
    )
    # clipping exactly at the AC ceiling (the reported Pmax, or less when
    # the inverters / injection limit are smaller)
    ac = np.clip(np.asarray(ac, dtype=float), 0.0, opt.ac_limit)
    peak_pu = float(ac.max()) if ac.size else 0.0
    ac = ac * opt.availability * (1.0 - opt.ac_loss)
    uncapped = float(np.max(ac)) if ac.size else 0.0
    if opt.cap is not None:
        ac = np.minimum(ac, opt.cap)
    clipped = float(
        np.sum(np.asarray(dc) * opt.inverter_efficiency >= opt.ac_limit - 1e-9)
    )
    if opt.capacity_basis == "dc":
        ac = ac / opt.dc_ac_ratio
    diag = {
        "uncapped_max": uncapped,
        "peak_pu": peak_pu,
        "clipped_hours": clipped,
        "mean_poa": float(np.mean(poa_global)) if n else 0.0,
        "mean_cell_temperature": float(np.mean(tc)) if n else 0.0,
    }
    return PVResult(
        np.clip(ac, 0.0, 1.0),
        np.asarray(poa_global, dtype=float),
        np.asarray(tc, dtype=float),
        np.asarray(dc, dtype=float),
        diag,
    )
