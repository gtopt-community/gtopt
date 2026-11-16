#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Regenerate the ``solution.csv`` goldens of the e2e integration cases.

The ``e2e_<case>_compare_solution`` tests (``integration_test/CMakeLists.txt``
→ ``add_e2e_case``) compare ``cases/<case>/output[_<solver>]/solution.csv``
against a fresh run via ``tools/gtopt_compare_csv.py``.  When the objective
convention or the ``solution.csv`` schema changes, run this tool to refresh
the goldens instead of hand-editing them.

For every case the tool runs gtopt exactly like ``run_gtopt.cmake`` does and
writes a **projected** ``solution.csv`` holding only the stable golden schema
(``GOLDEN_COLUMNS``): identifiers, status, the PHYSICAL ``obj_value``, the
SDDP gap columns and ``scale_objective``.  Run-dependent telemetry
(``kappa``, ``solve_time_s``, ...) is dropped, so regenerating never
produces noise diffs and newly appended writer columns never need a golden
update.

Only ``solution.csv`` is touched; the per-element goldens are left alone.

Usage::

    # generic goldens (output/) with the default solver
    python tools/update_solution_goldens.py --gtopt build/standalone/gtopt

    # the per-solver goldens (output_highs/) for the cases that have them
    python tools/update_solution_goldens.py --gtopt ... --solver highs

    # a subset of cases, or just report what would change
    python tools/update_solution_goldens.py --gtopt ... c0 ieee_9b --dry-run
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = REPO_ROOT / "cases"
E2E_CMAKELISTS = REPO_ROOT / "integration_test" / "CMakeLists.txt"

# Stable golden schema, in output order.  Every other solution.csv column is
# run-dependent telemetry (see gtopt_compare_csv.SKIP_COLUMNS).
GOLDEN_COLUMNS = (
    "scene",
    "phase",
    "status",
    "status_name",
    "obj_value",
    "gap",
    "gap_change",
    "scale_objective",
)

_ADD_E2E_RE = re.compile(r"^\s*add_e2e_case\(\s*(\S+)\s+(\S+?)\s*[\s)]", re.MULTILINE)


def e2e_cases() -> dict[str, str]:
    """Return ``{case_name: system_json}`` registered via ``add_e2e_case``."""
    text = E2E_CMAKELISTS.read_text(encoding="utf-8")
    return {m.group(1): m.group(2) for m in _ADD_E2E_RE.finditer(text)}


def project_solution_csv(text: str) -> str:
    """Project a gtopt ``solution.csv`` onto ``GOLDEN_COLUMNS``."""
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in GOLDEN_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"solution.csv lacks golden column(s) {missing}")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(GOLDEN_COLUMNS)
    for row in reader:
        writer.writerow([row[c] for c in GOLDEN_COLUMNS])
    return out.getvalue()


def run_case(gtopt: Path, case: str, system_json: str, solver: str | None) -> str:
    """Run gtopt on *case* and return the raw ``solution.csv`` text."""
    case_dir = CASES_DIR / case
    env = dict(os.environ)
    if solver:
        env["GTOPT_SOLVER"] = solver
    tmp_root = os.environ.get("TMPDIR") or str(Path.home() / "tmp")
    with tempfile.TemporaryDirectory(prefix=f"golden-{case}-", dir=tmp_root) as out:
        cmd = [
            str(gtopt),
            str(case_dir / system_json),
            "--output-directory",
            out,
            "--write-out",
            "all",
            "--quiet",
        ]
        proc = subprocess.run(
            cmd,
            cwd=case_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"gtopt failed on {case} (rc={proc.returncode}):\n{proc.stderr}"
            )
        return (Path(out) / "solution.csv").read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("cases", nargs="*", help="Case names (default: all e2e).")
    parser.add_argument("--gtopt", type=Path, required=True, help="gtopt binary.")
    parser.add_argument(
        "--solver",
        help=(
            "Write cases/<case>/output_<solver>/solution.csv (only for cases "
            "that already have that directory) using GTOPT_SOLVER=<solver>. "
            "Without it, the generic output/ goldens are written."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report changes, write nothing."
    )
    args = parser.parse_args(argv)

    registered = e2e_cases()
    names = args.cases or sorted(registered)
    subdir = f"output_{args.solver}" if args.solver else "output"
    rc = 0
    for name in names:
        if name not in registered:
            print(f"SKIP {name}: not an add_e2e_case() case", file=sys.stderr)
            rc = 1
            continue
        golden = CASES_DIR / name / subdir / "solution.csv"
        if not golden.parent.is_dir():
            continue
        if not golden.exists():
            print(f"SKIP {name}: {golden} has no solution.csv golden")
            continue
        try:
            new = project_solution_csv(
                run_case(args.gtopt, name, registered[name], args.solver)
            )
        except (RuntimeError, ValueError) as exc:
            print(f"FAIL {name}: {exc}", file=sys.stderr)
            rc = 1
            continue
        old = golden.read_text(encoding="utf-8")
        state = "unchanged" if old == new else "updated"
        if state == "updated" and not args.dry_run:
            golden.write_text(new, encoding="utf-8")
        print(f"{state:9s} {golden.relative_to(REPO_ROOT)}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
