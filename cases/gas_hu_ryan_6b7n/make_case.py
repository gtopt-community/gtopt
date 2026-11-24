#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Hu & Ryan 6-bus / 7-node coupled power-gas case (CC BY 4.0).

Source: D. Hu, S. M. Ryan, "Data for: Stochastic vs. deterministic
scheduling of a combined natural gas and power system with uncertain
wind energy", Mendeley Data, v1, doi:10.17632/ynb88g67xy.1, sheet
``6bus_7gasnodes`` (+ the forecast wind sheet), CC BY 4.0.  The
spreadsheet is vendored unmodified next to this script (sha256 in
README.md); CC BY 4.0 permits redistribution with attribution.

The LP-representable reduction (documented in README.md):

* commitment relaxed (pmin = 0, no ramps / min up-down), 24 h,
  independent hours;
* daily supplier caps spread evenly over the day (MaxOP / 24);
* the node-1 storage acts as a priced supply (storcost) limited to its
  initial level / 24 per hour (no injection);
* pipes oriented by enumerating the 2^5 orientations of the radial tree
  in the tangent LP (the exact Weymouth optimum uses the same).

Writes ``gas_hu_ryan_6b7n.json`` and ``expected.json`` with

* ``exact``: per-hour Weymouth optimum: the 4000-tangent LP flows with
  pressures re-solved from the exact equality pi_a - pi_b = K f|f|
  (feasible, hence an exact-Weymouth point at the relaxation cost);
* ``tangent_lp``: the tangent LP gtopt builds (same segments), solved by
  scipy/HiGHS;
* ``bound``: the outer-approximation error bound per hour.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import linprog, minimize

HERE = Path(__file__).resolve().parent
XLSX = HERE / "Data_IISE_DH_SMR.xlsx"
SHA256 = "e45b394338fdbc7fc5fda978dafba555a5597569fa8218103fb0d37bf63a9f3a"
SEGMENTS = 16
NB = 24
GAS_FAIL_COST = 50.0  # $/kcf, non-electric gas shedding
POWER_FAIL_COST = 1000.0  # $/MWh


def read_data() -> dict:
    """The sheet as plain lists (needs openpyxl)."""
    import openpyxl  # pylint: disable=import-outside-toplevel

    digest = hashlib.sha256(XLSX.read_bytes()).hexdigest()
    if digest != SHA256:
        sys.exit(f"{XLSX}: sha256 {digest} != pinned {SHA256}")
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    rows = [
        [v for v in r if v is not None]
        for r in wb["6bus_7gasnodes"].iter_rows(values_only=True)
    ]
    rows = [r for r in rows if r]
    wind = [
        float(r[1])
        for r in wb["6bus_7gasnodes(Forecast Wind)"].iter_rows(values_only=True)
        if isinstance(r[0], int) and r[1] is not None
    ][:NB]

    def block(title: str, ncol: int) -> list[list[float]]:
        i = next(k for k, r in enumerate(rows) if str(r[0]).startswith(title))
        out = []
        for r in rows[i + 1 :]:
            if isinstance(r[0], (int, float)) and len(r) >= ncol:
                out.append([float(x) for x in r[:ncol]])
            elif out:
                break
        return out

    return {
        "load": block("Electricity load data", 5)[:NB],
        "gens": block("Parameters of generators", 13),
        "lines": block("Parameters of power transmission branch", 5),
        "nodes": block("Parameters of nodes in gas nodes", 4),
        "pipes": block("Parameters of passive gas pipelines", 4),
        "comps": block("Parameters of natural gas compressors", 3),
        "storage": block("Gas storage facilities data", 7),
        "suppliers": block("Parameters of natural gas supplier", 5),
        "gas_demand": block("Gas demand", 2),
        "wind": wind,
    }


