#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Slow regression: PCP 2026-11-07 day-1, exogenous native reserves.

Runs gtopt on the converted day-1 case (unless ``--check-only``) and checks,
over the 24 hours of day 1:

* the mean marginal cost (``Bus/balance_dual``) at Polpaico220 is >= 0 —
  the exogenous load term must not reproduce PLEXOS' negative CMg
  (PLEXOS -0.363, endogenous gtopt -0.341, exogenous gtopt +0.061 $/MWh);
* the mean CPF@10s requirement (``ReserveZone/urequirement_sol`` of zone
  ``CPF10s``) is within 5 MW of PLEXOS' 194.9125 MW.

Exit 0 = pass, 1 = check failed, 2 = run / input error.  How to build the
case: see ``integration_test/README_pcp_reserve_day1.md``.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

BUS = "Polpaico220"
ZONE = "CPF10s"
PLEXOS_CPF10S_MEAN = 194.9125
CPF10S_TOL = 5.0
HOURS = 24


def _read_table(out: Path, cls: str, field: str) -> list[dict[str, float]]:
    """Rows of ``out/cls/field.{csv,parquet}`` as dicts (block, uid, value)."""
    csv_path = out / cls / f"{field}.csv"
    if csv_path.exists():
        with csv_path.open(newline="") as fh:
            raw = list(csv.DictReader(fh))
    else:
        pq = out / cls / f"{field}.parquet"
        if not pq.exists():
            raise FileNotFoundError(f"{cls}/{field} not found under {out}")
        import pandas as pd  # pylint: disable=import-outside-toplevel

        raw = pd.read_parquet(pq).to_dict("records")
    rows: list[dict[str, float]] = []
    for r in raw:
        keys = {str(k).strip('"'): v for k, v in r.items()}
        if "uid" in keys and "value" in keys:
            rows.append(
                {
                    "block": float(keys["block"]),
                    "uid": float(keys["uid"]),
                    "value": float(keys["value"]),
                }
            )
            continue
        for k, v in keys.items():  # wide layout: uid:<n> columns
            if k.startswith("uid:") and v not in ("", None):
                rows.append(
                    {
                        "block": float(keys["block"]),
                        "uid": float(k[4:]),
                        "value": float(v),
                    }
                )
    return rows


def _mean(rows: list[dict[str, float]], uid: int) -> float:
    vals = [r["value"] for r in rows if int(r["uid"]) == uid and r["block"] <= HOURS]
    if len(vals) != HOURS:
        raise ValueError(
            f"expected {HOURS} day-1 values for uid {uid}, got {len(vals)}"
        )
    return sum(vals) / len(vals)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case-dir", type=Path, required=True)
    ap.add_argument("--json", default="pcp_2026-11-07.json")
    ap.add_argument("--work-dir", type=Path, required=True)
    ap.add_argument("--gtopt", type=Path)
    ap.add_argument("--solver", default="")
    ap.add_argument(
        "--check-only",
        action="store_true",
        help="skip the solve; read <work-dir>/output",
    )
    args = ap.parse_args(argv)

    case_json = args.case_dir / args.json
    out = args.work_dir / "output"
    if not args.check_only:
        if args.gtopt is None:
            print("--gtopt is required unless --check-only", file=sys.stderr)
            return 2
        args.work_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(args.gtopt),
            str(case_json),
            "--set",
            f"input_directory={args.case_dir}",
            "--set",
            "output_format=csv",
            "--set",
            "output_compression=uncompressed",
            "-d",
            str(out),
        ]
        if args.solver:
            cmd += ["--solver", args.solver]
        rc = subprocess.run(cmd, cwd=args.work_dir, check=False).returncode
        if rc != 0:
            print(f"gtopt exited with {rc}", file=sys.stderr)
            return 2

    plan = json.loads(case_json.read_text())
    buses = {b["name"]: int(b["uid"]) for b in plan["system"]["bus_array"]}
    zones = {z["name"]: int(z["uid"]) for z in plan["system"]["reserve_zone_array"]}
    try:
        cmg = _mean(_read_table(out, "Bus", "balance_dual"), buses[BUS])
        cpf = _mean(_read_table(out, "ReserveZone", "urequirement_sol"), zones[ZONE])
    except (KeyError, FileNotFoundError, ValueError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2

    print(f"{BUS} mean CMg day 1: {cmg:.4f} $/MWh (must be >= 0)")
    print(
        f"{ZONE} mean requirement day 1: {cpf:.4f} MW "
        f"(PLEXOS {PLEXOS_CPF10S_MEAN}, tol {CPF10S_TOL})"
    )
    ok = cmg >= -1e-9 and abs(cpf - PLEXOS_CPF10S_MEAN) <= CPF10S_TOL
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
