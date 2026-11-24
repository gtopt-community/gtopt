#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Ratha et al. IEEE-24 bus / 12-node gas system (meshed, one loop).

Source: A. Ratha, A. Schwele, J. Kazempour, P. Pinson, S. Shariat
Torbaghan, A. Virag, "Affine policies for flexibility provision by
natural gas networks to power systems", online appendix, Zenodo,
doi:10.5281/zenodo.3755120, CC BY 4.0.  ``data/*.csv`` are the
unmodified ``Model_Data/24el_12ng`` files.

Deterministic reduction (documented in README.md): expected wind, hours
independent (no line pack), no reserves / affine policies.  The gas loop
4-5-6-8-7-4 makes the network meshed, so every pipe is
``weymouth_mode: linearized`` (tangent) and gtopt's gas SLP iterates to
the exact Weymouth equations; the compressor pipes (Gamma > 1) are a
compressor into an internal node followed by the pipe.

Writes ``gas_ratha_24el_12ng.json`` and ``expected.json`` holding, per
hour, the exact steady-state Weymouth optimum from scipy: an
independent SLP (same model, numpy/HiGHS) polished by SLSQP on the full
NLP, plus the max Weymouth residual of that point.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import linprog, minimize

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
NB = 24
GAS_FAIL_COST = 1000.0  # $/unit of unserved non-electric gas
POWER_FAIL_COST = 1000.0  # $/MWh


def _csv(name: str) -> list[dict[str, float]]:
    with (DATA / name).open(encoding="utf-8-sig") as fh:
        return [{k: float(v) for k, v in row.items()} for row in csv.DictReader(fh)]


def model() -> dict:
    """Plain description shared by the gtopt JSON and the references."""
    gbus = _csv("ng_bus_data.csv")
    nodes = {
        f"N{int(r['ngBusNum'])}": (r["Pre_min"], r["Pre_max"], r["G_dem_share"])
        for r in gbus
    }
    pipes, comps = [], []
    for r in _csv("ng_line_data.csv"):
        a, b = f"N{int(r['From'])}", f"N{int(r['To'])}"
        k, gam = 1.0 / r["Kmu"] ** 2, r["Gamma"]
        name = f"P{int(r['PipeLineNum'])}"
        if gam > 1.0:
            mid = f"{a}c{int(r['PipeLineNum'])}"
            lo, hi, _ = nodes[a]
            nodes[mid] = (lo, gam * hi, 0.0)
            comps.append((f"C{int(r['PipeLineNum'])}", a, mid, gam))
            a = mid
        pmax = max(nodes[a][1], nodes[b][1])
        pmin = min(nodes[a][0], nodes[b][0])
        fmax = r["Kmu"] * math.sqrt(pmax**2 - pmin**2)
        pipes.append((name, a, b, k, fmax))
    sup = [
        (f"S{int(r['ngProdNum'])}", f"N{int(r['Gnode'])}", r["Prod_max"], r["C_prod"])
        for r in _csv("ng_producers.csv")
    ]
    hourly = _csv("hourlyDemand.csv")[:NB]
    wf = _csv("wind_factors.csv")[:NB]
    gens = []
    for r in _csv("all_gens.csv"):
        gas = r["ngfpp_y1_n0"] > 0
        gens.append(
            (
                f"G{int(r['UnitNum'])}",
                int(r["elBusNum"]),
                r["PG_max"],
                r["ng_C_OM"] if gas else r["C_1"],
                f"N{int(r['ngBusNum'])}" if gas else None,
                r["ng_ConvEff"] if gas else 0.0,
                None,
            )
        )
    for r in _csv("wind_gens.csv"):
        w = int(r["WindNum"])
        gens.append(
            (
                f"W{w}",
                int(r["elBusNum"]),
                r["W_instcap"],
                0.0,
                None,
                0.0,
                [r["W_instcap"] * h[f"WF{w}"] for h in wf],
            )
        )
    shares = {int(r["elBusNum"]): r["P_dem_share"] for r in _csv("el_bus_data.csv")}
    loads = {b: [s * h["elTotDem"] for h in hourly] for b, s in shares.items() if s > 0}
    gd = {
        n: [sh * h["ngTotDem"] for h in hourly]
        for n, (_, _, sh) in nodes.items()
        if sh > 0
    }
    lines = [
        (int(r["LineNum"]), int(r["From"]), int(r["To"]), r["adm"], r["f_max"])
        for r in _csv("el_line_data.csv")
    ]
    return {
        "nodes": nodes,
        "pipes": pipes,
        "comps": comps,
        "sup": sup,
        "gens": gens,
        "loads": loads,
        "gd": gd,
        "lines": lines,
        "nbus": 24,
    }


def gtopt_planning(
    m: dict,
    name: str = "gas_ratha_24el_12ng",
    dirs: list[int] | None = None,
    segments: int = 16,
    gas_fail_cost: float = GAS_FAIL_COST,
) -> dict:
    """gtopt planning of ``m``: linearized pipes + SLP, or with ``dirs``
    (+1 = a -> b, -1 = b -> a per pipe) the radial tangent form."""

    def pipe(i: int, p: tuple) -> dict:
        pname, a, b, k, fm = p
        out = {"uid": i, "name": pname, "weymouth_k": k, "fmax": fm}
        if dirs is None:
            out.update(
                gas_node_a=a,
                gas_node_b=b,
                weymouth_mode="linearized",
                linearization="tangent",
            )
        else:
            a, b = (a, b) if dirs[i - 1] > 0 else (b, a)
            out.update(gas_node_a=a, gas_node_b=b, segments=segments)
        return out

    bus = [{"uid": i, "name": f"b{i}"} for i in range(1, m["nbus"] + 1)]
    gens, turbines = [], []
    for i, (gname, b, pmax, cost, gnode, hr, prof) in enumerate(m["gens"], start=1):
        g = {
            "uid": i,
            "name": gname,
            "bus": f"b{b}",
            "pmax": [prof] if prof else pmax,
            "capacity": pmax,
            "gcost": cost,
        }
        gens.append(g)
        if gnode:
            turbines.append(
                {
                    "uid": len(turbines) + 1,
                    "name": f"{gname}_gas",
                    "generator": gname,
                    "gas_node": gnode,
                    "heat_rate": hr,
                }
            )
    names = list(m["nodes"])
    return {
        "options": {
            "annual_discount_rate": 0.0,
            "output_format": "csv",
            "output_compression": "uncompressed",
            "model_options": {
                "use_single_bus": False,
                "use_kirchhoff": True,
                "scale_objective": 1000,
                "demand_fail_cost": POWER_FAIL_COST,
            },
            **(
                {
                    "gas_options": {
                        "slp_iterations": 200,
                        "slp_tolerance": 1e-7,
                        "slp_damping": 1.0,
                    }
                }
                if dirs is None
                else {}
            ),
        },
        "simulation": {
            "block_array": [{"uid": t + 1, "duration": 1} for t in range(NB)],
            "stage_array": [
                {"uid": 1, "first_block": 0, "count_block": NB, "chronological": True}
            ],
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        },
        "system": {
            "name": name,
            "bus_array": bus,
            "generator_array": gens,
            "demand_array": [
                {"uid": i, "name": f"d{b}", "bus": f"b{b}", "lmax": [ld]}
                for i, (b, ld) in enumerate(m["loads"].items(), start=1)
            ],
            "line_array": [
                {
                    "uid": i,
                    "name": f"l{i}_{a}_{b}",
                    "bus_a": f"b{a}",
                    "bus_b": f"b{b}",
                    "reactance": x,
                    "tmax_ab": fm,
                    "tmax_ba": fm,
                }
                for i, a, b, x, fm in m["lines"]
            ],
            "gas_node_array": [
                {"uid": i, "name": n, "pmin": lo, "pmax": hi}
                for i, (n, (lo, hi, _)) in enumerate(m["nodes"].items(), start=1)
            ],
            "gas_supply_array": [
                {"uid": i, "name": s, "gas_node": n, "fmax": fm, "cost": c}
                for i, (s, n, fm, c) in enumerate(m["sup"], start=1)
            ],
            "gas_demand_array": [
                {
                    "uid": i,
                    "name": f"GD{n}",
                    "gas_node": n,
                    "load": [q],
                    "fail_cost": gas_fail_cost,
                }
                for i, (n, q) in enumerate(m["gd"].items(), start=1)
            ],
            "gas_pipe_array": [pipe(i, p) for i, p in enumerate(m["pipes"], start=1)],
            "gas_compressor_array": [
                {
                    "uid": i,
                    "name": c,
                    "gas_node_in": a,
                    "gas_node_out": b,
                    "fmax": 1e5,
                    "max_ratio": gam,
                    "fuel_model": "none",
                    "allow_bypass": False,
                }
                for i, (c, a, b, gam) in enumerate(m["comps"], start=1)
            ],
            "gas_turbine_array": turbines,
        },
        "_node_order": names,
    }


def ptdf(m: dict) -> np.ndarray:
    nbus = m["nbus"]
    bmat = np.zeros((nbus, nbus))
    for _, a, b, x, _ in m["lines"]:
        y = 1.0 / x
        bmat[a - 1, a - 1] += y
        bmat[b - 1, b - 1] += y
        bmat[a - 1, b - 1] -= y
        bmat[b - 1, a - 1] -= y
    binv = np.zeros((nbus, nbus))
    binv[1:, 1:] = np.linalg.inv(bmat[1:, 1:])
    out = np.zeros((len(m["lines"]), nbus))
    for k, (_, a, b, x, _) in enumerate(m["lines"]):
        out[k] = (binv[a - 1] - binv[b - 1]) / x
    return out


class Hour:
    """One hour: variables, linear rows and the Weymouth equalities."""

    def __init__(self, m: dict, t: int) -> None:
        self.m = m
        nodes = list(m["nodes"])
        names = [f"pi_{n}" for n in nodes]
        names += [f"f_{p[0]}" for p in m["pipes"]]
        names += [f"c_{c[0]}" for c in m["comps"]]
        names += [s[0] for s in m["sup"]]
        names += [f"gfail_{n}" for n in m["gd"]]
        names += [g[0] for g in m["gens"]]
        names += [f"efail_{b}" for b in m["loads"]]
        self.ix = {n: i for i, n in enumerate(names)}
        nv = self.nv = len(names)
        lo, hi = np.zeros(nv), np.zeros(nv)
        for n, (pl, ph, _) in m["nodes"].items():
            lo[self.ix[f"pi_{n}"]], hi[self.ix[f"pi_{n}"]] = pl**2, ph**2
        for name, _a, _b, _k, fm in m["pipes"]:
            lo[self.ix[f"f_{name}"]], hi[self.ix[f"f_{name}"]] = -fm, fm
        for c in m["comps"]:
            hi[self.ix[f"c_{c[0]}"]] = 1e5
        for name, _n, fm, _c in m["sup"]:
            hi[self.ix[name]] = fm
        for n, q in m["gd"].items():
            hi[self.ix[f"gfail_{n}"]] = q[t]
        for name, _b, pmax, _c, _gn, _hr, prof in m["gens"]:
            hi[self.ix[name]] = prof[t] if prof else pmax
        for b, ld in m["loads"].items():
            hi[self.ix[f"efail_{b}"]] = ld[t]
        self.bounds = list(zip(lo, hi))
        cost = np.zeros(nv)
        for name, _n, _f, c in m["sup"]:
            cost[self.ix[name]] = c
        for n in m["gd"]:
            cost[self.ix[f"gfail_{n}"]] = m.get("gas_fail_cost", GAS_FAIL_COST)
        for name, _b, _p, c, *_ in m["gens"]:
            cost[self.ix[name]] = c
        for b in m["loads"]:
            cost[self.ix[f"efail_{b}"]] = POWER_FAIL_COST
        self.cost = cost
        aeq, beq = [], []
        for n in nodes:
            r = np.zeros(nv)
            for name, a, b, _k, _f in m["pipes"]:
                r[self.ix[f"f_{name}"]] = (b == n) - (a == n)
            for c, a, b, _g in m["comps"]:
                r[self.ix[f"c_{c}"]] = (b == n) - (a == n)
            for name, sn, _f, _c in m["sup"]:
                if sn == n:
                    r[self.ix[name]] = 1
            for name, _b, _p, _c, gn, hr, _pr in m["gens"]:
                if gn == n:
                    r[self.ix[name]] = -hr
            q = 0.0
            if n in m["gd"]:
                r[self.ix[f"gfail_{n}"]] = 1
                q = m["gd"][n][t]
            aeq.append(r)
            beq.append(q)
        nbus = m["nbus"]
        inj = np.zeros((nbus, nv))
        for name, b, *_ in m["gens"]:
            inj[b - 1, self.ix[name]] = 1
        load = np.zeros(nbus)
        for b, ld in m["loads"].items():
            inj[b - 1, self.ix[f"efail_{b}"]] = 1
            load[b - 1] = ld[t]
        aeq.append(inj.sum(axis=0))
        beq.append(load.sum())
        self.aeq, self.beq = np.array(aeq), np.array(beq)
        pt = ptdf(m)
        aub, bub = [], []
        for k, (*_, fmx) in enumerate(m["lines"]):
            flow, off = pt[k] @ inj, pt[k] @ load
            aub += [flow, -flow]
            bub += [fmx + off, fmx - off]
        for _c, a, b, gam in m["comps"]:
            r = np.zeros(nv)
            r[self.ix[f"pi_{b}"]], r[self.ix[f"pi_{a}"]] = 1, -(gam**2)
            aub.append(r)
            bub.append(0.0)
            r = np.zeros(nv)
            r[self.ix[f"pi_{a}"]], r[self.ix[f"pi_{b}"]] = 1, -1
            aub.append(r)
            bub.append(0.0)
        self.aub, self.bub = np.array(aub), np.array(bub)
        self.wey = [
            (self.ix[f"pi_{a}"], self.ix[f"pi_{b}"], self.ix[f"f_{name}"], k)
            for name, a, b, k, _f in m["pipes"]
        ]

    def slp(self, iters: int = 200, tol: float = 1e-9):
        """Tangent SLP, undamped (scipy/HiGHS), from f0 = 0.1 fmax."""
        f0 = np.array([0.1 * self.bounds[jf][1] for _ia, _ib, jf, _k in self.wey])
        res = None
        for _ in range(iters):
            rows, rhs = [], []
            for (ia, ib, jf, k), f in zip(self.wey, f0):
                r = np.zeros(self.nv)
                r[ia], r[ib], r[jf] = 1, -1, -2 * k * abs(f)
                rows.append(r)
                rhs.append(-k * f * abs(f))
            res = linprog(
                self.cost,
                A_ub=self.aub,
                b_ub=self.bub,
                A_eq=np.vstack([self.aeq, rows]),
                b_eq=np.concatenate([self.beq, rhs]),
                bounds=self.bounds,
                method="highs",
            )
            assert res.status == 0, res.message
            f = np.array([res.x[jf] for _ia, _ib, jf, _k in self.wey])
            fmin = np.array([1e-3 * self.bounds[jf][1] for *_, jf, _k in self.wey])
            change = np.max(np.abs(f - f0) / np.maximum(np.abs(f0), fmin))
            f0 = np.where(np.abs(f) < fmin, np.where(f < 0, -fmin, fmin), f)
            if change < tol:
                break
        return res

    def residual(self, x) -> float:
        return max(
            abs(x[ia] - x[ib] - k * x[jf] * abs(x[jf])) for ia, ib, jf, k in self.wey
        )

    def polish(self, x0):
        """SLSQP on the full Weymouth NLP from ``x0`` (local check)."""
        scale = np.maximum(np.abs(np.array([b[1] for b in self.bounds])), 1.0)
        cs = self.cost * scale / np.abs(self.cost * scale).max()
        aeq, aub = self.aeq * scale, self.aub * scale
        req = np.maximum(np.abs(aeq).max(axis=1), 1e-12)
        rub = np.maximum(np.abs(aub).max(axis=1), 1e-12)
        sw = max(scale[ia] for ia, *_ in self.wey)

        def wfun(z):
            x = z * scale
            return np.array(
                [
                    (x[ia] - x[ib] - k * x[jf] * abs(x[jf])) / sw
                    for ia, ib, jf, k in self.wey
                ]
            )

        def wjac(z):
            x = z * scale
            jac = np.zeros((len(self.wey), self.nv))
            for r, (ia, ib, jf, k) in enumerate(self.wey):
                jac[r, ia] = scale[ia] / sw
                jac[r, ib] = -scale[ib] / sw
                jac[r, jf] = -2 * k * abs(x[jf]) * scale[jf] / sw
            return jac

        bounds = [(lo / s, hi / s) for (lo, hi), s in zip(self.bounds, scale)]
        res = minimize(
            lambda z: cs @ z,
            np.clip(x0 / scale, [b[0] for b in bounds], [b[1] for b in bounds]),
            jac=lambda z: cs,
            bounds=bounds,
            constraints=[
                {
                    "type": "eq",
                    "fun": lambda z: (aeq @ z - self.beq) / req,
                    "jac": lambda z: aeq / req[:, None],
                },
                {
                    "type": "ineq",
                    "fun": lambda z: (self.bub - aub @ z) / rub,
                    "jac": lambda z: -aub / rub[:, None],
                },
                {"type": "eq", "fun": wfun, "jac": wjac},
            ],
            method="SLSQP",
            options={"maxiter": 2000, "ftol": 1e-15},
        )
        res.x = res.x * scale
        return res


def main() -> None:
    m = model()
    planning = gtopt_planning(m)
    order = planning.pop("_node_order")
    (HERE / "gas_ratha_24el_12ng.json").write_text(
        json.dumps(planning, indent=1) + "\n"
    )
    hours = []
    for t in range(NB):
        h = Hour(m, t)
        slp = h.slp()
        pol = h.polish(slp.x)
        obj_slp = float(h.cost @ slp.x)
        obj_nlp = float(h.cost @ pol.x) if pol.success else None
        hours.append(
            {
                "slp_objective": obj_slp,
                "slp_residual": h.residual(slp.x),
                "nlp_objective": obj_nlp,
                "nlp_residual": h.residual(pol.x) if pol.success else None,
                "pressure": {
                    n: math.sqrt(max(slp.x[h.ix[f"pi_{n}"]], 0.0)) for n in order
                },
                "flow": {p[0]: float(slp.x[h.ix[f"f_{p[0]}"]]) for p in m["pipes"]},
            }
        )
        print(t + 1, round(obj_slp, 4), obj_nlp and round(obj_nlp, 4))
    expected = {
        "objective_slp": sum(x["slp_objective"] for x in hours),
        "objective_nlp": sum(
            x["nlp_objective"] if x["nlp_objective"] is not None else x["slp_objective"]
            for x in hours
        ),
        "hours": hours,
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=1) + "\n")
    print("SLP", expected["objective_slp"], "NLP", expected["objective_nlp"])


if __name__ == "__main__":
    main()
