#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""LRC-SCUC benchmark: the T-SCUC / ERC-SCUC models of Tuo & Li in gtopt.

Source: M. Tuo, X. Li, "Security-Constrained Unit Commitment Considering
Locational Frequency Stability in Low-Inertia Power Grids", IEEE TPWRS
2023, DOI 10.1109/TPWRS.2022.3215915 (preprint arXiv:2110.11498); code
and data https://github.com/rpglab/LRC_SCUC (CC BY 4.0), fetched at a
pinned commit, never vendored.

The data file ``dataFile24BusAllinertia41sen_T.dat`` (modified IEEE
24-bus, 41 thermal units, 24 h, wind + solar) is converted to a gtopt
case that mirrors the repository's Pyomo models term by term:

  T-SCUC   cost·P + startup·v + resC·r; DC network (rateA); P + r ≤
           Pmax·u; P ≥ Pmin·u; r ≤ HRamp; |ΔP| ≤ HRamp (also on
           start-up / shut-down); N-1 reserve Σ r ≥ P_g + r_g ∀ g
           (ReserveZone plane, contingency_includes_reserve); every unit
           starts offline (a unit on in hour 1 pays its start-up);
           min up / down and no-load cost are NOT in the Pyomo model.
  ERC-SCUC T-SCUC + the system-equivalent RoCoF rows
           Σ_k H_k·Pmax_k·u_k − H_g·Pmax_g·u_g ≥ f0/(2·RoCoF)·P_g ∀ g
           (one UserConstraint per unit; no native gtopt element).

Wind (0.96 × bus count × Wind_pcnt) and solar (4 × bus count ×
Solar_pcnt) are fixed injections (pmin = pmax), as in the model.

Usage::

    python tools/lrc_scuc_benchmark.py --variant T --out case_t.json
    python tools/lrc_scuc_benchmark.py --variant ERC --gtopt build/standalone/gtopt \\
        --work-dir /tmp/lrc --check
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

PINNED_SHA = "b71f5ba9793992b5e88f52f7b8cfed81dceb5e42"
DATA_FILE = "dataFile24BusAllinertia41sen_T.dat"
DATA_URL = f"https://raw.githubusercontent.com/rpglab/LRC_SCUC/{PINNED_SHA}/{DATA_FILE}"
BASE_MVA = 100.0
SEN_WIND = 0.96  # wind coefficient (``sen``)
R0_SOLAR = 4.0  # solar coefficient (``r0``)
F0 = 60.0  # Hz
ROCOF = 0.5  # Hz/s
SHORTFALL_COST = 1.0e6  # reserve shortfall / unserved load: never optimal

# Paper, Table II (p9) and the repository models solved with HiGHS
# (linearised exactly: P·u = P, r·u = r since P = r = 0 when u = 0).
PAPER_TOTAL = {"T": 891_391.0, "ERC": 988_524.0}
CODE_TOTAL = {"T": 940_841.602, "ERC": 1_000_489.883}


def parse_dat(text: str) -> dict[str, dict[int, dict[str, float]]]:
    """Tables of a Pyomo ``.dat`` file: ``{set: {index: {column: value}}}``.

    Handles the ``param: SET: col1 col2 ... := rows ;`` blocks used by the
    LRC-SCUC data (comments start with ``#``).
    """
    body = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    tables: dict[str, dict[int, dict[str, float]]] = {}
    for match in re.finditer(r"param\s*:\s*(\w+)\s*:(.*?):=(.*?);", body, re.DOTALL):
        name, cols_text, rows_text = match.groups()
        cols = cols_text.split()
        values = rows_text.split()
        width = len(cols) + 1
        if len(values) % width:
            raise ValueError(f"table {name}: {len(values)} values, width {width}")
        table: dict[int, dict[str, float]] = {}
        for i in range(0, len(values), width):
            row = values[i : i + width]
            table[int(row[0])] = {c: float(v) for c, v in zip(cols, row[1:])}
        tables[name] = table
    for required in ("BUS", "GEND", "BRANCH", "PERIOD"):
        if required not in tables:
            raise ValueError(f"missing table {required}")
    return tables