def build(d: dict) -> dict:
    """Plain model description shared by gtopt JSON and the references."""
    nodes = {int(n): (math.sqrt(lo), math.sqrt(hi)) for n, lo, hi, _ in d["nodes"]}
    pipes = [(int(a), int(b), 1.0 / c**2, c) for _, a, b, c in d["pipes"]]
    comps = [(int(a), int(b), math.sqrt(t)) for a, b, t in d["comps"]]
    sup = [
        (f"SUP{int(i)}", int(n), mx / NB, cst) for i, n, _, mx, cst in d["suppliers"]
    ]
    for i, n, lmax, _lmin, qmax, scost, ini in d["storage"]:
        del lmax
        sup.append((f"STOR{int(i)}", int(n), min(qmax, ini / NB), scost))
    gd = [(int(n), q) for n, q in d["gas_demand"] if q > 0]
    gens = []
    for m, bus, gnode, _pmin, pmax, *_rest in d["gens"]:
        cprod, hr = _rest[6], _rest[7]
        gens.append(
            (f"G{int(m)}", int(bus), int(gnode) if hr > 0 else None, pmax, cprod, hr)
        )
    lines = [(int(i), int(a), int(b), x, fmax) for i, a, b, x, fmax in d["lines"]]
    loads = {
        3: [r[2] for r in d["load"]],
        4: [r[3] for r in d["load"]],
        5: [r[4] for r in d["load"]],
    }
    return {
        "nodes": nodes,
        "pipes": pipes,
        "comps": comps,
        "sup": sup,
        "gd": gd,
        "gens": gens,
        "lines": lines,
        "loads": loads,
        "wind": d["wind"],
    }


def ptdf(m: dict) -> np.ndarray:
    """Line x bus PTDF with bus 1 as slack."""
    nbus = 6
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


def fmax_of(m: dict, a: int, b: int, c: float) -> float:
    """Largest physical flow a -> b from the pressure bands."""
    return c * math.sqrt(max(m["nodes"][a][1] ** 2 - m["nodes"][b][0] ** 2, 0.0))


