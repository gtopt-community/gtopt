# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Read a solved gtopt expansion run into per-project hourly data.

Builds on ``gtopt_cost_recovery`` (planning view: LP annuity, margin and
profit of every expandable project) and ``gtopt_pricing_uplift._data``
(case / period / stream readers).  Money is turned into **one
representative year**: every period weight is
``duration × scenario probability × 8760 / horizon`` (stage discount
factors are not applied — the private engine does its own discounting
over the project life).
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from gtopt_cost_recovery import ProjectRecovery, compute_recovery
from gtopt_cost_recovery.recovery import capital_recovery_factor
from gtopt_pricing_uplift._data import (
    Case,
    Period,
    SeriesMap,
    load_case,
    load_series,
    resolve_ref,
    sched_value,
)
from gtopt_shared.field_sched import build_table_path, find_table_file

from .storage_value import StorageModel

log = logging.getLogger(__name__)

HOURS_PER_YEAR = 8760.0

#: (scenario, stage, block) of one period
PKey = tuple[int, int, int]


@dataclass
class Row:
    """One period of one project, already scaled to a representative year."""

    key: PKey
    hours: float  #: duration × probability × 8760/horizon [h/yr]
    price: float  #: bus marginal price [$/MWh]
    gen: float = 0.0  #: injection of the NEW capacity [MW]
    dem: float = 0.0  #: withdrawal (battery charge) of the NEW capacity [MW]
    gcost: float = 0.0  #: variable cost of the injection [$/MWh]
    ccost: float = 0.0  #: variable cost of the withdrawal [$/MWh]
    avail: float = 1.0  #: availability / profile factor of the unit
    dur: float = 0.0  #: raw block duration [h] (not probability-weighted)
    prob: float = 1.0  #: probability weight of the scenario
    #: unit drawing from a storage: shadow value of what it draws per MWh
    #: (Σ ϑ · draw / P) [$/MWh], used when the storage is valued at its
    #: shadow value (``storage_valuation``, :mod:`.storage_value`)
    fuel_value: float = 0.0
    #: cash fuel cost of a unit burning bought storage fuel (LNG): heat
    #: rate × the terminal's purchase price (``delivery_cost``, else
    #: ``spot_cost``) [$/MWh]; 0 for water and batteries
    fuel_cash: float = 0.0
    #: cash cost of the declared heat-rate curve above its first slope at
    #: the dispatched point [$/MWh of the unit]: fuel price × (Σₖ Δhₖ ·
    #: max(0, P − P̄ₖ) + noload_fuel · u) / P (``Reader.curve_cost``)
    curve_cost: float = 0.0

    @property
    def net(self) -> float:
        return self.gen - self.dem