def _series(values: list[float]) -> list[list[float]]:
    return [[round(v, 9) for v in values]]


def erc_expression(gens: dict[int, dict[str, float]], g: int) -> str:
    """ERC-SCUC RoCoF row of unit ``g`` as a gtopt user-constraint string."""
    terms = [
        f'{gen["inertia_constant"] * gen["genD_Pmax"]:g} * commitment("uc{k}").status'
        for k, gen in gens.items()
        if k != g
    ]
    k_mw = F0 / (2.0 * ROCOF)
    return " + ".join(terms) + f' - {k_mw:g} * generator("g{g}").generation >= 0'


def to_gtopt(tables: dict[str, dict[int, dict[str, float]]], variant: str) -> dict:
    """gtopt planning JSON of the T- or ERC-SCUC model."""
    if variant not in ("T", "ERC"):
        raise ValueError(f"unknown variant {variant!r}")
    buses, gens = tables["BUS"], tables["GEND"]
    branches, periods = tables["BRANCH"], tables["PERIOD"]
    hours = sorted(periods)
    load_pct = [periods[t]["load_pcnt"] / 100.0 for t in hours]
    wind_pct = [periods[t]["Wind_pcnt"] / 100.0 for t in hours]
    solar_pct = [periods[t]["Solar_pcnt"] / 100.0 for t in hours]

    generators: list[dict[str, Any]] = []
    commitments: list[dict[str, Any]] = []
    provisions: list[dict[str, Any]] = []
    for g, gen in gens.items():
        ramp = gen["HRamp"]
        generators.append(
            {
                "uid": g,
                "name": f"g{g}",
                "bus": int(gen["genD_bus"]),
                "gcost": gen["genC_Cost"],
                "pmin": 0,
                "pmax": gen["genD_Pmax"],
                "capacity": gen["genD_Pmax"],
            }
        )
        commitments.append(
            {
                "uid": g,
                "name": f"uc{g}",
                "generator": f"g{g}",
                "pmin": gen["genD_Pmin"],
                "startup_cost": gen["genC_Startup"],
                "initial_status": 0,
                "initial_hours": 100,
                "ramp_up": ramp,
                "ramp_down": ramp,
                "startup_ramp": ramp,
                "shutdown_ramp": ramp,
                "relax": False,
            }
        )
        provisions.append(
            {
                "uid": g,
                "name": f"r{g}",
                "generator": f"g{g}",
                "reserve_zones": [1],
                "urmax": ramp,
                "ur_provision_factor": 1,
                "urcost": gen["resC"],
            }
        )

    renewables: list[dict[str, Any]] = []
    for b, bus in buses.items():
        for kind, count, pct, coef in (
            ("wind", bus["bus_Wind"], wind_pct, SEN_WIND * BASE_MVA),
            ("solar", bus["bus_Solar"], solar_pct, R0_SOLAR * BASE_MVA),
        ):
            if count <= 0:
                continue
            mw = [coef * count * p for p in pct]
            renewables.append(
                {
                    "uid": 1000 + len(renewables) + 1,
                    "name": f"{kind}{b}",
                    "bus": b,
                    "gcost": 0,
                    "pmin": _series(mw),
                    "pmax": _series(mw),
                    "capacity": max(mw),
                }
            )

    system: dict[str, Any] = {
        "name": f"lrc_scuc_{variant.lower()}",
        "bus_array": [{"uid": b, "name": f"b{b}"} for b in buses],
        "demand_array": [
            {
                "uid": b,
                "name": f"d{b}",
                "bus": b,
                "lmax": _series([bus["bus_Pd"] * p for p in load_pct]),
            }
            for b, bus in buses.items()
            if bus["bus_Pd"] > 0
        ],
        "generator_array": generators + renewables,
        "commitment_array": commitments,
        "line_array": [
            {
                "uid": j,
                "name": f"l{j}",
                "bus_a": int(br["branch_fbus"]),
                "bus_b": int(br["branch_tbus"]),
                "reactance": 1.0 / abs(br["branch_b"]),
                "tmax_ab": br["branch_rateA"],
                "tmax_ba": br["branch_rateA"],
            }
            for j, br in branches.items()
        ],
        "reserve_zone_array": [
            {
                "uid": 1,
                "name": "n_1_spin",
                "urcost": SHORTFALL_COST,
                "urreq_planes": [{"alpha": 0.0, "contingency_coef": 1.0}],
                "contingency_generators": [f"g{g}" for g in gens],
                "contingency_includes_reserve": True,
            }
        ],
        "reserve_provision_array": provisions,
    }
    if variant == "ERC":
        system["user_constraint_array"] = [
            {"uid": g, "name": f"erc_rocof_g{g}", "expression": erc_expression(gens, g)}
            for g in gens
        ]
    return {
        "options": {
            "annual_discount_rate": 0.0,
            "output_format": "csv",
            "output_compression": "uncompressed",
            "model_options": {
                "use_single_bus": False,
                "use_kirchhoff": True,
                "demand_fail_cost": SHORTFALL_COST,
                "scale_objective": 1000,
                "continuous_phases": "none",
            },
        },
        "simulation": {
            "block_array": [{"uid": t, "duration": 1} for t in hours],
            "stage_array": [
                {
                    "uid": 1,
                    "first_block": 0,
                    "count_block": len(hours),
                    "chronological": True,
                }
            ],
            "scenario_array": [{"uid": 1}],
        },
        "system": system,
    }


