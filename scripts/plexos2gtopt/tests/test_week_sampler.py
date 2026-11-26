"""Typical-week samplers (option 1; Chebyshev part provisional)."""

from datetime import date

import numpy as np
import pytest
from numpy.polynomial import chebyshev as cheb

from plexos2gtopt.week_sampler import (
    BlockGrid,
    ChebyshevSampler,
    NestedChebyshevRefine,
    PeakRampSampler,
    RampRefineSampler,
    WEEK_START,
    UniformSampler,
    cell_means,
    chebyshev_nodes,
    clip_rescale,
    interpolatory_weights,
    nested_weights,
    parse_ramp_refine,
    parse_week_start,
    fixed_windows,
    ramp_events,
    refine_edges,
    select_window,
    smooth_cell_means,
)

T = 168.0


def _cell_sampled_poly(coef: np.ndarray, step: float) -> np.ndarray:
    """Exact cell means of p(x(t)) on a ``step`` grid over [0, T]."""
    edges = 2.0 * np.arange(int(T / step) + 1) * step / T - 1.0
    prim = cheb.chebval(edges, cheb.chebint(coef))
    return np.diff(prim) / np.diff(edges)


# --- nodes / weights ------------------------------------------------------


def test_hand_examples_3_nodes():
    # Lobatto n = 2: nodes −1, 0, 1 with Simpson weights 1/3, 4/3, 1/3
    x = chebyshev_nodes(2, "lobatto")
    assert np.allclose(x, [-1.0, 0.0, 1.0])
    assert np.allclose(interpolatory_weights(x), [1 / 3, 4 / 3, 1 / 3])
    # Gauss n = 3: ±√3/2, 0 with Fejér weights 4/9, 10/9, 4/9
    x = chebyshev_nodes(3, "gauss")
    assert np.allclose(x, [-np.sqrt(3) / 2, 0.0, np.sqrt(3) / 2])
    assert np.allclose(interpolatory_weights(x), [4 / 9, 10 / 9, 4 / 9])
    # scaled to the week: Lobatto blocks T/6, 2T/3, T/6 at t = 0, T/2, T
    g = ChebyshevSampler(n=2).grid()
    assert np.allclose(g.durations, [T / 6, 2 * T / 3, T / 6])
    assert np.allclose(g.times, [0.0, T / 2, T])


@pytest.mark.parametrize("kind", ["gauss", "lobatto"])
@pytest.mark.parametrize("n", [24, 48, 64, 84, 96, 128, 168, 336])
def test_weights_sum_and_symmetry(kind, n):
    s = ChebyshevSampler(n=n, kind=kind)
    g = s.grid()
    assert g.durations.sum() == pytest.approx(T, rel=1e-12)
    assert np.all(g.durations > 0)
    assert np.allclose(g.durations, g.durations[::-1])
    assert np.allclose(g.times + g.times[::-1], T)


@pytest.mark.parametrize("kind", ["gauss", "lobatto"])
def test_quadrature_exact_on_polynomials(kind):
    s = ChebyshevSampler(n=24, kind=kind)
    x = s.nodes_x()
    w = interpolatory_weights(x)
    rng = np.random.default_rng(1)
    c = rng.normal(size=s.degree + 1)
    exact = cheb.chebval(1.0, cheb.chebint(c)) - cheb.chebval(-1.0, cheb.chebint(c))
    assert w @ cheb.chebval(x, c) == pytest.approx(exact, rel=1e-12)


@pytest.mark.parametrize("kind", ["gauss", "lobatto"])
@pytest.mark.parametrize("n", [24, 48, 84])
def test_week_quadrature_exact_to_its_degree(kind, n):
    """On the 168 h week: Σ w_k p(t_k) = ∫_0^T p dt for every polynomial
    of degree ≤ N − 1 (Fejér, N Gauss nodes) / ≤ N (Clenshaw–Curtis,
    N + 1 Lobatto nodes), and not beyond (degree 2N: T_2N aliases)."""
    s = ChebyshevSampler(n=n, kind=kind)
    g = s.grid()
    x = 2.0 * g.times / T - 1.0
    for deg in range(s.degree + 1):
        c = np.zeros(deg + 1)
        c[-1] = 1.0  # T_deg
        prim = cheb.chebint(c)
        exact = (cheb.chebval(1.0, prim) - cheb.chebval(-1.0, prim)) * T / 2.0
        assert g.durations @ cheb.chebval(x, c) == pytest.approx(exact, abs=1e-9 * T), (
            deg
        )
    c = np.zeros(2 * n + 1)
    c[-1] = 1.0
    prim = cheb.chebint(c)
    exact = (cheb.chebval(1.0, prim) - cheb.chebval(-1.0, prim)) * T / 2.0
    assert abs(g.durations @ cheb.chebval(x, c) - exact) > 1.0


@pytest.mark.parametrize("n", [24, 48, 84])
def test_periodic_lobatto_week_weights(n):
    """Periodic Lobatto week (as emitted): N blocks, weights sum to 168 h,
    symmetric on the circle (w_k = w_{N−k}, node 0 = both ends), all
    positive, node times in [0, 168)."""
    g = ChebyshevSampler(n=n, periodic="C0").grid()
    w = g.durations
    assert len(w) == n
    assert w.sum() == pytest.approx(T, rel=1e-12)
    assert np.all(w > 0)
    assert np.allclose(w[1:], w[1:][::-1])
    assert g.times[0] == pytest.approx(0.0) and g.times[-1] < T
    # month weight: durations × days / 7 add up to the month's hours
    assert (w * 31 / 7).sum() == pytest.approx(31 * 24)


