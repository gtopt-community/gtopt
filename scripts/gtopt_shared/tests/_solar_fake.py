# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Synthetic Explorador Solar replies (request shapes from the site's JS;
values are made up, no downloaded data)."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
from datetime import datetime, timedelta

from gtopt_shared import explorador_solar as es

CSV_YEARS = (2004, 2005)
RULE = "-" * 89
HEADER = "Fecha/Hora,glb,dir,dif,sct,ghi,dirh,difh,dni,temp,vel,shadow,cloud"
META = (
    "DATOS HORARIOS GENERADOS CON EL EXPLORADOR DE ENERGIA SOLAR",
    '""',
    RULE,
    "DESCRIPCION,Synthetic test file",
    "VERSION DEL MODELO,2.0",
    "FUENTE,Synthetic source",
    RULE,
    '""',
    "CARACTERISTICAS DEL SITIO",
    "NOMBRE DEL SITIO,S",
    "LATITUD,-31.6",
    "LONGITUD,-71.4",
    "ALTURA,154.0",
    '""',
    RULE,
    "PERIODO DE DATOS",
    "FECHA INICIAL,{first}",
    "FECHA FINAL,{last}",
    "INTERVALO,60 minutos",
    "METODO DE CALCULO,Promedio de valores instantaneos cada 10 minutos",
    '""',
    RULE,
    "VARIABLES INCLUIDAS",
    '""',
    "VARIABLE,UNIDAD,DESCRIPCION",
    "glb,W/m2,Radiacion Global",
    "dif,W/m2,Radiacion Difusa",
    "ghi,W/m2,Radiacion Global Horizontal",
    "difh,W/m2,Radiacion Difusa",
    "dni,W/m2,Radiacion Directa Normal",
    "temp,C,Temperatura en 2 metros (Corejida)",
    "vel,m/s,Velocidad de Viento",
    'cloud,"1=Si, 0=No",Presencia de nubes',
    '""',
    RULE,
    '""',
)


def _sun(hour: int) -> float:
    return max(0.0, 1000.0 * math.sin(math.pi * (hour - 6) / 12))


def hourly_csv_text(
    years: tuple[int, ...] = CSV_YEARS, header: str = HEADER, nan_tail: int = 0
) -> str:
    """The real layout with made-up values: local-time (UTC-4) stamps,
    the file's last day missing (it ends on 12-30 23:00), one temperature
    gap and ``nan_tail`` trailing NaN radiation hours."""
    times = []
    for y in years:
        t, end = datetime(y, 1, 1), datetime(y + 1, 1, 1)
        if y == years[-1]:
            end -= timedelta(days=1)
        while t < end:
            times.append(t)
            t += timedelta(hours=1)
    body = []
    for k, t in enumerate(times):
        g = _sun(t.hour)
        rad = (
            ["NaN"] * 9
            if k >= len(times) - nan_tail
            else [f"{v:.2f}" for v in (g, 0, 0.2 * g, 0, g, 0.8 * g, 0.2 * g, g, 0)]
        )
        temp = "NaN" if (t.month, t.day, t.hour) == (1, 2, 3) else f"{15 + t.hour % 6}"
        body.append(
            f"{t:%Y-%m-%d %H:%M:%S},{','.join(rad[:8])},{temp},3.00,0.00,{rad[8]}"
        )
    meta = "\n".join(META).format(
        first=f"{times[0]:%Y-%m-%d %H:%M:%S}", last=f"{times[-1]:%Y-%m-%d %H:%M:%S}"
    )
    return meta + "\n" + header + "\n" + "\n".join(body) + "\n"


def server_data() -> dict[str, object]:
    """``ExploracionCompleta`` shape: HM hour-major (hour * 12 + month)."""
    hm = {
        "GHI": lambda m, h: _sun(h) * (1.0 - 0.04 * m),
        "GLB": lambda m, h: _sun(h),
        "DNI": lambda m, h: 0.8 * _sun(h),
        "Temp": lambda m, h: 100.0 * m + h,  # encodes (month, hour)
        "Vel": lambda m, h: 3.0,
        "Nube": lambda m, h: 10.0,
        "Sombra": lambda m, h: 0.0,
        "DIRP": lambda m, h: _sun(h),
    }
    names = list(hm)
    return {
        "VarsHM": names,
        "HM": [[hm[n](m, h) for h in range(24) for m in range(12)] for n in names],
        "VarsSM": ["DIRH", "DIFH"],
        "SM": [[6.0] * 12, [2.0] * 12],
    }


def solar_get(url: str) -> bytes:
    """Router + /temp replies; the hourly download enforces the profile
    as the real server does (HTTP 400 without a valid one)."""
    if "/temp/" in url:
        return hourly_csv_text().encode()
    q = json.loads(urllib.parse.unquote(url.split("/python-router/", 1)[1]))
    if q["tipo"] == "ExploracionCompleta":
        return json.dumps(
            {"_serverStatus": "ok", "_serverData": server_data()}
        ).encode()
    if q["tipo"] == "DHC_E":
        perfil = (q.get("registro") or {}).get("perfil")
        if perfil not in es.PROFILES or not q["registro"].get("sesionId"):
            raise urllib.error.HTTPError(url, 400, "Bad Request", None, None)  # type: ignore[arg-type]
        return json.dumps({"response": "dhc_e_synthetic.csv"}).encode()
    raise AssertionError(url)
