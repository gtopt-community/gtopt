# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CEN data: plant inventory from Infotécnica, hourly real generation.

Nothing downloaded here is stored in the repository: every request goes
through a cache directory (default ``~/.cache/gtopt/re2gtopt``), with a
pause between requests.  Link to the sources; do not redistribute.

Infotécnica (Coordinador Eléctrico Nacional, public REST API, no login),
``https://api-infotecnica.coordinador.cl/v1``:

* ``/centrales/extended/`` -- every plant: ``id, nombre,
  tipo_central_nombre (Eólicas|Solares|...), region_nombre,
  comuna_nombre, latitud, longitud, potencia_maxima,
  fecha_inicio_operacion, ...``;
* ``/centrales/{id}/fichas-tecnicas/general/`` -- gross / net maximum
  power, connection point, commissioning date;
* ``/unidades-generadoras/?id_central={id}`` and
  ``/unidades-generadoras/{uid}/fichas-tecnicas/{turbinas-eolicas|
  generacion-solar}/`` -- turbine make, model, rating, count, tower
  height; PV module model, Wp, count, inverter count, tracking, tilt,
  azimuth.

A ficha is a dict of rows ``nombre_concepto, valor_texto,
valor_numerico, estado_certificacion_nombre``.  Rows certified
``Rechazado`` are skipped by default; values use decimal commas.

Hourly real generation per plant (gross MWh): the yearly workbooks
``CEN-hist_gen_de_energia_por_central_*.xlsx`` linked from
<https://www.coordinador.cl/reportes-y-estadisticas/>; one row per plant
and day, columns ``HORA 1..HORA 25`` (or ``1..25``) in local civil time
(America/Santiago, with daylight saving: a 25-hour day when it ends).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dlr2gtopt._towers import PoliteCache
from re2gtopt import _equipment as eq

logger = logging.getLogger("re2gtopt")

INFOTECNICA_API = "https://api-infotecnica.coordinador.cl/v1"
CEN_GEN_FILES: dict[str, str] = {
    "2016-2019": "https://www.coordinador.cl/wp-content/uploads/2022/09/"
    "CEN-hist_gen_de_energia_por_central_16_19.xlsx",
    "2020-2023": "https://www.coordinador.cl/wp-content/uploads/2026/10/"
    "CEN-hist_gen_de_energia_por_central_20_23_v02.xlsx",
    "2024": "https://www.coordinador.cl/wp-content/uploads/2025/04/"
    "CEN-hist_gen_de_energia_por_central_2024.xlsx",
    "2025": "https://www.coordinador.cl/wp-content/uploads/2026/05/"
    "CEN-hist_gen_de_energia_por_central_2025.xlsx",
}
CHILE_TZ = "America/Santiago"
_TYPES = {"Eólicas": "wind", "Solares": "pv"}
_MONTHS_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}  # fmt: skip


def norm(text: str) -> str:
    """Lower-case ASCII letters and digits only."""
    t = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", t.lower())


_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}


def plant_key(name: str) -> str:
    """Normalised plant name without technology prefixes (PE, PFV, ...)
    and with roman unit numbers as digits (``PE Canela II`` ==
    ``PE-CANELA-2``)."""
    t = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    words = re.split(r"[^a-z0-9]+", t.lower())
    drop = {
        "parque", "eolico", "eolica", "pe", "pfv", "fv", "solar", "central",
        "planta", "fotovoltaica", "fotovoltaico", "psf", "pv", "de", "del",
    }  # fmt: skip
    keep = [w for w in words if w and w not in drop]
    keep = [_ROMAN.get(w, w) if k else w for k, w in enumerate(keep)]
    return "".join(keep)


