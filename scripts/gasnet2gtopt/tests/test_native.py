# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Native gas-element output of the converter."""

from __future__ import annotations

import copy

import pytest

from gasnet2gtopt.common import BuildOptions
from gasnet2gtopt.native import build_native

from .conftest import LIT_CASES, VARIANTS, lit_options, load_case, load_lit
from .conftest import variant_options


def _sys(case: str, **kw: object) -> dict:
    planning, _ = build_native(load_case(case), BuildOptions(nblocks=1, **kw))
    return planning["system"]


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_every_variant_emits_native_arrays(name: str) -> None:
    planning, meta = build_native(load_case(VARIANTS[name][0]), variant_options(name))
    sysd = planning["system"]
    assert "decision_variable_array" not in sysd
    assert "user_constraint_array" not in sysd
    assert sysd["gas_node_array"]
    assert set(meta["nodes"]) == {n["name"] for n in sysd["gas_node_array"]}
    assert len(planning["simulation"]["block_array"]) == VARIANTS[name][2]
    for node in sysd["gas_node_array"]:
        assert node["pressure_cost"] == pytest.approx(-1e-4)


@pytest.mark.parametrize("name", sorted(LIT_CASES))
def test_literature_builds(name: str) -> None:
    planning, _ = build_native(load_lit(name), lit_options())
    assert planning["system"]["gas_pipe_array"]


def test_bypassed_station_is_a_regulator() -> None:
    (cs,) = _sys("tiny_compressor")["gas_compressor_array"]
    assert cs == {
        "uid": 1,
        "name": cs["name"],
        "gas_node_in": "A",
        "gas_node_out": "B",
        "fmax": cs["fmax"],
        "max_ratio": 1.0,
        "fuel_model": "none",
        "allow_bypass": True,
    }


def test_fixed_fraction_and_saldarriaga_stations() -> None:
    (ff,) = _sys("tiny_compressor", compressor="series")["gas_compressor_array"]
    assert ff["fuel_model"] == "fixed_fraction"
    assert ff["fuel_fraction"] > 0
    assert ff["allow_bypass"] is True
    (sd,) = _sys("tiny_compressor", compressor="series", fuel_model="saldarriaga")[
        "gas_compressor_array"
    ]
    assert sd["fuel_model"] == "saldarriaga"
    assert sd["op_ratio"] == [pytest.approx(1.2)]
    assert sd["allow_bypass"] is False


def test_booster_drives() -> None:
    (el,) = _sys("tiny_plant", booster="on")["gas_compressor_array"]
    assert el["type"] == "fuel_gas_booster"
    assert el["drive"] == "electric"
    assert el["bus"] == "B"
    assert el["power_max"] == 5
    (gas,) = _sys("tiny_plant", booster="on", booster_drive="gas")[
        "gas_compressor_array"
    ]
    assert "drive" not in gas
    assert "bus" not in gas


def test_turbine_gate_and_derate() -> None:
    (gt,) = _sys("tiny_plant")["gas_turbine_array"]
    assert gt["name"] == "G_R_gas"
    assert gt["min_pressure"] == 30.0
    assert gt["pressure_band"] == 0.5
    assert "nominal_pressure" not in gt
    (dr,) = _sys("tiny_plant", derate=True)["gas_turbine_array"]
    assert dr["nominal_pressure"] == pytest.approx(34.5)
    (none,) = _sys("tiny_plant", plant_pressure=False)["gas_turbine_array"]
    assert "min_pressure" not in none

    desc = load_case("tiny_plant")
    desc["gas_network"]["generator_gas_link_array"][0]["derate_min_output"] = 0.9
    planning, _ = build_native(desc, BuildOptions(nblocks=1, derate=True))
    assert planning["system"]["gas_turbine_array"][0]["derate_min_output"] == 0.9


def test_linepack_and_block_schedules() -> None:
    sysd = build_native(load_case("tiny_linepack"), variant_options("lp_pack"))[0][
        "system"
    ]
    assert sysd["gas_pipe_array"][0]["linepack"] is True
    assert sysd["gas_demand_array"][0]["load"] == [[40, 40, 160, 40]]
    steady = build_native(load_case("tiny_linepack"), variant_options("lp_steady"))[0]
    assert "linepack" not in steady["system"]["gas_pipe_array"][0]
    assert _sys("mini3")["gas_demand_array"][0]["load"] == 60
    assert _sys("mini3", segments=3)["gas_pipe_array"][0]["segments"] == 3


def test_no_compressor_no_turbine_arrays_dropped() -> None:
    sysd = build_native(load_lit("saldarriaga_phd_fig2_2"), lit_options())[0]["system"]
    assert "gas_turbine_array" not in sysd


def test_tiebreak_off_and_errors() -> None:
    desc = load_case("tiny_compressor")
    desc["gas_network"]["pressure_tiebreak"] = 0
    planning, _ = build_native(desc, BuildOptions(nblocks=1))
    assert "pressure_cost" not in planning["system"]["gas_node_array"][0]

    bad = copy.deepcopy(load_case("tiny_compressor"))
    bad["gas_network"]["compressor_station_array"][0]["fuel_model"] = "bogus"
    with pytest.raises(ValueError, match="fuel_model"):
        build_native(bad, BuildOptions(nblocks=1, compressor="series"))

    bad = copy.deepcopy(load_case("tiny_plant"))
    bad["gas_network"]["compressor_station_array"][0]["drive"] = "steam"
    with pytest.raises(ValueError, match="drive"):
        build_native(bad, BuildOptions(nblocks=1, booster="on"))
