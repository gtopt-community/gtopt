# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Plant inventory and equipment sizing that reproduces the reported Pmax.

Inventory (CSV or JSON list), one row per plant:

==================  ==========================================================
column              meaning (default)
==================  ==========================================================
``name``            plant name (required)
``type``            ``wind`` or ``pv`` (required)
``lat``, ``lon``    location, degrees (required)
``pmax_mw``         reported maximum power, MW (required; ``capacity_mw`` is
                    accepted as an alias).  It is gtopt's
                    ``Generator.capacity``: every profile is p.u. of it
``pmax_basis``      ``gross`` (default) or ``net`` (after electrical losses)
``injection_mw``    grid injection limit, MW (optional; the ceiling when
                    below Pmax)
``uid``             GeneratorProfile uid (row number)
``generator``       gtopt generator name or uid (``name``)
``elevation``       m a.s.l. (from the weather source, else 0)
``commissioning``   year (generic equipment choice)
wind                ``turbine`` (``auto``, an OEDB type ``oedb:V112/3450``,
                    a class ``iec1|iec2|iec3|iec3s``, ``sp<W/m2>`` or a CSV),
                    ``turbine_mw``, ``n_turbines``, ``hub_height``,
                    ``rotor_diameter``
pv                  ``tracking``, ``tilt``, ``azimuth``, ``dc_ac_ratio``,
                    ``gcr``, ``module`` (model text), ``module_wp``,
                    ``n_modules``, ``inverter`` (model text),
                    ``inverter_kw``, ``n_inverters``, ``gamma_pdc``
``source``          where the equipment came from (``cen``/``generic``/...)
any option          a per-plant override of a model option (e.g.
                    ``wake_loss``, ``soiling``, ``cap``)
==================  ==========================================================

