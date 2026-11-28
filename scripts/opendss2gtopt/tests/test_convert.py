# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for opendss2gtopt.convert (gtopt planning JSON)."""

from __future__ import annotations

from pathlib import Path

import pytest

from opendss2gtopt.convert import _pf, _profile, build_planning
from opendss2gtopt.dss_parser import parse_dss
from opendss2gtopt.network import build_network

DATA = Path(__file__).parent / "data"


def _tiny():
    return build_network(parse_dss(DATA / "tiny_feeder.dss"))


def test_dc_planning_has_no_distribution_fields() -> None:
    p = build_planning(_tiny())
    s = p["system"]
    assert "network_model" not in p["options"]["model_options"]
    assert "capacitor_array" not in s
    assert all("vmin_pu" not in b and "shunt_q" not in b for b in s["bus_array"])
    assert all("power_factor" not in d for d in s["demand_array"])
    slack = s["bus_array"][0]
    assert slack["name"] == "src_src" and slack["reference_theta"] == 0.0
    assert s["line_array"][0]["name"] == "source_impedance"
    assert s["generator_array"][0]["name"] == "source"
    assert len(p["simulation"]["block_array"]) == 1
    assert s["battery_array"][0]["emax"] == pytest.approx(1.2)


def test_lindistflow_planning() -> None:
    p = build_planning(
        _tiny(), network_model="lindistflow", time_mode="daily", vmin=0.9
    )
    s = p["system"]
    assert p["options"]["model_options"]["network_model"] == "lindistflow"
    buses = {b["name"]: b for b in s["bus_array"]}
    assert buses["src_src"]["reference_voltage"] == pytest.approx(1.02)
    assert buses["b1"]["vmin_pu"] == 0.9 and buses["c3"]["vmax_pu"] == 1.05
    assert any("shunt_q" in b for b in s["bus_array"])
    reg = next(x for x in s["line_array"] if x["name"] == "rega")
    assert reg["tap_ratio_min"] == pytest.approx(1 / 1.1)
    assert reg["tap_ratio_max"] == pytest.approx(1 / 0.9)
    assert next(x for x in s["line_array"] if x["name"] == "sw")["type"] == "switch"
    assert "capacitor_array" not in s
    # banks fold into bus shunts (switched csw at its present 1-of-3 state)
    assert buses["c3"]["shunt_q"] >= 0.15 - 1e-9
    assert buses["b5"]["shunt_q"] >= 0.3 - 1e-9
    gens = {g["name"]: g for g in s["generator_array"]}
    assert gens["source"]["qmax"] > 0
    assert gens["g2"]["qmin"] == pytest.approx(-0.06)
    assert gens["g1"]["power_factor"] == pytest.approx(0.95)
    assert gens["pv1"]["type"] == "pv"
    assert len(s["demand_array"][0]["lmax"][0]) == 24
    assert s["generator_profile_array"][0]["generator"] == "pv1"
    assert len(p["simulation"]["block_array"]) == 24


def test_options_and_errors() -> None:
    net = _tiny()
    p = build_planning(
        net, network_model="lindistflow", optimize_taps=False, source_impedance=False
    )
    s = p["system"]
    assert s["bus_array"][0]["name"] == "src"
    assert all("tap_ratio_min" not in x for x in s["line_array"])
    with pytest.raises(ValueError):
        build_planning(net, network_model="ac")
    with pytest.raises(ValueError):
        build_planning(net, time_mode="weekly")


def test_helpers() -> None:
    assert _pf(1.0, 0.0) is None
    assert _pf(0.0, 0.0) is None
    assert _pf(0.8, -0.6) == pytest.approx(-0.8)
    net = _tiny()
    assert _profile(net, "", 3) == [1.0, 1.0, 1.0]
    assert _profile(net, "day", 1) == [1.0]
    assert len(_profile(net, "day", 48)) == 48
    assert _profile(net, "day", 12)[0] == pytest.approx(0.5)
    net.loadshapes["empty"] = []
    assert _profile(net, "empty", 2) == [1.0, 1.0]


def test_loss_segments() -> None:
    """lindistflow defaults to 16 loss segments; dc keeps gtopt's default."""
    mo = build_planning(_tiny())["options"]["model_options"]
    assert "loss_segments" not in mo
    mo = build_planning(_tiny(), network_model="lindistflow")["options"]
    assert mo["model_options"]["loss_segments"] == 16
    mo = build_planning(_tiny(), loss_segments=4)["options"]["model_options"]
    assert mo["loss_segments"] == 4
