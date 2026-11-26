# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Equipment libraries: wind turbine power curves and PV module/inverter data.

Wind
    ``oedb:<type>`` loads a manufacturer power curve from the open
    turbine library of the Open Energy Database (OEDB) as shipped with
    ``windpowerlib`` (MIT; Haas et al. 2019, JOSS 4(36):1508,
    DOI 10.21105/joss.01508; data sources per turbine in the library,
    e.g. wind-turbine-models.com).  ``windpowerlib`` is an optional
    dependency: without it, the library type falls back to a generic
    curve of the same specific power (:data:`OEDB_FALLBACK`), with a
    warning.  The curves are never copied into this repository.

    ``auto`` (the generic default when the CEN does not report the
    turbine) picks a representative public curve by commissioning year
    and site wind (IEC class matched to the mean hub-height speed):

    ========================  =================  =================
    commissioning year        mean hub v ≥ 8 m/s  mean hub v < 8 m/s
    ========================  =================  =================
    ≥ 2016 (modern, default)  V112/3450 (I/II)    E-141/4200 (III)
    < 2016                    V112/3075 (II)      V100/1800 (III)
    ========================  =================  =================

    Modern 2020s onshore turbines are 4-6 MW with 140-170 m rotors; the
    largest of that kind in the OEDB library is the E-141/4200 (4.2 MW,
    141 m, 269 W/m²).  Generic hub height: 120 m (modern), 95 m (older).

PV
    Module temperature coefficient from the CEC module database shipped
    with pvlib (``retrieve_sam('CECMod')``, matched by normalised model
    name), inverter AC rating from the CEC inverter database
    (``CECInverter``).  Generic module: modern mono-PERC/TOPCon,
    η ≈ 21 %, γ = −0.35 %/K.

    Generic DC/AC ratio (when the CEN reports neither the ratio nor the
    modules) by commissioning year, :data:`GENERIC_DC_AC`, overridable
    with ``--dc-ac-table`` or a fixed ``--dc-ac-ratio``:

    ==================  ======  ==========================================
    commissioning year  DC/AC   rationale
    ==================  ======  ==========================================
    ≤ 2015              1.05    early fixed-tilt plants, modules expensive
    2016–2019           1.20    single-axis trackers, cheaper modules
    ≥ 2020 / unknown    1.30    current practice (LBNL Utility-Scale Solar
                                2023: median ILR ≈ 1.3 in the 2020s)
    ==================  ======  ==========================================
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from re2gtopt._wind import PowerCurve, generic_curve, specific_power

logger = logging.getLogger("re2gtopt")

#: rated MW and rotor diameter of the library types used by ``auto`` and
#: common in Chile (public manufacturer data sheets), for the fallback
#: curve when windpowerlib is not installed.
OEDB_FALLBACK: dict[str, tuple[float, float]] = {
    "E-141/4200": (4.2, 141.0),
    "V112/3450": (3.45, 112.0),
    "V112/3300": (3.3, 112.0),
    "V112/3075": (3.075, 112.0),
    "V117/3450": (3.45, 117.0),
    "V126/3450": (3.45, 126.0),
    "V100/1800": (1.8, 100.0),
    "V90/2000": (2.0, 90.0),
    "N131/3600": (3.6, 131.0),
    "SWT142/3150": (3.15, 142.0),
}
AUTO_MODERN_YEAR = 2016
AUTO_HIGH_WIND = 8.0  # m/s mean at hub height: IEC I/II above, III below
GENERIC_HUB_MODERN = 120.0
GENERIC_HUB_OLDER = 95.0
GENERIC_GAMMA_PDC = -0.0035


def auto_turbine(commissioning_year: int | None, mean_hub_speed: float) -> str:
    """The representative OEDB type for a site (see the module table)."""
    modern = commissioning_year is None or commissioning_year >= AUTO_MODERN_YEAR
    high = mean_hub_speed >= AUTO_HIGH_WIND
    if modern:
        return "oedb:V112/3450" if high else "oedb:E-141/4200"
    return "oedb:V112/3075" if high else "oedb:V100/1800"