def test_week_start_default_and_parse():
    assert WEEK_START == (2, 12.0)  # Wednesday 12:00
    for samp in (UniformSampler(), PeakRampSampler(), ChebyshevSampler(n=8)):
        assert samp.window_start == WEEK_START
    assert parse_week_start("wed:12") == (2, 12.0)
    assert parse_week_start("Mon") == (0, 0.0)
    assert parse_week_start("6:23.5") == (6, 23.5)
    for bad in ("xyz:1", "7:0", "wed:24", "wed:-1"):
        with pytest.raises(ValueError):
            parse_week_start(bad)


def test_select_window_starts_wednesday_noon():
    # July 2027 starts on a Thursday; Wednesdays are the 7th, 14th, ...
    first = date(2027, 7, 1)
    y = np.zeros(31 * 24)
    y[20 * 24 + 18] = 10.0  # peak on Wed 21st 18:00
    i0 = select_window(y, first, WEEK_START, 1.0)
    d, h = divmod(i0, 24)
    assert (first.replace(day=1 + d)).weekday() == 2 and h == 12
    assert i0 <= 20 * 24 + 18 < i0 + 168


# --- projection ------------------------------------------------------------


@pytest.mark.parametrize("kind", ["gauss", "lobatto"])
def test_polynomial_reproduced_and_energy(kind):
    s = ChebyshevSampler(n=20, kind=kind)
    rng = np.random.default_rng(2)
    c = rng.normal(size=s.degree + 1)
    y = _cell_sampled_poly(c, 0.5)
    fit = s.fit(y, 0.5)
    assert np.allclose(fit.coef, c, atol=1e-9)
    assert fit.max_dev < 1e-9
    # quadrature identity Σ w p(t_k) = ∫p = Σ y Δs
    assert fit.energy_nodes == pytest.approx(fit.energy, rel=1e-13)


def _week_like(step: float) -> np.ndarray:
    t = (np.arange(int(T / step)) + 0.5) * step
    return 0.5 + 0.4 * np.sin(2 * np.pi * t / 24.0) * np.sin(np.pi * t / T) ** 2


@pytest.mark.parametrize("n", [64, 96, 128, 168])
def test_energy_conserved_on_real_like_series(n):
    s = ChebyshevSampler(n=n)
    y = _week_like(0.5)
    fit = s.fit(y, 0.5)
    assert fit.energy_nodes == pytest.approx(fit.energy, rel=1e-12)
    assert fit.energy == pytest.approx(y.sum() * 0.5)


def test_periodicity_constraints():
    rng = np.random.default_rng(3)
    y = _week_like(0.5) + 0.1 * np.linspace(0, 1, 336) + 0.01 * rng.normal(size=336)
    s = ChebyshevSampler(n=48, periodic="C1")
    fit = s.fit(y, 0.5)
    p = fit.coef
    assert cheb.chebval(1.0, p) == pytest.approx(cheb.chebval(-1.0, p), abs=1e-10)
    dp = cheb.chebder(p)
    assert cheb.chebval(1.0, dp) == pytest.approx(cheb.chebval(-1.0, dp), abs=1e-8)
    # Lobatto end nodes are the same instant (Wed 12:00): equal values
    assert fit.node_values[0] == pytest.approx(fit.node_values[-1], abs=1e-10)
    assert fit.energy_nodes == pytest.approx(fit.energy, rel=1e-12)


def test_odd_terms_none_gives_symmetric_week():
    y = _week_like(0.5) + 0.2 * np.linspace(0, 1, 336)
    s = ChebyshevSampler(n=40, odd_terms="none")
    fit = s.fit(y, 0.5)
    assert np.allclose(fit.coef[1::2], 0.0)
    assert np.allclose(fit.node_values, fit.node_values[::-1])
    assert fit.energy_nodes == pytest.approx(fit.energy, rel=1e-12)
    k3 = ChebyshevSampler(n=40, odd_terms=3).fit(y, 0.5)
    assert np.count_nonzero(np.abs(k3.coef[1::2]) > 0) <= 3


def test_bounds_report_clip_and_bounded():
    # a capacity factor with sharp edges: the projection overshoots [0, 1]
    t = (np.arange(336) + 0.5) * 0.5
    y = np.clip(np.sin(2 * np.pi * (t - 6.0) / 24.0) * 1.3, 0.0, 1.0)
    rep = ChebyshevSampler(n=64, bound_mode="report").fit(y, 0.5, bounds=(0.0, 1.0))
    assert rep.overshoot_count > 0
    clip = ChebyshevSampler(n=64, bound_mode="clip_rescale").fit(
        y, 0.5, bounds=(0.0, 1.0)
    )
    assert clip.node_values.min() >= -1e-12 and clip.node_values.max() <= 1 + 1e-12
    assert clip.energy_nodes == pytest.approx(clip.energy, rel=1e-10)
    bnd = ChebyshevSampler(n=32, bound_mode="bounded").fit(y, 0.5, bounds=(0.0, 1.0))
    assert bnd.node_values.min() >= -1e-6 and bnd.node_values.max() <= 1 + 1e-6
    assert bnd.energy_nodes == pytest.approx(bnd.energy, rel=1e-6)


def test_clip_rescale_hand():
    w = np.array([1.0, 2.0, 1.0])
    v = clip_rescale(np.array([1.2, 0.5, -0.2]), w, 0.0, 1.0, 2.0)
    # clipped 1, .5, 0 → energy 2.0 already
    assert np.allclose(v, [1.0, 0.5, 0.0])
    v = clip_rescale(np.array([1.2, 0.5, -0.2]), w, 0.0, 1.0, 2.5)
    # deficit 0.5 on headroom (0, .5, 1)·w = 2 ⇒ +25 % of each headroom
    assert np.allclose(v, [1.0, 0.625, 0.25])
    assert w @ v == pytest.approx(2.5)


