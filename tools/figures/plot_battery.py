#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Plot battery arbitrage: dispatch, state of charge and opportunity cost.

Three panels over the day, from a gtopt CSV output directory:

1. generation stacked by unit, battery charging drawn below zero, and
   total demand;
2. battery state of charge (MWh);
3. the marginal cost at the battery's bus (bus balance dual) against the
   battery's **opportunity cost of stored energy** (energy balance dual),
   with the charging and discharging hours shaded.

Usage::

    python tools/figures/make_battery_demo_case.py \\
        cases/bat_4b_24/bat_4b_24.json /tmp/battery_demo.json
    gtopt /tmp/battery_demo.json --solver highs \\
        --set output_directory=/tmp/battery_demo --set output_format=csv
    python tools/figures/plot_battery.py /tmp/battery_demo.json \\
        /tmp/battery_demo docs/images/battery_arbitrage.svg
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import _style
import matplotlib.pyplot as plt

Series = dict[int, list[float]]


def _read(out_dir: Path, cls: str, field: str, n: int) -> Series:
    """Read ``<cls>/<field>*.csv`` (long, sparse) into ``{uid: [per block]}``."""
    series: Series = defaultdict(lambda: [0.0] * n)
    for path in sorted((out_dir / cls).glob(f"{field}*.csv")):
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                block = int(row["block"]) - 1
                if 0 <= block < n:
                    series[int(row["uid"])][block] += float(row["value"])
    return series


def _step(values: list[float]) -> list[float]:
    return [*values, values[-1]]


def plot(case: Path, out_dir: Path, output: Path) -> None:
    planning = json.loads((out_dir / "planning.json").read_text(encoding="utf-8"))
    system = planning["system"]
    case_json = json.loads(case.read_text(encoding="utf-8"))
    n = len(case_json["simulation"]["block_array"])
    hours = list(range(n + 1))

    # The input case keeps the battery's bus; the written planning.json
    # has it expanded into a converter generator/demand pair.
    battery = case_json["system"]["battery_array"][0]
    bus_uid = next(
        b["uid"] for b in system["bus_array"] if b["name"] == battery["bus"]
    )
    gen_names = {g["uid"]: g["name"] for g in system["generator_array"]}

    gen = _read(out_dir, "Generator", "generation_sol", n)
    load = _read(out_dir, "Demand", "load_sol", n)
    discharge = _read(out_dir, "Battery", "fout_sol", n)[battery["uid"]]
    charge = _read(out_dir, "Battery", "finp_sol", n)[battery["uid"]]
    soc = _read(out_dir, "Battery", "energy_sol", n)[battery["uid"]]
    energy_value = _read(out_dir, "Battery", "energy_dual", n)[battery["uid"]]
    price = _read(out_dir, "Bus", "balance_dual", n)[bus_uid]

    converter_dem = {
        d["uid"] for d in system["demand_array"] if d["name"].startswith("bat")
    }
    demand = [
        sum(load[u][h] for u in load if u not in converter_dem) for h in range(n)
    ]

    _style.apply()
    fig, (ax_g, ax_s, ax_p) = plt.subplots(
        3,
        1,
        figsize=(11.0, 7.4),
        sharex=True,
        gridspec_kw={"height_ratios": [3.0, 1.3, 2.2], "hspace": 0.32},
    )

    # -- dispatch ----------------------------------------------------------
    layers = [
        ("solar", "g_solar", _style.YELLOW),
        ("baseload 20 $/MWh", "g1", _style.BLUE),
        ("mid-merit 40 $/MWh", "g2", _style.VIOLET),
        ("peaker 90 $/MWh", "g_peak", _style.ORANGE),
    ]
    by_name = {gen_names[u]: v for u, v in gen.items() if u in gen_names}
    bottom = [0.0] * n
    for label, name, color in layers:
        values = by_name.get(name, [0.0] * n)
        if not any(values):
            continue
        top = [b + v for b, v in zip(bottom, values, strict=True)]
        ax_g.fill_between(
            hours, _step(bottom), _step(top), step="post", color=color, alpha=0.85,
            linewidth=0, label=label,
        )
        bottom = top
    top = [b + v for b, v in zip(bottom, discharge, strict=True)]
    ax_g.fill_between(
        hours, _step(bottom), _step(top), step="post", color=_style.AQUA,
        alpha=0.95, linewidth=0, label="battery discharge",
    )
    ax_g.fill_between(
        hours, _step([-c for c in charge]), 0, step="post", color=_style.AQUA,
        alpha=0.45, hatch="////", edgecolor=_style.SURFACE, linewidth=0,
        label="battery charge",
    )
    ax_g.step(hours, _step(demand), where="post", color=_style.TEXT,
              linewidth=1.8, label="demand")
    ax_g.axhline(0, color=_style.TEXT_2, linewidth=0.8)
    ax_g.set_ylabel("MW")
    ax_g.set_title("Battery arbitrage: charge on the solar surplus, "
                   "discharge into the evening peak")
    ax_g.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0))

    # -- state of charge ---------------------------------------------------
    ax_s.fill_between(hours, [0.0, *soc], color=_style.AQUA, alpha=0.35,
                      linewidth=0)
    ax_s.plot(hours, [0.0, *soc], color=_style.AQUA, linewidth=2)
    ax_s.set_ylim(0, battery.get("emax", max(soc)) * 1.12)
    ax_s.set_ylabel("MWh")
    ax_s.set_title("State of charge")

    # -- prices ------------------------------------------------------------
    for h in range(n):
        if charge[h] > 1e-6:
            ax_p.axvspan(h, h + 1, color=_style.AQUA, alpha=0.10, linewidth=0)
        elif discharge[h] > 1e-6:
            ax_p.axvspan(h, h + 1, color=_style.ORANGE, alpha=0.10, linewidth=0)
    ax_p.step(hours, _step(price), where="post", color=_style.BLUE,
              linewidth=2.2, label=f"marginal cost\n(bus {battery['bus']})")
    ax_p.step(hours, _step(energy_value), where="post", color=_style.MAGENTA,
              linewidth=2.2, linestyle="--",
              label="opportunity cost\nof stored energy")
    ax_p.set_ylabel("$/MWh")
    ax_p.set_xlabel("hour")
    ax_p.set_title("Marginal cost vs battery opportunity cost")
    ax_p.set_ylim(-3, max(max(price), max(energy_value)) * 1.25)
    ymax = ax_p.get_ylim()[1]
    first_c = next((h for h in range(n) if charge[h] > 1e-6), None)
    first_d = next((h for h in range(n) if discharge[h] > 1e-6), None)
    if first_c is not None:
        ax_p.text(first_c + 0.2, ymax * 0.88, "charging", color=_style.AQUA,
                  fontsize=9, fontweight="bold")
    if first_d is not None:
        ax_p.text(first_d + 0.2, ymax * 0.88, "discharging",
                  color=_style.ORANGE, fontsize=9, fontweight="bold")
    ax_p.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0))
    ax_p.set_xlim(0, n)
    ax_p.set_xticks(range(0, n + 1, 3))

    fig.savefig(output, bbox_inches="tight")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("figure", type=Path)
    args = parser.parse_args()
    plot(args.case, args.output_dir, args.figure)


if __name__ == "__main__":
    main()
