# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""One weather-source layer for ``dlr2gtopt`` and ``re2gtopt``.

``--weather-source`` picks where the hourly weather of a set of points
(span midpoints, plants) comes from:

* ``explorador`` -- the Chilean Ministry of Energy Explorador Eólico WRF
  1 km hourly hindcast (``wrf2010``: year 2010; ``wrf2015``: 2015), all
  four variables (wind speed and direction at 10/20 m, temperature,
  global horizontal irradiance).  The download is ``dlr2gtopt``'s
  :class:`~dlr2gtopt._weather.Explorador` client, reused as is.
  <https://eolico.minenergia.cl>.  No licence is stated: the series are
  fetched by script, cited, and never redistributed.
* ``open-meteo`` -- the Open-Meteo historical archive API (ERA5 0.25°
  from 1940, ECMWF IFS 9 km from 2017, ...): temperature, wind at 10 and
  100 m, global, direct-normal and diffuse irradiance and surface
  pressure, for any year.  Data CC BY 4.0; **the free endpoint is for
  non-commercial use only**: commercial users pass an API key (customer
  endpoint) or a self-hosted ``base_url``.

Every backend returns a :class:`PointWeather`: hourly UTC series, one
column per point, with wind at the requested height (the DLR vertical
interpolation, :func:`dlr2gtopt._weather.at_height`: log law between
the source levels, direction through u/v, temperature linear).

A source covers fixed years (:data:`COVERAGE`).  A period outside them
fails unless a **climatology** mode is chosen:

* ``calendar`` -- each target hour takes the source-year hour with the
  same local month, day and hour (29 February -> 28 February); the
  weather of one real year is replayed on every target year, with all
  variables and points from the same hour (what the DLR study did);
* ``month-hour`` -- each target hour takes a statistic (``mean``,
  ``median`` or ``qNN``, the NN-th percentile) of the source hours of the
  same local month and hour of day, per variable (direction: circular
  mean through u/v).  Smooth; it breaks the coincidence between
  variables, so DLR ratings should prefer ``calendar``.

The local calendar is a fixed UTC offset (default −4, Chile standard
time, as the Explorador series and PLP block maps) or a tool's time basis
(``dlr2gtopt._tz.TimeBasis``: ``--timezone`` / ``--dst``), anything with
``local(times)`` and ``describe()``.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from gtopt_shared import explorador_solar as es

FloatArray = npt.NDArray[np.float64]

WEATHER_SOURCES = ("explorador", "explorador-solar", "open-meteo")
CLIMATOLOGY_MODES = ("off", "calendar", "month-hour")
FIELDS = (
    "temperature",
    "wind_speed",
    "wind_direction",
    "ghi",
    "dni",
    "dhi",
    "pressure",
    "wind_speed_10m",
    "wind_speed_100m",
    "wind_direction_100m",
)
"""Canonical fields [°C, m/s, deg FROM, W/m², W/m², W/m², Pa, m/s, m/s, deg]."""

EXPLORADOR_ROUTER_PATTERN = (
    "https://eolico.minenergia.cl/python-router/"
    '{"tipo":"DescargaExploracion","datos":{...,"descarga":'
    '"SerieHorariaViento<Model>","variable":"vel|vdir|temp|glb",...}}'
    " then https://eolico.minenergia.cl/temp/<file>"
)
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_CUSTOMER_ARCHIVE_URL = (
    "https://customer-archive-api.open-meteo.com/v1/archive"
)
OPEN_METEO_LICENCE = (
    "Weather data by Open-Meteo.com, CC BY 4.0; free API non-commercial "
    "only (commercial: API key or own server)"
)
EXPLORADOR_LICENCE = (
    "Explorador Eolico, Ministerio de Energia / DGF Universidad de Chile; "
    "no licence stated: cite, fetch by script, do not redistribute"
)
_OM_HOURLY = (
    "temperature_2m",
    "surface_pressure",
    "wind_speed_10m",
    "wind_speed_100m",
    "wind_direction_10m",
    "wind_direction_100m",
    "shortwave_radiation",
    "direct_normal_irradiance",
    "diffuse_radiation",
)
_OM_RADIATION = {
    "shortwave_radiation": "ghi",
    "direct_normal_irradiance": "dni",
    "diffuse_radiation": "dhi",
}


class WeatherSourceError(ValueError):
    """A source cannot supply a requested variable or period."""


class LocalClock(Protocol):
    """A local calendar (``dlr2gtopt._tz.TimeBasis``)."""

    def local(self, times: Sequence[datetime]) -> list[datetime]:
        """Naive local times of naive-UTC ``times``."""

    def describe(self) -> str:
        """Human text for logs and provenance."""


CalendarBasis = float | LocalClock
"""A fixed UTC offset [h] or a :class:`LocalClock`."""


def describe_basis(basis: CalendarBasis) -> str:
    """``UTC-4 (fixed)`` or the clock's own description."""
    if isinstance(basis, (int, float)):
        return f"UTC{float(basis):+g} (fixed)"
    return basis.describe()


class Fetcher(Protocol):  # pylint: disable=too-few-public-methods
    """A cached, polite HTTP getter (``dlr2gtopt._towers.PoliteCache``)."""

    cache_dir: Path

    def fetch(self, key: str, url: str) -> bytes:
        """Body of ``url``, cached under ``key``."""


@dataclass(frozen=True)
class Coverage:
    """What one source / model supplies."""

    source: str
    model: str
    first_year: int
    last_year: int | None  # None: until a few days ago
    resolution: str
    fields: tuple[str, ...]
    url: str
    licence: str

    def missing(self, years: Sequence[int]) -> list[int]:
        """The requested years outside the coverage."""
        last = self.last_year if self.last_year is not None else date.today().year
        return [y for y in years if not self.first_year <= y <= last]


