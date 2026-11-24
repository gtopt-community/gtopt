# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Cross-check the PowerSimulations reserve goldens against gtopt.

For a reserve golden written by ``tools/sienna_solve_golden.jl``
(``reserves_uc``, ``reserves_uc_re`` and their ``*_noordc`` variants) this
module converts the golden's ``system_data`` with ``sienna2gtopt
reserves``, solves it with gtopt (a MIP: CPLEX or HiGHS, never CLP) to a
1e-9 relative gap and compares, hour by hour:

* the objective, after removing the data constant
  ``sienna2gtopt._reserves.objective_offset`` (ORDC met-requirement
  valuation, interruptible-load valuation);
* thermal + wind dispatch per unit;
* commitment status per thermal unit;
* per RangeReserve service the requirement and the provided total, and
  per ORDC the served requirement (gtopt: Σ over the stacked step zones of
  ``urequirement_sol``; PSI: ``ServiceRequirementVariable``).

Per-unit reserve provisions are NOT compared: both models are degenerate
in them (every unit offers a RangeReserve at the same 1e-4 $/MW and the
ORDC provisions are free), so only service totals are meaningful.

Usage::

    python tools/sienna_reserve_crosscheck.py [slug ...] [--solver cplex]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
GOLDENS_DIR = _TOOLS_DIR / "sienna_goldens"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# pylint: disable=wrong-import-position
from sienna2gtopt._reserves import build_reserves, objective_offset

RESERVE_SLUGS = (
    "reserves_uc",
    "reserves_uc_re",
    "reserves_uc_noordc",
    "reserves_uc_re_noordc",
)

# Tolerances.  Both sides solve the same MIP to a 1e-9 relative gap
# (HiGHS in the golden, gtopt's solver here), so the objective must agree
# to solver precision.  Dispatch / commitment may legitimately differ
# between alternative optima (equal-cost units); the per-hour totals may
# not.
OBJ_REL_TOL = 1e-6
OBJ_ABS_TOL = 1e-3  # $
MW_TOL = 1e-4  # per-hour totals (load balance, requirement, ORDC served)
RESERVE_TOTAL_TOL = 1e-4  # MW, provided ≥ requirement − tol


@dataclass
class CrossCheck:
    """Comparison metrics of one slug."""

    slug: str
    sienna_obj: float
    gtopt_obj: float
    offset: float
    max_dispatch_diff: float = 0.0
    max_dispatch_unit: str = ""
    commitment_mismatches: int = 0
    max_requirement_diff: float = 0.0
    min_reserve_margin: float = 0.0
    max_ordc_served_diff: float = 0.0
    max_total_gen_diff: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def gtopt_obj_adjusted(self) -> float:
        """gtopt objective minus the data constant (PSI's objective form)."""
        return self.gtopt_obj - self.offset

    @property
    def obj_abs_diff(self) -> float:
        """|gtopt (adjusted) − Sienna| [$]."""
        return abs(self.gtopt_obj_adjusted - self.sienna_obj)

    @property
    def obj_rel_diff(self) -> float:
        """Relative objective difference."""
        return self.obj_abs_diff / max(1.0, abs(self.sienna_obj))


def load_golden(slug: str) -> dict[str, Any]:
    """The golden payload of ``slug``."""
    return json.loads((GOLDENS_DIR / f"{slug}.json").read_text())


def solve_case(
    gtopt_bin: str, payload: Mapping[str, Any], run_dir: Path, solver: str
) -> None:
    """Write ``payload`` (plus a tight MIP gap) and solve it in ``run_dir``."""
    run_dir.mkdir(parents=True, exist_ok=True)
    case = dict(payload)
    options = dict(case.get("options", {}))
    options["solver_options"] = {"mip_gap": 1e-9, "mip_gap_abs": 0.0}
    options["output_format"] = "csv"
    case["options"] = options
    (run_dir / "case.json").write_text(json.dumps(case, indent=1))
    proc = subprocess.run(
        [gtopt_bin, "case.json", "--solver", solver],
        cwd=str(run_dir),
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gtopt failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )


def _read_text(path: Path) -> str:
    """Text of a gtopt CSV output, decompressing ``*.csv.zst`` (the default)."""
    if path.suffix != ".zst":
        return path.read_text()
    try:
        from compression import zstd  # type: ignore[import-not-found,unused-ignore]  # 3.14+

        return zstd.decompress(path.read_bytes()).decode()
    except ImportError:
        import zstandard  # pylint: disable=import-outside-toplevel

        return (
            zstandard.ZstdDecompressor()
            .decompressobj()
            .decompress(path.read_bytes())
            .decode()
        )