**Pmax reproduction.**  Wind: the turbine count is
``round(Pmax_gross / rating)`` (``Pmax_net / (1 − electrical_loss)``
when the basis is ``net``); without a reported rating it is chosen in
``GENERIC_TURBINE_MW`` (4-6 MW), preferring a rating that divides Pmax
exactly.  The installed ratio Σ ratings / Pmax enters the model and the
plant output is clipped at Pmax (1.0 p.u.).  PV: AC = Pmax (or the
injection limit, or the inverter total when smaller), DC = AC × DC/AC,
modules = DC / Wp.  A CEN equipment list that disagrees with Pmax beyond
``tolerance`` is reported, and Pmax stays the ceiling.
"""

from __future__ import annotations

import csv
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from re2gtopt import _equipment as eq

logger = logging.getLogger("re2gtopt")

PLANT_TYPES = ("wind", "pv")
GENERIC_TURBINE_MW = (4.0, 6.0)  # typical modern onshore rating range
GENERIC_TURBINE_STEP = 0.05
GENERIC_MODULE_WP = 580.0  # modern bifacial mono-PERC/TOPCon, ~21-22 %
_NUMERIC = {
    "lat",
    "lon",
    "pmax_mw",
    "injection_mw",
    "elevation",
    "turbine_mw",
    "hub_height",
    "rotor_diameter",
    "tilt",
    "azimuth",
    "dc_ac_ratio",
    "gcr",
    "module_wp",
    "inverter_kw",
    "gamma_pdc",
    "wake_loss",
    "availability",
    "electrical_loss",
    "cap",
    "soiling",
    "dc_loss",
    "ac_loss",
    "max_angle",
    "albedo",
    "cut_in",
    "cut_out",
    "restart",
}
_INT = {"uid", "commissioning", "n_turbines", "n_modules", "n_inverters"}


@dataclass
class Plant:
    """One wind or PV plant."""

    name: str
    type: str
    lat: float
    lon: float
    pmax_mw: float
    uid: int
    generator: str
    pmax_basis: str = "gross"
    injection_mw: float | None = None
    elevation: float | None = None
    commissioning: int | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """A plant attribute (None/blank -> default)."""
        v = self.attrs.get(key)
        return default if v is None or v == "" else v

    @property
    def ceiling_mw(self) -> float:
        """The output ceiling: Pmax, or the injection limit when lower."""
        if self.injection_mw is not None and 0 < self.injection_mw < self.pmax_mw:
            return self.injection_mw
        return self.pmax_mw


def _coerce(key: str, val: Any) -> Any:
    if val is None or (isinstance(val, str) and val.strip() == ""):
        return None
    if key in _NUMERIC:
        return float(val)
    if key in _INT:
        return int(float(val))
    return val.strip() if isinstance(val, str) else val


def plant_from_row(row: dict[str, Any], k: int) -> Plant:
    """A plant from an inventory row (``k`` = 1-based row, default uid)."""
    r = {str(a).strip(): _coerce(str(a).strip(), b) for a, b in row.items() if a}
    if r.get("pmax_mw") is None and r.get("capacity_mw") is not None:
        r["pmax_mw"] = float(r["capacity_mw"])
    for need in ("name", "type", "lat", "lon", "pmax_mw"):
        if r.get(need) is None:
            raise ValueError(f"plant row {k}: missing {need}")
    typ = str(r["type"]).lower()
    if typ in ("solar", "fv", "pfv", "photovoltaic"):
        typ = "pv"
    if typ in ("eolica", "eólica"):
        typ = "wind"
    if typ not in PLANT_TYPES:
        raise ValueError(f"plant {r['name']}: type must be one of {PLANT_TYPES}")
    if float(r["pmax_mw"]) <= 0:
        raise ValueError(f"plant {r['name']}: pmax_mw must be > 0")
    basis = str(r.get("pmax_basis") or "gross").lower()
    if basis not in ("gross", "net"):
        raise ValueError(f"plant {r['name']}: pmax_basis must be gross or net")
    base = {
        "name",
        "type",
        "lat",
        "lon",
        "pmax_mw",
        "capacity_mw",
        "uid",
        "generator",
        "pmax_basis",
        "injection_mw",
        "elevation",
        "commissioning",
    }
    return Plant(
        name=str(r["name"]),
        type=typ,
        lat=float(r["lat"]),
        lon=float(r["lon"]),
        pmax_mw=float(r["pmax_mw"]),
        uid=int(r.get("uid") or k),
        generator=str(r.get("generator") or r["name"]),
        pmax_basis=basis,
        injection_mw=r.get("injection_mw"),
        elevation=r.get("elevation"),
        commissioning=r.get("commissioning"),
        attrs={a: b for a, b in r.items() if a not in base},
    )


def read_inventory(path: Path) -> list[Plant]:
    """Plants from a CSV or JSON (list of objects, or ``{"plants": [...]}``)."""
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["plants"] if isinstance(data, dict) else data
    else:
        with path.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    plants = [plant_from_row(dict(r), k) for k, r in enumerate(rows, start=1)]
    uids = [p.uid for p in plants]
    if len(set(uids)) != len(uids):
        raise ValueError(f"{path}: duplicate plant uids")
    names = [p.name for p in plants]
    if len(set(names)) != len(names):
        raise ValueError(f"{path}: duplicate plant names")
    return plants


INVENTORY_COLUMNS = (
    "uid",
    "name",
    "generator",
    "type",
    "lat",
    "lon",
    "pmax_mw",
    "pmax_basis",
    "injection_mw",
    "elevation",
    "commissioning",
    "turbine",
    "turbine_mw",
    "n_turbines",
    "hub_height",
    "rotor_diameter",
    "tracking",
    "dc_ac_ratio",
    "module",
    "module_wp",
    "n_modules",
    "inverter",
    "inverter_kw",
    "n_inverters",
    "source",
)


def write_inventory(plants: list[Plant], path: Path) -> None:
    """CSV inventory (the base columns plus every attribute present)."""
    extra = sorted({k for p in plants for k in p.attrs} - set(INVENTORY_COLUMNS))
    cols = list(INVENTORY_COLUMNS) + extra
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for p in plants:
            row = {
                "uid": p.uid,
                "name": p.name,
                "generator": p.generator,
                "type": p.type,
                "lat": p.lat,
                "lon": p.lon,
                "pmax_mw": p.pmax_mw,
                "pmax_basis": p.pmax_basis,
                "injection_mw": p.injection_mw,
                "elevation": p.elevation,
                "commissioning": p.commissioning,
            }
            row.update(p.attrs)
            w.writerow({c: row.get(c) for c in cols})


# ---------------------------------------------------------------------------
# sizing
# ---------------------------------------------------------------------------


def choose_turbine_mw(
    pmax_mw: float,
    lo: float = GENERIC_TURBINE_MW[0],
    hi: float = GENERIC_TURBINE_MW[1],
    step: float = GENERIC_TURBINE_STEP,
) -> float:
    """A turbine rating in [lo, hi] that divides ``pmax_mw`` as exactly as
    possible (ties: the rating nearest the middle of the range).  Plants
    smaller than ``lo`` are one turbine of ``pmax_mw``."""
    if pmax_mw <= lo:
        return pmax_mw
    best = (math.inf, math.inf, lo)
    n_steps = int(round((hi - lo) / step))
    mid = 0.5 * (lo + hi)
    for i in range(n_steps + 1):
        r = round(lo + i * step, 6)
        n = max(1, round(pmax_mw / r))
        err = abs(n * r - pmax_mw)
        key = (round(err, 6), abs(r - mid), r)
        best = min(best, key)
    return best[2]


@dataclass
class Sizing:
    """Equipment of a plant sized to its reported Pmax, and its provenance."""

    plant: str
    type: str
    pmax_mw: float
    ceiling_mw: float
    source: str  # "cen" or "generic" (per plant)
    installed_ratio: float  # wind: Σ ratings / Pmax(gross basis)
    ac_limit: float  # p.u. of Pmax (PV: inverters / injection; wind: 1)
    turbine: str | None = None
    turbine_mw: float | None = None
    n_turbines: int | None = None
    hub_height: float | None = None
    dc_ac_ratio: float | None = None
    dc_mw: float | None = None
    module: str | None = None
    module_wp: float | None = None
    n_modules: int | None = None
    gamma_pdc: float | None = None
    library_match: str | None = None
    warnings: list[str] = field(default_factory=list)

    def row(self) -> dict[str, Any]:
        """Flat dict for the sizing report."""
        return {k: v for k, v in self.__dict__.items() if k != "warnings"} | {
            "warnings": "; ".join(self.warnings)
        }


def size_wind(
    p: Plant,
    turbine_spec: str,
    electrical_loss: float,
    tolerance: float = 0.05,
    equipment_source: str = "cen",
) -> Sizing:
    """Turbine count and installed ratio reproducing Pmax."""
    use_cen = equipment_source == "cen"
    rating = p.get("turbine_mw") if use_cen else None
    n_rep = p.get("n_turbines") if use_cen else None
    model = p.get("turbine") if use_cen else None
    src = "cen" if (rating or model) else "generic"
    warns: list[str] = []
    gross_target = (
        p.pmax_mw / (1.0 - electrical_loss) if p.pmax_basis == "net" else p.pmax_mw
    )
    if rating is None:
        rating = choose_turbine_mw(gross_target)
    n = int(n_rep) if n_rep else max(1, round(gross_target / rating))
    installed = n * rating
    if n_rep and abs(installed / gross_target - 1.0) > tolerance:
        warns.append(
            f"CEN equipment {n} x {rating:g} MW = {installed:g} MW disagrees with "
            f"Pmax {p.pmax_mw:g} MW ({p.pmax_basis}); Pmax is the ceiling"
        )
    spec = turbine_spec
    match = None
    if model:
        oedb = (
            eq.match_oedb(str(model), rating)
            if not str(model).startswith(("oedb:", "iec", "sp"))
            else None
        )
        if oedb:
            spec, match = f"oedb:{oedb}", oedb
        elif str(model).startswith(("oedb:", "iec", "sp")):
            spec = str(model)
        else:
            rotor = p.get("rotor_diameter") or eq.rotor_from_model(str(model))
            if rotor and rating:
                sp_w = rating * 1e6 / (math.pi * float(rotor) ** 2 / 4.0)
                spec = f"sp{round(sp_w)}"
            warns.append(
                f"turbine {model!r} not in the OEDB library: generic curve {spec}"
            )
    hub = p.get("hub_height") if use_cen else None
    return Sizing(
        plant=p.name,
        type="wind",
        pmax_mw=p.pmax_mw,
        ceiling_mw=p.ceiling_mw,
        source=src,
        installed_ratio=installed / gross_target,
        ac_limit=p.ceiling_mw / p.pmax_mw,
        turbine=spec,
        turbine_mw=rating,
        n_turbines=n,
        hub_height=float(hub) if hub else eq.generic_hub_height(p.commissioning),
        library_match=match,
        warnings=warns,
    )


def size_pv(
    p: Plant,
    dc_ac_ratio: float | None,
    tolerance: float = 0.05,
    equipment_source: str = "cen",
    dc_ac_table: tuple[tuple[int, float], ...] = eq.GENERIC_DC_AC,
) -> Sizing:
    """AC ceiling, DC size and module count reproducing Pmax.  The generic
    DC/AC is ``dc_ac_ratio`` or, when None, ``dc_ac_table`` by the plant's
    commissioning year (:func:`_equipment.generic_dc_ac`)."""
    if dc_ac_ratio is None:
        dc_ac_ratio = eq.generic_dc_ac(p.commissioning, dc_ac_table)
    use_cen = equipment_source == "cen"
    warns: list[str] = []
    src = "generic"
    ac_mw = p.ceiling_mw
    inv_kw = p.get("inverter_kw") if use_cen else None
    n_inv = p.get("n_inverters") if use_cen else None
    inv_model = p.get("inverter") if use_cen else None
    if inv_model and not inv_kw:
        m = eq.match_inverter(str(inv_model))
        if m:
            inv_kw = m[1] / 1000.0
    if inv_kw and n_inv:
        src = "cen"
        inv_mw = float(inv_kw) * int(n_inv) / 1000.0
        if abs(inv_mw / p.pmax_mw - 1.0) > tolerance:
            warns.append(
                f"inverters {n_inv} x {inv_kw:g} kW = {inv_mw:g} MW vs Pmax "
                f"{p.pmax_mw:g} MW; ceiling = min(inverters, Pmax)"
            )
        ac_mw = min(ac_mw, inv_mw)
    wp = p.get("module_wp") if use_cen else None
    n_mod = p.get("n_modules") if use_cen else None
    ratio = p.get("dc_ac_ratio") if use_cen else None
    if ratio:
        src = "cen"
    if wp and n_mod:
        src = "cen"
        dc_rep = float(wp) * int(n_mod) / 1e6
        if not ratio:
            ratio = dc_rep / p.pmax_mw
    if not ratio:
        ratio = dc_ac_ratio
    if not 0.8 <= float(ratio) <= 2.0:
        warns.append(f"DC/AC {ratio:.2f} implausible: generic {dc_ac_ratio:g}")
        ratio = dc_ac_ratio
    module = p.get("module") if use_cen else None
    gamma = p.get("gamma_pdc")
    match = None
    if module and gamma is None:
        mm = eq.match_module(str(module))
        if mm:
            gamma, match = mm.gamma_pdc, mm.name
            src = "cen"
        else:
            warns.append(f"module {module!r} not in the CEC database: generic gamma")
    wp = float(wp) if wp else GENERIC_MODULE_WP
    dc_mw = p.pmax_mw * float(ratio)
    return Sizing(
        plant=p.name,
        type="pv",
        pmax_mw=p.pmax_mw,
        ceiling_mw=ac_mw,
        source=src,
        installed_ratio=1.0,
        ac_limit=ac_mw / p.pmax_mw,
        dc_ac_ratio=float(ratio),
        dc_mw=dc_mw,
        module=str(module) if module else "generic mono-PERC/TOPCon",
        module_wp=wp,
        n_modules=int(n_mod) if n_mod else int(round(dc_mw * 1e6 / wp)),
        gamma_pdc=float(gamma) if gamma is not None else eq.GENERIC_GAMMA_PDC,
        library_match=match,
        warnings=warns,
    )


def check_peak(
    sizing: Sizing, peak_pu: float, tolerance: float = 0.05
) -> dict[str, Any]:
    """The Pmax consistency check: the modelled peak (max hourly output
    over the history, before availability and the cap, p.u. of Pmax) must
    be within ``tolerance`` of the ceiling (Pmax, or the injection /
    inverter limit)."""
    expected = sizing.ac_limit
    ok = abs(peak_pu - expected) <= tolerance
    row = sizing.row()
    row.update(
        {
            "modelled_peak_pu": round(peak_pu, 4),
            "modelled_peak_mw": round(peak_pu * sizing.pmax_mw, 3),
            "expected_peak_pu": round(expected, 4),
            "peak_ok": ok,
        }
    )
    if not ok:
        logger.warning(
            "plant %s: modelled peak %.3f p.u. vs ceiling %.3f (tolerance %.2f)",
            sizing.plant,
            peak_pu,
            expected,
            tolerance,
        )
    return row