_EXPL_FIELDS = ("temperature", "wind_speed", "wind_direction", "ghi")
_OM_FIELDS = FIELDS
COVERAGE: dict[tuple[str, str], Coverage] = {
    ("explorador", "wrf2010"): Coverage(
        "explorador",
        "wrf2010",
        2010,
        2010,
        "WRF 1 km, hourly; wind/temperature at 10/20 m",
        _EXPL_FIELDS,
        EXPLORADOR_ROUTER_PATTERN,
        EXPLORADOR_LICENCE,
    ),
    ("explorador", "wrf2015"): Coverage(
        "explorador",
        "wrf2015",
        2015,
        2015,
        "WRF 1 km, hourly; wind/temperature at 10/20 m",
        _EXPL_FIELDS,
        EXPLORADOR_ROUTER_PATTERN,
        EXPLORADOR_LICENCE,
    ),
    ("open-meteo", "era5"): Coverage(
        "open-meteo",
        "era5",
        1940,
        None,
        "ERA5 0.25 deg (~30 km), hourly",
        _OM_FIELDS,
        OPEN_METEO_ARCHIVE_URL,
        OPEN_METEO_LICENCE,
    ),
    ("open-meteo", "era5_land"): Coverage(
        "open-meteo",
        "era5_land",
        1950,
        None,
        "ERA5-Land 0.1 deg (~9 km), hourly",
        _OM_FIELDS,
        OPEN_METEO_ARCHIVE_URL,
        OPEN_METEO_LICENCE,
    ),
    ("open-meteo", "era5_seamless"): Coverage(
        "open-meteo",
        "era5_seamless",
        1940,
        None,
        "ERA5 + ERA5-Land, hourly",
        _OM_FIELDS,
        OPEN_METEO_ARCHIVE_URL,
        OPEN_METEO_LICENCE,
    ),
    ("open-meteo", "ecmwf_ifs"): Coverage(
        "open-meteo",
        "ecmwf_ifs",
        2017,
        None,
        "ECMWF IFS 9 km analysis, hourly",
        _OM_FIELDS,
        OPEN_METEO_ARCHIVE_URL,
        OPEN_METEO_LICENCE,
    ),
}
_SOLAR_FIELDS = ("temperature", "wind_speed", "ghi", "dni", "dhi")
COVERAGE[("explorador-solar", "hourly")] = Coverage(
    "explorador-solar",
    "hourly",
    es.SOLAR_FIRST_YEAR,
    es.SOLAR_LAST_YEAR,
    "GOES satellite irradiance + meteorology, hourly (DHC_E); temperature "
    "at 2 m; wind speed at 5.5 m (site SOLEVAPP report), no direction",
    _SOLAR_FIELDS,
    es.ROUTER_PATTERN,
    es.LICENCE,
)
COVERAGE[("explorador-solar", "aggregate")] = Coverage(
    "explorador-solar",
    "aggregate",
    es.SOLAR_FIRST_YEAR,
    es.SOLAR_LAST_YEAR,
    "month x hour means over 2004-2016 (ExploracionCompleta HM); wind at "
    "5.5 m; DHI from the monthly diffuse fraction",
    _SOLAR_FIELDS,
    es.AGGREGATE_PATTERN,
    es.LICENCE,
)
DEFAULT_MODEL = {
    "explorador": "wrf2010",
    "explorador-solar": "hourly",
    "open-meteo": "era5",
}


def coverage(source: str, model: str | None = None) -> Coverage:
    """The coverage of ``source`` / ``model`` (default model if None)."""
    if source not in WEATHER_SOURCES:
        raise WeatherSourceError(
            f"weather source must be one of {WEATHER_SOURCES}, got {source!r}"
        )
    m = model or DEFAULT_MODEL[source]
    try:
        return COVERAGE[source, m]
    except KeyError:
        models = sorted(k[1] for k in COVERAGE if k[0] == source)
        raise WeatherSourceError(
            f"weather source {source}: model must be one of {models}, got {m!r}"
        ) from None


def require_fields(cov: Coverage, need: Sequence[str]) -> None:
    """Fail clearly when the source lacks a needed variable."""
    lack = [f for f in need if f not in cov.fields]
    if lack:
        raise WeatherSourceError(
            f"weather source {cov.source} ({cov.model}) has no {', '.join(lack)}; "
            f"it supplies {', '.join(cov.fields)}"
        )


# ---------------------------------------------------------------------------
# Point weather
# ---------------------------------------------------------------------------


