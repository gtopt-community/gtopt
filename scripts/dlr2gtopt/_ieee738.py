# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""IEEE Std 738 steady-state ampacity (SI form), vectorised with numpy.

Reference: IEEE Std 738-2012, "IEEE Standard for Calculating the
Current-Temperature Relationship of Bare Overhead Conductors",
doi:10.1109/IEEESTD.2013.6692858 (revised 2023).

Same conventions as gtopt's C++ evaluator (``include/gtopt/ieee738.hpp``),
so a rating computed here and one computed by gtopt from the same weather
agree:

* heat balance at the maximum conductor temperature ``T_s``::

      I^2 R(T_s) + q_s = q_c + q_r,   I = sqrt((q_c + q_r - q_s) / R(T_s))

  clamped to 0 when the net heat loss is not positive;
* convection ``q_c = max(q_c1, q_c2, q_cn)`` with the wind-angle factor
  ``K = 1.194 - cos(phi) + 0.194 cos(2 phi) + 0.368 sin(2 phi)`` applied
  to the two forced forms only;
* attack angle ``phi = |((dir - azimuth + 90) mod 180) - 90|`` and the
  low-wind rule (below ``low_wind_speed`` the direction is ignored and
  ``low_wind_angle`` is used; ``low_wind_speed = 0`` disables it);
* solar gain: ``solar_mode = "ghi"`` uses the global horizontal irradiance
  directly as absorbed flux, ``q_s = alpha * S * D`` (conservative, no
  sun-geometry model); ``"none"`` sets ``q_s = 0``;
* resistance linear in temperature, either from the tabulated AC values
  ``r25``/``r75`` or from ``R(T) = ac_factor * r20 * (1 + alpha_r (T - 20))``
  (the convention of the Chilean CEN technical sheets).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
ArrayLike = npt.ArrayLike

LOW_WIND_SPEED = 0.5
"""Default low-wind threshold [m/s] (same as gtopt's ``kLowWindSpeed``)."""
LOW_WIND_ANGLE = 45.0
"""Default low-wind attack angle [deg] (gtopt's ``kLowWindAttackAngle``)."""
SOLAR_MODES = ("ghi", "none")


@dataclass(frozen=True)
class Conductor:
    """Conductor thermal parameters (SI).

    Give either ``r25`` and ``r75`` (AC, ohm/m) or ``r20`` (DC, ohm/m) with
    ``alpha_r`` (1/K) and ``ac_factor``.
    """

    diameter: float
    r25: float | None = None
    r75: float | None = None
    r20: float | None = None
    alpha_r: float | None = None
    ac_factor: float = 1.0
    emissivity: float = 0.8
    absorptivity: float = 0.8
    subconductors: int = 1

    def __post_init__(self) -> None:
        tab = self.r25 is not None and self.r75 is not None
        dc = self.r20 is not None and self.alpha_r is not None
        if tab == dc:
            raise ValueError(
                "conductor: give either r25+r75 or r20+alpha_r (exactly one form)"
            )
        if self.diameter <= 0.0:
            raise ValueError("conductor: diameter must be > 0")
        if self.subconductors < 1:
            raise ValueError("conductor: subconductors must be >= 1")

    def resistance(self, temperature: ArrayLike) -> FloatArray:
        """AC resistance [ohm/m] at ``temperature`` [degC]."""
        t = np.asarray(temperature, dtype=float)
        if self.r20 is not None and self.alpha_r is not None:
            return self.ac_factor * self.r20 * (1.0 + self.alpha_r * (t - 20.0))
        assert self.r25 is not None and self.r75 is not None
        return self.r25 + (self.r75 - self.r25) / 50.0 * (t - 25.0)

    def to_json(self) -> dict[str, float | int]:
        """The ``dlr.conductor`` object of gtopt's Line schema."""
        out: dict[str, float | int] = {"diameter": self.diameter}
        if self.r20 is not None and self.alpha_r is not None:
            out.update(r20=self.r20, alpha_r=self.alpha_r, ac_factor=self.ac_factor)
        else:
            assert self.r25 is not None and self.r75 is not None
            out.update(r25=self.r25, r75=self.r75)
        out.update(
            emissivity=self.emissivity,
            absorptivity=self.absorptivity,
            subconductors=self.subconductors,
        )
        return out


