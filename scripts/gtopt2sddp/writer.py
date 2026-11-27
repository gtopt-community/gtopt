# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Write a gtopt planning as a PSR SDDP block study (``.dat`` dialect).

The inverse of :mod:`sddp2gtopt` (path A, ``sddp_case`` +
``sddp_case_writer``): the files written here are the ones that reader
parses, in the layouts it accepts (CSV where it reads CSV, header-driven
fixed width elsewhere, see :mod:`gtopt2sddp.fixed_width`).

Mapping (gtopt -> PSR):

* stages -> consecutive calendar months from ``start_year/start_month``;
  block hours -> ``duvame<NB>.dat`` (every stage needs the same block
  count); scenarios -> historical series (``CAUDALES 2``), scenario ``s``
  reading hydrology year ``hydro_year + s - 1``.
* Bus -> ``dbus.dat`` (code = bus uid) + ``volt.dat``.
* Line -> ``dcirc.dat`` (R/X in % on 100 MVA at the higher end voltage,
  rating = max(tmax_ab, tmax_ba)); rating changes and late entries ->
  ``mcirc.dat``.
* Generator driven by a Turbine -> hydro plant of its junction
  (``chidro``); Generator with a profile -> renewable (``cgnd`` +
  ``gndsite`` + ``blrenw``); any other Generator -> thermal (``ctermi``,
  cost in ``CVaria``, stage changes in ``mtermi``, per-block pmax caps
  in ``rgme``).
