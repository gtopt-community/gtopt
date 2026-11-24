# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Elevation term pi_a - e^s pi_b = K_e f|f| (both emitters)."""

from __future__ import annotations

import copy
import math

import pytest

from gasnet2gtopt import physics as ph
from gasnet2gtopt.common import BuildOptions, gas_properties, pipe_physics
from gasnet2gtopt.legacy import build_legacy
from gasnet2gtopt.native import build_native

from . import lp_ref
from .conftest import load_case


def test_exponent_and_factor() -> None:
    s = ph.elevation_exponent(4000.0)
    assert s == pytest.approx(
        2 * 9.80665 * 0.6 * 0.028964 * 4000 / (0.9 * 8.314 * 288.15)
    )
    assert ph.elevation_exponent(-4000.0) == pytest.approx(-s)
    assert ph.elevation_k_factor(0.0) == 1.0
    assert ph.elevation_k_factor(1e-12) == pytest.approx(1.0)
    for x in (-0.5, 1e-3, 0.63):
        assert ph.elevation_k_factor(x) == pytest.approx(math.expm1(x) / x)


def _chain() -> dict:
    """mini3 with S at 0 m, M at 2000 m and T at 4000 m."""
    desc = copy.deepcopy(load_case("mini3"))
    for n, h in zip(desc["gas_network"]["gas_node_array"], (0.0, 2000.0, 4000.0)):
        n["elevation"] = h
    desc["gas_network"]["gas_specific_gravity"] = 0.62
    return desc


def test_pipe_physics() -> None:
    desc = _chain()
    gas = desc["gas_network"]
    nodes = {n["name"]: n for n in gas["gas_node_array"]}
    assert gas_properties(gas) == (0.62, 288.15, 0.9)
    pipe = gas["gas_pipeline_array"][0]
    ke, exp_s = pipe_physics(pipe, gas, nodes)
    s = ph.elevation_exponent(2000.0, 0.62)
    k = ph.weymouth_k(pipe["length"], pipe["diameter"], gravity=0.62)
    assert exp_s == pytest.approx(math.exp(s))
    assert ke == pytest.approx(k * ph.elevation_k_factor(s))
    flat = dict(pipe, elevation_exponent=0.0)
    assert pipe_physics(flat, gas, nodes) == (pytest.approx(k), 1.0)
    level = {n: dict(v, elevation=100.0) for n, v in nodes.items()}
    assert pipe_physics(pipe, gas, level)[1] == 1.0


def test_native_passes_elevation_and_gas_options() -> None:
    planning, _ = build_native(_chain(), BuildOptions(nblocks=1))
    assert planning["options"]["gas_options"] == {"gas_specific_gravity": 0.62}
    elev = [n.get("elevation") for n in planning["system"]["gas_node_array"]]
    assert elev == [0.0, 2000.0, 4000.0]
    plain, _ = build_native(load_case("mini3"), BuildOptions(nblocks=1))
    assert "gas_options" not in plain["options"]


def test_legacy_climb_costs_pressure() -> None:
    """The climbing chain loses outlet pressure against the flat one."""
    flat = lp_ref.solve(build_legacy(load_case("mini3"), BuildOptions(nblocks=1))[0])
    planning, meta = build_legacy(_chain(), BuildOptions(nblocks=1))
    assert meta["pipes"]["S_M"]["exp_s"] > 1.0
    climb = lp_ref.solve(planning)
    assert climb.status == 0
    assert climb.objective > flat.objective
