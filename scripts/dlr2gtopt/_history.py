# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Long per-span hourly weather histories (≥ 10 years) for typical years.

The Explorador Eólico WRF hindcasts (1 km) carry every variable but only
for one year (2010, 2015).  Its 1980-2017 reconstruction (``recon``)
carries wind speed and direction at 10/20 m only.  ERA5 carries every
variable for decades, but on a 0.25° (~30 km) grid.  A history is
therefore built by combining sources, per field:

* **wind** (speed and direction): Explorador ``recon`` (1 km, interpolated
  to each span midpoint and to conductor height exactly as the WRF
  series), or ERA5 10 m (``--wind era5``, log-law to conductor height);
* **temperature and solar irradiance**: ERA5 through the Open-Meteo
  archive API, at each span midpoint (the API picks the ERA5 cell);
* **calibration** (``--calibrate wrf``): ERA5 is coarse, so each span's
  ERA5 temperature receives the month × hour-of-day mean difference
  (WRF − ERA5) and its GHI the month × hour ratio (WRF / ERA5), both
  fitted on the WRF overlap year(s).  This keeps ERA5's inter-annual and
  day-to-day variability and adds the 1 km WRF local climatology
  (coastal fog, altitude, the conductor-height temperature).

The output is an ordinary :class:`~dlr2gtopt._weather.Weather` table
(UTC hours × spans), written by ``Weather.write``.

Sources and licences:

* Explorador Eólico, Ministerio de Energía / DGF Universidad de Chile,
  <https://eolico.minenergia.cl>; no licence stated -- cite it and do not
  redistribute the series.
* ERA5: Hersbach et al. (2020), Q. J. R. Meteorol. Soc. 146:1999-2049,
  DOI 10.1002/qj.3803; Copernicus Climate Change Service, licence
  "Licence to use Copernicus Products" (free, attribution required).
* Open-Meteo historical weather API, <https://open-meteo.com>, data
  CC BY 4.0.  The free endpoint is **non-commercial only**; commercial
  users pass ``--api-key`` (customer endpoint) or ``--base-url`` (own
  server).
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import numpy.typing as npt

from dlr2gtopt._spans import Span
from dlr2gtopt._towers import PoliteCache
from dlr2gtopt._weather import (
    EXPLORADOR_GRID,
    Explorador,
    NodeSeries,
    RegularGrid,
    Weather,
    explorador_wind,
    fill_gaps,
    interpolate_spans,
    vertical_wind,
)

logger = logging.getLogger("dlr2gtopt")

FloatArray = npt.NDArray[np.float64]

ERA5_URL = "https://archive-api.open-meteo.com/v1/archive"
ERA5_CUSTOMER_URL = "https://customer-archive-api.open-meteo.com/v1/archive"
ERA5_MODELS = ("era5", "era5_land", "era5_seamless")
ERA5_ATTRIBUTION = (
    "Contains modified Copernicus Climate Change Service information (ERA5); "
    "weather data by Open-Meteo.com (CC BY 4.0)"
)
ERA5_VARS = "temperature_2m,shortwave_radiation,wind_speed_10m,wind_direction_10m"
WIND_SOURCES = ("recon", "era5")
CALIBRATIONS = ("none", "wrf")


@dataclass
class Era5Point:
    """ERA5 hourly series at one point (UTC)."""

    temperature: FloatArray  # 2 m [degC]
    ghi: FloatArray  # shortwave radiation, preceding-hour mean [W/m2]
    wind_speed: FloatArray  # 10 m [m/s]
    wind_direction: FloatArray  # 10 m [deg]


def era5_url(
    lats: Sequence[float],
    lons: Sequence[float],
    start: str,
    end: str,
    *,
    model: str = "era5",
    api_key: str | None = None,
    base_url: str | None = None,
) -> str:
    """Open-Meteo archive URL (several points, one date range)."""
    if model not in ERA5_MODELS:
        raise ValueError(f"ERA5 model must be one of {ERA5_MODELS}")
    base = base_url or (ERA5_CUSTOMER_URL if api_key else ERA5_URL)
    params = {
        "latitude": ",".join(f"{x:.4f}" for x in lats),
        "longitude": ",".join(f"{x:.4f}" for x in lons),
        "start_date": start,
        "end_date": end,
        "hourly": ERA5_VARS,
        "models": model,
        "wind_speed_unit": "ms",
        "timezone": "GMT",
    }
    if api_key:
        params["apikey"] = api_key
    return base + "?" + urllib.parse.urlencode(params, safe=",")


