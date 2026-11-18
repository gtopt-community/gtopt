#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Plot SDDP lower/upper bounds and gap per iteration.

Reads the ``history`` array of a ``solver_status.json`` written by an SDDP
run (the same data ``gtopt_monitor`` plots live) and writes a two-panel
figure: bounds on top, relative gap (log scale) below.

Usage::

    python tools/figures/plot_sddp_convergence.py OUT_DIR/solver_status.json \\
        docs/images/sddp_convergence.svg
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _style
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator


def _millions(value: float, _pos: int) -> str:
    return f"{value / 1e6:g} M"


def plot(status_path: Path, output: Path, title: str) -> None:
    status = json.loads(status_path.read_text(encoding="utf-8"))
    history = status.get("history", [])
    if not history:
        raise SystemExit(f"{status_path}: no SDDP iteration history")

    iters = [h["iteration"] for h in history]
    lbs = [h["lower_bound"] for h in history]
    ubs = [h["upper_bound"] for h in history]
    gaps = [abs(h["gap"]) for h in history]

    # Stop where the bounds meet (or at the first converged row); later
    # rows only repeat it.
    last = next(
        (i for i, h in enumerate(history) if h.get("converged") or h["gap"] <= 0),
        len(history) - 1,
    )
    iters, lbs, ubs, gaps = (x[: last + 1] for x in (iters, lbs, ubs, gaps))
    pos = [(i, g) for i, g in zip(iters, gaps, strict=True) if g > 0]

    _style.apply()
    fig, (ax_b, ax_g) = plt.subplots(
        2,
        1,
        figsize=(10, 5.6),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 2], "hspace": 0.35},
    )

    ax_b.fill_between(iters, lbs, ubs, color=_style.BLUE, alpha=0.08, linewidth=0)
    ax_b.plot(iters, ubs, color=_style.ORANGE, marker="o", markersize=5)
    ax_b.plot(iters, lbs, color=_style.BLUE, marker="o", markersize=5)
    ax_b.annotate(
        "Upper bound (forward pass)",
        (iters[1], ubs[1]),
        xytext=(8, 8),
        textcoords="offset points",
        color=_style.TEXT,
        fontsize=9,
    )
    ax_b.annotate(
        "Lower bound (cuts)",
        (iters[1], lbs[1]),
        xytext=(8, -14),
        textcoords="offset points",
        color=_style.TEXT,
        fontsize=9,
    )
    ax_b.set_ylabel("Expected cost ($)")
    ax_b.yaxis.set_major_formatter(FuncFormatter(_millions))
    ax_b.set_ylim(bottom=0)
    ax_b.set_title(title)

    ax_g.semilogy(
        [i for i, _ in pos],
        [g for _, g in pos],
        color=_style.VIOLET,
        marker="o",
        markersize=5,
    )
    ax_g.set_ylabel("Relative gap")
    ax_g.set_xlabel("Iteration")
    ax_g.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax_g.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v * 100:g}%"))
    ax_g.set_xlim(ax_b.get_xlim())
    ax_g.annotate(
        f"gap = 0 at iteration {iters[-1]}"
        if gaps[-1] <= 0
        else f"converged at iteration {iters[-1]}",
        pos[-1],
        xytext=(10, 0),
        textcoords="offset points",
        ha="left",
        va="center",
        color=_style.TEXT_2,
        fontsize=9,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("status", type=Path, help="solver_status.json")
    parser.add_argument("output", type=Path, help="output image (.svg or .png)")
    parser.add_argument(
        "--title",
        default="SDDP convergence: two-reservoir hydro cascade, 24 phases, 8 scenarios",
    )
    args = parser.parse_args()
    plot(args.status, args.output, args.title)


if __name__ == "__main__":
    main()