@dataclass
class ProjectData:
    """One expandable project of the plan (only its NEW capacity)."""

    kind: str  #: Generator / Battery / Line
    name: str
    uid: int
    bus: int
    new_mw: float  #: new power capacity (battery: converter MW)
    installed_mw: float
    new_mwh: float = 0.0  #: battery: new energy capacity
    rows: list[Row] = field(default_factory=list)
    elem: dict[str, Any] = field(default_factory=dict)
    power_elem: dict[str, Any] = field(default_factory=dict)
    planning: ProjectRecovery | None = None
    social_rate: float = 0.0
    #: line: yearly congestion rent net of transfer cost on the new MW
    line_margin: float = 0.0
    #: reserve revenue at the reserve-requirement duals [$/yr] (new share)
    reserve_revenue: float = 0.0
    #: an existing (non-candidate) unit evaluated as a whole (--existing)
    existing: bool = False
    #: the element's ``type`` field (``solar``, ``thermal``, ...), if any
    elem_type: str | None = None
    #: storage type the unit draws from (``lng_terminal`` / ``reservoir``)
    storage_kind: str | None = None

    @property
    def built(self) -> bool:
        return self.new_mw > 1e-6 or self.new_mwh > 1e-6

    def energy_revenue(self) -> float:
        """Σ λ·(gen − dem)·h — injections and withdrawals at the bus price."""
        return sum(r.price * r.net * r.hours for r in self.rows)

    def variable_cost(self, shadow: bool = False) -> float:
        """Σ ((gcost + fuel)·gen + ccost·dem)·h — the cash variable cost.

        ``gcost`` is the unit's own cost as modelled in gtopt (fuel + VOM,
        or VOM only for a unit fed by a storage).  ``fuel`` is the cash
        price of bought storage fuel (``Row.fuel_cash``, LNG); with the
        ``shadow`` diagnostic it is the storage shadow value of what the
        unit draws instead (``Row.fuel_value``).
        """
        return sum(
            (
                (r.gcost + r.curve_cost + (r.fuel_value if shadow else r.fuel_cash))
                * r.gen
                + r.ccost * r.dem
            )
            * r.hours
            for r in self.rows
        )

    def energy_mwh(self) -> float:
        """Energy injected per year [MWh/yr]."""
        return sum(r.gen * r.hours for r in self.rows)

    def charge_mwh(self) -> float:
        return sum(r.dem * r.hours for r in self.rows)

    def captured_price(self) -> float | None:
        e = self.energy_mwh()
        return sum(r.price * r.gen * r.hours for r in self.rows) / e if e else None

    def duration_h(self) -> float | None:
        """Battery storage duration MWh / MW."""
        if self.kind != "Battery" or self.new_mw <= 1e-9:
            return None
        return self.new_mwh / self.new_mw

    def planning_rate(self, power: bool = False) -> float:
        elem = self.power_elem if power else self.elem
        wacc = elem.get("wacc", self.elem.get("wacc"))
        return float(wacc) if isinstance(wacc, (int, float)) else self.social_rate

    def planning_overnight(
        self, life: float, power: bool = False, fallback_rate: float = 0.08
    ) -> float | None:
        """Overnight $/unit from the element, or the annuity / CRF.

        ``overnight_capcost`` when the element has it, else
        ``annual_capcost / CRF(rate, life)`` with the planning rate (or
        ``fallback_rate`` when that is 0: the annuity was computed outside
        gtopt at an unknown rate); ``None`` if neither field is set.
        """
        elem = self.power_elem if power else self.elem
        prefix = "power_" if power and "power_annual_capcost" in elem else ""
        val = elem.get(f"{prefix}overnight_capcost")
        if isinstance(val, (int, float)):
            return float(val)
        ann = elem.get(f"{prefix}annual_capcost")
        if isinstance(ann, list) and ann:
            ann = ann[-1]
        if isinstance(ann, (int, float)):
            rate = self.planning_rate(power)
            k = capital_recovery_factor(rate if rate > 0 else fallback_rate, life)
            return float(ann) / k if k > 0 else None
        return None


@dataclass
class UnitInfo:
    """Any generating / storage unit of the system (for capacity pies)."""

    name: str
    kind: str
    installed_mw: float
    new_mw: float = 0.0
    duration_h: float | None = None
    uid: int = 0
    #: the element's ``type`` field (``solar``, ``hydro_ror``, ...), if any
    elem_type: str | None = None
    #: MW in service, time-weighted over the horizon (pmax schedule ∧
    #: capacity): a unit entering half-way counts half, as in the CEN's
    #: sub-period calculation; 0 when not in service in the horizon
    available_mw: float = 0.0
    #: highest MW in service in any period (the unit's size)
    max_mw: float = 0.0
    #: dispatch [MW] and availability profile factor per period
    gen: dict[PKey, float] = field(default_factory=dict)
    avail: dict[PKey, float] = field(default_factory=dict)


@dataclass
class SystemData:
    """System-wide quantities of the representative year."""

    results_dir: str
    scale: float  #: 8760 / horizon hours
    horizon_hours: float
    peak_demand_mw: float
    case: Case
    price: SeriesMap
    units: list[UnitInfo] = field(default_factory=list)
    projects: list[ProjectData] = field(default_factory=list)
    #: (scenario, stage, block, bus) -> generators [(uid, name, bus, gen, srmc)]
    dispatch: dict[PKey, list[tuple[int, str, int, float, float]]] = field(
        default_factory=dict
    )
    obj_value: float | None = None
    has_reserves: bool = False
    #: unserved energy of the representative year [MWh/yr]
    unserved_mwh: float = 0.0
    #: highest variable cost of any generator of the case [$/MWh] — the
    #: price ceiling of a market without scarcity pricing (Chile's CMg)
    max_srmc: float = 0.0
    #: highest bus price of the case [$/MWh]
    max_price: float = 0.0
    #: every period of the case with its scenario probability
    periods: list[tuple[Period, float]] = field(default_factory=list)
    #: served + unserved load per period [MW] (battery charging excluded)
    load: dict[PKey, float] = field(default_factory=dict)
    #: highest variable cost of a unit DISPATCHED in each period [$/MWh]
    #: (storage excluded) — the Chilean CMg ceiling of that period
    dispatched_srmc: dict[PKey, float] = field(default_factory=dict)
    #: stage uid -> calendar month (1–12), when the stages are months
    stage_month: dict[int, int] = field(default_factory=dict)
    #: chronological hours (year, month, day, hour 1–24, block uid) of the
    #: case's ``simulation.block_hour_map`` (empty when it has none)
    hour_map: list[tuple[int, int, int, int, int]] = field(default_factory=list)
    #: block uid -> stage uid
    block_stage: dict[int, int] = field(default_factory=dict)
    #: storages of the case and the units drawing from them
    storage: StorageModel | None = None

    def bus_price(self, key: PKey, bus: int) -> float:
        return self.price.get((key[0], key[1], key[2], bus), 0.0)

    @property
    def scenarios(self) -> list[int]:
        return sorted(self.case.scenario_weight)

    def monthly_stages(self) -> bool:
        """True when every stage maps to one calendar month."""
        stages = {p.stage for p, _w in self.periods}
        return bool(stages) and stages <= set(self.stage_month)


