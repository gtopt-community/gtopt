# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Maintenance requirement and cost from usage (EOH / starts / cycles).

Definitions (NTCyO Capítulo Costos Variables, CNE 2023, arts. 2-33 to 2-42):

* HOM — operating hours between expected preventive maintenances
  (``interval_eoh``).
* HEOM = HOM − Nf·Kf − Nd·Kd — the operating hours actually reached when
  failures (Nf, Kf [h/failure]) and starts/stops (Nd, Kd [h/start]) consume
  part of the interval.
* COM — storage cycles between maintenances (``interval_cycles``).

Equivalent operating hours consumed by the usage of a unit::

    EOH = hours + Kd·Nd + Kf·Nf + a_pmin·pmin_hours + a_ramp·ramp_MW
          + Σ_{s,d} a_res[s,d] · reserve_MWh[s,d]

with ``Nd`` = starts (each start closes one start/stop cycle) and
``Nf = failures_per_year · years``.  Maintenance events over the period are
the first interval reached (OEM "whichever occurs first")::

    events = max(EOH / interval_eoh, starts / interval_starts,
                 EFC / interval_cycles)

and ``cost = events · cost_per_event``, ``outage_hours = events ·
outage_days · 24``, ``HEOM = interval_eoh · hours / EOH`` (the operating
hours per maintenance cycle).  Events are fractional on purpose: over a
planning horizon they are the expected share of the next maintenance.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gtopt_usage.wear_keys import parse_factors, resolve

#: Columns of :func:`maintenance_table` added to the usage columns.
MAINTENANCE_COLUMNS = [
    "eoh",
    "events",
    "events_eoh",
    "events_starts",
    "events_cycles",
    "heom",
    "cost",
    "outage_hours",
]