def test_other_value_modes():
    y = _week_like(0.5)
    s = ChebyshevSampler(n=16, values="interp")
    g = s.grid()
    assert np.allclose(
        s.sample(y, 0.5, g)[1], np.interp(g.times[1], (np.arange(336) + 0.5) * 0.5, y)
    )
    m = ChebyshevSampler(n=16, values="mean", durations="voronoi")
    gv = m.grid()
    assert gv.durations.sum() == pytest.approx(T)
    vals = m.sample(y, 0.5, gv)
    assert vals @ gv.durations == pytest.approx(y.sum() * 0.5)


# --- uniform / peak-ramp / window selection ---------------------------------


def test_uniform_block_means_conserve_energy():
    y = _week_like(0.5)
    for h in (1.0, 2.0):
        s = UniformSampler(hours=h)
        g = s.grid()
        assert g.n == int(T / h)
        vals = s.sample(y, 0.5, g)
        assert vals @ g.durations == pytest.approx(y.sum() * 0.5)


def test_peak_ramp_grid_constant_size():
    y = _week_like(1.0) * 1000
    s = PeakRampSampler(fine=4)
    g = s.grid(y, 1.0)
    assert g.n == 7 * (12 + 4)
    assert g.durations.sum() == pytest.approx(T)
    vals = s.sample(y, 1.0, g)
    assert vals @ g.durations == pytest.approx(y.sum())
    # the daily peak hour sits in a 1 h block
    peak = int(np.argmax(y[:24]))
    k = int(np.searchsorted(g.starts, peak, side="right")) - 1
    assert g.durations[k] == 1.0


def test_cell_means():
    y = np.array([1.0, 3.0])  # two 1 h samples
    assert np.allclose(cell_means(y, 1.0, np.array([0.0, 0.5, 2.0])), [1.0, 7 / 3])


def test_select_window():
    # March 2028: 1st is a Wednesday
    d0 = date(2028, 3, 1)
    y = np.zeros(31 * 24)
    y[20 * 24 + 18] = 5.0  # peak on the 21st (Tue) 18:00
    i = select_window(y, d0, (2, 12.0))
    # Wednesday-noon windows: the one from the 15th 12:00 covers the 21st
    assert i == 14 * 24 + 12
    j = select_window(y, d0, (0, 0.0))
    assert j == 19 * 24  # Monday the 20th


@pytest.mark.parametrize("kind", ["gauss", "lobatto"])
@pytest.mark.parametrize("n", [64, 168, 336])
def test_wcell_values_exact_energy_and_bounds(kind, n):
    s = ChebyshevSampler(n=n, kind=kind, values="wcell")
    t = (np.arange(336) + 0.5) * 0.5
    y = np.clip(np.sin(2 * np.pi * (t - 6.0) / 24.0) * 1.3, 0.0, 1.0)
    g = s.grid()
    v = s.sample(y, 0.5, g)
    assert v @ g.durations == pytest.approx(y.sum() * 0.5, rel=1e-12)
    assert v.min() >= 0.0 and v.max() <= 1.0


def test_projection_reproduces_polynomials():
    # a degree-10 polynomial given as cell means on a fine grid: the
    # projection recovers it up to the step-function discretisation error
    from plexos2gtopt.week_sampler import (  # pylint: disable=import-outside-toplevel
        chebyshev_projection,
    )

    rng = np.random.default_rng(5)
    c = rng.normal(size=11)
    for step in (0.1, 0.025):
        y = _cell_sampled_poly(c, step)
        coef, corr = chebyshev_projection(y, step, T, 32)
        err = np.abs(coef[:11] - c).max() + np.abs(coef[11:]).max()
        assert err < 40 * step**2 * np.abs(c).sum()
        assert corr < 1e-3
    # exact energy: ∫p dt = Σ y Δs
    assert cheb.chebval(1.0, cheb.chebint(coef, lbnd=-1)) * T / 2 == pytest.approx(
        y.sum() * 0.025, rel=1e-12
    )


@pytest.mark.parametrize("kind", ["gauss", "lobatto"])
@pytest.mark.parametrize("n", [64, 168, 336])
def test_projection_stable_and_energy_exact(kind, n):
    t = (np.arange(336) + 0.5) * 0.5
    y = np.clip(np.sin(2 * np.pi * (t - 6.0) / 24.0) * 1.3, 0.0, 1.0)
    s = ChebyshevSampler(n=n, kind=kind, values="proj", bound_mode="report")
    fit = s.fit_proj(y, 0.5, (0.0, 1.0))
    assert fit.energy_nodes == pytest.approx(fit.energy, rel=1e-12)
    # Gibbs-type overshoot only (no blow-up)
    assert fit.node_values.min() > -0.15 and fit.node_values.max() < 1.15
    clip = ChebyshevSampler(n=n, kind=kind, values="proj", bound_mode="clip_rescale")
    v = clip.sample(y, 0.5, clip.grid(), (0.0, 1.0))
    assert v.min() >= -1e-12 and v.max() <= 1 + 1e-12
    assert v @ clip.grid().durations == pytest.approx(y.sum() * 0.5, rel=1e-10)


