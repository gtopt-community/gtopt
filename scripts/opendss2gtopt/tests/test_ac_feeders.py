# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Converter fidelity against OpenDSS AC solutions (optional engine).

Each feeder is solved twice in OpenDSS: the original phase-coordinate
script (with its regulator controls), and the balanced equivalent that
``opendss2gtopt`` + ``gtopt2opendss`` produce, with the regulator taps
set to the bank average of the original solution.  The errors measure
the single-phase-equivalent reduction itself; they are zero for a
balanced feeder.  Skips without ``opendssdirect.py`` or the feeders.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtopt2opendss import ac
from gtopt2opendss.convert import planning_to_dss
from opendss2gtopt.convert import build_planning
from opendss2gtopt.dss_parser import parse_dss
from opendss2gtopt.feeders import feeder_path
from opendss2gtopt.network import build_network

dss = pytest.importorskip("opendssdirect")

DATA = Path(__file__).parent / "data"

# feeder -> (max |dV| pu, mean |dV| pu, |relative loss error|)
TOLERANCES = {
    "tiny": (0.01, 0.003, 0.15),
    "ieee4": (0.001, 0.0005, 0.01),
    "ieee13": (0.04, 0.008, 0.15),
    "ieee34": (0.02, 0.004, 0.05),
    "ieee123": (0.03, 0.006, 0.10),
}


def _feeder(name: str) -> Path:
    if name == "tiny":
        return DATA / "tiny_feeder.dss"
    path = feeder_path(name)
    if path is None:
        pytest.skip(f"{name} feeder not available (set GTOPT_OPENDSS_FEEDERS)")
    return path


@pytest.mark.parametrize("name", list(TOLERANCES))
def test_equivalent_matches_original(name: str) -> None:
    path = _feeder(name)
    net = build_network(parse_dss(path))
    planning = build_planning(net, network_model="lindistflow")
    ref = ac.solve_file(path)
    assert ref.converged
    taps = {}
    for xf in dss.Transformers.AllNames():
        dss.Transformers.Name(xf)
        dss.Transformers.Wdg(2)
        taps[xf.lower()] = dss.Transformers.Tap()
    lines = {ln["name"]: ln for ln in planning["system"]["line_array"]}
    for br in net.branches:
        if br.regulator:
            members = [m.split(".", 1)[1].lower() for m in br.members]
            lines[br.name]["tap_ratio"] = len(members) / sum(taps[m] for m in members)
    eq = ac.solve_text(planning_to_dss(planning))
    assert eq.converged
    cmp = ac.compare_voltages(eq.vpu, ref, model_losses_kw=eq.losses_kw)
    vmax, vmean, loss = TOLERANCES[name]
    print(
        f"{name}: max|dV|={cmp.max_abs_dv:.4f} mean={cmp.mean_abs_dv:.4f} "
        f"loss {ref.losses_kw:.2f}->{eq.losses_kw:.2f} kW"
    )
    assert cmp.max_abs_dv < vmax
    assert cmp.mean_abs_dv < vmean
    assert abs(cmp.loss_rel_err or 0.0) < loss