def parse_number(text: Any) -> float | None:
    """``"59,4"`` / ``"1.234,5"`` / ``59.4`` -> float (None if not a number)."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    t = str(text).strip()
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    m = re.search(r"-?\d+(\.\d+)?", t)
    return float(m.group(0)) if m else None


def ficha_values(ficha: Any, skip_rejected: bool = True) -> dict[str, Any]:
    """``nombre_concepto`` -> value (numeric when given, else the text)."""
    rows = ficha.values() if isinstance(ficha, dict) else ficha or []
    out: dict[str, Any] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = r.get("nombre_concepto")
        if not name:
            continue
        if skip_rejected and r.get("estado_certificacion_nombre") == "Rechazado":
            continue
        txt = r.get("valor_texto")
        num = r.get("valor_numerico")
        val = txt if txt not in (None, "") else num
        if val in (None, ""):
            continue
        out[str(name)] = val
    return out


def find(values: dict[str, Any], *patterns: str) -> Any:
    """First value whose concept name matches any regex (case-insensitive)."""
    for pat in patterns:
        rx = re.compile(pat, re.IGNORECASE)
        for k, v in values.items():
            if rx.search(k):
                return v
    return None


rotor_from_model = eq.rotor_from_model


def tracking_from_text(text: Any) -> str | None:
    """``single-axis`` / ``fixed`` from a free-text tracking field."""
    if text in (None, ""):
        return None
    t = norm(str(text))
    if any(k in t for k in ("fijo", "fixed", "sinseguimiento", "noaplica")):
        return "fixed"
    if any(k in t for k in ("eje", "axis", "tracker", "seguidor", "seguimiento")):
        return "single-axis"
    return None


class Infotecnica:
    """Polite, cached client of the Infotécnica public API."""

    def __init__(self, cache: PoliteCache, base: str = INFOTECNICA_API) -> None:
        self.cache = cache
        self.base = base.rstrip("/")

    def get(self, path: str) -> Any:
        """JSON of an API path (cached)."""
        key = "infotecnica/" + norm(path) + ".json"
        return json.loads(self.cache.fetch(key, self.base + path))

    def plants(self) -> list[dict[str, Any]]:
        """Every plant with coordinates (``/centrales/extended/``)."""
        data = self.get("/centrales/extended/")
        return data if isinstance(data, list) else data.get("results", [])

    def units(self, central_id: int) -> list[dict[str, Any]]:
        """Generating units of a plant."""
        data = self.get(f"/unidades-generadoras/?id_central={central_id}")
        return data if isinstance(data, list) else data.get("results", [])

    def ficha(self, kind: str, ident: int, slug: str) -> Any:
        """A technical sheet (``kind`` = centrales | unidades-generadoras)."""
        return self.get(f"/{kind}/{ident}/fichas-tecnicas/{slug}/")


@dataclass
class InventoryOptions:
    """What to pull from Infotécnica."""

    types: tuple[str, ...] = ("wind", "pv")
    regions: tuple[str, ...] = ()
    bbox: tuple[float, float, float, float] | None = None  # lat0 lat1 lon0 lon1
    names: tuple[str, ...] = ()
    min_mw: float = 0.0
    equipment: bool = True
    skip_rejected: bool = True


def _select(p: dict[str, Any], opt: InventoryOptions) -> bool:
    typ = _TYPES.get(str(p.get("tipo_central_nombre")))
    if typ not in opt.types:
        return False
    if p.get("latitud") is None or p.get("longitud") is None:
        return False
    if float(p.get("potencia_maxima") or 0.0) < opt.min_mw:
        return False
    if opt.regions and norm(p.get("region_nombre") or "") not in {
        norm(r) for r in opt.regions
    }:
        return False
    if opt.bbox is not None:
        la0, la1, lo0, lo1 = opt.bbox
        if not (
            la0 <= float(p["latitud"]) <= la1 and lo0 <= float(p["longitud"]) <= lo1
        ):
            return False
    if opt.names and plant_key(p.get("nombre", "")) not in {
        plant_key(n) for n in opt.names
    }:
        return False
    return True


def _year(text: Any) -> int | None:
    m = re.search(r"(19|20)\d{2}", str(text or ""))
    return int(m.group(0)) if m else None


def wind_equipment(sheets: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Turbine data summed over a plant's units."""
    out: dict[str, Any] = {}
    n_tot = 0
    for v in sheets:
        model = find(v, r"^Modelo Turbina", r"Modelo.*turbina")
        brand = find(v, r"Marca de la turbina", r"Fabricante")
        mw = parse_number(find(v, r"Potencia nominal"))
        n = parse_number(find(v, r"N[úu]mero de turbinas"))
        hub = parse_number(find(v, r"Altura de la torre", r"Altura.*buje"))
        if model and "turbine" not in out:
            out["turbine"] = f"{brand} {model}".strip() if brand else str(model)
        elif brand and "turbine" not in out:
            out["turbine"] = str(brand)
        if mw and "turbine_mw" not in out:
            out["turbine_mw"] = mw if mw < 20 else mw / 1000.0  # kW typed as MW
        if hub and "hub_height" not in out and 30 <= hub <= 200:
            out["hub_height"] = hub
        if n:
            n_tot += int(n)
    if n_tot:
        out["n_turbines"] = n_tot
    if "turbine" in out:
        d = rotor_from_model(out["turbine"])
        if d:
            out["rotor_diameter"] = d
    return out


