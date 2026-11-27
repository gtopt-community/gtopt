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

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

    @property
    def built(self) -> bool:
        return self.new_mw > 1e-6 or self.new_mwh > 1e-6

    def energy_revenue(self) -> float:
        """Σ λ·(gen − dem)·h — injections and withdrawals at the bus price."""
        return sum(r.price * r.net * r.hours for r in self.rows)

    def variable_cost(self) -> float:
        """Σ (gcost·gen + ccost·dem)·h (fuel + VOM as modelled in gtopt)."""
        return sum((r.gcost * r.gen + r.ccost * r.dem) * r.hours for r in self.rows)

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

    def bus_price(self, key: PKey, bus: int) -> float:
        return self.price.get((key[0], key[1], key[2], bus), 0.0)


def _get(series: SeriesMap | None, key: PKey, uid: int) -> float:
    if series is None:
        return 0.0
    return series.get((key[0], key[1], key[2], uid), 0.0)


def _by_name(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((it for it in items if it.get("name") == name), None)


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

    def series(self, stem: str) -> SeriesMap | None:
        if stem not in self._cache:
            self._cache[stem] = load_series(self.rdir, stem)
        return self._cache[stem]

    def gcost(self, gen: dict[str, Any], p: Period) -> float:
        uid = int(gen["uid"])
        srmc = self.series("Generator/srmc_sol")
        key = (p.scenario, p.stage, p.block, uid)
        if srmc is not None and key in srmc:
            return srmc[key]
        return (
            sched_value(
                gen.get("gcost"),
                p,
                uid,
                class_name="Generator",
                input_dir=self.case.input_dir,
            )
            or 0.0
        )


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


def load_system(
    results_dir: str | Path,
    *,
    planning: str | Path | None = None,
    case_dir: str | Path | None = None,
) -> SystemData:
    """Load a solved case into :class:`SystemData` (one representative year)."""
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
    periods = [
        (p, case.scenario_weight.get(s, 1.0))
        for s, plist in case.periods.items()
        for p in plist
    ]
    _fill_system(sysd, reader, periods, gens, dems, bat_names)
    recs = {(r.kind, r.name): r for r in rep.projects}
    for gen in gens:
        rec = recs.get(("Generator", str(gen.get("name"))))
        if rec is None:
            continue
        proj = ProjectData(
            kind="Generator",
            name=rec.name,
            uid=rec.uid,
            bus=_bus(case, gen.get("bus")),
            new_mw=rec.new,
            installed_mw=rec.installed,
            elem=gen,
            planning=rec,
            social_rate=social,
        )
        share = _share(rec)
        gsol = reader.series("Generator/generation_sol")
        for p, prob in periods:
            key = (p.scenario, p.stage, p.block)
            proj.rows.append(
                Row(
                    key=key,
                    hours=p.duration * prob * scale,
                    price=sysd.bus_price(key, proj.bus),
                    gen=_get(gsol, key, rec.uid) * share,
                    gcost=reader.gcost(gen, p),
                    avail=_profile(system, rec.name, p),
                )
            )
        sysd.projects.append(proj)
    for bat in system.get("battery_array", []) or []:
        rec = recs.get(("Battery", str(bat.get("name"))))
        if rec is None:
            continue
        proj = _battery(sysd, reader, periods, rec, bat, gens, dems, convs, social)
        sysd.projects.append(proj)
        sysd.units.append(
            UnitInfo(
                name=proj.name,
                kind="Battery",
                installed_mw=proj.new_mw,
                new_mw=proj.new_mw,
                duration_h=proj.duration_h(),
            )
        )
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


def _battery(
    sysd: SystemData,
    reader: _Reader,
    periods: list[tuple[Period, float]],
    rec: ProjectRecovery,
    bat: dict[str, Any],
    gens: list[dict[str, Any]],
    dems: list[dict[str, Any]],
    convs: list[dict[str, Any]],
    social: float,
) -> ProjectData:
    name = rec.name
    gen = _by_name(gens, f"{name}_gen") or {}
    dem = _by_name(dems, f"{name}_dem") or {}
    conv = _by_name(convs, f"{name}_conv") or {}
    power_elem = dict(conv)
    for fld in ("power_overnight_capcost", "power_annual_capcost", "wacc"):
        if fld in bat:
            power_elem.setdefault(fld, bat[fld])
    new_mw = rec.power_new if rec.power_new is not None else 0.0
    if rec.power_new is None:
        # converter not expandable: power = discharge limit of the battery
        new_mw = float(bat.get("pmax_discharge", 0.0) or 0.0) if rec.new > 0 else 0.0
    proj = ProjectData(
        kind="Battery",
        name=name,
        uid=rec.uid,
        bus=_bus(sysd.case, gen.get("bus", dem.get("bus"))),
        new_mw=new_mw,
        installed_mw=new_mw,
        new_mwh=rec.new,
        elem=bat,
        power_elem=power_elem,
        planning=rec,
        social_rate=social,
    )
    share = _share(rec)
    gsol = reader.series("Generator/generation_sol")
    lsol = reader.series("Demand/load_sol")
    for p, prob in periods:
        key = (p.scenario, p.stage, p.block)
        cc = sched_value(
            bat.get("charge_cost"),
            p,
            rec.uid,
            class_name="Battery",
            input_dir=reader.case.input_dir,
        )
        proj.rows.append(
            Row(
                key=key,
                hours=p.duration * prob * sysd.scale,
                price=sysd.bus_price(key, proj.bus),
                gen=_get(gsol, key, int(gen["uid"])) * share if gen else 0.0,
                dem=_get(lsol, key, int(dem["uid"])) * share if dem else 0.0,
                gcost=reader.gcost(gen, p) if gen else 0.0,
                ccost=cc or 0.0,
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
    """Peak demand, unit list and the dispatch table (marginal units)."""
    case = sysd.case

    def is_bat(name: str, suffix: str) -> bool:
        return name.endswith(suffix) and name[: -len(suffix)] in bat_names

    load = reader.series("Demand/load_sol")
    fail = reader.series("Demand/fail_sol")
    gsol = reader.series("Generator/generation_sol")
    capainst = reader.series("Generator/capainst_sol") or {}
    for p, _prob in periods:
        key = (p.scenario, p.stage, p.block)
        total = 0.0
        for dem in dems:
            if is_bat(str(dem.get("name")), "_dem"):
                continue
            uid = int(dem["uid"])
            total += _get(load, key, uid) + _get(fail, key, uid)
            sysd.unserved_mwh += _get(fail, key, uid) * p.duration * _prob * sysd.scale
        sysd.peak_demand_mw = max(sysd.peak_demand_mw, total)
        rows = []
        for gen in gens:
            uid = int(gen["uid"])
            q = _get(gsol, key, uid)
            rows.append(
                (
                    uid,
                    str(gen.get("name")),
                    _bus(case, gen.get("bus")),
                    q,
                    reader.gcost(gen, p),
                )
            )
        sysd.dispatch[key] = rows
        for row in rows:
            if not is_bat(row[1], "_gen"):
                sysd.max_srmc = max(sysd.max_srmc, row[4])
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
        if inst is None:
            pmax = gen.get("pmax")
            inst = cap_f or (float(pmax) if isinstance(pmax, (int, float)) else 0.0)
        sysd.units.append(
            UnitInfo(
                name=name,
                kind="Generator",
                installed_mw=inst,
                new_mw=max(0.0, inst - cap_f),
            )
        )


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
    for prov in provs:
        proj = by_gen.get(str(prov.get("generator")))
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