# (last commissioning year, DC/AC): first row with year <= last wins;
# years after the last row and unknown years take the last ratio
GENERIC_DC_AC: tuple[tuple[int, float], ...] = (
    (2015, 1.05),
    (2019, 1.20),
    (9999, 1.30),
)


def generic_dc_ac(
    commissioning_year: int | None,
    table: tuple[tuple[int, float], ...] = GENERIC_DC_AC,
) -> float:
    """Generic DC/AC ratio by commissioning year (:data:`GENERIC_DC_AC`)."""
    if not table:
        raise ValueError("empty DC/AC table")
    if commissioning_year is not None:
        for last, ratio in table:
            if commissioning_year <= last:
                return ratio
    return table[-1][1]


def parse_dc_ac_table(text: str) -> tuple[tuple[int, float], ...]:
    """``"2015:1.05,2019:1.2,9999:1.3"`` -> :data:`GENERIC_DC_AC` form."""
    rows = []
    for item in text.split(","):
        y, sep, r = item.strip().partition(":")
        if not sep:
            raise ValueError(f"DC/AC table item {item!r}: use YEAR:RATIO")
        ratio = float(r)
        if not 0.8 <= ratio <= 2.0:
            raise ValueError(f"DC/AC table item {item!r}: ratio outside 0.8-2.0")
        rows.append((int(y), ratio))
    rows.sort()
    if not rows:
        raise ValueError("empty DC/AC table")
    return tuple(rows)


def generic_hub_height(commissioning_year: int | None) -> float:
    """Generic hub height [m] by commissioning year."""
    if commissioning_year is None or commissioning_year >= AUTO_MODERN_YEAR:
        return GENERIC_HUB_MODERN
    return GENERIC_HUB_OLDER


@lru_cache(maxsize=1)
def _oedb_tables() -> tuple[Any, Any] | None:
    try:
        import windpowerlib  # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    import pandas as pd  # pylint: disable=import-outside-toplevel

    d = Path(windpowerlib.__file__).parent / "oedb"
    return (
        pd.read_csv(d / "power_curves.csv"),
        pd.read_csv(d / "turbine_data.csv"),
    )


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def oedb_types() -> list[str]:
    """Turbine types with a power curve in the OEDB library ([] without
    windpowerlib)."""
    tabs = _oedb_tables()
    if tabs is None:
        return []
    return [str(t) for t in tabs[0]["turbine_type"]]


_ROTOR_RX = (
    r"\bV-?(\d{2,3})\b",
    r"AW-?\s?\d+/(\d{2,3})",
    r"SWT-?[\d.]+-(\d{2,3})",
    r"\bSG\s?[\d.]+-(\d{2,3})",
    r"\bE-?(\d{2,3})\b",
    r"\bN(\d{2,3})\b",
    r"\bGE\s?[\d.]+-(\d{2,3})",
    r"\bG(\d{2,3})\b",
)


def rotor_from_model(model: str) -> float | None:
    """Rotor diameter [m] read from a turbine model name (V90, AW-3000/116,
    SWT-2.3-101, SG 5.0-145, E-82, N131, GE 2.5-120)."""
    for rx in _ROTOR_RX:
        m = re.search(rx, model, re.IGNORECASE)
        if m:
            d = float(m.group(1))
            if 40 <= d <= 250:
                return d
    return None


def _model_kw(model: str) -> int | None:
    """Rated power [kW] read from a model name (``2.0 MW``, ``AW-1500/82``,
    ``SWT-2.3-101``, ``V112/3450``)."""
    low = model.lower()
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*mw", low)
    if m:
        return round(float(m.group(1).replace(",", ".")) * 1000)
    m = re.search(r"(\d{3,4})\s*/\s*\d{2,3}\b", low)  # AW-1500/82
    if m:
        return int(m.group(1))
    m = re.search(r"(?<!\d)\d{2,3}\s*/\s*(\d{3,4})\b", low)  # V112/3450
    if m:
        return int(m.group(1))
    m = re.search(r"-(\d(?:\.\d+)?)-\d{2,3}\b", low)  # SWT-2.3-101
    if m:
        return round(float(m.group(1)) * 1000)
    return None


