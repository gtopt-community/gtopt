#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Check a gtopt output directory against analytically derived values.

Golden-CSV comparison (``tools/gtopt_compare_csv.py``) only *warns* on
numeric differences unless a solver-specific golden exists, and a golden
written by gtopt itself proves nothing about the model.  This checker
instead asserts hand-derived values (objective, primal solutions, duals)
with an explicit tolerance and fails on any mismatch.

The expectations live in a JSON file next to the case::

    {
      "<case>": {
        "objective": 8815.5556,
        "checks": [
          {"file": "Reservoir/water_value_dual", "uid": 1, "stage": 1,
           "value": -1000.0},
          {"file": "Reservoir/drain_sol", "uid": 1, "stages": [1, 2],
           "sum": 800.0},
          {"file": "Reservoir/water_value_dual", "uid": 1, "stage": 1,
           "max": 0.0}
        ]
      }
    }

* ``objective`` is the ``obj_value`` of the first ``solution.csv`` row
  (the whole horizon in monolithic runs, phase 1 incl. the future-cost
  variable in SDDP runs).
* ``value`` / ``sum`` compare with ``|a - e| <= atol + rtol * |e|``.
* ``min`` / ``max`` assert bounds (sign checks for water values).
* gtopt writes sparse outputs: a (stage, block, uid) row that is
  missing means the value is 0, and the checker treats it so.

Output files are read in both layouts gtopt writes: one flat
``<file>.csv`` or per-(scene, phase) shards ``<file>_s<N>_p<M>.csv``, in
long (``uid``/``value`` columns) or wide (``uid:N`` columns) form.

Exit code: 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

RTOL = 1e-6
ATOL = 1e-6


def _read_rows(output_dir: Path, stem: str) -> list[dict[str, str]]:
    """Return every row of ``<stem>.csv`` or its scene/phase shards."""
    base = output_dir / stem
    paths = [base.with_suffix(".csv")] if base.with_suffix(".csv").exists() else []
    if not paths:
        paths = sorted(base.parent.glob(f"{base.name}_s*_p*.csv"))
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(newline="", encoding="utf-8") as fh:
            rows.extend(csv.DictReader(fh))
    return rows


def _values(rows: list[dict[str, str]], uid: int) -> dict[int, float]:
    """Map stage -> value for one element uid (summed over blocks)."""
    out: dict[int, float] = {}
    for row in rows:
        if "uid" in row:
            if int(float(row["uid"])) != uid:
                continue
            raw = row["value"]
        else:
            raw = row.get(f"uid:{uid}", "")
        if raw in ("", None):
            continue
        stage = int(float(row["stage"]))
        out[stage] = out.get(stage, 0.0) + float(raw)
    return out


def _close(actual: float, expected: float, rtol: float, atol: float) -> bool:
    return abs(actual - expected) <= atol + rtol * abs(expected)


def _objective(output_dir: Path) -> float | None:
    rows = _read_rows(output_dir, "solution")
    if not rows:
        return None
    return float(rows[0]["obj_value"])


def _check_one(
    output_dir: Path, check: dict[str, Any], rtol: float, atol: float
) -> str | None:
    """Return an error message, or None when the check passes."""
    rows = _read_rows(output_dir, check["file"])
    if not rows and not (output_dir / check["file"]).parent.is_dir():
        return f"{check['file']}: output not found"
    by_stage = _values(rows, int(check["uid"]))
    stages = check.get("stages", [check.get("stage")])
    actual = sum(by_stage.get(int(s), 0.0) for s in stages)
    label = f"{check['file']} uid={check['uid']} stages={stages}"
    expected = check.get("value", check.get("sum"))
    if expected is not None and not _close(actual, float(expected), rtol, atol):
        return f"{label}: got {actual:.10g}, expected {float(expected):.10g}"
    if "min" in check and actual < float(check["min"]) - atol:
        return f"{label}: got {actual:.10g} < min {check['min']}"
    if "max" in check and actual > float(check["max"]) + atol:
        return f"{label}: got {actual:.10g} > max {check['max']}"
    print(f"ok  {label}: {actual:.10g}")
    return None


def run_checks(output_dir: Path, spec: dict[str, Any]) -> list[str]:
    """Run every check of one case spec and return the failures."""
    rtol = float(spec.get("rtol", RTOL))
    atol = float(spec.get("atol", ATOL))
    errors: list[str] = []
    if "objective" in spec:
        obj = _objective(output_dir)
        exp = float(spec["objective"])
        if obj is None or not math.isfinite(obj):
            errors.append("solution.csv: missing or non-finite obj_value")
        elif not _close(obj, exp, rtol, atol):
            errors.append(f"objective: got {obj:.10g}, expected {exp:.10g}")
        else:
            print(f"ok  objective: {obj:.10g}")
    for check in spec.get("checks", []):
        err = _check_one(output_dir, check, rtol, atol)
        if err:
            errors.append(err)
    return errors


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--case", required=True, help="key in --expected")
    args = parser.parse_args(argv)

    spec_all = json.loads(args.expected.read_text(encoding="utf-8"))
    if args.case not in spec_all:
        print(f"case '{args.case}' not in {args.expected}", file=sys.stderr)
        return 1
    errors = run_checks(args.output_dir, spec_all[args.case])
    for err in errors:
        print(f"FAIL {err}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