# Named conductors.  DRAKE is the IEEE 738 worked example (26/7 ACSR);
# FLINT is AAAC Flint 375.4 mm2 with the values of the CEN Infotecnica
# technical sheet of Los Vilos - Las Palmas 220 kV (R20 0.0889 ohm/km);
# emissivity/absorptivity 0.5 reproduce CEN's thermal tables to ~1 %.
CONDUCTORS: dict[str, Conductor] = {
    "drake": Conductor(diameter=0.02814, r25=7.283e-5, r75=8.688e-5),
    "flint": Conductor(
        diameter=0.02517,
        r20=0.0889e-3,
        alpha_r=0.0036,
        ac_factor=1.02,
        emissivity=0.5,
        absorptivity=0.5,
    ),
}


def conductor_from_args(name: str | None = None, **overrides: Any) -> Conductor:
    """A named conductor (``CONDUCTORS``) with optional field overrides."""
    clean: dict[str, Any] = {k: v for k, v in overrides.items() if v is not None}
    if name is not None:
        key = name.lower()
        if key not in CONDUCTORS:
            raise ValueError(
                f"unknown conductor {name!r}; known: {', '.join(sorted(CONDUCTORS))}"
            )
        return replace(CONDUCTORS[key], **clean)
    return Conductor(**clean)


def wind_angle_factor(attack_angle_deg: ArrayLike) -> FloatArray:
    """IEEE 738 wind-direction factor ``K_angle`` (1 at 90 deg)."""
    phi = np.radians(np.asarray(attack_angle_deg, dtype=float))
    return 1.194 - np.cos(phi) + 0.194 * np.cos(2 * phi) + 0.368 * np.sin(2 * phi)


def attack_angle(
    wind_direction_deg: ArrayLike,
    azimuth_deg: ArrayLike,
    wind_speed: ArrayLike | None = None,
    low_wind_speed: float = LOW_WIND_SPEED,
    low_wind_angle: float = LOW_WIND_ANGLE,
) -> FloatArray:
    """Angle in [0, 90] deg between the wind and the span axis.

    Below ``low_wind_speed`` the direction is meaningless and
    ``low_wind_angle`` is returned (``low_wind_speed = 0`` disables it).
    """
    m = np.mod(
        np.asarray(wind_direction_deg, dtype=float)
        - np.asarray(azimuth_deg, dtype=float)
        + 90.0,
        180.0,
    )
    phi = np.abs(m - 90.0)
    if wind_speed is not None and low_wind_speed > 0.0:
        phi = np.where(
            np.asarray(wind_speed, dtype=float) < low_wind_speed, low_wind_angle, phi
        )
    return np.asarray(phi, dtype=float)