@pytest.mark.parametrize("n", [16, 64, 168])
def test_lsw_generalized_inverse_equals_projection(n):
    from plexos2gtopt.week_sampler import (  # pylint: disable=import-outside-toplevel
        chebyshev_projection,
        lsw_operator,
    )

    t = (np.arange(336) + 0.5) * 0.5
    y = np.clip(np.sin(2 * np.pi * (t - 6.0) / 24.0) * 1.3, 0.0, 1.0) + 0.1
    s = ChebyshevSampler(n=n, values="lsw")
    a, w, gplus = lsw_operator(336, 0.5, T, s.degree, "lsw")
    assert a.shape == (336, s.degree + 1) and w.shape == (336,)
    raw = gplus @ y
    # G⁺ = Γ⁻¹ A_θᵀ W reproduces the continuous projection's raw coefficients
    edges = 2.0 * np.arange(337) * 0.5 / T - 1.0
    theta = np.arccos(edges)
    ref = np.array(
        [y @ (theta[:-1] - theta[1:]) / np.pi]
        + [
            2 / np.pi * y @ ((np.sin(k * theta[:-1]) - np.sin(k * theta[1:])) / k)
            for k in range(1, s.degree + 1)
        ]
    )
    assert np.allclose(raw, ref, atol=1e-12)
    # after its own energy fix, proj differs from lsw only by the fix
    proj, _ = chebyshev_projection(y, 0.5, T, s.degree)
    assert np.abs(raw - proj).max() < 1e-3 * np.abs(raw).max()
    fit = s.fit_lsw(y, 0.5)
    assert not fit.rescale_fallback
    assert fit.energy_correction == pytest.approx(1.0, abs=1e-3)
    assert fit.energy_nodes == pytest.approx(fit.energy, rel=1e-12)
    assert np.allclose(fit.coef, raw * fit.energy_correction)


def test_lsw_polynomial_and_fallback():
    from plexos2gtopt.week_sampler import (  # pylint: disable=import-outside-toplevel
        rescale_energy,
    )

    rng = np.random.default_rng(6)
    c = rng.normal(size=9)
    c[0] = 5.0  # positive series
    y = _cell_sampled_poly(c, 0.025)
    fit = ChebyshevSampler(n=32, values="lsw").fit_lsw(y, 0.025)
    assert np.abs(fit.coef[:9] - c).max() < 1e-2
    assert fit.energy_nodes == pytest.approx(fit.energy, rel=1e-12)
    # both signs → additive c_0 fallback, energy still exact
    coef, factor, fb = rescale_energy(np.array([0.0, 1.0]), 3.0, T, True)
    assert fb and factor == 1.0
    assert coef[0] * T == pytest.approx(3.0)


def test_lsw_discrete_normal_equations_ill_conditioned():
    from plexos2gtopt.week_sampler import (  # pylint: disable=import-outside-toplevel
        lsw_operator,
    )

    a, w, _ = lsw_operator(336, 0.5, T, 96, "lswd")
    assert np.linalg.cond((a.T * w) @ a) > 1e8


def test_default_is_fct_clipped_to_data_range():
    s = ChebyshevSampler(n=64)
    assert s.values == "fct" and s.bound_mode == "clip_rescale"
    t = (np.arange(336) + 0.5) * 0.5
    y = np.clip(np.sin(2 * np.pi * (t - 6.0) / 24.0) * 1.3, 0.0, 1.0)
    g = s.grid()
    v = s.sample(y, 0.5, g)
    assert v.min() >= -1e-12 and v.max() <= 1.0 + 1e-12
    assert v @ g.durations == pytest.approx(y.sum() * 0.5, rel=1e-10)


def test_fct_dct_round_trip_and_truncation():
    from scipy.fft import dct  # pylint: disable=import-outside-toplevel

    # DCT-I of a polynomial sampled at Lobatto points recovers it
    c = np.array([1.0, -0.5, 0.25, 0.125, -0.0625])
    m = 16
    xj = np.cos(np.pi * np.arange(m + 1) / m)
    a = dct(cheb.chebval(xj, c), type=1) / m
    a[0] /= 2
    a[-1] /= 2
    assert np.allclose(a[:5], c) and np.allclose(a[5:], 0.0, atol=1e-14)
    # a known series truncated: degree-6 data, degree-3 fit keeps c_0..c_3
    from plexos2gtopt.week_sampler import (  # pylint: disable=import-outside-toplevel
        fct_coefficients,
    )

    c6 = np.array([2.0, 0.3, -0.2, 0.1, 0.05, -0.03, 0.02])
    y = _cell_sampled_poly(c6, 0.0125)
    got = fct_coefficients(y, 0.0125, T, 3, 8192)
    assert np.allclose(got, c6[:4], atol=1e-5)


@pytest.mark.parametrize("n", [64, 168])
def test_fct_matches_projection_and_energy(n):
    from plexos2gtopt.week_sampler import (  # pylint: disable=import-outside-toplevel
        chebyshev_projection,
    )

    t = (np.arange(336) + 0.5) * 0.5
    y = np.clip(np.sin(2 * np.pi * (t - 6.0) / 24.0) * 1.3, 0.0, 1.0) + 0.05
    s = ChebyshevSampler(n=n, bound_mode="report")
    assert s.values == "fct"
    fit = s.fit_fct(y, 0.5)
    proj, _ = chebyshev_projection(y, 0.5, T, s.degree)
    assert np.abs(fit.coef - proj).max() < 2e-5
    assert fit.energy_nodes == pytest.approx(fit.energy, rel=1e-12)
    assert not fit.rescale_fallback
    # point sampling of the step function aliases the jumps
    pt = ChebyshevSampler(n=n, values="fct_point").fit_fct(y, 0.5)
    assert np.abs(pt.coef - proj).max() > np.abs(fit.coef - proj).max()


