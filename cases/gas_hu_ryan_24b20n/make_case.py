#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Hu & Ryan IEEE 24-bus + Belgian 20-node coupled system (CC BY 4.0).

Source: sheet ``24bus_20gasnodes`` (+ its forecast-wind sheet) of
``../gas_hu_ryan_6b7n/Data_IISE_DH_SMR.xlsx`` (D. Hu, S. M. Ryan,
Mendeley Data, doi:10.17632/ynb88g67xy.1, CC BY 4.0).  Same LP
reduction as the 6-bus case (README.md); parallel pipes and compressors
are merged (C = C1 + C2, i.e. K = 1 / (C1 + C2)^2), which makes the
Belgian network a tree.

Writes three files:

* ``gas_hu_ryan_24b20n.json`` -- radial tangent form (16 tangents),
  pipes oriented by the exact optimum's flows;
* ``gas_hu_ryan_24b20n_slp.json`` -- the same system with linearized
  pipes and the gas SLP (no orientation needed);
* ``expected.json`` -- per hour, the exact Weymouth optimum (scipy
  tangent SLP + SLSQP polish), the scipy tangent LP of the radial form
  and its outer-approximation bound.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

HERE = Path(__file__).resolve().parent


def _load_ratha():
    """The Ratha case's model helpers (shared Hour / gtopt_planning)."""
    path = HERE.parent / "gas_ratha_24el_12ng" / "make_case.py"
    spec = importlib.util.spec_from_file_location("gas_ratha_make_case", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_RATHA = _load_ratha()
Hour = _RATHA.Hour
gtopt_planning = _RATHA.gtopt_planning

XLSX = HERE.parent / "gas_hu_ryan_6b7n" / "Data_IISE_DH_SMR.xlsx"
SHA256 = "e45b394338fdbc7fc5fda978dafba555a5597569fa8218103fb0d37bf63a9f3a"
SHEET = "24bus_20gasnodes"
NB = 24
SEGMENTS = 16
GAS_FAIL_COST = 50.0


def read_rows() -> tuple[list[list], list[float]]:
    import openpyxl  # pylint: disable=import-outside-toplevel

    digest = hashlib.sha256(XLSX.read_bytes()).hexdigest()
    if digest != SHA256:
        sys.exit(f"{XLSX}: sha256 {digest} != pinned {SHA256}")
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    rows = [list(r) for r in wb[SHEET].iter_rows(values_only=True)]
    wind = [
        float(r[1])
        for r in wb[f"{SHEET}(Forecast Wind)"].iter_rows(values_only=True)
        if isinstance(r[0], int) and r[1] is not None
    ][:NB]
    return rows, wind


def block(rows: list[list], title: str, ncol: int) -> list[list]:
    """Numeric rows after the row whose first cell starts with ``title``
    (columns kept in place, ``None`` for empty cells)."""
    i = next(
        k for k, r in enumerate(rows) if r[0] and str(r[0]).strip().startswith(title)
    )
    out = []
    for r in rows[i + 1 :]:
        if isinstance(r[0], (int, float)) and not isinstance(r[0], bool):
            out.append(r[:ncol])
        elif out and r[0] is None and all(v is None for v in r[:ncol]):
            break
    return out


def model() -> dict:
    rows, wind = read_rows()
    gnodes = block(rows, "Parameters of nodes in gas nodes", 5)
    nodes = {
        f"N{int(r[0])}": (math.sqrt(r[1]), math.sqrt(r[2]), r[4] or 0.0) for r in gnodes
    }
    cpipe: dict[tuple[str, str], float] = defaultdict(float)
    for r in block(rows, "Parameters of passive gas pipelines", 4):
        cpipe[(f"N{int(r[1])}", f"N{int(r[2])}")] += float(r[3])
    pipes = []
    for (a, b), c in cpipe.items():
        fm = c * math.sqrt(
            max(nodes[a][1], nodes[b][1]) ** 2 - min(nodes[a][0], nodes[b][0]) ** 2
        )
        pipes.append((f"P{a[1:]}_{b[1:]}", a, b, 1.0 / c**2, fm))
    comps = {}
    for r in block(rows, "Parameters of natural gas compressor", 4):
        comps[(f"N{int(r[1])}", f"N{int(r[2])}")] = math.sqrt(float(r[3]))
    sup = [
        (f"SUP{int(r[0])}", f"N{int(r[1])}", float(r[3]) / NB, float(r[4]))
        for r in block(rows, "Parameters of natural gas supplier", 5)
    ]
    for r in block(rows, "Storage facilities data", 7):
        qmax, ini = float(r[4]), float(r[6])
        sup.append((f"STOR{int(r[0])}", f"N{int(r[1])}", min(qmax, ini / NB), r[5]))
    shares = {
        int(r[0]): r[3] or 0.0
        for r in block(rows, "Parameters of electricity nodes", 4)
    }
    eload = [float(r[1]) for r in block(rows, "Electricity load data", 2)][:NB]
    gdem = [float(r[1]) for r in block(rows, "Gas demand", 2)][:NB]
    gens = []
    for r in block(rows, "Parameters of generators", 13):
        unit, bus, gnode, _pmin, pmax = r[:5]
        cprod, phi = r[11] or 0.0, r[12] or 0.0
        gens.append(
            (
                f"G{int(unit)}",
                int(bus),
                float(pmax),
                float(cprod),
                f"N{int(gnode)}" if phi > 0 else None,
                float(phi),
                None,
            )
        )
    wbuses = [int(r[1]) for r in block(rows, "Wind turbines data", 2)]
    for w, b in enumerate(wbuses, start=1):
        gens.append(
            (
                f"W{w}",
                b,
                400.0 / len(wbuses),
                0.0,
                None,
                0.0,
                [x / len(wbuses) for x in wind],
            )
        )
    lines = [
        (int(r[0]), int(r[1]), int(r[2]), float(r[3]), float(r[4]))
        for r in block(rows, "Parameters of power transmission branch", 5)
    ]
    return {
        "nodes": nodes,
        "pipes": pipes,
        "comps": [(f"C{a[1:]}_{b[1:]}", a, b, g) for (a, b), g in comps.items()],
        "sup": sup,
        "gens": gens,
        "loads": {b: [s * x for x in eload] for b, s in shares.items() if s > 0},
        "gd": {n: [sh * x for x in gdem] for n, (_, _, sh) in nodes.items() if sh > 0},
        "lines": lines,
        "nbus": 24,
        "gas_fail_cost": GAS_FAIL_COST,
    }


def tangent_lp(h: Hour, dirs: list[int], nseg: int):
    """scipy version of gtopt's radial tangent LP with orientations."""
    aub, bub = list(h.aub), list(h.bub)
    bounds = list(h.bounds)
    for (ia, ib, jf, k), s in zip(h.wey, dirs):
        fm = h.bounds[jf][1]
        bounds[jf] = (0.0, fm) if s > 0 else (-fm, 0.0)
        hi, lo = (ia, ib) if s > 0 else (ib, ia)
        for j in range(nseg):
            tk = (2 * j + 1) * fm / (2 * nseg)
            r = np.zeros(h.nv)
            r[hi], r[lo], r[jf] = -1, 1, 2 * k * tk * s
            aub.append(r)
            bub.append(k * tk * tk)
    return linprog(
        h.cost,
        A_ub=np.array(aub),
        b_ub=bub,
        A_eq=h.aeq,
        b_eq=h.beq,
        bounds=bounds,
        method="highs",
    )


def main() -> None:
    m = model()
    hours, flows = [], []
    for t in range(NB):
        h = Hour(m, t)
        slp = h.slp()
        pol = h.polish(slp.x)
        x = pol.x if pol.success and h.cost @ pol.x <= h.cost @ slp.x + 1e-6 else slp.x
        flows.append([x[jf] for *_, jf, _k in h.wey])
        hours.append({"h": h, "exact": float(h.cost @ x), "res": h.residual(x)})
    # One orientation for the day: the sign of the summed exact flows.
    dirs = [1 if sum(f[i] for f in flows) >= 0 else -1 for i in range(len(m["pipes"]))]
    out = []
    for t, hr in enumerate(hours):
        h = hr["h"]
        lp = tangent_lp(h, dirs, SEGMENTS)
        assert lp.status == 0, (t, lp.message)
        bound = sum(k * (h.bounds[jf][1] / (2 * SEGMENTS)) ** 2 for *_, jf, k in h.wey)
        out.append(
            {
                "exact": hr["exact"],
                "exact_residual": hr["res"],
                "tangent_lp": float(lp.fun),
                "pi_bound_sum": bound,
            }
        )
        print(t + 1, round(hr["exact"], 3), round(float(lp.fun), 3))
    for suffix, d in (("", dirs), ("_slp", None)):
        planning = gtopt_planning(
            m,
            name=f"gas_hu_ryan_24b20n{suffix}",
            dirs=d,
            segments=SEGMENTS,
            gas_fail_cost=GAS_FAIL_COST,
        )
        planning.pop("_node_order")
        (HERE / f"gas_hu_ryan_24b20n{suffix}.json").write_text(
            json.dumps(planning, indent=1) + "\n"
        )
    expected = {
        "pipe_orientation": dict(zip([p[0] for p in m["pipes"]], dirs)),
        "segments": SEGMENTS,
        "objective_exact": sum(o["exact"] for o in out),
        "objective_tangent_lp": sum(o["tangent_lp"] for o in out),
        "hours": out,
    }
    (HERE / "expected.json").write_text(json.dumps(expected, indent=1) + "\n")
    print(
        "exact",
        expected["objective_exact"],
        "tangent",
        expected["objective_tangent_lp"],
    )


if __name__ == "__main__":
    main()
