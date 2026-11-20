# SPDX-License-Identifier: BSD-3-Clause
"""CLI entry point for ``gtopt_check_cases``.

Check every case of a case collection (a directory with
``catalog.json``, e.g. a ``gtopt-cases`` checkout) against the catalog.

Default (LP only)
    Build each case's LP with ``gtopt --lp-only`` (case JSON + profile)
    and compare rows / cols / nnz with ``size.lp`` in the catalog.  A
    match confirms that this gtopt build reads the inputs as intended.

``--solve``
    Also solve each case; output goes to ``--out/<case id>/output``
    (default ``$GTOPT_CASES_OUT`` or ``~/gtopt-cases-out``), never into
    the collection.  With ``--results FILE`` (a JSON
    ``{"cases": [{"id": ..., "reference": {"objective": ...}}]}``) the
    objective is compared with relative tolerance ``--rtol``.

``--update``
    Write the measured LP sizes back into ``catalog.json`` (``size.lp``).

Examples::

    gtopt_check_cases --repo ~/gtopt-cases -j 2 --threads 8
    gtopt_check_cases --repo ~/gtopt-cases --only pcp_2025-10-19 --solve
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from .checks import (
    RunSpec,
    check_case,
    format_result,
    has_problem,
    load_catalog,
    save_catalog,
    select_cases,
    update_lp_sizes,
)


def _cpu_slot(i: int, args: argparse.Namespace) -> str | None:
    """CPU list for job slot ``i`` (``--cpus`` or ``--cpus-base``)."""
    if args.cpus:
        return args.cpus
    if args.cpus_base is None or not args.threads:
        return None
    lo = args.cpus_base + i * args.threads
    return f"{lo}-{lo + args.threads - 1}"


def _references(path: str | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {c["id"]: c.get("reference") or {} for c in data.get("cases", [])}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="gtopt_check_cases",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--repo", type=Path, default=Path("."), help="collection root (catalog.json)"
    )
    ap.add_argument("--gtopt", help="gtopt binary (default $GTOPT_BIN or PATH)")
    ap.add_argument("--only", nargs="*", help="substrings of case ids")
    ap.add_argument("--application", help="daily_scheduling, medium_long_term, ...")
    ap.add_argument("--solve", action="store_true", help="also solve")
    ap.add_argument("--update", action="store_true", help="write LP sizes to catalog")
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(os.environ.get("GTOPT_CASES_OUT", "~/gtopt-cases-out")),
        help="results root for --solve (outside the collection)",
    )
    ap.add_argument("--results", help="reference objectives JSON for --solve")
    ap.add_argument("-j", "--jobs", type=int, default=1)
    ap.add_argument("--threads", type=int, default=8, help="solver threads per job")
    ap.add_argument("--cpus", help="taskset CPU list for every job (e.g. 0-7)")
    ap.add_argument(
        "--cpus-base", type=int, help="pin job k to CPUs base+k*threads..+threads-1"
    )
    ap.add_argument("--set", action="append", default=[], help="extra gtopt --set")
    ap.add_argument("--rtol", type=float, default=0.005)
    ap.add_argument("--workdir", type=Path, help="scratch dir (default under TMPDIR)")
    ap.add_argument("--keep", action="store_true", help="keep the LP work dirs")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo.expanduser().resolve()
    if not (repo / "catalog.json").is_file():
        print(f"{repo}: no catalog.json", file=sys.stderr)
        return 2
    exe = args.gtopt or os.environ.get("GTOPT_BIN") or shutil.which("gtopt")
    if not exe:
        print("gtopt binary not found: pass --gtopt or set $GTOPT_BIN", file=sys.stderr)
        return 2
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="gtopt-check-cases-"))
    refs = _references(args.results)
    cat = load_catalog(repo)
    cases = select_cases(cat, args.only, args.application)

    def one(i: int, case: dict[str, Any]) -> dict[str, Any]:
        spec = RunSpec(exe, args.threads, _cpu_slot(i % args.jobs, args), args.set)
        solve_dir = (
            args.out.expanduser() / case["id"] / "output" if args.solve else None
        )
        return check_case(
            repo,
            case,
            spec,
            workdir,
            solve_dir=solve_dir,
            reference=refs.get(case["id"]),
            rtol=args.rtol,
            keep=args.keep,
        )

    results: list[dict[str, Any]] = []
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = [ex.submit(one, i, c) for i, c in enumerate(cases)]
        for fut in cf.as_completed(futs):
            r = fut.result()
            results.append(r)
            print(format_result(r), flush=True)

    if args.update:
        n = update_lp_sizes(cat, results)
        save_catalog(repo, cat)
        print(f"catalog.json: size.lp updated for {n} case(s)")
    bad = [r for r in results if has_problem(r)]
    print(f"\n{len(results)} cases, {len(bad)} with problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