@pytest.mark.parametrize("values", ["fct", "lsw"])
def test_fct_periodic_and_odd_terms(values):
    t = (np.arange(336) + 0.5) * 0.5
    y = _week_like(0.5) + 0.2 * t / T
    base = ChebyshevSampler(n=48, values=values, bound_mode="report")
    c1 = (
        ChebyshevSampler(
            n=48, values=values, periodic="C1", bound_mode="report"
        ).fit_fct(y, 0.5)
        if values == "fct"
        else ChebyshevSampler(
            n=48, values=values, periodic="C1", bound_mode="report"
        ).fit_lsw(y, 0.5)
    )
    p = c1.coef
    assert cheb.chebval(1.0, p) == pytest.approx(cheb.chebval(-1.0, p), abs=1e-10)
    dp = cheb.chebder(p)
    assert cheb.chebval(1.0, dp) == pytest.approx(cheb.chebval(-1.0, dp), abs=1e-8)
    assert c1.energy_nodes == pytest.approx(c1.energy, rel=1e-12)
    assert c1.node_values[0] == pytest.approx(c1.node_values[-1], abs=1e-9)
    none = ChebyshevSampler(n=48, values=values, odd_terms="none", bound_mode="report")
    fn = none.fit_fct(y, 0.5) if values == "fct" else none.fit_lsw(y, 0.5)
    assert np.allclose(fn.coef[1::2], 0.0)
    assert fn.energy_nodes == pytest.approx(fn.energy, rel=1e-12)
    k3 = ChebyshevSampler(n=48, values=values, odd_terms=3, bound_mode="report")
    f3 = k3.fit_fct(y, 0.5) if values == "fct" else k3.fit_lsw(y, 0.5)
    assert np.count_nonzero(np.abs(f3.coef[1::2]) > 0) <= 3
    # unconstrained fit is not periodic (the series has a trend)
    fb = base.fit_fct(y, 0.5) if values == "fct" else base.fit_lsw(y, 0.5)
    assert abs(cheb.chebval(1.0, fb.coef) - cheb.chebval(-1.0, fb.coef)) > 1e-3


def test_order_selection_tail_estimate_and_choice():
    from plexos2gtopt.week_sampler import (  # pylint: disable=import-outside-toplevel
        choose_order,
        degree_of,
        trace_errors,
    )

    t = (np.arange(336) + 0.5) * 0.5
    solar = 100 * np.clip(np.sin(2 * np.pi * (t - 6) / 24), 0, None)
    wind = 60 + 30 * np.sin(2 * np.pi * t / 50)
    # smooth wind is resolved at a low order; the solar kinks need more
    _, rms_w, _ = trace_errors(wind, 0.5, degree_of(32, "lobatto"))
    est_s, rms_s, ramp_s = trace_errors(solar, 0.5, degree_of(32, "lobatto"))
    assert rms_w < 1e-3 * 60 < rms_s
    assert est_s == pytest.approx(rms_s, rel=0.3)
    assert ramp_s > 0
    # errors fall with N; the choice is the first candidate under tol
    ch = choose_order([solar, wind], [100.0, 100.0], 0.5, tol=0.01)
    assert ch.met
    errs = [ch.table[n][1] for n in sorted(ch.table)]
    assert errs[-1] <= 0.01 and all(e > 0.01 for e in errs[:-1])
    assert all(a >= b for a, b in zip(errs, errs[1:]))
    strict = choose_order([solar], [100.0], 0.5, tol=1e-9, candidates=(32, 64))
    assert not strict.met and strict.n == 64


def _solar_week(seed: int = 0) -> np.ndarray:
    t = (np.arange(336) + 0.5) * 0.5
    rng = np.random.default_rng(seed)
    return np.clip(np.sin(2 * np.pi * (t - 6) / 24), 0, None) * (
        0.6 + 0.4 * rng.random(336)
    )


@pytest.mark.parametrize("basis", ["plain", "clearsky"])
@pytest.mark.parametrize("n", [48, 128])
def test_solar_night_nodes_exactly_zero(basis, n):
    from plexos2gtopt.week_sampler import (  # pylint: disable=import-outside-toplevel
        night_nodes,
    )

    y = _solar_week()
    s = ChebyshevSampler(n=n, solar_basis=basis)
    g = s.grid()
    v = s.sample(y, 0.5, g)
    night = night_nodes(y, 0.5, g.durations, T)
    assert night.sum() > 0
    assert np.all(v[night] == 0.0)
    assert v.min() >= 0.0 and v.max() <= y.max() + 1e-12
    assert v @ g.durations == pytest.approx(y.sum() * 0.5, rel=1e-12)
    # without the mask the polynomial leaks into the night
    off = ChebyshevSampler(n=n, solar_night="off")
    assert np.any(off.sample(y, 0.5, g)[night] != 0.0)


def test_clearsky_reproduces_envelope_times_linear_k():
    from plexos2gtopt.week_sampler import (  # pylint: disable=import-outside-toplevel
        clearsky_nodes,
    )

    t = (np.arange(336) + 0.5) * 0.5
    env = np.clip(np.sin(2 * np.pi * (t - 6) / 24), 0, None)
    k = 0.5 + 0.4 * t / T  # clearness, linear in t
    y = env * k
    s = ChebyshevSampler(n=32)
    g = s.grid()
    v = clearsky_nodes(s, y, 0.5, envelope=env)
    j = np.clip((g.times / 0.5).astype(int), 0, 335)
    kk = 0.5 + 0.4 * g.times / T
    assert np.allclose(v, env[j] * kk, atol=2e-3)


# --- alignment with the user's paper code (tcheby ChebyLib) -----------------


def test_trapezoid_weights_match_paper_int_trapezoide():
    from plexos2gtopt.week_sampler import (  # pylint: disable=import-outside-toplevel
        trapezoid_weights,
    )

    # ChebyLib::int_trapezoide: interior (x_{i+1} − x_{i−1})/2, ends half
    t = np.array([0.0, 1.0, 3.0, 4.0])
    assert np.allclose(trapezoid_weights(t, 0.0, 4.0), [0.5, 1.5, 1.5, 0.5])
    g = ChebyshevSampler(n=16, durations="trapezoid").grid()
    assert g.durations.sum() == pytest.approx(T)
    # Lobatto: equals the Voronoi cells
    gv = ChebyshevSampler(n=16, durations="voronoi").grid()
    assert np.allclose(g.durations, gv.durations)


