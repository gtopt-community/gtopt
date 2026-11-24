#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Large integration test: RTS-GMLC day with reserves, series and inertia.

Converts one RTS-GMLC day with ``rts_gmlc_to_gtopt --timeseries --reserves
--inertia`` (sources downloaded into ``--cache-dir`` unless
``--no-download``), solves it with gtopt and checks, per hour:

* the solve is optimal;
* every reserve product (``reserves.csv``: Spin_Up_R1..R3, Flex_Up/Down,
  Reg_Up/Down) is covered by its eligible provisions:
  sum(provision) >= the day-ahead requirement series;
* the system inertia (sum of InertiaProvision) covers
  ``--inertia-requirement``.

Exit 0 = pass, 1 = check failed, 2 = run / input error.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

TOL = 1e-6
SUFFIX = "_s0_p0.csv"  # scene 0, phase 0 of a monolithic solve
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def read_long(path: Path) -> dict[tuple[int, int], float]:
    """``{(block, uid): value}`` of a long-layout gtopt output CSV."""
    with path.open(newline="") as fh:
        return {
            (int(r["block"]), int(r["uid"])): float(r["value"])
            for r in csv.DictReader(fh)
        }


def _sum_by_zone(
    values: dict[tuple[int, int], float], zone_of: dict[int, int]
) -> dict[tuple[int, int], float]:
    out: dict[tuple[int, int], float] = defaultdict(float)
    for (block, uid), value in values.items():
        if uid in zone_of:
            out[(block, zone_of[uid])] += value
    return out


def inertia_factors(system: dict) -> dict[int, float]:
    """InertiaProvision uid -> MWs per MW of ``provision_sol``.

    gtopt's default ``provision_factor`` is H·S / pmin_eff, pmin_eff = the
    generator pmin, or its capacity when pmin is 0 (the converter emits no
    pmin), so a unit at full output provides H·S.
    """
    gens = {g["uid"]: g for g in system["generator_array"]}
    out: dict[int, float] = {}
    for prov in system.get("inertia_provision_array", []):
        gen = gens[prov["generator"]]
        pmin_eff = float(gen.get("pmin") or gen["capacity"])
        out[prov["uid"]] = (
            float(prov["inertia_constant"]) * float(prov["rated_power"]) / pmin_eff
        )
    return out


def check(case: dict, out: Path) -> list[str]:
    """Failure messages of the solved case in ``out`` (empty = pass)."""
    errors: list[str] = []
    with (out / "solution.csv").open(newline="") as fh:
        statuses = [r["status_name"] for r in csv.DictReader(fh)]
    if statuses != ["optimal"]:
        errors.append(f"solve status {statuses}, expected ['optimal']")
        return errors

    system = case["system"]
    zone_name = {z["uid"]: z["name"] for z in system.get("reserve_zone_array", [])}
    for side, key in (("u", "ur"), ("d", "dr")):
        zones = [z for z in system.get("reserve_zone_array", []) if f"{key}req" in z]
        if not zones:
            continue
        zone_of = {
            p["uid"]: p["reserve_zones"][0]
            for p in system["reserve_provision_array"]
            if f"{key}max" in p
        }
        path = out / "ReserveProvision" / f"{side}provision_sol{SUFFIX}"
        provided = _sum_by_zone(read_long(path) if path.exists() else {}, zone_of)
        for zone in zones:
            for block, req in enumerate(zone[f"{key}req"][0], start=1):
                got = provided.get((block, zone["uid"]), 0.0)
                if got < req - TOL:
                    errors.append(
                        f"{zone_name[zone['uid']]} block {block}: "
                        f"provided {got:.3f} < requirement {req:.3f}"
                    )

    pf = inertia_factors(system)
    for zone in system.get("inertia_zone_array", []):
        req = float(zone["requirement"])
        path = out / "InertiaProvision" / f"provision_sol{SUFFIX}"
        per_block: dict[int, float] = defaultdict(float)
        for (block, uid), value in (read_long(path) if path.exists() else {}).items():
            per_block[block] += pf.get(uid, 0.0) * value
        for block in range(1, len(case["simulation"]["block_array"]) + 1):
            if per_block[block] < req - TOL:
                errors.append(
                    f"inertia block {block}: {per_block[block]:.1f} < "
                    f"requirement {req:.1f} MWs"
                )
    return errors


def convert(args: argparse.Namespace, case_path: Path) -> None:
    """Run the converter in a subprocess (scripts/ on PYTHONPATH)."""
    cmd = [
        sys.executable,
        "-m",
        "rts_gmlc_to_gtopt",
        "--day",
        str(args.day),
        "--cache-dir",
        str(args.cache_dir),
        "--timeseries",
        "--reserves",
        "--inertia",
        "--inertia-requirement",
        str(args.inertia_requirement),
        "-o",
        str(case_path),
    ]
    if args.no_download:
        cmd.append("--no-download")
    subprocess.run(cmd, check=True, cwd=_SCRIPTS)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--gtopt", required=True, type=Path)
    ap.add_argument("--work-dir", required=True, type=Path)
    ap.add_argument(
        "--cache-dir", type=Path, default=Path.home() / ".cache/gtopt/rts_gmlc"
    )
    ap.add_argument("--day", type=int, default=200)
    ap.add_argument("--inertia-requirement", type=float, default=20000.0)
    ap.add_argument("--solver", default=None)
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args(argv)

    args.work_dir.mkdir(parents=True, exist_ok=True)
    case_path = args.work_dir / f"rts_gmlc_d{args.day}.json"
    out = args.work_dir / "output"
    try:
        convert(args, case_path)
        cmd = [
            str(args.gtopt),
            str(case_path),
            "-d",
            str(out),
            "--set",
            "output_format=csv",
            "--set",
            "output_compression=uncompressed",
        ]
        if args.solver:
            cmd += ["--solver", args.solver]
        subprocess.run(cmd, check=True, cwd=args.work_dir)
        case = json.loads(case_path.read_text(encoding="utf-8"))
        errors = check(case, out)
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for err in errors:
        print(f"FAIL: {err}")
    if not errors:
        print(f"PASS: RTS-GMLC day {args.day} reserves and inertia covered")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
