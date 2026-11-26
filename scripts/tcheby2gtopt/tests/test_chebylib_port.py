# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Port of tcheby ``tests/tschedht/ChebyLib_test.cpp`` (9 gtest
cases on ``tschedht::ChebyLib``, Matus et al.) to our Python
Chebyshev operators.

Mapping of the C++ operators (``src/tschedht/ChebyLib.cpp``):

* ``ChebyLib::extremes(n)`` — n Chebyshev–Lobatto points ascending on
  [−1, 1] -> :func:`plexos2gtopt.week_sampler.chebyshev_nodes`
  (``n − 1``, ``"lobatto"``).
* ``vandermonde_extremes_op`` / ``vandermonde_op`` (``T_j(x_i)``) ->
  :func:`plexos2gtopt.week_reconstruct.node_vandermonde`.
* ``int_op(m)`` ((m+1)×m, antiderivative vanishing at x = −1) and
  ``diff_op(m)`` ((m−1)×m) -> ``numpy.polynomial.chebyshev.chebint``
  (``lbnd=−1``) / ``chebder``, the operators
  :mod:`plexos2gtopt.week_reconstruct` uses (``rebuild_energy``,
  :func:`cumulative_integration_matrix`); ``int_op(m, xb, xe)`` /
  ``diff_op(m, xb, xe)`` scale by ``(xe−xb)/2`` / ``2/(xe−xb)``
  (``scl``).
