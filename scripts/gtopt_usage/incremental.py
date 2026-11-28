# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Incremental SSCC maintenance cost from counters and a fixed calendar.

Scheduled maintenance is sunk: only the **difference** between a run
WITHOUT SSCC (A, base) and the same case WITH SSCC (B) is a cost of the
service.  Per unit and per period (gtopt stage) the usage is turned into
counter increments (NTCyO naming: HOM interval, HEOM, Kf, Kd)::

    EOH = Σ_blocks on·h·w(p) + c_pmin·h_pmin + Kd·starts + Kf·trips
          + Σ_{s,d} α_{s,d} · R_{s,d} / Pmax
    w(p) = a + b·(p − Pmin)/(Pmax − Pmin),   p clipped to [Pmin, Pmax]

``b`` may be negative (low-load penalty: more wear near Pmin); ``a >= 0``
and ``a + b >= 0`` keep ``w >= 0``.  ``trips = trips_per_year x years``
(exogenous, default 0).  ``R_{s,d}`` is the reserve held of service ``s``
in direction ``d`` (up / dn) [MW·h], so ``R_{s,d} / Pmax`` counts
equivalent full-reserve hours.  A battery counts equivalent full cycles
``EFC·(DoD / dod_ref)^dod_exponent`` instead (per stage DoD from
``Battery/energy_sol``, exponent 0 = off).

Two cost methods, both reported as B − A:

* **rate**: ``ΔC = ΔEOH · Σ_k C_k / HOM_k  (+ ΔEFC · Σ_k C_k / COM_k)``;
* **event**: the counters of :mod:`gtopt_usage.counters` run against the
  fixed calendar; ``C = PV(extra events) + PV(advancement of calendar
  events) + PV(replacement energy of extra outages) + terminal value of
  the wear left at the end of the horizon``.  The replacement price is
  ``replacement_price`` or else the run's expected bus marginal cost
  (``Bus/balance_dual``) at the unit's bus in that stage.

A negative delta is a saving.  ``gross`` sums the positive unit deltas,
``savings`` the negative ones and ``net`` both, per technology.  θ per
technology, service and direction (the six SSCC products) = net Δ cost /
reserve MW·h, allocated over the products with the ``service_weights``
(as :func:`gtopt_usage.derive_theta`), for both methods side by side.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from gtopt_compare_runs.reader import Run
from gtopt_usage.accounting import (
    HOURS_PER_YEAR,
    ON_EPS,
    _ref_name,
    reserve_table,
    usage_table,
)
from gtopt_usage.wear_keys import parse_factors, resolve
from gtopt_usage.counters import (
    MEASURES,
    CalendarEvent,
    CounterResult,
    CounterSim,
    MaintType,
    Period,
)

#: Default discount rate per year.
DISCOUNT_RATE = 0.06

_UNIT_KEYS = {
    "technology",
    "kd",
    "kf",
    "trips_per_year",
    "load_weight",
    "eoh_per_pmin_hour",
    "alpha_reserve",
    "replacement_price",
    "reset_fraction",
    "dod_ref",
    "dod_exponent",
    "pmin",
    "pmax",
    "types",
    "initial",
}


