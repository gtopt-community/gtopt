# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Write a gtopt planning as a PLP case (``.dat`` dialect).

The inverse of :mod:`plp2gtopt`: the files written here are the ones its
parsers read (``plpblo``, ``plpeta``, ``plpbar``, ``plpcnfli``,
``plpcnfce``, ``plpdem``, ``plpcosce``, ``plpmance``, ``plpmanli``,
``plpaflce``, ``plpextrac``, ``plpmanem`` -- always written, possibly with
a zero count -- plus ``plpess``, ``plpcenre``, ``plpfilemb``, ``plpmat``
and ``centipo.csv`` when needed), in the layouts those parsers accept.

Mapping (gtopt -> PLP central types, in the order ``plpcnfce`` needs):

* **embalse**: a Junction with a Reservoir (and its Turbine/Generator);
* **serie**: any other Junction (a Junction without a plant becomes a
  serie central with ``Barra = 0``);
* **pasada**: a Turbine fed by a Flow without a junction (hydro pasada,
  m3/s in ``plpaflce``), and a Generator with a capacity profile (solar,
  wind, ... -- MW in ``plpaflce`` plus a ``centipo.csv`` row);
* **termica**: every other Generator;
* **bateria**: a Battery (``plpess.dat``);
* **falla**: one per demand bus, ``CosVar`` = the demand fail cost.

Hydro arcs: the turbine outlet is ``SerHid``, one spill waterway is
``SerVer`` (``VertMin``/``VertMax``), seepage waterways go to
``plpfilemb``, any other waterway to ``plpextrac``.  Per-block values go
to ``plpmance`` / ``plpmanli`` / ``plpaflce``, per-stage values to
``plpcosce`` / ``plpmanem``.  What PLP cannot express is reported in
:attr:`PlpWriter.warnings` and ``gtopt2plp.json``.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from gtopt_shared.case_reader import GtoptCase, per_stage

logger = logging.getLogger(__name__)

