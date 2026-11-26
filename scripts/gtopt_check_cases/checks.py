# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Library behind ``gtopt_check_cases``: check a case collection.

A *case collection* (e.g. the public ``gtopt-cases`` repository) is a
directory with a ``catalog.json`` listing one entry per case::

    {"cases": [{"id": "chile/daily_scheduling/pcp_2025-10-19",
                "path": "chile/daily_scheduling/pcp_2025-10-19",
                "application": "daily_scheduling",
                "case_json": "case/pcp_2025-10-19.json",
                "profile": "profiles/daily_scheduling.json",
                "size": {"lp": {"lps": 1, "rows": ..., "cols": ...,
                                "nnz": ...}}}, ...]}

Each case is run as ``gtopt <case.json> [<profile.json>]`` from a
working directory outside the collection, with ``input_directory``
pinned to the case directory, so nothing is ever written into the
collection (it publishes inputs only).
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gtopt_check_output._reader import (
    dataset_layout,
    dataset_uid_cols,
    open_dataset,
    streaming_sol_weighted_sum_per_uid,
)
from gtopt_shared.solution_csv import parse_solution_csv, resolve_objective

LP_KEYS = ("rows", "cols", "nnz")

#: Demand failure (MW) below this is solver noise, not unserved load.
UNSERVED_TOL_MW = 1e-6


@dataclass
class RunSpec:
    """How to run gtopt for one case."""

    exe: str
    threads: int | None = None
    cpus: str | None = None
    sets: list[str] = field(default_factory=list)


def load_catalog(repo: Path) -> dict[str, Any]:
    """``<repo>/catalog.json``."""
    return json.loads((repo / "catalog.json").read_text(encoding="utf-8"))