def read_field(
    run_dir: Path, cls: str, fld: str, hours: int | None = None
) -> dict[str, list[float]]:
    """``{"uid:<n>": [value per block]}`` of a gtopt CSV output.

    Reads ``output/<cls>/<fld>_s*_p*.csv`` (or ``<fld>.csv``) in either
    the long layout (``scenario,stage,block,uid,value``) or the wide one
    (``scenario,stage,block,uid:1,uid:2,...``).
    """
    folder = run_dir / "output" / cls
    paths = [
        p
        for pat in (f"{fld}_s*_p*.csv", f"{fld}.csv")
        for ext in ("", ".zst")
        for p in sorted(folder.glob(pat + ext))
    ]
    if not paths:
        raise FileNotFoundError(folder / f"{fld}.csv")
    cells: dict[str, dict[int, float]] = {}
    for path in paths:
        lines = [ln for ln in _read_text(path).splitlines() if ln.strip()]
        header = [h.strip().strip('"') for h in lines[0].split(",")]
        blk = header.index("block")
        for line in lines[1:]:
            row = [c.strip().strip('"') for c in line.split(",")]
            block = int(float(row[blk]))
            if "value" in header:
                key = f"uid:{int(float(row[header.index('uid')]))}"
                val = row[header.index("value")]
                cells.setdefault(key, {})[block] = float(val) if val else 0.0
                continue
            for i, name in enumerate(header):
                if name.startswith("uid:"):
                    cells.setdefault(name, {})[block] = float(row[i]) if row[i] else 0.0
    # gtopt omits all-zero rows, so a cell may miss blocks: pad with 0.
    last = hours or max((max(v) for v in cells.values()), default=0)
    return {k: [v.get(b, 0.0) for b in range(1, last + 1)] for k, v in cells.items()}


def read_objective(run_dir: Path) -> float:
    """``obj_value`` of ``output/solution.csv``."""
    lines = (run_dir / "output" / "solution.csv").read_text().splitlines()
    header = [h.strip().strip('"') for h in lines[0].split(",")]
    return float(lines[1].split(",")[header.index("obj_value")])


def _by_name(
    values: Mapping[str, list[float]], elements: Sequence[Mapping[str, Any]]
) -> dict[str, list[float]]:
    """Re-key ``uid:<n>`` CSV columns by element name."""
    names = {f"uid:{e['uid']}": e["name"] for e in elements}
    return {names.get(k, k): v for k, v in values.items()}


def compare(
    slug: str, golden: Mapping[str, Any], payload: Mapping[str, Any], run_dir: Path
) -> CrossCheck:
    """Metrics of a solved gtopt run against its golden."""
    system = payload["system"]
    hours = int(golden["horizon_hours"])
    result = CrossCheck(
        slug=slug,
        sienna_obj=float(golden["objective"]),
        gtopt_obj=read_objective(run_dir),
        offset=objective_offset(golden),
    )

    gen = _by_name(
        read_field(run_dir, "Generator", "generation_sol", hours),
        system["generator_array"],
    )
    sienna_gen = golden["gen_dispatch_mw"]
    total_g = [0.0] * hours
    total_s = [0.0] * hours
    for name, series in gen.items():
        ref = sienna_gen[name]
        for t in range(hours):
            diff = abs(series[t] - float(ref[t]))
            total_g[t] += series[t]
            total_s[t] += float(ref[t])
            if diff > result.max_dispatch_diff:
                result.max_dispatch_diff = diff
                result.max_dispatch_unit = f"{name}@h{t + 1}"
    result.max_total_gen_diff = max(abs(a - b) for a, b in zip(total_g, total_s))

    uc_gen = {c["name"]: c["generator"] for c in system["commitment_array"]}
    status = _by_name(
        read_field(run_dir, "Commitment", "status_sol", hours),
        system["commitment_array"],
    )
    for uc_name, series in status.items():
        ref = golden["thermal_on"][uc_gen[uc_name]]
        result.commitment_mismatches += sum(
            1 for t in range(hours) if round(series[t]) != round(float(ref[t]))
        )

    zones = system.get("reserve_zone_array", [])
    if zones:
        zone_ids = {f"uid:{z['uid']}": z for z in zones}
        # Hard requirements only (no ORDC step, no shortage cost) write no
        # requirement column; the converted input is compared instead.
        try:
            ureq = read_field(run_dir, "ReserveZone", "urequirement_sol", hours)
        except FileNotFoundError:
            ureq = {}
        try:
            dreq = read_field(run_dir, "ReserveZone", "drequirement_sol", hours)
        except FileNotFoundError:
            dreq = {}
        _compare_reserves(result, golden, system, run_dir, zone_ids, ureq, dreq)
    return result


