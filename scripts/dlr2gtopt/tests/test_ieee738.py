# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""IEEE 738 evaluator: reference vectors and conventions shared with gtopt."""

from __future__ import annotations

import numpy as np
import pytest

from dlr2gtopt._ieee738 import (
    CONDUCTORS,
    Conductor,
    air_properties,
    ampacity,
    ampacity_to_mw,
    attack_angle,
    conductor_from_args,
    heat_balance,
    wind_angle_factor,
)

DRAKE = CONDUCTORS["drake"]
FLINT = CONDUCTORS["flint"]


def test_drake_ieee738_example() -> None:
    """IEEE 738 worked example: 100 C, Ta 40 C, 0.61 m/s perpendicular, sun
    (absorbed flux 22.46 W/m, given here as an equivalent GHI) -> ~1025 A."""
    s_eq = 22.46 / (0.8 * DRAKE.diameter)
    hb = heat_balance(DRAKE, 100.0, 40.0, 0.61, 90.0, s_eq)
    assert float(hb.ampacity) == pytest.approx(1025.0, rel=2e-3)
    assert float(hb.qc) == pytest.approx(82.1, rel=5e-3)
    assert float(hb.qr) == pytest.approx(39.1, rel=5e-3)
    assert float(hb.resistance) == pytest.approx(9.3905e-5, rel=1e-4)


def test_flint_matches_cen_static_rating() -> None:
    """CEN sheet of Los Vilos-Las Palmas: 0.587 kA at 25 C with sun (55 C)."""
    i = float(ampacity(FLINT, 55.0, 25.0, 0.61, None, 0.0, 1000.0))
    assert i == pytest.approx(587.0, rel=0.02)


def test_wind_angle_factor() -> None:
    assert float(wind_angle_factor(90.0)) == pytest.approx(1.0, abs=1e-3)
    assert float(wind_angle_factor(0.0)) == pytest.approx(0.388, abs=1e-3)
    k = wind_angle_factor(np.array([0.0, 45.0, 90.0]))
    assert np.all(np.diff(k) > 0)


def test_attack_angle_symmetry_and_wrap() -> None:
    assert float(attack_angle(90.0, 0.0)) == pytest.approx(90.0)
    assert float(attack_angle(180.0, 0.0)) == pytest.approx(0.0)
    assert float(attack_angle(10.0, 350.0)) == pytest.approx(20.0)
    assert float(attack_angle(190.0, 0.0)) == pytest.approx(10.0)
    assert float(attack_angle(45.0, 180.0)) == pytest.approx(45.0)


def test_low_wind_rule() -> None:
    phi = attack_angle([0.0, 0.0], 0.0, [0.2, 2.0])
    assert phi.tolist() == pytest.approx([45.0, 0.0])
    off = attack_angle([0.0], 0.0, [0.2], low_wind_speed=0.0)
    assert off.tolist() == pytest.approx([0.0])
    custom = attack_angle([0.0], 0.0, [0.2], low_wind_angle=30.0)
    assert custom.tolist() == pytest.approx([30.0])


def test_solar_modes_and_validation() -> None:
    sun = float(ampacity(DRAKE, 75.0, 30.0, 1.0, None, 0.0, 1000.0))
    none = float(ampacity(DRAKE, 75.0, 30.0, 1.0, None, 0.0, 1000.0, solar_mode="none"))
    night = float(ampacity(DRAKE, 75.0, 30.0, 1.0, None, 0.0, 0.0))
    assert none == pytest.approx(night)
    assert sun < none
    with pytest.raises(ValueError, match="solar_mode"):
        heat_balance(DRAKE, 75.0, 30.0, 1.0, solar_mode="sun")


def test_direction_reduces_rating() -> None:
    perp = float(ampacity(DRAKE, 75.0, 30.0, 2.0, 90.0, 0.0))
    para = float(ampacity(DRAKE, 75.0, 30.0, 2.0, 0.0, 0.0))
    assert para < perp
    assert perp == pytest.approx(float(ampacity(DRAKE, 75.0, 30.0, 2.0, None)))


def test_still_air_uses_natural_convection() -> None:
    hb = heat_balance(DRAKE, 75.0, 30.0, 0.0)
    assert float(hb.qc) > 0.0
    hot = heat_balance(DRAKE, 75.0, 80.0, 0.0, solar_irradiance=1000.0)
    assert float(hot.ampacity) == 0.0


def test_elevation_lowers_density() -> None:
    _, rho0, _ = air_properties(50.0, 0.0)
    _, rho3, _ = air_properties(50.0, 3000.0)
    assert float(rho3) < float(rho0)
    assert float(ampacity(DRAKE, 75.0, 30.0, 1.0, None, 0.0, 0.0, 3000.0)) < float(
        ampacity(DRAKE, 75.0, 30.0, 1.0, None, 0.0, 0.0, 0.0)
    )


def test_resistance_forms() -> None:
    assert float(DRAKE.resistance(75.0)) == pytest.approx(8.688e-5)
    assert float(FLINT.resistance(20.0)) == pytest.approx(1.02 * 0.0889e-3)
    assert float(FLINT.resistance(55.0)) == pytest.approx(
        1.02 * 0.0889e-3 * (1 + 0.0036 * 35)
    )


def test_conductor_validation_and_json() -> None:
    with pytest.raises(ValueError, match="exactly one form"):
        Conductor(diameter=0.02)
    with pytest.raises(ValueError, match="exactly one form"):
        Conductor(diameter=0.02, r25=1e-4, r75=1.2e-4, r20=1e-4, alpha_r=0.004)
    with pytest.raises(ValueError, match="diameter"):
        Conductor(diameter=0.0, r25=1e-4, r75=1.2e-4)
    with pytest.raises(ValueError, match="subconductors"):
        Conductor(diameter=0.02, r25=1e-4, r75=1.2e-4, subconductors=0)
    assert DRAKE.to_json()["r25"] == pytest.approx(7.283e-5)
    js = FLINT.to_json()
    assert js["r20"] == pytest.approx(0.0889e-3)
    assert "r25" not in js
    assert js["subconductors"] == 1


def test_conductor_from_args() -> None:
    c = conductor_from_args("Flint", subconductors=2, emissivity=None)
    assert c.subconductors == 2
    assert c.emissivity == pytest.approx(0.5)
    own = conductor_from_args(None, diameter=0.03, r25=1e-4, r75=1.2e-4)
    assert own.diameter == pytest.approx(0.03)
    with pytest.raises(ValueError, match="unknown conductor"):
        conductor_from_args("nope")


def test_ampacity_to_mw() -> None:
    mw = ampacity_to_mw(587.0, 220.0)
    assert float(mw) == pytest.approx(223.7, rel=1e-3)
    two = ampacity_to_mw(
        587.0, 220.0, subconductors=2, safety_factor=0.9, v_pu=1.0, power_factor=1.0
    )
    assert float(two) == pytest.approx(2 * 0.9 * float(mw))