def parse_era5(payload: Any) -> tuple[list[datetime], list[Era5Point]]:
    """Open-Meteo archive JSON (one object, or a list for several points)."""
    items = payload if isinstance(payload, list) else [payload]
    times: list[datetime] = []
    out: list[Era5Point] = []
    for it in items:
        h = it["hourly"]
        times = [datetime.strptime(t, "%Y-%m-%dT%H:%M") for t in h["time"]]

        def arr(name: str, hh: dict[str, Any] = h) -> FloatArray:
            raw = np.array([np.nan if x is None else x for x in hh[name]], float)
            gaps = int(np.isnan(raw).sum())
            if gaps:
                logger.warning("ERA5 %s: %d missing hours interpolated", name, gaps)
            return fill_gaps(raw)

        out.append(
            Era5Point(
                arr("temperature_2m"),
                arr("shortwave_radiation"),
                arr("wind_speed_10m"),
                arr("wind_direction_10m"),
            )
        )
    return times, out


def era5_points(
    cache: PoliteCache,
    points: Sequence[tuple[float, float]],
    years: Sequence[int],
    *,
    model: str = "era5",
    api_key: str | None = None,
    base_url: str | None = None,
    snap: float = 0.0,
    retries: int = 0,
    ghi_lead: int = 1,
) -> tuple[list[datetime], list[Era5Point]]:
    """ERA5 at several points for whole calendar years (one request per
    year, cached; the API key never enters the cache key).

    ``snap > 0`` requests each grid cell of that size once (ERA5 is 0.25°,
    so spans in the same cell share one series), which keeps the request
    weight -- and Open-Meteo's rate limit -- low.  After HTTP 429 the
    request is retried ``retries`` times, waiting 60 s and doubling.

    Open-Meteo's ``shortwave_radiation`` at label t is the mean over the
    PRECEDING hour [t−1, t]; ``ghi_lead = 1`` (default) moves it so hour t
    carries the mean over [t, t+1], aligned with the hour-start convention
    of the other fields (the last hour repeats).  ``0`` keeps the labels."""
    if snap > 0:
        cells = [
            (round(la / snap) * snap, round(lo / snap) * snap) for la, lo in points
        ]
        uniq = list(dict.fromkeys(cells))
        t, got = era5_points(
            cache,
            uniq,
            years,
            model=model,
            api_key=api_key,
            base_url=base_url,
            retries=retries,
            ghi_lead=ghi_lead,
        )
        return t, [got[uniq.index(c)] for c in cells]
    times: list[datetime] = []
    acc: list[list[Era5Point]] = [[] for _ in points]
    for y in sorted(years):
        url = era5_url(
            [p[0] for p in points],
            [p[1] for p in points],
            f"{y}-01-01",
            f"{y}-12-31",
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
        key_src = url.split("apikey=", maxsplit=1)[0]
        key = (
            "era5/"
            + hashlib.sha1(key_src.encode(), usedforsecurity=False).hexdigest()[:16]
            + ".json"
        )
        t, pts = parse_era5(json.loads(_fetch_retry(cache, key, url, retries)))
        times += t
        for k, p in enumerate(pts):
            acc[k].append(p)

    def lead(a: FloatArray) -> FloatArray:
        if ghi_lead <= 0 or a.size <= ghi_lead:
            return a
        return np.concatenate([a[ghi_lead:], np.repeat(a[-1:], ghi_lead)])

    out = [
        Era5Point(
            np.concatenate([p.temperature for p in ps]),
            lead(np.concatenate([p.ghi for p in ps])),
            np.concatenate([p.wind_speed for p in ps]),
            np.concatenate([p.wind_direction for p in ps]),
        )
        for ps in acc
    ]
    return times, out


def _fetch_retry(cache: PoliteCache, key: str, url: str, retries: int) -> bytes:
    wait = 60.0
    for attempt in range(retries + 1):
        try:
            return cache.fetch(key, url)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == retries:
                raise
            logger.warning("HTTP 429 (rate limit): waiting %.0f s", wait)
            cache.sleep(wait)
            wait *= 2
    raise AssertionError("unreachable")


def recon_wind(
    client: Explorador,
    spans: Sequence[Span],
    *,
    method: str = "bilinear",
    height: float = 15.0,
    profile: str = "log",
    grid: RegularGrid = EXPLORADOR_GRID,
    progress: Callable[[int, int], None] | None = None,
) -> Weather:
    """Per-span wind (speed, direction) from Explorador wind-only series
    (the ``recon`` 1980-2017 reconstruction, or any WRF model)."""
    nodes = grid.nodes_for(((s.lat, s.lon) for s in spans), method)
    series: dict[tuple[int, int], NodeSeries] = {}
    times: list[datetime] = []
    for k, node in enumerate(nodes, start=1):
        la, lo = grid.node_coords(node)
        times, spd, wdir = explorador_wind(client, la, lo)
        zero = np.zeros(len(times))
        series[node] = NodeSeries(spd, wdir, {10.0: zero}, zero)
        if progress is not None:
            progress(k, len(nodes))
    w = interpolate_spans(
        grid, spans, series, times, method=method, height=height, profile=profile
    )
    return Weather(
        w.times,
        w.spans,
        {k: w.data[k] for k in ("wind_speed", "wind_direction")},
    )


def _index(times: Sequence[datetime]) -> dict[datetime, int]:
    return {t: i for i, t in enumerate(times)}


def _month_hour_key(times: Sequence[datetime]) -> npt.NDArray[np.int64]:
    return np.array([(t.month - 1) * 24 + t.hour for t in times], dtype=np.int64)


def month_hour_delta(
    target: FloatArray,
    source: FloatArray,
    keys: npt.NDArray[np.int64],
    *,
    ratio: bool = False,
    clip: tuple[float, float] = (0.5, 2.0),
) -> FloatArray:
    """(288, spans) month × hour calibration: mean(target − source), or the
    ratio sum(target)/sum(source) (clipped; 1 where the source is ~0)."""
    n_s = target.shape[1]
    out = np.full((288, n_s), 1.0 if ratio else 0.0)
    for k in range(288):
        m = keys == k
        if not m.any():
            continue
        if ratio:
            s = source[m].sum(axis=0)
            t = target[m].sum(axis=0)
            ok = s > 1.0
            out[k, ok] = np.clip(t[ok] / s[ok], *clip)
        else:
            out[k] = (target[m] - source[m]).mean(axis=0)
    return out


def combine_history(
    times: Sequence[datetime],
    spans: Sequence[Span],
    era5: Sequence[Era5Point],
    *,
    wind: Weather | None = None,
    calib: Weather | None = None,
    height: float = 15.0,
) -> Weather:
    """Per-span history on ``times`` (UTC).

    ``era5[j]`` is the ERA5 series at span ``j`` on ``times``.  ``wind``
    (if given) replaces ERA5 wind; it must cover ``times``.  ``calib`` (a
    WRF weather table of the same spans, any overlapping hours) calibrates
    temperature (additive) and GHI (ratio) by month × hour.
    """
    n_t, n_s = len(times), len(spans)
    temp = np.column_stack([p.temperature for p in era5]) if n_s else np.zeros((n_t, 0))
    ghi = np.column_stack([p.ghi for p in era5]) if n_s else np.zeros((n_t, 0))
    if wind is None:
        spd = np.column_stack(
            [vertical_wind({10.0: p.wind_speed}, height, "none") for p in era5]
        )
        # ERA5 has one level: scale 10 m -> height with a neutral log law
        # (roughness 0.03 m, open terrain).
        z0 = 0.03
        spd = spd * np.log(height / z0) / np.log(10.0 / z0)
        wdir = np.column_stack([p.wind_direction for p in era5])
    else:
        idx = _index(wind.times)
        missing = [t for t in times if t not in idx]
        if missing:
            raise ValueError(
                f"wind history misses {len(missing)} hours (first {missing[0]})"
            )
        rows = np.array([idx[t] for t in times])
        spd = wind.data["wind_speed"][rows]
        wdir = wind.data["wind_direction"][rows]
    if calib is not None:
        cidx = _index(times)
        both = [(i, cidx[t]) for i, t in enumerate(calib.times) if t in cidx]
        if not both:
            raise ValueError("calibration weather does not overlap the history")
        ci = np.array([a for a, _ in both])
        hi = np.array([b for _, b in both])
        keys = _month_hour_key([calib.times[i] for i in ci])
        dt = month_hour_delta(calib.data["ambient_temperature"][ci], temp[hi], keys)
        all_keys = _month_hour_key(times)
        temp = temp + dt[all_keys]
        if "solar_irradiance" in calib.data:
            rg = month_hour_delta(
                calib.data["solar_irradiance"][ci], ghi[hi], keys, ratio=True
            )
            ghi = ghi * rg[all_keys]
        logger.info(
            "calibration on %d overlap hours: mean T shift %.2f C",
            len(both),
            float(dt.mean()),
        )
    return Weather(
        list(times),
        list(spans),
        {
            "ambient_temperature": temp,
            "wind_speed": spd,
            "wind_direction": wdir,
            "solar_irradiance": np.maximum(ghi, 0.0),
        },
    )


def year_hours(years: Sequence[int]) -> list[datetime]:
    """Every UTC hour of the given calendar years, in order."""
    out: list[datetime] = []
    for y in sorted(years):
        t = datetime(y, 1, 1)
        end = datetime(y + 1, 1, 1)
        while t < end:
            out.append(t)
            t += timedelta(hours=1)
    return out


def parse_years(text: str) -> list[int]:
    """``2008-2017`` or ``2008,2010,2012`` (or a mix) -> sorted years."""
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    if not out:
        raise ValueError("no years given")
    return sorted(out)