def _get(series: SeriesMap | None, key: PKey, uid: int) -> float:
    if series is None:
        return 0.0
    return series.get((key[0], key[1], key[2], uid), 0.0)


def _by_name(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((it for it in items if it.get("name") == name), None)


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    return float(v) if isinstance(v, (int, float)) else None


def _peak_value(v: Any) -> float | None:
    """A scalar, or the largest entry of an inline (nested) schedule."""
    if isinstance(v, list):
        vals = [x for x in (_peak_value(it) for it in v) if x is not None]
        return max(vals) if vals else None
    return _num(v)


class _TableIndex:
    """``(uid, scenario, stage, block) -> value`` over one input table.

    Only the index columns present in the table take part in the key;
    a key the table does not have falls back to ``sched_value`` (its
    relaxed matching), a uid the table does not have returns ``None``.
    """

    def __init__(self, df: pd.DataFrame) -> None:
        self.cols = [c for c in ("scenario", "stage", "block") if c in df.columns]
        self.data: dict[tuple[int, ...], float] = {}
        cols = ["uid", *self.cols, "value"]
        for row in df[cols].itertuples(index=False):
            key = tuple(int(v) for v in row[:-1])
            self.data.setdefault(key, float(row[-1]))
        self.uids = {k[0] for k in self.data}

    def key(self, p: Period, uid: int) -> tuple[int, ...]:
        by = {"scenario": p.scenario, "stage": p.stage, "block": p.block}
        return (uid, *(by[c] for c in self.cols))


def _profile(system: dict[str, Any], name: str, p: Period) -> float:
    """Inline availability profile of a generator (1 when absent).

    Nesting depth picks the axes: 3 = ``[scenario][stage][block]``,
    2 = ``[stage][block]``, 1 = ``[block]``, scalar = constant.
    """
    profiles = (system.get("generator_profile_array") or []) + (
        system.get("capacity_profile_array") or []
    )
    for prof in profiles:
        if prof.get("generator", prof.get("owner")) != name:
            continue
        value: Any = prof.get("profile")
        depth, probe = 0, value
        while isinstance(probe, list) and probe:
            depth, probe = depth + 1, probe[0]
        axes = (p.scenario - 1, p.stage_index, p.block_index)[3 - min(depth, 3) :]
        for idx in axes:
            value = value[min(max(idx, 0), len(value) - 1)]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 1.0


class _Reader:
    """Stream cache over one results directory."""

    def __init__(self, rdir: Path, case: Case) -> None:
        self.rdir = rdir
        self.case = case
        self._cache: dict[str, SeriesMap | None] = {}
        self._tables: dict[tuple[str, str], _TableIndex | None] = {}
        self._profiles: dict[str, dict[str, Any]] | None = None

    def series(self, stem: str) -> SeriesMap | None:
        if stem not in self._cache:
            self._cache[stem] = load_series(self.rdir, stem)
        return self._cache[stem]

    def _index(self, cls: str, name: str) -> _TableIndex | None:
        key = (cls, name)
        if key not in self._tables:
            path = find_table_file(build_table_path(self.case.input_dir, cls, name))
            if path is None:
                log.warning("schedule table %s/%s not found", cls, name)
                self._tables[key] = None
            else:
                df = pd.read_parquet(path) if ".parquet" in path.name else None
                if df is None or "uid" not in df.columns or "value" not in df.columns:
                    self._tables[key] = None  # wide / CSV: slow path
                else:
                    self._tables[key] = _TableIndex(df)
        return self._tables[key]

    def sched(self, value: Any, cls: str, p: Period, uid: int) -> float | None:
        """``sched_value`` with an index over file-backed tables."""
        if isinstance(value, str) and "#" not in value:
            idx = self._index(cls, value)
            if idx is not None:
                if uid not in idx.uids:
                    return None
                hit = idx.data.get(idx.key(p, uid))
                if hit is not None:
                    return hit
        return sched_value(value, p, uid, class_name=cls, input_dir=self.case.input_dir)

    def profile(self, name: str, p: Period) -> float:
        """Availability profile factor of generator ``name`` (1 when none)."""
        if self._profiles is None:
            system = self.case.planning.get("system", {})
            self._profiles = {}
            for prof in (system.get("generator_profile_array") or []) + (
                system.get("capacity_profile_array") or []
            ):
                if str(prof.get("owner_kind", "generator")) != "generator":
                    continue
                owner = prof.get("generator", prof.get("owner"))
                if owner is not None:
                    self._profiles.setdefault(str(owner), prof)
        prof = self._profiles.get(name)
        if prof is None:
            return 1.0
        value = prof.get("profile")
        if isinstance(value, str):
            v = self.sched(value, "GeneratorProfile", p, int(prof.get("uid", 0)))
            return 1.0 if v is None else v
        return _profile(self.case.planning.get("system", {}), name, p)

    def gcost(self, gen: dict[str, Any], p: Period) -> float:
        uid = int(gen["uid"])
        srmc = self.series("Generator/srmc_sol")
        key = (p.scenario, p.stage, p.block, uid)
        if srmc is not None and key in srmc:
            return srmc[key]
        return self.sched(gen.get("gcost"), "Generator", p, uid) or 0.0

    def curve_cost(self, gen: dict[str, Any], p: Period, gen_mw: float) -> float:
        """Declared heat-rate curve cost above ``srmc`` at ``gen_mw`` [$/MWh].

        gtopt's ``srmc`` is the first segment (fuel price × h₁ + gcost);
        the curve ``pmax_segments`` / ``heat_rate_segments`` adds
        ``Σₖ (hₖ₊₁ − hₖ) · max(0, P − P̄ₖ)`` and a committed unit its
        ``Commitment.noload_fuel · u`` (``Commitment/status_sol``), at the
        price of the generator's ``fuel``.  0 without a curve or fuel.
        """
        if gen_mw <= 1e-9:
            return 0.0
        uid = int(gen["uid"])
        system = self.case.planning.get("system", {})
        fuel = resolve_ref(gen.get("fuel"), system.get("fuel_array", []) or [])
        if fuel is None:
            return 0.0
        felem: dict[str, Any] = next(
            (f for f in system.get("fuel_array", []) or [] if int(f["uid"]) == fuel),
            {},
        )
        price = self.sched(felem.get("price"), "Fuel", p, fuel) or 0.0
        if price == 0.0:
            return 0.0
        burn = 0.0
        hrs = list(gen.get("heat_rate_segments") or [])
        bps = list(gen.get("pmax_segments") or [])
        if hrs and len(hrs) == len(bps):
            for k in range(1, len(hrs)):
                burn += (hrs[k] - hrs[k - 1]) * max(0.0, gen_mw - bps[k - 1])
        for cmt in system.get("commitment_array", []) or []:
            if (
                resolve_ref(cmt.get("generator"), system.get("generator_array", []))
                != uid
            ):
                continue
            noload = float(cmt.get("noload_fuel") or 0.0)
            if noload > 0.0:
                status = self.series("Commitment/status_sol")
                key = (p.scenario, p.stage, p.block, int(cmt["uid"]))
                u = status.get(key, 1.0) if status is not None else 1.0
                burn += noload * u
        return price * burn / gen_mw

    def in_service_mw(
        self,
        elem: dict[str, Any],
        periods: list[tuple[Period, float]],
        *,
        cls: str = "Generator",
        field_name: str = "pmax",
        cap: float | None = None,
    ) -> tuple[float, float]:
        """(highest, time-weighted mean) of ``min(cap, <field>)`` over the
        periods — the MW a unit has in service (0 before it enters, during
        a full outage, after it retires).  The mean weights each period by
        duration × scenario probability."""
        uid = int(elem["uid"])
        top = 0.0
        acc = tot = 0.0
        for p, w in periods:
            val = self.sched(elem.get(field_name), cls, p, uid)
            vals = [v for v in (cap, val) if v is not None]
            mw = min(vals) if vals else 0.0
            top = max(top, mw)
            acc += mw * p.duration * w
            tot += p.duration * w
        return top, (acc / tot if tot > 0 else top)


def _bus(case: Case, ref: Any) -> int:
    buses = case.planning.get("system", {}).get("bus_array", []) or []
    uid = resolve_ref(ref, buses)
    return -1 if uid is None else uid


def _share(rec: ProjectRecovery, power: bool = False) -> float:
    """Fraction of the element's output attributable to its new capacity."""
    if power:
        new = rec.power_new or 0.0
        return 1.0 if new > 0 else 0.0
    if rec.installed <= 1e-9 or rec.existing <= 1e-9:
        return 1.0
    return rec.new / rec.installed


_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        )
    )
}


