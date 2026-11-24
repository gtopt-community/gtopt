"""Load a classic PSR **SDDP** ``.dat`` study (monthly blocks, historical series).

This is the medium-term counterpart of :mod:`sddp2gtopt.dat_loader` (which
handles the hourly NCP dispatch dialect).  It is selected when the case
ships a per-block demand file ``deme<NB><sfx>.dat`` and builds a
fully time-indexed intermediate model (:class:`SddpBlockCase`) that
:mod:`sddp2gtopt.sddp_case_writer` turns into a multi-scenario SDDP gtopt
planning (JSON + Parquet).

PSR conventions implemented here (all generic, keyed by the system
suffix read from ``sistem.dat``):

* **Existing vs future.**  Every plant/circuit record carries an
  existing (``0``) / future (``1``) flag.  A future element enters service
  at the date of its first ``MOD:``/dated modification record (the way
  committed projects are scheduled in the COES cases); a future element
  without any modification never operates.
* **Modifications.**  ``mtermi``/``mhidro``/``mgnd``/``mcirc`` records
  override non-blank fields from their date onward.
* **Monthly tables.**  ``A~NO`` × 12-month tables (optionally per block)
  give the time-varying limits: volumes, outflows, irrigation, maintenance,
  generation constraints, reserves, demand and block durations.
* **Hydrology.**  ``hinflw.dat`` holds monthly inflows per gauging station
  (``PV`` column of ``chidro``).  With an empty station topology
  (``htopol.dat`` without upstream links) the station values are the
  plant incremental inflows.  Series ``s`` replays the historical year
  ``hydro_initial_year + s - 1``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .sddp_dat_parsers import (
    Date,
    MonthlyFile,
    MonthlyTable,
    SddpCircuit,
    SddpControl,
    find_case_file,
    parse_block_profiles,
    parse_bus_load_factors,
    parse_circuit_mods,
    parse_bus_connections,
    parse_circuits,
    parse_csv_dat,
    parse_dated_fw_file,
    parse_dbus,
    parse_fuels,
    parse_fw_file,
    parse_hist_inflows,
    parse_mod_file,
    parse_monthly_file,
    parse_sddp_control,
    parse_stage_profiles,
    span_keys,
    parse_system_suffix,
    infer_kv_from_name,
    parse_volt,
    to_float,
    to_int,
)

logger = logging.getLogger(__name__)

Grid = list[list[float]]  # [stage][block]

# PSR floors circuit reactances below 0.05 % (see ``sddp.out``,
# "Reactancias de circuitos modificadas").
_MIN_X_PCT = 0.05
# Hours → hm³ for 1 m³/s.
HM3_PER_M3S_H = 0.0036


@dataclass
class StageInfo:
    """One SDDP stage (calendar month) and its block durations [h]."""

    year: int
    month: int
    durations: list[float]

    @property
    def date(self) -> Date:
        return (self.year, self.month, 1)


@dataclass
class SddpThermal:
    code: int
    name: str
    bus: int
    pmax: Grid
    pmin: Grid
    gcost: list[float]  # per stage [$/MWh]
    pmin_soft: Grid | None = None  # gmme / rgme ``>`` floors (soft)
    capacity: float = 0.0
    fuel: str = ""


@dataclass
class SddpHydro:
    code: int
    name: str
    station: int
    # PSR ``chidro`` has two downstream codes: ``TAA`` (*turbinamiento aguas
    # abajo*) receives the TURBINED water, ``VAA`` (*vertimiento aguas abajo*)
    # the SPILLED water.  Blank = the water leaves the system (sea).
    downstream: int | None  # TAA (turbined)
    spill_to: int | None  # VAA (spilled)
    active: list[bool]
    pot: list[float]  # installed MW per stage (0 = no generator)
    pmax: Grid
    fp: list[float]  # production factor per stage [MW/(m³/s)]
    qmax: list[float]  # turbine flow limit per stage [m³/s] (0 = none)
    bus: int | None = None
    vmin: float = 0.0
    vmax: float = 0.0
    eini: float = 0.0
    emax: list[float] = field(default_factory=list)  # per stage (vmxo)
    soft_emin: list[float] = field(default_factory=list)  # per stage (vmin op.)
    soft_emin_cost: float = 0.0  # $/hm³
    min_outflow: list[float] = field(default_factory=list)  # m³/s
    min_outflow_cost: float = 0.0  # $/(m³/s·h)
    max_outflow: list[float] = field(default_factory=list)  # m³/s (0 = none)
    irrigation: list[float] = field(default_factory=list)  # m³/s
    irrigation_cost: float = 0.0  # $/(m³/s·h)
    pmin_soft: Grid | None = None  # rgme ``>`` floors
    # Inflow stations of out-of-service upstream plants routed here.
    extra_stations: list[int] = field(default_factory=list)

    @property
    def has_storage(self) -> bool:
        return self.vmax > self.vmin + 1e-9


@dataclass
class SddpRenewable:
    code: int
    name: str
    bus: int
    station: str  # profile column (gndsite name)
    tech: int  # PSR TechTyp: 0 other, 1 wind, 2 solar, 3 biomass, 4 small hydro, 5 CSP
    capacity: list[float]  # MW per stage (0 = inactive)
    om_cost: float = 0.0


@dataclass
class SddpBattery:
    code: int
    name: str
    bus: int
    emin: float
    emax: float
    eini: float
    pmax: float
    eff_charge: float
    eff_discharge: float


@dataclass
class SddpLine:
    code: int
    name: str
    from_bus: int
    to_bus: int
    voltage: float
    resistance: float  # Ω
    reactance: float  # Ω
    active: list[bool]
    tmax: list[float]  # MW per stage


@dataclass
class SddpBlockCase:
    """Fully time-indexed PSR SDDP study (input to the gtopt writer)."""

    control: SddpControl
    suffix: str
    stages: list[StageInfo]
    scenarios: list[int]  # series numbers (1-based)
    hydro_years: list[int]  # first hydrology year of each scenario
    buses: dict[int, str]
    bus_kv: dict[int, float]
    lines: list[SddpLine]
    thermals: list[SddpThermal]
    hydros: list[SddpHydro]
    renewables: list[SddpRenewable]
    batteries: list[SddpBattery]
    demands: dict[int, Grid]  # bus → [stage][block] MW
    inflows: dict[int, list[list[float]]]  # station → [scenario][stage] m³/s
    profiles: dict[str, list[Grid]]  # station → [scenario][stage][block] p.u.
    skipped: list[str] = field(default_factory=list)
    # Plants never in service in the horizon (not modelled): NUM → the plant
    # their water continues to (``VAA``, else ``TAA``), so cascades through
    # them are not cut.
    hydro_bypass: dict[int, int | None] = field(default_factory=dict)

    @property
    def num_blocks(self) -> int:
        return len(self.stages[0].durations) if self.stages else 0


# ───────────────────────────── generic helpers ──────────────────────────────


def is_sddp_block_case(case_dir: str | Path) -> bool:
    """True when ``case_dir`` is a PSR SDDP block study (has ``deme*.dat``)."""
    case_dir = Path(case_dir)
    if not case_dir.is_dir():
        return False
    return any(case_dir.glob("deme*.dat*")) and any(case_dir.glob("[sS]ddp.dat*"))


def _stage_dates(ctl: SddpControl) -> list[tuple[int, int]]:
    out = []
    y, m = ctl.initial_year, max(1, ctl.initial_stage)
    for _ in range(ctl.num_stages):
        out.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _effective(
    base: dict[str, str],
    mods: list[tuple[Date, dict[str, str]]],
    when: Date,
) -> dict[str, str]:
    """Base record overridden by every modification dated ``<= when``."""
    rec = dict(base)
    for date, mod in mods:
        if date <= when:
            for k, v in mod.items():
                if v != "":
                    rec[k] = v
    return rec


def _entry_date(
    is_future: bool, mods: list[tuple[Date, dict[str, str]]]
) -> Date | None:
    """Service-entry date: ``(0,0,0)`` for existing, first mod for future."""
    if not is_future:
        return (0, 0, 0)
    dates = sorted(d for d, _ in mods)
    return dates[0] if dates else None


def _group_mods(
    mods: list[tuple[Date, dict[str, str]]], key: str
) -> dict[int, list[tuple[Date, dict[str, str]]]]:
    out: dict[int, list[tuple[Date, dict[str, str]]]] = {}
    for date, rec in sorted(mods, key=lambda x: x[0]):
        code = to_int(rec.get(key))
        if code is not None:
            out.setdefault(code, []).append((date, rec))
    return out


def _fv(rec: dict[str, str], key: str, default: float = 0.0) -> float:
    v = to_float(rec.get(key))
    return default if v is None else v


def _tagged(stem: str, nb_tag: str, sfx: str = "") -> tuple[str, str]:
    """Block-tagged file names: 3-digit then 2-digit block count.

    ``deme05pe``/``dbf005pe``/``duvame09``/``rgme09gu`` … — several block
    resolutions may coexist in a case (``deme03``/``deme05``/``deme09``),
    so no wildcard fallback is used.
    """
    return f"{stem}{nb_tag}{sfx}.dat", f"{stem}{nb_tag[-2:]}{sfx}.dat"


def _table_value(
    table: MonthlyTable | None, year: int, month: int, block: int = 0
) -> float | None:
    """Monthly-table lookup, falling back to the latest earlier year."""
    if table is None:
        return None
    v = table.get(year, month, block)
    if v is not None:
        return v
    years = sorted(y for (y, b) in table.values if b == block and y < year)
    for y in reversed(years):
        v = table.get(y, month, block)
        if v is not None:
            return v
    return None


def _availability(
    mf: MonthlyFile | None,
    code: int,
    year: int,
    month: int,
    installed: float,
    units: int,
) -> float:
    """Maintenance availability factor (0–1) from a ``pm*`` file."""
    if mf is None:
        return 1.0
    t = mf.by_code().get(code)
    v = _table_value(t, year, month)
    if v is None or installed <= 0:
        return 1.0
    kind = (t.units if t is not None and t.units else None) or mf.units or 2
    info = (t.info_type if t is not None and t.info_type else None) or mf.info_type
    if info == 1:  # reduction
        if kind == 2:
            return max(0.0, 1.0 - v / 100.0)
        if kind == 3:
            return max(0.0, 1.0 - v / installed)
        if kind == 1 and units > 0:
            return max(0.0, 1.0 - v / units)
        return 1.0
    if kind == 2:
        return max(0.0, min(1.0, v / 100.0))
    if kind == 3:
        return max(0.0, min(1.0, v / installed))
    if kind == 1 and units > 0:
        return max(0.0, min(1.0, v / units))
    return 1.0


def _opt_monthly(case_dir: Path, *names: str) -> MonthlyFile | None:
    path = find_case_file(case_dir, *names)
    return parse_monthly_file(path) if path is not None else None


# ──────────────────────────────── loaders ───────────────────────────────────


def _load_stages(case_dir: Path, ctl: SddpControl, nb_tag: str) -> list[StageInfo]:
    """Stage list with block durations (``duvame`` hours, else ``durfix`` %)."""
    duva = _opt_monthly(case_dir, *_tagged("duvame", nb_tag))
    dur_tab = duva.tables[0] if duva and duva.tables else None
    fix_path = find_case_file(case_dir, *_tagged("durfix", nb_tag))
    fix_pct: list[float] = []
    if fix_path is not None:
        from .sddp_dat_parsers import (  # pylint: disable=import-outside-toplevel
            parse_fixed_durations,
        )

        fix_pct = parse_fixed_durations(fix_path)
    stages: list[StageInfo] = []
    import calendar  # pylint: disable=import-outside-toplevel

    for y, m in _stage_dates(ctl):
        hours = calendar.monthrange(y, m)[1] * 24.0
        durs: list[float] = []
        for b in range(1, ctl.num_blocks + 1):
            v = _table_value(dur_tab, y, m, b)
            if v is None and fix_pct:
                v = hours * fix_pct[b - 1] / 100.0
            if v is None:
                v = hours / ctl.num_blocks
            durs.append(v)
        stages.append(StageInfo(year=y, month=m, durations=durs))
    return stages


def _load_demands(
    case_dir: Path, sfx: str, nb_tag: str, stages: list[StageInfo]
) -> dict[int, Grid]:
    """Per-bus MW demand: ``deme`` class energy × normalised ``dbf`` factors."""
    deme = _opt_monthly(case_dir, *_tagged("deme", nb_tag, sfx))
    if deme is None:
        return {}
    dbf_path = find_case_file(case_dir, *_tagged("dbf", nb_tag, sfx))
    factors = parse_bus_load_factors(dbf_path) if dbf_path else {}
    nb = len(stages[0].durations) if stages else 0
    out: dict[int, Grid] = {}
    dates_by_class: dict[int, list[Date]] = {}
    for lcod, date in factors:
        dates_by_class.setdefault(lcod, []).append(date)
    for v in dates_by_class.values():
        v.sort()
    for t in deme.tables:
        dates = dates_by_class.get(t.code, [])
        for s, st in enumerate(stages):
            fdate = next((d for d in reversed(dates) if d <= st.date), None)
            if fdate is None and dates:
                fdate = dates[0]
            for b in range(nb):
                gwh = _table_value(t, st.year, st.month, b + 1) or 0.0
                mw = gwh * 1000.0 / st.durations[b] if st.durations[b] > 0 else 0.0
                weights = (
                    factors.get((t.code, fdate), {}).get(b + 1, {}) if fdate else {}
                )
                tot = sum(weights.values())
                if tot <= 0:
                    continue
                for bus, w in weights.items():
                    if w == 0:
                        continue
                    grid = out.setdefault(bus, [[0.0] * nb for _ in stages])
                    grid[s][b] += mw * w / tot
    return out


def _load_network(
    case_dir: Path, stages: list[StageInfo], skipped: list[str]
) -> tuple[
    dict[int, str], dict[int, float], dict[tuple[int, int], int], list[SddpLine]
]:
    bus_path = find_case_file(case_dir, "dbus.dat")
    if bus_path is None:
        return {}, {}, {}, []
    buses, gen_bus = parse_dbus(bus_path)
    conn_path = find_case_file(case_dir, "busconnection.dat")
    if conn_path is not None:
        gen_bus.update(parse_bus_connections(conn_path))
    volt_path = find_case_file(case_dir, "volt.dat", "Volt.dat")
    kv = parse_volt(volt_path) if volt_path else {}
    # ``volt.dat`` may leave buses at 0 kV (every AMM Guatemala bus);
    # recover the level from the name (``ESC-231`` → 230).  The line
    # impedances are built from X % on the 100 MVA base with
    # ``Z_base = V²/100``, so ``X/V²`` — what the LP uses — does not
    # depend on the voltage: this only fixes the reported kV.
    for num, name in buses.items():
        if num not in kv:
            named = infer_kv_from_name(name)
            if named is not None:
                kv[num] = named
    lines: list[SddpLine] = []
    circ_path = find_case_file(case_dir, "dcirc.dat")
    if circ_path is None:
        return buses, kv, gen_bus, lines
    base = parse_circuits(circ_path)
    mcirc_path = find_case_file(case_dir, "mcirc.dat")
    mods: dict[int, list[tuple[Date, SddpCircuit]]] = {}
    if mcirc_path is not None:
        for date, c in parse_circuit_mods(mcirc_path):
            mods.setdefault(c.code, []).append((date, c))
    for c in base:
        cmods = sorted(mods.get(c.code, []), key=lambda x: x[0])
        entry = (0, 0, 0) if not c.future else (cmods[0][0] if cmods else None)
        active: list[bool] = []
        tmax: list[float] = []
        rec = c
        for st in stages:
            eff = c
            for d, m in cmods:
                if d <= st.date:
                    eff = m
            rec = eff
            on = entry is not None and entry <= st.date
            active.append(on)
            tmax.append(eff.rating)
        if not any(active):
            continue
        first = next(i for i, a in enumerate(active) if a)
        eff = c
        for d, m in cmods:
            if d <= stages[first].date:
                eff = m
        if eff.from_bus not in buses or eff.to_bus not in buses:
            continue
        volt = max(kv.get(eff.from_bus, 0.0), kv.get(eff.to_bus, 0.0)) or 100.0
        zbase = volt * volt / 100.0
        x_pct = eff.x_pct if abs(eff.x_pct) >= _MIN_X_PCT else _MIN_X_PCT
        lines.append(
            SddpLine(
                code=c.code,
                name=(rec.name or f"c{c.code}").replace(" ", "_"),
                from_bus=eff.from_bus,
                to_bus=eff.to_bus,
                voltage=volt,
                resistance=max(0.0, eff.r_pct) / 100.0 * zbase,
                reactance=x_pct / 100.0 * zbase,
                active=active,
                tmax=tmax,
            )
        )
    csum = find_case_file(case_dir, "csumcirc.dat")
    if csum is not None:
        skipped.append("csumcirc/msumcirc: sum-of-circuit-flow limits not modelled")
    return buses, kv, gen_bus, lines


def _reserve_derate(rrme: MonthlyFile | None) -> dict[tuple[str, int], MonthlyTable]:
    if rrme is None:
        return {}
    return {(t.tag or "?", t.code): t for t in rrme.tables}


def _gen_constraints(
    rgme: MonthlyFile | None,
) -> dict[tuple[str, int], list[MonthlyTable]]:
    out: dict[tuple[str, int], list[MonthlyTable]] = {}
    if rgme is None:
        return out
    for t in rgme.tables:
        if t.target is not None:
            out.setdefault(t.target, []).append(t)
    return out


def _load_thermals(
    case_dir: Path,
    sfx: str,
    nb_tag: str,
    stages: list[StageInfo],
    gen_bus: dict[tuple[int, int], int],
    skipped: list[str],
) -> list[SddpThermal]:
    path = find_case_file(case_dir, f"ctermi{sfx}.dat", "ctermi*.dat")
    if path is None:
        return []
    fuel_path = find_case_file(case_dir, f"ccombu{sfx}.dat", "ccombu*.dat")
    fuels = parse_fuels(fuel_path) if fuel_path else {}
    # Monthly fuel-price schedule (``combme``) overrides ``ccombu`` costs.
    fuel_prices = _monthly_by_code(_opt_monthly(case_dir, f"combme{sfx}.dat"))
    _, recs = parse_fw_file(path, key_label="Nombre")
    mod_path = find_case_file(case_dir, f"mtermi{sfx}.dat")
    mods = _group_mods(parse_mod_file(mod_path), "Num") if mod_path else {}
    pmtr = _opt_monthly(case_dir, f"pmtrme{sfx}.dat")
    gmin = _opt_monthly(case_dir, *_tagged("gmme", nb_tag, sfx))
    gmin_by = gmin.by_code() if gmin else {}
    rrme = _reserve_derate(_opt_monthly(case_dir, *_tagged("rrme", nb_tag, sfx)))
    rgme = _gen_constraints(_opt_monthly(case_dir, *_tagged("rgme", nb_tag, sfx)))
    nb = len(stages[0].durations)
    out: list[SddpThermal] = []
    for base in recs:
        code = to_int(base.get("Num"))
        if code is None:
            continue
        bus = gen_bus.get((2, code))
        pmods = mods.get(code, [])
        entry = _entry_date(base.get("Tipo", "0") == "1", pmods)
        if bus is None or entry is None:
            continue
        pmax: Grid = []
        pmin: Grid = []
        soft: Grid = []
        gcost: list[float] = []
        any_soft = False
        cap = 0.0
        for st in stages:
            rec = _effective(base, pmods, st.date)
            on = entry <= st.date
            gmax = _fv(rec, "GerMax", _fv(rec, "PotIns"))
            cap = max(cap, _fv(rec, "PotIns"))
            nuni = int(_fv(rec, "#Uni", 1.0))
            avail = _availability(pmtr, code, st.year, st.month, gmax, nuni)
            forced = (1.0 - _fv(rec, "Teif") / 100.0) * (1.0 - _fv(rec, "Ih") / 100.0)
            avail_mw = gmax * avail * forced if on else 0.0
            fcode = int(_fv(rec, "Comb"))
            fuel_cost = fuels.get(fcode, 0.0)
            fp = _table_value(fuel_prices.get(fcode), st.year, st.month)
            if fp is not None and fp > 0:
                fuel_cost = fp
            cesp = _fv(rec, "CEsp1")
            gcost.append(cesp * fuel_cost + _fv(rec, "CVaria"))
            must = rec.get("MR", "0") == "1"
            row_max: list[float] = []
            row_min: list[float] = []
            row_soft: list[float] = []
            for b in range(1, nb + 1):
                hi = avail_mw
                res = rrme.get(("t", code))
                rv = _table_value(res, st.year, st.month, b)
                if rv:
                    hi *= max(0.0, 1.0 - rv / 100.0)
                lo = min(_fv(rec, "GerMin") * avail, hi) if must and on else 0.0
                floor = 0.0
                gm = gmin_by.get(code)
                gv = _table_value(gm, st.year, st.month, b)
                if gv and on:
                    floor = max(floor, gv)
                for t in rgme.get(("T", code), []):
                    v = _table_value(t, st.year, st.month, b)
                    if v is None or not on:
                        continue
                    if t.sense == "<":
                        hi = min(hi, v)
                    elif t.sense in (">", "="):
                        floor = max(floor, v)
                        if t.sense == "=":
                            hi = min(hi, max(v, 0.0)) if v > 0 else hi
                floor = min(floor, hi)
                any_soft = any_soft or floor > 0
                row_max.append(hi)
                row_min.append(min(lo, hi))
                row_soft.append(floor)
            pmax.append(row_max)
            pmin.append(row_min)
            soft.append(row_soft)
        if all(v <= 0 for row in pmax for v in row):
            continue
        out.append(
            SddpThermal(
                code=code,
                name=base.get("Nombre", f"t{code}"),
                bus=bus,
                pmax=pmax,
                pmin=pmin,
                gcost=gcost,
                pmin_soft=soft if any_soft else None,
                capacity=cap,
            )
        )
    if mods:
        logger.info("thermal modifications: %d plant(s)", len(mods))
    skipped.append(
        "thermal unit commitment / ramps / min up-down (opectr) relaxed: "
        "monthly blocks are non-chronological; GerMin enforced only for MR=1"
    )
    return out


def _monthly_by_code(mf: MonthlyFile | None) -> dict[int, MonthlyTable]:
    return mf.by_code() if mf else {}


def _load_hydros(
    case_dir: Path,
    sfx: str,
    nb_tag: str,
    stages: list[StageInfo],
    gen_bus: dict[tuple[int, int], int],
    ctl: SddpControl,
    skipped: list[str],
    bypass: dict[int, int | None] | None = None,
) -> list[SddpHydro]:
    path = find_case_file(case_dir, f"chidro{sfx}.dat", "chidro*.dat")
    if path is None:
        return []
    spans, recs = parse_fw_file(path, key_label="Nombre")
    keys = span_keys(spans)
    cota_vol = [
        (keys[i], keys[i + 1])
        for i, k in enumerate(keys[:-1])
        if re.fullmatch(r"Cota\d", k)
    ]
    mod_path = find_case_file(case_dir, f"mhidro{sfx}.dat")
    mods = _group_mods(parse_mod_file(mod_path), "NUM") if mod_path else {}
    pmhi = _opt_monthly(case_dir, f"pmhime{sfx}.dat")
    vmin_t = _monthly_by_code(_opt_monthly(case_dir, f"vminme{sfx}.dat"))
    vmxo_t = _monthly_by_code(_opt_monthly(case_dir, f"vmxome{sfx}.dat"))
    mnto_t = _monthly_by_code(_opt_monthly(case_dir, f"mntome{sfx}.dat"))
    mxto_t = _monthly_by_code(_opt_monthly(case_dir, f"mxtome{sfx}.dat"))
    mxtb_t = _monthly_by_code(_opt_monthly(case_dir, f"mxtbme{sfx}.dat"))
    rieg_t = _monthly_by_code(_opt_monthly(case_dir, f"riegme{sfx}.dat"))
    rrme = _reserve_derate(_opt_monthly(case_dir, *_tagged("rrme", nb_tag, sfx)))
    rgme = _gen_constraints(_opt_monthly(case_dir, *_tagged("rgme", nb_tag, sfx)))
    nb = len(stages[0].durations)
    pen_hm3 = 1000.0  # k$/hm³ → $/hm³
    out: list[SddpHydro] = []
    dropped: dict[int, tuple[int, int | None]] = {}
    for base in recs:
        code = to_int(base.get("NUM"))
        if code is None:
            continue
        hmods = mods.get(code, [])
        entry = _entry_date(base.get("Tipo", "0") == "1", hmods)
        active = [entry is not None and entry <= st.date for st in stages]
        if not any(active):
            # Not built in the horizon: no turbine, so water reaching it
            # follows the spill path (VAA), else the turbine path (TAA).
            dropped[code] = (
                to_int(base.get("PV")) or 0,
                to_int(base.get("VAA")) or to_int(base.get("TAA")) or None,
            )
            if bypass is not None:
                bypass[code] = dropped[code][1]
            continue
        first = active.index(True)
        rec0 = _effective(base, hmods, stages[first].date)
        vmin = _fv(rec0, "VMin")
        vmax = _fv(rec0, "VMax")
        eini = _initial_volume(rec0, vmin, vmax, cota_vol)
        pot: list[float] = []
        pmax: Grid = []
        fp: list[float] = []
        qmax: list[float] = []
        emax: list[float] = []
        soft_emin: list[float] = []
        min_out: list[float] = []
        max_out: list[float] = []
        irrig: list[float] = []
        soft: Grid = []
        any_soft = False
        for s, st in enumerate(stages):
            rec = _effective(base, hmods, st.date)
            on = active[s]
            p_inst = _fv(rec, "Pot") if on else 0.0
            nuni = int(_fv(rec, "#Uni", 1.0))
            avail = _availability(pmhi, code, st.year, st.month, p_inst, nuni)
            forced = (1.0 - _fv(rec, "ICP") / 100.0) * (1.0 - _fv(rec, "IH") / 100.0)
            pot.append(p_inst)
            fp.append(_fv(rec, "FPMed"))
            q = _fv(rec, "QMax")
            qv = _table_value(mxtb_t.get(code), st.year, st.month)
            if qv is not None:
                q = qv if q <= 0 else min(q, qv)
            qmax.append(q)
            vm = _fv(rec, "VMax", vmax)
            xo = _table_value(vmxo_t.get(code), st.year, st.month)
            emax.append(min(vm, xo) if xo is not None and xo > vmin else vm)
            vo = _table_value(vmin_t.get(code), st.year, st.month)
            soft_emin.append(max(0.0, vo) if vo is not None else 0.0)
            mo = _table_value(mnto_t.get(code), st.year, st.month) or 0.0
            min_out.append(max(mo, _fv(rec, "MinV+T")) if on else 0.0)
            xo2 = _table_value(mxto_t.get(code), st.year, st.month)
            max_out.append(xo2 if xo2 and xo2 > 0 else 0.0)
            ir = _table_value(rieg_t.get(code), st.year, st.month)
            irrig.append(max(0.0, ir) if ir else 0.0)
            row: list[float] = []
            row_soft: list[float] = []
            for b in range(1, nb + 1):
                hi = p_inst * avail * forced
                rv = _table_value(rrme.get(("h", code)), st.year, st.month, b)
                if rv:
                    hi *= max(0.0, 1.0 - rv / 100.0)
                floor = 0.0
                for t in rgme.get(("H", code), []):
                    v = _table_value(t, st.year, st.month, b)
                    if v is None or not on:
                        continue
                    if t.sense == "<":
                        hi = min(hi, v)
                    elif t.sense in (">", "="):
                        floor = max(floor, v)
                floor = min(floor, hi)
                any_soft = any_soft or floor > 0
                row.append(hi)
                row_soft.append(floor)
            pmax.append(row)
            soft.append(row_soft)
        vmin_tab = vmin_t.get(code)
        mnto_tab = mnto_t.get(code)
        rieg_tab = rieg_t.get(code)
        out.append(
            SddpHydro(
                code=code,
                name=base.get("Nombre", f"h{code}"),
                station=to_int(base.get("PV")) or 0,
                downstream=to_int(rec0.get("TAA")) or None,
                spill_to=to_int(rec0.get("VAA")) or None,
                active=active,
                pot=pot,
                pmax=pmax,
                fp=fp,
                qmax=qmax,
                bus=gen_bus.get((1, code)),
                vmin=vmin,
                vmax=vmax,
                eini=eini,
                emax=emax,
                soft_emin=soft_emin,
                soft_emin_cost=(
                    (vmin_tab.penalty or 0.0) * pen_hm3 if vmin_tab is not None else 0.0
                ),
                min_outflow=min_out,
                min_outflow_cost=(
                    max(mnto_tab.penalty or 0.0, 0.0) * pen_hm3 * HM3_PER_M3S_H
                    if mnto_tab is not None
                    else ctl.deficit_cost * HM3_PER_M3S_H
                ),
                max_outflow=max_out,
                irrigation=irrig,
                irrigation_cost=(
                    max(rieg_tab.penalty or 0.0, 0.0) * pen_hm3 * HM3_PER_M3S_H
                    if rieg_tab is not None
                    else 0.0
                ),
                pmin_soft=soft if any_soft else None,
            )
        )
    skipped.append(
        "hydro: production-factor × volume curves, evaporation, filtration, "
        "non-controllable spill (VNC) not modelled — FPMed used"
    )
    capped = [h.name for h in out if any(v > 0 for v in h.max_outflow)]
    if capped:
        skipped.append(
            f"hydro: maximum total outflow (mxtome) not modelled for {len(capped)} "
            "plant(s)"
        )
    _route_dropped_inflows(out, dropped, skipped)
    return out


def _initial_volume(
    rec: dict[str, str],
    vmin: float,
    vmax: float,
    cota_vol: list[tuple[str, str]],
) -> float:
    """Initial stored volume [hm³] from ``VInic`` and its type ``tini``.

    ``tini`` 0 (default): per-unit of the useful volume; 1: elevation
    [m], converted with the ``Cota``/``Vol`` table of the plant.
    """
    if vmax <= vmin:
        return 0.0
    vinic = _fv(rec, "VInic")
    if int(_fv(rec, "tini")) == 1:
        pts = sorted((_fv(rec, c), _fv(rec, v)) for c, v in cota_vol if _fv(rec, c) > 0)
        if len(pts) >= 2:
            if vinic <= pts[0][0]:
                vol = pts[0][1]
            elif vinic >= pts[-1][0]:
                vol = pts[-1][1]
            else:
                vol = pts[-1][1]
                for (h0, v0), (h1, v1) in zip(pts, pts[1:]):
                    if h0 <= vinic <= h1:
                        vol = v0 + (v1 - v0) * (vinic - h0) / max(h1 - h0, 1e-9)
                        break
            return max(vmin, min(vmax, vol))
        return vmin + 0.5 * (vmax - vmin)
    return vmin + max(0.0, min(1.0, vinic)) * (vmax - vmin)


def _route_dropped_inflows(
    hydros: list[SddpHydro],
    dropped: dict[int, tuple[int, int | None]],
    skipped: list[str],
) -> None:
    """Hand the natural inflow of out-of-service plants to the next plant.

    A plant that never operates in the horizon is not modelled, but its
    incremental inflow still reaches the river: walk its downstream chain
    (VAA, else TAA) through other dropped plants until an in-service plant
    is found and add the station there.
    """
    by_code = {h.code: h for h in hydros}
    owned = {h.station for h in hydros if h.station}
    lost = 0
    for code, (station, down) in dropped.items():
        if not station or station in owned:
            continue
        seen = {code}
        while down is not None and down in dropped and down not in seen:
            seen.add(down)
            down = dropped[down][1]
        target = by_code.get(down) if down is not None else None
        if target is None:
            lost += 1
            continue
        if station not in target.extra_stations:
            target.extra_stations.append(station)
        owned.add(station)
    if lost:
        skipped.append(
            f"hydro: {lost} out-of-service plant station(s) with no in-service "
            "downstream plant — their inflow leaves the modelled system"
        )


def _load_renewables(
    case_dir: Path,
    sfx: str,
    stages: list[StageInfo],
    gen_bus: dict[tuple[int, int], int] | None = None,
) -> list[SddpRenewable]:
    path = find_case_file(case_dir, f"cgnd{sfx}.dat", "cgnd*.dat")
    if path is None:
        return []
    _, recs = parse_fw_file(path, key_label="Name")
    site_path = find_case_file(case_dir, f"gndsite{sfx}.dat")
    sites = (
        {
            to_int(r.get("Code")): r.get("Name", "").strip()
            for r in parse_csv_dat(site_path)
        }
        if site_path
        else {}
    )
    mod_path = find_case_file(case_dir, f"mgnd{sfx}.dat")
    mods = _group_mods(parse_dated_fw_file(mod_path), "Num") if mod_path else {}
    out: list[SddpRenewable] = []
    for base in recs:
        code = to_int(base.get("Num"))
        bus = to_int(base.get("Bus")) or None
        if code is not None and bus is None and gen_bus:
            bus = gen_bus.get((3, code))
        if code is None or bus is None:
            continue
        rmods = mods.get(code, [])
        entry = _entry_date(base.get("Tipo", "0") == "1", rmods)
        if entry is None:
            continue
        caps: list[float] = []
        for st in stages:
            rec = _effective(base, rmods, st.date)
            on = entry <= st.date
            caps.append(_fv(rec, "PotIns") * _fv(rec, "FatOpe", 1.0) if on else 0.0)
        if not any(c > 0 for c in caps):
            continue
        stat = to_int(base.get("Stat")) or code
        out.append(
            SddpRenewable(
                code=code,
                name=base.get("Name", f"r{code}"),
                bus=bus,
                station=sites.get(stat, base.get("Name", "")),
                tech=int(_fv(base, "TechTyp")),
                capacity=caps,
                om_cost=max(0.0, _fv(base, "O&M")),
            )
        )
    return out


def _load_batteries(
    case_dir: Path, sfx: str, stages: list[StageInfo], skipped: list[str]
) -> list[SddpBattery]:
    path = find_case_file(case_dir, f"cbattery{sfx}.dat")
    if path is None:
        return []
    mod_path = find_case_file(case_dir, f"mbattery{sfx}.dat")
    mod_dates: dict[int, list[Date]] = {}
    if mod_path is not None:
        for r in parse_csv_dat(mod_path):
            code = to_int(r.get("Num"))
            y, m, d = (
                to_int(r.get("Year")),
                to_int(r.get("Month")),
                to_int(r.get("Day")),
            )
            if code is not None and y and m:
                mod_dates.setdefault(code, []).append((y, m, d or 1))
    last = stages[-1].date
    out: list[SddpBattery] = []
    for r in parse_csv_dat(path):
        code = to_int(r.get("Num"))
        bus = to_int(r.get("Bus"))
        if code is None or bus is None:
            continue
        future = r.get("Type", "0").strip() == "1"
        dates = sorted(mod_dates.get(code, []))
        if future and (not dates or dates[0] > last):
            continue
        out.append(
            SddpBattery(
                code=code,
                name=r.get("Name", f"bat{code}").strip(),
                bus=bus,
                emin=to_float(r.get("Amin")) or 0.0,
                emax=to_float(r.get("Amax")) or 0.0,
                eini=to_float(r.get("Ainic")) or 0.0,
                pmax=to_float(r.get("Pot")) or 0.0,
                eff_charge=to_float(r.get("RC")) or 1.0,
                eff_discharge=to_float(r.get("RD")) or 1.0,
            )
        )
    if out:
        skipped.append(
            "batteries: monthly blocks are non-chronological — modelled as a "
            "per-stage energy-neutral storage (no inter-stage carry-over)"
        )
    return out


def _load_inflows(
    case_dir: Path,
    ctl: SddpControl,
    stages: list[StageInfo],
    stations: set[int],
    scenarios: list[int],
) -> tuple[dict[int, list[list[float]]], list[int]]:
    path = find_case_file(case_dir, "hinflw.dat")
    if path is None:
        return {}, []
    hist = parse_hist_inflows(path)
    y0 = ctl.hydro_initial_year or min(y for st in hist.values() for (y, _) in st)
    y_start = stages[0].year if stages else 0
    years = [y0 + s - 1 for s in scenarios]
    out: dict[int, list[list[float]]] = {}
    for code in sorted(stations):
        series = hist.get(code)
        if series is None:
            continue
        per_scn: list[list[float]] = []
        for hy in years:
            row: list[float] = []
            for st in stages:
                # Keep the calendar month (seasonality); advance the
                # historical year with the study year.
                key = (hy + st.year - y_start, st.month)
                row.append(max(0.0, series.get(key, 0.0)))
            per_scn.append(row)
        out[code] = per_scn
    return out, years


def _load_profiles(
    case_dir: Path,
    sfx: str,
    nb_tag: str,
    stages: list[StageInfo],
    scenarios: list[int],
    stations: set[str],
) -> dict[str, list[Grid]]:
    if not stations:
        return {}
    path = find_case_file(case_dir, *_tagged("blrenw", nb_tag, sfx))
    years = {st.year for st in stages}
    names, rows = parse_block_profiles(path, years=years) if path else ([], {})
    path0 = find_case_file(
        case_dir, f"blrenw{nb_tag}{sfx}0.dat", f"blrenw{nb_tag}0.dat"
    )
    names0, rows0 = parse_block_profiles(path0) if path0 else ([], {})
    path_s = find_case_file(case_dir, *_tagged("gndme", nb_tag, sfx))
    names_s, rows_s = parse_stage_profiles(path_s) if path_s else ([], {})
    idx = {n: i for i, n in enumerate(names)}
    idx0 = {n: i for i, n in enumerate(names0)}
    idx_s = {n: i for i, n in enumerate(names_s)}
    nb = len(stages[0].durations)
    scn_avail = sorted({k[0] for k in rows}) or [1]
    out: dict[str, list[Grid]] = {}
    for name in sorted(stations):
        i = idx.get(name)
        i0 = idx0.get(name)
        i_s = idx_s.get(name)
        if i is None and i0 is None and i_s is None:
            continue
        per_scn: list[Grid] = []
        for s in scenarios:
            scn = s if s in scn_avail else scn_avail[(s - 1) % len(scn_avail)]
            grid: Grid = []
            for k, st in enumerate(stages):
                row: list[float] = []
                for b in range(1, nb + 1):
                    v = None
                    if i_s is not None:
                        vals_s = rows_s.get((scn, k + 1, b)) or rows_s.get(
                            (1, k + 1, b)
                        )
                        if vals_s is not None and i_s < len(vals_s):
                            v = vals_s[i_s]
                    if v is None and i is not None:
                        vals = rows.get((scn, st.year, st.month, b))
                        if vals is not None and i < len(vals):
                            v = vals[i]
                    if v is None and i0 is not None:
                        vals0 = rows0.get((scn, 0, st.month, b)) or rows0.get(
                            (1, 0, st.month, b)
                        )
                        if vals0 is not None and i0 < len(vals0):
                            v = vals0[i0]
                    row.append(max(0.0, min(1.0, v if v is not None else 1.0)))
                grid.append(row)
            per_scn.append(grid)
        out[name] = per_scn
    return out


def load_sddp_block_case(
    case_dir: str | Path,
    *,
    num_scenarios: int | None = None,
    deficit_segments: str = "first",
) -> SddpBlockCase:
    """Parse a PSR SDDP block study into a :class:`SddpBlockCase`.

    Args:
        case_dir: PSR case directory (``sddp.dat`` + ``*<sfx>.dat``).
        num_scenarios: Keep only the first N historical series (default:
            all ``NUMERO DE SERIES``).
        deficit_segments: ``"first"`` (default), ``"all"`` or ``"split"``;
            only decides
            whether a multi-segment deficit curve is reported as simplified
            (the split itself happens in the writer).
    """
    case_dir = Path(case_dir)
    ctl_path = find_case_file(case_dir, "sddp.dat")
    if ctl_path is None:
        raise FileNotFoundError(f"{case_dir}: no sddp.dat")
    ctl = parse_sddp_control(ctl_path)
    sys_path = find_case_file(case_dir, "sistem.dat", "Sistem.dat")
    sfx = parse_system_suffix(sys_path) if sys_path else ""
    nb_tag = f"{ctl.num_blocks:03d}" if ctl.num_blocks else ""
    skipped: list[str] = []
    if len(ctl.deficit_segments) > 1 and deficit_segments not in ("all", "split"):
        skipped.append(
            "deficit cost curve "
            + ", ".join(f"{p:g}%@{c:g}" for p, c in ctl.deficit_segments)
            + f": single demand_fail_cost = first segment ({ctl.deficit_cost:g} $/MWh)"
        )

    stages = _load_stages(case_dir, ctl, nb_tag)
    n_scn = ctl.num_series if ctl.inflow_type == 2 else 1
    if num_scenarios:
        n_scn = min(n_scn, num_scenarios) if n_scn else num_scenarios
    scenarios = list(range(1, max(1, n_scn) + 1))

    buses, kv, gen_bus, lines = _load_network(case_dir, stages, skipped)
    thermals = _load_thermals(case_dir, sfx, nb_tag, stages, gen_bus, skipped)
    bypass: dict[int, int | None] = {}
    hydros = _load_hydros(
        case_dir, sfx, nb_tag, stages, gen_bus, ctl, skipped, bypass=bypass
    )
    renewables = _load_renewables(case_dir, sfx, stages, gen_bus)
    batteries = _load_batteries(case_dir, sfx, stages, skipped)
    demands = _load_demands(case_dir, sfx, nb_tag, stages)
    if buses:
        orphan = sorted(b for b in demands if b not in buses)
        if orphan:
            lost = sum(
                v * h
                for b in orphan
                for row, st in zip(demands[b], stages)
                for v, h in zip(row, st.durations)
            )
            for b in orphan:
                del demands[b]
            skipped.append(
                f"demand at bus(es) missing from dbus dropped: {orphan} "
                f"({lost / 1e3:.1f} GWh)"
            )
    inflows, hydro_years = _load_inflows(
        case_dir,
        ctl,
        stages,
        {s for h in hydros for s in [h.station, *h.extra_stations] if s},
        scenarios,
    )
    profiles = _load_profiles(
        case_dir, sfx, nb_tag, stages, scenarios, {r.station for r in renewables}
    )
    no_prof = sorted({r.name for r in renewables if r.station not in profiles})
    if no_prof:
        skipped.append(
            f"renewables without a block profile (dispatchable up to capacity): "
            f"{', '.join(no_prof)}"
        )
    for name, pattern in (
        ("joint reserve requirements (rsme/resger)", f"rsme*{sfx}.dat"),
        ("gas network (sisgas/intgas/demgas)", "sisgas.dat"),
        ("hourly chronology (hrload/hrrenw/hrbmap)", f"hrload{sfx}.dat"),
    ):
        if any(case_dir.glob(pattern + "*")):
            skipped.append(f"{name}: not modelled")

    case = SddpBlockCase(
        control=ctl,
        suffix=sfx,
        stages=stages,
        scenarios=scenarios,
        hydro_years=hydro_years,
        buses=buses,
        bus_kv=kv,
        lines=lines,
        thermals=thermals,
        hydros=hydros,
        renewables=renewables,
        batteries=batteries,
        demands=demands,
        inflows=inflows,
        profiles=profiles,
        skipped=skipped,
        hydro_bypass=bypass,
    )
    logger.info(
        "loaded PSR SDDP block case %s (sfx=%s): %d stages × %d blocks, "
        "%d scenarios, %d buses, %d lines, %d thermals, %d hydros "
        "(%d reservoirs), %d renewables, %d batteries, %d demand buses",
        case_dir.name,
        sfx,
        len(stages),
        case.num_blocks,
        len(scenarios),
        len(buses),
        len(lines),
        len(thermals),
        len(hydros),
        sum(1 for h in hydros if h.has_storage),
        len(renewables),
        len(batteries),
        len(demands),
    )
    return case


def summarize(case: SddpBlockCase) -> dict[str, Any]:
    """Key aggregate figures (for ``--info`` and validation reports)."""
    hours = [sum(st.durations) for st in case.stages]
    dem_gwh = sum(
        g[s][b] * case.stages[s].durations[b] / 1000.0
        for g in case.demands.values()
        for s in range(len(case.stages))
        for b in range(case.num_blocks)
    )
    tech_names = {
        0: "other",
        1: "wind",
        2: "solar",
        3: "biomass",
        4: "small hydro",
        5: "csp",
    }
    ren_mw: dict[str, float] = {}
    for r in case.renewables:
        key = tech_names.get(r.tech, f"tech{r.tech}")
        ren_mw[key] = ren_mw.get(key, 0.0) + r.capacity[0]
    return {
        "stages": len(case.stages),
        "blocks": case.num_blocks,
        "hours": sum(hours),
        "scenarios": len(case.scenarios),
        "buses": len(case.buses),
        "lines": len(case.lines),
        "thermals": len(case.thermals),
        "thermal_mw": sum(t.capacity for t in case.thermals),
        "hydros": len(case.hydros),
        "hydro_mw": sum(h.pot[0] for h in case.hydros),
        "reservoirs": sum(1 for h in case.hydros if h.has_storage),
        "renewables": len(case.renewables),
        "renewable_mw": ren_mw,
        "batteries": len(case.batteries),
        "demand_buses": len(case.demands),
        "demand_gwh": dem_gwh,
    }