def air_properties(
    film_temperature: ArrayLike, elevation: ArrayLike = 0.0
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Air viscosity [kg/(m s)], density [kg/m3] and conductivity [W/(m K)]."""
    tf = np.asarray(film_temperature, dtype=float)
    he = np.asarray(elevation, dtype=float)
    mu = 1.458e-6 * (tf + 273.0) ** 1.5 / (tf + 383.4)
    rho = (1.293 - 1.525e-4 * he + 6.379e-9 * he**2) / (1.0 + 0.00367 * tf)
    k = 2.424e-2 + 7.477e-5 * tf - 4.407e-9 * tf**2
    return mu, rho, k


@dataclass(frozen=True)
class HeatBalance:
    """Terms of the heat balance (arrays broadcast from the inputs) [W/m]."""

    qc: FloatArray
    qr: FloatArray
    qs: FloatArray
    resistance: FloatArray
    ampacity: FloatArray


def heat_balance(
    conductor: Conductor,
    conductor_temperature: float,
    ambient_temperature: ArrayLike,
    wind_speed: ArrayLike,
    attack_angle_deg: ArrayLike = 90.0,
    solar_irradiance: ArrayLike = 0.0,
    elevation: ArrayLike = 0.0,
    solar_mode: str = "ghi",
) -> HeatBalance:
    """IEEE 738 heat balance; ``ampacity`` is per sub-conductor [A]."""
    if solar_mode not in SOLAR_MODES:
        raise ValueError(f"solar_mode must be one of {SOLAR_MODES}")
    ts = float(conductor_temperature)
    ta = np.asarray(ambient_temperature, dtype=float)
    d = conductor.diameter
    dt = ts - ta
    mu, rho, kf = air_properties((ts + ta) / 2.0, elevation)
    vw = np.maximum(np.asarray(wind_speed, dtype=float), 0.0)
    re = d * rho * vw / mu
    kang = wind_angle_factor(attack_angle_deg)
    qc1 = kang * (1.01 + 1.35 * re**0.52) * kf * dt
    qc2 = kang * 0.754 * re**0.6 * kf * dt
    qcn = np.where(
        dt > 0.0, 3.645 * np.sqrt(rho) * d**0.75 * np.maximum(dt, 0.0) ** 1.25, 0.0
    )
    qc = np.maximum(np.maximum(qc1, qc2), qcn)
    qr = (
        17.8
        * d
        * conductor.emissivity
        * (((ts + 273.0) / 100.0) ** 4 - ((ta + 273.0) / 100.0) ** 4)
    )
    if solar_mode == "ghi":
        qs = (
            conductor.absorptivity
            * np.maximum(np.asarray(solar_irradiance, dtype=float), 0.0)
            * d
        )
    else:
        qs = np.zeros_like(qc)
    r = conductor.resistance(ts)
    net = qc + qr - qs
    amp = np.where((net > 0.0) & (r > 0.0), np.sqrt(np.maximum(net, 0.0) / r), 0.0)
    return HeatBalance(
        qc=np.asarray(qc, dtype=float),
        qr=np.asarray(qr, dtype=float),
        qs=np.asarray(qs, dtype=float),
        resistance=np.asarray(r, dtype=float),
        ampacity=np.asarray(amp, dtype=float),
    )


def ampacity(
    conductor: Conductor,
    conductor_temperature: float,
    ambient_temperature: ArrayLike,
    wind_speed: ArrayLike,
    wind_direction: ArrayLike | None = None,
    azimuth: ArrayLike = 0.0,
    solar_irradiance: ArrayLike = 0.0,
    elevation: ArrayLike = 0.0,
    *,
    solar_mode: str = "ghi",
    low_wind_speed: float = LOW_WIND_SPEED,
    low_wind_angle: float = LOW_WIND_ANGLE,
) -> FloatArray:
    """Per-sub-conductor ampacity [A] from weather (wind perpendicular if
    ``wind_direction`` is None)."""
    if wind_direction is None:
        phi: ArrayLike = 90.0
    else:
        phi = attack_angle(
            wind_direction, azimuth, wind_speed, low_wind_speed, low_wind_angle
        )
    return heat_balance(
        conductor,
        conductor_temperature,
        ambient_temperature,
        wind_speed,
        phi,
        solar_irradiance,
        elevation,
        solar_mode,
    ).ampacity


def ampacity_to_mw(
    amps: ArrayLike,
    voltage_kv: float,
    *,
    v_pu: float = 1.0,
    power_factor: float = 1.0,
    subconductors: int = 1,
    safety_factor: float = 1.0,
) -> FloatArray:
    """Three-phase MW rating, as gtopt's ``ampacity_to_mw``."""
    return (
        safety_factor
        * math.sqrt(3.0)
        * voltage_kv
        * v_pu
        * power_factor
        * np.asarray(amps, dtype=float)
        * subconductors
        / 1000.0
    )
