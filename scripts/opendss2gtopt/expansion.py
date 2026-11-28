# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Distribution expansion studies on a converted feeder.

Adds expansion candidates to a LinDistFlow planning built by
:func:`opendss2gtopt.convert.build_planning`:

* ``vad``: the Chilean *Valor Agregado de Distribución* sizing core --
  the feeder at its dimensioning (grown) peak day must meet the NTD
  voltage band at minimum annualised cost.  Candidates: a parallel
  circuit on every line section (reconductoring / new circuit, binary
  under ``integer=True``), transformer upgrades in modules (the
  substation and every other transformer, all at the HV/MV per-MVA
  cost -- an assumption for MV/LV units), and a battery (non-wires
  alternative) at the feeder end.
  Capex is annualised (``overnight_capcost`` + ``economic_life``) at
  ONE rate, the regulated VAD rate (LGSE art. 182), set as the social
  rate ``simulation.annual_discount_rate``; no candidate carries a
  ``wacc`` (the empresa modelo is remunerated at the regulated rate, so
  there is no private-rate wedge).  COyM is a fixed O&M share of capex,
  energy and losses are valued at the PNP and unserved energy at the
  outage cost.
* ``hosting``: PV hosting capacity -- zero-cost PV candidates at the
  chosen buses; the LP builds PV until a voltage or thermal limit binds.

Cost parameters come from ``data/vad_params.json`` (public CNE / NTD /
NREL sources with URLs and date; see docs/methods/distribution-expansion.md).
Values the public sources do not give (BESS capex) are explicit
arguments, labelled as assumptions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .network import Network

DATA = Path(__file__).resolve().parent / "data" / "vad_params.json"

# Assumption (not in the public VAD sources): 4 h utility BESS, USD/kWh.
DEFAULT_BESS_USD_KWH = 400.0

STUDIES = ("none", "vad", "hosting")


