# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Steady-state natural-gas physics shared by both emitters.

Mirrors ``include/gtopt/gas_physics.hpp`` (the C++ ``gtopt::gas``
namespace) so the native elements and the generic prototype output are
the same LP.  Units: flow km3/h (standard), pressure bar abs,
``pi = p**2`` bar^2, line pack km3, gas cost $/km3.
"""

from __future__ import annotations

import math

T_BASE = 288.15  # K, standard temperature
P_BASE_KPA = 101.325  # kPa, standard pressure
P_BASE_BAR = 1.01325  # bar, standard pressure
R_GAS = 8.314  # J/(mol K)
ALPHA_NG = (1.3 - 1.0) / 1.3  # (kappa-1)/kappa for natural gas, ~0.23
LHV_KG = 47.0e6  # J/kg
G_ACC = 9.80665  # m/s^2
M_AIR = 0.028964  # kg/mol, molar mass of air (elevation exponent)


def elevation_exponent(
    dh_m: float, gravity: float = 0.6, z: float = 0.9, t_flow: float = T_BASE
) -> float:
    """s = 2 g M (h_b - h_a) / (Z R T): pi_a - e^s pi_b = K_e f|f|."""
    return 2.0 * G_ACC * gravity * M_AIR * dh_m / (z * R_GAS * t_flow)


def elevation_k_factor(s: float) -> float:
    """(e^s - 1)/s, -> 1 as s -> 0 (series below 1e-4)."""
    if abs(s) < 1e-4:
        return 1.0 + s / 2.0 + s * s / 6.0
    return math.expm1(s) / s


def weymouth_k(
    length_km: float,
    diameter_mm: float,
    efficiency: float = 0.92,
    gravity: float = 0.6,
    t_flow: float = T_BASE,
    z: float = 0.9,
) -> float:
    """Weymouth constant K [bar^2 / (km3/h)^2]: p_a^2 - p_b^2 = K q|q|.

    SI Weymouth (Menon, Gas Pipeline Hydraulics, 2005, eq. 2.62):
        Q = 3.7435e-3 E (Tb/Pb) [(P1^2-P2^2)/(G Tf L Z)]^0.5 D^2.667
    Q [m3/d std], P [kPa abs], L [km], D [mm].  Converted to bar and km3/h:
    P_kPa^2 = 1e4 P_bar^2, Q_m3d = 24000 Q_km3h.
    """
    c = 3.7435e-3 * efficiency * (T_BASE / P_BASE_KPA) * diameter_mm**2.667
    k_kpa = gravity * t_flow * length_km * z / c**2
    return k_kpa * 24000.0**2 / 1e4


def linepack_coeff(
    length_km: float, diameter_mm: float, z: float = 0.9, t_flow: float = T_BASE
) -> float:
    """Line pack per bar of average pressure [km3 std / bar]."""
    vol_m3 = math.pi * (diameter_mm / 1000.0) ** 2 / 4.0 * length_km * 1000.0
    return vol_m3 / (z * P_BASE_BAR) * (T_BASE / t_flow) / 1000.0


def compressor_fuel_fraction(
    ratio: float, eff: float = 0.80, driver_eff: float = 0.33
) -> float:
    """Fuel burnt per unit throughput at pressure ratio ``ratio``.

    Adiabatic head H = Z R T / M * k/(k-1) * (r^((k-1)/k) - 1) / eta_c
    [J/kg] with k = 1.3, G = 0.6, Z = 0.9, T = 288.15 K;
    fuel [kg/kg] = H / (eta_driver * LHV).
    """
    molar_mass = 0.02896 * 0.6  # kg/mol
    head = 0.9 * R_GAS * T_BASE / molar_mass / ALPHA_NG
    head *= (ratio**ALPHA_NG - 1.0) / eff
    return head / (driver_eff * LHV_KG)


def adiabatic_gamma(
    eff: float = 0.80, driver_eff: float = 0.33, alpha: float = ALPHA_NG
) -> float:
    """gamma of LC = gamma (beta^alpha - 1) Psi from the adiabatic head.

    ``compressor_fuel_fraction(r) == gamma * (r**alpha - 1)`` exactly for
    the default alpha (~0.043 with the default efficiencies).
    """
    r = 2.0
    return compressor_fuel_fraction(r, eff, driver_eff) / (r**alpha - 1.0)


def gas_lhv_mw_per_km3h(gravity: float = 0.6) -> float:
    """Thermal power of 1 km3/h (std) of gas at LHV [MW] (~9.6)."""
    rho_std = P_BASE_KPA * 1e3 / (R_GAS * T_BASE) * 0.02896 * gravity
    return LHV_KG * rho_std * 1000.0 / 3600.0 / 1e6


def electric_kappa(driver_eff: float = 0.33, motor_eff: float = 0.95) -> float:
    """MW of motor input per km3/h of Saldarriaga fuel equivalent (~3.3)."""
    return gas_lhv_mw_per_km3h() * driver_eff / motor_eff


def saldarriaga_coeffs(
    gamma: float, alpha: float, psi0: float, beta0: float, p_in0: float
) -> tuple[float, float, float]:
    """First-order Taylor plane of Saldarriaga's fuel in pi = p^2.

    ``LC = gamma ((pi_out/pi_in)^a - 1) Psi`` with ``a = alpha/2`` is
    homogeneous of degree 1 in Psi and 0 in pi, so the plane at
    (Psi0, pi_in0, r0 pi_in0) has no constant term:
    ``LC ~ c_psi Psi + c_pi (pi_out - r0 pi_in)``.  Returns
    ``(c_psi, c_pi, r0)``.
    """
    a = alpha / 2.0
    r0 = beta0**2
    pi_in0 = p_in0**2
    c_psi = gamma * (r0**a - 1.0)
    c_pi = gamma * a * psi0 * r0 ** (a - 1.0) / pi_in0
    return c_psi, c_pi, r0


def tangent_points(
    kw: float,
    fmax: float,
    nseg: int,
    layout: str = "uniform",
    p_in: float | None = None,
) -> list[float]:
    """Tangent points t_k of the Weymouth outer approximation.

    ``uniform``: midpoints of N equal flow segments (uniform pi error
    K (F/2N)^2).  ``relative_pressure``: breakpoints uniform in
    theta = asin(sqrt(K) f / P), denser near capacity (uniform relative
    outlet-pressure error), tangents at the segment midpoints.
    """
    if layout == "uniform":
        return [fmax * (2 * k - 1) / (2.0 * nseg) for k in range(1, nseg + 1)]
    if layout != "relative_pressure":
        raise ValueError(f"unknown tangent_layout {layout!r}")
    if p_in is None:
        raise ValueError("relative_pressure layout needs the inlet pressure")
    sk = math.sqrt(kw)
    f_eff = min(fmax, 0.999 * p_in / sk)
    theta_f = math.asin(sk * f_eff / p_in)
    b = [p_in / sk * math.sin(theta_f * k / nseg) for k in range(nseg + 1)]
    return [0.5 * (b[k - 1] + b[k]) for k in range(1, nseg + 1)]


def plant_capability_rows(
    pmax: float,
    p_min: float,
    band: float,
    p_nom: float | None = None,
    out_min: float | None = None,
) -> list[tuple[float, float]]:
    """Concave piecewise-linear cap(pi) <= pmax for a gas turbine.

    Returns ``[(slope, intercept), ...]`` with gen <= slope * pi + intercept:
    a hard-stop ramp from 0 at ``(p_min - band)^2`` to ``out_min * pmax``
    at ``p_min^2``, and (``p_nom > p_min``) a derate line up to ``pmax``
    at ``p_nom^2``; ``out_min`` defaults to ``p_min / p_nom``.
    """
    rows = []
    if p_nom is None or p_nom <= p_min:
        out_min = 1.0
    elif out_min is None:
        out_min = p_min / p_nom
    lo2, m2 = (p_min - band) ** 2, p_min**2
    s1 = out_min * pmax / (m2 - lo2)
    rows.append((s1, -s1 * lo2))
    if out_min < 1.0:
        assert p_nom is not None
        n2 = p_nom**2
        s2 = (1.0 - out_min) * pmax / (n2 - m2)
        if s2 >= s1:
            raise ValueError("derate line steeper than the hard-stop ramp")
        rows.append((s2, out_min * pmax - s2 * m2))
    return rows
