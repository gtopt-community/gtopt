# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Shared helpers: radial guard, profiles, station settings."""

from __future__ import annotations

import copy

import pytest

from gasnet2gtopt import common as cm
from gasnet2gtopt.physics import adiabatic_gamma

from .conftest import load_case


def test_check_radial_accepts_tree_and_rejects_loops() -> None:
    gas = load_case("tiny_compressor")["gas_network"]
    cm.check_radial(gas)
    loop = copy.deepcopy(gas)
    loop["gas_pipeline_array"].append(
        {"name": "T_S", "node_a": "T", "node_b": "S", "fmax": 1}
    )
    with pytest.raises(ValueError, match="closes a loop"):
        cm.check_radial(loop)
    meshed = dict(gas, network_model="meshed")
    with pytest.raises(NotImplementedError):
        cm.check_radial(meshed)


def test_resolve_profile_forms() -> None:
    prof = {
        "res_winter": [1.0, 2.0],
        "res_summer_scale": 0.5,
        "solar_pu": [0.0, 0.5],
        "city": [3, 4],
    }
    assert cm.resolve_profile(7, prof, 2, "winter") == [7.0, 7.0]
    assert cm.resolve_profile("city", prof, 2, "winter") == [3.0, 4.0]
    assert cm.resolve_profile("res", prof, 2, "winter") == [1.0, 2.0]
    assert cm.resolve_profile("res", prof, 2, "summer") == [0.5, 1.0]
    assert cm.resolve_profile("solar", prof, 2, "winter") == [0.0, 600.0]
    assert cm.resolve_profile({"summer": 1, "winter": 2}, prof, 2, "summer") == [
        1.0,
        1.0,
    ]
    # multi-day horizons repeat the daily profile
    assert cm.resolve_profile("city", prof, 6, "winter") == [3.0, 4.0] * 3


def test_station_model_modes() -> None:
    cs = load_case("tiny_compressor")["gas_network"]["compressor_station_array"][0]
    off = cm.station_model(cs, cm.BuildOptions())
    assert off.mode == "off"
    assert not off.is_booster
    series = cm.station_model(cs, cm.BuildOptions(compressor="series"))
    assert series.model == "fixed_fraction"
    assert series.sgamma == pytest.approx(adiabatic_gamma())
    assert series.gamma_ff == pytest.approx(series.sgamma * (1.4**series.alpha - 1))
    sald = cm.station_model(
        cs, cm.BuildOptions(compressor="series", fuel_model="saldarriaga", cs_gamma=0.2)
    )
    assert sald.model == "saldarriaga"
    assert sald.sgamma == 0.2

    booster = load_case("tiny_plant")["gas_network"]["compressor_station_array"][0]
    b_off = cm.station_model(booster, cm.BuildOptions())
    assert b_off.is_booster
    assert b_off.mode == "off"
    b_on = cm.station_model(booster, cm.BuildOptions(booster="on", booster_drive="gas"))
    assert b_on.mode == "series"
    assert b_on.drive == "gas"
    assert b_on.model == "saldarriaga"


def test_operating_point_defaults_and_lists() -> None:
    node = {"pmin": 30, "pmax": 60}
    psi0, betas, p_in0 = cm.operating_point({"fmax": 100, "max_ratio": 1.4}, node)
    assert psi0 == 60
    assert betas == [pytest.approx(1.2)]
    assert p_in0 == 45
    _, betas, _ = cm.operating_point(
        {"fmax": 100, "max_ratio": 1.4, "op_ratio": [1.0, 1.3]}, node
    )
    assert betas == [1.0, 1.3]


def test_plant_pressure_settings() -> None:
    link = {"generator": "G", "min_pressure": 30, "derate_nominal_factor": 1.1}
    assert cm.plant_pressure_settings(link, cm.BuildOptions()) == (30.0, 0.5, None)
    p = cm.plant_pressure_settings(link, cm.BuildOptions(derate=True))
    assert p is not None
    assert p[2] == pytest.approx(33.0)
    assert (
        cm.plant_pressure_settings(link, cm.BuildOptions(plant_pressure=False)) is None
    )
    assert cm.plant_pressure_settings({"generator": "G"}, cm.BuildOptions()) is None
    over = cm.BuildOptions(plant_pmin={"G": 25.0})
    assert cm.plant_pressure_settings(link, over) == (25.0, 0.5, None)


def test_power_system_resolves_profiles() -> None:
    desc = {
        "profiles": {"res_winter": [1.0, 2.0], "res_summer_scale": 0.5},
        "power_system": {
            "generator_array": [{"name": "W", "pmax": "res"}, {"name": "G", "pmax": 5}],
            "demand_array": [{"name": "D", "lmax": 3}],
        },
    }
    psys = cm.power_system(desc, cm.BuildOptions(nblocks=2), [{"name": "X"}])
    assert psys["generator_array"][0]["pmax"] == [[1.0, 2.0]]
    assert psys["generator_array"][1]["pmax"] == 5
    assert psys["demand_array"][0]["lmax"] == [[3.0, 3.0]]
    assert psys["demand_array"][1] == {"name": "X", "uid": 2}
    sk = cm.planning_skeleton(3)
    assert len(sk["simulation"]["block_array"]) == 3
    assert cm.system_name({"name": "c"}, cm.BuildOptions(linepack=True)) == (
        "c_winter_off_lp"
    )
