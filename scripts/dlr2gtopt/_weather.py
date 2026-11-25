# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Per-span hourly weather: grids, interpolation and the data sources.

Every source produces a :class:`Weather` table: for each hour (UTC) and
each span, the four fields gtopt's ``Line.dlr`` critical span reads --
``ambient_temperature`` [degC], ``wind_speed`` [m/s], ``wind_direction``
[deg, meteorological: where the wind blows FROM] and ``solar_irradiance``
[W/m2, global horizontal] -- at the conductor height.

Interpolation (as in Matus et al. 2012, who interpolated the mesoscale
model to each span):

* horizontal: ``nearest`` grid node or ``bilinear`` between the four
  surrounding nodes of a regular lat/lon grid;
* vertical, wind: ``log`` (log-law through the two model levels),
  ``power`` (power law fitted to the two levels, exponent clipped) or
  ``none``; temperature: linear in height;
* direction: through the u/v components (no 0/360 wrap error); the speed
  is interpolated as a scalar so averaging opposite winds does not shrink
  it.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import urllib.parse
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from dlr2gtopt._spans import Span
from dlr2gtopt._towers import PoliteCache

logger = logging.getLogger("dlr2gtopt")

FloatArray = npt.NDArray[np.float64]
FIELDS = ("ambient_temperature", "wind_speed", "wind_direction", "solar_irradiance")
INTERP_METHODS = ("nearest", "bilinear")
WIND_PROFILES = ("log", "power", "none")
CORRECTIONS = ("none",)
Node = tuple[int, int]


# ---------------------------------------------------------------------------
# Weather table
# ---------------------------------------------------------------------------


@dataclass
class Weather:
    """Hourly weather of a set of spans: arrays shaped (hours, spans)."""

    times: list[datetime]
    spans: list[Span]
    data: dict[str, FloatArray] = field(default_factory=dict)

    def __post_init__(self) -> None:
        shape = (len(self.times), len(self.spans))
        for k, v in self.data.items():
            if v.shape != shape:
                raise ValueError(f"weather field {k}: shape {v.shape} != {shape}")

    def select(self, idx: Sequence[int]) -> Weather:
        """The weather of a subset of spans (by column index)."""
        return Weather(
            self.times,
            [self.spans[i] for i in idx],
            {k: v[:, list(idx)] for k, v in self.data.items()},
        )

    def write(self, path: Path) -> None:
        """Long table ``time,line,span,<fields>`` (.parquet or .csv)."""
        import pandas as pd

        n_t, n_s = len(self.times), len(self.spans)
        cols: dict[str, Any] = {
            "time": np.repeat(np.array(self.times, dtype="datetime64[s]"), n_s),
            "line": np.tile([s.line for s in self.spans], n_t),
            "span": np.tile([s.span for s in self.spans], n_t),
        }
        for k in FIELDS:
            if k in self.data:
                cols[k] = self.data[k].reshape(-1)
        df = pd.DataFrame(cols)
        if path.suffix == ".parquet":
            df.to_parquet(path, index=False)
        else:
            df.to_csv(path, index=False, float_format="%.4f")


def read_weather(path: Path, spans: Sequence[Span]) -> Weather:
    """Read a long weather table (written by :meth:`Weather.write`, or a
    user CSV with the same columns) for the given spans."""
    import pandas as pd

    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"])
    if "line" not in df.columns:
        df["line"] = spans[0].line if spans else ""
    times = sorted(df["time"].unique())
    t_idx = {t: i for i, t in enumerate(times)}
    s_idx = {(s.line, s.span): j for j, s in enumerate(spans)}
    rows = np.array([t_idx[t] for t in df["time"]])
    keys = list(zip(df["line"].astype(str), df["span"].astype(int)))
    keep = np.array([k in s_idx for k in keys])
    cols = np.array([s_idx.get(k, -1) for k in keys])
    data: dict[str, FloatArray] = {}
    for k in FIELDS:
        if k not in df.columns:
            continue
        arr = np.full((len(times), len(spans)), np.nan)
        arr[rows[keep], cols[keep]] = df[k].to_numpy(dtype=float)[keep]
        data[k] = arr
    py_times = [pd.Timestamp(t).to_pydatetime() for t in times]
    return Weather(py_times, list(spans), data)