def load_vad_params(path: str | Path | None = None) -> dict[str, Any]:
    """The derived VAD parameter set (``data/vad_params.json``)."""
    with open(path or DATA, encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def item(params: dict[str, Any], key: str) -> float:
    """Value of one ``items`` entry of the parameter set."""
    for it in params["items"]:
        if it["key"] == key:
            return float(it["value"])
    raise KeyError(key)


def crf(rate: float, life: float) -> float:
    """Capital recovery factor ``r(1+r)^n / ((1+r)^n - 1)``."""
    if rate <= 0.0:
        return 1.0 / life
    g = (1.0 + rate) ** life
    return rate * g / (g - 1.0)


def _feeder_end(planning: dict[str, Any]) -> str:
    """Bus farthest (in series resistance) from the slack."""
    system = planning["system"]
    slack = str(system["bus_array"][0]["name"])
    adj: dict[str, list[tuple[str, float]]] = {}
    for ln in system["line_array"]:
        a, b = str(ln["bus_a"]), str(ln["bus_b"])
        r = (
            float(ln.get("resistance", 0.0))
            / max(float(ln.get("voltage", 1.0)), 1e-9) ** 2
        )
        adj.setdefault(a, []).append((b, r))
        adj.setdefault(b, []).append((a, r))
    dist = {slack: 0.0}
    stack = [slack]
    while stack:
        u = stack.pop()
        for v, r in adj.get(u, []):
            if v not in dist:
                dist[v] = dist[u] + r
                stack.append(v)
    return max(dist, key=lambda k: dist[k])


def add_vad_candidates(
    planning: dict[str, Any],
    net: Network,
    *,
    params: dict[str, Any] | None = None,
    growth: float = 1.5,
    voltage_band: float | None = None,
    integer: bool = False,
    bess_bus: str | None = None,
    bess_usd_kwh: float = DEFAULT_BESS_USD_KWH,
    line_usd_km: float | None = None,
) -> dict[str, Any]:
    """Turn *planning* (LinDistFlow, daily) into a VAD sizing study."""
    p = params or load_vad_params()
    rate = float(p["annuity_rate"])
    coym = float(p["coym_pct"])
    lives = p["lives"]
    band = (
        voltage_band
        if voltage_band is not None
        else p["quality"]["mv_voltage_band_pct_high_medium_density"] / 100.0
    )
    usd_km = (
        line_usd_km
        if line_usd_km is not None
        else item(p, "mv_line_23kv_overhead_per_km_long_rural")
    )
    system = planning["system"]
    opts = planning["options"]
    mo = opts["model_options"]
    mo["demand_fail_cost"] = item(p, "cfld_sen_0_5pct")
    # One rate for annuities and discounting: the regulated VAD rate as
    # the social rate.  No per-candidate wacc -> annuities fall back to it.
    opts.pop("annual_discount_rate", None)
    planning["simulation"]["annual_discount_rate"] = rate

    for bus in system["bus_array"][1:]:
        bus["vmin_pu"] = round(1.0 - band, 6)
        bus["vmax_pu"] = round(1.0 + band, 6)
    for d in system["demand_array"]:
        d["lmax"] = [[round(v * growth, 9) for v in row] for row in d["lmax"]]
    src = system["generator_array"][0]
    src["gcost"] = float(p["loss_energy_price_usd_mwh"])

    def econ(overnight_per_unit: float, life: float) -> dict[str, Any]:
        return {
            "overnight_capcost": round(overnight_per_unit, 6),
            "economic_life": life,
            "fixed_om_cost": round(coym * overnight_per_unit, 6),
        }

    by_name = {b.name: b for b in net.branches}
    lines = system["line_array"]
    new_lines: list[dict[str, Any]] = []
    for ln in list(lines):
        br = by_name.get(str(ln["name"]))
        if br is None:
            continue
        rating = float(br.rating_mva or 0.0)
        if br.kind == "line":
            step = max(rating, 1.0)
            km = br.length_m / 1000.0 if br.length_m else 1.0
            cand = {
                "uid": len(lines) + len(new_lines) + 1,
                "name": f"{br.name}_x2",
                "bus_a": ln["bus_a"],
                "bus_b": ln["bus_b"],
                "voltage": ln["voltage"],
                "resistance": ln["resistance"],
                "reactance": ln["reactance"],
                "type": "candidate",
                "capacity": 0.0,
                "expcap": round(step, 6),
                "expmod": 1,
                **econ(usd_km * km / step, float(lives["conductor_bare_yr"])),
            }
            if integer:
                cand["integer_expmod"] = True
            new_lines.append(cand)
        elif br.kind == "transformer" and rating > 0.0:
            # every transformer gets upgrade modules at the HV/MV
            # substation per-MVA cost: the first one from the source is
            # the substation; for the others (MV/MV, MV/LV, regulators)
            # the per-MVA cost is an ASSUMPTION (Chilean MV/LV unit
            # costs are not public).
            ln.pop("tmax_ab", None)
            ln.pop("tmax_ba", None)
            ln["capacity"] = round(rating, 6)
            ln["expcap"] = round(rating / 2.0, 6)
            ln["expmod"] = 2
            ln.update(
                econ(
                    item(p, "hv_mv_substation_package_per_mva"),
                    float(lives["electromechanical_equipment_yr"]),
                )
            )
            if integer:
                ln["integer_expmod"] = True
    lines.extend(new_lines)

    bus = bess_bus or _feeder_end(planning)
    peak = sum(max(row) for d in system["demand_array"] for row in d["lmax"])
    e_step = max(round(peak * 0.25, 3), 0.1)  # MWh per module
    bat = {
        "uid": len(system.get("battery_array", [])) + 1,
        "name": "bess_nwa",
        "bus": bus,
        "eini": 0.0,
        "capacity": 0.0,
        "expcap": e_step,
        "expmod": 8,
        "input_efficiency": 0.95,
        "output_efficiency": 0.95,
        "pmax_charge": round(e_step * 8 / 4, 6),
        "pmax_discharge": round(e_step * 8 / 4, 6),
        **econ(bess_usd_kwh * 1000.0, float(lives["backup_generators_yr"])),
    }
    if integer:
        bat["integer_expmod"] = True
    system.setdefault("battery_array", []).append(bat)
    system["name"] = f"{system['name']}_vad"
    return planning


def add_hosting_candidates(
    planning: dict[str, Any],
    *,
    buses: list[str] | None = None,
    module_mw: float = 0.1,
    max_modules: int = 200,
    integer: bool = False,
) -> dict[str, Any]:
    """Zero-cost PV candidates: the LP builds PV up to the hosting limit."""
    system = planning["system"]
    nblocks = len(planning["simulation"]["block_array"])
    shape = [
        max(0.0, 1.0 - ((h + 0.5 - 12.5) / 6.0) ** 2) if nblocks == 24 else 1.0
        for h in range(nblocks)
    ]
    gens = system["generator_array"]
    profiles = system.setdefault("generator_profile_array", [])
    for bname in buses or [_feeder_end(planning)]:
        g: dict[str, Any] = {
            "uid": len(gens) + 1,
            "name": f"pv_host_{bname}",
            "bus": bname,
            "type": "pv",
            "gcost": 0.0,
            "capacity": 0.0,
            "expcap": module_mw,
            "expmod": max_modules,
            "annual_capcost": 1e-3,
        }
        if integer:
            g["integer_expmod"] = True
        gens.append(g)
        profiles.append(
            {
                "uid": len(profiles) + 1,
                "name": f"{g['name']}_profile",
                "generator": g["name"],
                "profile": [[[round(v, 6) for v in shape]]],
            }
        )
    system["name"] = f"{system['name']}_hosting"
    return planning
