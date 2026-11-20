# SPDX-License-Identifier: BSD-3-Clause
"""Rank the binding lines of a solved gtopt case, to choose line lifts.

Some lines of the CEN PCP cases are operated above their stated rating
because their capacity is expanded outside the PLEXOS model.  In gtopt
those lines need ``plexos2gtopt --lift-line-caps`` (hard cap -> soft
band) or the LP over-constrains the network.  :mod:`auto_lift_lines`
detects candidates *before* solving (DC OPF with all caps lifted); this
report works *after* a gtopt solve and lists, per line:

* ``rating``: the soft-band start (``tmax_normal_*``) when the line has
  one, otherwise the hard cap (``tmax_*``), per direction;
* ``max_util``: max |flow| / rating over all blocks and
  ``blocks_at_cap`` (blocks with |flow| >= ``--at`` x rating);
* ``overload_mwh``: energy in the soft overload band
  (``overloadp/n_sol``);
* ``dual_max`` / ``dual_mean``: |reduced cost| of the flow column
  ($/MWh), i.e. how much the objective would drop per extra MW;
* ``lifted``: whether the line is in the given lift list.

Lines are ranked by ``dual_max``.  Candidates for lifting are binding
lines with a large dual that are not lifted yet; lifted lines that
never bind may be dropped.  ``--suggest`` prints a ``--lift-line-caps``
value (current list + new candidates above ``--min-dual``).

Example::

    plexos2gtopt-line-lifts case/pcp_2026-04-07.json out/output \\
        --lifted line_lifts.json --lifted-key per_case.2026-04-07 \\
        --top 30 --suggest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

Rating = dict[int, float] | float | None


def _per_block(value: Any, blocks: list[int]) -> Rating:
    """Scalar, or ``{block_uid: value}`` for a [stage][block] schedule."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    flat: list[float] = []
    stack = [value]
    while stack:
        v = stack.pop(0)
        if isinstance(v, list):
            stack = list(v) + stack
        elif isinstance(v, (int, float)):
            flat.append(float(v))
    if len(flat) == len(blocks):
        return dict(zip(blocks, flat, strict=True))
    return max(flat) if flat else None


def _at(rating: Rating, block: int) -> float | None:
    if isinstance(rating, dict):
        return rating.get(block)
    return rating


def _read(out_dir: Path, name: str) -> pd.DataFrame:
    """``Line/<name>`` output (parquet or csv) as ``uid, block, value``."""
    for f in sorted((out_dir / "Line").glob(f"{name}*")):
        if f.name.endswith((".csv", ".csv.gz", ".csv.zst")):
            df = pd.read_csv(f)
        else:
            df = pd.read_parquet(f)
        return df[["uid", "block", "value"]]
    return pd.DataFrame(columns=["uid", "block", "value"])


def analyze(
    case: dict[str, Any], out_dir: Path, lifted: set[str], at: float = 0.999
) -> list[dict[str, Any]]:
    """Per-line binding statistics, sorted by ``dual_max`` (descending)."""
    blk = case["simulation"]["block_array"]
    blocks = [int(b["uid"]) for b in blk]
    dur = {int(b["uid"]): float(b.get("duration", 1.0)) for b in blk}
    flow, flown = _read(out_dir, "flow_sol"), _read(out_dir, "flown_sol")
    cost = _read(out_dir, "flow_cost")
    parts = [_read(out_dir, n) for n in ("overloadp_sol", "overloadn_sol")]
    parts = [p for p in parts if not p.empty]
    ovl = pd.concat(parts) if parts else _read(out_dir, "overloadp_sol")
    rows = []
    for line in case["system"]["line_array"]:
        uid = line["uid"]
        r_ab = _per_block(line.get("tmax_normal_ab", line.get("tmax_ab")), blocks)
        r_ba = _per_block(line.get("tmax_normal_ba", line.get("tmax_ba")), blocks)
        util = []
        f = flow[flow["uid"] == uid]
        for b, v in zip(f["block"], f["value"], strict=True):
            rat = _at(r_ab if v >= 0 else r_ba, int(b))
            if rat:
                util.append(abs(v) / rat)
        fn = flown[flown["uid"] == uid]
        for b, v in zip(fn["block"], fn["value"], strict=True):
            rat = _at(r_ba, int(b))
            if rat:
                util.append(abs(v) / rat)
        c = cost[cost["uid"] == uid]["value"].abs()
        ov = ovl[ovl["uid"] == uid]
        ov_mwh = float((ov["value"] * ov["block"].map(dur).fillna(1.0)).sum())
        rows.append(
            {
                "line": line.get("name", str(uid)),
                "enforce_level": line.get("enforce_level"),
                "rating_ab": max(r_ab.values()) if isinstance(r_ab, dict) else r_ab,
                "max_util": round(max(util), 4) if util else None,
                "blocks_at_cap": sum(1 for u in util if u >= at),
                "overload_mwh": round(ov_mwh, 3),
                "dual_max": round(float(c.max()), 4) if len(c) else 0.0,
                "dual_mean": round(float(c.mean()), 4) if len(c) else 0.0,
                "lifted": line.get("name") in lifted,
            }
        )
    rows.sort(key=lambda r: (r["dual_max"], r["max_util"] or 0), reverse=True)
    return rows