MONTHS = (
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

NO_LIMIT = 9999.0
CENTIPO = {"solar": "SOL", "wind": "EO", "csp": "CSP"}


def _num(value: float) -> str:
    """Shortest float text that round-trips (no Fortran ``d`` exponent)."""
    v = float(value)
    if v.is_integer() and abs(v) < 1e15:
        return f"{v:.1f}"
    return repr(v)


def _sanitize(name: Any) -> str:
    return re.sub(r"[\s']+", "_", str(name)).strip("_") or "X"


def line_names(lines: list[dict[str, Any]]) -> list[str]:
    """PLP names of ``line_array`` in order (sanitized, made unique)."""
    out: list[str] = []
    names: set[str] = set()
    for i, ln in enumerate(lines):
        name = _sanitize(ln.get("name", f"L{i + 1}"))
        while name in names:
            name += "_"
        names.add(name)
        out.append(name)
    return out


def hydro_month(calendar: int) -> int:
    """PLP hydrological month (1 = April) of a calendar month (1-12)."""
    return ((calendar - 4) % 12) + 1


@dataclass
class _Central:
    """One ``plpcnfce`` central."""

    kind: str  # embalse | serie | pasada | termica | bateria | falla
    num: int
    name: str
    generator: dict[str, Any] | None = None
    junction: dict[str, Any] | None = None
    reservoir: dict[str, Any] | None = None
    turbine: dict[str, Any] | None = None
    battery: dict[str, Any] | None = None
    bus: int = 0
    potmin: float = 0.0
    potmax: float = 0.0
    vertmin: float = 0.0
    vertmax: float = 0.0
    cosvar: float = 0.0
    rendi: float = 1.0
    serhid: Any = None  # junction ref
    server: Any = None  # junction ref
    afluen: float = 0.0
    inflow: np.ndarray | None = None  # (n_scen, n_blocks) when not constant
    centipo: str | None = None
    vols: tuple[float, float, float, float] | None = None  # ini, fin, min, max (hm3)


@dataclass
class PlpWriter:
    """Export a :class:`GtoptCase` as a PLP case directory."""

    case: GtoptCase
    out_dir: Path
    start_month: int | None = None
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Declare the state :meth:`write` fills (typed empty defaults)."""
        self.months: list[int] = []
        self.sob: np.ndarray = np.zeros(0, dtype=np.int64)
        self.buses: list[dict[str, Any]] = []
        self.bus_ref: dict[Any, dict[str, Any]] = {}
        self.bus_names: dict[int, str] = {}
        self.centrals: list[_Central] = []
        self._used_nums: set[int] = set()
        self._used_names: set[str] = set()
        self.extrac: list[tuple[str, float, Any]] = []
        self.filemb: list[dict[str, Any]] = []
        self.cenre: list[dict[str, Any]] = []
        self.spill_costs: set[float] = set()
        self.hydro_gen_ids: set[int] = set()
        self._junction_central: dict[int, _Central] = {}
        self._jref: dict[Any, dict[str, Any]] = {}
        self._pasada_flow_ids: set[int] = set()
        self.demand_bus: dict[int, np.ndarray] = {}
        self.manli: list[tuple[str, list[str]]] = []

    def warn(self, msg: str) -> None:
        """Record and log a lossy-conversion warning (once per text)."""
        if msg not in self.warnings:
            self.warnings.append(msg)
            logger.warning("%s", msg)

    def _write(self, name: str, lines: list[str]) -> None:
        (self.out_dir / name).write_text(
            "\n".join(lines) + "\n", encoding="latin-1", errors="replace"
        )

    def _first(self, arr: np.ndarray, what: str) -> np.ndarray:
        if arr.shape[0] > 1 and not np.allclose(arr, arr[0], equal_nan=True):
            self.warn(f"{what} varies by scenario; PLP keeps scenario 1 only")
        return np.asarray(arr[0])

    # ── setup ──────────────────────────────────────────────────────────
    def _stage_months(self) -> list[int]:
        """Calendar month of every stage."""
        g = self.case.grid
        out: list[int] = []
        cur = self.start_month
        for k, st in enumerate(g.stages):
            if st.month and st.month.lower() in MONTHS and self.start_month is None:
                out.append(MONTHS.index(st.month.lower()) + 1)
                continue
            if cur is None:
                cur = 4  # PLP hydrological year starts in April
            out.append(((cur - 1 + k) % 12) + 1)
        return out

    def write(self) -> Path:
        """Write every file; returns the output directory."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.collect()
        self.write_files()
        return self.out_dir

    def collect(self) -> None:
        """Map the case onto PLP centrals, buses and blocks (no file written).

        Fills :attr:`centrals`, :attr:`bus_names`, :attr:`months`, ... ; the
        results export (:mod:`gtopt2plp.results`) calls it alone so its
        names and numbers are the ones the ``.dat`` files carry.
        """
        c = self.case
        g = c.grid
        blocks_ok = all(
            st.first_block
            == (g.stages[k - 1].first_block + g.stages[k - 1].count_block if k else 0)
            for k, st in enumerate(g.stages)
        )
        if not blocks_ok:
            raise ValueError(
                "PLP needs the blocks of each stage contiguous and in stage order"
            )
        self.months = self._stage_months()
        self.sob = g.stage_of_block()
        probs = np.asarray(g.probabilities)
        if g.n_scenarios > 1 and not np.allclose(probs, probs[0]):
            self.warn(
                "scenario probabilities are not in PLP files: pass them to plp2gtopt with -p "
                + ",".join(f"{p:.6g}" for p in probs)
            )
        rate = (c.planning.get("simulation", {}) or {}).get("annual_discount_rate")
        if rate:
            self.warn(
                f"annual_discount_rate is not in PLP files: pass it to plp2gtopt with -d {rate}"
            )

        self.buses = c.elements("bus_array")
        self.bus_ref = c.ref_index(self.buses)
        self.bus_names = {
            int(b["uid"]): _sanitize(b.get("name", f"B{b['uid']}")) for b in self.buses
        }
        self.centrals = []
        self._used_nums = set()
        self._used_names = set()
        self.extrac = []
        self.filemb = []
        self.cenre = []
        self.spill_costs = set()

        self._collect_hydro()
        self._collect_generators()
        self._collect_batteries()
        self._collect_fallas()

    def write_files(self) -> None:
        """Write the ``.dat`` files of the collected case (after :meth:`collect`)."""
        c = self.case
        self._write_blocks()
        self._write_buses()
        self._write_lines()
        self._write_centrals()
        self._write_demands()
        self._write_costs()
        self._write_mance()
        self._write_aflce()
        self._write_extrac()
        self._write_manem()
        self._write_ess()
        self._write_hydro_extras()
        self._write_plpmat()
        for array in (
            "reserve_zone_array",
            "user_constraint_array",
            "flow_right_array",
            "volume_right_array",
            "emission_zone_array",
        ):
            if c.elements(array):
                self.warn(f"{array} is not exported (no PLP file written for it)")
        meta = {
            "source": "gtopt2plp",
            "centrals": {
                ce.name: {"kind": ce.kind, "num": ce.num} for ce in self.centrals
            },
            "plp2gtopt_flags": self._flags(),
            "gtopt_set": self.gtopt_set(),
            "warnings": self.warnings,
        }
        (self.out_dir / "gtopt2plp.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def plp2gtopt_flags(self) -> list[str]:
        """Flags for ``plp2gtopt`` that restore what PLP files cannot hold."""
        return self._flags()

    def gtopt_set(self) -> list[str]:
        """``gtopt --set`` overrides for solving the read-back case alike.

        plp2gtopt makes reservoir ``emin`` soft (``strict_storage_emin =
        false``, PLP's per-stage emin), while a gtopt case defaults to a
        hard ``emin``.
        """
        mo = self.case.options.get("model_options") or {}
        strict = mo.get("strict_storage_emin", True)
        if self.case.elements("reservoir_array") and strict is not False:
            return ["model_options.strict_storage_emin=true"]
        return []

    def _flags(self) -> list[str]:
        g = self.case.grid
        flags = ["-y", "all" if g.n_scenarios > 1 else "1"]
        probs = np.asarray(g.probabilities)
        if g.n_scenarios > 1 and not np.allclose(probs, probs[0]):
            flags += ["-p", ",".join(f"{p:.10g}" for p in probs)]
        rate = (self.case.planning.get("simulation", {}) or {}).get(
            "annual_discount_rate"
        )
        if rate:
            flags += ["-d", f"{rate}"]
        return flags

    # ── naming / numbering ─────────────────────────────────────────────
    def _new_num(self, prefer: Any) -> int:
        if isinstance(prefer, int) and prefer > 0 and prefer not in self._used_nums:
            self._used_nums.add(prefer)
            return prefer
        n = max(self._used_nums, default=0) + 1
        self._used_nums.add(n)
        return n

    def _new_name(self, base: Any) -> str:
        name = _sanitize(base)
        k = 1
        out = name
        while out in self._used_names:
            k += 1
            out = f"{name}_{k}"
        self._used_names.add(out)
        return out

    def _bus_uid(self, ref: Any) -> int:
        bus = self.bus_ref.get(ref)
        return 0 if bus is None else int(bus["uid"])

    def _gen_limits(self, ce: _Central, gen: dict[str, Any]) -> None:
        c = self.case
        pmax = self._first(
            c.field("Generator", gen, "pmax", default=np.nan), f"{ce.name} pmax"
        )
        cap = gen.get("capacity")
        capv = float(cap) if isinstance(cap, (int, float)) else math.nan
        pmax = np.where(
            np.isnan(pmax), capv if not math.isnan(capv) else NO_LIMIT, pmax
        )
        pmin = np.nan_to_num(
            self._first(
                c.field("Generator", gen, "pmin", default=0.0), f"{ce.name} pmin"
            )
        )
        ce.potmax = float(pmax.max())
        ce.potmin = (
            float(pmin.max()) if np.allclose(pmin, pmin.max()) else float(pmin.min())
        )
        ce.generator = {**gen, "_pmax": pmax, "_pmin": pmin}
        gcost = np.nan_to_num(
            self._first(
                c.field("Generator", gen, "gcost", default=0.0), f"{ce.name} gcost"
            )
        )
        st = per_stage(c.grid, gcost[None, :], "mean")[0]
        if not np.allclose(per_stage(c.grid, gcost[None, :], "max")[0], st):
            self.warn(
                f"{ce.name}: gcost varies within a stage; PLP uses the stage mean"
            )
        ce.cosvar = float(st[0])
        ce.generator["_gcost_stage"] = st
        ce.bus = self._bus_uid(gen.get("bus"))
        for extra in (
            "expcap",
            "expmod",
            "annual_capcost",
            "emission_factor",
            "startup_cost",
        ):
            if gen.get(extra) not in (None, 0, 0.0):
                self.warn(f"{ce.name}: {extra} is not exported")

    # ── collection ─────────────────────────────────────────────────────
    def _collect_hydro(self) -> None:
        c = self.case
        g = c.grid
        junctions = c.elements("junction_array")
        jref = c.ref_index(junctions)
        gens = c.ref_index(c.elements("generator_array"))
        self.hydro_gen_ids = set()
        by_junction: dict[int, _Central] = {}
        reservoirs = {
            id(jref[r["junction"]]): r
            for r in c.elements("reservoir_array")
            if r.get("junction") in jref
        }
        turbines_at: dict[int, dict[str, Any]] = {}
        pasadas: list[dict[str, Any]] = []
        for tu in c.elements("turbine_array"):
            ja = tu.get("junction_a")
            if ja in jref:
                if id(jref[ja]) in turbines_at:
                    self.warn(
                        f"junction {ja}: several turbines; only the first is exported"
                    )
                    continue
                turbines_at[id(jref[ja])] = tu
            elif tu.get("flow") is not None:
                pasadas.append(tu)
            else:
                self.warn(
                    f"turbine {tu.get('name')}: neither junction nor flow; dropped"
                )
        # embalses first, then series (plpcnfce order)
        ordered = [j for j in junctions if id(j) in reservoirs] + [
            j for j in junctions if id(j) not in reservoirs
        ]
        for j in ordered:
            t = turbines_at.get(id(j))
            gen = gens.get(t.get("generator")) if t else None
            base = (gen or {}).get("name") or j.get("name")
            ce = _Central(
                kind="embalse" if id(j) in reservoirs else "serie",
                num=self._new_num((gen or {}).get("uid", j.get("uid"))),
                name=self._new_name(base),
                junction=j,
                reservoir=reservoirs.get(id(j)),
                turbine=t,
            )
            if gen is not None:
                self.hydro_gen_ids.add(id(gen))
                self._gen_limits(ce, gen)
            if t is not None:
                pf = self._first(
                    c.field("Turbine", t, "production_factor", default=1.0),
                    f"{ce.name} production_factor",
                )
                ce.rendi = float(pf[0])
                if not np.allclose(pf, ce.rendi):
                    self.warn(
                        f"turbine {t.get('name')}: time-varying production factor; first value kept"
                    )
                ce.serhid = t.get("junction_b")
                if t.get("capacity") is not None:
                    self.warn(
                        f"turbine {t.get('name')}: flow capacity is not a PLP field (PLP bounds "
                        "the turbine by PotMax/Rendi)"
                    )
            by_junction[id(j)] = ce
            self.centrals.append(ce)
        self._junction_central = {id(j): by_junction[id(j)] for j in junctions}
        self._jref = jref
        self._hydro_arcs(by_junction)
        self._hydro_inflows(by_junction)
        self._reservoirs(by_junction)
        # hydro pasadas
        flows = c.ref_index(c.elements("flow_array"))
        self._pasada_flow_ids = set()
        for t in pasadas:
            gen = gens.get(t.get("generator"))
            fl = flows.get(t.get("flow"))
            ce = _Central(
                kind="pasada",
                num=self._new_num((gen or {}).get("uid", t.get("uid"))),
                name=self._new_name((gen or t).get("name")),
                turbine=t,
            )
            if gen is not None:
                self.hydro_gen_ids.add(id(gen))
                self._gen_limits(ce, gen)
            pf = c.field("Turbine", t, "production_factor", default=1.0)
            ce.rendi = float(pf.flat[0])
            if fl is not None:
                self._pasada_flow_ids.add(id(fl))
                q = np.nan_to_num(c.field("Flow", fl, "discharge", default=0.0))
                self._set_inflow(ce, q)
            ce.vertmax = NO_LIMIT
            self.centrals.append(ce)
        _ = g

    def _set_inflow(self, ce: _Central, q: np.ndarray) -> None:
        if np.allclose(q, q.flat[0]):
            ce.afluen = float(q.flat[0])
        else:
            ce.inflow = q

    def _hydro_arcs(self, by_junction: dict[int, _Central]) -> None:
        c = self.case
        jref = self._jref
        seep_ww = {s.get("waterway") for s in c.elements("reservoir_seepage_array")}
        wws = c.elements("waterway_array")
        out_of: dict[int, list[dict[str, Any]]] = {}
        for ww in wws:
            ja = jref.get(ww.get("junction_a"))
            if ja is None:
                self.warn(f"waterway {ww.get('name')}: unknown junction; dropped")
                continue
            out_of.setdefault(id(ja), []).append(ww)
        for jid, ce in by_junction.items():
            arcs = out_of.get(jid, [])
            seep = [
                w for w in arcs if w.get("name") in seep_ww or w.get("uid") in seep_ww
            ]
            rest = [w for w in arcs if w not in seep]
            spill = next((w for w in rest if "_ver_" in str(w.get("name", ""))), None)
            if spill is None and ce.turbine is not None:
                spill = rest[0] if rest else None
            if spill is None and ce.turbine is None and rest:
                # a plant without turbine: its first arc is the outlet (SerHid needs a
                # turbine), so use it as the spill, unlimited unless bounded.
                spill = rest[0]
            j = ce.junction or {}
            if spill is not None:
                rest.remove(spill)
                fmin = c.field("Waterway", spill, "fmin", default=0.0)
                fmax = c.field("Waterway", spill, "fmax", default=np.nan)
                ce.server = spill.get("junction_b")
                ce.vertmin = float(np.nanmax(fmin)) if not np.isnan(fmin).all() else 0.0
                ce.vertmax = (
                    NO_LIMIT
                    if np.isnan(fmax).all()
                    else min(float(np.nanmax(fmax)), NO_LIMIT)
                )
                fc = spill.get("fcost")
                if isinstance(fc, (int, float)):
                    self.spill_costs.add(float(fc))
                if j.get("drain"):
                    self.warn(
                        f"junction {j.get('name')}: has both a spill waterway and a drain; drain "
                        "dropped"
                    )
            elif j.get("drain"):
                cap = j.get("drain_capacity")
                ce.vertmax = float(cap) if isinstance(cap, (int, float)) else NO_LIMIT
                if j.get("drain_cost"):
                    self.spill_costs.add(float(j["drain_cost"]))
            else:
                ce.vertmax = 0.0
            for w in rest:
                fmax = c.field("Waterway", w, "fmax", default=np.nan)
                fm = NO_LIMIT if np.isnan(fmax).all() else float(np.nanmax(fmax))
                if (
                    w.get("fcost")
                    or np.nanmax(c.field("Waterway", w, "fmin", default=0.0)) > 0
                ):
                    self.warn(
                        f"waterway {w.get('name')}: fmin/fcost are lost (plpextrac carries fmax "
                        "only)"
                    )
                self.extrac.append((ce.name, fm, w.get("junction_b")))
            for w in seep:
                rs = [
                    s
                    for s in c.elements("reservoir_seepage_array")
                    if s.get("waterway") in (w.get("name"), w.get("uid"))
                ]
                self.filemb.append(
                    {"central": ce, "to": w.get("junction_b"), "seepage": rs[0]}
                )
        for pf in c.elements("reservoir_production_factor_array"):
            self.cenre.append(pf)

    def _hydro_inflows(self, by_junction: dict[int, _Central]) -> None:
        c = self.case
        for f in c.elements("flow_array"):
            j = self._jref.get(f.get("junction"))
            if j is None:
                continue  # pasada flows are handled with their turbine
            direction = int(f.get("direction", 1) or 1)
            q = np.nan_to_num(c.field("Flow", f, "discharge", default=0.0))
            if direction < 0:
                self.warn(
                    f"flow {f.get('name')}: withdrawals (direction -1) are not exported"
                )
                continue
            ce = by_junction[id(j)]
            total = (
                ce.inflow if ce.inflow is not None else np.full_like(q, ce.afluen)
            ) + q
            self._set_inflow(ce, total)

    def _reservoirs(self, by_junction: dict[int, _Central]) -> None:
        c = self.case
        for ce in by_junction.values():
            r = ce.reservoir
            if r is None:
                continue
            fcr = r.get("flow_conversion_rate", 0.0036)
            if not math.isclose(float(fcr), 0.0036, rel_tol=1e-9):
                self.warn(
                    f"reservoir {r.get('name')}: flow_conversion_rate {fcr} != 0.0036; PLP uses hm3"
                )
            emin = np.nan_to_num(c.field("Reservoir", r, "emin", default=0.0))
            emax = c.field("Reservoir", r, "emax", default=np.nan)
            cap = r.get("capacity")
            emax = np.where(
                np.isnan(emax),
                float(cap) if isinstance(cap, (int, float)) else 0.0,
                emax,
            )
            r_st_min = per_stage(c.grid, emin, "min")[0]
            r_st_max = per_stage(c.grid, emax, "max")[0]
            vmax = float(r_st_max.max())
            vmin = float(r_st_min[0])
            eini = r.get("eini")
            efin = r.get("efin")
            ce.vols = (
                vmin if eini is None else float(eini),
                0.0 if efin is None else float(efin),
                vmin,
                vmax,
            )
            ce.reservoir = {**r, "_emin_st": r_st_min, "_emax_st": r_st_max}
            for extra in ("soft_emin", "efin_cost", "spillway_capacity"):
                if r.get(extra) not in (None, 0, 0.0):
                    self.warn(f"reservoir {r.get('name')}: {extra} is not exported")

    def _collect_generators(self) -> None:
        c = self.case
        profiles: dict[Any, dict[str, Any]] = {}
        for p in c.elements("generator_profile_array"):
            profiles[p.get("generator")] = {**p, "_cls": "GeneratorProfile"}
        for p in c.elements("capacity_profile_array"):
            if p.get("owner_kind", "generator") == "generator":
                profiles[p.get("owner")] = {**p, "_cls": "GeneratorProfile"}
        thermals: list[_Central] = []
        for gen in c.elements("generator_array"):
            if id(gen) in self.hydro_gen_ids:
                continue
            prof = profiles.get(gen.get("name"), profiles.get(gen.get("uid")))
            if prof is not None:
                ce = _Central(
                    kind="pasada",
                    num=self._new_num(gen.get("uid")),
                    name=self._new_name(gen.get("name")),
                )
                self._gen_limits(ce, gen)
                pr = np.nan_to_num(
                    c.field(prof["_cls"], prof, "profile", default=1.0), nan=1.0
                )
                cap = gen.get("capacity")
                capv = float(cap) if isinstance(cap, (int, float)) else ce.potmax
                limits = ce.generator or {}
                pmax = limits["_pmax"]
                avail = np.minimum(pmax[None, :], pr * capv)
                ce.potmax = float(max(capv, avail.max()))
                limits["_pmax"] = np.full_like(pmax, ce.potmax)
                ce.rendi = 1.0
                self._set_inflow(ce, avail)
                if ce.inflow is None:
                    ce.inflow = np.full_like(avail, ce.afluen)
                    ce.afluen = 0.0
                ce.centipo = CENTIPO.get(str(gen.get("type", "")).lower(), "SOL")
                self.centrals.append(ce)
            else:
                ce = _Central(
                    kind="termica",
                    num=self._new_num(gen.get("uid")),
                    name=self._new_name(gen.get("name")),
                )
                self._gen_limits(ce, gen)
                thermals.append(ce)
        # plpcnfce order: embalse, serie, pasada, termica
        self.centrals.sort(key=lambda x: ["embalse", "serie", "pasada"].index(x.kind))
        self.centrals.extend(thermals)

    def _collect_batteries(self) -> None:
        c = self.case
        for bat in c.elements("battery_array"):
            ce = _Central(
                kind="bateria",
                num=self._new_num(bat.get("uid")),
                name=self._new_name(bat.get("name")),
                battery=bat,
            )
            ce.bus = self._bus_uid(bat.get("bus"))
            if ce.bus == 0:
                self.warn(
                    f"battery {ce.name}: no bus (converter-based batteries are not exported)"
                )
                continue
            pc = float(np.nanmax(c.field("Battery", bat, "pmax_charge", default=0.0)))
            pd = float(
                np.nanmax(c.field("Battery", bat, "pmax_discharge", default=0.0))
            )
            if not math.isclose(pc, pd):
                self.warn(
                    f"battery {ce.name}: charge and discharge limits differ; PLP uses the max"
                )
            ce.potmax = max(pc, pd)
            for extra in ("eini", "efin", "discharge_cost"):
                if bat.get(extra) not in (None, 0, 0.0):
                    self.warn(f"battery {ce.name}: {extra} is not exported")
            if float(np.nanmax(c.field("Battery", bat, "emin", default=0.0))) > 0:
                self.warn(
                    f"battery {ce.name}: emin > 0 is not exported (plpess has no emin)"
                )
            self.centrals.append(ce)

    def _collect_fallas(self) -> None:
        c = self.case
        mo = c.options.get("model_options", {}) or {}
        default = mo.get("demand_fail_cost", c.options.get("demand_fail_cost"))
        default = float(default) if default else 1000.0
        self.demand_bus = {}
        fcost_bus: dict[int, float] = {}
        for d in c.elements("demand_array"):
            bus = self._bus_uid(d.get("bus"))
            if bus == 0:
                self.warn(f"demand {d.get('name')}: unknown bus; dropped")
                continue
            lmax = np.nan_to_num(
                self._first(
                    c.field("Demand", d, "lmax", default=0.0), f"demand {d.get('name')}"
                )
            )
            if bus in self.demand_bus:
                self.warn(
                    "several demands on one bus are merged (PLP has one demand per bus)"
                )
            self.demand_bus[bus] = self.demand_bus.get(bus, 0.0) + lmax
            fc = d.get("fcost")
            fc = float(fc) if isinstance(fc, (int, float)) and fc else default
            if bus in fcost_bus and not math.isclose(fcost_bus[bus], fc):
                self.warn(
                    f"bus {bus}: demands with different fail costs; the lowest is kept"
                )
            fcost_bus[bus] = min(fc, fcost_bus.get(bus, fc))
            for extra in ("emin", "ecost"):
                if d.get(extra) not in (None, 0, 0.0):
                    self.warn(f"demand {d.get('name')}: {extra} is not exported")
        for bus in self.demand_bus:
            ce = _Central(
                kind="falla",
                num=self._new_num(None),
                name=self._new_name(f"FALLA_{self.bus_names.get(bus, bus)}"),
            )
            ce.bus, ce.potmax, ce.cosvar = bus, NO_LIMIT, fcost_bus[bus]
            self.centrals.append(ce)

    # ── files ──────────────────────────────────────────────────────────
    def _mes_of_block(self, b: int) -> int:
        return hydro_month(self.months[int(self.sob[b])])

    def _write_blocks(self) -> None:
        g = self.case.grid
        lines = [
            "# Archivo con la duracion de los bloques",
            "# Bloques",
            f"  {g.n_blocks}",
            "# Bloque   Etapa   NHoras  Ano   Mes  TipoBloque",
        ]
        years = self._year_index()
        for b in range(g.n_blocks):
            k = int(self.sob[b])
            j = b - g.stages[k].first_block + 1
            lines.append(
                f"  {b + 1:04d}   {k + 1:04d}  {_num(g.durations[b])}  {years[k]:03d}  "
                f"{hydro_month(self.months[k]):03d}  'Bloque {j:02d}'"
            )
        self._write("plpblo.dat", lines)
        eta = [
            "# Archivo con la duracion de las etapas",
            "# Etapas",
            f"  {g.n_stages}   'H'",
            "# Ano  Mes  Etapa FDesh   NHoras    FactTasa    TipoEtapa",
        ]
        for k, st in enumerate(g.stages):
            hours = sum(g.durations[b] for b in st.blocks)
            fact = 1.0 / st.discount_factor if st.discount_factor else 0.0
            eta.append(
                f"  {years[k]:03d}  {hydro_month(self.months[k]):03d}  {k + 1:04d}  F  "
                f"{_num(hours)}  {fact!r}  '{st.count_block:02d} Bloques'"
            )
        self._write("plpeta.dat", eta)

    def _year_index(self) -> list[int]:
        out, year = [], 1
        for k, m in enumerate(self.months):
            if k and hydro_month(m) <= hydro_month(self.months[k - 1]):
                year += 1
            out.append(year)
        return out

    def _write_buses(self) -> None:
        lines = [
            "# Archivo con definicion de Barras (plpbar.dat)",
            "# Numero de Barras",
            f"  {len(self.buses)}",
            "# Numero       Nombre",
        ]
        for b in self.buses:
            u = int(b["uid"])
            if u <= 0:
                self.warn(f"bus {b.get('name')}: uid <= 0 is dropped by PLP")
            name = self.bus_names[u]
            v = b.get("voltage")
            if isinstance(v, (int, float)) and v > 0 and not re.search(r"\d", name):
                self.warn(
                    f"bus {name}: voltage is taken from the incident lines (PLP has no bus kV "
                    "field)"
                )
            lines.append(f"  {u:5d}   '{name}'")
        self._write("plpbar.dat", lines)

    def _write_lines(self) -> None:
        c = self.case
        g = c.grid
        lines_ = c.elements("line_array")
        modes = [ln.get("loss_allocation_mode", "receiver") for ln in lines_]
        mode = max(set(modes), key=modes.count) if modes else "receiver"
        if len(set(modes)) > 1:
            self.warn(
                f"lines use several loss allocation modes; PLP has one ({mode} kept)"
            )
        code = {"sender": "E", "receiver": "R", "split": "M"}.get(mode, "R")
        lossy = [
            bool(ln.get("use_line_losses"))
            and ln.get("resistance") not in (None, 0, 0.0)
            for ln in lines_
        ]
        out = [
            "# Archivo de configuracion de lineas (plpcnfli.dat)",
            "# Num.Lineas   Modela Perdidas  Perd.en.ERM   Ang. de Ref.",
            f"  {len(lines_)}  {'T' if any(lossy) else 'F'}  '{code}'  1000.d0",
            "# Nombre  F.Max.A-B  F.Max.B-A  Barra A  Barra B  Tension  R(Ohm)  X(ohm)  "
            "Mod.Perd.  Num.Tramos  Operativa   LinDC",
        ]
        self.manli = []
        for i, (ln, name) in enumerate(zip(lines_, line_names(lines_), strict=True)):
            a, b = self._bus_uid(ln.get("bus_a")), self._bus_uid(ln.get("bus_b"))
            tab = self._first(
                c.field("Line", ln, "tmax_ab", default=NO_LIMIT), f"{name} tmax_ab"
            )
            tba = self._first(
                c.field("Line", ln, "tmax_ba", default=NO_LIMIT), f"{name} tmax_ba"
            )
            act = (
                self._first(
                    c.field("Line", ln, "active", default=1.0), f"{name} active"
                )
                > 0.5
            )
            for key in ("reactance", "resistance", "voltage"):
                if isinstance(ln.get(key), (str, list)):
                    self.warn(
                        f"line {name}: time-varying {key} is not exported (first value kept)"
                    )
            x = float(c.field("Line", ln, "reactance", default=0.0).flat[0])
            r = float(c.field("Line", ln, "resistance", default=0.0).flat[0])
            # gtopt reads a line without voltage in per-unit (V = 1.0); PLP
            # Tension 0 would make V = 0 and drop the line from Kirchhoff.
            v = float(c.field("Line", ln, "voltage", default=1.0).flat[0])
            dc = "reactance" not in ln
            if not dc and x == 0:
                self.warn(f"line {name}: reactance 0 is read back by PLP as a DC line")
            segs = int(ln.get("loss_segments", 2) or 2)
            tramos = max(1, math.ceil(segs / 2)) if lossy[i] else 1
            base = (float(tab.max()), float(tba.max()), bool(act[0]))
            out.append(
                f"'{name}'  {_num(base[0])}  {_num(base[1])}  {a}  {b}  {_num(v)}  {_num(r)}  "
                f"{_num(x)}  "
                f"{'T' if lossy[i] else 'F'}  {tramos}  {'T' if base[2] else 'F'}  "
                f"{'T' if dc else 'F'}"
            )
            rows = [
                f"  {bb + 1:04d}  {_num(tab[bb])}  {_num(tba[bb])}  {'T' if act[bb] else 'F'}"
                for bb in range(g.n_blocks)
                if (tab[bb], tba[bb], bool(act[bb])) != base
            ]
            if rows:
                self.manli.append((name, rows))
            if ln.get("tcost") not in (None, 0, 0.0):
                self.warn(f"line {name}: tcost is not exported")
        self._write("plpcnfli.dat", out)
        ml = [
            "# Archivo de mantenimientos de lineas (plpmanli.dat)",
            "# numero de lineas con matenimientos",
            f" {len(self.manli)}",
        ]
        for name, rows in self.manli:
            ml += [
                "# Nombre de la linea",
                f"'{name}'",
                "# Numero de bloques con mantenimiento",
                f"   {len(rows)}",
                "# Bloque  F.Max.A-B  F.Max.B-A  Operativa",
                *rows,
            ]
        self._write("plpmanli.dat", ml)

    def _write_centrals(self) -> None:
        kinds = [ce.kind for ce in self.centrals]
        n = len(self.centrals)
        head = (
            "                                                  IPot MinTec  Inter   FCAD    "
            "Cen_MTTdHrz Hid_Indep  Cen_NEtaArr Cen_NEtaDet"
        )
        lines = [
            "# Archivo de configuracion de las centrales (plpcnfce.dat)",
            "# Num.Centrales  Num.Embalses Num.Serie Num.Fallas Num.Pas.Pur. Num.BAT",
            f"     {n}   {kinds.count('embalse')}   {kinds.count('serie')}   "
            f"{kinds.count('falla')}   "
            f"{kinds.count('pasada')}   {kinds.count('bateria')}",
            "# Interm Min.Tec. Cos.Arr.Det. FFaseSinMT EtapaCambioFase",
            "  F      F        F            F          00",
        ]
        if n == 0:
            raise ValueError(
                "PLP needs at least one central (no generators, hydro, batteries or demands)"
            )
        titles = {
            "embalse": "# Centrales de Embalse",
            "serie": "# Centrales Serie Hidraulica",
            "pasada": "# Centrales Pasada Puras",
            "termica": "# Centrales Termicas",
            "bateria": "# Baterias",
            "falla": "# Fallas",
        }
        last = None
        for ce in self.centrals:
            if ce.kind != last:
                lines.append(titles[ce.kind])
                last = ce.kind
            serhid = self._central_num(ce.serhid)
            server = self._central_num(ce.server)
            lines += [
                head,
                f"    {ce.num} '{ce.name}'  1  F  F  F  F  F  0  0",
                "          PotMin PotMax VertMin VertMax",
                f"           {_num(ce.potmin)}  {_num(ce.potmax)}  {_num(ce.vertmin)}  "
                f"{_num(ce.vertmax)}",
                "           Start   Stop ON(t<0) NEta_OnOff",
                "             0.0    0.0 F       0",
            ]
            if ce.kind == "embalse" and ce.vols is not None:
                ini, fin, vmin, vmax = ce.vols
                lines += [
                    "          CosVar  Rendi  Barra Genera Vertim    t<0  Afluen    Inicial      "
                    "Final     Minimo     Maximo  Escala EmbCFUE",
                    f"           {_num(ce.cosvar)}  {_num(ce.rendi)}  {ce.bus}  {serhid}  "
                    f"{server}  0.0  {_num(ce.afluen)}  "
                    f"{_num(ini)}  {_num(fin)}  {_num(vmin)}  {_num(vmax)}  1.0E+6  T",
                ]
            else:
                lines += [
                    "          CosVar  Rendi  Barra SerHid SerVer    t<0  Afluen",
                    f"           {_num(ce.cosvar)}  {_num(ce.rendi)}  {ce.bus}  {serhid}  "
                    f"{server}  0.0  {_num(ce.afluen)}",
                ]
        self._write("plpcnfce.dat", lines)
        tipos = [(ce.name, ce.centipo) for ce in self.centrals if ce.centipo]
        if tipos:
            self._write(
                "centipo.csv", ["Central,Tipo_T", *(f"{n_},{t}" for n_, t in tipos)]
            )

    def _central_num(self, jref: Any) -> int:
        if jref is None:
            return 0
        j = self._jref.get(jref)
        if j is None:
            self.warn(f"junction {jref!r} unknown; arc sent to the sea")
            return 0
        return self._junction_central[id(j)].num

    def _write_demands(self) -> None:
        g = self.case.grid
        lines = [
            "# Archivo de demandas por barra (plpdem.dat)",
            "# Numero de barras",
            f"{len(self.demand_bus)}",
        ]
        for bus, lmax in self.demand_bus.items():
            lines += [
                "# Nombre de la Barra",
                f"'{self.bus_names[bus]}'",
                "# Numero de Demandas",
                f"{g.n_blocks}",
                "# Mes  Bloque   Demanda",
            ]
            lines += [
                f"   {self._mes_of_block(b):02d}    {b + 1:04d}    {_num(lmax[b])}"
                for b in range(g.n_blocks)
            ]
        self._write("plpdem.dat", lines)

    def _write_costs(self) -> None:
        g = self.case.grid
        rows = []
        for ce in self.centrals:
            st = (ce.generator or {}).get("_gcost_stage")
            if st is None or np.allclose(st, st[0]):
                continue
            rows.append((ce.name, st))
        lines = [
            "# Archivo de precios de termicas (plpcosce.dat)",
            "# Numero de centrales con cambio de costo variable",
            f" {len(rows)}",
        ]
        for name, st in rows:
            lines += [
                "# Nombre de la central",
                f"'{name}'",
                "# Numero de etapas",
                f"   {g.n_stages}",
                "# Mes   Etapa    CosVar",
            ]
            lines += [
                f"   {hydro_month(self.months[k]):02d}     {k + 1:04d}     {_num(st[k])}"
                for k in range(g.n_stages)
            ]
        self._write("plpcosce.dat", lines)

    def _write_mance(self) -> None:
        g = self.case.grid
        entries = []
        for ce in self.centrals:
            gen = ce.generator
            if gen is None:
                continue
            pmax, pmin = gen["_pmax"], gen["_pmin"]
            rows = [
                f"     {self._mes_of_block(b):02d}      {b + 1:04d}        1     {_num(pmin[b])}  "
                f"  {_num(pmax[b])}"
                for b in range(g.n_blocks)
                if not (
                    math.isclose(pmax[b], ce.potmax)
                    and math.isclose(pmin[b], ce.potmin)
                )
            ]
            if rows:
                entries.append((ce.name, rows))
        lines = [
            "# Archivo de mantenimientos de centrales (plpmance.dat)",
            "# numero de centrales con matenimientos",
            f" {len(entries)}",
        ]
        for name, rows in entries:
            lines += [
                "# Nombre de la central",
                f"'{name}'",
                "#   Numero de Bloques e Intervalos",
                f"   {len(rows)}                 01",
                "#   Mes    Bloque  NIntPot   PotMin   PotMax",
                *rows,
            ]
        self._write("plpmance.dat", lines)

    def _write_aflce(self) -> None:
        g = self.case.grid
        recs = [ce for ce in self.centrals if ce.inflow is not None]
        ns = g.n_scenarios
        head = "  ".join(f"Hidro{i + 1}" for i in range(ns))
        lines = [
            "# Archivo de caudales por etapa (plpaflce.dat)",
            "# Nro. Cent. c/Caudales Estoc. y Nro. Hidrologias",
            f"  {len(recs)}  {ns}",
        ]
        for ce in recs:
            q = ce.inflow
            assert q is not None
            if q.shape[0] != ns:
                q = np.repeat(q[:1], ns, axis=0)
            lines += [
                "# Nombre de la central",
                f"'{ce.name}'",
                "# Numero de Bloques con caudales",
                f"  {g.n_blocks}",
                f"# Mes  Bloque  {head}",
            ]
            lines += [
                f"  {self._mes_of_block(b):03d}  {b + 1:04d}  "
                + "  ".join(_num(q[s, b]) for s in range(ns))
                for b in range(g.n_blocks)
            ]
        self._write("plpaflce.dat", lines)

    def _write_extrac(self) -> None:
        lines = [
            "# Archivo de Extracciones (plpextrac.dat)",
            "# Numero Centrales con extraccion",
            f"{len(self.extrac)}",
        ]
        for name, fmax, to in self.extrac:
            to_num = self._central_num(to)
            to_name = next((ce.name for ce in self.centrals if ce.num == to_num), None)
            if to_name is None:
                self.warn(f"extraction from {name}: no downstream central; dropped")
                continue
            lines += [
                "# Nombre de central de extraccion",
                f"'{name}'",
                "# Maxima Extraccion (m3/seg)",
                _num(fmax),
                "# Nombre de la Central aguas abajo",
                f"'{to_name}'",
            ]
        self._write("plpextrac.dat", lines)

    def _write_manem(self) -> None:
        g = self.case.grid
        entries = []
        for ce in self.centrals:
            r = ce.reservoir
            if r is None or ce.vols is None:
                continue
            emin_st, emax_st = r["_emin_st"], r["_emax_st"]
            if np.allclose(emin_st, ce.vols[2]) and np.allclose(emax_st, ce.vols[3]):
                continue
            rows = [
                f"     00     {k + 1:04d}  {_num(emin_st[k])}  {_num(emax_st[k])}"
                for k in range(g.n_stages)
            ]
            entries.append((ce.name, rows))
        lines = [
            "# Archivo de mantenimientos embalses (plpmanem.dat)",
            "# Numero de embalses con mantenimientos",
            f" {len(entries)}",
        ]
        for name, rows in entries:
            lines += [
                "# Nombre del embalse",
                f"'{name}'",
                "#   Numero de Etapas con mantenimiento",
                f"   {len(rows)}",
                "#   Mes   Etapa     VolMin     VolMax",
                *rows,
            ]
        self._write("plpmanem.dat", lines)

    def _write_ess(self) -> None:
        bats = [ce for ce in self.centrals if ce.kind == "bateria"]
        if not bats:
            return
        lines = [
            "# Archivo de configuracion de ESS (plpess.dat)",
            "# Numero de ESS",
            f" {len(bats)}",
            "# Nombre  nd    nc    mloss  Emax   DCMax  DCMod  CenCarga",
        ]
        for ce in bats:
            bat = ce.battery or {}
            emax = float(
                np.nanmax(
                    self.case.field(
                        "Battery",
                        bat,
                        "emax",
                        default=float(bat.get("capacity", 0.0) or 0.0),
                    )
                )
            )
            nd = float(bat.get("output_efficiency", 1.0) or 1.0)
            nc = float(bat.get("input_efficiency", 1.0) or 1.0)
            asl = bat.get("annual_standing_loss")
            mloss = 100.0 * (1.0 - math.exp(-float(asl) / 12.0)) if asl else 0.0
            lines.append(
                f"'{ce.name}'  {_num(nd)}  {_num(nc)}  {_num(mloss)}  {_num(emax)}  "
                f"{_num(ce.potmax)}  0"
            )
        self._write("plpess.dat", lines)

    def _write_hydro_extras(self) -> None:
        names = {
            id(ce.reservoir): ce.name
            for ce in self.centrals
            if ce.reservoir is not None
        }
        res_name = {}
        for ce in self.centrals:
            if ce.reservoir is not None:
                res_name[ce.reservoir.get("name")] = ce.name
                res_name[ce.reservoir.get("uid")] = ce.name
        turb_name = {}
        for ce in self.centrals:
            if ce.turbine is not None:
                turb_name[ce.turbine.get("name")] = ce.name
                turb_name[ce.turbine.get("uid")] = ce.name
        _ = names
        if self.cenre:
            lines = [
                "# Archivo de Rendimiento de Embalses (plpcenre.dat)",
                "# Numero de Embalses con Rendimiento",
                f" {len(self.cenre)}",
            ]
            for pf in self.cenre:
                segs = pf.get("segments") or []
                lines += [
                    "# Nombre de Central",
                    f"'{turb_name.get(pf.get('turbine'), pf.get('turbine'))}'",
                    "# Nombre del Embalse",
                    f"'{res_name.get(pf.get('reservoir'), pf.get('reservoir'))}'",
                    "# Rendimiento Medio",
                    _num(pf.get("mean_production_factor", 1.0)),
                    "# Numero de Tramos",
                    f" {len(segs)}",
                    "#Tramo      Volumen     Pendiente    Constante  F.Escala",
                ]
                lines += [
                    f"  {i + 1}  {_num(s.get('volume', 0.0))}  {_num(s.get('slope', 0.0))}  "
                    f"{_num(s.get('constant', 0.0))}  1.0E+6"
                    for i, s in enumerate(segs)
                ]
            self._write("plpcenre.dat", lines)
        if self.filemb:
            lines = [
                "# Archivo de filtraciones de embalses (plpfilemb.dat)",
                "# Numero de embalses con filtraciones",
                f" {len(self.filemb)}",
            ]
            for fe in self.filemb:
                s = fe["seepage"]
                segs = s.get("segments") or [
                    {
                        "volume": 0.0,
                        "slope": s.get("slope", 0.0),
                        "constant": s.get("constant", 0.0),
                    }
                ]
                to_num = self._central_num(fe["to"])
                to_name = next(
                    (ce.name for ce in self.centrals if ce.num == to_num), ""
                )
                mean = float(s.get("constant", 0.0) or 0.0)
                lines += [
                    "# Nombre del embalse",
                    f"'{fe['central'].name}'",
                    "# Filtracion media (m3/s)",
                    _num(mean),
                    "# Numero de tramos",
                    f" {len(segs)}",
                    "# Tramo  Volumen  Pendiente  Constante",
                ]
                lines += [
                    f"  {i + 1}  {_num(x.get('volume', 0.0))}  {_num(x.get('slope', 0.0))}  "
                    f"{_num(x.get('constant', 0.0))}"
                    for i, x in enumerate(segs)
                ]
                lines += ["# Central que recibe la filtracion", f"'{to_name}'"]
            self._write("plpfilemb.dat", lines)

    def _write_plpmat(self) -> None:
        if len(self.spill_costs) > 1:
            self.warn(
                "spill costs differ by plant; PLP has one CVert (the largest is used)"
            )
        cvert = max(self.spill_costs) if self.spill_costs else 0.0
        lines = [
            "# Archivo con Parametros Matematicos (plpmat.dat)",
            "# PDMaxIte    PDError UmbIntConf NPlanosPorDefecto",
            "    0         0.001       0.001                50",
            "# PMMaxIte    PMError",
            "    10            5.0",
            "# Lambda CTasa CCauFal  CVert CInter Ctransm FCotFinEF FPreProc FPrevia",
            f"    0.00   0.0  7000.0   {_num(cvert)}   0.01    0.01         F        F       F",
            "# FFixTrasm  FSeparaFCF  FGrabaCSV   FGrabaRES",
            "  T          F           T           F",
            "# ABLMax   ABLEpsilon NumEtaCF",
            "    20        0.001         1",
            "# FConvPGradx FConvPVar UmbGradX UmbVar",
            "  F           F         0.5       100",
        ]
        self._write("plpmat.dat", lines)