class Hour:
    """One hour: variables and linear rows, shared by LP and NLP."""

    def __init__(self, m: dict, t: int, dirs: tuple[int, ...]) -> None:
        self.m, self.dirs = m, dirs
        names = [f"pi{n}" for n in range(1, 8)]
        names += [f"f{i}" for i in range(len(m["pipes"]))]
        names += [f"c{i}" for i in range(len(m["comps"]))]
        names += [s[0] for s in m["sup"]]
        names += [f"gfail{n}" for n, _ in m["gd"]]
        names += [g[0] for g in m["gens"]] + ["W"]
        names += [f"efail{b}" for b in m["loads"]]
        self.ix = {n: i for i, n in enumerate(names)}
        nv = len(names)
        self.nv = nv
        lo, hi = np.zeros(nv), np.zeros(nv)
        for n, (pl, ph) in m["nodes"].items():
            lo[self.ix[f"pi{n}"]], hi[self.ix[f"pi{n}"]] = pl**2, ph**2
        for i, (a, b, _k, c) in enumerate(m["pipes"]):
            s = dirs[i]
            fm = fmax_of(m, a, b, c) if s > 0 else fmax_of(m, b, a, c)
            lo[self.ix[f"f{i}"]], hi[self.ix[f"f{i}"]] = (0, fm) if s > 0 else (-fm, 0)
        for i, _ in enumerate(m["comps"]):
            hi[self.ix[f"c{i}"]] = 1e5
        for name, _n, fmx, _ in m["sup"]:
            hi[self.ix[name]] = fmx
        for n, q in m["gd"]:
            hi[self.ix[f"gfail{n}"]] = q
        for name, _b, _gn, pmax, _c, _hr in m["gens"]:
            hi[self.ix[name]] = pmax
        hi[self.ix["W"]] = m["wind"][t]
        for b, ld in m["loads"].items():
            hi[self.ix[f"efail{b}"]] = ld[t]
        self.bounds = list(zip(lo, hi))
        cost = np.zeros(nv)
        for name, _n, _f, cst in m["sup"]:
            cost[self.ix[name]] = cst
        for n, _ in m["gd"]:
            cost[self.ix[f"gfail{n}"]] = GAS_FAIL_COST
        for name, _b, _gn, _p, cprod, _hr in m["gens"]:
            cost[self.ix[name]] = cprod
        for b in m["loads"]:
            cost[self.ix[f"efail{b}"]] = POWER_FAIL_COST
        self.cost = cost
        aeq, beq = [], []
        for n in range(1, 8):  # gas balances
            r = np.zeros(nv)
            for i, (a, b, *_) in enumerate(m["pipes"]):
                r[self.ix[f"f{i}"]] = (b == n) - (a == n)
            for i, (a, b, _) in enumerate(m["comps"]):
                r[self.ix[f"c{i}"]] = (b == n) - (a == n)
            for name, sn, _f, _c in m["sup"]:
                if sn == n:
                    r[self.ix[name]] = 1
            for name, _b, gn, _p, _c, hr in m["gens"]:
                if gn == n:
                    r[self.ix[name]] = -hr
            q = 0.0
            for gn, dq in m["gd"]:
                if gn == n:
                    r[self.ix[f"gfail{n}"]] = 1
                    q = dq
            aeq.append(r)
            beq.append(q)
        inj = np.zeros((6, nv))  # power injections
        for name, b, *_ in m["gens"]:
            inj[b - 1, self.ix[name]] = 1
        inj[0, self.ix["W"]] = 1
        load = np.zeros(6)
        for b, ld in m["loads"].items():
            inj[b - 1, self.ix[f"efail{b}"]] = 1
            load[b - 1] = ld[t]
        aeq.append(inj.sum(axis=0))
        beq.append(load.sum())
        self.aeq, self.beq = np.array(aeq), np.array(beq)
        pt = ptdf(m)
        aub, bub = [], []
        for k, (*_, fmx) in enumerate(m["lines"]):
            flow = pt[k] @ inj
            off = pt[k] @ load
            aub += [flow, -flow]
            bub += [fmx + off, fmx - off]
        for i, (a, b, rmax) in enumerate(m["comps"]):
            r = np.zeros(nv)
            r[self.ix[f"pi{b}"]], r[self.ix[f"pi{a}"]] = 1, -(rmax**2)
            aub.append(r)
            bub.append(0.0)
            r = np.zeros(nv)
            r[self.ix[f"pi{a}"]], r[self.ix[f"pi{b}"]] = 1, -1
            aub.append(r)
            bub.append(0.0)
        self.aub, self.bub = aub, bub

    def lp(self, nseg: int):
        aub, bub = list(self.aub), list(self.bub)
        for i, (a, b, k, _c) in enumerate(self.m["pipes"]):
            s = self.dirs[i]
            hi, lo = (a, b) if s > 0 else (b, a)
            fm = abs(self.bounds[self.ix[f"f{i}"]][1 if s > 0 else 0])
            for j in range(nseg):
                tk = (2 * j + 1) * fm / (2 * nseg)
                r = np.zeros(self.nv)
                r[self.ix[f"pi{hi}"]], r[self.ix[f"pi{lo}"]] = -1, 1
                r[self.ix[f"f{i}"]] = 2 * k * tk * s
                aub.append(r)
                bub.append(k * tk * tk)
        return linprog(
            self.cost,
            A_ub=np.array(aub),
            b_ub=bub,
            A_eq=self.aeq,
            b_eq=self.beq,
            bounds=self.bounds,
            method="highs",
        )

    def exact_from(self, x):
        """Exact-Weymouth point with the flows of ``x``: pressures solve
        pi_a - pi_b = K f|f| (an LP in pi, flows fixed).  Feasible ->
        the Weymouth NLP attains the relaxation cost (cost is
        pressure-free), so this point is its optimum up to the tangent
        error of ``x``."""
        aeq, beq = [], []
        for i, (a, b, k, _c) in enumerate(self.m["pipes"]):
            r = np.zeros(self.nv)
            r[self.ix[f"pi{a}"]], r[self.ix[f"pi{b}"]] = 1, -1
            f = x[self.ix[f"f{i}"]]
            aeq.append(r)
            beq.append(k * f * abs(f))
        bounds = list(self.bounds)
        for j in range(self.nv):
            if not self.ix_is_pi(j):
                bounds[j] = (x[j], x[j])
        obj = np.array([-1.0 if self.ix_is_pi(j) else 0.0 for j in range(self.nv)])
        return linprog(
            obj,
            A_ub=np.array(self.aub),
            b_ub=self.bub,
            A_eq=np.vstack([self.aeq, aeq]),
            b_eq=np.concatenate([self.beq, beq]),
            bounds=bounds,
            method="highs",
        )

    def ix_is_pi(self, j: int) -> bool:
        return any(j == self.ix[f"pi{n}"] for n in range(1, 8))

    def nlp(self, x0):
        """Exact Weymouth NLP (SLSQP on scaled variables, analytic
        Jacobians)."""
        scale = np.ones(self.nv)
        for n in range(1, 8):
            scale[self.ix[f"pi{n}"]] = 1e4
        for i in range(len(self.m["pipes"])):
            scale[self.ix[f"f{i}"]] = 1e3
        for name in [s[0] for s in self.m["sup"]] + [
            f"c{i}" for i in range(len(self.m["comps"]))
        ]:
            scale[self.ix[name]] = 1e3
        for n, _ in self.m["gd"]:
            scale[self.ix[f"gfail{n}"]] = 1e3
        for name in (
            [g[0] for g in self.m["gens"]]
            + ["W"]
            + [f"efail{b}" for b in self.m["loads"]]
        ):
            scale[self.ix[name]] = 1e2
        aeq = self.aeq * scale
        aub = np.array(self.aub) * scale
        req = np.abs(aeq).max(axis=1)
        rub = np.abs(aub).max(axis=1)
        cs = self.cost * scale
        cs = cs / np.abs(cs).max()
        weq = []
        for i, (a, b, k, _c) in enumerate(self.m["pipes"]):
            weq.append((self.ix[f"pi{a}"], self.ix[f"pi{b}"], self.ix[f"f{i}"], k))

        def wfun(z):
            x = z * scale
            return np.array(
                [
                    (x[ia] - x[ib] - k * x[jf] * abs(x[jf])) / 1e4
                    for ia, ib, jf, k in weq
                ]
            )

        def wjac(z):
            x = z * scale
            jac = np.zeros((len(weq), self.nv))
            for r, (ia, ib, jf, k) in enumerate(weq):
                jac[r, ia] = scale[ia] / 1e4
                jac[r, ib] = -scale[ib] / 1e4
                jac[r, jf] = -2 * k * abs(x[jf]) * scale[jf] / 1e4
            return jac

        cons = [
            {
                "type": "eq",
                "fun": lambda z: (aeq @ z - self.beq) / req,
                "jac": lambda z: aeq / req[:, None],
            },
            {
                "type": "ineq",
                "fun": lambda z: (np.array(self.bub) - aub @ z) / rub,
                "jac": lambda z: -aub / rub[:, None],
            },
            {"type": "eq", "fun": wfun, "jac": wjac},
        ]
        bounds = [
            (lo / sc if lo is not None else None, hi / sc if hi is not None else None)
            for (lo, hi), sc in zip(self.bounds, scale)
        ]
        z0 = np.clip(x0 / scale, [b[0] for b in bounds], [b[1] for b in bounds])
        res = minimize(
            lambda z: cs @ z,
            z0,
            jac=lambda z: cs,
            bounds=bounds,
            constraints=cons,
            method="SLSQP",
            options={"maxiter": 10000, "ftol": 1e-14},
        )
        res.x = res.x * scale
        res.fun = float(self.cost @ res.x)
        res.maxviol = float(np.max(np.abs(wfun(res.x / scale)))) * 1e4
        return res


