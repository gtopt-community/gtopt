#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Compare gtopt runs that differ only in LP scaling (equilibration /
scale_objective) — the comparison half of
``tools/check_equilibration_invariance.sh``.

Every ``--variant NAME=DIR`` is a gtopt output directory.  The FIRST
variant is the reference; every other one is compared against it:

* SDDP LB / UB per iteration (``solver_status.json`` ``history``);
* the objective (``solution.csv`` or ``solution.parquet``, Σ obj_value;
  SDDP: first phase only — the simulation pass's policy cost);
* SDDP first-iteration optimality cuts (``cuts/sddp_cuts.parquet``),
  matched by ``(type, phase, scene, iteration, extra)`` and normalised by
  the α coefficient ``w``: intercept ``a = rhs / w`` and slopes
  ``β = c / w`` keyed by ``(class, variable, uid)`` — physical units.

A value pair passes when ``|x − y| <= rtol · max(|x|, |y|, 1)``.  Prints
one line per check and a PASS / FAIL summary; exit 0 on PASS, 1 on FAIL,
2 on missing / unreadable output.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

CutKey = tuple[int, int, int, int, int]
CoefKey = tuple[str, str, int]


@dataclass
class Variant:
    """Quantities read back from one gtopt output directory."""

    name: str
    path: Path
    lower: list[float] = field(default_factory=list)
    upper: list[float] = field(default_factory=list)
    objective: float | None = None
    cuts: dict[CutKey, tuple[float, dict[CoefKey, float]]] = field(default_factory=dict)


def close(x: float, y: float, rtol: float) -> bool:
    """Relative closeness with a unit floor on the scale."""
    return abs(x - y) <= rtol * max(abs(x), abs(y), 1.0)


def rel_diff(x: float, y: float) -> float:
    """``|x − y| / max(|x|, |y|, 1)``."""
    return abs(x - y) / max(abs(x), abs(y), 1.0)


def read_history(path: Path) -> tuple[list[float], list[float]]:
    """LB / UB per iteration from ``solver_status.json``."""
    status = path / "solver_status.json"
    if not status.is_file():
        return [], []
    data = json.loads(status.read_text(encoding="utf-8"))
    hist = data.get("history", [])
    return (
        [float(h["lower_bound"]) for h in hist],
        [float(h["upper_bound"]) for h in hist],
    )


def read_objective(path: Path, first_phase_only: bool) -> float | None:
    """Σ obj_value over ``solution.csv`` (or ``solution.parquet``).

    SDDP: only the first phase's rows — every later phase's obj_value
    also carries its α (future cost), and alternative optima may split
    the same total differently across phases, so only the first-phase
    objective (the simulated policy's expected cost) is comparable.
    """
    rows: list[dict[str, object]] = []
    sol_csv = path / "solution.csv"
    sol_pq = path / "solution.parquet"
    if sol_csv.is_file():
        with sol_csv.open(encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    elif sol_pq.is_file():
        import pyarrow.parquet as pq  # pylint: disable=import-outside-toplevel

        rows = pq.read_table(sol_pq).to_pylist()
    rows = [r for r in rows if r.get("obj_value") not in (None, "")]
    if not rows:
        return None
    if first_phase_only and "phase" in rows[0]:
        first = min(int(str(r["phase"])) for r in rows)
        rows = [r for r in rows if int(str(r["phase"])) == first]
    return sum(float(str(r["obj_value"])) for r in rows)


def read_first_cuts(
    path: Path,
) -> dict[CutKey, tuple[float, dict[CoefKey, float]]]:
    """First-iteration optimality cuts, α-normalised, physical units."""
    cut_file = path / "cuts" / "sddp_cuts.parquet"
    if not cut_file.is_file():
        return {}
    import pyarrow.parquet as pq  # pylint: disable=import-outside-toplevel

    rows = pq.read_table(cut_file).to_pylist()
    rows = [r for r in rows if int(r["type"]) == 0]  # optimality cuts
    if not rows:
        return {}
    first = min(int(r["iteration"]) for r in rows)
    out: dict[CutKey, tuple[float, dict[CoefKey, float]]] = {}
    for r in rows:
        if int(r["iteration"]) != first:
            continue
        coeffs = r["coeffs"] or []
        alpha = [c["val"] for c in coeffs if c["var"] == "alpha" and c["val"]]
        if not alpha:
            continue
        w = float(alpha[0])
        key: CutKey = (
            int(r["type"]),
            int(r["phase"]),
            int(r["scene"]),
            int(r["iteration"]),
            int(r["extra"]),
        )
        beta = {
            (str(c["cls"]), str(c["var"]), int(c["uid"])): float(c["val"]) / w
            for c in coeffs
            if c["var"] != "alpha"
        }
        out[key] = (float(r["rhs"]) / w, beta)
    return out


def load_variant(spec: str, method: str) -> Variant:
    """Parse ``NAME=DIR`` and read the run's outputs."""
    # Split on the LAST '=': variant names may contain '=' ("eq=ruiz").
    name, sep, raw = spec.rpartition("=")
    if not sep or not name:
        raise ValueError(f"--variant expects NAME=DIR, got {spec!r}")
    path = Path(raw)
    if not path.is_dir():
        raise ValueError(f"variant {name!r}: directory {path} not found")
    var = Variant(name=name, path=path)
    var.objective = read_objective(path, first_phase_only=method == "sddp")
    if method == "sddp":
        var.lower, var.upper = read_history(path)
        var.cuts = read_first_cuts(path)
    return var


@dataclass
class Report:
    """Accumulated check results."""

    failures: int = 0
    checks: int = 0
    lines: list[str] = field(default_factory=list)

    def check(self, label: str, ok: bool, detail: str) -> None:
        """Record one check."""
        self.checks += 1
        if not ok:
            self.failures += 1
        self.lines.append(f"  [{'ok' if ok else 'FAIL'}] {label}: {detail}")


def compare_bounds(ref: Variant, var: Variant, rtol: float, rep: Report) -> None:
    """LB / UB per iteration."""
    n_ref, n_var = len(ref.lower), len(var.lower)
    rep.check(
        f"{var.name} iterations",
        n_ref == n_var and n_ref > 0,
        f"{n_var} vs {n_ref} (reference)",
    )
    for it in range(min(n_ref, n_var)):
        for label, a, b in (
            ("LB", ref.lower[it], var.lower[it]),
            ("UB", ref.upper[it], var.upper[it]),
        ):
            rep.check(
                f"{var.name} iter {it} {label}",
                close(a, b, rtol),
                f"{b:.10g} vs {a:.10g} (rel {rel_diff(a, b):.2e})",
            )


def compare_cuts(ref: Variant, var: Variant, rtol: float, rep: Report) -> None:
    """First-iteration cuts, a and β in physical units."""
    missing = sorted(set(ref.cuts) ^ set(var.cuts))
    rep.check(
        f"{var.name} first-iteration cut set",
        not missing and bool(ref.cuts),
        f"{len(var.cuts)} cuts vs {len(ref.cuts)}"
        + (f"; unmatched keys {missing[:4]}" if missing else ""),
    )
    worst_a = 0.0
    worst_b = 0.0
    worst_where = ""
    for key in sorted(set(ref.cuts) & set(var.cuts)):
        a_ref, b_ref = ref.cuts[key]
        a_var, b_var = var.cuts[key]
        da = rel_diff(a_ref, a_var)
        worst_a = max(worst_a, da)
        for ck in set(b_ref) | set(b_var):
            db = rel_diff(b_ref.get(ck, 0.0), b_var.get(ck, 0.0))
            if db > worst_b:
                worst_b, worst_where = db, f"cut {key} coef {ck}"
    rep.check(
        f"{var.name} cut intercepts a",
        worst_a <= rtol,
        f"max rel diff {worst_a:.2e}",
    )
    rep.check(
        f"{var.name} cut slopes β",
        worst_b <= rtol,
        f"max rel diff {worst_b:.2e}" + (f" at {worst_where}" if worst_where else ""),
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--variant",
        action="append",
        required=True,
        help="NAME=DIR of one gtopt output directory (first = reference)",
    )
    parser.add_argument("--method", choices=("sddp", "monolithic"), default="sddp")
    parser.add_argument("--rtol", type=float, default=1e-6)
    parser.add_argument("--cut-rtol", type=float, default=1e-6)
    args = parser.parse_args(argv)

    try:
        variants = [load_variant(v, args.method) for v in args.variant]
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if len(variants) < 2:
        print("error: need at least two variants to compare", file=sys.stderr)
        return 2

    ref = variants[0]
    if ref.objective is None and (args.method == "monolithic" or not ref.lower):
        print(f"error: no objective / bounds found in {ref.path}", file=sys.stderr)
        return 2

    rep = Report()
    for var in variants[1:]:
        if ref.objective is not None:
            obj = var.objective
            rep.check(
                f"{var.name} objective",
                obj is not None and close(ref.objective, obj, args.rtol),
                "missing"
                if obj is None
                else f"{obj:.10g} vs {ref.objective:.10g} "
                f"(rel {rel_diff(ref.objective, obj):.2e})",
            )
        if args.method == "sddp":
            compare_bounds(ref, var, args.rtol, rep)
            compare_cuts(ref, var, args.cut_rtol, rep)

    print(f"Equilibration invariance ({args.method}); reference = {ref.name}")
    for v in variants:
        last = (
            f"LB={v.lower[-1]:.10g} UB={v.upper[-1]:.10g}"
            if v.lower
            else f"obj={v.objective}"
        )
        print(f"  {v.name}: {last}  cuts(iter 1)={len(v.cuts)}")
    for line in rep.lines:
        print(line)
    verdict = "PASS" if rep.failures == 0 else "FAIL"
    print(
        f"{verdict}: {rep.checks - rep.failures}/{rep.checks} checks within "
        f"rtol={args.rtol:g} (cuts {args.cut_rtol:g})"
    )
    return 0 if rep.failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