def _compare_reserves(
    result: CrossCheck,
    golden: Mapping[str, Any],
    system: Mapping[str, Any],
    run_dir: Path,
    zone_ids: Mapping[str, Mapping[str, Any]],
    ureq: Mapping[str, list[float]],
    dreq: Mapping[str, list[float]],
) -> None:
    hours = int(golden["horizon_hours"])
    services = golden["system_data"]["services"]
    provisions = system["reserve_provision_array"]
    up = _by_name(
        read_field(run_dir, "ReserveProvision", "uprovision_sol", hours), provisions
    )
    try:
        dn = _by_name(
            read_field(run_dir, "ReserveProvision", "dprovision_sol", hours), provisions
        )
    except FileNotFoundError:
        dn = {}
    ordc_served: dict[str, list[float]] = {}
    margin = float("inf")
    for key, zone in zone_ids.items():
        name = str(zone["name"])
        base, _, step = name.rpartition("_s")
        if base in services and step.isdigit():
            served = ureq[key]
            acc = ordc_served.setdefault(base, [0.0] * hours)
            for t in range(hours):
                acc[t] += served[t]
            continue
        down = "drreq" in zone
        # A hard requirement (no shortage cost) may have no requirement
        # column; then the converted input itself is compared.
        req_sol = (dreq if down else ureq).get(key) or zone[
            "drreq" if down else "urreq"
        ][0]
        ref = golden["reserve_requirement_mw"][name]
        provided = [0.0] * hours
        for prov in provisions:
            if zone["uid"] in prov["reserve_zones"]:
                series = (dn if down else up).get(prov["name"], [0.0] * hours)
                for t in range(hours):
                    provided[t] += series[t]
        for t in range(hours):
            result.max_requirement_diff = max(
                result.max_requirement_diff, abs(req_sol[t] - float(ref[t]))
            )
            margin = min(margin, provided[t] - float(ref[t]))
    result.min_reserve_margin = 0.0 if margin == float("inf") else margin
    for svc, served in ordc_served.items():
        ref = golden.get("ordc_served_mw", {}).get(svc)
        if ref is None:
            result.notes.append(f"golden has no ORDC served series for {svc}")
            continue
        for t in range(hours):
            result.max_ordc_served_diff = max(
                result.max_ordc_served_diff, abs(served[t] - float(ref[t]))
            )


def failures(res: CrossCheck) -> list[str]:
    """Tolerance violations of ``res`` (empty when it passes)."""
    out: list[str] = []
    if res.obj_abs_diff > max(OBJ_ABS_TOL, OBJ_REL_TOL * abs(res.sienna_obj)):
        out.append(
            f"objective: gtopt−offset {res.gtopt_obj_adjusted:.6f} vs "
            f"Sienna {res.sienna_obj:.6f}"
        )
    if res.max_total_gen_diff > MW_TOL:
        out.append(f"hourly total generation differs by {res.max_total_gen_diff}")
    if res.max_requirement_diff > MW_TOL:
        out.append(f"reserve requirement differs by {res.max_requirement_diff}")
    if res.min_reserve_margin < -RESERVE_TOTAL_TOL:
        out.append(f"reserve provided below requirement by {-res.min_reserve_margin}")
    if res.max_ordc_served_diff > MW_TOL:
        out.append(f"ORDC served requirement differs by {res.max_ordc_served_diff}")
    return out


def run_slug(
    gtopt_bin: str, slug: str, work_dir: Path, solver: str, solve: bool = True
) -> CrossCheck:
    """Convert, solve (unless ``solve`` is false) and compare one slug."""
    golden = load_golden(slug)
    payload = build_reserves(golden)
    run_dir = work_dir / slug
    if solve:
        solve_case(gtopt_bin, payload, run_dir, solver)
    return compare(slug, golden, payload, run_dir)


def main(argv: list[str] | None = None) -> int:
    """CLI: print the comparison table for the given slugs."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("slugs", nargs="*", default=list(RESERVE_SLUGS))
    ap.add_argument("--gtopt", default=os.environ.get("GTOPT_BIN", "gtopt"))
    ap.add_argument("--solver", default="cplex", choices=("cplex", "highs"))
    ap.add_argument("--work-dir", type=Path, default=Path("sienna_reserve_runs"))
    ap.add_argument(
        "--compare-only",
        action="store_true",
        help="do not solve; compare the outputs already in --work-dir/<slug>",
    )
    args = ap.parse_args(argv)
    bad = 0
    for slug in args.slugs:
        res = run_slug(
            args.gtopt,
            slug,
            args.work_dir,
            args.solver,
            solve=not args.compare_only,
        )
        fails = failures(res)
        bad += bool(fails)
        print(
            f"{slug}: sienna={res.sienna_obj:.6f} gtopt={res.gtopt_obj:.6f} "
            f"offset={res.offset:.6f} adj_diff={res.gtopt_obj_adjusted - res.sienna_obj:+.3e} "
            f"max_dispatch_diff={res.max_dispatch_diff:.4g} ({res.max_dispatch_unit}) "
            f"total_gen_diff={res.max_total_gen_diff:.3g} "
            f"uc_mismatch={res.commitment_mismatches} "
            f"req_diff={res.max_requirement_diff:.3g} "
            f"reserve_margin={res.min_reserve_margin:.3g} "
            f"ordc_served_diff={res.max_ordc_served_diff:.3g} "
            f"{'PASS' if not fails else 'FAIL ' + '; '.join(fails)}"
        )
        for note in res.notes:
            print(f"  note: {note}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