def _calendar(sysd: SystemData, reader: _Reader) -> None:
    """Stage → month and the chronological hour map, when the case has them."""
    sim = sysd.case.planning.get("simulation", {}) or {}
    for p, _w in sysd.periods:
        sysd.block_stage.setdefault(p.block, p.stage)
    for s_idx, st in enumerate(sim.get("stage_array", []) or []):
        month = st.get("month")
        uid = int(st.get("uid", s_idx + 1))
        if isinstance(month, str) and month.lower() in _MONTHS:
            sysd.stage_month[uid] = _MONTHS[month.lower()]
        elif isinstance(month, int) and 1 <= month <= 12:
            sysd.stage_month[uid] = month
    bhm = sim.get("block_hour_map")
    df: pd.DataFrame | None = None
    if isinstance(bhm, str):
        path = find_table_file(str(reader.case.input_dir / bhm))
        if path is not None:
            df = pd.read_parquet(path) if ".parquet" in path.name else pd.read_csv(path)
    elif isinstance(bhm, list) and bhm and isinstance(bhm[0], Mapping):
        df = pd.DataFrame(bhm)
    if df is None or not {"month", "hour", "block"} <= set(df.columns):
        return
    if "year" not in df.columns:
        df["year"] = 2001  # a non-leap year with no holidays
    if "day" not in df.columns:
        df["day"] = 1
    known = set(sysd.block_stage)
    for row in df[["year", "month", "day", "hour", "block"]].itertuples(index=False):
        y, m, d, h, b = (int(v) for v in row)
        if b in known:
            sysd.hour_map.append((y, m, d, h, b))
    if sysd.hour_map:
        votes: dict[int, dict[int, int]] = {}
        for _y, m, _d, _h, b in sysd.hour_map:
            st = sysd.block_stage[b]
            if st in sysd.stage_month:
                continue
            votes.setdefault(st, {}).setdefault(m, 0)
            votes[st][m] += 1
        for st, by_month in votes.items():
            month, count = max(by_month.items(), key=lambda kv: kv[1])
            if count >= 0.9 * sum(by_month.values()):
                sysd.stage_month[st] = month


