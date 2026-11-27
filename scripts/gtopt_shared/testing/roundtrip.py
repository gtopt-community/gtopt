# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Helpers for exporter round-trip tests (``gtopt -> dialect -> gtopt``).

* :func:`run_module` runs a converter as ``python -m <module>`` in a
  subprocess (the converters install signal handlers and ``sys.exit``),
* :func:`gtopt_binary` finds the ``gtopt`` solver (``GTOPT_BIN`` or
  ``PATH``),
* :func:`solve_cost` solves a planning and returns its total cost: the
  final SDDP upper bound, or the monolithic objective.  By default the
  case's own method is used, with ``cascade`` solved as ``sddp``
  (plp2gtopt writes ``cascade``): a case split in phases must be solved
  by SDDP, since monolithic solves each phase on its own.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run_module(
    module: str, *args: str | Path, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m module args...``; returns the completed process."""
    env = dict(os.environ)
    scripts = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = scripts + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    return subprocess.run(
        [sys.executable, "-m", module, *map(str, args)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def gtopt_binary() -> str | None:
    """Path of the gtopt solver, or ``None``."""
    env = os.environ.get("GTOPT_BIN")
    if env and Path(env).is_file():
        return env
    return shutil.which("gtopt")


def solve_cost(
    gtopt: str,
    case_json: Path,
    out_dir: Path,
    method: str | None = None,
    sets: tuple[str, ...] = (),
) -> float:
    """Solve ``case_json`` with HiGHS and return its total cost."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if method is None:
        planning = json.loads(case_json.read_text(encoding="utf-8"))
        method = (planning.get("options") or {}).get("method") or "monolithic"
        if method == "cascade":
            method = "sddp"
    proc = subprocess.run(
        [
            gtopt,
            str(case_json),
            "--set",
            f"input_directory={case_json.parent}",
            "-d",
            str(out_dir),
            "--solver",
            "highs",
            "--set",
            f"method={method}",
            *[a for x in sets for a in ("--set", x)],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gtopt failed ({proc.returncode}): {proc.stderr[-2000:]}")
    status = out_dir / "solver_status.json"
    if status.exists():
        data = json.loads(status.read_text(encoding="utf-8"))
        if data.get("method") == "sddp" and data.get("upper_bound") is not None:
            return float(data["upper_bound"])
    with (out_dir / "solution.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return sum(float(r["obj_value"]) for r in rows)