@pytest.mark.parametrize("n", [16, 64, 168, 336])
def test_clenshaw_curtis_default_and_positive(n):
    s = ChebyshevSampler(n=n)
    assert s.kind == "lobatto" and s.durations == "quadrature"
    w = s.grid().durations
    assert np.all(w > 0.0) and w.sum() == pytest.approx(T)


@pytest.mark.parametrize("durations", ["quadrature", "trapezoid"])
def test_block_energy_exact_for_both_weightings(durations):
    y = _week_like(0.5)
    s = ChebyshevSampler(n=48, durations=durations)
    g = s.grid()
    v = s.sample(y, 0.5, g)
    assert v @ g.durations == pytest.approx(y.sum() * 0.5, rel=1e-12)


def test_l1_energy_fit_paper():
    t = (np.arange(336) + 0.5) * 0.5
    y = np.clip(np.sin(2 * np.pi * (t - 6.0) / 24.0), 0.0, None) + 0.0
    # m = n: interpolation of the point samples (before energy restoration)
    s1 = ChebyshevSampler(n=24, values="l1", solar_night="off", bound_mode="report")
    g = s1.grid()
    from plexos2gtopt.week_sampler import (  # pylint: disable=import-outside-toplevel
        l1_energy_values,
    )

    v = l1_energy_values(s1, y, 0.5)
    idx = np.clip((g.times / 0.5).astype(int), 0, 335)
    assert np.allclose(v, y[idx], atol=1e-9)
    # oversampled (the paper's demand variant): nonnegative at the samples
    s2 = ChebyshevSampler(n=24, values="l1", l1_oversample=2, l1_y="cell")
    v2 = s2.sample(y, 0.5, s2.grid())
    assert v2.min() >= 0.0
    assert v2 @ s2.grid().durations == pytest.approx(y.sum() * 0.5, rel=1e-10)


def test_clip_rescale_unbounded_side_stays_finite():
    w = np.array([1.0, 2.0, 1.0])
    v = clip_rescale(np.array([1.0, 2.0, 3.0]), w, 0.0, np.inf, 8.0)
    assert np.all(np.isfinite(v))
    assert w @ v == pytest.approx(8.0)
    v = clip_rescale(np.array([1.0, 2.0, 3.0]), w, 0.0, np.inf, 10.0)
    assert np.all(np.isfinite(v)) and w @ v == pytest.approx(10.0)


def test_sampler_unbounded_bounds_no_nan():
    t = (np.arange(336) + 0.5) * 0.5
    y = 1000.0 + 200.0 * np.sin(2 * np.pi * t / 24.0)
    s = ChebyshevSampler(n=96, periodic="C0")
    g = s.grid()
    v = s.sample(y, 0.5, g, bounds=(0.0, np.inf))
    assert np.all(np.isfinite(v))
    assert v @ g.durations == pytest.approx(y.sum() * 0.5, rel=1e-10)


def test_unbounded_series_stays_within_data_range():
    # a line rating stepping 130 → 150 (no physical cap given): the
    # truncated series overshoots at the step (Gibbs); nodes must not
    # exceed the rating and the energy is kept
    y = np.where(np.arange(336) < 150, 130.0, 150.0)
    s = ChebyshevSampler(n=96, periodic="C0")
    g = s.grid()
    v = s.sample(y, 0.5, g, bounds=(0.0, np.inf))
    assert v.max() <= 150.0 + 1e-9 and v.min() >= 130.0 - 30.0
    assert v @ g.durations == pytest.approx(y.sum() * 0.5, rel=1e-10)


def test_mixed_sign_series_keeps_data_range():
    t = (np.arange(336) + 0.5) * 0.5
    y = 50.0 * np.sin(2 * np.pi * t / 24.0) - 10.0
    s = ChebyshevSampler(n=64, periodic="C0")
    g = s.grid()
    v = s.sample(y, 0.5, g)
    assert v.min() >= y.min() - 1e-9 and v.max() <= y.max() + 1e-9
    assert v @ g.durations == pytest.approx(y.sum() * 0.5, rel=1e-10)


def test_periodic_merge_then_bound_keeps_ends_and_cap():
    # wind calm at the window start only: the end merge zeroes both ends
    # and rescales the interior — the result must stay ≤ the capacity
    t = (np.arange(336) + 0.5) * 0.5
    y = np.clip(0.95 + 0.1 * np.sin(2 * np.pi * t / 30.0), 0.0, 1.0)
    y[:6] = 0.0
    s = ChebyshevSampler(n=64, periodic="C0")
    g = s.grid()
    v = s.sample(y, 0.5, g, bounds=(0.0, 1.0))
    assert v.max() <= 1.0 + 1e-12 and v.min() >= 0.0
    # periodic Lobatto: N blocks, node N (≡ node 0) dropped
    assert len(v) == len(g.durations) == 64 and g.times[-1] < 168.0
    assert v[0] == 0.0
    assert v @ g.durations == pytest.approx(y.sum() * 0.5, rel=1e-10)


def test_periodic_lobatto_drops_end_node():
    """Periodic Lobatto: N blocks; node 0 carries w_0 + w_N."""
    s = ChebyshevSampler(n=16, periodic="C0")
    full = s.full_grid()
    g = s.grid()
    assert len(full.durations) == 17 and len(g.durations) == 16
    assert g.durations[0] == pytest.approx(full.durations[0] + full.durations[-1])
    assert np.sum(g.durations) == pytest.approx(168.0)
    assert ChebyshevSampler(n=16).grid().durations.shape == (17,)


