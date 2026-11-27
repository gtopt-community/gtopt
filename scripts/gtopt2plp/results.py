# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Write a solved gtopt run as PLP (CEN65) output files.

The results half of :mod:`gtopt2plp`: the files PLP itself writes at the
end of a simulation, with the headers, row order and units of the CEN65
Fortran writers (``plp-gdbdcen.f``, ``plp-gdbdbar.f``, ``plp-gdbdlin.f``,
``plp-gdbdemb.f``, ``genpdess.f``), so the tools that read PLP output
(``gtopt_compare --case plp``, the CEN R / pandas scripts) read gtopt
results unchanged:

========== ============================================================
file       columns (one row per simulation x element x block)
========== ============================================================
plpcen.csv ``Hidro,Bloque,TipoEtapa,CenNum,CenNom,CenTip,CenBar,BarNom,
           CenQgen,CenPgen,CenEgen,CenInyP,CenInyE,CenRen,CenCVar,
           CenCostOp,CenPMax``
plpfal.csv ``Hidro,Bloque,TipoEtapa,CenNum,CenNom,CenTip,CenBar,BarNom,
           CenPgen,CenEgen`` (deficit centrals, non-zero rows only)
plpbar.csv ``Hidro,Bloque,TipoEtapa,BarNum,BarNom,CMgBar,DemBarP,DemBarE,
           PerBarP,PerBarE,BarRetP,BarRetE``
plplin.csv ``Hidro,Bloque,TipoEtapa,LinNum,LinNom,BarA,BarB,LinFluP,
           LinFluE,LinFluMax,LinUso,LinPerP,LinPerE,LinPer2P,LinPer2E,
           LinITP,LinITE``
plpemb.csv ``Hidro,Bloque,TipoEtapa,EmbNum,EmbNom,EmbFac,EmbVini,EmbVfin,
           EmbQgen,EmbQver,EmbQdef,EmbPsom,EmbPsom2,EmbAflu,EmbQFil,
           EmbQReb``
plpess.csv ``Hidro, Bloque, EssNom, Efin, EMin, EMax, DCar, DCmin, DCmax,
           DCMod, GDes, GDMin, GDMax, PGNet, EfPSom, EfPSom2``
plpcostop  ``Hidro, IBlo, CostoOperActual`` (one row per block)
.csv
barras.csv ``#Numero, Nombre, Latitud``; ``centrales.csv`` and
           ``lineas.csv``: the name tables of the same run
========== ============================================================

Conventions (as PLP):

* ``Hidro`` is ``Sim  N`` (``"Sim" + I3``) with ``N`` the 1-based position
  of the gtopt scenario, followed by the ``MEDIA`` rows: the
  probability-weighted mean of every base quantity (PLP's plain mean when
  the scenarios are equally likely); derived columns of a ``MEDIA`` row are
  computed from the means, as PLP does.
* ``Bloque`` is the 1-based global block index, ``TipoEtapa`` the stage
  label ``NN Bloques`` that ``plpeta.dat`` carries.
* Units: power MW (``*P`` columns), energy GWh (``*E`` = MW x hours /
  1000), money $/h (``CenInyP``, ``BarRetP``, ``LinITP``) and k$
  (``CenInyE``, ``CenCostOp``, ``BarRetE``, ``LinITE``), marginal costs
  $/MWh, flows m3/s, volumes ``m3 / EmbFac`` with ``EmbFac = 1e6`` (i.e.
  hm3, the ``Escala`` the input export writes in ``plpcnfce.dat``).
* ``CMgBar`` is gtopt's ``Bus/balance_dual`` as written: gtopt already
  back-scales duals by ``scale_objective`` and the probability, discount
  and block-duration factors, so it is the undiscounted $/MWh price PLP
  writes (``CMg x FPhi``).  ``CostoOperActual`` is the discounted
  (present-value) cost in $, PLP's ``/FPhi``.
* Water values: ``EmbPsom2`` is the volume shadow price per dam3
  (``Reservoir/water_value_dual`` [$/hm3] / 1000) and ``EmbPsom`` its $/MWh
  form ``EmbPsom2 x 3.6 / FactRendim`` with ``FactRendim`` the summed
  production factor of the plant and its downstream turbine chain.