def fetch(cache_dir: Path) -> Path:
    """The pinned data file, downloaded into ``cache_dir`` once."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    local = cache_dir / f"{PINNED_SHA[:12]}_{DATA_FILE}"
    if not (local.exists() and local.stat().st_size > 0):
        with urllib.request.urlopen(DATA_URL) as resp:
            local.write_bytes(resp.read())
    return local


def objective(out: Path) -> tuple[str, float]:
    """(status, obj_value) of a monolithic gtopt run."""
    with (out / "solution.csv").open(newline="") as fh:
        row = next(csv.DictReader(fh))
    return row["status_name"], float(row["obj_value"])


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: convert, and with ``--gtopt`` solve and check."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--variant", choices=("T", "ERC"), default="ERC")
    ap.add_argument("--data", type=Path, default=None, help="local .dat file")
    ap.add_argument(
        "--cache-dir", type=Path, default=Path.home() / ".cache/gtopt/lrc_scuc"
    )
    ap.add_argument("--out", type=Path, default=None, help="write the case JSON")
    ap.add_argument("--gtopt", type=Path, default=None)
    ap.add_argument("--work-dir", type=Path, default=None)
    ap.add_argument("--solver", default=None)
    ap.add_argument(
        "--rel-tol",
        type=float,
        default=2e-4,
        help="tolerance vs the HiGHS reproduction of the repository model",
    )
    args = ap.parse_args(argv)
    try:
        data = args.data if args.data is not None else fetch(args.cache_dir)
        case = to_gtopt(parse_dat(data.read_text(encoding="utf-8")), args.variant)
        out_json = args.out
        if out_json is None and args.work_dir is not None:
            out_json = args.work_dir / f"lrc_scuc_{args.variant.lower()}.json"
        if out_json is not None:
            out_json.parent.mkdir(parents=True, exist_ok=True)
            out_json.write_text(json.dumps(case, indent=1), encoding="utf-8")
        if args.gtopt is None or out_json is None:
            return 0
        out = out_json.parent / f"output_{args.variant.lower()}"
        cmd = [str(args.gtopt), str(out_json), "-d", str(out)]
        if args.solver:
            cmd += ["--solver", args.solver]
        subprocess.run(cmd, check=True, cwd=out_json.parent)
        status, obj = objective(out)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    paper = PAPER_TOTAL[args.variant]
    code = CODE_TOTAL[args.variant]
    print(
        f"{args.variant}-SCUC gtopt {obj:.3f} ({status}); paper {paper:.0f} "
        f"({(obj - paper) / paper:+.3%}); code {code}"
    )
    if status != "optimal":
        print(f"FAIL: status {status}")
        return 1
    if code is not None and abs(obj - code) > args.rel_tol * code:
        print(f"FAIL: |gtopt - code| > {args.rel_tol:g} relative")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
