# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Meshed networks: native linearized pipes, valves and SLP options."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from gasnet2gtopt.common import BuildOptions
from gasnet2gtopt.legacy import build_legacy
from gasnet2gtopt.native import build_native

from .conftest import load_case


def _loop() -> dict[str, Any]:
    """mini3 with a parallel S -> T pipe (a loop) and an open valve."""
    desc = copy.deepcopy(load_case("mini3"))
    gas = desc["gas_network"]
    gas["network_model"] = "meshed"
    gas["slp_iterations"] = 50
    gas["slp_damping"] = 0.5
    gas["gas_pipeline_array"].append(
        {
            "uid": 3,
            "name": "S_T",
            "node_a": "S",
            "node_b": "T",
            "length": 150,
            "diameter": 300,
            "fmax": 200,
            "linearization": "tangent",
            "reference_flow": -5.0,
        }
    )
    gas["compressor_station_array"] = [
        {
            "uid": 1,
            "name": "V_MT",
            "node_in": "M",
            "node_out": "T",
            "fmax": 100,
            "bidirectional": True,
        }
    ]
    return desc


def test_meshed_native_linearizes_every_pipe() -> None:
    planning, _ = build_native(_loop(), BuildOptions(nblocks=1))
    pipes = planning["system"]["gas_pipe_array"]
    assert {p["weymouth_mode"] for p in pipes} == {"linearized"}
    st = next(p for p in pipes if p["name"] == "S_T")
    assert st["linearization"] == "tangent"
    assert st["reference_flow"] == -5.0
    assert planning["options"]["gas_options"] == {
        "slp_iterations": 50,
        "slp_damping": 0.5,
    }
    (valve,) = planning["system"]["gas_compressor_array"]
    assert valve == {
        "uid": 1,
        "name": "V_MT",
        "gas_node_in": "M",
        "gas_node_out": "T",
        "fmax": 100,
        "max_ratio": 1.0,
        "fuel_model": "none",
        "bidirectional": True,
    }


def test_meshed_is_native_only() -> None:
    with pytest.raises(NotImplementedError, match="native"):
        build_legacy(_loop(), BuildOptions(nblocks=1))


def test_radial_pipes_keep_their_mode() -> None:
    planning, _ = build_native(load_case("mini3"), BuildOptions(nblocks=1))
    assert all("weymouth_mode" not in p for p in planning["system"]["gas_pipe_array"])
