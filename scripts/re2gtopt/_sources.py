# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Weather at plant points, from the same sources, history and
interpolation as ``dlr2gtopt``'s DLR history.

* **Wind** -- Explorador Eólico (``recon`` 1980-2017 reconstruction or a
  WRF hindcast), bilinear over the four surrounding 0.01° nodes
  (``dlr2gtopt._weather.EXPLORADOR_GRID.stencil``, the DLR code), speed
  interpolated per height, direction through u/v.  Hub height from the
  available levels (``_wind.shear_extrapolate``), optionally with a
  month × hour shear ratio fitted on a multi-height WRF year
  (``--shear ratio``).  Or ERA5 (10 m and 100 m, fitted shear).
* **Temperature, pressure, irradiance** -- ERA5 via the Open-Meteo
  archive API (CC BY 4.0; non-commercial free tier), one request per
  year and point (or ERA5 cell with ``snap``), cached, ``pause`` apart,
  exactly like ``dlr2gtopt history``.  Radiation (GHI, DNI, DHI) is the
  mean over the preceding hour at Open-Meteo; it is moved one hour
  earlier (``rad_lead = 1``, as ``dlr2gtopt``'s ``ghi_lead``) so the
  label t means [t, t+1], the hour-start convention of the DLR history
  and of CEN hourly generation.

The output is a long table (UTC hours × plants), ``time, plant, <fields>``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from dlr2gtopt import _history as dhi
from dlr2gtopt import _weather as dwx
from dlr2gtopt._towers import PoliteCache
from gtopt_shared.weather_source import PointWeather
from re2gtopt._plants import Plant

logger = logging.getLogger("re2gtopt")

FloatArray = npt.NDArray[np.float64]
ERA5_RE_VARS = (
    "temperature_2m",
    "surface_pressure",
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
    "wind_speed_10m",
    "wind_speed_100m",
    "wind_direction_100m",
)
WIND_SOURCES = ("explorador", "era5")
FIELDS = (
    "wind_speed_hub",
    "wind_direction",
    "temperature",
    "pressure",
    "ghi",
    "dni",
    "dhi",
    "wind_speed_10m",
)


@dataclass
class PlantWeather:
    """Hourly weather at plants: field -> (hours, plants), UTC."""

    times: list[datetime]
    plants: list[str]
    data: dict[str, FloatArray] = field(default_factory=dict)
    elevation: dict[str, float] = field(default_factory=dict)

    def write(self, path: Path) -> None:
        """Long table ``time, plant, <fields>``."""
        import pandas as pd  # pylint: disable=import-outside-toplevel

        n_t, n_p = len(self.times), len(self.plants)
        cols: dict[str, Any] = {
            "time": np.repeat(np.array(self.times, dtype="datetime64[s]"), n_p),
            "plant": np.tile(self.plants, n_t),
            "elevation": np.tile(
                [self.elevation.get(p, np.nan) for p in self.plants], n_t
            ),
        }
        for k in FIELDS:
            if k in self.data:
                cols[k] = self.data[k].reshape(-1)
        df = pd.DataFrame(cols)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".parquet":
            df.to_parquet(path, index=False)
        else:
            df.to_csv(path, index=False, float_format="%.4f")

    def column(self, fld: str, plant: str) -> FloatArray | None:
        """One plant's series of a field (None if absent)."""
        if fld not in self.data:
            return None
        return self.data[fld][:, self.plants.index(plant)]


def read_plant_weather(path: Path) -> PlantWeather:
    """Read a table written by :meth:`PlantWeather.write`."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"])
    plants = list(dict.fromkeys(df["plant"].astype(str)))
    data: dict[str, FloatArray] = {}
    t_index = pd.DatetimeIndex(sorted(df["time"].unique()))
    times: list[datetime] = [t.to_pydatetime() for t in t_index]
    df["plant"] = df["plant"].astype(str)
    for k in FIELDS:
        if k not in df.columns:
            continue
        wide = df.pivot(index="time", columns="plant", values=k)
        wide = wide.reindex(index=t_index, columns=plants)
        # a writable copy: under pandas copy-on-write ``to_numpy`` returns a
        # read-only view, and PlantWeather fields are edited in place
        data[k] = np.array(wide.to_numpy(dtype=float), dtype=float, copy=True)
    elev: dict[str, float] = {}
    if "elevation" in df.columns:
        for p, e in df.groupby("plant")["elevation"].first().items():
            if np.isfinite(e):
                elev[str(p)] = float(e)
    return PlantWeather(times, plants, data, elev)


# ---------------------------------------------------------------------------
# Explorador wind at plants
# ---------------------------------------------------------------------------


def explorador_levels(
    client: dwx.Explorador,
    lat: float,
    lon: float,
    heights: Sequence[float],
    method: str = "bilinear",
) -> tuple[list[datetime], dict[float, FloatArray], FloatArray]:
    """Bilinear (DLR code) wind speed per height and the direction at the
    highest level, at one point."""
    grid = dwx.EXPLORADOR_GRID
    times: list[datetime] = []
    spd: dict[float, FloatArray] = {}
    u_acc: FloatArray | None = None
    v_acc: FloatArray | None = None
    for node, w in grid.stencil(lat, lon, method):
        la, lo = grid.node_coords(node)
        times, s_lv, d_lv = dwx.explorador_wind(client, la, lo, list(heights))
        top = max(s_lv)
        for h, a in s_lv.items():
            spd[h] = spd.get(h, np.zeros_like(a)) + w * a
        u, v = dwx.uv_from_dir(s_lv[top], d_lv[top])
        u_acc = w * u if u_acc is None else u_acc + w * u
        v_acc = w * v if v_acc is None else v_acc + w * v
    assert u_acc is not None and v_acc is not None
    return times, spd, dwx.dir_from_uv(u_acc, v_acc)


# ---------------------------------------------------------------------------
# ERA5 at plants (Open-Meteo archive)
# ---------------------------------------------------------------------------


def era5_re_url(
    lat: float,
    lon: float,
    year: int,
    *,
    model: str = "era5",
    api_key: str | None = None,
    base_url: str | None = None,
) -> str:
    """Open-Meteo archive URL of one point and one calendar year."""
    base = base_url or (dhi.ERA5_CUSTOMER_URL if api_key else dhi.ERA5_URL)
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "hourly": ",".join(ERA5_RE_VARS),
        "models": model,
        "wind_speed_unit": "ms",
        "timezone": "GMT",
    }
    if api_key:
        params["apikey"] = api_key
    return base + "?" + urllib.parse.urlencode(params, safe=",")


def parse_era5_re(payload: Any) -> tuple[list[datetime], dict[str, FloatArray], float]:
    """Times, variables (gaps interpolated) and the model cell elevation."""
    h = payload["hourly"]
    times = [datetime.strptime(t, "%Y-%m-%dT%H:%M") for t in h["time"]]
    out = {}
    for v in ERA5_RE_VARS:
        if v not in h:
            continue
        raw = np.array([np.nan if x is None else x for x in h[v]], dtype=float)
        out[v] = dwx.fill_gaps(raw) if np.isfinite(raw).any() else raw
    return times, out, float(payload.get("elevation", np.nan))


def era5_at(
    cache: PoliteCache,
    lat: float,
    lon: float,
    years: Sequence[int],
    *,
    snap: float = 0.0,
    rad_lead: int = 1,
    retries: int = 2,
    **kw: Any,
) -> tuple[list[datetime], dict[str, FloatArray], float]:
    """ERA5 for whole years at a point (``snap``: the ERA5 cell centre)."""
    if snap > 0:
        lat, lon = round(lat / snap) * snap, round(lon / snap) * snap
    times: list[datetime] = []
    acc: dict[str, list[FloatArray]] = {}
    elev = float("nan")
    for y in sorted(years):
        url = era5_re_url(lat, lon, y, **kw)
        key_src = url.split("apikey=", maxsplit=1)[0]
        key = (
            "era5re/"
            + hashlib.sha1(key_src.encode(), usedforsecurity=False).hexdigest()[:16]
            + ".json"
        )
        body = dhi._fetch_retry(cache, key, url, retries)  # noqa: SLF001
        t, vals, elev = parse_era5_re(json.loads(body))
        times += t
        for k, a in vals.items():
            acc.setdefault(k, []).append(a)
    out = {k: np.concatenate(v) for k, v in acc.items()}
    if rad_lead > 0:
        for k in (
            "shortwave_radiation",
            "direct_normal_irradiance",
            "diffuse_radiation",
        ):
            if k in out and out[k].size > rad_lead:
                a = out[k]
                out[k] = np.concatenate([a[rad_lead:], np.repeat(a[-1:], rad_lead)])
    return times, out, elev


def _on(times: list[datetime], src_t: list[datetime], a: FloatArray) -> FloatArray:
    idx = {t: i for i, t in enumerate(src_t)}
    rows = [idx.get(t, -1) for t in times]
    out = np.full(len(times), np.nan)
    ok = np.array([r >= 0 for r in rows])
    out[ok] = a[np.array([r for r in rows if r >= 0], dtype=np.int64)]
    return out


def plant_history(  # pylint: disable=too-many-arguments
    plants: Sequence[Plant],
    hub_heights: dict[str, float],
    years: Sequence[int],
    *,
    cache: PoliteCache,
    wind_source: str = "explorador",
    explorador_model: str = "recon",
    levels: Sequence[float] = (10, 20),
    shear: str = "fitted",
    alpha: float = 1.0 / 7.0,
    z0: float = 0.03,
    shear_ratio: dict[str, FloatArray] | None = None,
    utc_offset: float | str = -4.0,
    method: str = "bilinear",
    era5_kw: dict[str, Any] | None = None,
    met: PointWeather | None = None,
) -> PlantWeather:
    """Hourly weather at every plant on the UTC hours of ``years``.

    ``met`` (from :func:`gtopt_shared.weather_source.point_weather`, one
    column per plant) replaces the ERA5 download of temperature, pressure,
    irradiance and, with ``wind_source = "era5"``, the 10/100 m wind;
    fields it lacks are left out of the table (the models then use the
    ISA pressure and decompose GHI)."""
    from re2gtopt._wind import shear_extrapolate  # pylint: disable=C0415

    if wind_source not in WIND_SOURCES:
        raise ValueError(f"wind source must be one of {WIND_SOURCES}")
    times = dhi.year_hours(years)
    n_t, n_p = len(times), len(plants)
    data = {k: np.full((n_t, n_p), np.nan) for k in FIELDS}
    elev: dict[str, float] = {}
    client = dwx.Explorador(cache, explorador_model)
    ekw = dict(era5_kw or {})
    for j, p in enumerate(plants):
        if met is None:
            t_e, ev, e_elev = era5_at(cache, p.lat, p.lon, years, **ekw)
        else:
            t_e, ev, e_elev = _met_as_era5(met, j)
        elev[p.name] = float(p.elevation) if p.elevation is not None else e_elev
        data["temperature"][:, j] = _on(times, t_e, ev["temperature_2m"])
        if "surface_pressure" in ev:
            data["pressure"][:, j] = _on(times, t_e, ev["surface_pressure"] * 100.0)
        data["ghi"][:, j] = _on(times, t_e, ev["shortwave_radiation"])
        for f, v in (
            ("dni", "direct_normal_irradiance"),
            ("dhi", "diffuse_radiation"),
            ("wind_speed_10m", "wind_speed_10m"),
        ):
            if v in ev:
                data[f][:, j] = _on(times, t_e, ev[v])
        if p.type != "wind":
            continue
        hub = hub_heights.get(p.name, 100.0)
        if wind_source == "era5":
            if "wind_speed_100m" not in ev:
                raise ValueError(
                    f"plant {p.name}: the weather source has no 100 m wind"
                )
            lv = {
                10.0: _on(times, t_e, ev["wind_speed_10m"]),
                100.0: _on(times, t_e, ev["wind_speed_100m"]),
            }
            data["wind_direction"][:, j] = _on(times, t_e, ev["wind_direction_100m"])
            data["wind_speed_hub"][:, j] = shear_extrapolate(
                lv, hub, shear, alpha=alpha, z0=z0
            )
            continue
        t_x, s_lv, d_top = explorador_levels(client, p.lat, p.lon, levels, method)
        lv = {h: _on(times, t_x, a) for h, a in s_lv.items()}
        data["wind_direction"][:, j] = _on(times, t_x, d_top)
        ratio = None
        if shear == "ratio":
            if shear_ratio is None or p.name not in shear_ratio:
                raise ValueError(f"plant {p.name}: shear ratio not fitted")
            from dlr2gtopt._typical import local_calendar  # pylint: disable=C0415

            cal = local_calendar(times, utc_offset)
            ratio = shear_ratio[p.name][cal.month - 1, cal.hour]
        data["wind_speed_hub"][:, j] = shear_extrapolate(
            lv, hub, shear, alpha=alpha, z0=z0, ratio=ratio
        )
    if met is not None:
        data = {k: a for k, a in data.items() if np.isfinite(a).any()}
    return PlantWeather(times, [p.name for p in plants], data, elev)


_MET_AS_ERA5 = {
    "temperature": "temperature_2m",
    "ghi": "shortwave_radiation",
    "dni": "direct_normal_irradiance",
    "dhi": "diffuse_radiation",
    "wind_speed_10m": "wind_speed_10m",
    "wind_speed_100m": "wind_speed_100m",
    "wind_direction_100m": "wind_direction_100m",
}


def _met_as_era5(
    met: PointWeather, j: int
) -> tuple[list[datetime], dict[str, FloatArray], float]:
    """Column ``j`` of a shared-layer table under the ERA5 variable names."""
    ev = {v: met.data[f][:, j] for f, v in _MET_AS_ERA5.items() if f in met.data}
    if "pressure" in met.data:
        ev["surface_pressure"] = met.data["pressure"][:, j] / 100.0
    return list(met.times), ev, float("nan")


def fit_ratio_from_wrf(
    client: dwx.Explorador,
    plant: Plant,
    ref_height: float,
    hub_height: float,
    utc_offset: float | str = -4.0,
    method: str = "bilinear",
) -> FloatArray:
    """(12, 24) month × local-hour ratio v_hub / v_ref fitted on a
    multi-height WRF year at the plant (``client.model`` = ``wrf2010``)."""
    from dlr2gtopt._typical import local_calendar  # pylint: disable=C0415
    from re2gtopt._wind import fit_shear_ratio  # pylint: disable=C0415

    t, lv, _ = explorador_levels(
        client, plant.lat, plant.lon, sorted({ref_height, hub_height}), method
    )
    cal = local_calendar(t, utc_offset)
    return fit_shear_ratio(lv[ref_height], lv[hub_height], cal.month, cal.hour)
