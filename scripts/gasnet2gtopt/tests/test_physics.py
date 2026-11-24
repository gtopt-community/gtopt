# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Physics helpers (mirror of include/gtopt/gas_physics.hpp)."""

from __future__ import annotations

import math

import pytest

from gasnet2gtopt import physics as ph


def test_weymouth_k_scaling() -> None:
    k = ph.weymouth_k(80, 406)
    assert k > 0
    assert ph.weymouth_k(160, 406) == pytest.approx(2 * k)
    assert ph.weymouth_k(80, 812) == pytest.approx(k * 2 ** (-2 * 2.667))
    assert ph.weymouth_k(80, 406, efficiency=0.46) == pytest.approx(4 * k)


def test_linepack_coeff_is_volume_per_bar() -> None:
    vol = math.pi * 0.406**2 / 4 * 80e3
    assert ph.linepack_coeff(80, 406) == pytest.approx(vol / (0.9 * 1.01325) / 1e3)


@pytest.mark.parametrize("nseg", [2, 4, 8, 16])
def test_uniform_tangents_error_bound(nseg: int) -> None:
    kw, fmax = ph.weymouth_k(80, 406), 300.0
    pts = ph.tangent_points(kw, fmax, nseg)
    gaps = []
    for i in range(1001):
        f = fmax * i / 1000
        env = max(kw * (2 * t * f - t * t) for t in pts)
        gaps.append(kw * f * f - env)
    assert min(gaps) >= -1e-9
    assert max(gaps) == pytest.approx(kw * (fmax / (2 * nseg)) ** 2, rel=1e-6)


def test_relative_pressure_tangents() -> None:
    kw = ph.weymouth_k(80, 406)
    pts = ph.tangent_points(kw, 300.0, 8, "relative_pressure", 70.0)
    steps = [b - a for a, b in zip(pts, pts[1:])]
    assert all(s > 0 for s in steps)
    assert steps[-1] < steps[0]
    with pytest.raises(ValueError, match="inlet pressure"):
        ph.tangent_points(kw, 300.0, 8, "relative_pressure")
    with pytest.raises(ValueError, match="unknown tangent_layout"):
        ph.tangent_points(kw, 300.0, 8, "bogus")


def test_adiabatic_gamma_and_fuel_fraction() -> None:
    gamma = ph.adiabatic_gamma()
    assert gamma == pytest.approx(0.04333528512949229, rel=1e-12)
    for r in (1.1, 1.4, 2.0):
        assert ph.compressor_fuel_fraction(r) == pytest.approx(
            gamma * (r**ph.ALPHA_NG - 1)
        )


def test_saldarriaga_plane_matches_partials() -> None:
    gamma, a = ph.adiabatic_gamma(), ph.ALPHA_NG
    psi0, beta0, p_in0 = 170.0, 1.2, 58.0
    c_psi, c_pi, r0 = ph.saldarriaga_coeffs(gamma, a, psi0, beta0, p_in0)

    def lc(psi: float, pin: float, pout: float) -> float:
        return gamma * psi * ((pout / pin) ** (a / 2) - 1)

    pin0 = p_in0**2
    pout0 = r0 * pin0
    h = 1e-4
    assert c_psi == pytest.approx(
        (lc(psi0 + h, pin0, pout0) - lc(psi0 - h, pin0, pout0)) / (2 * h), rel=1e-6
    )
    assert c_pi == pytest.approx(
        (lc(psi0, pin0, pout0 + h) - lc(psi0, pin0, pout0 - h)) / (2 * h), rel=1e-6
    )
    assert c_psi * psi0 == pytest.approx(lc(psi0, pin0, pout0))


def test_electric_kappa() -> None:
    assert ph.gas_lhv_mw_per_km3h() == pytest.approx(9.6, rel=0.01)
    assert ph.electric_kappa() == pytest.approx(ph.gas_lhv_mw_per_km3h() * 0.33 / 0.95)


def test_plant_capability_rows() -> None:
    ((s1, c1),) = ph.plant_capability_rows(500, 30, 0.5)
    assert s1 * 29.5**2 + c1 == pytest.approx(0, abs=1e-9)
    assert s1 * 30**2 + c1 == pytest.approx(500)
    rows = ph.plant_capability_rows(500, 30, 0.5, 34.5)
    assert len(rows) == 2
    (s1, c1), (s2, c2) = rows
    assert s1 * 900 + c1 == pytest.approx(500 * 30 / 34.5)
    assert s2 * 34.5**2 + c2 == pytest.approx(500)
    assert len(ph.plant_capability_rows(500, 30, 0.5, 34.5, 0.9)) == 2
    with pytest.raises(ValueError, match="steeper"):
        ph.plant_capability_rows(500, 30, 5.0, 30.1, 0.01)