def load_lifted(spec: str | None, key: str | None = None) -> set[str]:
    """Line names from a comma list, a text file, or a JSON list/object.

    ``key`` is a dotted path into a JSON object
    (``per_case.2026-04-07``, ``common``).
    """
    if not spec:
        return set()
    path = Path(spec)
    if not path.is_file():
        return {n.strip() for n in spec.split(",") if n.strip()}
    text = path.read_text(encoding="utf-8")
    if text.lstrip()[:1] in "[{":
        data: Any = json.loads(text)
        for part in (key or "").split(".") if key else []:
            data = data[part]
        if not isinstance(data, list):
            raise ValueError(f"{spec}: --lifted-key must select a list of names")
        return {str(n) for n in data}
    return {ln.strip() for ln in text.splitlines() if ln.strip()}


def suggest(rows: list[dict[str, Any]], lifted: set[str], min_dual: float) -> list[str]:
    """New lift candidates: binding, not lifted, ``dual_max >= min_dual``."""
    return [
        r["line"]
        for r in rows
        if not r["lifted"] and r["dual_max"] >= min_dual and r["blocks_at_cap"]
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (``plexos2gtopt-line-lifts``)."""
    ap = argparse.ArgumentParser(
        prog="plexos2gtopt-line-lifts",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("case_json", type=Path, help="planning JSON of the solved case")
    ap.add_argument("output", type=Path, help="gtopt output directory")
    ap.add_argument(
        "--lifted", help="lift list in use: comma list, text file or JSON file"
    )
    ap.add_argument("--lifted-key", help="dotted key into a JSON --lifted file")
    ap.add_argument("--at", type=float, default=0.999, help="at-cap threshold")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--min-dual", type=float, default=1.0, help="for --suggest")
    ap.add_argument("--suggest", action="store_true")
    ap.add_argument("--json", type=Path, help="write the full table here")
    args = ap.parse_args(argv)

    lifted = load_lifted(args.lifted, args.lifted_key)
    case = json.loads(args.case_json.read_text(encoding="utf-8"))
    rows = analyze(case, args.output, lifted, args.at)
    hdr = f"{'line':40s} {'EL':>3s} {'util':>6s} {'@cap':>5s} {'ovl_MWh':>9s}"
    print(hdr + f" {'dual_max':>9s} {'dual_mean':>9s} lifted")
    for r in rows[: args.top]:
        util = f"{r['max_util']:.2f}" if r["max_util"] is not None else "-"
        print(
            f"{r['line'][:40]:40s} {r['enforce_level'] or '-'!s:>3s} {util:>6s}"
            f" {r['blocks_at_cap']:5d} {r['overload_mwh']:9.1f}"
            f" {r['dual_max']:9.2f} {r['dual_mean']:9.2f}"
            f" {'yes' if r['lifted'] else ''}"
        )
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    if args.suggest:
        new = suggest(rows, lifted, args.min_dual)
        print(f"\n{len(new)} new candidate(s): {', '.join(new) or '-'}")
        print("--lift-line-caps " + ",".join(sorted(lifted | set(new))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