* Junction -> hydro plant (``chidro``): reservoir (VMin/VMax/VInic),
  turbine (FPMed, QMax, ``TAA`` = downstream plant), spill waterway
  (``VAA``), inflows (sum of the junction's Flows) -> ``hinflw.dat``.
* Demand -> one load class per bus (``deme<NB>`` in GWh + ``dbf<NB>``).
* Battery -> ``cbattery.dat``.

What PSR cannot express is reported through :attr:`SddpWriter.warnings`
(and the log): see ``DESIGN`` notes in the package README.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from gtopt_shared.case_reader import GtoptCase, per_stage
from gtopt2sddp.fixed_width import NAME_WIDTH, Column, fmt_fixed7, header, record

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

TECH_CODES = {
    "wind": 1,
    "solar": 2,
    "biomass": 3,
    "small_hydro": 4,
    "hydro_ror": 4,
    "csp": 5,
}

DEFAULT_KV = 100.0
HM3_PER_M3S_H = 0.0036


def _sanitize(name: str) -> str:
    return re.sub(r"[\s,]+", "_", str(name)).strip("_") or "X"


class _Names:
    """Unique PSR names (at most ``width`` characters) with a back map."""

    def __init__(self, width: int = NAME_WIDTH) -> None:
        self.width = width
        self.used: set[str] = set()
        self.map: dict[str, str] = {}

    def get(self, original: str) -> str:
        base = _sanitize(original)[: self.width]
        name = base
        k = 1
        while name in self.used:
            k += 1
            suffix = f"_{k}"
            name = base[: self.width - len(suffix)] + suffix
        self.used.add(name)
        if name != original:
            self.map[name] = original
        return name


@dataclass
class _Plant:
    """One hydro plant (one gtopt junction)."""

    num: int
    name: str
    junction: dict[str, Any]
    reservoir: dict[str, Any] | None = None
    turbine: dict[str, Any] | None = None
    generator: dict[str, Any] | None = None
    turbine_to: Any = None  # junction ref (TAA)
    spill_to: Any = None  # junction ref (VAA)
    qmax: float = 0.0
    station: int = 0
    inflow: np.ndarray | None = None  # (n_scen, n_stage)


@dataclass
class SddpWriter:
    """Export a :class:`GtoptCase` as a PSR SDDP block study."""

    case: GtoptCase
    out_dir: Path
    sfx: str = "gt"
    start_year: int = 2030
    start_month: int | None = None
    hydro_year: int = 2000
    title: str = "gtopt2sddp export"
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Declare the state :meth:`write` fills (typed empty defaults)."""
        self.nb: int = 0
        self.dates: list[tuple[int, int]] = []
        self.stage_of_block: np.ndarray = np.zeros(0, dtype=np.int64)
        self._buses: dict[Any, dict[str, Any]] = {}
        self.names: _Names = _Names()
        self.bus_names: dict[int, str] = {}
        self.profile_of: dict[int, dict[str, Any]] = {}
        self.busconn: list[tuple[int, str, int]] = []
        self.rgme: list[list[str]] = []
        self.use_losses: bool = False
        # (kind, gtopt uid) -> PSR agent name, for the results export
        # (kinds: bus, thermal, hydro (junction uid), renewable, line,
        # battery); ``plants`` keeps the hydro plants as written.
        self.agents: dict[tuple[str, int], str] = {}
        self.plants: list[_Plant] = []

    # ── helpers ────────────────────────────────────────────────────────
    def warn(self, msg: str) -> None:
        """Record and log a lossy-conversion warning (once per text)."""
        if msg not in self.warnings:
            self.warnings.append(msg)
            logger.warning("%s", msg)

    def _write(self, name: str, lines: list[str]) -> None:
        path = self.out_dir / name
        path.write_text("\n".join(lines) + "\n", encoding="latin-1", errors="replace")

    def _stage_dates(self) -> list[tuple[int, int]]:
        """(year, month) of every stage: consecutive months."""
        g = self.case.grid
        month = self.start_month
        if month is None:
            first = g.stages[0].month if g.stages else None
            month = (
                MONTHS.index(first.lower()) + 1
                if first and first.lower() in MONTHS
                else 1
            )
        out = []
        y, m = self.start_year, month
        for st in g.stages:
            if st.month and st.month.lower() in MONTHS:
                want = MONTHS.index(st.month.lower()) + 1
                if want != m:
                    self.warn(
                        f"stage {st.uid} is {st.month} but PSR stages are consecutive "
                        f"months; written as {y}-{m:02d}"
                    )
            out.append((y, m))
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out

    def _bus_uid(self, ref: Any) -> int | None:
        bus = self._buses.get(ref)
        return None if bus is None else int(bus["uid"])

    def _first_scenario(self, arr: np.ndarray, what: str) -> np.ndarray:
        """Row of scenario 1, warning when scenarios differ."""
        if arr.shape[0] > 1 and not np.allclose(arr, arr[0], equal_nan=True):
            self.warn(f"{what} varies by scenario; PSR keeps scenario 1 only")
        return np.asarray(arr[0])

    def _monthly_table(
        self,
        title: str | None,
        values: dict[tuple[int, int, int | None], float],
        *,
        per_block: bool,
        width: int = 14,
    ) -> list[str]:
        """One ``parse_monthly_file`` table: ``{(year, month, block): v}``."""
        cols = [Column(str(m), width) for m in range(1, 13)]
        lines = [] if title is None else [title]
        lines.append(("A~no Blk " if per_block else "A~NO ") + header(cols))
        years = sorted({k[0] for k in values})
        nb = self.case.grid.stages[0].count_block if per_block else 1
        for y in years:
            for b in range(1, nb + 1) if per_block else [None]:
                row = [values.get((y, m, b)) for m in range(1, 13)]
                lead = f"{y:4d}{b:4d} " if per_block else f"{y:4d} "
                lines.append(record(cols, row, lead=lead))
        return lines

    # ── main entry ─────────────────────────────────────────────────────
    def write(self) -> Path:
        """Write every file; returns the output directory."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        c = self.case
        g = c.grid
        nbs = {st.count_block for st in g.stages}
        if len(nbs) != 1:
            raise ValueError(
                f"PSR needs the same number of blocks in every stage (got {sorted(nbs)})"
            )
        self.nb = nbs.pop()
        self.dates = self._stage_dates()
        self.stage_of_block = g.stage_of_block()
        if g.n_scenarios > 1:
            probs = np.asarray(g.probabilities)
            if not np.allclose(probs, probs[0]):
                self.warn("scenario probabilities become equal (1/N) in PSR")
            years = {y for y, _ in self.dates}
            if len(self.dates) > 12 and len(years) > 1:
                self.warn(
                    "more than 12 stages with several scenarios: PSR scenario s reads "
                    "hydrology year Y0+s-1+k in stage year k, so consecutive scenarios "
                    "share inflow rows; inflows of later scenarios may be overwritten"
                )
        buses = c.elements("bus_array")
        self._buses = c.ref_index(buses)
        self.names = _Names()
        self.bus_names = {
            int(b["uid"]): self.names.get(b.get("name", f"B{b['uid']}")) for b in buses
        }
        self.agents = {("bus", u): n for u, n in self.bus_names.items()}

        gens = c.elements("generator_array")
        gen_index = c.ref_index(gens)
        turbines = c.elements("turbine_array")
        hydro_gen_ids = {
            id(gen_index[t["generator"]])
            for t in turbines
            if t.get("generator") in gen_index
        }
        profiles = c.elements("generator_profile_array") + [
            p
            for p in c.elements("capacity_profile_array")
            if p.get("owner_kind", "generator") == "generator"
        ]
        self.profile_of = {}
        for p in profiles:
            ref = p.get("generator", p.get("owner"))
            gen = gen_index.get(ref)
            if gen is not None:
                self.profile_of[id(gen)] = p
        thermals = [
            gen
            for gen in gens
            if id(gen) not in hydro_gen_ids and id(gen) not in self.profile_of
        ]
        renewables = [gen for gen in gens if id(gen) in self.profile_of]

        self.busconn = []
        self.rgme = []
        self._write_sddp_dat()
        self._write_sistem()
        self._write_durations()
        self._write_thermals(thermals)
        plants = self._write_hydro(gen_index)
        self.plants = plants
        for plant in plants:
            if plant.junction.get("uid") is not None:
                self.agents["hydro", int(plant.junction["uid"])] = plant.name
        self._write_renewables(renewables)
        self._write_lines()
        self._write_demands()
        self._write_batteries()
        self._write_buses()
        if self.rgme:
            lines: list[str] = []
            for tbl in self.rgme:
                lines.extend(tbl)
            self._write(f"rgme{self.nb:03d}{self.sfx}.dat", lines)
        for array in (
            "reserve_zone_array",
            "user_constraint_array",
            "flow_right_array",
            "volume_right_array",
            "emission_zone_array",
        ):
            if c.elements(array):
                self.warn(f"{array} is not exported (no PSR equivalent here)")
        meta = {
            "source": "gtopt2sddp",
            "names": self.names.map,
            "hydro_plants": {p.name: p.junction.get("name") for p in plants},
            "warnings": self.warnings,
        }
        (self.out_dir / "gtopt2sddp.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return self.out_dir

    # ── control files ──────────────────────────────────────────────────
    def _deficit_cost(self) -> float:
        mo = self.case.options.get("model_options", {}) or {}
        cost = mo.get("demand_fail_cost", self.case.options.get("demand_fail_cost"))
        fcosts = {
            float(d["fcost"])
            for d in self.case.elements("demand_array")
            if isinstance(d.get("fcost"), (int, float))
        }
        if len(fcosts) > 1:
            self.warn(
                "per-demand fail costs differ; PSR has one deficit cost (the largest is used)"
            )
        if fcosts and (not cost or max(fcosts) != cost):
            cost = max(fcosts)
        return float(cost) if cost else 1000.0

    def _spill_vert(self) -> float:
        costs: set[float] = set()
        for j in self.case.elements("junction_array"):
            if j.get("drain") and isinstance(j.get("drain_cost"), (int, float)):
                costs.add(float(j["drain_cost"]))
        for r in self.case.elements("reservoir_array"):
            if isinstance(r.get("spillway_cost"), (int, float)) and r["spillway_cost"]:
                costs.add(float(r["spillway_cost"]))
        if len(costs) > 1:
            self.warn(
                "spill costs differ by plant; PSR has one study-wide spill cost (max used)"
            )
        return max(costs) / 3.6 if costs else 0.0

    def _write_sddp_dat(self) -> None:
        g = self.case.grid
        y0, m0 = self.dates[0]
        rate = float(
            (self.case.planning.get("simulation", {}) or {}).get(
                "annual_discount_rate", 0.0
            )
            or 0.0
        )
        losses = any(
            (ln.get("resistance") not in (None, 0, 0.0))
            and ln.get("use_line_losses", True)
            for ln in self.case.elements("line_array")
        )
        self.use_losses = losses
        vert = self._spill_vert()
        deficit = self._deficit_cost()
        lines = [
            self.title,
            "IDIOMA                       1  0=ING; 1=ESP; 2=POR",
            "OBJETIVO                     2  1=POL; 2=SIM;",
            "ETAPA                        2  1=SEMANAL; 2=MENSUAL; 3=TRIM.",
            "CAUDALES                     2  0=LECT; 1=ARP; 2=HIST; 3=FORW&BACK",
            "REPRESENTACION DE LA RED     1  0=NO;     ; 1=SI;",
            f"PERDAS TRANSMISION           {int(losses)}  0=NO;     ; 1=SI;",
            f"MES/SEMANA INICIAL        {m0:4d}",
            f"ANO INICIAL               {y0:4d}",
            f"NUMERO DE ETAPAS          {g.n_stages:4d}",
            f"MES  COND. INIC. HIDRO    {m0:4d}",
            f"ANO COND. INIC. HIDRO     {self.hydro_year:4d}",
            "NUMERO DE SISTEMAS           1",
            f"NUMERO DE SERIES          {g.n_scenarios:4d}",
            f"NUMERO DE BLOQUES DEMANDA {self.nb:4d} 0",
            f"TASA DE DESCUENTO         {rate:.6g}",
            f"PEN. DEFL. MINIMA           0.  us$/hm3   vert {vert:.10g}  k$/hm3",
            "------------------------- Costo Deficit --------------------------------",
            ".....% ......c1",
            f"  100. {deficit:.10g}",
        ]
        self._write("sddp.dat", lines)

    def _write_sistem(self) -> None:
        self._write(
            "sistem.dat",
            [" NUM NOME                 ID UnM", f"   1 {'gtopt':<20} {self.sfx:<2} $"],
        )

    def _write_durations(self) -> None:
        g = self.case.grid
        vals: dict[tuple[int, int, int | None], float] = {}
        for k, st in enumerate(g.stages):
            y, m = self.dates[k]
            for j, b in enumerate(st.blocks):
                dur = g.durations[b]
                if dur <= 0:
                    raise ValueError(f"block {g.block_uids[b]} has duration {dur} <= 0")
                vals[(y, m, j + 1)] = dur
        self._write(
            f"duvame{self.nb:03d}.dat", self._monthly_table(None, vals, per_block=True)
        )

    # ── thermal ────────────────────────────────────────────────────────
    def _per_stage_block(
        self, arr: np.ndarray
    ) -> dict[tuple[int, int, int | None], float]:
        g = self.case.grid
        out: dict[tuple[int, int, int | None], float] = {}
        for k, st in enumerate(g.stages):
            y, m = self.dates[k]
            for j, b in enumerate(st.blocks):
                out[(y, m, j + 1)] = float(arr[b])
        return out

    def _cap_table(self, kind: str, code: int, name: str, arr: np.ndarray) -> None:
        title = f"**** {len(self.rgme) + 1:3d} {name[:12]:<12} :<: {kind}:{code}"
        self.rgme.append(
            self._monthly_table(title, self._per_stage_block(arr), per_block=True)
        )

    def _write_thermals(self, thermals: list[dict[str, Any]]) -> None:
        c = self.case
        g = c.grid
        cols = [
            Column("Num", 5),
            Column("Nombre", 12, text=True),
            Column("#Uni", 4),
            Column("Tipo", 4),
            Column("PotIns", 16),
            Column("GerMin", 16),
            Column("GerMax", 16),
            Column("Teif", 7),
            Column("Ih", 7),
            Column("CVaria", 18),
            Column("MR", 4),
            Column("Comb", 5),
            Column("G1", 7),
            Column("CEsp1", 7),
        ]
        rows: list[list[object]] = []
        mods: dict[int, list[list[object]]] = defaultdict(list)
        for num, gen in enumerate(thermals, start=1):
            name = self.names.get(gen.get("name", f"T{num}"))
            if gen.get("uid") is not None:
                self.agents["thermal", int(gen["uid"])] = name
            pmax = self._first_scenario(
                c.field("Generator", gen, "pmax", default=np.nan), f"{name} pmax"
            )
            cap = gen.get("capacity")
            if np.all(np.isnan(pmax)):
                pmax = np.full(
                    g.n_blocks, float(cap) if isinstance(cap, (int, float)) else 1e9
                )
            pmax = np.nan_to_num(pmax, nan=0.0)
            potins = float(pmax.max())
            if potins <= 0:
                self.warn(f"generator {name} has pmax <= 0 everywhere; PSR drops it")
            if not np.allclose(pmax, potins):
                self._cap_table("T", num, name, pmax)
            pmin = self._first_scenario(
                c.field("Generator", gen, "pmin", default=0.0), f"{name} pmin"
            )
            pmin = np.nan_to_num(pmin, nan=0.0)
            gcost = self._first_scenario(
                c.field("Generator", gen, "gcost", default=0.0), f"{name} gcost"
            )
            gcost = np.nan_to_num(gcost, nan=0.0)
            cost_st = per_stage(g, gcost[None, :], "mean")[0]
            pmin_st = per_stage(g, pmin[None, :], "min")[0]
            if not np.allclose(per_stage(g, gcost[None, :], "max")[0], cost_st):
                self.warn(
                    f"{name}: gcost varies within a stage; PSR uses the stage mean"
                )
            if not np.allclose(per_stage(g, pmin[None, :], "max")[0], pmin_st):
                self.warn(
                    f"{name}: pmin varies within a stage; PSR uses the stage minimum"
                )
            must_run = bool(pmin_st.max() > 0)
            for extra in (
                "expcap",
                "expmod",
                "annual_capcost",
                "emission_factor",
                "startup_cost",
            ):
                if gen.get(extra) not in (None, 0, 0.0):
                    self.warn(f"{name}: {extra} is not exported")
            bus = self._bus_uid(gen.get("bus"))
            if bus is None:
                self.warn(
                    f"{name}: bus {gen.get('bus')!r} unknown; generator dropped by PSR"
                )
            else:
                self.busconn.append((bus, "T", num))
            rows.append(
                [
                    num,
                    name,
                    1,
                    0,
                    potins,
                    float(pmin_st[0]) if must_run else 0.0,
                    potins,
                    0.0,
                    0.0,
                    float(cost_st[0]),
                    int(must_run),
                    1,
                    None,
                    0.0,
                ]
            )
            for k in range(1, g.n_stages):
                cv = (
                    float(cost_st[k])
                    if not math.isclose(cost_st[k], cost_st[k - 1])
                    else None
                )
                gm = (
                    float(pmin_st[k])
                    if must_run and not math.isclose(pmin_st[k], pmin_st[k - 1])
                    else None
                )
                if cv is not None or gm is not None:
                    mods[k].append(
                        [
                            num,
                            None,
                            None,
                            None,
                            None,
                            gm,
                            None,
                            None,
                            None,
                            cv,
                            None,
                            None,
                            None,
                            None,
                        ]
                    )
        if not thermals:
            return
        lead_hdr = "!"
        lines = ["$version=1", header(cols, lead=lead_hdr)]
        lines += [record(cols, r, lead=" ") for r in rows]
        self._write(f"ctermi{self.sfx}.dat", lines)
        self._write(
            f"ccombu{self.sfx}.dat", ["!Code,Name,FuelUnit,Cost", "1,F_NONE,MWh,0.0"]
        )
        if mods:
            mlines = ["$version=1"]
            for k in sorted(mods):
                y, m = self.dates[k]
                mlines.append(f"MOD: 01/{m:02d}/{y}")
                mlines.append(header(cols, lead=lead_hdr))
                mlines += [record(cols, r, lead=" ") for r in mods[k]]
            self._write(f"mtermi{self.sfx}.dat", mlines)

    # ── hydro ──────────────────────────────────────────────────────────
    def _write_hydro(self, gen_index: dict[Any, dict[str, Any]]) -> list[_Plant]:
        c = self.case
        g = c.grid
        junctions = c.elements("junction_array")
        if not junctions:
            return []
        jidx = c.ref_index(junctions)
        plants: list[_Plant] = []
        plant_of: dict[int, _Plant] = {}
        for num, j in enumerate(junctions, start=1):
            new_plant = _Plant(num=num, name="", junction=j)
            plants.append(new_plant)
            plant_of[id(j)] = new_plant

        def plant(ref: Any) -> _Plant | None:
            j = jidx.get(ref)
            return None if j is None else plant_of[id(j)]

        for r in c.elements("reservoir_array"):
            p = plant(r.get("junction"))
            if p is None:
                self.warn(f"reservoir {r.get('name')}: junction unknown; dropped")
            elif p.reservoir is not None:
                self.warn(
                    f"junction {p.junction.get('name')}: several reservoirs; first kept"
                )
            else:
                p.reservoir = r
        waterways = c.ref_index(c.elements("waterway_array"))
        for t in c.elements("turbine_array"):
            ja, jb = t.get("junction_a"), t.get("junction_b")
            if ja is None and t.get("waterway") in waterways:
                ww = waterways[t["waterway"]]
                ja, jb = ww.get("junction_a"), ww.get("junction_b")
                t = {**t, "_waterway": ww}
            p = plant(ja)
            if p is None:
                self.warn(f"turbine {t.get('name')}: junction unknown; dropped")
                continue
            if p.turbine is not None:
                self.warn(
                    f"junction {p.junction.get('name')}: several turbines; first kept"
                )
                continue
            p.turbine = t
            p.turbine_to = jb
            p.generator = gen_index.get(t.get("generator"))
        turbine_ww = {
            id(p.turbine["_waterway"])
            for p in plants
            if p.turbine and "_waterway" in p.turbine
        }
        for ww in c.elements("waterway_array"):
            if id(ww) in turbine_ww:
                continue
            p = plant(ww.get("junction_a"))
            if p is None:
                continue
            fmin = c.field("Waterway", ww, "fmin", default=0.0)
            if np.nanmax(fmin) > 0:
                self.warn(f"waterway {ww.get('name')}: fmin > 0 is not exported")
            if p.turbine is None and p.turbine_to is None and p.qmax == 0.0:
                fmax = c.field("Waterway", ww, "fmax", default=np.nan)
                p.turbine_to = ww.get("junction_b")
                p.qmax = 0.0 if np.all(np.isnan(fmax)) else float(np.nanmax(fmax))
                p.turbine = None
                p.junction = {**p.junction, "_outlet": ww}
            elif p.spill_to is None:
                p.spill_to = ww.get("junction_b")
            else:
                self.warn(
                    f"junction {p.junction.get('name')}: more than two outgoing waterways; "
                    f"{ww.get('name')} dropped"
                )
        # inflows
        flows_by_plant: dict[int, np.ndarray] = {}
        for f in c.elements("flow_array"):
            p = plant(f.get("junction"))
            if p is None:
                self.warn(f"flow {f.get('name')}: no junction; dropped")
                continue
            direction = int(f.get("direction", 1) or 1)
            q = np.nan_to_num(c.field("Flow", f, "discharge", default=0.0), nan=0.0)
            if direction < 0:
                self.warn(
                    f"flow {f.get('name')}: withdrawals (direction -1) are not exported"
                )
                continue
            st = per_stage(g, q, "mean")
            if not np.allclose(per_stage(g, q, "max"), st):
                self.warn(
                    "inflows vary within a stage; PSR uses the duration-weighted mean"
                )
            flows_by_plant[p.num] = flows_by_plant.get(p.num, 0.0) + st
        names_used: set[str] = set()
        for p in plants:
            base = (p.generator or {}).get("name") or p.junction.get(
                "name", f"H{p.num}"
            )
            base = re.sub(r"^(j_|tb_|r_)", "", str(base))
            p.name = (
                self.names.get(base)
                if base not in names_used
                else self.names.get(f"{base}_{p.num}")
            )
            names_used.add(base)
            if p.num in flows_by_plant:
                p.station = p.num
                p.inflow = flows_by_plant[p.num]
            if p.reservoir is not None:
                fcr = p.reservoir.get("flow_conversion_rate", HM3_PER_M3S_H)
                if not math.isclose(float(fcr), HM3_PER_M3S_H, rel_tol=1e-9):
                    self.warn(
                        f"reservoir {p.reservoir.get('name')}: flow_conversion_rate {fcr} "
                        "!= 0.0036 (hm3); volumes written unscaled"
                    )
        cols = [
            Column("NUM", 5),
            Column("Nombre", 12, text=True),
            Column("PV", 5),
            Column("VAA", 5),
            Column("TAA", 5),
            Column("#Uni", 4),
            Column("Tipo", 4),
            Column("Pot", 16),
            Column("FPMed", 16),
            Column("QMin", 7),
            Column("QMax", 16),
            Column("VMin", 16),
            Column("VMax", 16),
            Column("VInic", 18),
            Column("MinV+T", 8),
            Column("VNC", 7),
            Column("ICP", 7),
            Column("IH", 7),
        ]
        rows = []
        for p in plants:
            pot = fp = qmax = 0.0
            if p.turbine is not None:
                if p.generator is not None:
                    pm = self._first_scenario(
                        c.field("Generator", p.generator, "pmax", default=np.nan),
                        f"{p.name} pmax",
                    )
                    capg = p.generator.get("capacity")
                    pm = np.where(
                        np.isnan(pm),
                        float(capg) if isinstance(capg, (int, float)) else 0.0,
                        pm,
                    )
                    pot = float(pm.max())
                    if pot > 0 and not np.allclose(pm, pot):
                        self._cap_table("H", p.num, p.name, pm)
                    bus = self._bus_uid(p.generator.get("bus"))
                    if bus is not None:
                        self.busconn.append((bus, "H", p.num))
                    gc = c.field("Generator", p.generator, "gcost", default=0.0)
                    if np.nanmax(np.abs(gc)) > 0:
                        self.warn(f"hydro generator {p.name}: gcost is 0 in PSR")
                pf = self._first_scenario(
                    c.field("Turbine", p.turbine, "production_factor", default=1.0),
                    f"{p.name} production_factor",
                )
                fp = float(pf[0])
                if not np.allclose(pf, fp):
                    self.warn(
                        f"turbine {p.turbine.get('name')}: production factor varies; first value "
                        "kept"
                    )
                capt = p.turbine.get("capacity")
                if capt is None and "_waterway" in p.turbine:
                    fmax = c.field(
                        "Waterway", p.turbine["_waterway"], "fmax", default=np.nan
                    )
                    capt = None if np.all(np.isnan(fmax)) else float(np.nanmax(fmax))
                qmax = float(capt) if isinstance(capt, (int, float)) else 0.0
            else:
                qmax = p.qmax
            vmin = vmax = vinic = 0.0
            if p.reservoir is not None:
                r = p.reservoir
                emin = np.nan_to_num(
                    c.field("Reservoir", r, "emin", default=0.0), nan=0.0
                )
                emax = c.field("Reservoir", r, "emax", default=np.nan)
                capr = r.get("capacity")
                emax = np.where(
                    np.isnan(emax),
                    float(capr) if isinstance(capr, (int, float)) else 0.0,
                    emax,
                )
                vmin, vmax = float(emin.min()), float(emax.max())
                if not np.allclose(emin, vmin) or not np.allclose(emax, vmax):
                    self.warn(
                        f"reservoir {r.get('name')}: time-varying emin/emax are not exported"
                    )
                eini = r.get("eini")
                eini = vmin if eini is None else float(eini)
                vinic = (eini - vmin) / (vmax - vmin) if vmax > vmin else 0.0
                for extra in ("efin", "efin_cost"):
                    if r.get(extra) not in (None, 0, 0.0):
                        self.warn(f"reservoir {r.get('name')}: {extra} is not exported")
                if vmax <= vmin:
                    self.warn(
                        f"reservoir {r.get('name')}: VMax <= VMin, PSR treats it as run-of-river"
                    )
            taa = plant(p.turbine_to)
            vaa = plant(p.spill_to)
            rows.append(
                [
                    p.num,
                    p.name,
                    p.station or None,
                    vaa.num if vaa else None,
                    taa.num if taa else None,
                    1,
                    0,
                    pot,
                    fp,
                    0.0,
                    qmax,
                    vmin,
                    vmax,
                    vinic,
                    0.0,
                    0,
                    0.0,
                    0.0,
                ]
            )
        for p in plants:
            j = p.junction
            if p.spill_to is None and not j.get("drain"):
                self.warn(
                    f"junction {j.get('name')}: no spill in gtopt, but PSR always allows "
                    "spill (at the study-wide spill cost)"
                )
            if j.get("drain_capacity") is not None:
                self.warn(
                    f"junction {j.get('name')}: drain_capacity is not exported (PSR spill is "
                    "unbounded)"
                )
        lines = [header(cols, lead=" ")] + [record(cols, r, lead=" ") for r in rows]
        self._write(f"chidro{self.sfx}.dat", lines)
        self._write_hinflw([p for p in plants if p.inflow is not None])
        return plants

    def _write_hinflw(self, plants: list[_Plant]) -> None:
        g = self.case.grid
        y_first = self.dates[0][0]
        table: dict[tuple[int, int], dict[int, float]] = defaultdict(dict)
        clash = False
        for p in plants:
            assert p.inflow is not None
            for s in range(g.n_scenarios):
                for k, (y, m) in enumerate(self.dates):
                    key = (self.hydro_year + s + (y - y_first), m)
                    val = float(p.inflow[s, k])
                    old = table[key].get(p.station)
                    if old is not None and not math.isclose(
                        old, val, rel_tol=1e-6, abs_tol=1e-6
                    ):
                        clash = True
                    table[key].setdefault(p.station, val)
        if clash:
            self.warn(
                "hinflw: overlapping scenario-years with different inflows; first written kept"
            )
        stations = [p.station for p in plants]
        lines = [
            f"{1:2d}/{self.hydro_year:4d}" + "".join(f"{st:7d}" for st in stations)
        ]
        for y, m in sorted(table):
            row = table[(y, m)]
            lines.append(
                f"{m:2d}/{y:4d}"
                + "".join(fmt_fixed7(row.get(st, 0.0)) for st in stations)
            )
        self._write("hinflw.dat", lines)

    # ── renewables ─────────────────────────────────────────────────────
    def _write_renewables(self, gens: list[dict[str, Any]]) -> None:
        if not gens:
            return
        c = self.case
        g = c.grid
        cols = [
            Column("Num", 4),
            Column("Name", 12, text=True),
            Column("Bus", 6),
            Column("Tipo", 4),
            Column("#Uni", 4),
            Column("PotIns", 16),
            Column("FatOpe", 8),
            Column("ProbFal", 7),
            Column("SFal", 4),
            Column("Stat", 5),
            Column("O&M", 14),
            Column("CurtCos", 7),
            Column("TechTyp", 7),
        ]
        rows, sites, series = [], [], []
        for num, gen in enumerate(gens, start=1):
            name = self.names.get(gen.get("name", f"R{num}"))
            if gen.get("uid") is not None:
                self.agents["renewable", int(gen["uid"])] = name
            prof_el = self.profile_of[id(gen)]
            cls = "GeneratorProfile" if "generator" in prof_el else "CapacityProfile"
            prof = c.field(cls, prof_el, "profile", default=1.0)
            if cls == "CapacityProfile" and np.isnan(prof).all():
                prof = c.field("GeneratorProfile", prof_el, "profile", default=1.0)
            prof = np.nan_to_num(prof, nan=1.0)
            capg = gen.get("capacity")
            pmax = c.field("Generator", gen, "pmax", default=np.nan)
            capv = (
                float(capg)
                if isinstance(capg, (int, float))
                else float(np.nanmax(pmax))
            )
            pmax = np.where(np.isnan(pmax), capv, pmax)
            avail = np.minimum(pmax, prof * capv)  # (n_scen, n_blocks)
            potins = float(avail.max()) if avail.size else 0.0
            pu = avail / potins if potins > 0 else np.zeros_like(avail)
            series.append((name, pu))
            gcost = float(np.nanmax(c.field("Generator", gen, "gcost", default=0.0)))
            if gcost < 0:
                self.warn(f"{name}: negative O&M clipped to 0 by PSR")
            bus = self._bus_uid(gen.get("bus"))
            tech = TECH_CODES.get(str(gen.get("type", "")).lower(), 0)
            rows.append(
                [num, name, bus, 0, 1, potins, 1.0, 0.0, 0, num, gcost, 0.0, tech]
            )
            sites.append(f"{num},{name},{tech},1")
            if bus is not None:
                self.busconn.append((bus, "R", num))
        lines = ["$version=3", header(cols, lead="!")] + [
            record(cols, r, lead=" ") for r in rows
        ]
        self._write(f"cgnd{self.sfx}.dat", lines)
        self._write(
            f"gndsite{self.sfx}.dat",
            ["$version=2", "!Code,Name,TechType,Datasource", *sites],
        )
        out = ["$unit pu", "!Scn,Year,Month,Block," + ",".join(n for n, _ in series)]
        for s in range(g.n_scenarios):
            for k, st in enumerate(g.stages):
                y, m = self.dates[k]
                for j, b in enumerate(st.blocks):
                    vals = ",".join(f"{pu[s, b]:.10g}" for _, pu in series)
                    out.append(f"{s + 1},{y},{m},{j + 1},{vals}")
        self._write(f"blrenw{self.nb:03d}{self.sfx}.dat", out)

    # ── network ────────────────────────────────────────────────────────
    def _bus_kv(self) -> dict[int, float]:
        kv: dict[int, float] = {}
        for b in self.case.elements("bus_array"):
            v = b.get("voltage")
            if isinstance(v, (int, float)) and v > 0:
                kv[int(b["uid"])] = float(v)
        for ln in self.case.elements("line_array"):
            v = ln.get("voltage")
            if not isinstance(v, (int, float)) or v <= 0:
                continue
            for end in ("bus_a", "bus_b"):
                u = self._bus_uid(ln.get(end))
                if u is not None and u not in kv:
                    kv[u] = float(v)
        return kv

    def _default_kv(self) -> float:
        """kV for buses gtopt gives no voltage.

        A case without any voltage is per-unit (gtopt V = 1.0): writing 1 kV
        keeps it per-unit through PSR (X% = X * 1e4, no 0.05 % floor hit).
        """
        if not self._bus_kv():
            return 1.0
        return DEFAULT_KV

    def _write_buses(self) -> None:
        kv = self._bus_kv()
        buses = self.case.elements("bus_array")
        lines = ["$version=1", "!Code,Name,SystemId"]
        lines += [f"{int(b['uid'])},{self.bus_names[int(b['uid'])]},1" for b in buses]
        self._write("dbus.dat", lines)
        vlines = ["(Bus) (...Nome...) O (.kV.)"]
        for b in buses:
            u = int(b["uid"])
            vlines.append(
                f"{u:5d} {self.bus_names[u]:<12}      {kv.get(u, self._default_kv()):g}"
            )
        self._write("volt.dat", vlines)
        conn = ["!SystemId,BusCode,PlantType,PlantCode"]
        conn += [f"1,{bus},{kind},{code}" for bus, kind, code in self.busconn]
        self._write("busconnection.dat", conn)

    def _write_lines(self) -> None:
        c = self.case
        g = c.grid
        lines_ = c.elements("line_array")
        if not lines_:
            return
        kv = self._bus_kv()
        head = "Code,Name,BusFromCode,BusToCode,Resistance,Reactance,Rating,Existing"
        rows = ["!" + head]
        mrows = ["!Day,Month,Year," + head]
        for code, ln in enumerate(lines_, start=1):
            name = _sanitize(ln.get("name", f"L{code}"))
            a, b = self._bus_uid(ln.get("bus_a")), self._bus_uid(ln.get("bus_b"))
            if a is None or b is None:
                self.warn(f"line {name}: unknown bus; dropped")
                continue
            if ln.get("uid") is not None:
                self.agents["line", int(ln["uid"])] = name
            v = max(kv.get(a, self._default_kv()), kv.get(b, self._default_kv()))
            if not isinstance(ln.get("voltage"), (int, float)) and kv:
                self.warn(
                    f"line {name}: no voltage in a case with kV buses; PSR gives it {v:g} kV"
                )
            x = ln.get("reactance")
            r = ln.get("resistance")
            if isinstance(x, str) or isinstance(r, str):
                self.warn(
                    f"line {name}: time-varying R/X are not exported (first stage kept)"
                )
            xs = float(np.nanmax(c.field("Line", ln, "reactance", default=0.0)[0][:1]))
            rs = float(np.nanmax(c.field("Line", ln, "resistance", default=0.0)[0][:1]))
            xpct = xs * 1e4 / v**2
            rpct = rs * 1e4 / v**2 if self.use_losses else 0.0
            if xpct < 0.05:
                self.warn(f"line {name}: reactance below 0.05% is floored by PSR")
            tab = self._first_scenario(
                c.field("Line", ln, "tmax_ab", default=np.nan), f"{name} tmax"
            )
            tba = self._first_scenario(
                c.field("Line", ln, "tmax_ba", default=np.nan), f"{name} tmax"
            )
            if not np.allclose(np.nan_to_num(tab, nan=-1), np.nan_to_num(tba, nan=-1)):
                self.warn(
                    f"line {name}: asymmetric limits; PSR rating = max(tmax_ab, tmax_ba)"
                )
            rating_b = np.fmax(tab, tba)
            rating_b = np.where(np.isnan(rating_b), 0.0, rating_b)
            rating = per_stage(g, rating_b[None, :], "max")[0]
            if not np.allclose(per_stage(g, rating_b[None, :], "min")[0], rating):
                self.warn("line limits vary within a stage; PSR uses the stage maximum")
            active = per_stage(g, c.field("Line", ln, "active", default=1.0), "max")[0]
            on = active > 0.5
            if not on.any():
                self.warn(f"line {name}: never active; dropped")
                continue
            k0 = int(np.argmax(on))
            if not on[k0:].all():
                self.warn(f"line {name}: retired mid-horizon; PSR keeps it after entry")
            existing = 0 if k0 == 0 else 1
            rows.append(
                f"{code},{name},{a},{b},{rpct:.12g},{xpct:.12g},{rating[k0]:.12g},{existing}"
            )
            prev = None if k0 > 0 else rating[0]
            for k in range(k0, g.n_stages):
                if prev is None or not math.isclose(rating[k], prev):
                    y, m = self.dates[k]
                    mrows.append(
                        f"1,{m},{y},{code},{name},{a},{b},{rpct:.12g},{xpct:.12g},"
                        f"{rating[k]:.12g},0"
                    )
                    prev = rating[k]
        self._write("dcirc.dat", rows)
        if len(mrows) > 1:
            self._write("mcirc.dat", mrows)

    # ── demand / batteries ─────────────────────────────────────────────
    def _write_demands(self) -> None:
        c = self.case
        g = c.grid
        dur = np.asarray(g.durations)
        per_bus: dict[int, np.ndarray] = {}
        for d in c.elements("demand_array"):
            bus = self._bus_uid(d.get("bus"))
            if bus is None:
                self.warn(f"demand {d.get('name')}: unknown bus; dropped")
                continue
            lmax = self._first_scenario(
                c.field("Demand", d, "lmax", default=0.0), f"demand {d.get('name')}"
            )
            lmax = np.nan_to_num(lmax, nan=0.0)
            if bus in per_bus:
                self.warn("several demands on one bus are merged by PSR")
            per_bus[bus] = per_bus.get(bus, 0.0) + lmax
            for extra in ("emin", "ecost", "capacity"):
                if d.get(extra) not in (None, 0, 0.0) and extra != "capacity":
                    self.warn(f"demand {d.get('name')}: {extra} is not exported")
        if not per_bus:
            return
        cols = [Column(str(m), 16) for m in range(1, 13)]
        lines = ["$version=2"]
        y0, m0 = self.dates[0]
        dbf = ["!BCod ..Bus.Name.. LCod dd/mm/yyyy Llev ..Load.. Icca"]
        for bus, lmax in per_bus.items():
            gwh = lmax * dur / 1000.0
            name = self.bus_names[bus]
            lines.append(
                f"**** Load  : {bus:4d} {name:<12} Sgmnt:    1             Demanda"
            )
            vals = self._per_stage_block(gwh)
            lines.append("A~no Blk " + header(cols))
            for y in sorted({k[0] for k in vals}):
                for b in range(1, self.nb + 1):
                    lines.append(
                        record(
                            cols,
                            [vals.get((y, m, b)) for m in range(1, 13)],
                            lead=f"{y:4d}{b:4d} ",
                        )
                    )
            for b in range(1, self.nb + 1):
                dbf.append(
                    f"{bus:5d} {name:<12} {bus:4d} 01/{m0:02d}/{y0:4d} {b:4d}      1.0    0"
                )
        self._write(f"deme{self.nb:03d}{self.sfx}.dat", lines)
        self._write(f"dbf{self.nb:03d}{self.sfx}.dat", dbf)

    def _write_batteries(self) -> None:
        c = self.case
        bats = c.elements("battery_array")
        if not bats:
            return
        out = ["!Num,Name,Bus,Type,Amin,Amax,Ainic,Pot,RC,RD"]
        for num, bat in enumerate(bats, start=1):
            name = _sanitize(bat.get("name", f"BAT{num}"))
            bus = self._bus_uid(bat.get("bus"))
            if bus is None:
                self.warn(
                    f"battery {name}: no bus (converter-based batteries are not exported)"
                )
                continue
            if bat.get("uid") is not None:
                self.agents["battery", int(bat["uid"])] = name

            def scal(
                key: str, default: float, bat: dict[str, Any] = bat, name: str = name
            ) -> float:
                arr = c.field("Battery", bat, key, default=default)
                if not np.allclose(arr, arr.flat[0], equal_nan=True):
                    self.warn(f"battery {name}: time-varying {key}; first value kept")
                v = float(arr.flat[0])
                return default if math.isnan(v) else v

            emax = scal("emax", float(bat.get("capacity", 0.0) or 0.0))
            emin = scal("emin", 0.0)
            eini = float(bat.get("eini", emin) if bat.get("eini") is not None else emin)
            pc, pd = scal("pmax_charge", 0.0), scal("pmax_discharge", 0.0)
            if not math.isclose(pc, pd):
                self.warn(
                    f"battery {name}: charge and discharge limits differ; PSR uses the max"
                )
            rc = float(bat.get("input_efficiency", 1.0) or 1.0)
            rd = float(bat.get("output_efficiency", 1.0) or 1.0)
            out.append(
                f"{num},{name},{bus},0,{emin:.12g},{emax:.12g},{eini:.12g},{max(pc, pd):.12g},"
                f"{rc:.12g},{rd:.12g}"
            )
        self._write(f"cbattery{self.sfx}.dat", out)
