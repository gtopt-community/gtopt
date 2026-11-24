#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""GasModels.jl case-6 loop with linearized pipes and the gas SLP.

Takes ``../gas_case6/gas_case6.json`` (fixed pipe orientations, tangent
outer approximation) and switches every pipe to
``weymouth_mode: linearized`` (signed flow, tangent linearization, no
orientation needed).  ``options.gas_options.slp_iterations`` re-linearizes
at each solution until the flows settle, so the result is a point of the
exact Weymouth equations.  It is compared against the exact NLP optimum
in ``../gas_case6/expected.json`` (GasModels WP objective -253.683).
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "gas_case6"


def main() -> None:
    planning = json.loads((SRC / "gas_case6.json").read_text())
    planning["options"]["gas_options"] = {
        "slp_iterations": 100,
        "slp_tolerance": 1e-7,
        "slp_damping": 0.5,
    }
    system = planning["system"]
    system["name"] = "gas_case6_meshed"
    for pipe in system["gas_pipe_array"]:
        pipe.pop("segments", None)
        pipe["weymouth_mode"] = "linearized"
        pipe["linearization"] = "tangent"
    (HERE / "gas_case6_meshed.json").write_text(json.dumps(planning, indent=1) + "\n")


if __name__ == "__main__":
    main()
