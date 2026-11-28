# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""LinDistFlow against the OpenDSS AC power flow of the same dispatch.

Pipeline per feeder: opendss2gtopt (``lindistflow``) -> gtopt (HiGHS)
-> gtopt2opendss with the solved dispatch and taps -> OpenDSS AC power
flow -> compare gtopt's ``|V| = sqrt(vsq)`` with the AC voltages.  The
residual is the LinDistFlow approximation alone (both sides see the
same balanced network, loads, taps and reactive injections), mostly
the neglected series-loss term ``r(P² + Q²)/V²``.

Skips without the ``gtopt`` binary (``GTOPT_BIN`` / PATH), the
``opendssdirect`` engine or the IEEE feeders.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from gtopt2opendss import ac
from gtopt2opendss.convert import planning_to_dss, read_dispatch
from gtopt2opendss.main import gtopt_vsq
from gtopt_shared.testing.roundtrip import gtopt_binary, solve_cost
from opendss2gtopt.convert import build_planning
from opendss2gtopt.dss_parser import parse_dss
from opendss2gtopt.feeders import feeder_path
from opendss2gtopt.network import build_network

TINY = (
    Path(__file__).resolve().parents[2]
    / "opendss2gtopt"
    / "tests"
    / "data"
    / "tiny_feeder.dss"
)

# feeder -> (max |dV|, mean |dV|) tolerance in p.u.  Measured
# (HiGHS, 16 loss segments): tiny 0.0011/0.0005, ieee4 0.0045/0.0020,
# ieee13 0.0024/0.0018, ieee34 0.0239/0.0085 (worst at 890, 0.81 pu
# with the regulators at neutral), ieee123 0.0010/0.0008.
TOLERANCE = {
    "tiny": (0.003, 0.001),
    "ieee4": (0.01, 0.004),
    "ieee13": (0.005, 0.003),
    "ieee34": (0.03, 0.012),
    "ieee123": (0.003, 0.002),
}


def _master(feeder: str) -> Path | None:
    if feeder == "tiny":
        return TINY
    try:
        return feeder_path(feeder)
    except (OSError, RuntimeError):
        return None


def run_ldf_vs_ac(
    master: Path, tmp: Path, *, optimize_taps: bool = False
) -> tuple[ac.Comparison, ac.AcSolution, dict[str, float]]:
    """Solve the LinDistFlow case and its OpenDSS AC image; compare."""
    gtopt = gtopt_binary()
    assert gtopt is not None
    net = build_network(parse_dss(master))
    planning = build_planning(
        net,
        network_model="lindistflow",
        vmin=0.0,
        vmax=2.0,
        optimize_taps=optimize_taps,
    )
    case = tmp / "case.json"
    case.write_text(json.dumps(planning), encoding="utf-8")
    out = tmp / "out"
    solve_cost(gtopt, case, out)
    vsq = gtopt_vsq(planning, out, 0)
    dispatch = read_dispatch(planning, out, 0)
    sol = ac.solve_text(planning_to_dss(planning, dispatch=dispatch))
    assert sol.converged
    vpu = ac.vpu_from_vsq(vsq)
    slack = {str(b["name"]) for b in planning["system"]["bus_array"][:1]}
    cmp = ac.compare_voltages(vpu, sol, skip=slack)
    return cmp, sol, {"taps": float(len(dispatch.tap))}


@pytest.mark.parametrize("feeder", list(TOLERANCE))
def test_lindistflow_matches_ac(feeder: str, tmp_path: Path) -> None:
    if gtopt_binary() is None:
        pytest.skip("gtopt binary not available")
    pytest.importorskip("opendssdirect")
    master = _master(feeder)
    if master is None:
        pytest.skip(f"feeder {feeder} not available")
    cmp, sol, _ = run_ldf_vs_ac(master, tmp_path)
    print(
        f"{feeder}: n={cmp.n_buses} max|dV|={cmp.max_abs_dv:.4f} "
        f"({cmp.worst_bus}) mean={cmp.mean_abs_dv:.4f} "
        f"AC losses={sol.losses_kw:.1f} kW"
    )
    max_tol, mean_tol = TOLERANCE[feeder]
    assert cmp.max_abs_dv <= max_tol
    assert cmp.mean_abs_dv <= mean_tol
    assert math.isfinite(sol.losses_kw)