def load_system(
    results_dir: str | Path,
    *,
    planning: str | Path | None = None,
    case_dir: str | Path | None = None,
    existing: Iterable[str] = (),
) -> SystemData:
    """Load a solved case into :class:`SystemData` (one representative year).

    Units drawing from a storage (LNG-linked, hydro below a reservoir)
    get ``Row.fuel_cash`` (LNG: heat rate × purchase price) and
    ``Row.fuel_value`` (the storage shadow value of what they draw, for
    the opt-in ``storage_valuation = shadow`` diagnostic;
    :mod:`.storage_value`).

    ``existing``: name patterns (``fnmatch``) of non-candidate generators
    and batteries to evaluate as whole projects (all their capacity, no
    planning annuity) — e.g. to compare a plant's model revenues with
    its observed ones.
    """
    rdir = Path(results_dir)
    rep = compute_recovery(rdir, planning=planning, case_dir=case_dir, per_year=True)
    ppath = Path(planning) if planning is not None else rdir / "planning.json"
    case = load_case(ppath, Path(case_dir) if case_dir is not None else None)
    price = load_series(rdir, "Bus/balance_dual") or {}
    scale = rep.scale
    sysd = SystemData(
        results_dir=str(rdir),
        scale=scale,
        horizon_hours=rep.horizon_hours,
        peak_demand_mw=0.0,
        case=case,
        price=price,
        obj_value=rep.obj_value,
    )
    reader = _Reader(rdir, case)
    system = case.planning.get("system", {})
    social = float(
        (case.planning.get("simulation", {}) or {}).get("annual_discount_rate")
        or (case.planning.get("options", {}) or {}).get("annual_discount_rate")
        or 0.0
    )
    bat_names = {str(b.get("name")) for b in system.get("battery_array", []) or []}
    gens = system.get("generator_array", []) or []
    dems = system.get("demand_array", []) or []
    convs = system.get("converter_array", []) or []
    sysd.periods = [
        (p, case.scenario_weight.get(s, 1.0))
        for s, plist in case.periods.items()
        for p in plist
    ]
    periods = sysd.periods
    _fill_system(sysd, reader, periods, gens, dems, bat_names)
    sysd.storage = store = StorageModel(sysd, reader, system, gens)
    _calendar(sysd, reader)
    patterns = list(existing)
    recs = {(r.kind, r.name): r for r in rep.projects}
    gsol = reader.series("Generator/generation_sol")
    for gen in gens:
        name = str(gen.get("name"))
        rec = recs.get(("Generator", name))
        is_existing = rec is None and _matches(name, patterns)
        if rec is None and not is_existing:
            continue
        if rec is not None:
            uid, new_mw, installed, share = rec.uid, rec.new, rec.installed, _share(rec)
        else:
            unit = next((u for u in sysd.units if u.name == name), None)
            uid = int(gen["uid"])
            new_mw = installed = unit.max_mw if unit is not None else 0.0
            share = 1.0
        proj = ProjectData(
            kind="Generator",
            name=name,
            uid=uid,
            bus=_bus(case, gen.get("bus")),
            new_mw=new_mw,
            installed_mw=installed,
            elem=gen,
            planning=rec,
            social_rate=social,
            existing=is_existing,
            elem_type=_type_of(gen),
            storage_kind=store.kind_of(int(gen["uid"])),
        )
        for p, prob in periods:
            key = (p.scenario, p.stage, p.block)
            proj.rows.append(
                Row(
                    key=key,
                    hours=p.duration * prob * scale,
                    price=sysd.bus_price(key, proj.bus),
                    gen=_get(gsol, key, uid) * share,
                    gcost=reader.gcost(gen, p),
                    avail=reader.profile(name, p),
                    dur=p.duration,
                    prob=prob,
                    fuel_value=store.fuel_value(
                        int(gen["uid"]), p, _get(gsol, key, int(gen["uid"]))
                    ),
                    fuel_cash=store.fuel_cash(int(gen["uid"]), p),
                    curve_cost=reader.curve_cost(
                        gen, p, _get(gsol, key, int(gen["uid"]))
                    ),
                )
            )
        sysd.projects.append(proj)
    for bat in system.get("battery_array", []) or []:
        name = str(bat.get("name"))
        rec = recs.get(("Battery", name))
        if rec is None and not _matches(name, patterns):
            continue
        proj = _battery(sysd, reader, periods, rec, bat, gens, dems, convs, social)
        sysd.projects.append(proj)
    _battery_units(sysd, reader, system, gens, convs, recs)
    _reserves(sysd, reader, periods)
    for line in system.get("line_array", []) or []:
        rec = recs.get(("Line", str(line.get("name"))))
        if rec is None:
            continue
        sysd.projects.append(
            ProjectData(
                kind="Line",
                name=rec.name,
                uid=rec.uid,
                bus=_bus(case, line.get("bus_a")),
                new_mw=rec.new,
                installed_mw=rec.installed,
                elem=line,
                planning=rec,
                social_rate=social,
                line_margin=rec.margin_new,
            )
        )
    return sysd


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pat) for pat in patterns)


