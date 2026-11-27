# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Marginal emission factors and the CO2 value of a project.

The marginal emission factor (MEF, t/MWh) of a bus and period is the
emission rate of the unit(s) setting its price: generators whose
variable cost equals the bus price (within a tolerance), that run, and
whose own bus has the same price (same congestion zone).  When no
generator matches (price set by storage, by unserved energy or by a
line), the running generator of the zone with the variable cost closest
to the price is taken.  This is the light-weight version of
``gtopt-marginal-units`` (which also reads the LP duals and the network
zones); an MEF table produced by that tool can be passed instead
(:func:`read_mef_csv`).

The CO2 value of a project at a carbon price ``p`` (dispatch unchanged,
the price passed through to the marginal price):

``V = p · Σ_t h_t · (MEF_t · net_t − e_own · gen_t)``

— the marginal price rises by ``p·MEF_t``, the project pays ``p·e_own``
on its own output.  For a battery (``e_own = 0``) this is the emission
arbitrage ``Σ MEF_t · (discharge_t − charge_t)``.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from pathlib import Path

from .case_data import PKey, ProjectData, SystemData

MefMap = dict[tuple[PKey, int], float]


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def marginal_emission_factors(
    sysd: SystemData,
    rate_of: Mapping[str, float],
    *,
    tol: float = 1e-4,
    exclude: set[str] | None = None,
) -> MefMap:
    """MEF per ``(period key, bus)`` for every bus that has a price."""
    skip = exclude or set()
    buses = {bus for (_s, _t, _b, bus) in sysd.price}
    out: MefMap = {}
    for key, gens in sysd.dispatch.items():
        running = [g for g in gens if g[3] > 1e-6 and g[1] not in skip]
        for bus in buses:
            lam = sysd.bus_price(key, bus)
            zone = [g for g in running if _close(sysd.bus_price(key, g[2]), lam, tol)]
            if not zone:
                out[(key, bus)] = 0.0
                continue
            marg = [g for g in zone if _close(g[4], lam, tol)]
            if not marg:
                best = min(abs(g[4] - lam) for g in zone)
                marg = [g for g in zone if abs(g[4] - lam) <= best + 1e-9]
            out[(key, bus)] = sum(rate_of.get(g[1], 0.0) for g in marg) / len(marg)
    return out


def read_mef_csv(path: Path) -> MefMap:
    """MEF table ``scenario,stage,block,bus,mef`` (t/MWh)."""
    out: MefMap = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            key = (int(row["scenario"]), int(row["stage"]), int(row["block"]))
            out[(key, int(row["bus"]))] = float(row["mef"])
    return out


def co2_tonnes(proj: ProjectData, mef: MefMap, own_rate: float) -> tuple[float, float]:
    """(avoided marginal t/yr ``Σ MEF·net·h``, own emissions t/yr)."""
    avoided = sum(mef.get((r.key, proj.bus), 0.0) * r.net * r.hours for r in proj.rows)
    own = own_rate * proj.energy_mwh()
    return avoided, own


def co2_value(proj: ProjectData, mef: MefMap, own_rate: float, price: float) -> float:
    """Yearly CO2 value [$/yr] at carbon ``price`` [$/t] (see module doc)."""
    avoided, own = co2_tonnes(proj, mef, own_rate)
    return price * (avoided - own)
