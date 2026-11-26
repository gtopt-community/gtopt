# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Loss-eps acceptance: the clean CPF load term keeps every price at or above
``−ε``; the ``--legacy-profile cen`` endogenous load term does not.

A 2-bus case with a resistive line and one CPF-style plane requirement
``R ≥ 80 − 0.2·D`` that the only provider (20 MW) cannot meet, so the
zone runs short at 1000 $/MW.  With the clean exogenous ``D`` (the demand)
the plane is data and the LMP is the energy cost.  With ``cen``'s
``D = generation`` every generated MWh lowers the shortage by 0.2 MW and
earns 200 $/MWh, so the price is about −190 $/MWh.  The guard ε = 0.1 no
longer holds, and the LP books fictitious losses on the line: the post-solve
check must flag it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from gtopt_check_output._loss_arbitrage import detect_loss_eps_invalidated
from plexos2gtopt._native_reserve import NativeReserveOptions
from plexos2gtopt.main import PLEXOS_LEGACY_PROFILES


def _gtopt_binary() -> Path | None:
    env = os.environ.get("GTOPT_BIN")
    if env and Path(env).is_file() and os.access(env, os.X_OK):
        return Path(env)
    candidate = Path(__file__).resolve().parents[3] / "build" / "standalone" / "gtopt"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    on_path = shutil.which("gtopt")
    return Path(on_path) if on_path else None


def _load_term(profile: str | None) -> str:
    """The zone ``load_term`` a profile's ``reserve_load_term`` writes."""
    term = NativeReserveOptions().load_term
    if profile is not None:
        term = next(
            t.value
            for t in PLEXOS_LEGACY_PROFILES[profile]
            if t.dest == "reserve_load_term"
        )
    return "endogenous_generation" if term == "endogenous" else term


def _case(load_term: str) -> dict[str, Any]:
    zone: dict[str, Any] = {
        "uid": 1,
        "name": "CPF",
        "urreq_planes": [{"alpha": 80.0, "demand_coef": -0.2}],
        "urcost": 1000.0,
        "load_term": load_term,
    }
    if load_term == "exogenous":
        zone["demand_load_factor"] = 1.0
    return {
        "options": {
            "output_format": "parquet",
            "model_options": {
                "use_single_bus": False,
                "use_kirchhoff": True,
                "demand_fail_cost": 1000.0,
                "scale_objective": 1.0,
            },
        },
        "simulation": {
            "block_array": [{"uid": 1, "duration": 1.0}],
            "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
            "scenario_array": [{"uid": 1}],
        },
        "system": {
            "name": "eps2bus",
            "bus_array": [{"uid": 1, "name": "A"}, {"uid": 2, "name": "B"}],
            "generator_array": [
                {"uid": 1, "name": "G1", "bus": "A", "gcost": 10.0, "pmax": 400.0},
                {"uid": 2, "name": "G2", "bus": "A", "gcost": 50.0, "pmax": 400.0},
            ],
            "demand_array": [{"uid": 1, "name": "D", "bus": "B", "lmax": 100.0}],
            "line_array": [
                {
                    "uid": 1,
                    "name": "AB",
                    "bus_a": "A",
                    "bus_b": "B",
                    "tmax_ab": 300.0,
                    "tmax_ba": 300.0,
                    "reactance": 0.01,
                    "resistance": 0.02,
                    "voltage": 10.0,
                    "loss_envelope": 300.0,
                    "line_losses_mode": "tangent_signed_flow",
                    "loss_segments": 8,
                }
            ],
            "reserve_zone_array": [zone],
            "reserve_provision_array": [
                {
                    "uid": 1,
                    "name": "p_G2",
                    "generator": "G2",
                    "reserve_zones": ["CPF"],
                    "urmax": 20.0,
                    "ur_provision_factor": 1.0,
                }
            ],
        },
    }


def test_profiles_pick_the_load_terms() -> None:
    assert _load_term(None) == "exogenous"
    assert _load_term("cen") == "endogenous_generation"


@pytest.mark.skipif(
    _gtopt_binary() is None,
    reason="gtopt binary not available (set GTOPT_BIN or build standalone)",
)
@pytest.mark.parametrize("profile", [None, "cen"])
def test_clean_holds_loss_eps_and_cen_breaks_it(
    tmp_path: Path, profile: str | None
) -> None:
    pd = pytest.importorskip("pandas")
    binary = _gtopt_binary()
    assert binary is not None
    planning = _case(_load_term(profile))
    (tmp_path / "c.json").write_text(json.dumps(planning))
    proc = subprocess.run(
        [str(binary), "c.json", "-d", "output"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    log = proc.stdout + proc.stderr
    if proc.returncode != 0 and "Could not find member" in log:
        pytest.skip(f"{binary} predates the extended reserve schema")
    assert proc.returncode == 0, log[-4000:]
    out = tmp_path / "output"
    lam = pd.read_parquet(next((out / "Bus").glob("balance_dual*")))["value"]
    viol = detect_loss_eps_invalidated(out, planning)
    if profile is None:
        assert lam.min() >= -0.1
        assert not viol
    else:
        assert lam.max() < -0.1
        assert {v.bus for v in viol} == {"A", "B"}
        assert all(v.excess_mw > 1.0 and v.lines == ("AB",) for v in viol)