@dataclass
class PointWeather:
    """Hourly weather at points: field -> (hours, points), UTC times.

    ``source_times`` is the UTC hour of the source each row was taken
    from (equal to ``times`` without climatology; ``None`` for a
    month-hour statistic, which mixes hours)."""

    times: list[datetime]
    points: list[tuple[float, float]]
    data: dict[str, FloatArray] = field(default_factory=dict)
    source_times: list[datetime] | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        shape = (len(self.times), len(self.points))
        for k, v in self.data.items():
            if v.shape != shape:
                raise ValueError(f"weather field {k}: shape {v.shape} != {shape}")

    def write_provenance(self, path: Path) -> Path:
        """Write the provenance next to an output (``<path>.provenance.json``)."""
        out = path.with_name(path.name + ".provenance.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.provenance, indent=2, sort_keys=True) + "\n")
        return out


def provenance(
    cov: Coverage,
    *,
    years: Sequence[int],
    climatology: str = "off",
    stat: str = "mean",
    utc_offset: CalendarBasis = -4.0,
    height: float | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The provenance record of a weather table."""
    out: dict[str, Any] = {
        "weather_source": cov.source,
        "model": cov.model,
        "resolution": cov.resolution,
        "url": cov.url,
        "licence": cov.licence,
        "source_years": [cov.first_year, cov.last_year],
        "requested_years": list(years),
        "retrieved": date.today().isoformat(),
        "fields": list(cov.fields),
        "climatology": climatology,
        "local_calendar": describe_basis(utc_offset),
    }
    if climatology == "month-hour":
        out["climatology_stat"] = stat
    if height is not None:
        out["height_m"] = height
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Climatology on another calendar
# ---------------------------------------------------------------------------


def _local(times: Sequence[datetime], basis: CalendarBasis) -> list[datetime]:
    if isinstance(basis, (int, float)):
        shift = timedelta(hours=float(basis))
        return [t + shift for t in times]
    return basis.local(times)


def local_times(times: Sequence[datetime], basis: CalendarBasis) -> list[datetime]:
    """Naive local times of naive-UTC ``times`` on ``basis``."""
    return _local(times, basis)


def year_hours(years: Sequence[int]) -> list[datetime]:
    """Every UTC hour of the given calendar years, in order."""
    out: list[datetime] = []
    for y in sorted(set(years)):
        t, end = datetime(y, 1, 1), datetime(y + 1, 1, 1)
        n = int((end - t).total_seconds() // 3600)
        out += [t + timedelta(hours=h) for h in range(n)]
    return out


def calendar_index(
    src_times: Sequence[datetime],
    target_times: Sequence[datetime],
    utc_offset: CalendarBasis = -4.0,
) -> npt.NDArray[np.int64]:
    """For each target hour, the source row with the same local month, day
    and hour (29 February -> 28 February).  A key the source lacks (a
    local-time edge of a UTC year, or the Explorador Solar's missing last
    day) takes the same hour of the day before, else of the day after,
    else the nearest earlier (then later) hour of the same day."""
    if not src_times:
        raise WeatherSourceError("climatology: the source has no hours")
    index: dict[tuple[int, int, int], int] = {}
    for i, t in enumerate(_local(src_times, utc_offset)):
        d = 28 if (t.month, t.day) == (2, 29) else t.day
        index.setdefault((t.month, d, t.hour), i)
    out = np.empty(len(target_times), dtype=np.int64)
    for k, t in enumerate(_local(target_times, utc_offset)):
        d = 28 if (t.month, t.day) == (2, 29) else t.day
        row = index.get((t.month, d, t.hour))
        for dd in (d - 1, d + 1):  # same hour of a neighbour day
            if row is None:
                row = index.get((t.month, dd, t.hour))
        if row is None:
            order = list(range(t.hour - 1, -1, -1)) + list(range(t.hour + 1, 24))
            row = next(
                (index[t.month, d, h] for h in order if (t.month, d, h) in index), None
            )
        if row is None:
            raise WeatherSourceError(
                f"climatology: no source hour for local {t.month:02d}-{d:02d}"
            )
        out[k] = row
    return out


_STAT = re.compile(r"^(mean|median|q(\d{1,2}(?:\.\d+)?))$")


def parse_stat(stat: str) -> float | None:
    """``mean`` -> None, ``median`` -> 0.5, ``qNN`` -> NN/100."""
    m = _STAT.match(stat)
    if m is None:
        raise WeatherSourceError(
            f"climatology stat must be mean, median or qNN (e.g. q10), got {stat!r}"
        )
    if m.group(1) == "mean":
        return None
    if m.group(1) == "median":
        return 0.5
    return float(m.group(2)) / 100.0


def _circular_mean(deg: FloatArray, axis: int = 0) -> FloatArray:
    r = np.radians(deg)
    return np.mod(
        np.degrees(np.arctan2(np.sin(r).mean(axis=axis), np.cos(r).mean(axis=axis))),
        360.0,
    )


def month_hour_table(
    src_times: Sequence[datetime],
    a: FloatArray,
    *,
    utc_offset: CalendarBasis = -4.0,
    stat: str = "mean",
    circular: bool = False,
) -> FloatArray:
    """(12, 24, points) statistic of ``a`` (hours, points) per local month
    and hour of day.  ``circular`` (directions): circular mean always."""
    q = parse_stat(stat)
    loc = _local(src_times, utc_offset)
    month = np.array([t.month for t in loc])
    hour = np.array([t.hour for t in loc])
    out = np.full((12, 24, a.shape[1]), np.nan)
    for m in range(1, 13):
        for h in range(24):
            sel = a[(month == m) & (hour == h)]
            if sel.size == 0:
                continue
            if circular:
                out[m - 1, h] = _circular_mean(sel)
            elif q is None:
                out[m - 1, h] = sel.mean(axis=0)
            else:
                out[m - 1, h] = np.quantile(sel, q, axis=0)
    if np.isnan(out).all(axis=2).any():
        raise WeatherSourceError(
            "climatology month-hour: the source misses some month x hour bins"
        )
    return out


_CIRCULAR = frozenset({"wind_direction", "wind_direction_100m"})


def apply_climatology(
    pw: PointWeather,
    years: Sequence[int],
    mode: str,
    *,
    stat: str = "mean",
    utc_offset: CalendarBasis = -4.0,
) -> PointWeather:
    """``pw`` replayed on the UTC hours of ``years`` (see module doc)."""
    if mode not in CLIMATOLOGY_MODES or mode == "off":
        raise WeatherSourceError(
            f"climatology must be calendar or month-hour, got {mode!r}"
        )
    target = year_hours(years)
    prov = dict(pw.provenance)
    prov["climatology"] = mode
    if mode == "calendar":
        rows = calendar_index(pw.times, target, utc_offset)
        data = {k: v[rows] for k, v in pw.data.items()}
        src = [pw.times[int(r)] for r in rows]
        prov["calendar_rule"] = (
            "same local month, day and hour of the source year (29 Feb -> 28 Feb)"
        )
        return PointWeather(target, list(pw.points), data, src, prov)
    parse_stat(stat)
    loc = _local(target, utc_offset)
    mi = np.array([t.month - 1 for t in loc])
    hi = np.array([t.hour for t in loc])
    data = {}
    for k, v in pw.data.items():
        tab = month_hour_table(
            pw.times, v, utc_offset=utc_offset, stat=stat, circular=k in _CIRCULAR
        )
        data[k] = tab[mi, hi]
    prov["climatology_stat"] = stat
    return PointWeather(target, list(pw.points), data, None, prov)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


PointHeights = Sequence[float | None] | None
"""Per-point heights [m] (None entries, or None: the scalar height)."""


def _point_heights(
    points: Sequence[tuple[float, float]], height: float, heights: PointHeights
) -> list[float]:
    if heights is None:
        return [height] * len(points)
    if len(heights) != len(points):
        raise WeatherSourceError(
            f"{len(heights)} per-point heights for {len(points)} points"
        )
    return [height if h is None else float(h) for h in heights]


def _height_provenance(
    height: float, hs: Sequence[float], per_point: bool
) -> dict[str, Any]:
    if not per_point:
        return {}
    return {
        "height_source": "per point (the scalar where unset)",
        "height_range_m": [min(hs), max(hs)] if hs else [height, height],
    }


def _pseudo_spans(
    points: Sequence[tuple[float, float]], heights: Sequence[float] | None = None
) -> list[Any]:
    from dlr2gtopt._spans import Span  # pylint: disable=import-outside-toplevel

    return [
        Span(
            line="p",
            span=k,
            lat=la,
            lon=lo,
            azimuth=0.0,
            length_m=0.0,
            chainage_km=0.0,
            height=None if heights is None else heights[k],
        )
        for k, (la, lo) in enumerate(points)
    ]


def explorador_points(
    cache: Fetcher,
    points: Sequence[tuple[float, float]],
    *,
    model: str = "wrf2010",
    height: float = 10.0,
    heights: PointHeights = None,
    method: str = "bilinear",
    profile: str = "log",
) -> PointWeather:
    """Explorador Eólico WRF hourly weather at points: the existing
    ``dlr2gtopt`` download and interpolation (grid stencil, :func:`at_height`).
    ``heights``: one height per point (dlr2gtopt ``span_heights``)."""
    # pylint: disable=import-outside-toplevel
    from dlr2gtopt import _weather as dwx

    cov = coverage("explorador", model)
    client = dwx.Explorador(cache, model)  # type: ignore[arg-type]
    hs = _point_heights(points, height, heights)
    per_point = heights is not None
    w = dwx.explorador_weather(
        client,
        _pseudo_spans(points, hs if per_point else None),
        method=method,
        height=height,
        profile=profile,
        span_heights=per_point,
    )
    names = {
        "ambient_temperature": "temperature",
        "wind_speed": "wind_speed",
        "wind_direction": "wind_direction",
        "solar_irradiance": "ghi",
    }
    data = {names[k]: v for k, v in w.data.items() if k in names}
    prov = provenance(
        cov,
        years=[cov.first_year],
        height=height,
        extra={
            "interpolation": method,
            "wind_profile": profile,
            **_height_provenance(height, hs, per_point),
        },
    )
    return PointWeather(list(w.times), list(points), data, list(w.times), prov)


def open_meteo_url(
    lats: Sequence[float],
    lons: Sequence[float],
    year: int,
    *,
    model: str = "era5",
    api_key: str | None = None,
    base_url: str | None = None,
) -> str:
    """Archive URL of several points for one calendar year."""
    base = base_url or (
        OPEN_METEO_CUSTOMER_ARCHIVE_URL if api_key else OPEN_METEO_ARCHIVE_URL
    )
    params = {
        "latitude": ",".join(f"{x:.4f}" for x in lats),
        "longitude": ",".join(f"{x:.4f}" for x in lons),
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "hourly": ",".join(_OM_HOURLY),
        "models": model,
        "wind_speed_unit": "ms",
        "timezone": "GMT",
    }
    if api_key:
        params["apikey"] = api_key
    return base + "?" + urllib.parse.urlencode(params, safe=",")


def _om_arr(h: Mapping[str, Any], name: str, n: int) -> FloatArray:
    if name not in h:
        return np.full(n, np.nan)
    a = np.array([np.nan if x is None else x for x in h[name]], dtype=float)
    bad = np.isnan(a)
    if bad.any() and not bad.all():
        idx = np.arange(a.size)
        a[bad] = np.interp(idx[bad], idx[~bad], a[~bad])
    return a


def parse_open_meteo(
    payload: Any,
) -> tuple[list[datetime], list[dict[str, FloatArray]]]:
    """Archive JSON (one object, or a list for several points) -> UTC times
    and per point ``{open-meteo variable: series}``."""
    items = payload if isinstance(payload, list) else [payload]
    times: list[datetime] = []
    out = []
    for it in items:
        h = it["hourly"]
        times = [datetime.strptime(t, "%Y-%m-%dT%H:%M") for t in h["time"]]
        out.append({v: _om_arr(h, v, len(times)) for v in _OM_HOURLY})
    return times, out


def _lead(a: FloatArray, n: int) -> FloatArray:
    if n <= 0 or a.size <= n:
        return a
    return np.concatenate([a[n:], np.repeat(a[-1:], n)])


def open_meteo_points(  # pylint: disable=too-many-locals
    cache: Fetcher,
    points: Sequence[tuple[float, float]],
    years: Sequence[int],
    *,
    model: str = "era5",
    height: float = 10.0,
    heights: PointHeights = None,
    profile: str = "log",
    api_key: str | None = None,
    base_url: str | None = None,
    snap: float = 0.0,
    rad_lead: int = 1,
) -> PointWeather:
    """Open-Meteo archive weather at points, one request per year for all
    (distinct, optionally ``snap``-rounded) points; the API key never
    enters the cache key.

    Radiation at label t is the mean over the PRECEDING hour at Open-Meteo;
    ``rad_lead = 1`` moves it so label t means [t, t+1] (hour start), as
    ``dlr2gtopt history`` and ``re2gtopt history`` do."""
    # pylint: disable=import-outside-toplevel
    from dlr2gtopt import _weather as dwx

    cov = coverage("open-meteo", model)
    missing = cov.missing(years)
    if missing:
        raise WeatherSourceError(
            f"open-meteo {model} starts in {cov.first_year}: no {missing}"
        )
    pts = (
        [(round(a / snap) * snap, round(b / snap) * snap) for a, b in points]
        if snap > 0
        else list(points)
    )
    uniq = list(dict.fromkeys(pts))
    times: list[datetime] = []
    acc: list[dict[str, list[FloatArray]]] = [{} for _ in uniq]
    for y in sorted(set(years)):
        url = open_meteo_url(
            [p[0] for p in uniq],
            [p[1] for p in uniq],
            y,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
        key_src = url.split("apikey=", maxsplit=1)[0]
        key = (
            "wxsrc_om/"
            + hashlib.sha1(key_src.encode(), usedforsecurity=False).hexdigest()[:16]
            + ".json"
        )
        t, per = parse_open_meteo(json.loads(cache.fetch(key, url)))
        if len(per) != len(uniq):
            raise WeatherSourceError(
                f"open-meteo returned {len(per)} points, asked {len(uniq)}"
            )
        times += t
        for k, d in enumerate(per):
            for v, a in d.items():
                acc[k].setdefault(v, []).append(a)
    n_t, n_p = len(times), len(points)
    data = {f: np.full((n_t, n_p), np.nan) for f in FIELDS}
    col = {p: i for i, p in enumerate(uniq)}
    hs = _point_heights(points, height, heights)
    for j, p in enumerate(pts):
        raw = {v: np.concatenate(a) for v, a in acc[col[p]].items()}
        ns = dwx.NodeSeries(
            speed={10.0: raw["wind_speed_10m"], 100.0: raw["wind_speed_100m"]},
            direction={
                10.0: raw["wind_direction_10m"],
                100.0: raw["wind_direction_100m"],
            },
            temperature={2.0: raw["temperature_2m"]},
            ghi=None,
        )
        spd, u_c, v_c, ta = dwx.at_height(ns, hs[j], profile)
        data["wind_speed"][:, j] = spd
        data["wind_direction"][:, j] = dwx.dir_from_uv(u_c, v_c)
        data["temperature"][:, j] = ta
        data["pressure"][:, j] = raw["surface_pressure"] * 100.0
        data["wind_speed_10m"][:, j] = raw["wind_speed_10m"]
        data["wind_speed_100m"][:, j] = raw["wind_speed_100m"]
        data["wind_direction_100m"][:, j] = raw["wind_direction_100m"]
        for v_om, f in _OM_RADIATION.items():
            data[f][:, j] = _lead(raw[v_om], rad_lead)
    data = {k: a for k, a in data.items() if np.isfinite(a).any()}
    prov = provenance(
        cov,
        years=sorted(set(years)),
        height=height,
        extra={
            "wind_profile": profile,
            "snap_deg": snap,
            "radiation_label": "hour start" if rad_lead > 0 else "hour end",
            "endpoint": "customer" if api_key else (base_url or "free"),
            **_height_provenance(height, hs, heights is not None),
        },
    )
    return PointWeather(times, list(points), data, list(times), prov)


def _solar_wind(
    v: FloatArray, hs: Sequence[float], j: int, z0: float, z_ref: float | None
) -> FloatArray | None:
    """``vel`` at the point height, or None when its own height is unknown
    (the field is then left out)."""
    if z_ref is None:
        return None
    try:
        return es.log_wind(v, hs[j], z_ref, z0)
    except es.SolarError as e:
        raise WeatherSourceError(str(e)) from None


def _solar_provenance(
    cov: Coverage,
    height: float,
    hs: Sequence[float],
    per_point: bool,
    z0: float,
    z_ref: float | None,
) -> dict[str, Any]:
    wind = (
        {
            "wind_height_m": z_ref,
            "wind_height_basis": es.WIND_HEIGHT_DOC
            if z_ref == es.WIND_HEIGHT
            else "given by the user (--explorador-solar-wind-height)",
            "wind_extrapolation": f"neutral log law from {z_ref:g} m, z0 = {z0} m",
        }
        if z_ref is not None
        else {"wind_speed": "left out: no wind height given"}
    )
    return provenance(
        cov,
        years=[cov.first_year, cov.last_year or cov.first_year],
        height=height,
        extra={
            "endpoint": es.SOLAR_URL + "/python-router/",
            **wind,
            "wind_direction": "not supplied by the source",
            **_height_provenance(height, hs, per_point),
        },
    )


def solar_hourly_points(
    cache: Fetcher,
    points: Sequence[tuple[float, float]],
    opt: es.SolarOptions,
    *,
    height: float = 10.0,
    heights: PointHeights = None,
) -> PointWeather:
    """Explorador Solar hourly series (2004-2016) at points; one download
    per distinct point, serial, cached (needs ``opt.profile``)."""
    cov = coverage("explorador-solar", "hourly")
    hs = _point_heights(points, height, heights)
    uniq = list(dict.fromkeys(points))
    parsed = {}
    try:
        es.registro(opt)  # fail before any request
        for p in uniq:
            parsed[p] = es.parse_hourly_csv(
                es.hourly_csv(cache, p[0], p[1], opt), opt.utc_offset, opt.label
            )
    except es.SolarError as e:
        raise WeatherSourceError(str(e)) from None
    times = parsed[uniq[0]][0]
    for p in uniq[1:]:
        if parsed[p][0] != times:
            raise WeatherSourceError("Explorador Solar: points on different hours")
    flds = sorted(set.intersection(*(set(parsed[p][1]) for p in uniq)))
    data = {f: np.full((len(times), len(points)), np.nan) for f in flds}
    for j, p in enumerate(points):
        for f in flds:
            a: FloatArray | None = parsed[p][1][f]
            if f == "wind_speed" and a is not None:
                a = _solar_wind(a, hs, j, opt.z0, opt.wind_height)
            if a is not None:
                data[f][:, j] = a
    data = {k: a for k, a in data.items() if np.isfinite(a).any()}
    prov = _solar_provenance(
        cov, height, hs, heights is not None, opt.z0, opt.wind_height
    )
    desc = parsed[uniq[0]][2]
    prov.update(
        {
            "aggregate": "hourly series (DHC_E)",
            "time_basis": desc["time_basis"] + ": the site states none; fitted "
            "on clear days against the solar zenith (no DST jump 2004-2016)",
            "hour_label": desc["hour_label"],
            "csv_columns": desc["columns"],
            "csv_variables": desc["variables"],
            "csv_metadata": {str(p): parsed[p][2]["metadata"] for p in uniq},
            "gaps_filled": {str(p): parsed[p][2]["gaps_filled"] for p in uniq},
            "gap_rule": "radiation: value 24 h earlier; others: linear",
            "download_profile": opt.profile,
        }
    )
    return PointWeather(list(times), list(points), data, list(times), prov)


def _need_wind_height(need: Sequence[str], opt: es.SolarOptions) -> None:
    if "wind_speed" in need and opt.wind_height is None:
        raise WeatherSourceError(
            "the Explorador Solar wind (vel) needs a height: pass "
            "--explorador-solar-wind-height H (default 5.5 m, the site's "
            "documented API wind height)"
        )


def solar_aggregate_points(
    cache: Fetcher,
    points: Sequence[tuple[float, float]],
    years: Sequence[int],
    *,
    height: float = 10.0,
    heights: PointHeights = None,
    z0: float = 0.03,
    wind_height: float | None = None,
) -> PointWeather:
    """Explorador Solar month x hour means (2004-2016) expanded on the UTC
    hours of ``years``: a climatology, never a time series.  The source's
    labels are fixed local time (UTC-4, hour-centred), whatever the tool's
    calendar, so daylight saving never shifts the diurnal cycle."""
    cov = coverage("explorador-solar", "aggregate")
    hs = _point_heights(points, height, heights)
    target = year_hours(years)
    names = {
        "temperature": "Temp",
        "wind_speed": "Vel",
        "ghi": "GHI",
        "dni": "DNI",
        "dhi": "DHI",
    }
    data = {f: np.full((len(target), len(points)), np.nan) for f in names}
    tabs: dict[tuple[float, float], dict[str, FloatArray]] = {}
    try:
        for p in dict.fromkeys(points):
            tabs[p] = es.parse_month_hour(es.aggregate_payload(cache, p[0], p[1]))
    except es.SolarError as e:
        raise WeatherSourceError(str(e)) from None
    for j, p in enumerate(points):
        for f, n in names.items():
            if n not in tabs[p]:
                continue
            a: FloatArray | None = es.expand_month_hour(tabs[p][n], target, -4.0)
            if f == "wind_speed" and a is not None:
                a = _solar_wind(a, hs, j, z0, wind_height)
            if a is not None:
                data[f][:, j] = a
    data = {k: a for k, a in data.items() if np.isfinite(a).any()}
    prov = _solar_provenance(cov, height, hs, heights is not None, z0, wind_height)
    prov.update(
        {
            "aggregate": "month x hour means 2004-2016 (HM, hour-major); DHI = "
            "GHI x monthly diffuse fraction DIFH/(DIRH+DIFH) of SM",
            "time_basis": "UTC-4 fixed, label = hour centre (inferred from the "
            "solar-noon position of GHI; the site states none)",
            "climatology": "month-hour",
            "climatology_stat": "mean",
            "requested_years": list(years),
        }
    )
    return PointWeather(target, list(points), data, None, prov)


def point_weather(  # pylint: disable=too-many-arguments
    source: str,
    cache: Fetcher,
    points: Sequence[tuple[float, float]],
    years: Sequence[int],
    *,
    model: str | None = None,
    climatology: str = "off",
    stat: str = "mean",
    utc_offset: CalendarBasis = -4.0,
    need: Sequence[str] = (),
    height: float = 10.0,
    heights: PointHeights = None,
    method: str = "bilinear",
    profile: str = "log",
    api_key: str | None = None,
    base_url: str | None = None,
    snap: float = 0.0,
    source_years: Sequence[int] | None = None,
    solar: es.SolarOptions | None = None,
) -> PointWeather:
    """Hourly weather at ``points`` on the UTC hours of ``years`` from
    ``source``; the one entry point both tools call.

    Years outside the source coverage need ``climatology`` (``calendar``
    or ``month-hour``), else :class:`WeatherSourceError`.  The climatology
    is built from ``source_years`` (default: the single year of a
    one-year source such as the Explorador WRF; required for open-ended
    sources).  ``need`` lists the fields the caller cannot do without."""
    if climatology not in CLIMATOLOGY_MODES:
        raise WeatherSourceError(
            f"climatology must be one of {CLIMATOLOGY_MODES}, got {climatology!r}"
        )
    cov = coverage(source, model)
    require_fields(cov, need)
    years = sorted(set(years))
    sopt = solar or es.SolarOptions()
    if source == "explorador-solar" and cov.model == "aggregate":
        if climatology != "month-hour":
            raise WeatherSourceError(
                "explorador-solar aggregate mode is a month x hour mean "
                "(2004-2016), not a time series: use --climatology month-hour "
                f"(got {climatology}); --explorador-solar-mode hourly gives "
                "the hourly series"
            )
        if stat != "mean":
            raise WeatherSourceError(
                "explorador-solar aggregate mode only has month x hour means "
                f"(--climatology-stat mean, got {stat})"
            )
        _need_wind_height(need, sopt)
        pw = solar_aggregate_points(
            cache,
            points,
            years,
            height=height,
            heights=heights,
            z0=sopt.z0,
            wind_height=sopt.wind_height,
        )
        need_fields = [f for f in need if f not in pw.data]
        if need_fields:
            raise WeatherSourceError(
                f"explorador-solar aggregate has no {', '.join(need_fields)}"
            )
        pw.provenance["local_calendar"] = describe_basis(utc_offset)
        return pw
    missing = cov.missing(years)
    src_years = years
    if missing:
        if climatology == "off":
            last = cov.last_year if cov.last_year is not None else "today"
            raise WeatherSourceError(
                f"weather source {source} ({cov.model}) covers "
                f"{cov.first_year}-{last}; years {missing} are outside it. "
                "Use --climatology calendar (replay the source year on the "
                "same local month/day/hour) or --climatology month-hour, "
                "or another --weather-source"
            )
        if source_years:
            src_years = sorted(set(source_years))
        elif cov.last_year == cov.first_year:
            src_years = [cov.first_year]
        else:
            raise WeatherSourceError(
                f"weather source {source} ({cov.model}): years {missing} are "
                "outside its coverage; pass --climatology-years (covered "
                "years to build the climatology from)"
            )
        bad = cov.missing(src_years)
        if bad:
            raise WeatherSourceError(
                f"climatology years {bad} are outside {source} ({cov.model}) "
                f"coverage {cov.first_year}-{cov.last_year or 'today'}"
            )
    if source == "explorador-solar":
        _need_wind_height(need, sopt)
        pw = solar_hourly_points(cache, points, sopt, height=height, heights=heights)
        keep_years = set(src_years)
        rows = [i for i, t in enumerate(pw.times) if t.year in keep_years]
        if not rows:
            raise WeatherSourceError(
                f"explorador-solar hourly file has no hours of {sorted(keep_years)}"
            )
        pw = PointWeather(
            [pw.times[i] for i in rows],
            pw.points,
            {k: v[rows] for k, v in pw.data.items()},
            [pw.times[i] for i in rows],
            pw.provenance,
        )
        lack = [f for f in need if f not in pw.data]
        if lack:
            raise WeatherSourceError(
                f"the Explorador Solar CSV has no {', '.join(lack)} "
                f"(columns: {pw.provenance.get('csv_columns')})"
            )
    elif source == "explorador":
        pw = explorador_points(
            cache,
            points,
            model=cov.model,
            height=height,
            heights=heights,
            method=method,
            profile=profile,
        )
    else:
        pw = open_meteo_points(
            cache,
            points,
            src_years,
            model=cov.model,
            height=height,
            heights=heights,
            profile=profile,
            api_key=api_key,
            base_url=base_url,
            snap=snap,
        )
    pw.provenance["requested_years"] = years
    pw.provenance["source_years_used"] = sorted({t.year for t in pw.times})
    pw.provenance["local_calendar"] = describe_basis(utc_offset)
    if climatology != "off" and missing:
        return apply_climatology(
            pw, years, climatology, stat=stat, utc_offset=utc_offset
        )
    return fill_to_years(pw, years, utc_offset)


def fill_to_years(
    pw: PointWeather, years: Sequence[int], basis: CalendarBasis = -4.0
) -> PointWeather:
    """``pw`` on exactly the UTC hours of ``years``.

    A source in local time (the Explorador, UTC−4) misses the first UTC
    hours of its year.  Those hours are filled by the calendar rule (the
    source hour with the same local month, day and hour), never dropped,
    and the fill is recorded in the provenance (``filled_hours``,
    ``fill_rule``, ``filled_times``) and in ``source_times``."""
    target = year_hours(years)
    have = {t: i for i, t in enumerate(pw.times)}
    gaps = [t for t in target if t not in have]
    if gaps:
        cal = calendar_index(pw.times, gaps, basis)
        fill = dict(zip(gaps, (int(r) for r in cal)))
        rows = np.array([have.get(t, fill.get(t, -1)) for t in target], np.int64)
    else:
        rows = np.array([have[t] for t in target], np.int64)
    prov = dict(pw.provenance)
    prov["filled_hours"] = len(gaps)
    if gaps:
        prov["fill_rule"] = (
            "calendar: the source hour with the same local month, day and hour"
        )
        prov["filled_times"] = [t.isoformat() for t in gaps]
    src = [pw.times[int(r)] for r in rows]
    return PointWeather(
        target,
        list(pw.points),
        {k: v[rows] for k, v in pw.data.items()},
        src,
        prov,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

WEATHER_SOURCE_HELP = (
    "weather source of temperature, wind and irradiance: explorador = "
    "Explorador Eolico WRF 1 km (Chile, year 2010 with --explorador-wrf "
    "wrf2010, 2015 with wrf2015); explorador-solar = Explorador Solar "
    "(2004-2016 hourly or month x hour means; needs "
    "--explorador-solar-profile; wind speed at 5.5 m, no direction) "
    "(Explorador data: no "
    "licence stated, not redistributed); open-meteo = Open-Meteo archive "
    "(ERA5 from 1940, ECMWF IFS from 2017; CC BY 4.0, the free API is "
    "NON-COMMERCIAL only: pass --api-key or --base-url for commercial use). "
    "Unset: the tool's previous default"
)


def add_weather_source_arguments(
    p: Any, *, with_open_meteo_keys: bool = True, with_model: bool = True
) -> None:
    """``--weather-source`` and its companions on an argparse (sub)parser."""
    g = p.add_argument_group("weather source")
    g.add_argument(
        "--weather-source",
        choices=WEATHER_SOURCES,
        default=None,
        help=WEATHER_SOURCE_HELP,
    )
    g.add_argument(
        "--climatology",
        choices=CLIMATOLOGY_MODES,
        default="off",
        help="years outside the source coverage: calendar = replay the source "
        "year on the same local month/day/hour; month-hour = per month x hour "
        "statistic (--climatology-stat) [%(default)s: fail]",
    )
    g.add_argument(
        "--climatology-stat",
        default="mean",
        help="month-hour statistic: mean, median or qNN (NN-th percentile) "
        "[%(default)s]",
    )
    g.add_argument(
        "--climatology-years",
        help="covered source years the climatology is built from, e.g. "
        "1991-2020 (default: the single year of the Explorador WRF)",
    )
    g.add_argument(
        "--climatology-utc-offset",
        type=float,
        default=None,
        help="explicit fixed UTC offset [h] of the climatology calendar; "
        "overrides the tool's --timezone/--dst (default: those, i.e. Chile "
        "UTC-4 standard time unless --dst on)",
    )
    g.add_argument(
        "--explorador-wrf",
        choices=sorted(k[1] for k in COVERAGE if k[0] == "explorador"),
        default="wrf2010",
        help="Explorador WRF hindcast year for --weather-source explorador "
        "[%(default)s]",
    )
    g.add_argument(
        "--explorador-solar-mode",
        choices=es.MODES,
        default="hourly",
        help="explorador-solar product: hourly = the 2004-2016 hourly series "
        "(DHC_E; needs --explorador-solar-profile); aggregate = month x hour "
        "means (no declaration; --climatology month-hour only) [%(default)s]",
    )
    g.add_argument(
        "--explorador-solar-profile",
        default=None,
        help="the download-profile declaration the Explorador Solar requires "
        "for the hourly series (no default; one of: "
        + "; ".join(es.PROFILES)
        + "). Sent with an anonymous session id, no personal data",
    )
    g.add_argument(
        "--explorador-solar-profile-other",
        default=None,
        help=f'text for the profile "{es.PROFILE_OTHER}" (1-60 characters)',
    )
    g.add_argument(
        "--explorador-solar-z0",
        type=float,
        default=0.03,
        help="roughness [m] of the log law that brings the Explorador "
        "Solar wind to the conductor / requested height [%(default)s]",
    )
    g.add_argument(
        "--explorador-solar-utc-offset",
        type=float,
        default=-4.0,
        help="time basis of the hourly CSV [h]; the site states none, the "
        "data fit Chile standard time with no DST [%(default)s]",
    )
    g.add_argument(
        "--explorador-solar-label",
        choices=("centre", "start"),
        default="centre",
        help="what the CSV stamp h means: centre = mean over h-30..h+30 min "
        "(fitted on the data; hour-start values = mean of two stamps); start "
        "= mean over h..h+1 [%(default)s]",
    )
    g.add_argument(
        "--explorador-solar-wind-height",
        type=float,
        default=es.WIND_HEIGHT,
        help="height [m] above ground of the Explorador Solar wind (vel); the "
        "CSV states none, the site's SOLEVAPP report documents the "
        "Exploradores API wind at 5.5 m [%(default)s]",
    )
    if with_model:
        g.add_argument(
            "--open-meteo-model",
            choices=sorted(k[1] for k in COVERAGE if k[0] == "open-meteo"),
            default=None,
            help="Open-Meteo archive model for --weather-source open-meteo "
            "(ERA5 0.25 deg from 1940, ERA5-Land, IFS 9 km from 2017) "
            "[--era5-model if the tool has it, else era5]",
        )
    if with_open_meteo_keys:
        g.add_argument("--api-key", help="Open-Meteo customer API key (commercial use)")
        g.add_argument("--base-url", help="Open-Meteo archive base URL (own server)")


def parse_years(text: str | None) -> list[int] | None:
    """``"1991-2020"`` or ``"2008,2010"`` (None/empty -> None)."""
    if not text:
        return None
    out: list[int] = []
    for part in text.split(","):
        a, _, b = part.strip().partition("-")
        try:
            lo, hi = int(a), int(b or a)
        except ValueError:
            raise WeatherSourceError(f"bad years {text!r}") from None
        out += range(lo, hi + 1)
    return sorted(set(out))


def calendar_basis(args: Any, tool_basis: CalendarBasis | None = None) -> CalendarBasis:
    """The climatology calendar: ``--climatology-utc-offset`` when given,
    else the tool's ``--timezone`` / ``--dst`` basis, else UTC−4."""
    off = getattr(args, "climatology_utc_offset", None)
    if off is not None:
        return float(off)
    return tool_basis if tool_basis is not None else -4.0


def solar_options(args: Any) -> es.SolarOptions:
    """The Explorador Solar options of the CLI."""
    return es.SolarOptions(
        profile=getattr(args, "explorador_solar_profile", None),
        profile_other=getattr(args, "explorador_solar_profile_other", None),
        z0=float(getattr(args, "explorador_solar_z0", 0.03)),
        utc_offset=float(getattr(args, "explorador_solar_utc_offset", -4.0)),
        wind_height=getattr(args, "explorador_solar_wind_height", es.WIND_HEIGHT),
        label=str(getattr(args, "explorador_solar_label", "centre")),
    )


def model_for(args: Any) -> str | None:
    """The model of the chosen ``--weather-source``."""
    if args.weather_source == "explorador":
        return str(args.explorador_wrf)
    if args.weather_source == "explorador-solar":
        return str(getattr(args, "explorador_solar_mode", "hourly"))
    if args.weather_source == "open-meteo":
        m = getattr(args, "open_meteo_model", None) or getattr(args, "era5_model", None)
        return str(m or "era5")
    return None
