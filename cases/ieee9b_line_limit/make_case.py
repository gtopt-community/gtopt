# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Build the ieee9b_line_limit case and its pandapower AC-OPF reference.

The network is the MATPOWER / pandapower ``case9`` (Chow 1982, 345 kV,
lines with resistance) with two changes so a lossy line congests:

* linear generator costs (``cp1`` only; gtopt prices energy linearly);
* line 5-6 (pandapower line 2, R = 46.4 ohm) limited to
  ``max_loading_percent = 80`` (120 MVA of its 150 MVA rating);
* no shunt charging on that line (``c_nf_per_km = 0``): a DC model has
  none, and with it the line's charging current makes the AC limit bind
  at the receiving end (see the README sensitivity row).

``python make_case.py`` writes ``<mode>/ieee9b_line_limit.json`` for the
three ``line_thermal_limit_mode`` values (``tangent_signed_flow`` loss
model, which books half of the loss at each end) and prints the AC-OPF
reference.  ``scripts/pp2gtopt/tests/test_thermal_limit_acopf.py``
imports this module.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

CASE_DIR = Path(__file__).resolve().parent
CASE_NAME = "ieee9b_line_limit"
MODES = ("flow", "sending", "full")
LOSS_MODE = "tangent_signed_flow"
LOSS_SEGMENTS = 8
#: pandapower index of the congested line (bus 6 -> bus 5, 1-based).
LINE_IDX = 2
MAX_LOADING = 80.0


def build_net(charging: bool = False) -> Any:
    """Return the modified pandapower case9 network.

    ``charging=True`` keeps the stock shunt capacitance of the congested
    line (sensitivity case).
    """
    import pandapower.networks as pn  # pylint: disable=import-outside-toplevel

    net = pn.case9()
    net.poly_cost["cp2_eur_per_mw2"] = 0.0
    net.poly_cost["cp0_eur"] = 0.0
    net.line.loc[LINE_IDX, "max_loading_percent"] = MAX_LOADING
    if not charging:
        net.line.loc[LINE_IDX, "c_nf_per_km"] = 0.0
    return net


def rating_mw(net: Any) -> float:
    """Thermal limit of the congested line as pp2gtopt writes it [MW]."""
    row = net.line.loc[LINE_IDX]
    kv = float(net.bus.loc[int(row["from_bus"]), "vn_kv"])
    return float(row["max_i_ka"]) * kv * math.sqrt(3) * MAX_LOADING / 100.0


def run_acopf(net: Any) -> dict[str, float]:
    """Solve the pandapower AC-OPF and return the congested-line figures.

    ``s_eq_*`` is the current at each end expressed as MVA at nominal
    voltage (``sqrt(3)·V_nom·I``): the quantity the 80 % limit bounds.
    """
    import pandapower as pp  # pylint: disable=import-outside-toplevel

    pp.runopp(net)
    res = net.res_line.loc[LINE_IDX]
    row = net.line.loc[LINE_IDX]
    kv = float(net.bus.loc[int(row["from_bus"]), "vn_kv"])
    # pandapower line 2 runs bus 4 -> 5 (0-based); power flows 5 -> 4.
    send_is_to = float(res["p_to_mw"]) > 0.0
    p_send = float(res["p_to_mw"] if send_is_to else res["p_from_mw"])
    p_recv = -float(res["p_from_mw"] if send_is_to else res["p_to_mw"])
    i_send = float(res["i_to_ka"] if send_is_to else res["i_from_ka"])
    i_recv = float(res["i_from_ka"] if send_is_to else res["i_to_ka"])
    send_bus = int(row["to_bus"] if send_is_to else row["from_bus"])
    recv_bus = int(row["from_bus"] if send_is_to else row["to_bus"])
    return {
        "p_send": p_send,
        "p_recv": p_recv,
        "loss": float(res["pl_mw"]),
        "s_eq_send": math.sqrt(3) * kv * i_send,
        "s_eq_recv": math.sqrt(3) * kv * i_recv,
        "loading": float(res["loading_percent"]),
        "lmp_send": float(net.res_bus.loc[send_bus, "lam_p"]),
        "lmp_recv": float(net.res_bus.loc[recv_bus, "lam_p"]),
        "total_loss": float(net.res_line["pl_mw"].sum()),
        "cost": float(net.res_cost),
        "send_bus": send_bus + 1,
        "recv_bus": recv_bus + 1,
    }


def gtopt_case(
    net: Any,
    mode: str,
    out: Path,
    loss_mode: str = LOSS_MODE,
    allocation: str | None = None,
) -> dict[str, Any]:
    """Convert *net* with pp2gtopt and write the gtopt JSON to *out*."""
    from pp2gtopt.convert import (  # pylint: disable=import-outside-toplevel
        LineLossOptions,
        convert,
    )

    planning = convert(
        out,
        net=net,
        name=CASE_NAME,
        solver_type="monolithic",
        scale_objective=1.0,
        losses=LineLossOptions(
            line_losses_mode=loss_mode,
            loss_segments=LOSS_SEGMENTS,
            line_thermal_limit_mode=mode,
        ),
    )
    opts = planning["options"]["model_options"]
    # Radial-free, all-positive prices: no arbitrage guard needed, and
    # ε would bias the LMPs by ε·(marginal loss).
    opts["loss_cost_eps"] = 0.0
    if allocation is not None:
        for line in planning["system"]["line_array"]:
            line["loss_allocation_mode"] = allocation
    out.write_text(json.dumps(planning, indent=2) + "\n", encoding="utf-8")
    return planning


def main() -> int:
    """Write the three mode variants and print the AC-OPF reference."""
    sys.path.insert(0, str(CASE_DIR.parents[1] / "scripts"))
    for mode in MODES:
        (CASE_DIR / mode).mkdir(exist_ok=True)
        gtopt_case(build_net(), mode, CASE_DIR / mode / f"{CASE_NAME}.json")
    print(f"rating {rating_mw(build_net()):.2f} MW")
    for charging in (False, True):
        print(f"AC-OPF, line charging {'on' if charging else 'off'}:")
        for key, val in run_acopf(build_net(charging)).items():
            print(f"  {key:12s} {val:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