def _type_of(elem: Mapping[str, Any]) -> str | None:
    t = elem.get("type")
    return str(t) if isinstance(t, str) and t else None


def _battery_power(
    bat: dict[str, Any], conv: dict[str, Any] | None, rec: ProjectRecovery | None
) -> float:
    """Converter MW of a battery (new MW for a candidate, else installed)."""
    if rec is not None and rec.new > 0:
        if rec.power_new is not None:
            return rec.power_new
        return _peak_value(bat.get("pmax_discharge")) or 0.0
    for val in (
        bat.get("pmax_discharge"),
        (conv or {}).get("capacity"),
        bat.get("power_capacity"),
    ):
        mw = _peak_value(val)
        if mw is not None and mw > 0:
            return mw
    return 0.0


def _battery_energy(bat: dict[str, Any], rec: ProjectRecovery | None) -> float:
    if rec is not None and rec.new > 0:
        return rec.new
    for val in (bat.get("emax"), bat.get("capacity")):
        mwh = _num(val)
        if mwh is not None and mwh > 0:
            return mwh
    return 0.0


def _battery_units(
    sysd: SystemData,
    reader: _Reader,
    system: dict[str, Any],
    gens: list[dict[str, Any]],
    convs: list[dict[str, Any]],
    recs: Mapping[tuple[str, str], ProjectRecovery],
) -> None:
    """Every battery of the case (existing and built) in the capacity pie."""
    gsol = reader.series("Generator/generation_sol")
    for bat in system.get("battery_array", []) or []:
        name = str(bat.get("name"))
        rec = recs.get(("Battery", name))
        if rec is not None and rec.installed <= 1e-9:
            continue  # a candidate that was not built
        conv = _by_name(convs, f"{name}_conv")
        mw = _battery_power(bat, conv, rec)
        mwh = _battery_energy(bat, rec)
        if rec is not None and rec.existing > 0 and rec.new > 0:
            mwh = rec.installed  # existing + new energy
        gen = _by_name(gens, f"{name}_gen")
        avail = mw
        if rec is None and isinstance(bat.get("pmax_discharge"), (list, str)):
            # existing battery with an entry / outage schedule
            top, avail = reader.in_service_mw(
                bat, sysd.periods, cls="Battery", field_name="pmax_discharge"
            )
            mw = top or mw
        unit = UnitInfo(
            name=name,
            kind="Battery",
            installed_mw=mw,
            new_mw=mw if rec is not None and rec.new > 0 else 0.0,
            duration_h=mwh / mw if mw > 1e-9 else None,
            uid=int(bat.get("uid", 0)),
            elem_type="battery",
            available_mw=avail,
            max_mw=mw,
        )
        if gen is not None:
            for p, _w in sysd.periods:
                key = (p.scenario, p.stage, p.block)
                q = _get(gsol, key, int(gen["uid"]))
                if q:
                    unit.gen[key] = q
        sysd.units.append(unit)


