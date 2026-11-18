#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Plot hourly dispatch and nodal prices from a gtopt CSV output directory.

Top panel: generation stacked by unit, unserved energy, and total demand.
Bottom panel: the locational marginal price (bus balance dual) at
selected buses, on a log scale so that scarcity prices and normal
prices share one readable axis.

Usage::

    gtopt cases/ieee_9b/ieee_9b.json --solver highs \\
        --set output_directory=/tmp/ieee9b --set output_format=csv
    python tools/figures/plot_dispatch.py cases/ieee_9b/ieee_9b.json \\
        /tmp/ieee9b docs/images/dispatch_ieee9b.svg
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import _style
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

Series = dict[int, list[float]]


def _read_field(out_dir: Path, cls: str, field: str, n_blocks: int) -> Series:
    """Read ``<cls>/<field>*.csv`` (long format) into ``{uid: [per block]}``."""
    series: Series = defaultdict(lambda: [0.0] * n_blocks)
    for path in sorted((out_dir / cls).glob(f"{field}*.csv")):
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                block = int(row["block"]) - 1
                if 0 <= block < n_blocks:
                    series[int(row["uid"])][block] = float(row["value"])
    return dict(series)


def _names(system: dict, array: str) -> dict[int, str]:
    return {e["uid"]: e["name"] for e in system.get(array, [])}


def plot(case: Path, out_dir: Path, output: Path, buses: list[str], title: str) -> None:
    planning = json.loads(case.read_text(encoding="utf-8"))
    system = planning["system"]
    n_blocks = len(planning["simulation"]["block_array"])
    hours = list(range(n_blocks + 1))

    gen = _read_field(out_dir, "Generator", "generation_sol", n_blocks)
    load = _read_field(out_dir, "Demand", "load_sol", n_blocks)
    fail = _read_field(out_dir, "Demand", "fail_sol", n_blocks)
    lmp = _read_field(out_dir, "Bus", "balance_dual", n_blocks)

    gen_names = _names(system, "generator_array")
    profiled = {p["generator"] for p in system.get("generator_profile_array", [])}
    bus_uid = {v: k for k, v in _names(system, "bus_array").items()}

    def step(values: list[float]) -> list[float]:
        return [*values, values[-1]]

    _style.apply()
    fig, (ax_d, ax_p) = plt.subplots(
        2,
        1,
        figsize=(10, 6.2),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1.6], "hspace": 0.3},
    )

    for ax in (ax_d, ax_p):
        ax.set_axisbelow(True)

    layers: list[tuple[str, list[float], str]] = []
    for i, uid in enumerate(sorted(gen)):
        name = gen_names.get(uid, f"g{uid}")
        label = f"{name} (solar)" if name in profiled else name
        color = _style.YELLOW if name in profiled else _style.SERIES[i % 3]
        layers.append((label, gen[uid], color))
    unserved = [sum(v[b] for v in fail.values()) for b in range(n_blocks)]
    if any(u > 1e-6 for u in unserved):
        layers.append(("unserved energy", unserved, _style.RED))

    base = [0.0] * n_blocks
    for label, values, color in layers:
        top = [b + v for b, v in zip(base, values, strict=True)]
        ax_d.fill_between(
            hours,
            step(base),
            step(top),
            step="post",
            color=color,
            alpha=0.9,
            linewidth=0,
            label=label,
        )
        ax_d.step(hours, step(top), where="post", color=_style.SURFACE, linewidth=1.2)
        base = top

    demand = [sum(v[b] for v in load.values()) + unserved[b] for b in range(n_blocks)]
    ax_d.step(
        hours,
        step(demand),
        where="post",
        color=_style.TEXT,
        linewidth=1.6,
        linestyle="--",
        label="demand",
    )
    ax_d.set_ylabel("Power (MW)")
    ax_d.set_ylim(bottom=0)
    ax_d.set_title(title)
    ax_d.legend(loc="upper left", ncols=len(layers) + 1, bbox_to_anchor=(0, 1.0))
    ax_d.margins(x=0)

    for i, bus in enumerate(buses):
        uid = bus_uid.get(bus)
        if uid is None or uid not in lmp:
            continue
        ax_p.step(
            hours,
            step(lmp[uid]),
            where="post",
            color=(_style.BLUE, _style.VIOLET, _style.AQUA)[i % 3],
            label=bus,
        )
    ax_p.set_yscale("log")
    ax_p.set_yticks([10, 20, 50, 100, 200, 500, 1000])
    ax_p.set_ylim(10, 2000)
    ax_p.minorticks_off()
    ax_p.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))
    ax_p.set_ylabel("LMP ($/MWh)")
    ax_p.set_xlabel("Hour")
    ax_p.set_xticks(range(0, n_blocks + 1, 3))
    ax_p.legend(loc="upper left", ncols=len(buses))
    ax_p.margins(x=0)

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case", type=Path, help="case JSON (for names)")
    parser.add_argument("out_dir", type=Path, help="gtopt CSV output directory")
    parser.add_argument("output", type=Path, help="output image (.svg or .png)")
    parser.add_argument(
        "--buses", default="b1,b5", help="comma-separated buses for the LMP panel"
    )
    parser.add_argument(
        "--title", default="IEEE 9-bus, 24 h dispatch with a solar unit (HiGHS)"
    )
    args = parser.parse_args()
    plot(args.case, args.out_dir, args.output, args.buses.split(","), args.title)


if __name__ == "__main__":
    main()