def main() -> None:
    m = build(read_data())
    npipe = len(m["pipes"])
    best = None
    for dirs in itertools.product([1, -1], repeat=npipe):
        tot = 0.0
        for t in range(NB):
            r = Hour(m, t, dirs).lp(64)
            if r.status != 0:
                break
            tot += r.fun
        else:
            if best is None or tot < best[1]:
                best = (dirs, tot)
    assert best is not None
    dirs = best[0]
    hours = []
    for t in range(NB):
        h = Hour(m, t, dirs)
        lp = h.lp(SEGMENTS)
        fine = h.lp(4000)
        nlp = h.exact_from(fine.x)
        if nlp.status == 0:  # relaxation tight: exact point at LP cost
            nlp.fun = float(h.cost @ nlp.x)
        else:
            nlp = min(
                (h.nlp(x0) for x0 in (fine.x, lp.x)),
                key=lambda r: (not r.success, r.fun),
            )
            assert nlp.success and nlp.maxviol < 1e-3, (t, nlp.message, nlp.maxviol)
        bound = sum(
            k
            * (abs(h.bounds[h.ix[f"f{i}"]][1 if dirs[i] > 0 else 0]) / (2 * SEGMENTS))
            ** 2
            for i, (_a, _b, k, _c) in enumerate(m["pipes"])
        )
        hours.append(
            {
                "tangent_lp": lp.fun,
                "exact": nlp.fun,
                "pi_bound_sum": bound,
                "pressure_exact": {
                    f"N{n}": math.sqrt(max(nlp.x[h.ix[f"pi{n}"]], 0))
                    for n in range(1, 8)
                },
                "G1": nlp.x[h.ix["G1"]],
                "G3": nlp.x[h.ix["G3"]],
            }
        )
    write_case(m, dirs)
    exp = {
        "pipe_orientation": list(dirs),
        "segments": SEGMENTS,
        "objective_tangent_lp": sum(h["tangent_lp"] for h in hours),
        "objective_exact": sum(h["exact"] for h in hours),
        "hours": hours,
    }
    (HERE / "expected.json").write_text(json.dumps(exp, indent=1) + "\n")
    print(exp["objective_tangent_lp"], exp["objective_exact"], dirs)


