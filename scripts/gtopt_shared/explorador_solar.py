# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Explorador Solar (Ministerio de Energía de Chile), <https://solar.minenergia.cl>.

Satellite-derived (GOES) irradiance and meteorology, 2004–2016, at any
point of Chile, through the site's own ``/python-router/<json>`` endpoint
(found in ``app/explorador/factories/ApiService.js``).  Two products:

* **hourly** (``tipo: "DHC_E"``, "Datos Horarios 2004 - 2016", CSV): the
  request returns a file name, read with ``GET /temp/<file>``.  The site
  REQUIRES a download-profile declaration with every download
  (``opciones.registro = {perfil, perfilOtro, sesionId}``, see
  ``app/explorador/factories/PerfilDescargaService.js``); without it the
  server answers HTTP 400 "Perfil de usuario no válido para la descarga".
  The profile is one of :data:`PROFILES`, chosen by the user
  (``--explorador-solar-profile``, no default); the session id is an
  anonymous random UUID, as the site generates it.  No personal data is
  sent.
* **aggregate** (``tipo: "ExploracionCompleta"``, no declaration): monthly,
  daily-cycle and month × hour means over 2004–2016.  ``HM`` holds 12 × 24
  month-hour means of GHI, GLB (tilted plane), DNI, Temp, Vel, Nube,
  Sombra and DIRP in **hour-major** order (index ``hour * 12 + month``,
  the site's ``list2matrix``).  There is no month-hour diffuse field:
  DHI is derived as ``GHI(m, h) · k_d(m)``, with ``k_d(m) = DIFH / (DIRH +
  DIFH)`` the daily diffuse fraction of month m from ``SM`` (energy
  consistent per month, DHI ≤ GHI).

Time basis.  The site labels hours without a zone.  Fitted on a real
hourly file (−31.6, −71.4; 1643 cloud-free days 2004–2016) against the
computed solar zenith: **fixed UTC−4, no daylight-saving jump, stamp =
centre of the averaging hour** (window h−30..h+30 min: RMSE 33 W/m²
vs 70 for hour start and 62 for hour end).  The aggregates agree (GHI
centroid 3.5 h before UTC solar noon all year).  Both are recorded in
the provenance; ``utc_offset`` and ``label`` are options.

Wind: speed only, no direction.  The CSV states no height for ``vel``
(the download request's ``hgt`` is the site's SRTM ground elevation).
The site's SOLEVAPP report documents the Exploradores API wind at
5.5 m (:data:`WIND_HEIGHT_DOC`), the default of
``SolarOptions.wind_height``; ``None`` leaves the wind out.  The speed is
brought to another height with a neutral log law of roughness ``z0``
(default 0.03 m, open terrain).

Licence: none stated.  Fetched by script, cited, cached locally, never
redistributed or committed.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

SOLAR_URL = "https://solar.minenergia.cl"
SOLAR_FIRST_YEAR = 2004
SOLAR_LAST_YEAR = 2016
WIND_HEIGHT = 5.5
WIND_HEIGHT_DOC = (
    "Explorador Solar SOLEVAPP report (Modulo_Evaporacion_DV.pdf, sec. 2, "
    "p. 5-6): the Exploradores API gives 'velocidad del viento a 5.5 metros "
    "de altura'; that the DHC_E vel column is this series is inferred, the "
    f"CSV states no height. {SOLAR_URL}/downloads/Modulo_Evaporacion_DV.pdf"
)
MODES = ("hourly", "aggregate")
PROFILES = (
    "Instalador Eléctrico",
    "Empresa eléctrica o energética",
    "Empresa Consultora",
    "Sector Público",
    "Academia o Investigación",
    "Estudiante",
    "Público general",
    "Otro, no reflejado en los anteriores",
)
"""The site's download profiles, verbatim (``PerfilDescargaService.js``)."""
PROFILE_OTHER = "Otro, no reflejado en los anteriores"
PROFILE_OTHER_MAX = 60
HM_FIELDS = ("GHI", "GLB", "DNI", "Temp", "Vel", "Nube", "Sombra", "DIRP")
ROUTER_PATTERN = (
    SOLAR_URL + '/python-router/{"registro":{"perfil":...,"sesionId":...},'
    '"tipo":"DHC_E","datos":{"lat":...,"lon":...}} then ' + SOLAR_URL + "/temp/<file>"
)
AGGREGATE_PATTERN = (
    SOLAR_URL + '/python-router/{"tipo":"ExploracionCompleta","datos":{"lat":...,'
    '"lon":...}}'
)
LICENCE = (
    "Explorador Solar, Ministerio de Energia de Chile; no licence stated: "
    "cite, fetch by script, do not redistribute"
)


class SolarError(ValueError):
    """The Explorador Solar cannot supply what was asked."""


class Fetcher(Protocol):  # pylint: disable=too-few-public-methods
    """A cached, polite HTTP getter (``dlr2gtopt._towers.PoliteCache``)."""

    def fetch(self, key: str, url: str) -> bytes:
        """Body of ``url``, cached under ``key``."""


@dataclass(frozen=True)
class SolarOptions:
    """What the hourly download needs from the user."""

    profile: str | None = None
    profile_other: str | None = None
    z0: float = 0.03
    utc_offset: float = -4.0
    session_id: str | None = None
    wind_height: float | None = WIND_HEIGHT
    label: str = "centre"


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re.sub(
        "[\\u0000-\\u001f\\u007f-\\u009f\\u200b-\\u200f\\u202a-\\u202e]", "", text
    )
    return re.sub(r"\s+", " ", text).strip()


def registro(opt: SolarOptions) -> dict[str, Any]:
    """The site's ``opciones.registro``; fails unless the user chose a profile."""
    allowed = "; ".join(f'"{p}"' for p in PROFILES)
    if not opt.profile:
        raise SolarError(
            "the Explorador Solar hourly download requires a download-profile "
            "declaration: pass --explorador-solar-profile with one of the "
            f"site's types: {allowed} (or use --explorador-solar-mode aggregate, "
            "which needs none)"
        )
    if opt.profile not in PROFILES:
        raise SolarError(
            f"--explorador-solar-profile {opt.profile!r} is not one of {allowed}"
        )
    other = None
    if opt.profile == PROFILE_OTHER:
        other = _clean(opt.profile_other or "")
        if not 1 <= len(other) <= PROFILE_OTHER_MAX:
            raise SolarError(
                f'profile "{PROFILE_OTHER}" needs --explorador-solar-profile-other '
                f"(1-{PROFILE_OTHER_MAX} characters)"
            )
    return {
        "perfil": opt.profile,
        "perfilOtro": other,
        "sesionId": opt.session_id or str(uuid.uuid4()),
    }


def router_url(query: dict[str, Any], base: str = SOLAR_URL) -> str:
    """``<base>/python-router/<url-encoded compact JSON>``."""
    return (
        base.rstrip("/")
        + "/python-router/"
        + urllib.parse.quote(
            json.dumps(query, separators=(",", ":"), ensure_ascii=False)
        )
    )


def _point_key(lat: float, lon: float) -> str:
    return f"{lat:.5f}_{lon:.5f}"


def _router(cache: Fetcher, key: str, url: str) -> Any:
    try:
        return json.loads(cache.fetch(key, url))
    except urllib.error.HTTPError as e:
        raise SolarError(
            f"Explorador Solar refused the request (HTTP {e.code}); for the "
            "hourly download check --explorador-solar-profile"
        ) from None


def hourly_csv(
    cache: Fetcher, lat: float, lon: float, opt: SolarOptions, base: str = SOLAR_URL
) -> str:
    """The hourly 2004–2016 CSV text at a point (``DHC_E``), cached.

    The router reply is not kept (it names a temporary file); the CSV is."""
    key = f"explorador_solar/{_point_key(lat, lon)}_DHC_E.csv"
    cache_dir = getattr(cache, "cache_dir", None)
    if cache_dir is not None and (cache_dir / key).exists():
        return str((cache_dir / key).read_text(encoding="utf-8"))
    query = {
        "registro": registro(opt),
        "tipo": "DHC_E",
        "datos": {
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "address": "",
            "nombre_sitio": "S",
        },
    }
    reply = _router(cache, key + ".router.json", router_url(query, base))
    if cache_dir is not None:
        (cache_dir / (key + ".router.json")).unlink(missing_ok=True)
    if not isinstance(reply, dict) or not reply.get("response"):
        text = reply.get("_serverText") if isinstance(reply, dict) else reply
        raise SolarError(f"Explorador Solar hourly download failed: {text!r}")
    body = cache.fetch(key, f"{base.rstrip('/')}/temp/{reply['response']}")
    return body.decode("utf-8-sig", errors="replace")


# ---------------------------------------------------------------------------
# Hourly CSV (layout of the real "DATOS HORARIOS" file)
# ---------------------------------------------------------------------------

DATA_HEADER = "Fecha/Hora"
COLUMNS = {
    "ghi": "ghi",
    "dni": "dni",
    "difh": "dhi",
    "temp": "temperature",
    "vel": "wind_speed",
}
"""CSV column -> field.  ``glb``/``dir``/``dif``/``sct`` are the tilted-plane
components and ``dirh`` the horizontal beam: not used."""
RADIATION = frozenset({"ghi", "dni", "dhi"})
_META_KEYS = {
    "VERSION DEL MODELO": "model_version",
    "FUENTE": "source",
    "DESCRIPCION": "description",
    "ALTURA": "site_altitude_m",
    "LATITUD": "site_lat",
    "LONGITUD": "site_lon",
    "FECHA INICIAL": "first_time",
    "FECHA FINAL": "last_time",
    "INTERVALO": "interval",
    "METODO DE CALCULO": "method",
}
LABEL = "centre"
"""The stamp h is the mean of 10-minute values over [h-30 min, h+30 min)
local standard time (fitted on clear days against the solar zenith: the
centred window has half the error of hour-start or hour-end, and no
daylight-saving jump in 2004-2016)."""


def _fill_radiation(a: FloatArray) -> tuple[FloatArray, int]:
    """NaN radiation takes the value 24 h earlier (persistence of the
    diurnal cycle; the file ends with ~4 NaN days), else 24 h later."""
    a = a.copy()
    bad = int(np.isnan(a).sum())
    for k in range(a.size):
        if np.isnan(a[k]) and k >= 24:
            a[k] = a[k - 24]
    for k in range(a.size - 1, -1, -1):
        if np.isnan(a[k]) and k + 24 < a.size:
            a[k] = a[k + 24]
    return a, bad


def _interp(a: FloatArray) -> tuple[FloatArray, int]:
    bad = np.isnan(a)
    n = int(bad.sum())
    if n and not bad.all():
        idx = np.arange(a.size)
        a = a.copy()
        a[bad] = np.interp(idx[bad], idx[~bad], a[~bad])
    return a, n


def parse_hourly_csv(
    text: str, utc_offset: float = -4.0, label: str = LABEL
) -> tuple[list[datetime], dict[str, FloatArray], dict[str, Any]]:
    """The Explorador Solar hourly CSV -> UTC hour-start times, fields and
    the file's description (metadata, variable table, gap counts).

    Layout: a metadata block (``KEY,value`` lines), a ``VARIABLE,UNIDAD,
    DESCRIPCION`` table, then ``Fecha/Hora,glb,dir,dif,sct,ghi,dirh,difh,
    dni,temp,vel,shadow,cloud`` rows in local standard time with literal
    ``NaN``.  With ``label = "centre"`` (the file's convention) the value
    of hour-start t is the mean of the stamps t and t+1; ``"start"`` keeps
    the stamps."""
    if label not in ("centre", "start"):
        raise SolarError(f"label must be centre or start, got {label!r}")
    lines = text.splitlines()
    meta: dict[str, Any] = {}
    variables: dict[str, dict[str, str]] = {}
    start = None
    in_vars = False
    for k, line in enumerate(lines):
        row = next(csv.reader([line]), [])
        if row and row[0] == DATA_HEADER:
            start = k
            break
        if row[:3] == ["VARIABLE", "UNIDAD", "DESCRIPCION"]:
            in_vars = True
            continue
        if in_vars and len(row) >= 3:
            variables[row[0]] = {"unit": row[1], "description": row[2]}
            continue
        in_vars = in_vars and bool(row and row[0].strip('"'))
        if len(row) >= 2 and row[0] in _META_KEYS:
            meta[_META_KEYS[row[0]]] = row[1]
    if start is None:
        raise SolarError(f"Explorador Solar CSV: no {DATA_HEADER!r} header row")
    header = next(csv.reader([lines[start]]))
    cols = {c: header.index(c) for c in COLUMNS if c in header}
    if "ghi" not in cols or "temp" not in cols:
        raise SolarError(f"Explorador Solar CSV: no ghi or temp column in {header}")
    rows = [r for r in csv.reader(lines[start + 1 :]) if r and r[0]]
    try:
        local = [datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S") for r in rows]
    except ValueError as e:
        raise SolarError(f"Explorador Solar CSV: {e}") from None
    data: dict[str, FloatArray] = {}
    gaps: dict[str, int] = {}
    for c, k in cols.items():
        fld = COLUMNS[c]
        a = np.array([float(r[k]) if k < len(r) and r[k] else np.nan for r in rows])
        if np.isnan(a).all():
            continue
        a, n = _fill_radiation(a) if fld in RADIATION else _interp(a)
        if label == "centre":
            a = 0.5 * (a + np.append(a[1:], a[-1]))
        data[fld] = a
        if n:
            gaps[fld] = n
    shift = timedelta(hours=-utc_offset)
    times = [t + shift for t in local]
    desc = {
        "metadata": meta,
        "variables": {c: variables.get(c, {}) for c in cols},
        "columns": {COLUMNS[c]: c for c in cols},
        "gaps_filled": gaps,
        "hour_label": "file stamp = centre of the averaging hour; hour-start "
        "value = mean of two stamps"
        if label == "centre"
        else "file stamp = hour start",
        "time_basis": f"UTC{utc_offset:+g} (fixed)",
    }
    return times, data, desc


# ---------------------------------------------------------------------------
# Aggregates (ExploracionCompleta)
# ---------------------------------------------------------------------------


def aggregate_payload(
    cache: Fetcher, lat: float, lon: float, base: str = SOLAR_URL
) -> Any:
    """``ExploracionCompleta`` reply at a point (cached, no declaration)."""
    q = {
        "tipo": "ExploracionCompleta",
        "datos": {"lat": round(lat, 5), "lon": round(lon, 5)},
    }
    key = f"explorador_solar/{_point_key(lat, lon)}_ExploracionCompleta.json"
    reply = _router(cache, key, router_url(q, base))
    if not isinstance(reply, dict) or reply.get("_serverStatus") != "ok":
        text = reply.get("_serverText") if isinstance(reply, dict) else reply
        raise SolarError(f"Explorador Solar aggregates failed: {text!r}")
    return reply["_serverData"]


def parse_month_hour(server_data: Any) -> dict[str, FloatArray]:
    """``HM`` (hour-major, index ``hour * 12 + month``) -> {name: (12, 24)}
    plus the derived ``DHI`` (daily diffuse fraction of ``SM``)."""
    names = list(server_data["VarsHM"])
    out: dict[str, FloatArray] = {}
    for name, vals in zip(names, server_data["HM"]):
        a = np.array([np.nan if v is None else v for v in vals], dtype=float)
        if a.size != 288:
            raise SolarError(f"Explorador Solar HM {name}: {a.size} values, not 288")
        out[name] = a.reshape(24, 12).T.copy()
    sm = dict(zip(server_data.get("VarsSM", []), server_data.get("SM", [])))
    if "GHI" in out and "DIFH" in sm and "DIRH" in sm:
        dif = np.asarray(sm["DIFH"], dtype=float)
        tot = dif + np.asarray(sm["DIRH"], dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            kd = np.where(tot > 0, dif / tot, 0.0)
        out["DHI"] = out["GHI"] * np.clip(kd, 0.0, 1.0)[:, None]
    return out


def expand_month_hour(
    tab: FloatArray, times: Sequence[datetime], utc_offset: float = -4.0
) -> FloatArray:
    """A (12, 24) month × hour table (label h = the hour centred on h:00
    local, fixed ``utc_offset``) on UTC hour-start ``times``: each hour
    takes the value at its centre, linearly between the two nearest labels
    of the same month (cyclic over the day)."""
    out = np.empty(len(times))
    for k, t in enumerate(times):
        c = t + timedelta(hours=0.5 + utc_offset)
        x = c.hour + c.minute / 60.0
        h0 = int(np.floor(x)) % 24
        w = x - np.floor(x)
        row = tab[c.month - 1]
        out[k] = (1 - w) * row[h0] + w * row[(h0 + 1) % 24]
    return out


def log_wind(v: FloatArray, z: float, z_ref: float, z0: float = 0.03) -> FloatArray:
    """Neutral log law from one level: ``v · ln(z/z0) / ln(z_ref/z0)``."""
    if z <= z0 or z_ref <= z0:
        raise SolarError(f"log law needs heights above z0={z0} m")
    return np.maximum(v * np.log(z / z0) / np.log(z_ref / z0), 0.0)