def match_oedb(model: str, rating_mw: float | None = None) -> str | None:
    """OEDB type for a free-text model (e.g. ``Vestas V112-3.45 MW``,
    ``V112/3450``): exact normalised match, else the same rotor diameter
    and rated power (from the name or ``rating_mw``, +-60 kW); the base
    type is preferred over its variants (``V90/2000`` over
    ``V90/2000/GS``)."""
    types = oedb_types() or list(OEDB_FALLBACK)
    n = _norm(model)
    for t in types:
        if _norm(t) == n:
            return t
    rotor = rotor_from_model(model)
    if rotor is None:
        return None
    want_kw = _model_kw(model)
    if want_kw is None and rating_mw:
        want_kw = round(rating_mw * 1000)
    cands: list[tuple[int, str]] = []
    for t in types:
        m = re.match(r"\D*(\d{2,3})/(\d{3,4})", t.replace("-", ""))
        if m and int(m.group(1)) == int(rotor):
            kw = int(m.group(2))
            if want_kw is None or abs(kw - want_kw) <= 60:
                cands.append((kw, t))
    if not cands or len({kw for kw, _ in cands}) > 1:
        return None  # no match, or ambiguous rated power
    return sorted((t for _, t in cands), key=lambda t: (t.count("/"), len(t)))[0]


def oedb_curve(turbine_type: str, cut_out: float | None = None) -> PowerCurve:
    """Power curve of an OEDB type (per-unit), or the generic curve of the
    same specific power without windpowerlib."""
    tabs = _oedb_tables()
    if tabs is not None:
        pc = tabs[0]
        row = pc[pc["turbine_type"] == turbine_type]
        if len(row):
            r = row.iloc[0].drop("turbine_type").dropna()
            v = np.array([float(x) for x in r.index])
            p = r.to_numpy(dtype=float)
            order = np.argsort(v)
            v, p = v[order], p[order] / p.max()
            last = float(v[np.flatnonzero(p > 0)[-1]])
            return PowerCurve(v, np.clip(p, 0, 1), cut_out or last + 0.5, turbine_type)
        raise ValueError(f"turbine {turbine_type!r} is not in the OEDB library")
    if turbine_type not in OEDB_FALLBACK:
        raise ValueError(
            f"turbine {turbine_type!r}: install windpowerlib for the OEDB library"
        )
    mw, d = OEDB_FALLBACK[turbine_type]
    logger.warning(
        "windpowerlib not installed: %s -> generic curve of %.0f W/m2",
        turbine_type,
        specific_power(mw, d),
    )
    return generic_curve(
        specific_power(mw, d), cut_out=cut_out or 25.0, name=turbine_type
    )


# ---------------------------------------------------------------------------
# PV
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleMatch:
    """A module matched in the CEC database."""

    name: str
    gamma_pdc: float  # 1/K
    efficiency: float | None


@lru_cache(maxsize=2)
def _sam(kind: str) -> Any:
    try:
        import pvlib  # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    return pvlib.pvsystem.retrieve_sam(kind)


def match_module(model: str) -> ModuleMatch | None:
    """CEC module whose normalised name contains the normalised ``model``
    (shortest such name wins); None without pvlib or without a match."""
    db = _sam("CECMod")
    if db is None or not model.strip():
        return None
    n = _norm(model)
    if len(n) < 5:
        return None
    hits = [c for c in db.columns if n in _norm(c)]
    if not hits:
        return None
    best = min(hits, key=len)
    col = db[best]
    gamma = float(col["gamma_r"]) / 100.0
    eff = None
    try:
        eff = float(col["STC"]) / (float(col["A_c"]) * 1000.0)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        eff = None
    return ModuleMatch(best, gamma, eff)


def match_inverter(model: str) -> tuple[str, float] | None:
    """CEC inverter (name, Paco [W]) for a model text; None if no match."""
    db = _sam("CECInverter")
    if db is None or not model.strip():
        return None
    n = _norm(model)
    if len(n) < 4:
        return None
    hits = [c for c in db.columns if n in _norm(c)]
    if not hits:
        return None
    best = min(hits, key=len)
    return best, float(db[best]["Paco"])