# ---------------------------------------------------------------------------
# Grid and interpolation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegularGrid:
    """Regular lat/lon grid: node (i, j) at (lat0 + i dlat, lon0 + j dlon)."""

    dlat: float
    dlon: float
    lat0: float = 0.0
    lon0: float = 0.0

    def node_coords(self, node: Node) -> tuple[float, float]:
        """(lat, lon) of a node."""
        return self.lat0 + node[0] * self.dlat, self.lon0 + node[1] * self.dlon

    def stencil(
        self, lat: float, lon: float, method: str = "bilinear"
    ) -> list[tuple[Node, float]]:
        """Nodes and weights interpolating the point (weights sum to 1)."""
        fi = (lat - self.lat0) / self.dlat
        fj = (lon - self.lon0) / self.dlon
        if method == "nearest":
            return [((round(fi), round(fj)), 1.0)]
        if method != "bilinear":
            raise ValueError(f"interpolation must be one of {INTERP_METHODS}")
        i0 = math.floor(round(fi, 9))
        j0 = math.floor(round(fj, 9))
        a, b = fi - i0, fj - j0
        out = [
            ((i0, j0), (1 - a) * (1 - b)),
            ((i0 + 1, j0), a * (1 - b)),
            ((i0, j0 + 1), (1 - a) * b),
            ((i0 + 1, j0 + 1), a * b),
        ]
        return [(n, w) for n, w in out if w > 1e-12]

    def nodes_for(
        self, points: Iterable[tuple[float, float]], method: str = "bilinear"
    ) -> list[Node]:
        """Deduplicated nodes needed by all points (sorted)."""
        need: set[Node] = set()
        for la, lo in points:
            need.update(n for n, _ in self.stencil(la, lo, method))
        return sorted(need)


