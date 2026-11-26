# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Read the tcheby SQLite test databases (read-only).

The tcheby research code (the Chebyshev short-term scheduler of Matus et al.,
``tests/test_dbs/*.db``) keeps one *system* database (buses, generators,
lines, hydro network, commitment, ...) and one *simulation* database
(stages, scenes, scenarios and per-scene inflow schedules).  This module
loads both into plain dataclasses; :mod:`tcheby2gtopt.convert` maps them
to a gtopt planning.

Schedules.  A tcheby schedule table ``<Class>.<field>_sched`` holds
``(id, date | hour, value)`` rows; the series is a zero-order hold
(``elecdb::Schedule<T, Order = 0>``): a value holds from its key until
the next key, and the element's own column is the value before the
first key.  Date keys are converted to hours from the study time zero.

The databases are opened with ``mode=ro`` URIs and never modified.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


@dataclass
class StepSchedule:
    """Zero-order-hold series: ``value(t) = values[i]`` for the last key
    ``hours[i] <= t``; ``default`` before the first key."""

    default: float
    hours: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)

    def at(self, t: np.ndarray | float) -> np.ndarray:
        """Values at the times ``t`` [h from time zero]."""
        tt = np.atleast_1d(np.asarray(t, dtype=float))
        if not self.hours:
            return np.full(tt.shape, float(self.default))
        keys = np.asarray(self.hours, dtype=float)
        vals = np.asarray(self.values, dtype=float)
        idx = np.searchsorted(keys, tt, side="right") - 1
        out = np.where(idx >= 0, vals[np.clip(idx, 0, None)], self.default)
        return out.astype(float)

    def hourly(self, start: float, hours: int) -> np.ndarray:
        """Hourly series on ``[start, start + hours)`` (value at hour start)."""
        return self.at(start + np.arange(hours, dtype=float))

    @property
    def is_constant(self) -> bool:
        return not self.hours


def parse_date(text: str) -> datetime:
    """Parse ``YYYY-M-D[ HH:MM[:SS]]`` (the tcheby key formats)."""
    s = str(text).strip()
    day, _, clock = s.partition(" ")
    y, m, d = (int(p) for p in day.split("-"))
    hh = mm = ss = 0
    if clock:
        parts = [int(float(p)) for p in clock.split(":")]
        parts += [0] * (3 - len(parts))
        hh, mm, ss = parts[:3]
    return datetime(y, m, d, hh, mm, ss)


def _f(v: Any, default: float) -> float:
    """Float of a DB cell; ``None`` / ``''`` -> ``default``."""
    n = _num(v)
    return default if n is None else n


def _num(v: Any, default: float | None = None) -> float | None:
    """Float of a DB cell; ``None`` / ``''`` -> ``default``."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# System elements
# ---------------------------------------------------------------------------


@dataclass
class Bus:
    id: int
    name: str
    active: bool = True


@dataclass
class Generator:  # pylint: disable=too-many-instance-attributes
    id: int
    name: str
    bus_id: int
    capacity: float
    pmin: StepSchedule
    pmax: StepSchedule
    cvar: StepSchedule
    active: bool = True


@dataclass
class Demand:
    id: int
    name: str
    bus_id: int
    load: StepSchedule
    active: bool = True


@dataclass
class Line:  # pylint: disable=too-many-instance-attributes
    id: int
    name: str
    bus_a_id: int
    bus_b_id: int
    capacity: float
    tmin: StepSchedule
    tmax: StepSchedule
    r: float | None = None
    x: float | None = None
    voltage: float | None = None
    active: bool = True


@dataclass
class Junction:
    id: int
    name: str
    active: bool = True


@dataclass
class Waterway:
    id: int
    name: str
    junc_a_id: int
    junc_b_id: int | None
    fmin: StepSchedule
    fmax: StepSchedule
    delay: float = 0.0
    active: bool = True


@dataclass
class Reservoir:  # pylint: disable=too-many-instance-attributes
    id: int
    name: str
    junc_id: int
    vini: float
    vmin: StepSchedule
    vmax: StepSchedule
    vfin: float | None = None
    active: bool = True


@dataclass
class Inflow:
    id: int
    name: str
    junc_id: int
    flow: StepSchedule
    active: bool = True


@dataclass
class Turbine:
    id: int
    name: str
    gen_id: int
    wway_id: int
    efficiency: float


@dataclass
class Commitment:  # pylint: disable=too-many-instance-attributes
    id: int
    name: str
    gen_id: int
    time_resolution: float | None = None
    down_time: float | None = None
    up_time: float | None = None
    last_state: float | None = None
    last_state_period: float | None = None
    start_cost: float | None = None
    stop_cost: float | None = None
    start_ramp: float | None = None
    stop_ramp: float | None = None


@dataclass
class Passthrough:  # pylint: disable=too-many-instance-attributes
    id: int
    name: str
    bus_id: int
    capacity: float
    efficiency: float
    flow: float
    cvar: float = 0.0
    active: bool = True


@dataclass
class Overspill:
    id: int
    name: str
    reserv_id: int
    wway_id: int | None
    spillcost: float


@dataclass
class TchebySystem:  # pylint: disable=too-many-instance-attributes
    """One tcheby system database."""

    name: str
    fail_cost: float = 500.0
    buses: list[Bus] = field(default_factory=list)
    generators: list[Generator] = field(default_factory=list)
    demands: list[Demand] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    junctions: list[Junction] = field(default_factory=list)
    waterways: list[Waterway] = field(default_factory=list)
    reservoirs: list[Reservoir] = field(default_factory=list)
    inflows: list[Inflow] = field(default_factory=list)
    turbines: list[Turbine] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)
    passthroughs: list[Passthrough] = field(default_factory=list)
    overspills: list[Overspill] = field(default_factory=list)
    #: element tables / features present in the DB but not mapped
    dropped: list[str] = field(default_factory=list)


@dataclass
class TchebySimulation:
    """One tcheby simulation database (first active scenario)."""

    time_zero: datetime
    #: stage durations [h]
    stages: list[float]
    #: inflow name -> per-stage value (the scene of that stage)
    inflows: dict[str, list[float]] = field(default_factory=dict)
    #: number of Chebyshev coefficients of the study (``Study.ncoeffs``)
    ncoeffs: int | None = None
    discount_rate: float = 0.0


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def connect_ro(path: str | Path) -> sqlite3.Connection:
    """Open an SQLite file read-only (URI ``mode=ro``)."""
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(p)
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def _tables(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {r[0] for r in rows}


def _rows(con: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if table not in _tables(con):
        return []
    # table comes from sqlite_master (_tables), not from user input
    cur = con.execute(f'SELECT * FROM "{table}"')  # noqa: S608
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _schedule(
    con: sqlite3.Connection,
    cls: str,
    fld: str,
    eid: int,
    default: float,
    time_zero: datetime,
) -> StepSchedule:
    """Step schedule of ``<cls>.<fld>_sched`` for element ``eid``."""
    sched = StepSchedule(default=default)
    rows = [r for r in _rows(con, f"{cls}.{fld}_sched") if r.get("id") == eid]
    keyed: list[tuple[float, float]] = []
    for r in rows:
        val = _num(r.get("value"))
        if val is None:
            continue
        if "hour" in r:
            hour = _f(r["hour"], 0.0)
        else:
            hour = (parse_date(r["date"]) - time_zero).total_seconds() / 3600.0
        keyed.append((hour, val))
    keyed.sort()
    sched.hours = [k for k, _ in keyed]
    sched.values = [v for _, v in keyed]
    return sched


def _active(v: Any) -> bool:
    """tcheby ``active``: NULL means active."""
    num = _num(v, 1.0)
    return num is None or num != 0.0


# Tables whose content the converter maps; anything else with rows is
# reported in ``TchebySystem.dropped``.
_MAPPED = {
    "Bus",
    "Generator",
    "Demand",
    "Line",
    "Junction",
    "Waterway",
    "Reservoir",
    "Inflow",
    "HidroTurbine",
    "Commitment",
    "Passthrough",
    "Overspill",
    "PowerSystem",
    "Class",
    "sqlite_sequence",
}
_MAPPED_SCHED = {
    "Generator.pmin_sched",
    "Generator.pmax_sched",
    "Generator.cvar_sched",
    "Demand.load_sched",
    "Line.tmin_sched",
    "Line.tmax_sched",
    "Waterway.fmin_sched",
    "Waterway.fmax_sched",
    "Reservoir.vmin_sched",
    "Reservoir.vmax_sched",
    "Inflow.flow_sched",
}


def read_system(  # noqa: C901
    path: str | Path, time_zero: datetime, name: str | None = None
) -> TchebySystem:
    """Load a tcheby system DB.  ``time_zero`` anchors date-keyed schedules."""
    con = connect_ro(path)
    try:
        return _read_system(con, time_zero, name or Path(path).stem)
    finally:
        con.close()


def _read_system(  # pylint: disable=too-many-locals
    con: sqlite3.Connection, tz: datetime, name: str
) -> TchebySystem:
    ps = _rows(con, "PowerSystem")
    sysm = TchebySystem(
        name=name,
        fail_cost=float(_f(ps[0].get("fail_cost"), 500.0)) if ps else 500.0,
    )

    def sch(cls: str, fld: str, eid: int, default: float) -> StepSchedule:
        return _schedule(con, cls, fld, eid, default, tz)

    for r in _rows(con, "Bus"):
        sysm.buses.append(
            Bus(int(r["id"]), str(r["name"]).strip(), _active(r["active"]))
        )

    for r in _rows(con, "Generator"):
        cap = _f(r.get("capacity"), 0.0)
        pmax0 = _f(r.get("pmax"), cap)
        pmin0 = _f(r.get("pmin"), 0.0)
        cvar0 = _f(r.get("cvar"), 0.0)
        gid = int(r["id"])
        sysm.generators.append(
            Generator(
                id=gid,
                name=str(r["name"]).strip(),
                bus_id=int(r["bus_id"]),
                capacity=cap,
                pmin=sch("Generator", "pmin", gid, pmin0),
                pmax=sch("Generator", "pmax", gid, pmax0),
                cvar=sch("Generator", "cvar", gid, cvar0),
                active=_active(r.get("active")),
            )
        )

    for r in _rows(con, "Demand"):
        did = int(r["id"])
        sysm.demands.append(
            Demand(
                id=did,
                name=str(r["name"]).strip(),
                bus_id=int(r["bus_id"]),
                load=sch("Demand", "load", did, _f(r.get("load"), 0.0)),
                active=_active(r.get("active")),
            )
        )

    for r in _rows(con, "Line"):
        lid = int(r["id"])
        cap = _f(r.get("capacity"), 0.0)
        tmax0 = _f(r.get("tmax"), cap)
        tmin0 = _f(r.get("tmin"), -cap)
        sysm.lines.append(
            Line(
                id=lid,
                name=str(r["name"]).strip(),
                bus_a_id=int(r["bus_a_id"]),
                bus_b_id=int(r["bus_b_id"]),
                capacity=cap,
                tmin=sch("Line", "tmin", lid, tmin0),
                tmax=sch("Line", "tmax", lid, tmax0),
                r=_num(r.get("r")),
                x=_num(r.get("x")),
                voltage=_num(r.get("voltage")),
                active=_active(r.get("active")),
            )
        )

    for r in _rows(con, "Junction"):
        sysm.junctions.append(
            Junction(int(r["id"]), str(r["name"]).strip(), _active(r.get("active")))
        )

    for r in _rows(con, "Waterway"):
        wid = int(r["id"])
        jb = _num(r.get("junc_b_id"))
        sysm.waterways.append(
            Waterway(
                id=wid,
                name=str(r["name"]).strip(),
                junc_a_id=int(r["junc_a_id"]),
                junc_b_id=None if jb is None else int(jb),
                fmin=sch("Waterway", "fmin", wid, _f(r.get("fmin"), 0.0)),
                fmax=sch("Waterway", "fmax", wid, _f(r.get("fmax"), 1e4)),
                delay=_f(r.get("delay"), 0.0),
                active=_active(r.get("active")),
            )
        )

    for r in _rows(con, "Reservoir"):
        rid = int(r["id"])
        sysm.reservoirs.append(
            Reservoir(
                id=rid,
                name=str(r["name"]).strip(),
                junc_id=int(r["junc_id"]),
                vini=_f(r.get("vini"), 0.0),
                vmin=sch("Reservoir", "vmin", rid, _f(r.get("vmin"), 0.0)),
                vmax=sch("Reservoir", "vmax", rid, _f(r.get("vmax"), 0.0)),
                vfin=_num(r.get("vfin")),
                active=_active(r.get("active")),
            )
        )

    for r in _rows(con, "Inflow"):
        iid = int(r["id"])
        sysm.inflows.append(
            Inflow(
                id=iid,
                name=str(r["name"]).strip(),
                junc_id=int(r["junc_id"]),
                flow=sch("Inflow", "flow", iid, _f(r.get("flow"), 0.0)),
                active=_active(r.get("active")),
            )
        )

    for r in _rows(con, "HidroTurbine"):
        sysm.turbines.append(
            Turbine(
                id=int(r["id"]),
                name=str(r["name"]).strip(),
                gen_id=int(r["gen_id"]),
                wway_id=int(r["wway_id"]),
                efficiency=_f(r.get("efficiency"), 1.0),
            )
        )

    for r in _rows(con, "Commitment"):
        sysm.commitments.append(
            Commitment(
                id=int(r["id"]),
                name=str(r["name"]).strip(),
                gen_id=int(r["gen_id"]),
                time_resolution=_num(r.get("time_resolution")),
                down_time=_num(r.get("down_time")),
                up_time=_num(r.get("up_time")),
                last_state=_num(r.get("last_state")),
                last_state_period=_num(r.get("last_state_period")),
                start_cost=_num(r.get("start_cost")),
                stop_cost=_num(r.get("stop_cost")),
                start_ramp=_num(r.get("start_ramp")),
                stop_ramp=_num(r.get("stop_ramp")),
            )
        )

    for r in _rows(con, "Passthrough"):
        sysm.passthroughs.append(
            Passthrough(
                id=int(r["id"]),
                name=str(r["name"]).strip(),
                bus_id=int(r["bus_id"]),
                capacity=_f(r.get("capacity"), 0.0),
                efficiency=_f(r.get("efficiency"), 1.0),
                flow=_f(r.get("flow"), 0.0),
                cvar=_f(r.get("cvar"), 0.0),
                active=_active(r.get("active")),
            )
        )

    for r in _rows(con, "Overspill"):
        wb = _num(r.get("wway_id"))
        sysm.overspills.append(
            Overspill(
                id=int(r["id"]),
                name=str(r["name"]).strip(),
                reserv_id=int(r["reserv_id"]),
                wway_id=None if wb is None else int(wb),
                spillcost=_f(r.get("spillcost"), 0.0),
            )
        )

    sysm.dropped = _dropped_tables(con)
    return sysm


def _dropped_tables(con: sqlite3.Connection) -> list[str]:
    out = []
    for t in sorted(_tables(con)):
        if t in _MAPPED or t in _MAPPED_SCHED:
            continue
        # t comes from sqlite_master (_tables), not from user input
        n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]  # noqa: S608
        if n:
            out.append(f"{t} ({n} rows)")
    return out


def read_simulation(path: str | Path) -> TchebySimulation:
    """Load a tcheby simulation DB: stages and the per-stage inflow of the
    first active scenario (``Scenario.scenes``: stage -> scene, then
    ``Scene.field_scheds`` -> ``ScheduleH`` default / first value)."""
    con = connect_ro(path)
    try:
        study = _rows(con, "Study")
        if not study:
            raise ValueError(f"{path}: no Study row")
        tz = parse_date(study[0]["time_zero"])
        stages = sorted(_rows(con, "Stage"), key=lambda r: int(r["id"]))
        durations = [float(r["duration"]) for r in stages]
        sim = TchebySimulation(
            time_zero=tz,
            stages=durations,
            ncoeffs=int(study[0]["ncoeffs"]) if study[0].get("ncoeffs") else None,
            discount_rate=_f(study[0].get("discount_rate"), 0.0),
        )
        scenarios = _rows(con, "Scenario")
        if not scenarios:
            return sim
        scen_id = int(scenarios[0]["id"])
        stage_scene = {
            int(r["stage_id"]): int(r["scene_id"])
            for r in _rows(con, "Scenario.scenes")
            if int(r["id"]) == scen_id
        }
        fields = {int(r["id"]): r for r in _rows(con, "Field")}
        sched = {int(r["id"]): r for r in _rows(con, "ScheduleH")}
        sched_vals: dict[int, list[tuple[float, float]]] = {}
        for r in _rows(con, "ScheduleH.values"):
            sched_vals.setdefault(int(r["id"]), []).append(
                (float(r["hour"]), float(r["value"]))
            )
        links = _rows(con, "Scene.field_scheds")
        for k, st in enumerate(stages):
            scene = stage_scene.get(int(st["id"]))
            for ln in links:
                if scene is None or int(ln["id"]) != scene:
                    continue
                fld = fields.get(int(ln["field_id"]))
                if not fld or fld["field_name"] != "flow_sched":
                    continue
                sh = sched.get(int(ln["schedule_id"]))
                if sh is None:
                    continue
                vals = sorted(sched_vals.get(int(sh["id"]), []))
                value = vals[0][1] if vals else _f(sh.get("default_value"), 0.0)
                seq = sim.inflows.setdefault(
                    str(fld["element_name"]).strip(), [float("nan")] * len(stages)
                )
                seq[k] = float(value)
        return sim
    finally:
        con.close()