@dataclass(frozen=True)
class WearParams:
    """Counter parameters of one technology or unit (see the module doc)."""

    types: tuple[MaintType, ...] = ()  #: maintenance types, lowest first
    kd: float = 0.0  #: [EOH per start]
    kf: float = 0.0  #: [EOH per trip]
    trips_per_year: float = 0.0  #: exogenous trip rate
    load_a: float = 1.0  #: w(Pmin)
    load_b: float = 0.0  #: w(Pmax) − w(Pmin)
    eoh_per_pmin_hour: float = 0.0  #: extra EOH per hour at Pmin
    alpha_reserve: Mapping[str, float] = field(default_factory=dict)
    replacement_price: float | None = None  #: [$/MWh]; None = bus price
    reset_fraction: float = 0.0  #: imperfect maintenance
    dod_ref: float = 1.0
    dod_exponent: float = 0.0
    pmin: float | None = None  #: [MW]; None = planning value
    pmax: float | None = None  #: [MW]; None = planning value
    initial: Mapping[str, Mapping[str, float]] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any], base: WearParams | None = None
    ) -> WearParams:
        """Parse a technology / unit object, over ``base`` when given."""
        bad = sorted(set(data) - _UNIT_KEYS)
        if bad:
            raise ValueError(f"unknown counter parameter(s): {', '.join(bad)}")
        kw: dict[str, Any] = {}
        for key in (
            "kd",
            "kf",
            "trips_per_year",
            "eoh_per_pmin_hour",
            "reset_fraction",
            "dod_ref",
            "dod_exponent",
        ):
            if key in data:
                kw[key] = float(data[key])
        for key in ("replacement_price", "pmin", "pmax"):
            if key in data:
                kw[key] = None if data[key] is None else float(data[key])
        if "load_weight" in data:
            lw = data["load_weight"]
            if not isinstance(lw, Mapping) or set(lw) - {"a", "b"}:
                raise ValueError("load_weight must be an object {a, b}")
            kw["load_a"] = float(lw.get("a", 1.0))
            kw["load_b"] = float(lw.get("b", 0.0))
        if "alpha_reserve" in data:
            kw["alpha_reserve"] = parse_factors(data["alpha_reserve"], "alpha_reserve")
        if "types" in data:
            kw["types"] = tuple(MaintType.from_dict(t) for t in data["types"])
        if "initial" in data:
            kw["initial"] = _initial_map(data["initial"])
        out = replace(base, **kw) if base is not None else cls(**kw)
        out.validate()
        return out

    def validate(self) -> None:
        """Reject negative factors and a load weight below 0 on [Pmin, Pmax]."""
        for name in (
            "kd",
            "kf",
            "trips_per_year",
            "eoh_per_pmin_hour",
            "dod_exponent",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0 (got {getattr(self, name)})")
        if self.load_a < 0 or self.load_a + self.load_b < 0:
            raise ValueError(
                "load_weight: w = a + b·x must be >= 0 on x in [0, 1] "
                f"(a = {self.load_a}, a + b = {self.load_a + self.load_b})"
            )
        if any(v < 0 for v in self.alpha_reserve.values()):
            raise ValueError("alpha_reserve factors must be >= 0")
        if not 0.0 <= self.reset_fraction < 1.0:
            raise ValueError("reset_fraction must be in [0, 1)")
        if not self.dod_ref > 0:
            raise ValueError("dod_ref must be > 0")
        if self.replacement_price is not None and self.replacement_price < 0:
            raise ValueError("replacement_price must be >= 0")

    def load_weight(self, p: np.ndarray, pmin: float, pmax: float) -> np.ndarray:
        """``w(p) = a + b·(p − Pmin)/(Pmax − Pmin)`` with p clipped."""
        if self.load_b == 0.0:
            return np.full(np.shape(p), self.load_a, dtype=float)
        if not pmax > pmin:
            raise ValueError("load_weight with b != 0 needs pmax > pmin")
        x = (np.clip(p, pmin, pmax) - pmin) / (pmax - pmin)
        return np.asarray(self.load_a + self.load_b * x, dtype=float)

    def alpha(self, service: str, direction: str | None = None) -> float:
        """α of ``(service, direction)`` → ``service`` → ``"*"``."""
        return resolve(self.alpha_reserve, service, direction)


def _initial_map(data: Any) -> dict[str, dict[str, float]]:
    if not isinstance(data, Mapping):
        raise ValueError("initial must be an object {type: {measure: value}}")
    out: dict[str, dict[str, float]] = {}
    for typ, vals in data.items():
        if not isinstance(vals, Mapping) or set(vals) - set(MEASURES):
            raise ValueError(f"initial.{typ}: measures are {', '.join(MEASURES)}")
        out[str(typ)] = {str(m): float(v) for m, v in vals.items()}
    return out


@dataclass(frozen=True)
class WearParamSet:
    """Counter parameters by technology with per-unit overrides.

    JSON layout::

        {"discount_rate": 0.06,
         "technologies": {"gas_turbine": {"kd": 20, "types": [...]}},
         "units": {"G1": {"technology": "gas_turbine", "initial": {...}}},
         "service_weights": {"cpf": {"up": 2.0, "dn": 1.0}}}
    """

    technologies: Mapping[str, WearParams] = field(default_factory=dict)
    units: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    service_weights: Mapping[str, float] = field(default_factory=dict)
    discount_rate: float = DISCOUNT_RATE

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WearParamSet:
        """Parse the JSON layout of the class doc."""
        known = {"discount_rate", "technologies", "units", "service_weights"}
        bad = sorted(set(data) - known)
        if bad:
            raise ValueError(f"unknown parameter section(s): {', '.join(bad)}")
        techs = {
            str(k): WearParams.from_dict(v)
            for k, v in (data.get("technologies") or {}).items()
        }
        units = {str(k): dict(v) for k, v in (data.get("units") or {}).items()}
        weights = parse_factors(data.get("service_weights") or {}, "service_weights")
        rate = float(data.get("discount_rate", DISCOUNT_RATE))
        if rate <= -1.0:
            raise ValueError("discount_rate must be > -1")
        out = cls(techs, units, weights, rate)
        for name in units:
            out.for_unit(name, "")
        return out

    def service_weight(self, service: str, direction: str | None = None) -> float:
        """Allocation weight of ``(service, direction)`` (default 1)."""
        return resolve(self.service_weights, service, direction, 1.0)

    def technology_of(self, name: str, default: str) -> str:
        """Technology of a unit: its override, else ``default``."""
        tech = self.units.get(name, {}).get("technology")
        return str(tech) if tech else default

    def for_unit(self, name: str, technology: str) -> WearParams | None:
        """Parameters of a unit (technology + overrides), None if none."""
        over = self.units.get(name)
        base = self.technologies.get(self.technology_of(name, technology))
        if base is None and over is None:
            return None
        if over is None:
            return base
        return WearParams.from_dict(
            {k: v for k, v in over.items() if k != "technology"}, base
        )

    def with_initial(
        self, initial: Mapping[str, Mapping[str, Mapping[str, float]]]
    ) -> WearParamSet:
        """A copy whose units take ``initial`` counters (unit -> type -> m)."""
        units = {k: dict(v) for k, v in self.units.items()}
        for unit, vals in initial.items():
            units.setdefault(unit, {})["initial"] = {
                t: dict(m) for t, m in vals.items()
            }
        return WearParamSet(
            self.technologies, units, self.service_weights, self.discount_rate
        )


def load_wear_params(path: str | Path) -> WearParamSet:
    """Read a :class:`WearParamSet` JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"{path}: expected a JSON object")
    return WearParamSet.from_dict(data)


def read_initial_csv(path: str | Path) -> dict[str, dict[str, dict[str, float]]]:
    """Initial counters CSV: ``unit,type[,eoh,starts,cycles,years]``."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            unit, typ = row.get("unit"), row.get("type")
            if not unit or not typ:
                raise ValueError(f"{path}: every row needs unit and type")
            out.setdefault(unit, {})[typ] = {
                m: float(row[m]) for m in MEASURES if row.get(m) not in (None, "")
            }
    return out


# ── calendar ────────────────────────────────────────────────────────────


def read_calendar_csv(
    path: str | Path,
    *,
    start_date: str | None = None,
) -> list[CalendarEvent]:
    """Calendar CSV ``unit,type,start,duration``.

    ``start``: hours from the start of the horizon, or an ISO date (needs
    ``start_date``, the date of hour 0).  ``duration`` in days (optional).
    """
    t0 = datetime.fromisoformat(start_date) if start_date else None
    out = []
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            unit, typ, start = row.get("unit"), row.get("type"), row.get("start")
            if not unit or not typ or not start:
                raise ValueError(f"{path}: every row needs unit, type and start")
            try:
                t = float(start)
            except ValueError as exc:
                if t0 is None:
                    raise ValueError(
                        f"{path}: start {start!r} is a date; pass --start-date"
                    ) from exc
                t = (datetime.fromisoformat(start) - t0).total_seconds() / 3600.0
            days = float(row.get("duration") or 0.0)
            out.append(CalendarEvent(unit, typ, t, days * 24.0))
    return out


def block_starts(run: Run) -> np.ndarray:
    """Start hour of every block (chronological block order)."""
    dur = np.asarray(run.grid.durations, dtype=float)
    return np.concatenate([[0.0], np.cumsum(dur)[:-1]]) if dur.size else dur


def stage_spans(run: Run) -> list[tuple[float, float]]:
    """``(t0, hours)`` of every stage."""
    dur = np.asarray(run.grid.durations, dtype=float)
    starts = block_starts(run)
    out = []
    for st in run.grid.stages:
        blocks = list(st.blocks)
        t0 = float(starts[blocks[0]]) if blocks else 0.0
        out.append((t0, float(dur[blocks].sum()) if blocks else 0.0))
    return out


def calendar_from_outages(
    run: Run, types_of: Mapping[str, str], default_type: str | None = None
) -> list[CalendarEvent]:
    """Calendar from ``Generator/outage_fraction_sol`` (plp2gtopt split).

    Contiguous blocks with ``f > 0`` (first scenario) form one event that
    starts with the first block; duration ``Σ f·h``.  The type is
    ``default_type`` or the unit's highest type (``types_of``).
    """
    df = run.cells("Generator", "outage_fraction_sol")
    if df.empty:
        return []
    df = df[df["s"] == df["s"].min()]
    starts = block_starts(run)
    dur = np.asarray(run.grid.durations, dtype=float)
    out = []
    for name, g in df.groupby("name"):
        typ = default_type or types_of.get(str(name))
        if typ is None:
            continue
        g = g.sort_values("b")
        prev_b, t_start, dur_h = None, 0.0, 0.0
        for b, f in zip(g["b"].to_numpy(), g["value"].to_numpy(), strict=True):
            if f > 0 and prev_b is not None and b == prev_b + 1:
                dur_h += f * dur[b]
            elif f > 0:
                if prev_b is not None:
                    out.append(CalendarEvent(str(name), typ, t_start, dur_h))
                t_start, dur_h = float(starts[b]), f * dur[b]
            else:
                if prev_b is not None:
                    out.append(CalendarEvent(str(name), typ, t_start, dur_h))
                prev_b = None
                continue
            prev_b = int(b)
        if prev_b is not None:
            out.append(CalendarEvent(str(name), typ, t_start, dur_h))
    return out


# ── usage per period ────────────────────────────────────────────────────


def _element_scalar(el: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        v = el.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def _unit_elements(run: Run) -> dict[str, dict[str, Any]]:
    out = {}
    for cls in ("Generator", "Battery"):
        for el in run.elements(cls):
            if "name" in el:
                out[str(el["name"])] = {**el, "_class": cls}
    return out


def _weighted_hours(
    run: Run,
    params: Mapping[str, WearParams],
    limits: Mapping[str, tuple[float, float]],
) -> pd.Series:
    """``Σ on·h·prob·w(p)`` by ``(name, k)``."""
    gen = run.cells("Generator", "generation_sol")
    on = run.cells("Generator", "usage_on_sol")
    keys = ["name", "s", "k", "b"]
    if on.empty:
        if gen.empty:
            return pd.Series(dtype=float)
        df = gen.assign(on=(gen["value"].to_numpy() > ON_EPS).astype(float))
        df = df.rename(columns={"value": "p"})
    else:
        df = on.rename(columns={"value": "on"}).merge(
            gen[[*keys, "value"]].rename(columns={"value": "p"}), on=keys, how="left"
        )
        df["p"] = df["p"].fillna(0.0)
    parts = []
    for name, g in df.groupby("name"):
        p = params.get(str(name))
        if p is None:
            continue
        pmin, pmax = limits.get(str(name), (0.0, 0.0))
        w = p.load_weight(g["p"].to_numpy(dtype=float), pmin, pmax)
        parts.append(g.assign(v=g["on"].to_numpy() * g["weight"].to_numpy() * w))
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts).groupby(["name", "k"])["v"].sum()


def _dod_factor(
    run: Run, params: Mapping[str, WearParams]
) -> dict[tuple[str, int], float]:
    """``E_s[(DoD / dod_ref)^β]`` by ``(name, k)`` for batteries with β > 0."""
    need = {n for n, p in params.items() if p.dod_exponent > 0}
    if not need:
        return {}
    df = run.cells("Battery", "energy_sol")
    if df.empty:
        return {}
    caps = {}
    for el in run.elements("Battery"):
        cap = _element_scalar(el, "energy_capacity", "capacity", "emax")
        if "name" in el and cap:
            caps[str(el["name"])] = cap
    probs = np.asarray(run.weights, dtype=float)
    out: dict[tuple[str, int], float] = {}
    for (name, s, k), g in df.groupby(["name", "s", "k"]):
        if name not in need or name not in caps:
            continue
        p = params[str(name)]
        dod = (g["value"].max() - g["value"].min()) / caps[str(name)]
        key = (str(name), int(k))
        out[key] = out.get(key, 0.0) + probs[int(s)] * (max(dod, 0.0) / p.dod_ref) ** (
            p.dod_exponent
        )
    return out


def _bus_prices(run: Run) -> dict[tuple[str, int], float]:
    """Expected hour-weighted bus marginal cost by ``(bus, k)``."""
    df = run.cells("Bus", "balance_dual")
    if df.empty:
        return {}
    df = df.assign(vw=df["value"] * df["weight"])
    g = df.groupby(["name", "k"])[["vw", "weight"]].sum()
    return {
        (str(n), int(k)): (float(r.vw / r.weight) if r.weight > 0 else 0.0)
        for (n, k), r in g.iterrows()
    }


def _cell(use: pd.DataFrame, name: str, k: int, col: str) -> float:
    """Value of a usage-table column at ``(name, k)``, 0 when absent."""
    if not use.empty and (name, k) in use.index:
        return float(use.loc[(name, k), col])
    return 0.0


@dataclass
class UnitUsage:
    """Counter periods and totals of one unit in one run."""

    name: str
    technology: str
    periods: list[Period]
    eoh: float
    cycles: float
    starts: float


def unit_periods(
    run: Run,
    params: WearParamSet,
    *,
    hours_per_year: float = HOURS_PER_YEAR,
) -> dict[str, UnitUsage]:
    """Counter periods (one per stage) of every unit with parameters."""
    usage = usage_table(run, hours_per_year=hours_per_year)
    reserve = reserve_table(run, hours_per_year=hours_per_year)
    elements = _unit_elements(run)
    techs = {
        str(n): params.technology_of(str(n), str(t))
        for n, t in zip(usage["name"], usage["technology"], strict=True)
    }
    for name, el in elements.items():
        techs.setdefault(
            name,
            params.technology_of(
                name,
                str(
                    el.get("type")
                    or ("battery" if el["_class"] == "Battery" else "other")
                ),
            ),
        )
    unit_params = {
        n: p for n in techs if (p := params.for_unit(n, techs[n])) is not None
    }
    limits: dict[str, tuple[float, float]] = {}
    for n, p in unit_params.items():
        el = elements.get(n, {})
        pmin = p.pmin if p.pmin is not None else _element_scalar(el, "pmin")
        pmax = p.pmax if p.pmax is not None else _element_scalar(el, "pmax", "capacity")
        limits[n] = (pmin or 0.0, pmax or 0.0)
    whours = _weighted_hours(run, unit_params, limits)
    dod = _dod_factor(run, unit_params)
    prices = _bus_prices(run)
    buses = run.names("Bus")
    use = usage.set_index(["name", "k"]) if not usage.empty else usage
    res = (
        reserve.groupby(["name", "service", "direction", "k"])["mwh"].sum()
        if not reserve.empty
        else pd.Series(dtype=float)
    )
    spans = stage_spans(run)
    out: dict[str, UnitUsage] = {}
    for n, p in unit_params.items():
        el = elements.get(n, {})
        is_bess = el.get("_class") == "Battery"
        bus = _ref_name(el.get("bus"), buses)
        pmin, pmax = limits[n]
        periods = []
        for k, (t0, hours) in enumerate(spans):

            def u(col: str, k: int = k, n: str = n) -> float:
                return _cell(use, n, k, col)

            reserve_eoh = 0.0
            if not res.empty and n in res.index.get_level_values(0):
                for (rn, svc, rd, rk), mwh in res.items():
                    alpha = p.alpha(str(svc), str(rd))
                    if rn == n and rk == k and alpha > 0:
                        if pmax <= 0:
                            raise ValueError(f"{n}: alpha_reserve needs pmax > 0")
                        reserve_eoh += alpha * float(mwh) / pmax
            starts = u("starts")
            eoh = 0.0
            cycles = 0.0
            if is_bess:
                cycles = u("efc") * dod.get((n, k), 1.0)
            else:
                eoh = (
                    float(whours.get((n, k), 0.0))
                    + p.eoh_per_pmin_hour * u("pmin_hours")
                    + p.kd * starts
                    + p.kf * p.trips_per_year * hours / hours_per_year
                    + reserve_eoh
                )
            price = p.replacement_price
            if price is None:
                price = prices.get((bus, k), 0.0) if bus is not None else 0.0
            energy = u("discharge_mwh") if is_bess else u("energy_mwh")
            periods.append(Period(t0, hours, eoh, starts, cycles, energy, price))
        out[n] = UnitUsage(
            n,
            techs[n],
            periods,
            eoh=sum(x.eoh for x in periods),
            cycles=sum(x.cycles for x in periods),
            starts=sum(x.starts for x in periods),
        )
    return out


# ── report ──────────────────────────────────────────────────────────────

UNIT_COLUMNS = [
    "name",
    "technology",
    "eoh_a",
    "eoh_b",
    "delta_eoh",
    "cycles_a",
    "cycles_b",
    "delta_cycles",
    "rate_delta",
    "event_a",
    "event_b",
    "event_delta",
    "extra_delta",
    "advance_delta",
    "replacement_delta",
    "terminal_delta",
]

TECH_COLUMNS = [
    "technology",
    "units",
    "rate_gross",
    "rate_savings",
    "rate_net",
    "event_gross",
    "event_savings",
    "event_net",
]

THETA_COLUMNS = [
    "technology",
    "service",
    "direction",
    "reserve_mwh_b",
    "delta_reserve_mwh",
    "rate_cost",
    "event_cost",
    "theta_rate",
    "theta_event",
]

EVENT_COLUMNS = [
    "run",
    "unit",
    "technology",
    "type",
    "source",
    "status",
    "time_h",
    "time_years",
    "ratio",
    "life_wasted",
    "advance_years",
    "cost_pv",
    "replacement_pv",
]


@dataclass
class MaintenanceReport:
    """Incremental maintenance cost tables (B − A)."""

    units: pd.DataFrame
    technologies: pd.DataFrame
    theta: pd.DataFrame
    events: pd.DataFrame

    def tables(self) -> dict[str, pd.DataFrame]:
        """The four tables by name."""
        return {
            "units": self.units,
            "technologies": self.technologies,
            "theta": self.theta,
            "events": self.events,
        }

    def write_csv(self, directory: str | Path) -> list[Path]:
        """Write ``<table>.csv`` for the four tables."""
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        paths = []
        for name, df in self.tables().items():
            path = out / f"maintenance_{name}.csv"
            df.to_csv(path, index=False)
            paths.append(path)
        return paths

    def write_json(self, path: str | Path) -> Path:
        """Write the tables as JSON records."""
        data = {
            name: [
                dict(r)
                for r in df.astype(object).where(pd.notna(df), None).to_dict("records")
            ]
            for name, df in self.tables().items()
        }
        p = Path(path)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return p


def _simulate(
    usage: UnitUsage,
    p: WearParams,
    calendar: Sequence[CalendarEvent],
    rate: float,
    hours_per_year: float,
) -> CounterResult:
    sim = CounterSim(
        usage.name,
        p.types,
        calendar=[e for e in calendar if e.unit == usage.name],
        initial=p.initial,
        reset_fraction=p.reset_fraction,
        discount_rate=rate,
        hours_per_year=hours_per_year,
    )
    return sim.run(usage.periods)


def _rate_cost(p: WearParams, eoh: float, cycles: float) -> float:
    per_eoh = sum(t.cost / t.interval_eoh for t in p.types if t.interval_eoh)
    per_cyc = sum(t.cost / t.interval_cycles for t in p.types if t.interval_cycles)
    return eoh * per_eoh + cycles * per_cyc


def _event_rows(
    label: str, tech: str, res: CounterResult, hpy: float
) -> list[dict[str, Any]]:
    return [
        {
            "run": label,
            "unit": e.unit,
            "technology": tech,
            "type": e.type,
            "source": e.source,
            "status": e.status,
            "time_h": e.time,
            "time_years": e.time / hpy,
            "ratio": e.ratio,
            "life_wasted": e.life_wasted,
            "advance_years": e.advance_years,
            "cost_pv": e.cost_pv,
            "replacement_pv": e.replacement_pv,
        }
        for e in res.events
    ]


def incremental_maintenance(
    base: Run,
    sscc: Run,
    params: WearParamSet,
    *,
    calendar: Sequence[CalendarEvent] = (),
    hours_per_year: float = HOURS_PER_YEAR,
) -> MaintenanceReport:
    """Incremental maintenance cost and θ of ``sscc`` (B) over ``base`` (A)."""
    ua = unit_periods(base, params, hours_per_year=hours_per_year)
    ub = unit_periods(sscc, params, hours_per_year=hours_per_year)
    rate = params.discount_rate
    rows, events = [], []
    for name in sorted(set(ua) | set(ub)):
        a, b = ua.get(name), ub.get(name)
        ref = b or a
        assert ref is not None
        p = params.for_unit(name, ref.technology)
        if p is None or not p.types:
            continue
        empty = UnitUsage(name, ref.technology, [], 0.0, 0.0, 0.0)
        a, b = a or empty, b or empty
        ra = _simulate(a, p, calendar, rate, hours_per_year)
        rb = _simulate(b, p, calendar, rate, hours_per_year)
        events += _event_rows(base.label or "A", ref.technology, ra, hours_per_year)
        events += _event_rows(sscc.label or "B", ref.technology, rb, hours_per_year)
        rows.append(
            {
                "name": name,
                "technology": ref.technology,
                "eoh_a": a.eoh,
                "eoh_b": b.eoh,
                "delta_eoh": b.eoh - a.eoh,
                "cycles_a": a.cycles,
                "cycles_b": b.cycles,
                "delta_cycles": b.cycles - a.cycles,
                "rate_delta": _rate_cost(p, b.eoh - a.eoh, b.cycles - a.cycles),
                "event_a": ra.total_pv,
                "event_b": rb.total_pv,
                "event_delta": rb.total_pv - ra.total_pv,
                "extra_delta": rb.extra_pv - ra.extra_pv,
                "advance_delta": rb.advance_pv - ra.advance_pv,
                "replacement_delta": rb.replacement_pv - ra.replacement_pv,
                "terminal_delta": rb.terminal_pv - ra.terminal_pv,
            }
        )
    units = pd.DataFrame(rows, columns=UNIT_COLUMNS)
    techs = _tech_table(units)
    theta = _theta(techs, base, sscc, params, hours_per_year)
    return MaintenanceReport(
        units, techs, theta, pd.DataFrame(events, columns=EVENT_COLUMNS)
    )


def _tech_table(units: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for tech, g in units.groupby("technology"):
        row: dict[str, Any] = {"technology": tech, "units": len(g)}
        for m in ("rate", "event"):
            d = g[f"{m}_delta"].to_numpy(dtype=float)
            row[f"{m}_gross"] = float(d[d > 0].sum())
            row[f"{m}_savings"] = float(d[d < 0].sum())
            row[f"{m}_net"] = float(d.sum())
        rows.append(row)
    return pd.DataFrame(rows, columns=TECH_COLUMNS)


def _theta(
    techs: pd.DataFrame,
    base: Run,
    sscc: Run,
    params: WearParamSet,
    hours_per_year: float,
) -> pd.DataFrame:
    def by_service(run: Run) -> pd.Series:
        r = reserve_table(run, hours_per_year=hours_per_year)
        if r.empty:
            return pd.Series(dtype=float)
        r = r.assign(
            technology=[
                params.technology_of(str(n), str(t))
                for n, t in zip(r["name"], r["technology"], strict=True)
            ]
        )
        return r.groupby(["technology", "service", "direction"])["mwh"].sum()

    rsa, rsb = by_service(base), by_service(sscc)
    if rsa.empty:
        dr = rsb
    elif rsb.empty:
        dr = -rsa
    else:
        dr = rsb.sub(rsa, fill_value=0.0)
    net = {
        m: dict(zip(techs["technology"], techs[f"{m}_net"], strict=True))
        for m in ("rate", "event")
    }
    rows = []
    for tech in sorted({t for t, _, _ in dr.index}):
        prods = [(s, d) for t, s, d in dr.index if t == tech]
        w = {sd: params.service_weight(*sd) for sd in prods}
        denom = sum(w[sd] * max(float(dr[(tech, sd[0], sd[1])]), 0.0) for sd in prods)
        for s, d in prods:
            d_res = float(dr[(tech, s, d)])
            share = w[(s, d)] * max(d_res, 0.0) / denom if denom > 0 else np.nan
            row: dict[str, Any] = {
                "technology": tech,
                "service": s,
                "direction": d,
                "reserve_mwh_b": float(rsb.get((tech, s, d), 0.0)),
                "delta_reserve_mwh": d_res,
            }
            for m in ("rate", "event"):
                cost = net[m].get(tech, 0.0) * share if denom > 0 else np.nan
                row[f"{m}_cost"] = cost
                row[f"theta_{m}"] = cost / d_res if d_res > 0 else np.nan
            rows.append(row)
    return pd.DataFrame(rows, columns=THETA_COLUMNS)


def default_types(
    params: WearParamSet, technologies: Mapping[str, str]
) -> dict[str, str]:
    """Highest maintenance type of every unit (unit -> technology map)."""
    out = {}
    for n, tech in technologies.items():
        p = params.for_unit(n, tech)
        if p is not None and p.types:
            out[n] = p.types[-1].name
    return out


__all__ = [
    "DISCOUNT_RATE",
    "MaintenanceReport",
    "UnitUsage",
    "WearParamSet",
    "WearParams",
    "calendar_from_outages",
    "default_types",
    "incremental_maintenance",
    "load_wear_params",
    "read_calendar_csv",
    "read_initial_csv",
    "unit_periods",
]
