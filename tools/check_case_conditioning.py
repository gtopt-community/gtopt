#!/usr/bin/env python3
"""Run a gtopt case under one solver variant, or check conditioning claims.

Driver for the ``numerics`` integration tests registered by
``integration_test/cmake/add_conditioning_case.cmake``.  It has two
subcommands.

``run``
    Solves one variant (solver + ``--set`` overrides) of a case and leaves
    ``<output-dir>/out/solution.csv`` behind.  With ``--phases N`` it
    writes a copy of the case JSON in which only the first ``N`` phases
    (and the stages they cover) stay active, so a long multi-phase case
    can be pinned on a short horizon.  Each kept phase is the same LP it
    is in the full run: phase ``k`` only depends on phases ``< k``.

    Exits with 77 (CTest ``SKIP_RETURN_CODE``) when the case directory is
    missing (large cases are not committed) or when ``gtopt --solvers``
    does not list the requested solver.  It then writes
    ``<output-dir>/SKIPPED`` so the ``check`` step skips as well.

``check``
    Reads ``solution.csv`` of every named variant and asserts the
    conditioning claims passed on the command line, using the per-phase
    ``kappa`` column (``max`` over the phases solved):

    * ``--kappa-above V:X``      max kappa of V is > X
    * ``--kappa-below V:X``      max kappa of V is < X
    * ``--kappa-ratio-at-least A:B:R``   max kappa(A) >= R * max kappa(B)
    * ``--objective-reference V`` + ``--objective-reltol T``: the total
      objective (sum of per-phase ``obj_value``) of every variant is
      within relative tolerance T of variant V.

    Every phase must also report ``status == 0`` (optimal).

Exit code: 0 pass, 1 failure, 77 skipped.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
from pathlib import Path

SKIP = 77
SKIP_MARKER = "SKIPPED"


def _skip(output_dir: Path, reason: str) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / SKIP_MARKER).write_text(reason + "\n", encoding="utf-8")
    print(f"SKIP: {reason}")
    return SKIP


def _available_solvers(gtopt: str) -> set[str]:
    proc = subprocess.run(
        [gtopt, "--solvers"],
        capture_output=True,
        text=True,
        check=False,
    )
    solvers: set[str] = set()
    listing = False
    for line in proc.stdout.splitlines():
        if line.startswith("Available LP solvers"):
            listing = True
            continue
        if listing:
            name = line.strip()
            if not name or not line.startswith(" "):
                break
            solvers.add(name)
    return solvers


def _truncate_phases(planning: dict, num_phases: int) -> None:
    """Deactivate every phase past the first ``num_phases`` and its stages."""
    simulation = planning["simulation"]
    phases = simulation.get("phase_array") or []
    stages = simulation.get("stage_array") or []
    if num_phases >= len(phases):
        return
    for phase in phases[num_phases:]:
        phase["active"] = 0
        first = int(phase.get("first_stage", 0))
        count = int(phase.get("count_stage", len(stages) - first))
        for stage in stages[first : first + count]:
            stage["active"] = 0


def cmd_run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    case_dir = Path(args.case_dir).resolve()
    case_json = case_dir / args.case_json

    if output_dir.exists():
        for stale in (output_dir / SKIP_MARKER, output_dir / "out" / "solution.csv"):
            if stale.exists():
                stale.unlink()

    if not case_json.is_file():
        return _skip(output_dir, f"case not available: {case_json}")
    if args.solver not in _available_solvers(args.gtopt):
        return _skip(output_dir, f"solver plugin '{args.solver}' not available")

    output_dir.mkdir(parents=True, exist_ok=True)
    planning_file = case_json
    if args.phases > 0:
        planning = json.loads(case_json.read_text(encoding="utf-8"))
        _truncate_phases(planning, args.phases)
        planning_file = output_dir / f"{case_json.stem}_phases{args.phases}.json"
        planning_file.write_text(json.dumps(planning), encoding="utf-8")

    cmd = [
        args.gtopt,
        str(planning_file),
        "--solver",
        args.solver,
        "--set",
        f"input_directory={case_dir}",
        "--set",
        f"output_directory={output_dir / 'out'}",
    ]
    for kv in args.set or []:
        cmd += ["--set", kv]
    print("Running:", " ".join(cmd), flush=True)
    log_path = output_dir / "gtopt.log"
    with log_path.open("w", encoding="utf-8") as log:
        rc = subprocess.run(
            cmd,
            cwd=case_dir,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
            env=os.environ.copy(),
        ).returncode
    if rc != 0:
        print(log_path.read_text(encoding="utf-8")[-4000:])
        print(f"FAIL: gtopt exited with code {rc}")
        return 1
    if not (output_dir / "out" / "solution.csv").is_file():
        print(f"FAIL: no solution.csv under {output_dir / 'out'}")
        return 1
    return 0


def _read_solution(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _parse_pair(text: str, arity: int) -> list[str]:
    parts = text.split(":")
    if len(parts) != arity:
        raise argparse.ArgumentTypeError(f"expected {arity} ':'-separated fields")
    return parts


def cmd_check(args: argparse.Namespace) -> int:
    variants: dict[str, Path] = {}
    for spec in args.variant:
        name, _, directory = spec.partition("=")
        variants[name] = Path(directory)

    kappa: dict[str, float] = {}
    objective: dict[str, float] = {}
    failures: list[str] = []
    for name, directory in variants.items():
        if (directory / SKIP_MARKER).exists():
            reason = (directory / SKIP_MARKER).read_text(encoding="utf-8").strip()
            print(f"SKIP: variant '{name}' was skipped ({reason})")
            return SKIP
        solution = directory / "out" / "solution.csv"
        if not solution.is_file():
            print(f"FAIL: variant '{name}' has no {solution}")
            return 1
        rows = _read_solution(solution)
        if not rows:
            print(f"FAIL: variant '{name}' has an empty {solution}")
            return 1
        for row in rows:
            if row["status"] != "0":
                failures.append(
                    f"{name}: phase {row['phase']} status "
                    f"{row['status']} ({row.get('status_name', '?')})"
                )
        kappas = [float(r["kappa"]) for r in rows]
        kappa[name] = max(kappas)
        objective[name] = math.fsum(float(r["obj_value"]) for r in rows)
        per_phase = " ".join(f"{k:.3g}" for k in kappas)
        print(
            f"{name:24s} phases={len(rows)} max_kappa={kappa[name]:.4g} "
            f"objective={objective[name]:.10g} kappa/phase=[{per_phase}]"
        )

    for spec in args.kappa_above or []:
        name, threshold = _parse_pair(spec, 2)
        if not kappa[name] > float(threshold):
            failures.append(f"max kappa({name})={kappa[name]:.4g} not > {threshold}")
    for spec in args.kappa_below or []:
        name, threshold = _parse_pair(spec, 2)
        if not kappa[name] < float(threshold):
            failures.append(f"max kappa({name})={kappa[name]:.4g} not < {threshold}")
    for spec in args.kappa_ratio_at_least or []:
        num, den, ratio = _parse_pair(spec, 3)
        actual = kappa[num] / kappa[den]
        print(f"kappa({num}) / kappa({den}) = {actual:.4g}")
        if not actual >= float(ratio):
            failures.append(f"kappa({num})/kappa({den})={actual:.4g} not >= {ratio}")
    if args.objective_reference:
        ref = objective[args.objective_reference]
        for name, value in objective.items():
            rel = abs(value - ref) / max(abs(ref), 1.0)
            print(f"objective({name}) rel. diff vs reference = {rel:.3e}")
            if not rel <= args.objective_reltol:
                failures.append(
                    f"objective({name})={value:.10g} differs from "
                    f"{args.objective_reference}={ref:.10g} by {rel:.3e} "
                    f"(> {args.objective_reltol:g})"
                )

    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        return 1
    print("PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="solve one variant of a case")
    run.add_argument("--gtopt", required=True, help="gtopt binary")
    run.add_argument("--case-dir", required=True)
    run.add_argument("--case-json", required=True)
    run.add_argument("--solver", required=True)
    run.add_argument("--output-dir", required=True)
    run.add_argument(
        "--phases",
        type=int,
        default=0,
        help="keep only the first N phases active (0 = all)",
    )
    run.add_argument("--set", action="append", help="extra gtopt --set k=v")
    run.set_defaults(func=cmd_run)

    check = sub.add_parser("check", help="assert conditioning claims")
    check.add_argument(
        "--variant", action="append", required=True, help="NAME=OUTPUT_DIR"
    )
    check.add_argument("--kappa-above", action="append", metavar="V:X")
    check.add_argument("--kappa-below", action="append", metavar="V:X")
    check.add_argument("--kappa-ratio-at-least", action="append", metavar="A:B:R")
    check.add_argument("--objective-reference", metavar="V")
    check.add_argument("--objective-reltol", type=float, default=1e-5)
    check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