* ``EfPSom`` is the battery energy dual ($/MWh), ``EfPSom2 = EfPSom / 3.6``.

Names and numbers are the input export's (:class:`gtopt2plp.writer.PlpWriter`
``collect``): ``CenNum`` is the ``plpcnfce.dat`` central number, ``BarNum``
the bus uid, ``LinNum`` the ``plpcnfli.dat`` position.  What gtopt does not
produce is written as 0: ``EmbQdef`` (PLP's fictitious inflow), ``EmbQReb``,
``LinPer2*``, the ESS ``DCmin`` / ``GDMin`` / ``DCMod``.  Bus losses
(``PerBar*``) are the ``Line/loss_sol`` shares by each line's
``loss_allocation_mode``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from gtopt_shared.case_reader import GtoptCase
from gtopt_shared.results_reader import GtoptResults
from gtopt2plp.writer import PlpWriter, _Central, line_names

logger = logging.getLogger(__name__)

EMB_FAC = 1.0e6  # m3 per output volume unit (hm3), plpcnfce Escala
FACT_TIEMPO = 3.6  # dam3 per (m3/s x hour)
CEN_TIP = {
    "embalse": "Emb",
    "serie": "Ser",
    "pasada": "Pas",
    "termica": "Ter",
    "bateria": "BAT",
    "falla": "Fal",
}
CEN_LETTER = {
    "embalse": "E",
    "serie": "S",
    "pasada": "P",
    "termica": "T",
    "bateria": "B",
    "falla": "F",
}


def fortran_e(value: float, decimals: int = 2, width: int = 9) -> str:
    """Fortran ``Ew.d`` text (``0.10E+07`` for 1e6, ``E9.2``)."""
    v = float(value)
    if v == 0.0:
        mant, exp = 0.0, 0
    else:
        exp = int(np.floor(np.log10(abs(v)))) + 1
        mant = v / 10.0**exp
        if round(abs(mant), decimals) >= 1.0:
            mant, exp = mant / 10.0, exp + 1
    return f"{mant:.{decimals}f}E{exp:+03d}".rjust(width)


def _f(value: float, width: int, decimals: int) -> str:
    return f"{float(value) + 0.0:{width}.{decimals}f}"


@dataclass
class PlpResultsWriter:
    """Export the results of a solved gtopt run as PLP output files."""

    case: GtoptCase
    results_dir: Path
    out_dir: Path
    writer: PlpWriter | None = None
    files: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Bind the readers (the PLP mapping is collected on :meth:`write`)."""
        self.res = GtoptResults(self.results_dir, self.case.grid)
        g = self.case.grid
        probs = np.asarray(g.probabilities, dtype=float)
        total = probs.sum()
        self.weights = (
            probs / total if total > 0 else np.full(g.n_scenarios, 1 / g.n_scenarios)
        )
        self.labels = [f"Sim{i + 1:3d}" for i in range(g.n_scenarios)] + ["MEDIA"]
        dur = np.asarray(g.durations, dtype=float)
        self.dur = dur
        sob = g.stage_of_block()
        self.sob = sob
        self.tipo = [f"{st.count_block:02d} Bloques" for st in g.stages]
        self.disc = np.array(
            [g.stages[int(k)].discount_factor for k in sob], dtype=float
        )
        self.cmg: dict[int, np.ndarray] = {}
        self._cen_series: dict[int, dict[str, np.ndarray]] = {}
        self._lines: list[dict[str, Any]] = []

    # ── helpers ────────────────────────────────────────────────────────
    def _aug(self, arr: np.ndarray) -> np.ndarray:
        """Append the probability-weighted MEDIA row to ``(n_scen, n)``."""
        arr = np.asarray(arr, dtype=float)
        return np.vstack([arr, self.weights @ arr])

    def _blk(self, cls: str, fld: str, uid: Any) -> np.ndarray:
        u = int(uid) if isinstance(uid, (int, np.integer)) else None
        return self._aug(self.res.block_of(cls, fld, u))

    def _stage_to_blocks(self, arr: np.ndarray) -> np.ndarray:
        return np.asarray(arr)[:, self.sob]

    def _field(
        self, cls: str, el: dict[str, Any], name: str, default: float
    ) -> np.ndarray:
        return self._aug(
            np.nan_to_num(self.case.field(cls, el, name, default=default), nan=default)
        )

    def _write(self, name: str, lines: list[str]) -> None:
        path = self.out_dir / name
        path.write_text("\n".join(lines) + "\n", encoding="latin-1", errors="replace")
        self.files.append(path)

    def _label(self, i: int, width: int = 6) -> str:
        return self.labels[i].ljust(width)

    # ── main entry ─────────────────────────────────────────────────────
    def write(self) -> list[Path]:
        """Write every result file; returns their paths."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if self.writer is None:
            self.writer = PlpWriter(self.case, self.out_dir)
            self.writer.collect()
        if not self.writer.centrals and not self.writer.buses:
            self.writer.collect()
        if not self.res.has("Bus", "balance_dual") and not self.res.has(
            "Generator", "generation_sol"
        ):
            raise ValueError(
                f"{self.results_dir}: no gtopt results (Bus/balance_dual, "
                "Generator/generation_sol) found"
            )
        w = self.writer
        self.cmg = {
            int(b["uid"]): self._blk("Bus", "balance_dual", int(b["uid"]))
            for b in w.buses
        }
        self._write_centrals()
        self._write_buses()
        self._write_lines()
        self._write_reservoirs()
        self._write_ess()
        self._write_costop()
        self._write_name_tables()
        return self.files

    # ── centrals ───────────────────────────────────────────────────────
    def _bus_cmg(self, bus: int) -> np.ndarray:
        got = self.cmg.get(bus)
        return got if got is not None else np.zeros((len(self.labels), len(self.dur)))

    def _central_series(self, ce: _Central) -> dict[str, np.ndarray]:
        """Per-central base series ``(n_scen + 1, n_blocks)``."""
        zeros = np.zeros((len(self.labels), len(self.dur)))
        gen = ce.generator or {}
        if ce.kind == "bateria":
            bat = ce.battery or {}
            p = self._blk("Battery", "fout_sol", bat.get("uid"))
            pmax = self._field("Battery", bat, "pmax_discharge", 0.0)
            return {"p": p, "q": p, "cvar": zeros, "pmax": pmax, "ren": zeros + 1.0}
        p = self._blk("Generator", "generation_sol", gen.get("uid")) if gen else zeros
        if ce.turbine is not None:
            q = self._turbine_flow(ce.turbine)
            ren = zeros + ce.rendi
        else:
            q, ren = p, zeros + 1.0
        if gen:
            cvar = self._field("Generator", gen, "gcost", 0.0)
            cap = gen.get("capacity")
            capv = float(cap) if isinstance(cap, (int, float)) else 0.0
            pmax = self._field("Generator", gen, "pmax", capv)
        else:
            cvar = pmax = zeros
        return {"p": p, "q": q, "cvar": cvar, "pmax": pmax, "ren": ren}

    def _turbine_flow(self, turbine: dict[str, Any]) -> np.ndarray:
        """Turbined flow (m3/s): ``Turbine/flow_sol``, else its waterway's flow."""
        if not self.res.has("Turbine", "flow_sol") and turbine.get("waterway"):
            ww = self.case.ref_index(self.case.elements("waterway_array")).get(
                turbine["waterway"]
            )
            if ww is not None:
                return self._blk("Waterway", "flow_sol", ww.get("uid"))
        return self._blk("Turbine", "flow_sol", turbine.get("uid"))

    def _write_centrals(self) -> None:
        w = self.writer
        assert w is not None
        head = (
            "Hidro,Bloque,TipoEtapa,CenNum,CenNom,CenTip,CenBar,BarNom,"
            "CenQgen,CenPgen,CenEgen,CenInyP,CenInyE,CenRen,CenCVar,CenCostOp,CenPMax"
        )
        fal_head = (
            "Hidro,Bloque,TipoEtapa,CenNum,CenNom,CenTip,CenBar,BarNom,CenPgen,CenEgen"
        )
        series = {
            id(ce): self._central_series(ce) for ce in w.centrals if ce.kind != "falla"
        }
        self._cen_series = series
        fail = self._fail_by_bus()
        lines, fal = [head], [fal_head]
        for i in range(len(self.labels)):
            for ce in w.centrals:
                bus_nom = w.bus_names.get(ce.bus, "").ljust(48)
                lead = (
                    f",{ce.num:4d},{ce.name.ljust(48)},{CEN_TIP[ce.kind]},"
                    f"{ce.bus:4d},{bus_nom}"
                )
                if ce.kind == "falla":
                    pf = fail.get(ce.bus)
                    if pf is None:
                        continue
                    for b, t in enumerate(self.dur):
                        if pf[i, b] == 0.0:
                            continue
                        fal.append(
                            f"{self._label(i)},{b + 1:4d},{self.tipo[self.sob[b]].ljust(12)}"
                            f"{lead},{_f(pf[i, b], 12, 2)},{_f(pf[i, b] * t * 1e-3, 12, 2)}"
                        )
                    continue
                s = series[id(ce)]
                cmg = self._bus_cmg(ce.bus)
                for b, t in enumerate(self.dur):
                    p = s["p"][i, b]
                    vals = (
                        s["q"][i, b],
                        p,
                        p * t * 1e-3,
                        p * cmg[i, b],
                        p * cmg[i, b] * t * 1e-3,
                        s["ren"][i, b],
                        s["cvar"][i, b],
                        p * s["cvar"][i, b] * t * 1e-3,
                        s["pmax"][i, b],
                    )
                    lines.append(
                        f"{self._label(i)},{b + 1:4d},{self.tipo[self.sob[b]].ljust(12)}"
                        + lead
                        + "".join(f",{_f(v, 12, 2)}" for v in vals)
                    )
        self._write("plpcen.csv", lines)
        self._write("plpfal.csv", fal)

    def _demands_at(self, bus: int) -> list[dict[str, Any]]:
        w = self.writer
        assert w is not None
        return [
            d
            for d in self.case.elements("demand_array")
            if w._bus_uid(d.get("bus")) == bus
        ]

    def _fail_by_bus(self) -> dict[int, np.ndarray]:
        w = self.writer
        assert w is not None
        out: dict[int, np.ndarray] = {}
        for b in w.buses:
            u = int(b["uid"])
            ds = self._demands_at(u)
            if ds:
                out[u] = sum(
                    (self._blk("Demand", "fail_sol", d.get("uid")) for d in ds),
                    np.zeros((len(self.labels), len(self.dur))),
                )
        return out

    # ── buses / lines ──────────────────────────────────────────────────
    def _line_data(self) -> list[dict[str, Any]]:
        w = self.writer
        assert w is not None
        c = self.case
        lines_ = c.elements("line_array")
        out = []
        for i, (ln, name) in enumerate(zip(lines_, line_names(lines_), strict=True)):
            uid = ln.get("uid")
            flow = self._blk("Line", "flow_sol", uid)
            if not self.res.has("Line", "flow_sol"):
                flow = self._blk("Line", "flowp_sol", uid) - self._blk(
                    "Line", "flown_sol", uid
                )
            mode = ln.get("loss_allocation_mode", "receiver")
            ems, rec = {"sender": (1.0, 0.0), "split": (0.5, 0.5)}.get(
                str(mode), (0.0, 1.0)
            )
            tmax = np.maximum(
                self._field("Line", ln, "tmax_ab", 0.0),
                self._field("Line", ln, "tmax_ba", 0.0),
            )
            out.append(
                {
                    "num": i + 1,
                    "name": name,
                    "a": w._bus_uid(ln.get("bus_a")),
                    "b": w._bus_uid(ln.get("bus_b")),
                    "flow": flow,
                    "loss": self._blk("Line", "loss_sol", uid),
                    "tmax": tmax,
                    "ems": ems,
                    "rec": rec,
                }
            )
        return out

    def _write_buses(self) -> None:
        w = self.writer
        assert w is not None
        lines_ = self._line_data()
        self._lines = lines_
        zeros = np.zeros((len(self.labels), len(self.dur)))
        per: dict[int, np.ndarray] = {int(b["uid"]): zeros.copy() for b in w.buses}
        for ln in lines_:
            fwd = ln["flow"] >= 0
            send = np.where(fwd, ln["ems"], ln["rec"])
            recv = 1.0 - send
            for bus, share in ((ln["a"], send), (ln["b"], recv)):
                if bus in per:
                    per[bus] += ln["loss"] * share
        fail = self._fail_by_bus()
        out = [
            "Hidro,Bloque,TipoEtapa,BarNum,BarNom,CMgBar,DemBarP,DemBarE,"
            "PerBarP,PerBarE,BarRetP,BarRetE"
        ]
        for i in range(len(self.labels)):
            for b in w.buses:
                u = int(b["uid"])
                dem = zeros.copy()
                for d in self._demands_at(u):
                    dem += self._blk("Demand", "load_sol", d.get("uid"))
                dem += fail.get(u, zeros)
                cmg = self._bus_cmg(u)
                name = w.bus_names[u].ljust(48)
                for k, t in enumerate(self.dur):
                    vals15 = (
                        dem[i, k] * t * 1e-3,
                        per[u][i, k],
                        per[u][i, k] * t * 1e-3,
                        dem[i, k] * cmg[i, k],
                        dem[i, k] * cmg[i, k] * t * 1e-3,
                    )
                    out.append(
                        f"{self._label(i)},{k + 1:4d},{self.tipo[self.sob[k]].ljust(12)},"
                        f"{u:4d},{name},{_f(cmg[i, k], 10, 2)},{_f(dem[i, k], 10, 2)}"
                        + "".join(f",{_f(v, 15, 3)}" for v in vals15)
                    )
        self._write("plpbar.csv", out)

    def _write_lines(self) -> None:
        out = [
            "Hidro,Bloque,TipoEtapa,LinNum,LinNom,BarA,BarB,LinFluP,LinFluE,"
            "LinFluMax,LinUso,LinPerP,LinPerE,LinPer2P,LinPer2E,LinITP,LinITE"
        ]
        for i in range(len(self.labels)):
            for ln in self._lines:
                ca, cb = self._bus_cmg(ln["a"]), self._bus_cmg(ln["b"])
                for k, t in enumerate(self.dur):
                    f = ln["flow"][i, k]
                    loss = ln["loss"][i, k]
                    fmax = ln["tmax"][i, k]
                    if f > 0:
                        it = f * (cb[i, k] - ca[i, k]) - loss * (
                            ln["rec"] * cb[i, k] + ln["ems"] * ca[i, k]
                        )
                    else:
                        it = -f * (ca[i, k] - cb[i, k]) - loss * (
                            ln["rec"] * ca[i, k] + ln["ems"] * cb[i, k]
                        )
                    uso = abs(f) / fmax * 100.0 if fmax > 1.0 else 0.0
                    vals = (
                        f,
                        f * t * 1e-3,
                        fmax,
                        uso,
                        loss,
                        loss * t * 1e-3,
                        0.0,
                        0.0,
                        it,
                        it * t * 1e-3,
                    )
                    out.append(
                        f"{self._label(i)},{k + 1:4d},{self.tipo[self.sob[k]].ljust(12)},"
                        f"{ln['num']:5d},{ln['name'].ljust(48)},{ln['a']:4d},{ln['b']:4d}"
                        + "".join(f",{_f(v, 8, 2)}" for v in vals)
                    )
        self._write("plplin.csv", out)

    # ── reservoirs ─────────────────────────────────────────────────────
    def _fact_rendim(self, ce: _Central) -> float:
        """Summed production factor of ``ce`` and its downstream turbines."""
        w = self.writer
        assert w is not None
        total, seen = 0.0, set()
        cur: _Central | None = ce
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if cur.turbine is not None:
                total += cur.rendi
            j = w._jref.get(cur.serhid) if cur.serhid is not None else None
            cur = w._junction_central.get(id(j)) if j is not None else None
        return total

    def _junction_flows(self, ce: _Central) -> dict[str, np.ndarray]:
        c = self.case
        w = self.writer
        assert w is not None
        zeros = np.zeros((len(self.labels), len(self.dur)))
        j = ce.junction or {}
        refs = {j.get("name"), j.get("uid")} - {None}
        seep = {s.get("waterway") for s in c.elements("reservoir_seepage_array")}
        tww = (ce.turbine or {}).get("waterway")
        qver, qfil = zeros.copy(), zeros.copy()
        for ww in c.elements("waterway_array"):
            if ww.get("junction_a") not in refs:
                continue
            f = self._blk("Waterway", "flow_sol", ww.get("uid"))
            if ww.get("name") in seep or ww.get("uid") in seep:
                qfil += f
            elif tww is None or tww not in (ww.get("name"), ww.get("uid")):
                qver += f
        qver += self._blk("Junction", "drain_sol", j.get("uid"))
        aflu = zeros.copy()
        for fl in c.elements("flow_array"):
            if fl.get("junction") in refs:
                sign = -1.0 if int(fl.get("direction", 1) or 1) < 0 else 1.0
                aflu += sign * self._blk("Flow", "flow_sol", fl.get("uid"))
        return {"qver": qver, "qfil": qfil, "aflu": aflu}

    def _write_reservoirs(self) -> None:
        w = self.writer
        assert w is not None
        out = [
            "Hidro,Bloque,TipoEtapa,EmbNum,EmbNom,EmbFac,EmbVini,EmbVfin,"
            "EmbQgen,EmbQver,EmbQdef,EmbPsom,EmbPsom2,EmbAflu,EmbQFil,EmbQReb"
        ]
        zeros = np.zeros((len(self.labels), len(self.dur)))
        embs = [ce for ce in w.centrals if ce.kind == "embalse" and ce.reservoir]
        data = []
        for ce in embs:
            r = ce.reservoir or {}
            uid = r.get("uid")
            u = int(uid) if isinstance(uid, (int, np.integer)) else None
            vini = self._aug(
                self._stage_to_blocks(self.res.stage_of("Reservoir", "eini_sol", u))
            )
            vfin = self._aug(
                self._stage_to_blocks(self.res.stage_of("Reservoir", "efin_sol", u))
            )
            wv = self._blk("Reservoir", "water_value_dual", uid)
            psom2 = wv * 1e-3
            fr = self._fact_rendim(ce)
            psom = psom2 * FACT_TIEMPO / fr if fr > 0.001 else zeros
            qgen = self._turbine_flow(ce.turbine) if ce.turbine is not None else zeros
            data.append((ce, vini, vfin, qgen, psom, psom2, self._junction_flows(ce)))
        fac = fortran_e(EMB_FAC)
        scale = 1e6 / EMB_FAC  # hm3 -> m3 / EmbFac
        for i in range(len(self.labels)):
            for ce, vini, vfin, qgen, psom, psom2, fl in data:
                for k in range(len(self.dur)):
                    vals = (
                        qgen[i, k],
                        fl["qver"][i, k],
                        0.0,
                        psom[i, k],
                        psom2[i, k],
                        fl["aflu"][i, k],
                        fl["qfil"][i, k],
                        0.0,
                    )
                    out.append(
                        f"{self._label(i)},{k + 1:4d},{self.tipo[self.sob[k]].ljust(12)},"
                        f"{ce.num:4d},{ce.name.ljust(48)},{fac},"
                        f"{_f(vini[i, k] * scale, 10, 5)},{_f(vfin[i, k] * scale, 10, 5)}"
                        + "".join(f",{_f(v, 10, 2)}" for v in vals)
                    )
        self._write("plpemb.csv", out)

    # ── batteries ──────────────────────────────────────────────────────
    def _write_ess(self) -> None:
        w = self.writer
        assert w is not None
        bats = [ce for ce in w.centrals if ce.kind == "bateria"]
        if not bats:
            return
        head = (
            "Hidro, Bloque, EssNom, Efin, EMin, EMax, DCar, DCmin, DCmax, DCMod, "
            "GDes, GDMin, GDMax, PGNet, EfPSom, EfPSom2"
        )
        out = [head]
        data = []
        for ce in bats:
            bat = ce.battery or {}
            uid = bat.get("uid")
            cap = float(bat.get("capacity", 0.0) or 0.0)
            data.append(
                (
                    ce,
                    self._blk("Battery", "energy_sol", uid),
                    self._field("Battery", bat, "emin", 0.0),
                    self._field("Battery", bat, "emax", cap),
                    self._blk("Battery", "finp_sol", uid),
                    self._field("Battery", bat, "pmax_charge", 0.0),
                    self._blk("Battery", "fout_sol", uid),
                    self._field("Battery", bat, "pmax_discharge", 0.0),
                    self._blk("Battery", "energy_dual", uid),
                )
            )
        for i, label in enumerate(self.labels):
            for ce, e, emin, emax, dcar, dcmax, gdes, gdmax, dual in data:
                for k in range(len(self.dur)):
                    first = (
                        e[i, k],
                        emin[i, k],
                        emax[i, k],
                        dcar[i, k],
                        0.0,
                        dcmax[i, k],
                    )
                    last = (
                        gdes[i, k],
                        0.0,
                        gdmax[i, k],
                        gdes[i, k] - dcar[i, k],
                        dual[i, k],
                        dual[i, k] / FACT_TIEMPO,
                    )
                    out.append(
                        f"{label},{k + 1:4d}, {ce.name}"
                        + "".join(f",{_f(v, 10, 3)}" for v in first)
                        + f",{0:3d}"
                        + "".join(f",{_f(v, 10, 3)}" for v in last)
                    )
        self._write("plpess.csv", out)

    # ── operating cost / name tables ───────────────────────────────────
    def _write_costop(self) -> None:
        w = self.writer
        assert w is not None
        total = np.zeros((len(self.labels), len(self.dur)))
        for ce in w.centrals:
            s = self._cen_series.get(id(ce))
            if s is not None:
                total += s["p"] * s["cvar"]
        fail = self._fail_by_bus()
        for ce in w.centrals:
            if ce.kind == "falla" and ce.bus in fail:
                total += fail[ce.bus] * ce.cosvar
        total = total * self.dur * self.disc
        out = ["Hidro, IBlo, CostoOperActual"]
        for i, label in enumerate(self.labels):
            for k in range(len(self.dur)):
                out.append(f"{label.ljust(8)},{k + 1:4d},{_f(total[i, k], 18, 3)}")
        self._write("plpcostop.csv", out)

    def _write_name_tables(self) -> None:
        w = self.writer
        assert w is not None
        bars = ["#Numero, Nombre, Latitud"] + [
            f"{int(b['uid']):4d},{w.bus_names[int(b['uid'])].ljust(48)},  0.0000,"
            for b in w.buses
        ]
        self._write("barras.csv", bars)
        cens = ["#Numero, Nombre, Tipo, Empresa, Barra"] + [
            f"{ce.num:4d},{ce.name.ljust(48)},{CEN_LETTER[ce.kind]},   0,{ce.bus:4d},"
            for ce in w.centrals
        ]
        self._write("centrales.csv", cens)
        lins = ["#Numero, Nombre, Voltaje, Empresa, BarraA, BarraB, VATT"]
        for ln, el in zip(self._lines, self.case.elements("line_array"), strict=True):
            v = el.get("voltage")
            kv = int(v) if isinstance(v, (int, float)) else 0
            lins.append(
                f"{ln['num']:4d},{ln['name'].ljust(48)},{kv:4d},   0,{ln['a']:4d},"
                f"{ln['b']:4d},      0.00,"
            )
        self._write("lineas.csv", lins)


def write_results(
    case: GtoptCase,
    results_dir: Path,
    out_dir: Path,
    writer: PlpWriter | None = None,
) -> list[Path]:
    """Write the PLP output files of a solved run; returns their paths."""
    return PlpResultsWriter(case, Path(results_dir), Path(out_dir), writer).write()