def write_case(m: dict, dirs: tuple[int, ...]) -> None:
    pipes = []
    for i, ((a, b, k, c), s) in enumerate(zip(m["pipes"], dirs), start=1):
        fa, fb = (a, b) if s > 0 else (b, a)
        pipes.append(
            {
                "uid": i,
                "name": f"P{a}_{b}",
                "gas_node_a": f"N{fa}",
                "gas_node_b": f"N{fb}",
                "weymouth_k": k,
                "fmax": fmax_of(m, fa, fb, c),
                "segments": SEGMENTS,
            }
        )
    gens = [
        {
            "uid": i,
            "name": name,
            "bus": f"b{b}",
            "pmax": p,
            "capacity": p,
            "gcost": cprod,
        }
        for i, (name, b, _gn, p, cprod, _hr) in enumerate(m["gens"], start=1)
    ]
    gens.append(
        {
            "uid": len(gens) + 1,
            "name": "W",
            "bus": "b1",
            "pmax": [m["wind"]],
            "capacity": 400.0,
            "gcost": 0.0,
        }
    )
    system = {
        "name": "hu_ryan_6b7n",
        "bus_array": [{"uid": b, "name": f"b{b}"} for b in range(1, 7)],
        "generator_array": gens,
        "demand_array": [
            {"uid": i, "name": f"d{b}", "bus": f"b{b}", "lmax": [ld]}
            for i, (b, ld) in enumerate(m["loads"].items(), start=1)
        ],
        "line_array": [
            {
                "uid": i,
                "name": f"l{a}_{b}",
                "bus_a": f"b{a}",
                "bus_b": f"b{b}",
                "reactance": x,
                "tmax_ab": fm,
                "tmax_ba": fm,
            }
            for i, a, b, x, fm in m["lines"]
        ],
        "gas_node_array": [
            {"uid": n, "name": f"N{n}", "pmin": lo, "pmax": hi}
            for n, (lo, hi) in m["nodes"].items()
        ],
        "gas_supply_array": [
            {"uid": i, "name": name, "gas_node": f"N{n}", "fmax": f, "cost": c}
            for i, (name, n, f, c) in enumerate(m["sup"], start=1)
        ],
        "gas_demand_array": [
            {
                "uid": i,
                "name": f"GD{n}",
                "gas_node": f"N{n}",
                "load": q,
                "fail_cost": GAS_FAIL_COST,
            }
            for i, (n, q) in enumerate(m["gd"], start=1)
        ],
        "gas_pipe_array": pipes,
        "gas_compressor_array": [
            {
                "uid": i,
                "name": f"C{a}_{b}",
                "gas_node_in": f"N{a}",
                "gas_node_out": f"N{b}",
                "fmax": 1e5,
                "max_ratio": r,
                "fuel_model": "none",
                "allow_bypass": False,
            }
            for i, (a, b, r) in enumerate(m["comps"], start=1)
        ],
        "gas_turbine_array": [
            {
                "uid": i,
                "name": f"{name}_gas",
                "generator": name,
                "gas_node": f"N{gn}",
                "heat_rate": hr,
            }
            for i, (name, _b, gn, _p, _c, hr) in enumerate(
                [g for g in m["gens"] if g[2]], start=1
            )
        ],
    }
    planning = {
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
        },
        "simulation": {
            "block_array": [{"uid": b + 1, "duration": 1} for b in range(NB)],
            "stage_array": [{"uid": 1, "first_block": 0, "count_block": NB}],
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        },
        "system": system,
    }
    (HERE / "gas_hu_ryan_6b7n.json").write_text(json.dumps(planning, indent=1) + "\n")


if __name__ == "__main__":
    main()
