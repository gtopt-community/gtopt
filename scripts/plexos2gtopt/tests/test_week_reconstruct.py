"""Hourly reconstruction of typical-week node solutions."""

import numpy as np
import pytest
from numpy.polynomial import chebyshev as cheb

from plexos2gtopt.week_reconstruct import (
    NodeGrid,
    gram_matrix,
    node_vandermonde,
    price_coefficients,
    rebuild_price_coef,
    revenue_integral,
    level_check,
    rebuild_energy,
    rebuild_point,
    rebuild_point_linear,
    rebuild_level,
    rebuild_price_adjoint,
    reconstruction_matrix,
    revenue_check,
    to_month,
)
from plexos2gtopt.week_sampler import ChebyshevSampler, UniformSampler


def _cheb_grid(n: int, kind: str = "lobatto") -> NodeGrid:
    g = ChebyshevSampler(n=n, kind=kind).grid()  # type: ignore[arg-type]
    return NodeGrid(g.times, g.durations)


@pytest.mark.parametrize("kind", ["gauss", "lobatto"])
@pytest.mark.parametrize("step", [1.0, 0.5])
def test_energy_conserved_exactly(kind, step):
    grid = _cheb_grid(64, kind)
    rng = np.random.default_rng(0)
    g = 50 + 10 * rng.random(len(grid.times))
    h = rebuild_energy(g, grid, step)
    assert h.shape == (int(168 / step),)
    assert h.sum() * step == pytest.approx(float(g @ grid.durations), rel=1e-11)


def test_polynomial_reproduced():
    grid = _cheb_grid(20)
    c = np.array([3.0, 1.0, -0.5, 0.25])
    x = 2 * grid.times / 168 - 1
    vals = cheb.chebval(x, c)
    mid = np.arange(168) + 0.5
    p = rebuild_point(vals, grid, 1.0)
    assert np.allclose(p, cheb.chebval(2 * mid / 168 - 1, c))


def test_piecewise_grid_and_revenue():
    s = UniformSampler(hours=2.0)
    g = s.grid()
    grid = NodeGrid(g.times, g.durations, polynomial=False)
    gen = np.arange(84, dtype=float)
    price = np.full(84, 30.0)
    h = rebuild_energy(gen, grid, 1.0)
    assert np.allclose(h[0::2], gen) and np.allclose(h[1::2], gen)
    rc = revenue_check(gen, price, grid)
    assert rc.gap == pytest.approx(0.0, abs=1e-9)
    assert to_month(h, 28) == pytest.approx(h.sum() * 4)


def test_revenue_gap_and_level_check_on_chebyshev():
    grid = _cheb_grid(32)
    t = grid.times
    gen = 100 + 50 * np.sin(2 * np.pi * t / 24)
    price = 60 + 30 * np.cos(2 * np.pi * t / 24)
    rc = revenue_check(gen, price, grid)
    assert abs(rc.rel_gap) < 0.05
    # a level exactly at the bounds at the nodes overshoots between them
    lv = 50 + 50 * np.sin(2 * np.pi * t / 24)
    lc = level_check(np.clip(lv, 0, 100), grid, 0.0, 100.0)
    assert lc.violations >= 0
    flat = level_check(np.full(len(t), 40.0), grid, 0.0, 100.0)
    assert flat.violations == 0 and flat.max_above == 0.0


