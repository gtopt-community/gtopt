# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Transmission payments of the line_limit_tolls case, per limit mode.

Reads the gtopt output of each variant (``<mode>/output`` by default, or
``--root DIR`` holding ``<mode>/``) and prints, per block and per year:

* the LMPs, flow, loss, sending- and receiving-end power;
* the line limit dual μ (``thermalp_dual`` for ``sending`` / ``full``,
  the flow reduced cost ``flow_cost`` for ``lossless``; under ``flow``
  the limit is implied by the PWL segment bounds and no output carries
  it, so μ is the value of 1 MW more flow computed from the LMPs);
* the Chilean ingreso tarifario IT = π_r·P_r − π_s·P_s (withdrawal value
  minus injection value of the line), its FTR-style congestion rent
  μ·T and the rest (loss rent);
* the peaje / cargo único = VATT − IT, shared by the two loads in
  proportion to their energy withdrawals.

Assumptions (stated in the README): VATT = 7.5 M$/yr, split allocation
(a = ½), K = 4 uniform segments over [0, 100] MW.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

MODES = ("lossless", "flow", "sending", "full")
VATT = 7.5e6  # $/yr, assumed annual value of the line (AVI + COMA)
A_SHARE = 0.5  # split allocation: half of the loss at each end
CASE_DIR = Path(__file__).resolve().parent


def _read(path: Path) -> dict[tuple[int, int], float]:
    """``(block, uid) -> value`` of a long-form CSV output (missing → {})."""
    if not path.exists():
        path = path.with_name(path.stem + "_s0_p0.csv")
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {
            (int(r["block"]), int(r["uid"])): float(r["value"])
            for r in csv.DictReader(fh)
        }


def _segment_slope(flow: float, coef: float, tmax: float, k: int) -> float:
    """Marginal loss of the uniform secant PWL at *flow* (left derivative)."""
    width = tmax / k
    seg = min(max(int(-(-flow // width)), 1), k)  # ceil, 1-based
    return coef * width * (2 * seg - 1)


def analyse(mode: str, out: Path) -> list[dict[str, float]]:
    """Per-block payments of one variant."""
    case = json.loads((CASE_DIR / mode / "line_limit_tolls.json").read_text())
    line = case["system"]["line_array"][0]
    tmax = float(line["tmax_ab"])
    coef = float(line.get("resistance", 0.0)) / float(line["voltage"]) ** 2
    k = int(line.get("loss_segments", 1))
    dur = {
        int(b["uid"]): float(b["duration"]) for b in case["simulation"]["block_array"]
    }
    lmax = {int(d["uid"]): d["lmax"][0] for d in case["system"]["demand_array"]}
    pi = _read(out / "Bus" / "balance_dual.csv")
    flow = _read(out / "Line" / "flow_sol.csv")
    loss = _read(out / "Line" / "loss_sol.csv")
    thermal = _read(out / "Line" / "thermalp_dual.csv")
    rc_f = _read(out / "Line" / "flow_cost.csv")
    a = 0.0 if mode == "lossless" else A_SHARE
    rows = []
    for blk in sorted(dur):
        f = flow.get((blk, 1), 0.0)
        ell = loss.get((blk, 1), 0.0)
        s = _segment_slope(f, coef, tmax, k) if mode != "lossless" else 0.0
        pi1, pi2 = pi[(blk, 1)], pi[(blk, 2)]
        if mode in ("sending", "full"):
            mu = -thermal.get((blk, 1), 0.0)
        elif mode == "lossless":
            mu = -rc_f.get((blk, 1), 0.0)
        else:
            # `flow`: f ≤ T is implied by the segment bounds (Σ widths = T),
            # so no row / column of the output carries the rent; it is the
            # value of 1 MW more flow, V = π2·(1−(1−a)s) − π1·(1+a·s).
            binding = f >= tmax - 1e-6
            mu = (pi2 * (1 - (1 - a) * s) - pi1 * (1 + a * s)) if binding else 0.0
        p_s = f + a * ell
        p_r = f - (1.0 - a) * ell
        income = (pi2 * p_r - pi1 * p_s) * dur[blk]
        rent = mu * tmax * dur[blk]
        rows.append(
            {
                "block": blk,
                "hours": dur[blk],
                "pi1": pi1,
                "pi2": pi2,
                "f": f,
                "loss": ell,
                "p_send": p_s,
                "p_recv": p_r,
                "mu": mu,
                "it": income,
                "rent": rent,
                "loss_rent": income - rent,
                "w_a": lmax[1][blk - 1] * dur[blk],
                "w_b": lmax[2][blk - 1] * dur[blk],
            }
        )
    return rows


def summary(rows: list[dict[str, float]]) -> dict[str, float]:
    """Annual totals and the peaje split."""
    tot = {key: sum(r[key] for r in rows) for key in ("it", "rent", "loss_rent")}
    w_a = sum(r["w_a"] for r in rows)
    w_b = sum(r["w_b"] for r in rows)
    peaje = VATT - tot["it"]
    return {
        **tot,
        "peaje": peaje,
        "cargo": peaje / (w_a + w_b),
        "pay_a": peaje * w_a / (w_a + w_b),
        "pay_b": peaje * w_b / (w_a + w_b),
    }


def main(argv: list[str] | None = None) -> int:
    """Print the per-block and annual tables."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=CASE_DIR)
    args = ap.parse_args(argv)
    print(
        "mode     blk  pi1     pi2     f       loss   P_s     P_r     mu      "
        "IT[k$]   CR[k$]   LR[k$]"
    )
    annual = {}
    for mode in MODES:
        rows = analyse(mode, args.root / mode / "output")
        for r in rows:
            print(
                f"{mode:8s} {r['block']:3d} {r['pi1']:7.3f} {r['pi2']:7.3f} "
                f"{r['f']:7.3f} {r['loss']:6.3f} {r['p_send']:7.3f} "
                f"{r['p_recv']:7.3f} {r['mu']:7.3f} {r['it'] / 1e3:8.1f} "
                f"{r['rent'] / 1e3:8.1f} {r['loss_rent'] / 1e3:8.1f}"
            )
        annual[mode] = summary(rows)
    print(
        f"\nVATT {VATT / 1e6:.1f} M$/yr\nmode     IT[k$]   CR[k$]   LR[k$]   "
        "peaje[k$] cargo[$/MWh] A[k$]   B[k$]"
    )
    for mode, s in annual.items():
        print(
            f"{mode:8s} {s['it'] / 1e3:8.1f} {s['rent'] / 1e3:8.1f} "
            f"{s['loss_rent'] / 1e3:8.1f} {s['peaje'] / 1e3:9.1f} "
            f"{s['cargo']:11.3f} {s['pay_a'] / 1e3:7.1f} {s['pay_b'] / 1e3:7.1f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
