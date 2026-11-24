"""Independent reference solver for the planning JSON emitted by gasnet2gtopt.

Parses the ``UserConstraint`` expressions that gasnet2gtopt writes (the
subset of gtopt's PAMPL syntax it uses: ``coef * decision_variable('x')
.value``, ``generator('G').generation``, ``demand('D').load``, ``prev(...)``,
``* block.duration`` and ``, for(block = N)``) and rebuilds the LP with
scipy HiGHS: DV / generator / demand columns per block, one bus balance per
bus (no lines: the tiny cases are single-bus) and the UC rows.  Used as the
oracle for the tiny cases; the optional end-to-end test then checks that
gtopt reproduces the same objective and pressures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linprog

TERM = re.compile(
    r"([+-])?\s*(?:([0-9.]+)\s*\*\s*)?(prev\()?"
    r"(decision_variable|generator|demand)\('([^']+)'\)\.(\w+)\)?"
    r"(\s*\*\s*block\.duration)?"
)
FOR_BLOCK = re.compile(r",\s*for\(block\s*=\s*(\d+)\)\s*$")
CMP = re.compile(r"\s(<=|>=|=)\s")
ATTR = {"decision_variable": "value", "generator": "generation", "demand": "load"}


@dataclass
class Solution:
    status: int
    objective: float
    values: dict[str, list[float]] = field(default_factory=dict)
    gen: dict[str, list[float]] = field(default_factory=dict)
    load: dict[str, list[float]] = field(default_factory=dict)
    duals: dict[str, list[float]] = field(default_factory=dict)
    lmp: dict[str, list[float]] = field(default_factory=dict)

    def pressure(self, node: str) -> list[float]:
        return [float(np.sqrt(max(x, 0.0))) for x in self.values[f"pi_{node}"]]


def parse(expr: str):
    """-> (terms, sense, rhs, only_block); term = (coef, kind, name, prev)."""
    only = None
    m = FOR_BLOCK.search(expr)
    if m:
        only = int(m.group(1))
        expr = expr[: m.start()]
    parts = CMP.split(expr)
    if len(parts) != 3:
        raise ValueError(f"cannot parse {expr!r}")
    lhs, sense, rhs = parts
    terms = []
    pos = 0
    lhs = lhs.strip()
    while pos < len(lhs):
        m = TERM.match(lhs, pos)
        if not m:
            raise ValueError(f"bad term at {lhs[pos:]!r}")
        sign, coef, prev, kind, name, attr, dur = m.groups()
        if attr != ATTR[kind]:
            raise ValueError(f"unsupported attribute {kind}.{attr}")
        c = (-1.0 if sign == "-" else 1.0) * float(coef or 1.0)
        terms.append((c, kind, name, bool(prev), bool(dur)))
        pos = m.end()
        while pos < len(lhs) and lhs[pos] == " ":
            pos += 1
    return terms, sense, float(rhs), only


def block_value(v, b: int) -> float:
    """Scalar, [per-block] or [[per-block]] schedule -> value at block b."""
    while isinstance(v, list):
        v = v[0] if len(v) == 1 and isinstance(v[0], list) else v[b]
    return float(v)


def solve(planning: dict) -> Solution:
    sysd = planning["system"]
    opts = planning["options"]["model_options"]
    durs = [b["duration"] for b in planning["simulation"]["block_array"]]
    nb = len(durs)
    if sysd.get("line_array"):
        raise ValueError("reference solver is single-bus per bus (no lines)")

    cols: dict[tuple[str, str, int], int] = {}
    lo, hi, cost = [], [], []
    const = 0.0

    def col(key, lb, ub, c):
        cols[key] = len(lo)
        lo.append(lb)
        hi.append(ub)
        cost.append(c)

    dvs = {d["name"]: d for d in sysd["decision_variable_array"]}
    for d in dvs.values():
        for b in range(nb):
            col(
                ("decision_variable", d["name"], b),
                d["lower_bound"],
                d["upper_bound"],
                d.get("cost", 0.0) * durs[b],
            )
    for g in sysd["generator_array"]:
        for b in range(nb):
            col(
                ("generator", g["name"], b),
                0.0,
                block_value(g["pmax"], b),
                g.get("gcost", 0.0) * durs[b],
            )
    fail_default = opts.get("demand_fail_cost", 1000.0)
    for d in sysd["demand_array"]:
        fc = d.get("fcost", fail_default)
        for b in range(nb):
            lmax = block_value(d["lmax"], b)
            col(("demand", d["name"], b), 0.0, lmax, -fc * durs[b])
            const += fc * lmax * durs[b]

    a_eq, b_eq, eq_tag = [], [], []
    a_ub, b_ub, ub_tag = [], [], []
    nv = len(lo)
    for bus in sysd["bus_array"]:
        for b in range(nb):
            r = np.zeros(nv)
            for g in sysd["generator_array"]:
                if g["bus"] == bus["name"]:
                    r[cols[("generator", g["name"], b)]] = 1.0
            for d in sysd["demand_array"]:
                if d["bus"] == bus["name"]:
                    r[cols[("demand", d["name"], b)]] = -1.0
            a_eq.append(r)
            b_eq.append(0.0)
            eq_tag.append(("bus", bus["name"], b))

    for uc in sysd["user_constraint_array"]:
        terms, sense, rhs0, only = parse(uc["expression"])
        for b in range(nb):
            if only is not None and b + 1 != only:
                continue
            r = np.zeros(nv)
            rhs = block_value(uc["rhs"], b) if "rhs" in uc else rhs0
            for c, kind, name, prev, dur in terms:
                c = c * (durs[b] if dur else 1.0)
                if prev and b == 0:
                    rhs -= c * dvs[name]["initial_value"]
                else:
                    r[cols[(kind, name, b - 1 if prev else b)]] += c
            if sense == "=":
                a_eq.append(r)
                b_eq.append(rhs)
                eq_tag.append(("uc", uc["name"], b))
            elif sense == "<=":
                a_ub.append(r)
                b_ub.append(rhs)
                ub_tag.append(("uc", uc["name"], b))
            else:
                a_ub.append(-r)
                b_ub.append(-rhs)
                ub_tag.append(("uc-ge", uc["name"], b))

    res = linprog(
        np.array(cost),
        A_ub=np.array(a_ub) if a_ub else None,
        b_ub=np.array(b_ub) if b_ub else None,
        A_eq=np.array(a_eq),
        b_eq=np.array(b_eq),
        bounds=list(zip(lo, hi)),
        method="highs",
    )
    sol = Solution(status=res.status, objective=float("nan"))
    if res.status != 0:
        return sol
    sol.objective = float(res.fun) + const
    x = res.x
    for (kind, name, b), i in cols.items():
        tgt = {"decision_variable": sol.values, "generator": sol.gen}.get(
            kind, sol.load
        )
        tgt.setdefault(name, [0.0] * nb)[b] = float(x[i])
    for (kind, name, b), m in zip(eq_tag, res.eqlin.marginals):
        tgt = sol.lmp if kind == "bus" else sol.duals
        tgt.setdefault(name, [0.0] * nb)[b] = float(m)
    if ub_tag:
        for (kind, name, b), m in zip(ub_tag, res.ineqlin.marginals):
            sol.duals.setdefault(name, [0.0] * nb)[b] = float(
                -m if kind == "uc-ge" else m
            )
    return sol
