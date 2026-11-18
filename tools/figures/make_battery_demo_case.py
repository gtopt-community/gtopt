#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Derive the battery arbitrage demo case from ``cases/bat_4b_24``.

The original case has a cheap baseload unit large enough to set a flat
price all day, so its battery never cycles.  The demo variant makes the
price move over the day:

* solar raised to 260 MW, so midday output exceeds demand (price 0);
* baseload ``g1`` (20 $/MWh) cut to 100 MW and mid-merit ``g2``
  (40 $/MWh) to 50 MW, so the evening peak needs ``g2``;
* a 90 $/MWh peaker ``g_peak`` added at bus b4 as the alternative the
  battery competes with.

The battery (60 MW / 200 MWh, 95 % charge and discharge efficiency) then
charges on the midday surplus and discharges into the evening peak.

Usage::

    python tools/figures/make_battery_demo_case.py \\
        cases/bat_4b_24/bat_4b_24.json /tmp/battery_demo.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def build(src: Path, dst: Path) -> None:
    case = json.loads(src.read_text(encoding="utf-8"))
    system = case["system"]
    gens = {g["name"]: g for g in system["generator_array"]}
    gens["g1"].update(pmax=100, capacity=100)
    gens["g2"].update(pmax=50, capacity=50)
    gens["g_solar"].update(pmax=260, capacity=260)
    system["generator_array"].append(
        {
            "uid": 4,
            "name": "g_peak",
            "bus": "b4",
            "pmin": 0,
            "pmax": 120,
            "gcost": 90,
            "capacity": 120,
        }
    )
    dst.write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