def save_catalog(repo: Path, cat: dict[str, Any]) -> None:
    """Write ``catalog.json`` back (cases sorted by id, trailing newline)."""
    cat["cases"] = sorted(cat["cases"], key=lambda c: c["id"])
    (repo / "catalog.json").write_text(
        json.dumps(cat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def select_cases(
    cat: dict[str, Any],
    only: list[str] | None = None,
    application: str | None = None,
) -> list[dict[str, Any]]:
    """Cases with a case JSON, filtered by id substrings / application."""
    out = []
    for c in cat.get("cases", []):
        if c.get("placeholder") or not c.get("case_json"):
            continue
        if application and c.get("application") != application:
            continue
        if only and not any(o in c["id"] for o in only):
            continue
        out.append(c)
    return out


def outside(repo: Path, path: Path) -> Path:
    """``path`` resolved; refuse a location inside the collection."""
    p = Path(path).expanduser().resolve()
    if p.is_relative_to(repo.resolve()):
        raise ValueError(f"refusing to write results inside the collection: {p}")
    return p


def gtopt_command(
    spec: RunSpec,
    case_json: Path,
    profile: Path | None,
    out_dir: Path,
    *,
    lp_only: bool = False,
) -> list[str]:
    """The gtopt argv for one run (before ``taskset``/``ulimit`` wrapping)."""
    cmd = [spec.exe, str(case_json.resolve())]
    if profile is not None:
        cmd.append(str(profile.resolve()))
    cmd += [
        "--set",
        f"input_directory={case_json.resolve().parent}",
        "--set",
        f"output_directory={out_dir}",
    ]
    if spec.threads:
        cmd += ["--set", f"solver_options.threads={spec.threads}"]
    for s in spec.sets:
        cmd += ["--set", s]
    if lp_only:
        cmd += ["--lp-only", "-l", str(out_dir / "model"), "--lp-scales", "summary"]
    if spec.cpus:
        cmd = ["taskset", "-c", spec.cpus, *cmd]
    return cmd


def run_gtopt(
    spec: RunSpec,
    case_json: Path,
    profile: Path | None,
    out_dir: Path,
    *,
    lp_only: bool = False,
) -> int:
    """Run gtopt with ``out_dir`` as cwd and core dumps enabled.

    With ``lp_only`` the ``.lp`` files are deleted afterwards; only the
    ``model*.scales.json`` sidecars (rows/cols/nnz) are kept.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = gtopt_command(spec, case_json, profile, out_dir, lp_only=lp_only)
    shell = "ulimit -c unlimited; exec " + shlex.join(cmd)
    log = out_dir / ("lp_only.log" if lp_only else "solve.log")
    with log.open("w", encoding="utf-8") as fh:
        rc = subprocess.run(
            ["bash", "-c", shell],
            cwd=out_dir,
            stdout=fh,
            stderr=subprocess.STDOUT,
            check=False,
        ).returncode
    if lp_only:
        for lp in out_dir.glob("model*.lp"):
            lp.unlink()
    return rc


def lp_dims(out_dir: Path) -> dict[str, Any] | None:
    """Rows/cols/nnz summed over the ``model*.scales.json`` sidecars.

    ``--lp-only`` writes one sidecar per (scene, phase) LP; ``lps``
    counts them.
    """
    files = sorted(out_dir.glob("model*.scales.json"))
    if not files:
        return None
    docs = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    return {
        "lps": len(docs),
        "rows": sum(d["num_rows"] for d in docs),
        "cols": sum(d["num_cols"] for d in docs),
        "nnz": sum((d.get("numerics") or {}).get("nnz") or 0 for d in docs),
        "gtopt_revision": (docs[0].get("gtopt") or {}).get("git_revision"),
    }


def lp_matches(dims: dict[str, Any] | None, want: dict[str, Any] | None) -> bool | None:
    """True/False when both sizes are known, else ``None``."""
    if not dims or not want:
        return None
    return all(dims.get(k) == want.get(k) for k in LP_KEYS)


def read_solution(out_dir: Path) -> dict[str, Any] | None:
    """Status / objective / kappa of the first ``solution.csv`` row."""
    path = out_dir / "solution.csv"
    if not path.exists():
        return None
    row = parse_solution_csv(path.read_text(encoding="utf-8"))
    if not row:
        return None
    obj = resolve_objective(row)

    def num(key: str) -> float | None:
        try:
            return float(row[key])
        except (KeyError, ValueError):
            return None

    return {
        "status": row.get("status_name") or row.get("status"),
        "objective": obj.value if obj else None,
        "kappa": num("kappa"),
        "max_kappa": num("max_kappa"),
        "gap": num("gap"),
    }


def block_durations(case_json: Path) -> dict[int, float]:
    """``{block uid: duration h}`` from the case JSON's inline
    ``simulation.block_array`` (empty when absent or unreadable: the
    unserved energy is then summed with a 1 h weight per block)."""
    try:
        doc = json.loads(case_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    blocks = (doc.get("simulation") or {}).get("block_array") or []
    out: dict[int, float] = {}
    for blk in blocks:
        if isinstance(blk, dict) and "uid" in blk:
            out[int(blk["uid"])] = float(blk.get("duration", 1.0))
    return out


def read_unserved(out_dir: Path, durations: dict[int, float]) -> dict[str, Any] | None:
    """Unserved energy of a solved case from ``Demand/fail_sol``.

    Returns ``None`` when the run wrote no demand-failure output, else
    ``{"energy_mwh", "peak_mw", "blocks"}``: the duration-weighted sum of
    the failure (MWh), the largest single cell (MW) and the number of
    (scenario, stage, block) cells with any demand failure above
    :data:`UNSERVED_TOL_MW`.  Streams the dataset batch by batch (wide or
    long layout, parquet or CSV).
    """
    ds = open_dataset(out_dir, "Demand/fail_sol")
    if ds is None:
        return None
    energy = sum(streaming_sol_weighted_sum_per_uid(ds, durations).values())
    long_form = dataset_layout(ds) == "long"
    val_cols = ["value"] if long_form else dataset_uid_cols(ds)
    names = set(ds.schema.names)
    key_cols = [c for c in ("scenario", "stage", "block") if c in names]
    cells: set[tuple[Any, ...]] = set()
    peak = 0.0
    for batch in ds.scanner(columns=key_cols + val_cols).to_batches():
        df = batch.to_pandas()
        vals = df[val_cols].max(axis=1).fillna(0.0)
        peak = max(peak, float(vals.max()))
        hit = df.loc[vals > UNSERVED_TOL_MW, key_cols]
        cells.update(hit.itertuples(index=False, name=None))
    if not cells:
        energy = 0.0
    return {"energy_mwh": float(energy), "peak_mw": peak, "blocks": len(cells)}


def update_lp_sizes(cat: dict[str, Any], results: list[dict[str, Any]]) -> int:
    """Store measured LP sizes as ``size.lp``; returns the count updated."""
    by_id = {r["id"]: r for r in results if r.get("lp")}
    n = 0
    for c in cat["cases"]:
        r = by_id.get(c["id"])
        if r is None:
            continue
        lp = {k: r["lp"][k] for k in ("lps", *LP_KEYS)}
        lp["gtopt_revision"] = r["lp"].get("gtopt_revision")
        c.setdefault("size", {})["lp"] = lp
        n += 1
    return n


def check_case(  # pylint: disable=too-many-arguments
    repo: Path,
    case: dict[str, Any],
    spec: RunSpec,
    workdir: Path,
    *,
    solve_dir: Path | None = None,
    reference: dict[str, Any] | None = None,
    rtol: float = 0.005,
    keep: bool = False,
    fail_on_unserved: bool = False,
) -> dict[str, Any]:
    """LP-only build of one case (+ optional solve into ``solve_dir``).

    A solved case with unserved energy (demand failure > 0) is always
    reported; it counts as a problem (``unserved_error``) when
    ``fail_on_unserved`` is set or the catalog entry declares
    ``"serves_all_load": true``.
    """
    case_json = repo / case["path"] / case["case_json"]
    profile = repo / case["profile"] if case.get("profile") else None
    work = outside(repo, workdir / case["id"].replace("/", "__"))
    if work.exists():
        shutil.rmtree(work)
    res: dict[str, Any] = {"id": case["id"]}
    res["lp_rc"] = run_gtopt(spec, case_json, profile, work / "lp", lp_only=True)
    res["lp"] = lp_dims(work / "lp")
    match = lp_matches(res["lp"], (case.get("size") or {}).get("lp"))
    if match is not None:
        res["lp_match"] = match
    if solve_dir is not None and res["lp_rc"] == 0:
        out = outside(repo, solve_dir)
        res["solve_rc"] = run_gtopt(spec, case_json, profile, out)
        sol = read_solution(out)
        res["solution"] = sol
        ref_obj = (reference or {}).get("objective")
        if sol and sol.get("objective") is not None and ref_obj:
            rel = abs(sol["objective"] - ref_obj) / abs(ref_obj)
            res["obj_rel_diff"] = rel
            res["obj_match"] = rel <= rtol
        unserved = read_unserved(out, block_durations(case_json))
        res["unserved"] = unserved
        if unserved and unserved["blocks"] > 0:
            strict = fail_on_unserved or bool(case.get("serves_all_load"))
            res["unserved_error"] = strict
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    return res


def format_result(r: dict[str, Any]) -> str:
    """One report line."""
    lp = r.get("lp") or {}
    s = (
        f"{r['id']:48s} lp_rc={r['lp_rc']} rows={lp.get('rows')} "
        f"cols={lp.get('cols')} nnz={lp.get('nnz')} "
        f"match={r.get('lp_match', 'n/a')}"
    )
    if "solution" in r:
        sol = r["solution"] or {}
        # No kappa column under kappa_mode=off: print nothing about it.
        kappa = sol.get("kappa")
        kappa_txt = f"kappa={kappa} " if kappa is not None else ""
        s += (
            f" | rc={r.get('solve_rc')} status={sol.get('status')} "
            f"obj={sol.get('objective')} {kappa_txt}"
            f"obj_match={r.get('obj_match', 'n/a')}"
        )
        uns = r.get("unserved")
        if uns and uns["blocks"] > 0:
            level = "ERROR" if r.get("unserved_error") else "WARNING"
            s += (
                f" | {level}: unserved energy {uns['energy_mwh']:.3f} MWh "
                f"in {uns['blocks']} block(s), peak {uns['peak_mw']:.3f} MW"
            )
    return s


def has_unserved(r: dict[str, Any]) -> bool:
    """A solved case that shed load (demand failure > 0)."""
    uns = r.get("unserved")
    return bool(uns and uns["blocks"] > 0)


def has_problem(r: dict[str, Any]) -> bool:
    """Non-zero return code, a size / objective mismatch, or unserved
    energy in a case that must serve all load (``unserved_error``)."""
    return bool(
        r["lp_rc"]
        or r.get("solve_rc")
        or r.get("lp_match") is False
        or r.get("obj_match") is False
        or r.get("unserved_error")
    )