@pytest.mark.parametrize("kind", ["gauss", "lobatto"])
@pytest.mark.parametrize("smooth", [0.0, 10.0])
def test_adjoint_prices_preserve_revenue_exactly(kind, smooth):
    grid = _cheb_grid(48, kind)
    t = grid.times
    rng = np.random.default_rng(4)
    price = 60 + 30 * np.cos(2 * np.pi * t / 24) + rng.normal(size=len(t))
    p = rebuild_price_adjoint(price, grid, 0.5, smooth)
    r = reconstruction_matrix(grid, 0.5)
    # energy conservation of R: 1ᵀ Δh R = wᵀ
    assert np.allclose(r.sum(axis=0) * 0.5, grid.durations)
    for _ in range(3):  # any node quantity
        u = rng.random(len(t)) * 100
        g = r @ u
        assert np.sum(p * g) * 0.5 == pytest.approx(
            float(np.sum(price * u * grid.durations)), rel=1e-10
        )
    rc = revenue_check(100 + 0 * t, price, grid, 0.5, "adjoint", smooth)
    assert abs(rc.rel_gap) < 1e-10
    naive = revenue_check(
        50 + 40 * np.sin(2 * np.pi * t / 24), price, grid, 0.5, "interp"
    )
    assert abs(naive.gap) > 0.0


def test_adjoint_total_cost_identity_small_lp():
    # 3-node Lobatto week: one generator meets the load, price = its cost;
    # total cost Σ_k c·g_k·w_k = Σ_h p_h·g_h·Δh with adjoint prices
    grid = _cheb_grid(2)
    g = np.array([10.0, 30.0, 20.0])
    cost = np.array([5.0, 7.0, 6.0])
    p = rebuild_price_adjoint(cost, grid, 1.0)
    h = rebuild_energy(g, grid, 1.0, interp="poly")
    assert np.sum(p * h) == pytest.approx(float(np.sum(cost * g * grid.durations)))
    with pytest.raises(ValueError):
        rebuild_price_adjoint(np.ones(337), _cheb_grid(336), 1.0)


def test_gram_matrix_closed_form():
    g = gram_matrix(3, 2.0)  # window 2 ⇒ dt = dx
    # ∫T0² = 2, ∫T1² = 2/3, ∫T0T2 = −2/3, ∫T2² = 14/15, ∫T1T3 = −2/5
    assert g[0, 0] == pytest.approx(2.0)
    assert g[1, 1] == pytest.approx(2 / 3)
    assert g[0, 2] == pytest.approx(-2 / 3)
    assert g[2, 2] == pytest.approx(14 / 15)
    assert g[1, 3] == pytest.approx(-2 / 5)
    assert g[0, 1] == 0.0


@pytest.mark.parametrize("kind", ["gauss", "lobatto"])
@pytest.mark.parametrize("n", [16, 64, 168, 336])
def test_coefficient_adjoint_identity(kind, n):
    grid = _cheb_grid(n, kind)
    rng = np.random.default_rng(n)
    price = 60 + 20 * rng.random(len(grid.times))
    a = price_coefficients(price, grid)
    gm = gram_matrix(len(a) - 1, grid.window)
    v = node_vandermonde(grid)
    lam = price * grid.durations
    # LP quadrature integrates the node interpolant exactly: wᵀu = ∫g
    for _ in range(3):
        u = rng.random(len(grid.times))
        c = np.linalg.solve(v, u)
        assert grid.durations @ u == pytest.approx(gm[0] @ c, rel=1e-10)
        assert a @ gm @ c == pytest.approx(lam @ u, rel=1e-9)
    u = rng.random(len(grid.times))
    assert revenue_integral(u, price, grid) == pytest.approx(lam @ u, rel=1e-9)


def test_coefficient_adjoint_hand_and_constant():
    # Lobatto 3 nodes, window 2: w = 1/3, 4/3, 1/3; constant price → constant
    grid = NodeGrid(np.array([0.0, 1.0, 2.0]), np.array([1 / 3, 4 / 3, 1 / 3]), 2.0)
    a = price_coefficients(np.full(3, 7.0), grid)
    assert np.allclose(a, [7.0, 0.0, 0.0])
    # price only at the middle node: λ = (0, 4/3, 0); ∫p·g = λᵀu for u = T2
    a = price_coefficients(np.array([0.0, 1.0, 0.0]), grid)
    u = np.array([1.0, -1.0, 1.0])  # T2 at −1, 0, 1
    assert a @ gram_matrix(2, 2.0) @ np.array([0, 0, 1.0]) == pytest.approx(-4 / 3)
    assert revenue_integral(u, np.array([0.0, 1.0, 0.0]), grid) == pytest.approx(-4 / 3)