def test_range_guard_keeps_masked_nodes_at_zero():
    """P0-2: masked (night) nodes get no room; lost energy is reported."""
    from plexos2gtopt.week_sampler import (  # pylint: disable=import-outside-toplevel
        GUARD_STATS,
        reset_guard_stats,
    )

    reset_guard_stats()
    v = np.array([0.0, 1.4, 0.9, 0.0])
    w = np.ones(4)
    frozen = np.array([True, False, False, True])
    out = clip_rescale(v, w, 0.0, 1.0, float(v.sum()), frozen=frozen)
    assert out[0] == 0.0 and out[3] == 0.0
    assert out.max() <= 1.0
    # 2.3 needed, room for 2.0 on the free nodes: 0.3 lost and reported
    assert out.sum() == pytest.approx(2.0)
    assert GUARD_STATS.energy_lost == pytest.approx(0.3)


def test_sampler_audit_periodic_n_nodes(tmp_path, monkeypatch) -> None:
    """The audit record works on the N-node periodic Lobatto set."""
    import json  # pylint: disable=import-outside-toplevel

    from plexos2gtopt import week_sampler as ws  # pylint: disable=import-outside-toplevel

    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(ws, "_AUDIT_PATH", str(path))
    s = ws.ChebyshevSampler(n=16, periodic="C0")
    t = (np.arange(336) + 0.5) * 0.5
    y = 1.0 + 0.5 * np.sin(2 * np.pi * t / 24.0)
    nodes = s.sample(y, 0.5, s.grid())
    assert len(nodes) == 16
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["n"] == 16 and rec["cell_neg"] == 0


# --- ramp-window refinement -------------------------------------------------


def _sunny_week(step: float = 0.5) -> np.ndarray:
    """Synthetic half-hourly net load of a sunny week starting at noon:
    flat demand 1000 minus a solar bell between 06:00 and 18:00 (window
    hour 0 = 12:00, so sunrise / sunset sit at 18 h / 6 h of every day)."""
    t = (np.arange(int(T / step)) + 0.5) * step  # cell centres
    clock = (t + 12.0) % 24.0
    solar = np.where(
        (clock > 6.0) & (clock < 18.0),
        800.0 * np.sin(np.pi * (clock - 6.0) / 12.0),
        0.0,
    )
    return 1000.0 - solar


def test_ramp_events_on_a_sunny_week() -> None:
    """Window detection: one sunrise (steepest fall) and one sunset
    (steepest rise) per day, within 1 h of 07:00 / 17:00; a flat day
    falls back to 06:00 / 18:00; fixed windows never overlap."""
    ev = ramp_events(_sunny_week(), 0.5)
    assert len(ev) == 14
    clock = (ev + 12.0) % 24.0
    morning, evening = clock[clock < 12.0], clock[clock >= 12.0]
    assert len(morning) == len(evening) == 7
    assert np.all(np.abs(morning - 7.0) <= 1.0)  # steepest after sunrise
    assert np.all(np.abs(evening - 17.0) <= 1.0)  # steepest before sunset
    flat = ramp_events(np.full(96, 5.0), 0.5)
    np.testing.assert_allclose(flat, [6.0, 18.0, 30.0, 42.0])
    wins = fixed_windows(ev, 1.0, 1.0, T)
    assert len(wins) == 14
    assert all(b - a == pytest.approx(2.0) for a, b in wins)
    # close / edge events are pushed apart and inside the period
    assert fixed_windows(np.array([0.2, 0.5, 167.9]), 1.0, 1.0, T) == [
        (0.0, 2.0),
        (2.0, 4.0),
        (166.0, T),
    ]
    assert fixed_windows(np.array([5.0]), 1.0, 2.0, T) == [(4.0, 6.0)]
    with pytest.raises(ValueError):
        fixed_windows(np.arange(10.0), 1.0, 1.0, 8.0)


@pytest.mark.parametrize("res", [0.5, 0.25])
@pytest.mark.parametrize(
    "base", [UniformSampler(hours=1.0), UniformSampler(hours=2.0, point=True)]
)
def test_ramp_refine_grid_weights_and_energy(base: UniformSampler, res: float) -> None:
    """Durations sum to the week, fine cells only inside the windows,
    coarse cells untouched elsewhere, and every block value conserves the
    energy of the trace."""
    ref = _sunny_week()
    samp = RampRefineSampler(base=base, resolution=res, half_width=1.0)
    assert samp.window_start == base.window_start and samp.window_hours == T
    g = samp.grid(ref, 0.5)
    g0 = base.grid(ref, 0.5)
    assert g.durations.sum() == pytest.approx(T)
    length = np.ceil(2.0 / base.hours) * base.hours
    assert g.n == g0.n + 14 * round(length / res - length / base.hours)
    assert samp.point == base.point
    assert set(np.round(g0.starts, 9)) <= set(np.round(g.starts, 9))
    np.testing.assert_allclose(g.times, g.starts + g.durations / 2)
    wins = fixed_windows(ramp_events(ref, 0.5), 1.0, base.hours, T)
    base_cells = set(zip(np.round(g0.starts, 9), np.round(g0.durations, 9)))
    for start, dur in zip(g.starts, g.durations, strict=True):
        if any(a - 1e-9 <= start < b for a, b in wins):
            assert dur == pytest.approx(res)
        else:  # outside the windows: the base sampler's own blocks
            assert (round(start, 9), round(dur, 9)) in base_cells
    # every fine block points at the base block it splits
    assert g.cells is not None
    np.testing.assert_allclose(g0.starts[g.cells] <= g.starts, True)
    assert len(set(g.cells.tolist())) == g0.n
    vals = samp.sample(ref, 0.5, g)
    assert float(vals @ g.durations) == pytest.approx(float(ref.sum() * 0.5))


