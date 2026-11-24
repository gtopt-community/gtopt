# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""GAMS-derived unit parameters of ``StarGenLite_Clustered-UC.gms``.

Every formula below transcribes one assignment of the GAMS model (the
``SET VALUE VARIABLES`` and ``SET BOUNDS`` sections), converted back from
the model's GW / MEUR scaling to MW / EUR so the gtopt case carries plain
MW and EUR (the objective is then directly comparable: GAMS MEUR x 1e6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from cucdata2gtopt._reader import RawUnit

#: ``p2ndResUpCost`` / ``p2ndResDwCost``: reserve cost = factor x pFixCost.
RESERVE_UP_COST_FACTOR = 0.4
RESERVE_DN_COST_FACTOR = 0.2

#: GAMS objective term ``pFixCost[MEUR/h] * p * v2ndRes[GW]`` in EUR per MW
#: of reserve is ``factor * fix_cost[EUR/h] * 1e-3`` (MEUR x GW -> EUR x MW
#: carries one 1e-3 that the energy terms do not).
RESERVE_COST_UNIT = 1e-3


@dataclass(frozen=True)
class UnitParams:
    """One thermal unit after the GAMS pre-processing (MW, EUR, h)."""

    name: str
    bus: int
    pmax: float
    pmin: float
    ini_out: float
    ini_state: float
    su_cap: float  #: startup capability, clamped to [pmin, pmax]
    sd_cap: float  #: shutdown capability, clamped to [pmin, pmax]
    ramp_up: float  #: clamped to pmax - pmin
    ramp_dn: float  #: clamped to pmax - pmin
    min_up: float  #: pMinTU (0 -> 1)
    min_dn: float  #: pMinTD (0 -> 1)
    var_cost: float  #: EUR/MWh on total output
    fix_cost: float  #: EUR/h while committed (no-load)
    startup_cost: float  #: EUR/start incl. SU-duration fix + half-pmin energy
    shutdown_cost: float  #: EUR/stop incl. SD-duration fix + half-pmin energy

    @property
    def ini_uc(self) -> int:
        """``pIniUC``: committed at t=0 iff the initial output >= pmin."""
        return 1 if self.ini_out >= self.pmin else 0

    @property
    def ini_up(self) -> float:
        """``pIniUp``: remaining min-up hours at t=0."""
        if self.ini_state < 0:
            return 0.0
        return max(0.0, self.min_up - self.ini_state) * self.ini_uc

    @property
    def ini_dn(self) -> float:
        """``pIniDw``: remaining min-down hours at t=0."""
        if self.ini_state >= 0:
            return 0.0
        return max(0.0, self.min_dn - abs(self.ini_state)) * (1 - self.ini_uc)

    @property
    def pinned_periods(self) -> int:
        """Periods whose ``vCommit`` GAMS fixes to ``pIniUC``
        (``ord(p) <= pIniUp + pIniDw``)."""
        return math.floor(self.ini_up + self.ini_dn + 1e-9)

    @property
    def is_t1(self) -> bool:
        """``t1(g)``: units with ``pMinTU = 1`` use eMaxOut2/eMaxOut3."""
        return self.min_up == 1

    @property
    def min_up_periods(self) -> int:
        """Length of the eMinTUp window: ``floor(pMinTU)`` hourly periods."""
        return max(1, math.floor(self.min_up + 1e-9))

    @property
    def min_dn_periods(self) -> int:
        """Length of the eMinTDw window: ``floor(pMinTD)`` hourly periods."""
        return max(1, math.floor(self.min_dn + 1e-9))

    @property
    def reserve_max(self) -> float:
        """``v2ndResUP.up`` / ``v2ndResDW.up`` of one unit."""
        return min(self.ramp_up + self.ramp_dn, self.pmax - self.pmin)

    @property
    def reserve_up_cost(self) -> float:
        """EUR per MW of up reserve (GAMS objective, see module doc)."""
        return RESERVE_UP_COST_FACTOR * self.fix_cost * RESERVE_COST_UNIT

    @property
    def reserve_dn_cost(self) -> float:
        """EUR per MW of down reserve."""
        return RESERVE_DN_COST_FACTOR * self.fix_cost * RESERVE_COST_UNIT

    def capacity_rows(self) -> list[tuple[float, float]]:
        """``(a_y, a_z)`` of the SU/SD capacity rows
        ``p + r_up <= P u - a_y y_t - a_z z_{t+1}``.

        Non-t1 units: eMaxOut, one row.  t1 units: eMaxOut2 + eMaxOut3.
        """
        span = self.pmax
        if not self.is_t1:
            return [(span - self.su_cap, span - self.sd_cap)]
        return [
            (span - self.su_cap, max(self.su_cap - self.sd_cap, 0.0)),
            (max(self.sd_cap - self.su_cap, 0.0), span - self.sd_cap),
        ]


def derive_unit(raw: RawUnit, name: str | None = None) -> UnitParams:
    """Apply the GAMS pre-processing to one raw workbook row."""
    pmax = raw["MaxProd"]
    pmin = raw["MinProd"]
    fuel = raw["FuelCost"]
    var_cost = raw["OMVarCost"] + raw["SlopeVarCost"] * fuel
    fix_cost = raw["InterVarCost"] * fuel
    su_dur = raw["SUduration1"]
    sd_dur = raw["SDduration"]
    return UnitParams(
        name=name or raw.name,
        bus=int(raw["Bus"]),
        pmax=pmax,
        pmin=pmin,
        ini_out=raw["IniProd"],
        ini_state=raw["IniState"],
        su_cap=min(pmax, max(pmin, raw["SUcap"])),
        sd_cap=min(pmax, max(pmin, raw["SDcap"])),
        ramp_up=min(pmax - pmin, raw["RampUp"]),
        ramp_dn=min(pmax - pmin, raw["RampDw"]),
        min_up=raw["MinTU"] or 1.0,
        min_dn=raw["MinTD"] or 1.0,
        var_cost=var_cost,
        fix_cost=fix_cost,
        startup_cost=raw["SUcost1"] * fuel
        + su_dur * fix_cost
        + su_dur * pmin * var_cost / 2.0,
        shutdown_cost=raw["ShutdownCost"] * fuel
        + sd_dur * fix_cost
        + sd_dur * pmin * var_cost / 2.0,
    )