def test_coefficient_vs_grid_adjoint_on_fine_grid():
    grid = _cheb_grid(32)
    t = grid.times
    price = 60 + 30 * np.cos(2 * np.pi * t / 24)
    gen = 100 + 50 * np.sin(2 * np.pi * t / 24)
    exact = float(np.sum(price * gen * grid.durations))
    for step in (1.0, 0.5, 0.125):
        rc = revenue_check(gen, price, grid, step, "coef")
        assert abs(rc.rel_gap) < (1e-3 if step < 1 else 1e-2)
    pc = rebuild_price_coef(price, grid, 0.125)
    pa = rebuild_price_adjoint(price, grid, 0.125)
    assert np.sqrt(np.mean((pc - pa) ** 2)) < 0.05 * np.abs(pc).max()
    assert revenue_check(gen, price, grid, 0.125, "adjoint").gap == pytest.approx(
        0.0, abs=1e-8 * exact
    )


def test_masked_solar_reconstruction_and_duality():
    from plexos2gtopt.week_reconstruct import (  # pylint: disable=import-outside-toplevel
        masked_reconstruction_matrix,
    )
    from plexos2gtopt.week_sampler import (  # pylint: disable=import-outside-toplevel
        night_nodes,
    )

    t = (np.arange(336) + 0.5) * 0.5
    y = np.clip(np.sin(2 * np.pi * (t - 6) / 24), 0, None)
    s = ChebyshevSampler(n=64)
    g = s.grid()
    u = s.sample(y, 0.5, g)
    grid = NodeGrid(g.times, g.durations)
    night_cells = y == 0.0
    rm, unscaled = masked_reconstruction_matrix(grid, night_cells, 0.5)
    h = rm @ u
    assert np.all(h[night_cells] == 0.0)
    day = ~night_nodes(y, 0.5, g.durations, 168.0)
    ok = np.isclose(rm.sum(axis=0) * 0.5, g.durations) | ~day
    assert np.mean(ok) > 0.9 and unscaled < len(u)
    assert h.sum() * 0.5 == pytest.approx(float(u @ g.durations), rel=0.02)
    # prices from the masked operator's adjoint keep the identity exactly
    price = 60 + 30 * np.cos(2 * np.pi * g.times / 24)
    p = rebuild_price_adjoint(price, grid, 0.5, operator=rm, active=day)
    assert np.sum(p * h) * 0.5 == pytest.approx(float(np.sum(price * u * g.durations)))