def test_smooth_cell_means_conserves_energy_and_shape() -> None:
    """Energy-conserving PCHIP sub-step means: exact on the data grid,
    every data cell's energy kept at 15 min, never negative for a
    nonnegative series, and smoother than holding the half-hour value."""
    rng = np.random.default_rng(3)
    y = np.clip(rng.normal(50.0, 40.0, 96), 0.0, None)
    y[10:20] = 0.0  # a night
    edges = np.arange(0.0, 48.0 + 1e-9, 0.5)
    np.testing.assert_allclose(smooth_cell_means(y, 0.5, edges), y, atol=1e-9)
    fine = np.arange(0.0, 48.0 + 1e-9, 0.25)
    m = smooth_cell_means(y, 0.5, fine)
    np.testing.assert_allclose(0.5 * (m[0::2] + m[1::2]), y, atol=1e-9)
    assert m.min() >= -1e-9
    assert np.all(np.abs(m[20:40]) < 1e-9)  # night stays dark
    np.testing.assert_allclose(smooth_cell_means(y, 0.5, fine), m)
    assert cell_means(y, 0.5, edges) == pytest.approx(y)


def test_refine_edges_and_parse() -> None:
    edges = np.arange(0.0, 6.1, 2.0)
    out = refine_edges(edges, [(1.0, 3.0)], 0.5)
    np.testing.assert_allclose(out, [0, 1, 1.5, 2, 2.5, 3, 4, 6])
    assert parse_ramp_refine("30m") == 0.5
    assert parse_ramp_refine("15m") == 0.25
    assert parse_ramp_refine("0.5h") == 0.5
    assert parse_ramp_refine("off") is None
    assert parse_ramp_refine("on") == 0.5
    with pytest.raises(ValueError):
        parse_ramp_refine("60m")
    with pytest.raises(ValueError):
        RampRefineSampler(base=UniformSampler()).grid(None, 0.5)


# --- nested Chebyshev refinement --------------------------------------------


def test_lobatto_nodes_nest() -> None:
    """The order-N Lobatto nodes are the even order-2N nodes."""
    for n in (24, 48, 64):
        np.testing.assert_allclose(
            chebyshev_nodes(2 * n, "lobatto")[::2],
            chebyshev_nodes(n, "lobatto"),
            atol=1e-15,
        )


def test_nested_weights_sum_positive_exactness() -> None:
    """Redistributed weights: sum kept, positive, exact on linears for any
    kept subset, exact to degree 2N when nothing is dropped, and close to
    the integral of a smooth week profile."""
    x = chebyshev_nodes(128, "lobatto")
    t = (x + 1.0) * T / 2.0
    wf = interpolatory_weights(x) * T / 2.0
    rng = np.random.default_rng(0)
    keep = (np.arange(len(t)) % 2 == 0) | (rng.random(len(t)) < 0.3)
    w = nested_weights(t, wf, keep)
    assert w.sum() == pytest.approx(T)
    assert np.all(w > 0.0)
    tk = t[keep]
    assert w @ (3.0 + 2.0 * tk) == pytest.approx(3.0 * T + T**2)
    full = nested_weights(t, wf, np.ones(len(t), dtype=bool))
    np.testing.assert_allclose(full, wf)
    c = rng.normal(size=129)
    exact = (
        (cheb.chebval(1.0, cheb.chebint(c)) - cheb.chebval(-1.0, cheb.chebint(c)))
        * T
        / 2
    )
    assert full @ cheb.chebval(x, c) == pytest.approx(exact, rel=1e-10, abs=1e-8)
    f = 1.0 + 0.5 * np.sin(2.0 * np.pi * tk / 24.0) ** 2
    assert w @ f == pytest.approx(T * 1.25, rel=2e-3)
    with pytest.raises(ValueError):
        nested_weights(t, wf, ~keep)


def test_nested_chebyshev_refine_grid_and_values() -> None:
    """64 + nested 128 nodes in the sun windows: all 64 kept, odd 128 nodes
    only inside the windows, weights positive summing to the week
    (periodic: the end node folded into node 0), values = the 128 fit."""
    ref = _sunny_week()
    base = ChebyshevSampler(n=64, periodic="C0")
    ns = NestedChebyshevRefine(base=base)
    assert ns.window_start == base.window_start and ns.window_hours == T
    g = ns.grid(ref, 0.5)
    g64 = base.grid(ref, 0.5)
    assert g.durations.sum() == pytest.approx(T)
    assert np.all(g.durations > 0.0)
    assert set(np.round(g64.times, 9)) <= set(np.round(g.times, 9))
    assert g.n == 64 + 2 * 14  # constant: 2 nested nodes per event
    ev = ramp_events(ref, 0.5)
    extra = sorted(set(np.round(g.times, 9)) - set(np.round(g64.times, 9)))
    # every added node is one of the 2 odd 128-nodes nearest an event
    odd = ns.fine.full_grid().times[1::2]
    near = {
        round(float(x), 9)
        for e in ev
        for x in odd[np.argsort(np.abs(odd - e), kind="stable")[:4]]
    }
    assert set(extra) <= near
    vals = ns.sample(ref, 0.5, g)
    fine = ns.fine
    fv = fine.sample(ref, 0.5, fine.grid())
    ft = fine.grid().times
    for tk, v in zip(g.times, vals, strict=True):
        assert v == pytest.approx(fv[int(np.argmin(np.abs(ft - tk)))])
    with pytest.raises(ValueError):
        NestedChebyshevRefine(base=ChebyshevSampler(n=8, kind="gauss"))
    with pytest.raises(ValueError):
        ns.grid(None, 0.5)
    with pytest.raises(ValueError):
        ns.sample(ref, 0.5, BlockGrid(np.array([T]), np.array([0.3])))
