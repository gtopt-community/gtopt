# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Numerical-stability monitor for a gtopt run (``gtopt_check_output --numerics``).

Optionally runs gtopt on a case (``--gtopt`` + ``--case-json``), then reads
what the run left in ``--work-dir`` and checks it against a committed
baseline file:

``out/solution.csv``
    every cell optimal (``status == 0``); ``log10`` of the largest
    per-cell ``kappa`` (``kappa_log10``).
``gtopt.log``
    the SDDP per-iteration ``kappa=`` clause: growth from the first to the
    worst iteration (``kappa_growth``, decades); solver numerical
    warnings (``unscaled infeasibilities``, ``numerical trouble``, ...).
``lp/*.lp``
    the LP of every cell as built (``--lp-file``): ranges of the matrix,
    objective, RHS and column bounds in decades (``log10 max/min`` over
    nonzero finite entries), and the count of tiny / huge matrix entries.
``out/cuts/*.parquet``
    SDDP cuts: decades of the nonzero cut coefficients (``cut_decades``).

Each metric is compared with ``baseline + tolerance`` (tolerances in the
baseline file, in decades).  ``--update-baselines`` writes the measured
values instead.  On failure a table ``case | solver | metric | value |
baseline | limit`` is printed.  ``--top N`` also lists the matrix entries
that drive the range (largest and smallest, by row/column family).

Exit codes: 0 pass, 1 regression or failed run, 77 skipped (case or
solver not available).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SKIP = 77

#: Metrics, in report order, and the tolerance key each one uses.
METRICS: dict[str, str] = {
    "kappa_log10": "kappa",
    "kappa_growth": "kappa_growth",
    "matrix_decades": "range",
    "objective_decades": "range",
    "rhs_decades": "range",
    "bounds_decades": "range",
    "cut_decades": "range",
}

#: Default tolerances (decades); the baseline file may override them.
DEFAULT_TOLERANCES: dict[str, float] = {
    "kappa": 1.0,
    "kappa_growth": 1.0,
    "range": 0.5,
}

#: Entries at or above this magnitude count as infinite.
INFINITY_CUT = 1e19
TINY_COEFF = 1e-9
HUGE_COEFF = 1e9

#: Log lines that mean the solver struggled numerically.
WARNING_PATTERNS: tuple[str, ...] = (
    r"unscaled infeasibilit",
    r"numerical trouble",
    r"numerical difficult",
    r"ill[- ]posed",
    r"singular basis",
    r"\bunstable\b",
)

_ITER_KAPPA_RE = re.compile(r"iter (\d+)\s+obj\b.*?\bkappa=([0-9.eE+-]+)")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_TOKEN_RE = re.compile(
    r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?(?![A-Za-z_])|[<>]=?|=[<>]?|[+-]|[^\s<>=+-]+"
)
_SECTIONS: dict[str, str] = {
    "min": "obj",
    "minimize": "obj",
    "minimum": "obj",
    "max": "obj",
    "maximize": "obj",
    "maximum": "obj",
    "st": "rows",
    "s.t.": "rows",
    "subject to": "rows",
    "such that": "rows",
    "bounds": "bounds",
    "bound": "bounds",
    "general": "int",
    "generals": "int",
    "integer": "int",
    "integers": "int",
    "binary": "int",
    "binaries": "int",
    "semi-continuous": "int",
    "end": "end",
}


# ---------------------------------------------------------------------------
# Ranges and LP files
# ---------------------------------------------------------------------------


@dataclass
class Range:
    """Range of nonzero finite magnitudes."""

    lo: float = math.inf
    hi: float = 0.0
    count: int = 0

    def add(self, value: float) -> None:
        """Fold one value in (zeros and infinities are ignored)."""
        mag = abs(value)
        if mag == 0.0 or mag >= INFINITY_CUT or not math.isfinite(mag):
            return
        self.lo = min(self.lo, mag)
        self.hi = max(self.hi, mag)
        self.count += 1

    def merge(self, other: Range) -> None:
        """Fold another range in."""
        self.lo = min(self.lo, other.lo)
        self.hi = max(self.hi, other.hi)
        self.count += other.count

    def decades(self) -> float:
        """``log10(max/min)``; 0 when empty."""
        return math.log10(self.hi / self.lo) if self.count else 0.0


@dataclass
class Entry:
    """One matrix entry kept for the offender report."""

    mag: float
    row: str
    col: str


@dataclass
class LpNumerics:
    """Ranges of one or more LP files."""

    matrix: Range = field(default_factory=Range)
    objective: Range = field(default_factory=Range)
    rhs: Range = field(default_factory=Range)
    bounds: Range = field(default_factory=Range)
    tiny: int = 0
    huge: int = 0
    files: int = 0
    #: per (row family, col family): (min entry, max entry)
    families: dict[tuple[str, str], tuple[Entry, Entry]] = field(default_factory=dict)

    def merge(self, other: LpNumerics) -> None:
        """Fold another file's numerics in."""
        self.matrix.merge(other.matrix)
        self.objective.merge(other.objective)
        self.rhs.merge(other.rhs)
        self.bounds.merge(other.bounds)
        self.tiny += other.tiny
        self.huge += other.huge
        self.files += other.files
        for key, (lo, hi) in other.families.items():
            self._fold_family(key, lo)
            self._fold_family(key, hi)

    def _fold_family(self, key: tuple[str, str], entry: Entry) -> None:
        cur = self.families.get(key)
        if cur is None:
            self.families[key] = (entry, entry)
            return
        lo, hi = cur
        self.families[key] = (
            entry if entry.mag < lo.mag else lo,
            entry if entry.mag > hi.mag else hi,
        )

    def add_coeff(self, value: float, row: str, col: str) -> None:
        """Fold one matrix entry in."""
        mag = abs(value)
        if mag == 0.0:
            return
        self.matrix.add(value)
        if mag < TINY_COEFF:
            self.tiny += 1
        if mag > HUGE_COEFF:
            self.huge += 1
        self._fold_family((family(row), family(col)), Entry(mag, row, col))


def family(name: str) -> str:
    """Element family of an LP name: the tokens before the first numeric one.

    ``generator_output_12_3_4`` -> ``generator_output``.
    """
    head: list[str] = []
    for tok in name.split("_"):
        if tok and (tok[0].isdigit() or tok[0] in "+-"):
            break
        head.append(tok)
    return "_".join(head) or name


def _is_number(tok: str) -> bool:
    return bool(_NUMBER_RE.match(tok)) or tok.lower() in {"inf", "infinity"}


def _to_float(tok: str) -> float:
    return math.inf if tok.lower() in {"inf", "infinity"} else float(tok)


def _terms(tokens: list[str]) -> list[tuple[float, str]]:
    """``(coefficient, name)`` pairs of a linear expression's tokens."""
    out: list[tuple[float, str]] = []
    sign = 1.0
    coeff: float | None = None
    for tok in tokens:
        if tok == "+":
            continue
        if tok == "-":
            sign = -sign
            continue
        if _is_number(tok):
            coeff = _to_float(tok)
            continue
        if tok in {"[", "]"} or tok.startswith("^"):
            continue
        out.append((sign * (1.0 if coeff is None else coeff), tok))
        sign = 1.0
        coeff = None
    return out


def _split_statement(text: str) -> tuple[str, list[str]]:
    """``(name, tokens)`` of ``name: expression`` (name may be empty)."""
    name = ""
    head, sep, rest = text.partition(":")
    if sep and head.strip() and " " not in head.strip():
        name, text = head.strip(), rest
    return name, _TOKEN_RE.findall(text)


def _number_after(tokens: list[str], op: int) -> float | None:
    """Signed constant right after the operator at ``tokens[op]``."""
    j = op + 1
    sign = 1.0
    while j < len(tokens) and tokens[j] in "+-":
        sign = -sign if tokens[j] == "-" else sign
        j += 1
    if j < len(tokens) and _is_number(tokens[j]):
        return sign * _to_float(tokens[j])
    return None


def _lhs_tokens(tokens: list[str]) -> list[str]:
    """Tokens of the linear expression of a row (``lo <= expr <= hi`` too)."""
    ops = [i for i, t in enumerate(tokens) if t[0] in "<>="]
    if not ops:
        return tokens
    if len(ops) >= 2:  # ranged row: lo <= expr <= hi
        return tokens[ops[0] + 1 : ops[1]]
    return tokens[: ops[0]]


def analyze_lp_text(text: str) -> LpNumerics:
    """Numerics of one CPLEX-LP-format model (as written by ``--lp-file``)."""
    lp = LpNumerics(files=1)
    section = "pre"
    statement: list[str] = []

    def flush() -> None:
        if not statement:
            return
        body = " ".join(statement)
        statement.clear()
        name, tokens = _split_statement(body)
        if section == "obj":
            for coeff, _col in _terms(tokens):
                lp.objective.add(coeff)
            return
        lhs = _lhs_tokens(tokens)
        for coeff, col in _terms(lhs):
            lp.add_coeff(coeff, name, col)
        ops = [i for i, t in enumerate(tokens) if t[0] in "<>="]
        if len(ops) >= 2 and ops[0] > 0 and _is_number(tokens[ops[0] - 1]):
            sign = -1.0 if ops[0] > 1 and tokens[ops[0] - 2] == "-" else 1.0
            lp.rhs.add(sign * _to_float(tokens[ops[0] - 1]))
        if ops and (value := _number_after(tokens, ops[-1])) is not None:
            lp.rhs.add(value)

    for raw in text.splitlines():
        line = raw.split("\\", 1)[0].strip()
        if not line:
            continue
        key = line.lower()
        if key in _SECTIONS:
            flush()
            section = _SECTIONS[key]
            if section == "end":
                break
            continue
        if section == "obj":
            # A new statement starts at "name:"; continuation lines start
            # with a sign or a number.
            statement.append(line)
        elif section == "rows":
            starts_new = bool(re.match(r"^[^\s:+<>=-][^\s:]*\s*:", line))
            if starts_new:
                flush()
            statement.append(line)
            if re.search(
                r"[<>=]=?\s*[+-]?\s*(?:[\d.]+(?:[eE][+-]?\d+)?|inf)\s*$", line
            ):
                flush()
        elif section == "bounds":
            for tok in _TOKEN_RE.findall(line):
                if _is_number(tok):
                    lp.bounds.add(_to_float(tok))
    flush()
    return lp


def analyze_lp_files(paths: list[Path]) -> LpNumerics:
    """Merged numerics of several LP files (plain or ``.gz``)."""
    total = LpNumerics()
    for path in paths:
        if path.suffix == ".gz":
            import gzip  # noqa: PLC0415

            text = gzip.decompress(path.read_bytes()).decode("utf-8", "replace")
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
        total.merge(analyze_lp_text(text))
    return total


# ---------------------------------------------------------------------------
# Run outputs
# ---------------------------------------------------------------------------


@dataclass
class RunNumerics:
    """Everything measured on one run."""

    metrics: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    lp: LpNumerics | None = None


def read_solution(path: Path, run: RunNumerics) -> None:
    """Status of every cell and the largest kappa from ``solution.csv``."""
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        run.failures.append(f"{path}: no cells")
        return
    kappas: list[float] = []
    for row in rows:
        if row.get("status") != "0":
            run.failures.append(
                f"scene {row.get('scene')} phase {row.get('phase')}: status "
                f"{row.get('status')} ({row.get('status_name', '?')})"
            )
        try:
            k = float(row.get("kappa", "-1"))
        except ValueError:
            k = -1.0
        if k > 0.0:
            kappas.append(k)
    if kappas:
        run.metrics["kappa_log10"] = math.log10(max(kappas))
    else:
        run.notes.append("no kappa in solution.csv (kappa_mode off?)")


def read_logs(paths: list[Path], run: RunNumerics, patterns: tuple[str, ...]) -> None:
    """SDDP per-iteration kappa growth and numerical warnings."""
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in paths)
    per_iter: dict[int, float] = {}
    for m in _ITER_KAPPA_RE.finditer(text):
        k = float(m.group(2))
        if k > 0.0:
            per_iter[int(m.group(1))] = k
    if per_iter:
        first = per_iter[min(per_iter)]
        worst = max(per_iter.values())
        run.metrics["kappa_growth"] = math.log10(worst / first)
        run.notes.append(
            "kappa per iteration: "
            + " ".join(f"{i}:{per_iter[i]:.2e}" for i in sorted(per_iter))
        )
    for pat in patterns:
        rx = re.compile(pat, re.IGNORECASE)
        hits = [ln.strip() for ln in text.splitlines() if rx.search(ln)]
        if hits:
            run.failures.append(
                f"log: {len(hits)} line(s) match '{pat}', first: {hits[0][:160]}"
            )


def read_cuts(cut_dir: Path, run: RunNumerics) -> None:
    """Range of the nonzero cut coefficients over every saved cut file."""
    files = sorted(cut_dir.glob("*.parquet"))
    if not files:
        return
    import pyarrow.parquet as pq  # noqa: PLC0415

    rng = Range()
    for path in files:
        table = pq.read_table(path, columns=["coeffs"])
        for cut in table.column("coeffs").to_pylist():
            for term in cut or []:
                rng.add(float(term["val"]))
    if rng.count:
        run.metrics["cut_decades"] = rng.decades()
        run.notes.append(
            f"cut coefficients: {rng.count} in [{rng.lo:.3e}, {rng.hi:.3e}]"
        )


def measure(
    work_dir: Path, top: int, patterns: tuple[str, ...] = WARNING_PATTERNS
) -> RunNumerics:
    """Read every output of a run under ``work_dir``."""
    run = RunNumerics()
    solution = work_dir / "out" / "solution.csv"
    if solution.is_file():
        read_solution(solution, run)
    else:
        run.failures.append(f"missing {solution}")
    logs = [work_dir / "gtopt.log", *sorted((work_dir / "out" / "logs").glob("*.log"))]
    read_logs([p for p in logs if p.is_file()], run, patterns)
    lp_files = sorted((work_dir / "lp").glob("*.lp*"))
    if lp_files:
        lp = analyze_lp_files(lp_files)
        run.lp = lp
        run.metrics["matrix_decades"] = lp.matrix.decades()
        run.metrics["objective_decades"] = lp.objective.decades()
        run.metrics["rhs_decades"] = lp.rhs.decades()
        run.metrics["bounds_decades"] = lp.bounds.decades()
        if lp.matrix.count == 0:
            run.failures.append(f"{lp.files} LP file(s) without matrix entries")
        if lp.tiny or lp.huge:
            run.failures.append(
                f"matrix: {lp.tiny} entries below {TINY_COEFF:g}, "
                f"{lp.huge} above {HUGE_COEFF:g}"
            )
        run.notes.append(
            f"{lp.files} LP file(s), matrix |a| in "
            f"[{lp.matrix.lo:.3e}, {lp.matrix.hi:.3e}]"
        )
        if top > 0:
            run.notes.extend(offender_lines(lp, top))
    read_cuts(work_dir / "out" / "cuts", run)
    return run


def offender_lines(lp: LpNumerics, top: int) -> list[str]:
    """The (row family, column family) pairs holding the extreme entries."""
    fams = list(lp.families.items())
    largest = sorted(fams, key=lambda kv: kv[1][1].mag, reverse=True)[:top]
    smallest = sorted(fams, key=lambda kv: kv[1][0].mag)[:top]
    out = [f"largest |a| by (row family, column family), top {top}:"]
    out += [
        f"  {hi.mag:10.3e}  {r:<28} x {c:<28} e.g. {hi.row} / {hi.col}"
        for (r, c), (_lo, hi) in largest
    ]
    out.append(f"smallest |a| by (row family, column family), top {top}:")
    out += [
        f"  {lo.mag:10.3e}  {r:<28} x {c:<28} e.g. {lo.row} / {lo.col}"
        for (r, c), (lo, _hi) in smallest
    ]
    return out


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


def load_baselines(path: Path) -> dict[str, Any]:
    """Read the baseline file (an empty skeleton when it does not exist)."""
    if not path.is_file():
        return {"tolerances": dict(DEFAULT_TOLERANCES), "cases": {}}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("tolerances", dict(DEFAULT_TOLERANCES))
    data.setdefault("cases", {})
    return data


def update_baselines(
    path: Path, data: dict[str, Any], case: str, solver: str, run: RunNumerics
) -> None:
    """Store the measured metrics of ``case`` / ``solver`` and write the file."""
    entry = data["cases"].setdefault(case, {})
    entry[solver] = {k: round(v, 2) for k, v in sorted(run.metrics.items())}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass
class Row:
    """One line of the comparison table."""

    metric: str
    value: float
    baseline: float | None
    limit: float | None

    @property
    def ok(self) -> bool:
        """True when within the limit (or not baselined)."""
        return self.limit is None or self.value <= self.limit + 1e-9


def compare(
    data: dict[str, Any], case: str, solver: str, run: RunNumerics
) -> list[Row]:
    """Rows of ``metric | value | baseline | limit`` for one run."""
    tols = {**DEFAULT_TOLERANCES, **data.get("tolerances", {})}
    limits = data.get("limits", {})
    base = data.get("cases", {}).get(case, {}).get(solver, {})
    rows: list[Row] = []
    for metric, tol_key in METRICS.items():
        if metric not in run.metrics:
            continue
        value = run.metrics[metric]
        b = base.get(metric)
        limit = None if b is None else b + tols[tol_key]
        if metric in limits:
            cap = float(limits[metric])
            limit = cap if limit is None else min(limit, max(cap, b or 0.0))
        rows.append(Row(metric, value, b, limit))
    return rows


def format_table(case: str, solver: str, rows: list[Row]) -> str:
    """Compact report table."""

    def num(v: float | None) -> str:
        return "-" if v is None else f"{v:.2f}"

    lines = [
        f"{'case':<22} {'solver':<7} {'metric':<18} {'value':>8} "
        f"{'baseline':>8} {'limit':>8}"
    ]
    for r in rows:
        flag = "" if r.ok else "  << REGRESSION"
        lines.append(
            f"{case:<22} {solver:<7} {r.metric:<18} {num(r.value):>8} "
            f"{num(r.baseline):>8} {num(r.limit):>8}{flag}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Running gtopt
# ---------------------------------------------------------------------------


def available_solvers(gtopt: str) -> set[str]:
    """Solver plugins listed by ``gtopt --solvers``."""
    proc = subprocess.run(
        [gtopt, "--solvers"], capture_output=True, text=True, check=False
    )
    solvers: set[str] = set()
    listing = False
    for line in proc.stdout.splitlines():
        if line.startswith("Available LP solvers"):
            listing = True
            continue
        if listing:
            if not line.startswith(" ") or not line.strip():
                break
            solvers.add(line.split()[0])
    return solvers


def truncate_phases(planning: dict[str, Any], num_phases: int) -> None:
    """Deactivate every phase past the first ``num_phases`` (and its stages)."""
    simulation = planning["simulation"]
    phases = simulation.get("phase_array") or []
    stages = simulation.get("stage_array") or []
    if num_phases <= 0:
        return
    if not phases:
        for stage in stages[num_phases:]:
            stage["active"] = 0
        return
    for phase in phases[num_phases:]:
        phase["active"] = 0
        first = int(phase.get("first_stage", 0))
        count = int(phase.get("count_stage", len(stages) - first))
        for stage in stages[first : first + count]:
            stage["active"] = 0


def run_gtopt(args: argparse.Namespace, work_dir: Path) -> int:
    """Solve the case into ``work_dir``; 0 ok, 1 failure, 77 skip."""
    case_json = Path(args.case_json).resolve()
    if not case_json.is_file():
        print(f"SKIP: case not available: {case_json}")
        return SKIP
    if args.solver not in available_solvers(args.gtopt):
        print(f"SKIP: solver plugin '{args.solver}' not available")
        return SKIP
    case_dir = Path(args.case_dir).resolve() if args.case_dir else case_json.parent

    for sub in ("out", "lp", "lpout"):
        target = work_dir / sub
        if target.is_dir():
            for stale in target.rglob("*"):
                if stale.is_file():
                    stale.unlink()
    (work_dir / "lp").mkdir(parents=True, exist_ok=True)

    planning_file = case_json
    if args.phases > 0:
        planning = json.loads(case_json.read_text(encoding="utf-8"))
        truncate_phases(planning, args.phases)
        planning_file = work_dir / f"{case_json.stem}_phases{args.phases}.json"
        planning_file.write_text(json.dumps(planning), encoding="utf-8")

    input_dir = case_dir
    with case_json.open(encoding="utf-8") as fh:
        opts = json.load(fh).get("options", {})
    if opts.get("input_directory"):
        input_dir = case_dir / opts["input_directory"]

    base = [
        args.gtopt,
        str(planning_file),
        "--solver",
        args.solver,
        "--set",
        f"input_directory={input_dir}",
    ]
    extra: list[str] = []
    for kv in args.set or []:
        extra += ["--set", kv]
    env = os.environ.copy()
    env.setdefault("GTOPT_KAPPA_MODE", args.kappa_mode)
    # Pass 1: build every cell and dump it (--lp-only).  A separate pass
    # because --lp-file next to a solve writes SDDP cells before they are
    # built, and because names (needed by the dump) stay off in the solve.
    # Pass 2: the solve itself, exactly as configured.
    passes = [
        (
            "lp",
            [
                *base,
                "--set",
                f"output_directory={work_dir / 'lpout'}",
                "--lp-only",
                "--lp-file",
                str(work_dir / "lp" / "model.lp"),
                *extra,
            ],
        ),
        ("solve", [*base, "--set", f"output_directory={work_dir / 'out'}", *extra]),
    ]
    for tag, cmd in passes:
        print("Running:", " ".join(cmd), flush=True)
        log_path = work_dir / ("gtopt.log" if tag == "solve" else f"gtopt_{tag}.log")
        with log_path.open("w", encoding="utf-8") as log:
            rc = subprocess.run(
                cmd,
                cwd=work_dir,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                env=env,
            ).returncode
        if rc != 0:
            print(log_path.read_text(encoding="utf-8", errors="replace")[-4000:])
            print(f"FAIL: gtopt ({tag} pass) exited with code {rc}")
            return 1
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def make_parser() -> argparse.ArgumentParser:
    """Argument parser of the ``--numerics`` mode."""
    p = argparse.ArgumentParser(
        prog="gtopt_check_output --numerics",
        description=__doc__.split("\n\n", 1)[0] if __doc__ else None,
    )
    p.add_argument("--case", required=True, help="case name (baseline key)")
    p.add_argument("--solver", required=True, help="solver (baseline key)")
    p.add_argument("--work-dir", required=True, type=Path)
    p.add_argument("--baselines", required=True, type=Path, help="baseline JSON")
    p.add_argument("--gtopt", help="gtopt binary: run the case first")
    p.add_argument("--case-json", help="planning JSON to run (with --gtopt)")
    p.add_argument("--case-dir", help="case directory (default: the JSON's)")
    p.add_argument("--phases", type=int, default=0, help="keep only the first N phases")
    p.add_argument("--set", action="append", metavar="K=V", help="extra gtopt --set")
    p.add_argument(
        "--kappa-mode",
        default="exact",
        help="GTOPT_KAPPA_MODE when the environment does not set it",
    )
    p.add_argument(
        "--top", type=int, default=0, help="list the N largest/smallest families"
    )
    p.add_argument(
        "--update-baselines",
        action="store_true",
        help="write the measured values into the baseline file",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point of ``gtopt_check_output --numerics``; returns the exit code."""
    args = make_parser().parse_args(argv)
    work_dir: Path = args.work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    if args.gtopt:
        if not args.case_json:
            print("error: --gtopt needs --case-json", file=sys.stderr)
            return 2
        rc = run_gtopt(args, work_dir)
        if rc != 0:
            return rc

    run = measure(work_dir, args.top)
    for note in run.notes:
        print(note)

    data = load_baselines(args.baselines)
    if args.update_baselines:
        update_baselines(args.baselines, data, args.case, args.solver, run)
        print(f"baselines updated: {args.baselines} [{args.case}/{args.solver}]")

    rows = compare(data, args.case, args.solver, run)
    print(format_table(args.case, args.solver, rows))
    bad = [r for r in rows if not r.ok]
    for failure in run.failures:
        print(f"FAIL: {failure}")
    if bad or run.failures:
        print(f"FAIL: {len(bad)} metric(s) above limit, {len(run.failures)} error(s)")
        return 1
    print("PASS")
    return 0