def _paper_cheby2hour(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Port of the user's paper code ``scripts/cheby2hour.py`` (hourly
    means of the node interpolant: E = Z·(b−a)/2·B·I·A⁻¹)."""
    a, b = x.min(), x.max()
    t = 2.0 / (b - a) * (x - a) - 1
    k = int(b - a) + 1
    xh = np.arange(k) + a
    th = 2.0 / (b - a) * (xh - a) - 1
    m = t.size
    amat = np.cos(np.outer(np.arccos(np.clip(t, -1, 1)), np.arange(m)))
    bmat = np.cos(np.outer(np.arccos(np.clip(th, -1, 1)), np.arange(m + 1)))
    imat = np.zeros((m + 1, m))
    for i in range(4, m + 1, 2):
        c = 1.0 / (2 * i) + 1.0 / (2 * (i - 2))
        imat[0, i - 1] = -c if (i // 2 + 1) % 2 == 1 else c
    for i in range(2, m):
        imat[i - 1, i] = -1.0 / (2 * (i - 1))
    for i in range(2, m + 2):
        imat[i - 1, i - 2] = 1.0 / (2 * (i - 1))
    imat[1, 0] = 1.0
    if m > 1:
        imat[0, 1] = 0.25
        imat[2, 1] = 0.25
    for j in range(m):
        imat[0, j] = -sum(-imat[i, j] if i % 2 else imat[i, j] for i in range(1, m + 1))
    dmat = 0.5 * (b - a) * bmat @ imat @ np.linalg.inv(amat)
    zmat = np.diff(np.eye(k), axis=0)
    return xh[:-1], zmat @ dmat


def test_energy_reconstruction_equals_paper_cheby2hour():
    grid = _cheb_grid(24)
    _, e = _paper_cheby2hour(grid.times)
    r = reconstruction_matrix(grid, 1.0)
    assert np.allclose(e, r, atol=1e-10)
    lin = rebuild_point_linear(np.arange(len(grid.times), dtype=float), grid, 1.0)
    assert lin.shape == (168,) and np.all(np.diff(lin) >= 0)


@pytest.mark.parametrize("n", [32, 64, 168])
def test_storage_recursion_cc_exact_weekly_first_order_intermediate(n):
    from plexos2gtopt.week_reconstruct import (  # pylint: disable=import-outside-toplevel
        block_ends,
        cumulative_integration_matrix,
    )

    grid = _cheb_grid(n)
    t = grid.times
    # every node lies inside its block [τ_{k−1}, τ_k]
    tau = block_ends(grid)
    lo = np.concatenate(([0.0], tau[:-1]))
    assert np.all((t >= lo - 1e-12) & (t <= tau + 1e-12))
    # a flow that is a polynomial of the node degree
    rng = np.random.default_rng(n)
    c = rng.normal(size=8)
    f = cheb.chebval(2 * t / 168 - 1, c)
    soc = np.cumsum(f * grid.durations)  # the LP's sparse recursion
    prim = cheb.chebint(c, lbnd=-1.0)

    def exact(tt):
        return cheb.chebval(2 * np.asarray(tt) / 168 - 1, prim) * 84.0

    # weekly total: exact Clenshaw–Curtis integral
    assert soc[-1] == pytest.approx(exact(168.0), abs=1e-9 * np.abs(f).max() * 168)
    # intermediate levels ≈ the integral up to the block END τ_k, not t_k
    thr = np.abs(f).max() * 168
    err_tau = np.abs(soc - exact(tau)).max() / thr
    err_t = np.abs(soc - exact(t)).max() / thr
    assert err_tau < err_t and err_tau < 1e-2
    # the dense S = V J V⁻¹ is exact at the nodes
    s = cumulative_integration_matrix(grid)
    assert np.allclose(s @ f, exact(t), atol=1e-9 * thr)
    # levels rebuilt at block ends, piecewise linear
    lv = rebuild_level(soc, grid, 0.5, initial=0.0)
    assert lv.shape == (336,)


# --- point semantics: end instant, periodic subspace, hourly averages -------


def test_cell_price_duality_and_no_negative_prices():
    """Default price reconstruction: π_k = λ_k / w_k constant on the
    quadrature cell [τ_{k−1}, τ_k].  For a cell-constant quantity the
    continuous identity ∫π·g dt = λᵀu holds exactly (checked on a fine
    grid); a scarcity spike gives no negative hourly price, unlike the
    coefficient-space adjoint."""
    from plexos2gtopt.week_reconstruct import (  # pylint: disable=import-outside-toplevel
        cell_edges,
        rebuild_price,
    )

    grid = _cheb_grid(32)
    w = grid.durations
    rng = np.random.default_rng(7)
    price = 40.0 + 5.0 * rng.random(len(w))
    price[len(w) // 2] = 15000.0  # scarcity spike
    lam = price * w
    edges = cell_edges(grid)
    assert edges[-1] == pytest.approx(grid.window)
    step = 168.0 / 2**16
    ph = rebuild_price(price, grid, step)
    assert ph.min() >= 0.0
    for _ in range(3):
        u = rng.random(len(w)) * 100.0
        gh = rebuild_energy(u, grid, step)  # cell-constant (default)
        assert np.sum(ph * gh) * step == pytest.approx(lam @ u, rel=2e-4)
        assert np.sum(gh) * step == pytest.approx(u @ w, rel=1e-10)
    # hourly: overlap-weighted means keep the price integral exactly
    hourly = rebuild_price(price, grid, 1.0)
    assert np.sum(hourly) == pytest.approx(lam.sum(), rel=1e-10)
    assert hourly.min() >= 0.0
    adj = rebuild_price(price, grid, 1.0, method="adjoint")
    assert adj.min() < 0.0  # the polynomial adjoint rings around the spike


def test_rebuild_energy_interpolants():
    """cell / linear / poly reconstructions; the cell one is exact."""
    grid = _cheb_grid(24)
    v = 50.0 + 40.0 * np.sin(2 * np.pi * grid.times / 24.0)
    cell = rebuild_energy(v, grid, 1.0)
    lin = rebuild_energy(v, grid, 1.0, interp="linear")
    poly = rebuild_energy(v, grid, 1.0, interp="poly")
    assert np.sum(cell) == pytest.approx(v @ grid.durations, rel=1e-12)
    assert np.sum(poly) == pytest.approx(v @ grid.durations, rel=1e-9)
    assert lin.min() >= v.min() - 1e-9 and lin.max() <= v.max() + 1e-9


def test_level_position_follows_rule():
    from plexos2gtopt.week_reconstruct import (  # pylint: disable=import-outside-toplevel
        level_position,
    )

    assert level_position("block") == "end"
    assert level_position("lg4_pos") == "node"
    grid = _cheb_grid(16)
    lv = np.linspace(0.0, 1.0, len(grid.times))
    end = rebuild_level(lv, grid, 1.0)
    node = rebuild_level(lv, grid, 1.0, rule="lg4_pos")
    assert not np.allclose(end, node)


def test_periodic_price_adjoint_duality_identity():
    from plexos2gtopt.week_reconstruct import (  # pylint: disable=import-outside-toplevel
        periodic_basis,
    )

    grid = _cheb_grid(24)
    rng = np.random.default_rng(3)
    price = 40.0 + 10.0 * rng.normal(size=len(grid.times))
    lam = price * grid.durations
    a = price_coefficients(price, grid, periodic=True)
    # the price polynomial is itself periodic
    assert cheb.chebval(-1.0, a) == pytest.approx(cheb.chebval(1.0, a), abs=1e-9)
    gmat = gram_matrix(len(grid.times) - 1, grid.window)
    v = node_vandermonde(grid)
    b = periodic_basis(len(grid.times))
    for _ in range(5):
        c = b @ rng.normal(size=b.shape[1])  # any periodic quantity
        u = v @ c
        assert u[0] == pytest.approx(u[-1], abs=1e-9)
        assert a @ gmat @ c == pytest.approx(lam @ u, rel=1e-9)
    # only λ_0 + λ_N matters: moving dual mass between the ends is invisible
    lam2 = lam.copy()
    lam2[0] += 0.5
    lam2[-1] -= 0.5
    a2 = price_coefficients(lam2 / grid.durations, grid, periodic=True)
    assert np.allclose(a, a2, atol=1e-9)


def test_hourly_revenue_identity_cell_averages():
    grid = _cheb_grid(96)
    t = grid.times
    price = 50.0 + 20.0 * np.sin(2 * np.pi * t / 24.0)
    gen = 100.0 + 60.0 * np.sin(2 * np.pi * (t - 3.0) / 24.0)
    exact = revenue_integral(gen, price, grid)
    assert exact == pytest.approx(np.sum(price * gen * grid.durations), rel=1e-9)
    # hourly cell averages of both: the gap is the within-cell covariance
    for step in (1.0, 0.5):
        ph = rebuild_price_coef(price, grid, step)
        gh = rebuild_energy(gen, grid, step, interp="poly")
        hourly = float(np.sum(ph * gh) * step)
        # Δ²/12·∫p′g′ ≈ 4e-4 (1 h), 1e-4 (0.5 h) for these daily sinusoids
        assert abs(hourly / exact - 1.0) < 6e-4 * step**2
    # averages keep the price integral exactly
    ph = rebuild_price_coef(price, grid, 1.0)
    a = price_coefficients(price, grid)
    integral = float(np.diff(cheb.chebval([-1.0, 1.0], cheb.chebint(a)))[0]) * 84.0
    assert ph.sum() == pytest.approx(integral, rel=1e-12)


def _nonneg_traces():
    t = (np.arange(336) + 0.5) * 0.5
    rng = np.random.default_rng(11)
    solar = np.clip(np.sin(2 * np.pi * (t - 6.0) / 24.0), 0.0, None) ** 0.7
    solar *= 0.6 + 0.4 * rng.random(336).round(1)
    solar[solar < 0.02] = 0.0
    wind = np.clip(0.4 + 0.4 * np.sin(2 * np.pi * t / 37.0), 0.0, 1.0)
    wind[100:140] = 0.0  # calm spell
    wind[200:203] = 1.0  # gust
    return {"solar": solar, "wind": wind}


@pytest.mark.parametrize("n", [32, 64, 96, 168])
def test_no_negative_nodes_or_cells_for_nonnegative_series(n):
    s = ChebyshevSampler(n=n, periodic="C0")
    g = s.grid()
    grid = NodeGrid(g.times, g.durations)
    for name, y in _nonneg_traces().items():
        v = s.sample(y, 0.5, g)
        assert v.min() >= 0.0, name
        assert v.max() <= y.max() + 1e-12, name
        energy = y.sum() * 0.5
        assert v @ g.durations == pytest.approx(energy, rel=1e-10)
        cells = rebuild_energy(v, grid, 0.5, bounds=(0.0, 1.0))
        assert cells.min() >= 0.0 and cells.max() <= 1.0, name
        assert cells.sum() * 0.5 == pytest.approx(energy, rel=1e-10)


@pytest.mark.parametrize("n,tol", [(64, 1e-2), (96, 2e-3), (168, 2e-3)])
def test_bounded_generation_rebuild_revenue_gap(n, tol):
    """Non-negative rebuild of the generation: cells ≥ 0, energy exact;
    the revenue identity then holds to ``tol`` (the unbounded rebuild is
    exact to ~2e-4 but has negative night cells)."""
    t = (np.arange(336) + 0.5) * 0.5
    solar = np.clip(np.sin(2 * np.pi * (t - 6) / 24), 0, None) ** 0.7
    solar *= 0.7 + 0.3 * np.sin(2 * np.pi * t / 70)
    price = (
        60 + 40 * np.cos(2 * np.pi * (t - 19) / 24) + 10 * np.sin(2 * np.pi * t / 50)
    )
    s = ChebyshevSampler(n=n, periodic="C0")
    g = s.grid()
    grid = NodeGrid(g.times, g.durations)
    gv = s.sample(100.0 * solar, 0.5, g, (0.0, 100.0))
    pv = s.sample(price, 0.5, g)
    unb = revenue_check(gv, pv, grid, 0.5, "coef")
    bnd = revenue_check(gv, pv, grid, 0.5, "coef", gen_bounds=(0.0, 100.0))
    # N blocks (periodic: node N dropped) → the node polynomial is not
    # periodic any more; the polynomial adjoint is exact to ~1e-3
    assert abs(unb.rel_gap) < 2e-3
    assert abs(bnd.rel_gap) < tol
    cells = rebuild_energy(gv, grid, 0.5, bounds=(0.0, 100.0))
    assert cells.min() >= 0.0
    assert cells.sum() * 0.5 == pytest.approx(gv @ g.durations, rel=1e-10)
