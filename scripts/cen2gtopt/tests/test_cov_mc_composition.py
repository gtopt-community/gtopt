# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage tests for :mod:`cen2gtopt._mc_composition` edge cases."""

from __future__ import annotations

import math

import pandas as pd

from cen2gtopt._mc_composition import (
    _emission_factor_for_fuel,
    _norm,
    compose_declared_mc,
)


def test_norm_handles_none_and_nan() -> None:
    assert _norm(None) == ""
    assert _norm(float("nan")) == ""
    assert _norm("Gas Natúral-1") == "gasnatural1"


def test_unknown_fuel_factor_is_nan() -> None:
    assert math.isnan(_emission_factor_for_fuel("Otro"))


def test_no_match_returns_empty_result() -> None:
    costos = pd.DataFrame(
        [
            {
                "date_utc": "2024-11-01",
                "central_name": "A",
                "configuracion": "A_CFG",
                "costo_combustible": 10.0,
            }
        ]
    )
    fuelcons = pd.DataFrame(
        [{"plant_name": "Z", "fuel": "Diesel", "heat_rate": 0.2, "fuel_unit": "m3"}]
    )
    res = compose_declared_mc(costos, fuelcons)
    assert res.n_match == 0
    assert res.declared_mc.empty
    assert len(res.unmatched_costos) == 1
    assert len(res.unmatched_fuelcons) == 1
    assert res.n_total_costos == 1
    assert res.n_total_fuelcons == 1
