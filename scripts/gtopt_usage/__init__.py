# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""gtopt_usage: usage accounting, maintenance cost and SSCC wear cost θ.

Turns one gtopt output directory into per-unit usage metrics (operating
hours, energy, hours at the committed minimum, starts / shutdowns, ramp
mileage, reserve MW·h held per service, BESS throughput and equivalent full
cycles), aggregated per stage and per year (:func:`usage_table`,
:func:`reserve_table`).

With per-technology maintenance parameters (:class:`MaintenanceParams`:
OEM intervals in EOH / starts / cycles, cost per maintenance event, the
NTCyO factors Kf / Kd, outage days) it computes equivalent operating hours
(EOH), the effective interval HEOM = HOM − Nf·Kf − Nd·Kd and the maintenance
cost per unit and technology (:func:`maintenance_table`).

:func:`derive_theta` compares two runs (without / with SSCC) and normalises
the maintenance-cost delta per technology by the reserve MW·h held, per
service: θ [$/MW·h] = Δ maintenance cost / reserve MW·h, reported next to
the known variable reserve costs written by gtopt (part-load, activation and
degradation adders) so that θ = known variable costs + derived maintenance
delta.

:func:`incremental_maintenance` runs maintenance counters per unit and
type (EOH / starts / cycles / calendar years, hierarchical reset, imperfect
maintenance) against a fixed calendar and prices only the B − A
difference, by the rate method (ΔEOH · Σ C_k / HOM_k) and the event method
(extra events, advancement of calendar events, replacement energy and
terminal wear, discounted), with θ per technology, service and direction for both.

The gtopt side needs ``options.output_usage_metrics = true`` for the
indicator / mileage streams (``Generator/usage_on``, ``Generator/usage_ramp``,
``Commitment/usage_at_pmin``); without them the operating hours fall back to
``generation_sol > ε`` and the mileage / pmin hours read 0.
"""

from gtopt_usage.accounting import reserve_table, usage_table
from gtopt_usage.counters import CalendarEvent, CounterSim, MaintType, Period
from gtopt_usage.incremental import (
    MaintenanceReport,
    WearParams,
    WearParamSet,
    incremental_maintenance,
    load_wear_params,
)
from gtopt_usage.maintenance import (
    MaintenanceParams,
    ParamSet,
    load_params,
    maintenance_table,
)
from gtopt_usage.theta import ThetaReport, derive_theta

__all__ = [
    "CalendarEvent",
    "CounterSim",
    "MaintType",
    "MaintenanceReport",
    "Period",
    "WearParamSet",
    "WearParams",
    "incremental_maintenance",
    "load_wear_params",
    "MaintenanceParams",
    "ParamSet",
    "ThetaReport",
    "derive_theta",
    "load_params",
    "maintenance_table",
    "reserve_table",
    "usage_table",
]