def pv_equipment(sheets: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """PV data summed over a plant's units."""
    out: dict[str, Any] = {}
    n_mod = 0
    n_inv = 0
    for v in sheets:
        n = parse_number(find(v, r"N[úu]mero de paneles"))
        wp = parse_number(find(v, r"Potencia nominal del panel"))
        ni = parse_number(find(v, r"N[úu]mero de Inversores"))
        maker = find(v, r"Fabricante de la celda", r"Fabricante.*panel")
        model = find(v, r"12\.5\.5", r"Modelo del panel", r"Modelo.*m[óo]dulo")
        trk = find(v, r"Sistema de Seguimiento", r"Caracter[íi]sticas de Seguimiento")
        tilt = parse_number(find(v, r"13\.4\.3", r"[Ii]nclinaci[óo]n"))
        az = parse_number(find(v, r"13\.4\.4", r"[Aa]zimut"))
        if n:
            n_mod += int(n)
        if ni:
            n_inv += int(ni)
        if wp and "module_wp" not in out and 100 <= wp <= 1000:
            out["module_wp"] = wp
        if model and "module" not in out:
            out["module"] = f"{maker} {model}".strip() if maker else str(model)
        t = tracking_from_text(trk)
        if t and "tracking" not in out:
            out["tracking"] = t
        if tilt is not None and "tilt" not in out and out.get("tracking") == "fixed":
            out["tilt"] = tilt
        if az is not None and "azimuth" not in out and out.get("tracking") == "fixed":
            out["azimuth"] = az
    if n_mod:
        out["n_modules"] = n_mod
    if n_inv:
        out["n_inverters"] = n_inv
    return out


def build_inventory(
    client: Infotecnica,
    opt: InventoryOptions,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Inventory rows (see ``_plants``) for the selected plants."""
    sel = [p for p in client.plants() if _select(p, opt)]
    rows: list[dict[str, Any]] = []
    for k, p in enumerate(sel, start=1):
        typ = _TYPES[str(p["tipo_central_nombre"])]
        row: dict[str, Any] = {
            "uid": k,
            "name": str(p["nombre"]).strip(),
            "type": typ,
            "lat": round(float(p["latitud"]), 5),
            "lon": round(float(p["longitud"]), 5),
            "pmax_mw": float(p.get("potencia_maxima") or 0.0),
            "pmax_basis": "gross",
            "commissioning": _year(p.get("fecha_inicio_operacion")),
            "region": p.get("region_nombre"),
            "comuna": p.get("comuna_nombre"),
            "cen_id": p.get("id"),
            "source": "cen",
        }
        if opt.equipment:
            gen = ficha_values(
                client.ficha("centrales", int(p["id"]), "general"), opt.skip_rejected
            )
            gross = parse_number(find(gen, r"Potencia m[áa]xima bruta"))
            net = parse_number(find(gen, r"potencia neta efectiva"))
            if gross:
                row["pmax_mw"] = gross
            if net:
                row["pmax_net_mw"] = net
            conn = find(gen, r"Puntos? de conexi[óo]n")
            if conn:
                row["substation"] = str(conn)
            slug = "turbinas-eolicas" if typ == "wind" else "generacion-solar"
            sheets = [
                ficha_values(
                    client.ficha("unidades-generadoras", int(u["id"]), slug),
                    opt.skip_rejected,
                )
                for u in client.units(int(p["id"]))
            ]
            row.update(
                wind_equipment(sheets) if typ == "wind" else pv_equipment(sheets)
            )
            if typ == "wind" and not row.get("turbine"):
                row["source"] = "generic"
            if typ == "pv" and not (row.get("module") or row.get("n_modules")):
                row["source"] = "generic"
        if row["pmax_mw"] > 0:
            rows.append(row)
        if progress is not None:
            progress(k, len(sel))
    return rows


def coverage(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Share of wind / PV MW with equipment data, and per-field counts."""
    out: dict[str, Any] = {}
    for typ, fields in (
        ("wind", ("turbine", "n_turbines", "hub_height", "rotor_diameter")),
        ("pv", ("module", "module_wp", "n_modules", "n_inverters", "tracking")),
    ):
        sub = [r for r in rows if r["type"] == typ]
        mw = sum(r["pmax_mw"] for r in sub)
        out[typ] = {
            "plants": len(sub),
            "mw": round(mw, 1),
            "mw_with_model": round(
                sum(r["pmax_mw"] for r in sub if r.get("source") == "cen"), 1
            ),
            "fields": {f: sum(1 for r in sub if r.get(f)) for f in fields},
        }
        out[typ]["share_mw_with_model"] = (
            round(out[typ]["mw_with_model"] / mw, 3) if mw else None
        )
    return out


# ---------------------------------------------------------------------------
# hourly real generation
# ---------------------------------------------------------------------------


def _cell_date(val: Any, year: int | None) -> date | None:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, (int, float)) and 20000 < float(val) < 80000:
        return date(1899, 12, 30) + timedelta(days=int(val))
    if isinstance(val, str):
        m = re.match(r"\s*(\d{1,2})[-/ ]([A-Za-z]{3})", val)
        if m and year is not None:
            mon = _MONTHS_ES.get(m.group(2).lower())
            if mon:
                return date(year, mon, int(m.group(1)))
        m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", val)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.match(r"\s*(\d{1,2})[-/](\d{1,2})[-/](\d{4})", val)
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def local_hour_to_utc(day: date, hour: int, tz: str = CHILE_TZ) -> datetime:
    """UTC start (naive) of the ``hour``-th (1-based) hour of a local civil
    day; counts real elapsed hours, so a 25-hour day (end of daylight
    saving) and a 23-hour day map without gaps or overlaps."""
    z = ZoneInfo(tz)
    start = datetime(day.year, day.month, day.day, tzinfo=z).astimezone(timezone.utc)
    return (start + timedelta(hours=hour - 1)).replace(tzinfo=None)


def _hour_columns(header: Sequence[Any]) -> dict[int, int]:
    out = {}
    for i, h in enumerate(header):
        t = str(h).strip().upper().replace("HORA", "").strip() if h is not None else ""
        if t.isdigit() and 1 <= int(t) <= 25:
            out[int(t)] = i
    return out


def parse_generation_rows(
    rows: Iterable[Sequence[Any]],
    wanted: dict[str, str],
    year: int | None = None,
    tz: str = CHILE_TZ,
    types: dict[str, str] | None = None,
) -> list[tuple[datetime, str, float]]:
    """(UTC hour start, inventory plant name, MWh) from workbook rows.

    ``wanted`` maps :func:`plant_key` of the CEN plant / key name to the
    inventory name.  The header row is the first one with hour columns.
    With ``types`` (plant name -> ``wind``/``pv``) and a ``TIPO`` column,
    a row must also have the plant's technology (``HP SAN ANDRES`` is
    not ``PFV SAN ANDRES``)."""
    out: list[tuple[datetime, str, float]] = []
    hours: dict[int, int] = {}
    name_cols: list[int] = []
    date_col = -1
    type_col = -1
    for row in rows:
        if not hours:
            hc = _hour_columns(row)
            if len(hc) >= 24:
                hours = hc
                low = [norm(str(c or "")) for c in row]
                name_cols = [
                    i
                    for i, c in enumerate(low)
                    if "nombre" in c or "llave" in c or c.endswith("name")
                ]
                date_col = next(
                    (i for i, c in enumerate(low) if "fecha" in c), min(hc.values()) - 1
                )
                type_col = next(
                    (i for i, c in enumerate(low) if c in ("tipo", "tipofuente")), -1
                )
            continue
        plant = None
        for i in name_cols:
            plant = wanted.get(plant_key(str(row[i] or "")))
            if plant:
                break
        if plant is None:
            continue
        if types and type_col >= 0 and not _same_tech(row[type_col], types.get(plant)):
            continue
        day = _cell_date(row[date_col], year)
        if day is None:
            continue
        n_day = int(
            (
                local_hour_to_utc(day + timedelta(days=1), 1, tz)
                - local_hour_to_utc(day, 1, tz)
            ).total_seconds()
            // 3600
        )
        for h, i in sorted(hours.items()):
            if h > n_day or i >= len(row):
                continue
            v = parse_number(row[i])
            if v is None:
                continue
            out.append((local_hour_to_utc(day, h, tz), plant, v))
    return out


def _same_tech(cell: Any, ptype: str | None) -> bool:
    t = norm(str(cell or ""))
    if ptype == "wind":
        return "eolic" in t
    if ptype == "pv":
        return "solar" in t or "fotovolt" in t
    return True


def read_generation_xlsx(
    path: Path,
    wanted: dict[str, str],
    year: int | None = None,
    types: dict[str, str] | None = None,
) -> list[tuple[datetime, str, float]]:
    """Every sheet of a CEN generation workbook (streamed, read-only)."""
    import openpyxl  # pylint: disable=import-outside-toplevel

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: list[tuple[datetime, str, float]] = []
    try:
        for ws in wb.worksheets:
            y = year or _year(ws.title) or _year(path.name)
            out += parse_generation_rows(
                ws.iter_rows(values_only=True), wanted, y, types=types
            )
    finally:
        wb.close()
    return out


# ---------------------------------------------------------------------------
# curtailment ("Reducciones de generación renovable") and forecasts
# ---------------------------------------------------------------------------

CURTAILMENT_INDEX = (
    "https://www.coordinador.cl/operacion/documentos/"
    "reducciones-de-generacion-renovable/"
)
FORECAST_INDEX = (
    "https://www.coordinador.cl/mercados/documentos/"
    "pronostico-centralizado-de-generacion-y-demanda-2/"
    "pronostico-centralizado-de-generacion/"
)


def parse_curtailment_rows(
    rows: Iterable[Sequence[Any]],
    wanted: dict[str, str],
    tz: str = CHILE_TZ,
) -> list[tuple[datetime, str, float]]:
    """(UTC hour start, plant, curtailed MWh) from one daily-hourly sheet of
    the monthly "Reducciones de Energía Eólica y Solar" workbook: per day a
    date cell, a ``Central/Hora | 1..24 | Total`` header, one row per
    plant."""
    out: list[tuple[datetime, str, float]] = []
    day: date | None = None
    hours: dict[int, int] = {}
    name_col = 1
    for row in rows:
        cells = list(row)
        stamp = next((c for c in cells[:4] if isinstance(c, (datetime, date))), None)
        if stamp is not None:
            day = stamp.date() if isinstance(stamp, datetime) else stamp
            hours = {}
            continue
        hc = _hour_columns(cells)
        if len(hc) >= 23:
            hours = hc
            name_col = next(
                (i for i, c in enumerate(cells) if c and "central" in norm(str(c))),
                1,
            )
            continue
        if day is None or not hours or name_col >= len(cells):
            continue
        plant = wanted.get(plant_key(str(cells[name_col] or "")))
        if plant is None:
            continue
        for h, i in sorted(hours.items()):
            v = parse_number(cells[i]) if i < len(cells) else None
            if v is not None:
                out.append((local_hour_to_utc(day, h, tz), plant, v))
    return out


def read_curtailment_xlsx(
    path: Path, wanted: dict[str, str]
) -> list[tuple[datetime, str, float]]:
    """Every ``Resumen-DiarioHorario-{Eólico,Solar}`` sheet of a workbook."""
    import openpyxl  # pylint: disable=import-outside-toplevel

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: list[tuple[datetime, str, float]] = []
    try:
        for ws in wb.worksheets:
            t = norm(ws.title)
            if "diariohorario" in t and ("eolic" in t or "solar" in t):
                out += parse_curtailment_rows(ws.iter_rows(values_only=True), wanted)
    finally:
        wb.close()
    return out


def parse_long_rows(
    rows: Iterable[Sequence[Any]],
    wanted: dict[str, str],
    tz: str = CHILE_TZ,
) -> list[tuple[datetime, str, float]]:
    """Long ``Fecha | Nombre | Valor [| Hora]`` tables of the monthly
    centralized-forecast archive (forecasts, real, curtailment): local
    civil timestamps; with ``Hora`` (1..24) the timestamp is the day."""
    out: list[tuple[datetime, str, float]] = []
    cols: dict[str, int] = {}
    z = ZoneInfo(tz)
    for row in rows:
        cells = list(row)
        if not cols:
            low = [norm(str(c or "")) for c in cells]
            if "fecha" in low and "nombre" in low and "valor" in low:
                cols = {k: low.index(k) for k in ("fecha", "nombre", "valor")}
                if "hora" in low:
                    cols["hora"] = low.index("hora")
            continue
        plant = wanted.get(plant_key(str(cells[cols["nombre"]] or "")))
        val = parse_number(cells[cols["valor"]])
        if plant is None or val is None:
            continue
        stamp = cells[cols["fecha"]]
        if isinstance(stamp, str):
            stamp = datetime.fromisoformat(stamp.strip())
        if not isinstance(stamp, datetime):
            continue
        if "hora" in cols:
            h = int(parse_number(cells[cols["hora"]]) or 1)
            t = local_hour_to_utc(stamp.date(), h, tz)
        else:
            t = stamp.replace(tzinfo=z).astimezone(timezone.utc).replace(tzinfo=None)
        out.append((t, plant, val))
    return out


def scrape_links(html: str, suffixes: tuple[str, ...] = (".xlsx", ".zip")) -> list[str]:
    """Absolute ``wp-content/uploads`` links of a CEN document page."""
    links = re.findall(
        r'href="(https://www\.coordinador\.cl/wp-content/uploads/[^"]+)"', html
    )
    seen: list[str] = []
    for u in links:
        if u.lower().endswith(suffixes) and u not in seen:
            seen.append(u)
    return seen