* ``diffd_op(x)`` (3-point non-uniform finite difference, one-sided at
  the ends) and ``diff2_op`` (its coefficient-space form ``V⁻¹ D V``)
  have no Python counterpart (gtopt's ``central2`` ramp is C++), so the
  minimal numpy reference :func:`diffd_op` below ports the C++.

The C++ tests compare some vectors with exact ``EXPECT_EQ``; here those
use a 1e-13 tolerance (LU solve vs. closed forms).  Each test names its
C++ origin.
"""

from __future__ import annotations

import numpy as np
import pytest
from numpy.polynomial import chebyshev as cheb

from plexos2gtopt.week_reconstruct import (
    NodeGrid,
    cumulative_integration_matrix,
    node_vandermonde,
)
from plexos2gtopt.week_sampler import chebyshev_nodes, interpolatory_weights


def extremes(n: int) -> np.ndarray:
    """``ChebyLib::extremes(n)``: n Lobatto points (n ≥ 2), ascending."""
    if n == 0:
        return np.zeros(0)
    if n == 1:
        return np.array([-1.0])
    return chebyshev_nodes(n - 1, "lobatto")


def vandermonde(n: int) -> np.ndarray:
    """``vandermonde_extremes_op(n, n)`` via our ``node_vandermonde``."""
    x = extremes(n)
    grid = NodeGrid(times=x + 1.0, durations=interpolatory_weights(x), window=2.0)
    return node_vandermonde(grid)


def int_op(m: int, xb: float = -1.0, xe: float = 1.0) -> np.ndarray:
    """``int_op(m[, xb, xe])`` column by column with ``chebint``."""
    eye = np.eye(m)
    return np.column_stack(
        [cheb.chebint(eye[j], lbnd=-1.0, scl=(xe - xb) / 2.0) for j in range(m)]
    )


def diff_op(m: int, xb: float = -1.0, xe: float = 1.0) -> np.ndarray:
    """``diff_op(m[, xb, xe])`` column by column with ``chebder``."""
    eye = np.eye(m)
    return np.column_stack(
        [cheb.chebder(eye[j], scl=2.0 / (xe - xb)) for j in range(m)]
    )


def diffd_op(x: np.ndarray) -> np.ndarray:
    """Port of ``ChebyLib::diffd_op``: 3-point non-uniform derivative."""
    m = len(x)
    d = np.zeros((m, m))
    if m == 1:
        return d
    for i in range(1, m - 1):
        h1 = x[i + 1] - x[i]
        h2 = x[i] - x[i - 1]
        d[i, i - 1] = -h1 / (h2 * (h1 + h2))
        d[i, i] = (h1 - h2) / (h1 * h2)
        d[i, i + 1] = h2 / (h1 * (h1 + h2))
    h1 = x[1] - x[0]
    d[0, 0], d[0, 1] = -1.0 / h1, 1.0 / h1
    h2 = x[m - 1] - x[m - 2]
    d[m - 1, m - 2], d[m - 1, m - 1] = -1.0 / h2, 1.0 / h2
    return d


# ---------------------------------------------------------------------------


def test_extremes():
    """ChebyLib_test.cpp: TEST(ChebyLib, extremes)."""
    assert extremes(0).size == 0
    x = extremes(2)
    assert x.size == 2 and x[0] == -1.0 and x[1] == 1.0
    x = extremes(3)
    assert x.size == 3 and x[0] == -1.0 and abs(x[1]) <= 1.0e-15 and x[2] == 1.0


def test_vandermonde_extremes():
    """ChebyLib_test.cpp: TEST(ChebyLib, vandermonde_extremes) — n = 168,
    A c = 2 has c = 2·e_0 and A c reproduces y."""
    n = 168
    a = vandermonde(n)
    y = np.full(n, 2.0)
    c = np.linalg.solve(a, y)
    cr = np.zeros(n)
    cr[0] = 2.0
    np.testing.assert_allclose(c, cr, atol=1e-13)
    np.testing.assert_allclose(a @ c, y, atol=1e-13)


def test_vandermonde_extremes_2():
    """ChebyLib_test.cpp: TEST(ChebyLib, vandermonde_extremes_2) — the
    angle form equals ``cos(j·acos x_i)`` within 1e-12 (n = 33)."""
    n = 33
    x = extremes(n)
    ae = np.cos(np.outer(np.arccos(np.clip(x, -1, 1)), np.arange(n)))
    ii = (n - 1) - np.arange(n)
    angle = np.cos(np.pi * np.outer(ii, np.arange(n)) / (n - 1))
    assert np.max(np.abs(vandermonde(n) - ae)) < 1.0e-12
    assert np.max(np.abs(angle - ae)) < 1.0e-12


def test_int_diff_op():
    """ChebyLib_test.cpp: TEST(ChebyLib, int_diff_op) — D(m+1)·I(m) = 1
    on every unit coefficient vector (m = 168).  The C++ bound is 1e-15;
    numpy's chebint/chebder round-trip reaches 1.5e-15, so 1e-14 here."""
    m = 168
    i_op = int_op(m)
    d_op = diff_op(m + 1)
    assert i_op.shape == (m + 1, m) and d_op.shape == (m, m + 1)
    assert np.max(np.abs(d_op @ i_op - np.eye(m))) < 1.0e-14


def test_int_op():
    """ChebyLib_test.cpp: TEST(ChebyLib, int_op) — ∫_{−1}^x T_0 = T_0 + T_1."""
    m = 8
    c = np.zeros(m)
    c[0] = 1.0
    ce = np.zeros(m + 1)
    ce[0] = ce[1] = 1.0
    np.testing.assert_array_equal(int_op(m) @ c, ce)


def test_diff_op():
    """ChebyLib_test.cpp: TEST(ChebyLib, diff_op) — T_1' = T_0."""
    m = 8
    c = np.zeros(m)
    c[1] = 1.0
    ce = np.zeros(m - 1)
    ce[0] = 1.0
    np.testing.assert_array_equal(diff_op(m) @ c, ce)


def test_diff2_op():
    """ChebyLib_test.cpp: TEST(ChebyLib, diff2_op) — the 3-point
    non-uniform derivative of a constant vanishes on 8 Lobatto nodes."""
    m = 8
    x = extremes(m)
    d = diffd_op(x)
    assert np.max(np.abs(d @ np.ones(m))) < 1e-15
    # (extra) exact on linear functions
    np.testing.assert_allclose(d @ (3.0 * x + 1.0), np.full(m, 3.0), atol=1e-13)


def test_diff2_op3():
    """ChebyLib_test.cpp: TEST(ChebyLib, diff2_op3) — on [0, 100]:
    I(m−1)·D(m)·c = c for c = ones(8) (p(−1) = Σ(−1)^j c_j = 0)."""
    m = 8
    d = diff_op(m, 0.0, 100.0)
    i_op = int_op(m - 1, 0.0, 100.0)
    c = np.ones(m)
    assert np.max(np.abs(i_op @ (d @ c) - c)) < 1e-12


def test_diff2_op2():
    """ChebyLib_test.cpp: TEST(ChebyLib, diff2_op2) — the coefficient
    form ``V⁻¹ D_fd V`` of the 3-point rule maps T_1 to T_0 (the rule is
    exact on linear functions), |error| < 1e-15 in the C++; 1e-14 here
    (LU solve)."""
    m = 8
    x = extremes(m)
    v = vandermonde(m)
    d2 = np.linalg.solve(v, diffd_op(x) @ v)
    c = np.zeros(m)
    c[1] = 1.0
    ce = np.zeros(m)
    ce[0] = 1.0
    assert np.max(np.abs(d2 @ c - ce)) < 1e-14


def test_value_space_integration_matches_int_op():
    """(extra) ``cumulative_integration_matrix`` (value space, ours) is
    ``V_{m+1} · int_op(m) · V⁻¹`` on m Lobatto nodes of [0, T]."""
    m, period = 9, 24.0
    x = extremes(m)
    grid = NodeGrid(
        times=(x + 1) * period / 2, durations=interpolatory_weights(x), window=period
    )
    s = cumulative_integration_matrix(grid)
    v = node_vandermonde(grid)
    v1 = cheb.chebvander(x, m)  # m+1 coefficients
    ref = v1 @ int_op(m, 0.0, period) @ np.linalg.inv(v)
    np.testing.assert_allclose(s, ref, atol=1e-10)


# --- quadrature: ChebyLib::clenshaw_quad / csq_average / int_trapezoide -----


def clenshaw_quad(size: int) -> np.ndarray:
    """``ChebyLib::clenshaw_quad(size)``: coefficient-space weights q with
    ``q·c`` = the MEAN of ``Σ c_k T_k`` over [−1, 1] (q_0 = 1/2,
    q_k = −1/((k+1)(k−1)) for even k)."""
    q = np.zeros(size)
    q[0] = 0.5
    for i in range(2, size, 2):
        q[i] = -1.0 / ((i + 1) * (i - 1))
    return q


def r_factor(x: np.ndarray, r: float) -> np.ndarray:
    """``ChebyLib::r_factor``: linear intra-period discount 1 − r(1+ξ)/2."""
    xi = 2.0 * (x - x[0]) / (x[-1] - x[0]) - 1.0
    return 1.0 - 0.5 * r * (1.0 + xi)


def test_clenshaw_quad_is_our_node_weights():
    """Our Clenshaw–Curtis node weights equal the paper library's
    coefficient-space rule mapped to the nodes, w = T · V⁻ᵀ q, once q_0 is
    the true mean of T_0 (= 1).  ``ChebyLib::clenshaw_quad`` /
    ``csq_average`` use q_0 = 1/2 with the full-mean q_k (k ≥ 2) and an
    un-halved T_0 column, and the scaled ``clenshaw_quad(m, xb, xe)``
    multiplies by 2·(xe − xb): constants integrate exactly but every
    T_k (k ≥ 2) term is doubled.  The library computes
    ``clenshaw_values`` from it but its LP models do not use them (they
    use the trapezoid / ``quadrature`` weights), so we keep the correct
    rule and pin the discrepancy here."""
    for n in (8, 24, 48, 84):
        x = extremes(n + 1)
        v = cheb.chebvander(x, n)  # V[i, k] = T_k(x_i), paper's column 0 = 1
        q_paper = clenshaw_quad(n + 1)
        q_mean = q_paper.copy()
        q_mean[0] = 1.0  # mean of T_0 over [−1, 1]
        w_ours = 84.0 * interpolatory_weights(x)
        np.testing.assert_allclose(
            w_ours, 168.0 * np.linalg.solve(v.T, q_mean), rtol=1e-10, atol=1e-10
        )
        c = np.random.default_rng(n).normal(size=n + 1)
        mean = cheb.chebval(1.0, cheb.chebint(c, lbnd=-1.0)) / 2.0
        assert q_mean @ c == pytest.approx(mean, rel=1e-12, abs=1e-12)
        # the paper's scaled rule: exact on constants, 2× on T_k, k ≥ 2
        w_paper = np.linalg.solve(v.T, 2.0 * 168.0 * q_paper)
        assert w_paper.sum() == pytest.approx(168.0)
        t2 = cheb.chebval(x, [0, 0, 1])
        assert w_paper @ t2 == pytest.approx(2.0 * (w_ours @ t2))


def test_r_factor_discount_weights():
    """``int_trapezoide(x, r)`` = trapezoid weights × ``r_factor``: a
    linear discount over the period (1 at the start, 1 − r at the end).
    gtopt discounts per stage instead (``Stage.discount_factor``), so it
    is not ported to the LP; kept here as the documented reference."""
    x = extremes(9)
    rf = r_factor(x, 0.1)
    assert rf[0] == pytest.approx(1.0) and rf[-1] == pytest.approx(0.9)
    assert np.all(np.diff(rf) < 0)