def _battery(
    sysd: SystemData,
    reader: _Reader,
    periods: list[tuple[Period, float]],
    rec: ProjectRecovery | None,
    bat: dict[str, Any],
    gens: list[dict[str, Any]],
    dems: list[dict[str, Any]],
    convs: list[dict[str, Any]],
    social: float,
) -> ProjectData:
    name = str(bat.get("name"))
    gen = _by_name(gens, f"{name}_gen") or {}
    dem = _by_name(dems, f"{name}_dem") or {}
    conv = _by_name(convs, f"{name}_conv") or {}
    power_elem = dict(conv)
    for fld in ("power_overnight_capcost", "power_annual_capcost", "wacc"):
        if fld in bat:
            power_elem.setdefault(fld, bat[fld])
    if rec is not None:
        new_mw = rec.power_new if rec.power_new is not None else 0.0
        if rec.power_new is None:
            # converter not expandable: power = discharge limit of the battery
            new_mw = (
                (_peak_value(bat.get("pmax_discharge")) or 0.0) if rec.new > 0 else 0.0
            )
        new_mwh, share, uid = rec.new, _share(rec), rec.uid
    else:
        new_mw = _battery_power(bat, conv, None)
        new_mwh, share, uid = _battery_energy(bat, None), 1.0, int(bat["uid"])
    proj = ProjectData(
        kind="Battery",
        name=name,
        uid=uid,
        bus=_bus(sysd.case, gen.get("bus", dem.get("bus"))),
        new_mw=new_mw,
        installed_mw=new_mw,
        new_mwh=new_mwh,
        elem=bat,
        power_elem=power_elem,
        planning=rec,
        social_rate=social,
        existing=rec is None,
        elem_type="battery",
    )
    gsol = reader.series("Generator/generation_sol")
    lsol = reader.series("Demand/load_sol")
    for p, prob in periods:
        key = (p.scenario, p.stage, p.block)
        cc = reader.sched(bat.get("charge_cost"), "Battery", p, uid)
        proj.rows.append(
            Row(
                key=key,
                hours=p.duration * prob * sysd.scale,
                price=sysd.bus_price(key, proj.bus),
                gen=_get(gsol, key, int(gen["uid"])) * share if gen else 0.0,
                dem=_get(lsol, key, int(dem["uid"])) * share if dem else 0.0,
                gcost=reader.gcost(gen, p) if gen else 0.0,
                ccost=cc or 0.0,
                dur=p.duration,
                prob=prob,
            )
        )
    return proj


