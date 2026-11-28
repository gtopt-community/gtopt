# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""opendss2gtopt -> gtopt2opendss round trip on the hand-made feeder.

The balanced equivalent re-exported to OpenDSS must reproduce the
equivalent's own data (same buses, kV bases, total load), and -- with
the optional engine -- an AC solution close to the original script.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtopt2opendss.convert import planning_to_dss
from opendss2gtopt.convert import build_planning
from opendss2gtopt.dss_parser import DssParser, parse_dss
from opendss2gtopt.network import build_network

TINY = (
    Path(__file__).resolve().parents[2]
    / "opendss2gtopt"
    / "tests"
    / "data"
    / "tiny_feeder.dss"
)


def test_reparse_exported_script() -> None:
    planning = build_planning(
        build_network(parse_dss(TINY)), network_model="lindistflow"
    )
    text = planning_to_dss(planning)
    again = build_network(DssParser().parse_text(text))
    assert set(again.buses) == {b["name"] for b in planning["system"]["bus_array"]}
    total = sum(d["lmax"][0][0] for d in planning["system"]["demand_array"])
    assert sum(x.p_mw for x in again.loads) == pytest.approx(total)
    for bus in planning["system"]["bus_array"]:
        assert again.buses[bus["name"]].kv == pytest.approx(bus["voltage"])


def test_ac_roundtrip() -> None:
    pytest.importorskip("opendssdirect")
    from gtopt2opendss import ac  # noqa: PLC0415

    planning = build_planning(
        build_network(parse_dss(TINY)), network_model="lindistflow"
    )
    sol = ac.solve_text(planning_to_dss(planning))
    assert sol.converged
    assert 0.9 < min(sol.vpu.values()) <= max(sol.vpu.values()) < 1.1