@dataclass(frozen=True)
class MaintenanceParams:
    """Maintenance parameters of one technology (or one unit override).

    Every interval is optional; an unset interval does not trigger events.
    """

    interval_eoh: float | None = None  #: HOM [EOH]
    interval_starts: float | None = None  #: OEM starts interval
    interval_cycles: float | None = None  #: COM [equivalent full cycles]
    cost_per_event: float = 0.0  #: $ per maintenance event
    kf: float = 0.0  #: [EOH per failure]
    kd: float = 0.0  #: [EOH per start/stop]
    failures_per_year: float = 0.0  #: Nf rate [1/year]
    eoh_per_pmin_hour: float = 0.0  #: extra wear at the committed minimum
    eoh_per_ramp_mw: float = 0.0  #: EOH per MW of ramp mileage
    #: EOH per MW·h of reserve held, by service (zone name), per direction
    #: (``{"cpf": {"up": a, "dn": b}}`` or ``"cpf_up"``) or ``"*"``.
    eoh_per_reserve_mwh: Mapping[str, float] = field(default_factory=dict)
    outage_days: float = 0.0  #: outage per maintenance event [days]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MaintenanceParams:
        """Build from a JSON object; unknown keys raise ``ValueError``."""
        known = {f.name for f in fields(cls)}
        bad = sorted(set(data) - known - {"technology"})
        if bad:
            raise ValueError(f"unknown maintenance parameter(s): {', '.join(bad)}")
        kwargs: dict[str, Any] = {k: v for k, v in data.items() if k in known}
        kwargs["eoh_per_reserve_mwh"] = parse_factors(
            kwargs.get("eoh_per_reserve_mwh", {}), "eoh_per_reserve_mwh"
        )
        out = cls(**kwargs)
        out.validate()
        return out

    def merged(self, data: Mapping[str, Any]) -> MaintenanceParams:
        """A copy with the keys of ``data`` overriding this one."""
        base = {f.name: getattr(self, f.name) for f in fields(self)}
        base.update({k: v for k, v in data.items() if k != "technology"})
        return MaintenanceParams.from_dict(base)

    def validate(self) -> None:
        """Reject negative or zero intervals and negative factors."""
        for name in ("interval_eoh", "interval_starts", "interval_cycles"):
            v = getattr(self, name)
            if v is not None and not v > 0:
                raise ValueError(f"{name} must be > 0 (got {v})")
        for name in (
            "cost_per_event",
            "kf",
            "kd",
            "failures_per_year",
            "eoh_per_pmin_hour",
            "eoh_per_ramp_mw",
            "outage_days",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0 (got {getattr(self, name)})")
        if any(v < 0 for v in self.eoh_per_reserve_mwh.values()):
            raise ValueError("eoh_per_reserve_mwh factors must be >= 0")

    def reserve_factor(self, service: str, direction: str | None = None) -> float:
        """EOH per MW·h of reserve of ``(service, direction)``.

        Resolved ``service_direction`` → ``service`` → ``"*"``
        (:func:`gtopt_usage.wear_keys.resolve`).
        """
        return resolve(self.eoh_per_reserve_mwh, service, direction)


@dataclass(frozen=True)
class ParamSet:
    """Maintenance parameters by technology, with per-unit overrides.

    JSON layout::

        {"technologies": {"gas_turbine": {"interval_eoh": 24000, ...}},
         "units": {"NEHUENCO_2": {"technology": "gas_turbine", "kd": 20}},
         "service_weights": {"cpf": {"up": 2.0, "dn": 1.0}, "csf": 1.0}}
    """

    technologies: Mapping[str, MaintenanceParams] = field(default_factory=dict)
    units: Mapping[str, tuple[str | None, Mapping[str, Any]]] = field(
        default_factory=dict
    )
    service_weights: Mapping[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ParamSet:
        """Parse the JSON layout of the class doc."""
        bad = sorted(set(data) - {"technologies", "units", "service_weights"})
        if bad:
            raise ValueError(f"unknown parameter section(s): {', '.join(bad)}")
        techs = {
            str(k): MaintenanceParams.from_dict(v)
            for k, v in (data.get("technologies") or {}).items()
        }
        units: dict[str, tuple[str | None, Mapping[str, Any]]] = {}
        for name, over in (data.get("units") or {}).items():
            if not isinstance(over, Mapping):
                raise ValueError(f"units.{name} must be an object")
            tech = over.get("technology")
            units[str(name)] = (str(tech) if tech is not None else None, dict(over))
        weights = parse_factors(data.get("service_weights") or {}, "service_weights")
        out = cls(techs, units, weights)
        for name in units:  # fail early on bad overrides
            out.for_unit(name, "")
        return out

    def service_weight(self, service: str, direction: str | None = None) -> float:
        """Allocation weight of ``(service, direction)`` (default 1)."""
        return resolve(self.service_weights, service, direction, 1.0)

    def technology_of(self, name: str, default: str) -> str:
        """Technology of a unit: its override, else ``default``."""
        tech, _ = self.units.get(name, (None, {}))
        return tech or default

    def for_unit(self, name: str, technology: str) -> MaintenanceParams | None:
        """Parameters of a unit (technology defaults + unit overrides)."""
        tech, over = self.units.get(name, (None, {}))
        base = self.technologies.get(tech or technology)
        if base is None:
            if not over:
                return None
            base = MaintenanceParams()
        return base.merged(over) if over else base


def load_params(path: str | Path) -> ParamSet:
    """Read a :class:`ParamSet` JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"{path}: expected a JSON object")
    return ParamSet.from_dict(data)


def _reserve_eoh(
    reserve: pd.DataFrame, params: ParamSet, techs: Mapping[str, str]
) -> pd.Series:
    """Σ_{s,d} a_res[s,d]·MW·h by ``(name, k)``."""
    if reserve.empty:
        return pd.Series(dtype=float)
    dirs = reserve["direction"] if "direction" in reserve else [None] * len(reserve)
    factors = []
    for name, service, d in zip(reserve["name"], reserve["service"], dirs, strict=True):
        p = params.for_unit(name, techs.get(name, "other"))
        factors.append(p.reserve_factor(service, d) if p is not None else 0.0)
    return (
        reserve.assign(v=reserve["mwh"].to_numpy() * np.asarray(factors))
        .groupby(["name", "k"])["v"]
        .sum()
    )


def maintenance_table(
    usage: pd.DataFrame,
    params: ParamSet,
    reserve: pd.DataFrame | None = None,
    hours_per_year: float = 8760.0,
) -> pd.DataFrame:
    """Add EOH, events, HEOM, cost and outage hours to a usage table.

    ``usage`` is a :func:`~gtopt_usage.accounting.usage_table` (per stage)
    or its per-year sum; ``reserve`` a
    :func:`~gtopt_usage.accounting.reserve_table` (needed only for
    ``eoh_per_reserve_mwh``, matched by ``(name, k)``).  Units without
    parameters get zeros.  The stage hours used for ``Nf`` come from the
    unit's operating hours when no grid is attached (failures are an
    exposure-based rate per operating year).

    ``events`` takes the max over the intervals, which is not additive:
    call it on the aggregation level you report (whole horizon, year or
    stage), not on stages to be summed afterwards.
    """
    df = usage.copy()
    for col in MAINTENANCE_COLUMNS:
        df[col] = 0.0
    if df.empty:
        return df
    df["technology"] = [
        params.technology_of(n, t)
        for n, t in zip(df["name"], df["technology"], strict=True)
    ]
    techs = dict(zip(df["name"], df["technology"], strict=False))
    res_eoh = (
        _reserve_eoh(reserve, params, techs)
        if reserve is not None and "k" in df.columns
        else pd.Series(dtype=float)
    )
    rows = []
    for rec in df.to_dict("records"):
        p = params.for_unit(rec["name"], rec["technology"])
        if p is None:
            rows.append(rec)
            continue
        nf = p.failures_per_year * rec["hours"] / hours_per_year
        extra = float(res_eoh.get((rec["name"], rec.get("k")), 0.0))
        eoh = (
            rec["hours"]
            + p.kd * rec["starts"]
            + p.kf * nf
            + p.eoh_per_pmin_hour * rec["pmin_hours"]
            + p.eoh_per_ramp_mw * rec["ramp_mw"]
            + extra
        )
        ev_eoh = eoh / p.interval_eoh if p.interval_eoh else 0.0
        ev_st = rec["starts"] / p.interval_starts if p.interval_starts else 0.0
        ev_cy = rec["efc"] / p.interval_cycles if p.interval_cycles else 0.0
        events = max(ev_eoh, ev_st, ev_cy)
        heom = (
            p.interval_eoh * rec["hours"] / eoh
            if p.interval_eoh and eoh > 0
            else (p.interval_eoh or 0.0)
        )
        rows.append(
            {
                **rec,
                "eoh": eoh,
                "events": events,
                "events_eoh": ev_eoh,
                "events_starts": ev_st,
                "events_cycles": ev_cy,
                "heom": heom,
                "cost": events * p.cost_per_event,
                "outage_hours": events * p.outage_days * 24.0,
            }
        )
    return pd.DataFrame(rows, columns=df.columns)


def by_technology(table: pd.DataFrame) -> pd.DataFrame:
    """Sum a :func:`maintenance_table` per technology."""
    if table.empty:
        return pd.DataFrame(columns=["technology", "units", *MAINTENANCE_COLUMNS])
    num = [
        c
        for c in table.columns
        if c not in ("name", "kind", "technology", "k", "stage", "year", "heom")
        and pd.api.types.is_numeric_dtype(table[c])
    ]
    out = table.groupby("technology", as_index=False)[num].sum()
    out.insert(1, "units", table.groupby("technology")["name"].nunique().to_numpy())
    return out


__all__ = [
    "MAINTENANCE_COLUMNS",
    "MaintenanceParams",
    "ParamSet",
    "by_technology",
    "load_params",
    "maintenance_table",
]