def _fill_system(
    sysd: SystemData,
    reader: _Reader,
    periods: list[tuple[Period, float]],
    gens: list[dict[str, Any]],
    dems: list[dict[str, Any]],
    bat_names: set[str],
) -> None:
    """Load, peak demand, unit list and the dispatch table (marginal units)."""
    case = sysd.case

    def is_bat(name: str, suffix: str) -> bool:
        return name.endswith(suffix) and name[: -len(suffix)] in bat_names

    load = reader.series("Demand/load_sol")
    fail = reader.series("Demand/fail_sol")
    gsol = reader.series("Generator/generation_sol")
    capainst = reader.series("Generator/capainst_sol") or {}
    real_dems = [d for d in dems if not is_bat(str(d.get("name")), "_dem")]
    bus_of = {int(g["uid"]): _bus(case, g.get("bus")) for g in gens}
    for p, _prob in periods:
        key = (p.scenario, p.stage, p.block)
        total = 0.0
        for dem in real_dems:
            uid = int(dem["uid"])
            total += _get(load, key, uid) + _get(fail, key, uid)
            sysd.unserved_mwh += _get(fail, key, uid) * p.duration * _prob * sysd.scale
        sysd.load[key] = total
        sysd.peak_demand_mw = max(sysd.peak_demand_mw, total)
        rows = []
        top = 0.0
        for gen in gens:
            uid = int(gen["uid"])
            name = str(gen.get("name"))
            q = _get(gsol, key, uid)
            c = reader.gcost(gen, p)
            rows.append((uid, name, bus_of[uid], q, c))
            if not is_bat(name, "_gen"):
                sysd.max_srmc = max(sysd.max_srmc, c)
                if q > 1e-6:
                    top = max(top, c)
        sysd.dispatch[key] = rows
        sysd.dispatched_srmc[key] = top
    sysd.max_price = max(sysd.price.values(), default=0.0)
    for gen in gens:
        name = str(gen.get("name"))
        if is_bat(name, "_gen"):
            continue
        uid = int(gen["uid"])
        inst = max(
            (v for (_s, _t, _b, u), v in capainst.items() if u == uid),
            default=None,
        )
        cap = gen.get("capacity")
        cap_f = float(cap) if isinstance(cap, (int, float)) else 0.0
        expandable = any(gen.get(k) is not None for k in ("expcap", "expmod", "capmax"))
        if inst is None:
            pmax = gen.get("pmax")
            inst = cap_f or (
                float(pmax)
                if isinstance(pmax, (int, float)) and not expandable
                else 0.0
            )
        unit = UnitInfo(
            name=name,
            kind="Generator",
            installed_mw=inst,
            new_mw=max(0.0, inst - cap_f),
            uid=uid,
            elem_type=_type_of(gen),
        )
        if (
            gen.get("pmax") is not None
            and unit.new_mw <= 0
            and (inst > 0 or not expandable)
        ):
            unit.max_mw, unit.available_mw = reader.in_service_mw(
                gen, periods, cap=cap_f if cap_f > 0 else None
            )
            if inst > 0:
                unit.max_mw = min(unit.max_mw, inst)
                unit.available_mw = min(unit.available_mw, inst)
        else:
            unit.max_mw = unit.available_mw = inst
        for p, _w in periods:
            key = (p.scenario, p.stage, p.block)
            q = _get(gsol, key, uid)
            if q:
                unit.gen[key] = q
            a = reader.profile(name, p)
            if a != 1.0:
                unit.avail[key] = a
        sysd.units.append(unit)


def _reserves(
    sysd: SystemData, reader: _Reader, periods: list[tuple[Period, float]]
) -> None:
    """Reserve revenue ``Σ h·Σ_z (|μ_up,z|·up + |μ_dn,z|·dn)`` per project.

    ``μ`` are the zone requirement duals (``ReserveZone/urequirement_dual``,
    ``drequirement_dual``), ``up`` / ``dn`` the provisions
    (``ReserveProvision/uprovision_sol``, ``dprovision_sol``) of the
    project's generator (a battery provides through ``<bat>_gen``).
    """
    system = sysd.case.planning.get("system", {})
    provs = system.get("reserve_provision_array", []) or []
    zones = system.get("reserve_zone_array", []) or []
    if not provs or not zones:
        return
    sysd.has_reserves = True
    udual = reader.series("ReserveZone/urequirement_dual")
    ddual = reader.series("ReserveZone/drequirement_dual")
    uprov = reader.series("ReserveProvision/uprovision_sol")
    dprov = reader.series("ReserveProvision/dprovision_sol")
    by_gen = {
        **{p.name: p for p in sysd.projects if p.kind == "Generator"},
        **{f"{p.name}_gen": p for p in sysd.projects if p.kind == "Battery"},
    }
    gen_names = {
        int(g["uid"]): str(g.get("name"))
        for g in system.get("generator_array", []) or []
        if "uid" in g
    }
    for prov in provs:
        ref = prov.get("generator")
        if isinstance(ref, int) and not isinstance(ref, bool):
            ref = gen_names.get(ref, ref)
        proj = by_gen.get(str(ref))
        if proj is None:
            continue
        share = _share(proj.planning) if proj.planning is not None else 1.0
        zuids = [resolve_ref(z, zones) for z in prov.get("reserve_zones", []) or []]
        puid = int(prov["uid"])
        for p, prob in periods:
            key = (p.scenario, p.stage, p.block)
            h = p.duration * prob * sysd.scale
            up, dn = _get(uprov, key, puid), _get(dprov, key, puid)
            for z in zuids:
                if z is None:
                    continue
                proj.reserve_revenue += (
                    h
                    * share
                    * (abs(_get(udual, key, z)) * up + abs(_get(ddual, key, z)) * dn)
                )