def uv_from_dir(
    speed: FloatArray, direction: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """Meteorological direction (FROM, deg) -> (u east, v north) components."""
    rad = np.radians(direction)
    return -speed * np.sin(rad), -speed * np.cos(rad)


def dir_from_uv(u: FloatArray, v: FloatArray) -> FloatArray:
    """(u, v) -> meteorological direction in [0, 360)."""
    return np.mod(np.degrees(np.arctan2(-u, -v)), 360.0)


def vertical_wind(
    levels: Mapping[float, FloatArray],
    height: float,
    profile: str = "log",
    min_alpha: float = -0.5,
    max_alpha: float = 1.0,
) -> FloatArray:
    """Wind speed at ``height`` from speeds at one or two model levels."""
    if profile not in WIND_PROFILES:
        raise ValueError(f"wind profile must be one of {WIND_PROFILES}")
    hs = sorted(levels)
    if len(hs) == 1 or profile == "none":
        h = min(hs, key=lambda x: abs(x - height))
        return np.asarray(levels[h], dtype=float)
    h1, h2 = hs[0], hs[-1]
    v1 = np.asarray(levels[h1], dtype=float)
    v2 = np.asarray(levels[h2], dtype=float)
    if profile == "log":
        out = v1 + (v2 - v1) * math.log(height / h1) / math.log(h2 / h1)
    else:
        with np.errstate(divide="ignore", invalid="ignore"):
            alpha = np.log(v2 / v1) / math.log(h2 / h1)
        alpha = np.clip(
            np.nan_to_num(alpha, nan=0.0, posinf=0.0, neginf=0.0), min_alpha, max_alpha
        )
        out = v1 * (height / h1) ** alpha
    return np.maximum(out, 0.0)


def vertical_linear(levels: Mapping[float, FloatArray], height: float) -> FloatArray:
    """Linear interpolation/extrapolation in height (temperature, u, v)."""
    hs = sorted(levels)
    if len(hs) == 1:
        return np.asarray(levels[hs[0]], dtype=float)
    h1, h2 = hs[0], hs[-1]
    v1 = np.asarray(levels[h1], dtype=float)
    v2 = np.asarray(levels[h2], dtype=float)
    return v1 + (v2 - v1) * (height - h1) / (h2 - h1)


@dataclass
class NodeSeries:
    """Raw model series at one grid node: field -> {height: array}."""

    speed: dict[float, FloatArray]
    direction: dict[float, FloatArray]
    temperature: dict[float, FloatArray]
    ghi: FloatArray | None


def at_height(
    ns: NodeSeries, height: float, profile: str
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
    """(speed, u, v, temperature) at conductor height for one node."""
    spd = vertical_wind(ns.speed, height, profile)
    us, vs = {}, {}
    for h, d in ns.direction.items():
        u, v = uv_from_dir(ns.speed.get(h, spd), d)
        us[h], vs[h] = u, v
    u = vertical_linear(us, height)
    v = vertical_linear(vs, height)
    return spd, u, v, vertical_linear(ns.temperature, height)


def interpolate_spans(
    grid: RegularGrid,
    spans: Sequence[Span],
    node_series: Mapping[Node, NodeSeries],
    times: list[datetime],
    *,
    method: str = "bilinear",
    height: float = 15.0,
    profile: str = "log",
) -> Weather:
    """Per-span weather from gridded node series (vertical then horizontal)."""
    cache: dict[Node, tuple[FloatArray, ...]] = {}
    n_t, n_s = len(times), len(spans)
    out = {k: np.zeros((n_t, n_s)) for k in FIELDS}
    has_ghi = all(ns.ghi is not None for ns in node_series.values())
    for j, s in enumerate(spans):
        u_acc = np.zeros(n_t)
        v_acc = np.zeros(n_t)
        for node, w in grid.stencil(s.lat, s.lon, method):
            if node not in cache:
                ns = node_series[node]
                ghi = ns.ghi if ns.ghi is not None else np.full(n_t, np.nan)
                cache[node] = (*at_height(ns, height, profile), ghi)
            spd, u, v, ta, ghi = cache[node]
            out["wind_speed"][:, j] += w * spd
            out["ambient_temperature"][:, j] += w * ta
            out["solar_irradiance"][:, j] += w * ghi
            u_acc += w * u
            v_acc += w * v
        out["wind_direction"][:, j] = dir_from_uv(u_acc, v_acc)
    if not has_ghi:
        del out["solar_irradiance"]
        logger.warning("weather: no solar irradiance from this source")
    return Weather(times, list(spans), out)


# ---------------------------------------------------------------------------
# Explorador Eolico (Chile, WRF 1 km hindcast)
# ---------------------------------------------------------------------------

EXPLORADOR_URL = "https://eolico.minenergia.cl"
# WRF-2010 hindcast nodes sit at multiples of 0.01 deg in lat and lon
# (found by bisecting the server's nearest-node snapping).
EXPLORADOR_GRID = RegularGrid(dlat=0.01, dlon=0.01)
EXPLORADOR_MODELS = {
    "wrf2010": {"nombre": "WRF 2010", "value": "wrf2010", "recon": "off", "index": 0}
}
EXPLORADOR_UTC_OFFSET = -4.0  # series are in Chilean standard time (UTC-4)


class Explorador:
    """Client of the public hourly-series download of the Explorador Eolico
    (Ministerio de Energia / DGF Universidad de Chile).  Cached, polite."""

    def __init__(
        self,
        cache: PoliteCache,
        model: str = "wrf2010",
        base_url: str = EXPLORADOR_URL,
    ) -> None:
        if model not in EXPLORADOR_MODELS:
            raise ValueError(
                f"explorador model must be one of {list(EXPLORADOR_MODELS)}"
            )
        self.cache = cache
        self.model = model
        self.base = base_url.rstrip("/")

    def series_csv(
        self, lat: float, lon: float, variable: str, heights: Sequence[float]
    ) -> str:
        """Hourly CSV text of one variable at one point."""
        hs = "-".join(f"{h:g}" for h in heights)
        key = f"{self.model}/{lat:.5f}_{lon:.5f}_{variable}_{hs}.csv"
        path = self.cache.cache_dir / key
        if path.exists():
            return path.read_text(encoding="utf-8")
        q = {
            "tipo": "DescargaExploracion",
            "datos": {
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "address": "",
                "nombre_sitio": "S",
                "descarga": "SerieHorariaVientoWrf2010",
                "modelo": EXPLORADOR_MODELS[self.model],
                "variable": variable,
                "alturas": list(heights),
            },
        }
        router = (
            self.base
            + "/python-router/"
            + urllib.parse.quote(json.dumps(q, separators=(",", ":")))
        )
        reply = json.loads(self.cache.fetch(key + ".router.json", router))
        body = self.cache.fetch(key, f"{self.base}/temp/{reply['_serverData']}")
        (self.cache.cache_dir / (key + ".router.json")).unlink(missing_ok=True)
        return body.decode("utf-8")

    def node_series(
        self, lat: float, lon: float, wind_heights: Sequence[float] = (10, 20)
    ) -> tuple[list[datetime], NodeSeries]:
        """All fields at one grid node."""
        times, spd = parse_explorador_csv(
            self.series_csv(lat, lon, "vel", wind_heights)
        )
        _, wdir = parse_explorador_csv(self.series_csv(lat, lon, "vdir", wind_heights))
        _, temp = parse_explorador_csv(self.series_csv(lat, lon, "temp", wind_heights))
        _, glb = parse_explorador_csv(self.series_csv(lat, lon, "glb", [10]))
        return times, NodeSeries(spd, wdir, temp, next(iter(glb.values())))


def parse_explorador_csv(
    text: str, utc_offset_h: float = EXPLORADOR_UTC_OFFSET
) -> tuple[list[datetime], dict[float, FloatArray]]:
    """Explorador CSV (``,h1,h2`` header, ``YYYY-mm-dd HH:MM:SS`` rows) ->
    UTC times and one array per height; blanks are linearly interpolated."""
    rows = list(csv.reader(io.StringIO(text)))
    heights = [float(h) for h in rows[0][1:]]
    body = [r for r in rows[1:] if r]
    shift = timedelta(hours=-utc_offset_h)
    times = [datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S") + shift for r in body]
    out: dict[float, FloatArray] = {}
    for k, h in enumerate(heights):
        a = np.array(
            [
                float(r[k + 1]) if len(r) > k + 1 and r[k + 1] != "" else np.nan
                for r in body
            ]
        )
        out[h] = fill_gaps(a)
    return times, out


def fill_gaps(a: FloatArray) -> FloatArray:
    """Linear interpolation over NaN (edges take the nearest value)."""
    bad = np.isnan(a)
    if bad.all():
        raise ValueError("series has no data")
    if bad.any():
        idx = np.arange(a.size)
        a = a.copy()
        a[bad] = np.interp(idx[bad], idx[~bad], a[~bad])
    return a


def explorador_weather(
    client: Explorador,
    spans: Sequence[Span],
    *,
    method: str = "bilinear",
    height: float = 15.0,
    profile: str = "log",
    grid: RegularGrid = EXPLORADOR_GRID,
    progress: Callable[[int, int], None] | None = None,
) -> Weather:
    """Per-span weather from the Explorador grid nodes around each span."""
    nodes = grid.nodes_for(((s.lat, s.lon) for s in spans), method)
    series: dict[Node, NodeSeries] = {}
    times: list[datetime] = []
    for k, node in enumerate(nodes, start=1):
        la, lo = grid.node_coords(node)
        times, series[node] = client.node_series(la, lo)
        if progress is not None:
            progress(k, len(nodes))
    return interpolate_spans(
        grid, spans, series, times, method=method, height=height, profile=profile
    )


# ---------------------------------------------------------------------------
# Open-Meteo (global NWP forecasts, point API)
# ---------------------------------------------------------------------------

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_CUSTOMER_URL = "https://customer-api.open-meteo.com/v1/forecast"
OPEN_METEO_MODELS = ("best_match", "ecmwf_ifs025", "gfs_seamless", "icon_seamless")
OPEN_METEO_ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
_OM_VARS = (
    "temperature_2m,temperature_80m,wind_speed_10m,wind_speed_80m,"
    "wind_direction_10m,wind_direction_80m,shortwave_radiation"
)


def open_meteo_url(
    lats: Sequence[float],
    lons: Sequence[float],
    *,
    model: str = "best_match",
    forecast_days: int = 3,
    past_days: int = 0,
    api_key: str | None = None,
    base_url: str | None = None,
) -> str:
    """Open-Meteo forecast URL for several points (one request)."""
    base = base_url or (OPEN_METEO_CUSTOMER_URL if api_key else OPEN_METEO_URL)
    params = {
        "latitude": ",".join(f"{x:.5f}" for x in lats),
        "longitude": ",".join(f"{x:.5f}" for x in lons),
        "hourly": _OM_VARS,
        "models": model,
        "wind_speed_unit": "ms",
        "timezone": "GMT",
        "forecast_days": str(forecast_days),
        "past_days": str(past_days),
    }
    if api_key:
        params["apikey"] = api_key
    return base + "?" + urllib.parse.urlencode(params, safe=",")


def parse_open_meteo(
    payload: Any, height: float, profile: str = "log"
) -> tuple[list[datetime], list[NodeSeries]]:
    """Open-Meteo JSON (one object or a list for several points)."""
    items = payload if isinstance(payload, list) else [payload]
    times: list[datetime] = []
    out = []
    for it in items:
        h = it["hourly"]
        times = [datetime.strptime(t, "%Y-%m-%dT%H:%M") for t in h["time"]]

        def arr(name: str, hh: Mapping[str, Any] = h) -> FloatArray:
            return fill_gaps(
                np.array([np.nan if x is None else x for x in hh[name]], float)
            )

        out.append(
            NodeSeries(
                speed={10.0: arr("wind_speed_10m"), 80.0: arr("wind_speed_80m")},
                direction={
                    10.0: arr("wind_direction_10m"),
                    80.0: arr("wind_direction_80m"),
                },
                temperature={2.0: arr("temperature_2m"), 80.0: arr("temperature_80m")},
                ghi=arr("shortwave_radiation"),
            )
        )
    return times, out


def open_meteo_weather(
    cache: PoliteCache,
    spans: Sequence[Span],
    *,
    height: float = 15.0,
    profile: str = "log",
    batch: int = 50,
    **url_kw: Any,
) -> Weather:
    """Per-span weather from Open-Meteo at each span midpoint (the API picks
    the model cell; several spans per request)."""
    series: list[NodeSeries] = []
    times: list[datetime] = []
    for k in range(0, len(spans), batch):
        chunk = spans[k : k + batch]
        url = open_meteo_url([s.lat for s in chunk], [s.lon for s in chunk], **url_kw)
        key_src = url.split("apikey=", maxsplit=1)[0]
        import hashlib

        key = "open_meteo/" + hashlib.sha1(key_src.encode()).hexdigest()[:16] + ".json"
        t, ns = parse_open_meteo(json.loads(cache.fetch(key, url)), height, profile)
        times = t
        series += ns
    grid = RegularGrid(1.0, 1.0)
    pseudo = [
        Span(
            s.line,
            s.span,
            float(i),
            0.0,
            s.azimuth,
            s.length_m,
            s.chainage_km,
            s.elevation,
        )
        for i, s in enumerate(spans)
    ]
    w = interpolate_spans(
        grid,
        pseudo,
        {(i, 0): n for i, n in enumerate(series)},
        times,
        method="nearest",
        height=height,
        profile=profile,
    )
    return Weather(w.times, list(spans), w.data)


# ---------------------------------------------------------------------------
# ECMWF open data (IFS / AIFS, GRIB2 0.25 deg)
# ---------------------------------------------------------------------------

ECMWF_PARAMS = ("10u", "10v", "100u", "100v", "2t", "ssrd")
ECMWF_ATTRIBUTION = "Contains ECMWF open data (CC BY 4.0)"
GridField = tuple[list[datetime], FloatArray, FloatArray, FloatArray]
"""(valid times, lats (ny), lons (nx), values (time, ny, nx))."""


def ecmwf_fetch(
    target: Path,
    *,
    model: str = "ifs",
    source: str = "ecmwf",
    steps: Sequence[int] = tuple(range(0, 145, 3)),
    params: Sequence[str] = ECMWF_PARAMS,
    client_factory: Callable[..., Any] | None = None,
) -> Path:
    """Download the latest run with the official ``ecmwf-opendata`` client."""
    if client_factory is None:
        try:
            from ecmwf.opendata import Client  # ty: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "ecmwf-opendata is not installed (pip install ecmwf-opendata)"
            ) from exc
        client_factory = Client
    client = client_factory(source=source, model=model)
    target.parent.mkdir(parents=True, exist_ok=True)
    client.retrieve(type="fc", step=list(steps), param=list(params), target=str(target))
    return target


def read_grib(
    path: Path, open_datasets: Callable[[str], Iterable[Any]] | None = None
) -> dict[str, GridField]:
    """GRIB2 -> {param: GridField} through cfgrib (optional dependency)."""
    if open_datasets is None:
        try:
            import cfgrib  # ty: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("reading GRIB needs cfgrib (and eccodes)") from exc
        open_datasets = cfgrib.open_datasets
    names = {
        "u10": "10u",
        "v10": "10v",
        "u100": "100u",
        "v100": "100v",
        "t2m": "2t",
        "ssrd": "ssrd",
    }
    out: dict[str, GridField] = {}
    for ds in open_datasets(str(path)):
        for var in ds.data_vars:
            key = names.get(str(var))
            if key is None:
                continue
            da = ds[var]
            valid = np.atleast_1d(np.asarray(da["valid_time"].values))
            stamps = valid.astype("datetime64[s]").astype("int64")
            times = [
                datetime.fromtimestamp(int(t), tz=timezone.utc).replace(tzinfo=None)
                for t in stamps
            ]
            vals = np.asarray(da.values, dtype=float).reshape(
                len(times), da.shape[-2], da.shape[-1]
            )
            out[key] = (
                times,
                np.asarray(da["latitude"].values, dtype=float),
                np.asarray(da["longitude"].values, dtype=float),
                vals,
            )
    return out


def deaccumulate(times: list[datetime], acc: FloatArray) -> FloatArray:
    """Accumulated J/m2 since run start -> mean W/m2 over each step."""
    out = np.zeros_like(acc)
    for k in range(1, len(times)):
        dt = (times[k] - times[k - 1]).total_seconds()
        out[k] = np.maximum(acc[k] - acc[k - 1], 0.0) / dt
    if len(times) > 1:
        out[0] = out[1]
    return out


def ecmwf_weather(
    fields: Mapping[str, GridField],
    spans: Sequence[Span],
    *,
    method: str = "bilinear",
    height: float = 15.0,
    profile: str = "log",
) -> Weather:
    """Per-span weather from ECMWF open-data fields (same bilinear path)."""
    if "10u" not in fields or "10v" not in fields or "2t" not in fields:
        raise ValueError("ecmwf: need at least 10u, 10v and 2t")
    times, lats, lons, _ = fields["10u"]
    dlat = float(lats[1] - lats[0])
    dlon = float(lons[1] - lons[0])
    grid = RegularGrid(dlat, dlon, float(lats[0]), float(lons[0]))
    ssrd = None
    if "ssrd" in fields:
        ssrd = deaccumulate(times, fields["ssrd"][3])
    else:
        logger.warning("ecmwf: no ssrd in the data; solar_irradiance left unset")
    series: dict[Node, NodeSeries] = {}
    for node in grid.nodes_for(((s.lat, s.lon) for s in spans), method):
        i, j = node
        j %= len(lons)

        def at(p: str, ii: int = i, jj: int = j) -> FloatArray:
            return np.asarray(fields[p][3][:, ii, jj], dtype=float)

        spd: dict[float, FloatArray] = {}
        drn: dict[float, FloatArray] = {}
        for h, (pu, pv) in ((10.0, ("10u", "10v")), (100.0, ("100u", "100v"))):
            if pu in fields and pv in fields:
                u, v = at(pu), at(pv)
                spd[h] = np.hypot(u, v)
                drn[h] = dir_from_uv(u, v)
        series[node] = NodeSeries(
            spd, drn, {2.0: at("2t") - 273.15}, None if ssrd is None else ssrd[:, i, j]
        )
    return interpolate_spans(
        grid, spans, series, times, method=method, height=height, profile=profile
    )


def apply_correction(weather: Weather, correction: str = "none") -> Weather:
    """Hook for a per-span statistical correction of a coarse forecast
    (e.g. MOS fitted against the Explorador 1 km history).  Only ``none``
    is implemented; see the README for how to plug one in."""
    if correction not in CORRECTIONS:
        raise ValueError(f"correction must be one of {CORRECTIONS}")
    return weather
