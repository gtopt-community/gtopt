"""Rebuild hourly series of a typical week from its node (block) solution.

The typical-week stages of the ISP expansion case (``Stage.period_hours``)
carry one value per block: node values of the dispatch, prices, flows and
storage levels at the node times ``t_k`` with real durations ``Δt_k``.
This module maps them back to a regular grid (hourly or half-hourly) of
the week window, and the week back to its month (× days / 7):

* **energy-like** quantities (generation, load, flows): by default the
  interpolant implied by the LP's storage rule — CELL-CONSTANT on the
  quadrature cells ``[τ_{k−1}, τ_k]`` (``τ_k = Σ_{j≤k} w_j``; the
  ``block`` rule), averaged over every output cell, so the rebuilt
  series integrates to ``Σ_k g_k·w_k`` exactly.  ``interp="linear"``
  (piecewise linear through the nodes, the ``trapezoid`` rule) and
  ``interp="poly"`` (the node polynomial — only for smooth inputs: it
  overshoots near kinks such as sunrise / sunset) are options.
* **prices** (duals): by default the node LMP ``π_k = λ_k / w_k`` held
  constant on its cell ``[τ_{k−1}, τ_k]``, an hour's price being the
  overlap-weighted mean of the cell prices (:func:`rebuild_price`,
  ``price_reconstruction = "cell"``).  Then ``∫ π·g dt = Σ_k λ_k g_k``
  exactly for every cell-constant quantity ``g``, and a nonnegative
  node price can never turn into a negative hourly price.  The
  coefficient-space adjoint (``"adjoint"``, :func:`price_coefficients`,
  ``G a = Vᵀλ``) is kept as an option: exact for every ``g`` of the node
  polynomial space, but a scarcity spike rings into negative prices.
  The hourly-grid adjoint (:func:`rebuild_price_adjoint`) and plain
  interpolation (:func:`rebuild_point`) remain for comparison.
* **storage levels**: the rule of the LP balance (:func:`rebuild_level`
  ``rule``): block-END values (``τ_k``) for the ``block`` recursion, the
  level AT the node ``t_k`` for the banded rules (``trapezoid``,
  ``lg4[_pos]``, ``lg6[_pos]``), interpolated linearly; levels outside
  the bounds are reported.

PROVISIONAL with the Chebyshev sampler (see :mod:`week_sampler`).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.polynomial import chebyshev as cheb


@dataclass(frozen=True)
class NodeGrid:
    """Node times and real durations of one typical-week stage."""

    times: np.ndarray  #: node times [h] inside the window
    durations: np.ndarray  #: real block durations [h]
    window: float = 168.0
    #: True for a polynomial node set (Chebyshev); False = piecewise blocks
    polynomial: bool = True


def _x(t: np.ndarray, window: float) -> np.ndarray:
    return 2.0 * np.asarray(t, dtype=float) / window - 1.0


def node_polynomial(values: np.ndarray, grid: NodeGrid) -> np.ndarray:
    """Chebyshev coefficients of the interpolant through the node values."""
    x = _x(grid.times, grid.window)
    v = cheb.chebvander(x, len(x) - 1)
    return np.linalg.solve(v, np.asarray(values, dtype=float))


def _edges(grid: NodeGrid, step: float) -> np.ndarray:
    return np.arange(int(round(grid.window / step)) + 1) * step


def cell_edges(grid: NodeGrid) -> np.ndarray:
    """Quadrature-cell edges ``[0, τ_0, …, τ_{n−1} = window]`` (real h)."""
    d = np.asarray(grid.durations, dtype=float)
    e = np.concatenate(([0.0], np.cumsum(d)))
    e *= grid.window / e[-1] if e[-1] > 0.0 else 1.0
    return e


def _cell_constant(values: np.ndarray, grid: NodeGrid, step: float) -> np.ndarray:
    """Overlap-weighted means of the cell-constant node values."""
    bedges = cell_edges(grid)
    cum = np.concatenate(([0.0], np.cumsum(np.asarray(values) * np.diff(bedges))))
    return np.diff(np.interp(_edges(grid, step), bedges, cum)) / step


def _piecewise_linear(values: np.ndarray, grid: NodeGrid, step: float) -> np.ndarray:
    """Cell means of the piecewise-linear interpolant through the nodes
    (constant beyond the end nodes)."""
    edges = _edges(grid, step)
    sub = 32
    fine = np.linspace(0.0, 1.0, sub + 1)[:-1] + 0.5 / sub
    t = (edges[:-1, None] + step * fine[None, :]).ravel()
    v = np.interp(t, np.asarray(grid.times, dtype=float), np.asarray(values))
    return np.asarray(v.reshape(len(edges) - 1, sub).mean(axis=1), dtype=float)


def rebuild_energy(
    values: np.ndarray,
    grid: NodeGrid,
    step: float = 1.0,
    bounds: tuple[float, float] | None = None,
    interp: str = "cell",
) -> np.ndarray:
    """Cell means (``step`` h cells) of an energy-like node quantity.

    ``interp``: ``"cell"`` (default; cell-constant on the quadrature
    cells — the ``block`` storage rule, energy exact), ``"linear"``
    (piecewise linear through the nodes — the ``trapezoid`` rule) or
    ``"poly"`` (the node polynomial, smooth inputs only).  Block grids
    (``grid.polynomial`` False) are always cell-constant.

    ``bounds`` (e.g. ``(0, pmax)`` for a non-negative series): the cell
    means of the polynomial are clipped into the bounds and the window
    energy is restored on the headroom (:func:`clip_rescale`), so the
    rebuilt series is in range and its energy stays exact."""
    if bounds is not None:
        from .week_sampler import (  # pylint: disable=import-outside-toplevel
            clip_rescale,
        )

        raw = rebuild_energy(values, grid, step, interp=interp)
        energy = float(np.sum(np.asarray(values) * grid.durations))
        lo, hi = bounds
        if raw.min() >= lo and raw.max() <= hi:
            return raw
        return clip_rescale(raw, np.full(len(raw), step), lo, hi, energy)
    edges = _edges(grid, step)
    vals = np.asarray(values, dtype=float)
    if not grid.polynomial or interp == "cell":
        return _cell_constant(vals, grid, step)
    if interp == "linear":
        return _piecewise_linear(vals, grid, step)
    c = node_polynomial(vals, grid)
    prim = cheb.chebval(_x(edges, grid.window), cheb.chebint(c))
    # d t = window / 2 · d x
    return np.diff(prim) * (grid.window / 2.0) / step


def rebuild_point(values: np.ndarray, grid: NodeGrid, step: float = 1.0) -> np.ndarray:
    """Point values at the cell midpoints (prices, storage levels)."""
    mid = _edges(grid, step)[:-1] + step / 2.0
    vals = np.asarray(values, dtype=float)
    if not grid.polynomial:
        starts = np.concatenate(([0.0], np.cumsum(grid.durations)[:-1]))
        k = np.clip(np.searchsorted(starts, mid, side="right") - 1, 0, len(vals) - 1)
        return vals[k]
    return np.asarray(
        cheb.chebval(_x(mid, grid.window), node_polynomial(vals, grid)), dtype=float
    )


def rebuild_point_linear(
    values: np.ndarray, grid: NodeGrid, step: float = 1.0
) -> np.ndarray:
    """Piecewise-linear interpolation of node values at the cell midpoints.

    This is how the user's paper code post-processes every Chebyshev
    output, duals included, to hourly values (``cheby_interpol.py``).
    Kept for comparison: for prices it is not consistent with the
    energy reconstruction (it leaves a revenue gap)."""
    mid = _edges(grid, step)[:-1] + step / 2.0
    return np.asarray(
        np.interp(mid, grid.times, np.asarray(values, dtype=float)), dtype=float
    )


def reconstruction_matrix(
    grid: NodeGrid, step: float = 1.0, interp: str = "poly"
) -> np.ndarray:
    """``R`` (cells × nodes): cell means of the rebuilt energy-like series
    (``interp`` as :func:`rebuild_energy`; the polynomial by default, the
    operator of the adjoint price options).

    ``1ᵀ·Δh·R = wᵀ`` (the node durations): energy is conserved for any
    node vector."""
    n = len(grid.times)
    return np.column_stack(
        [rebuild_energy(np.eye(n)[k], grid, step, interp=interp) for k in range(n)]
    )


def masked_reconstruction_matrix(
    grid: NodeGrid, night: np.ndarray, step: float = 1.0, min_day: float = 0.2
) -> tuple[np.ndarray, int]:
    """``R`` with zero output at night (solar): ``R_m = diag(day)·R·C``.

    ``night`` flags the output cells (``step`` grid) where the trace is
    zero.  Each node column keeps its energy ``w_k`` on the daylight cells
    (column scale ``C``), so ``1ᵀΔh R_m = wᵀ`` on the daylight nodes and
    the rebuilt series is exactly 0 at night.  A column whose daylight
    share is below ``min_day`` is left unscaled (its node is essentially
    a night node, whose value the sampler set to 0); their count is
    returned.  Prices consistent with this operator are its adjoint
    (:func:`rebuild_price_adjoint` with ``r = R_m``)."""
    r = reconstruction_matrix(grid, step)
    day = ~np.asarray(night, dtype=bool)
    rm = r * day[:, None]
    share = rm.sum(axis=0) * step
    w = np.asarray(grid.durations, dtype=float)
    ok = share > min_day * w
    rm[:, ok] *= (w[ok] / share[ok])[None, :]
    return rm, int(np.sum(~ok))


def rebuild_price_adjoint(
    prices: np.ndarray,
    grid: NodeGrid,
    step: float = 1.0,
    smooth: float = 0.0,
    operator: np.ndarray | None = None,
    active: np.ndarray | None = None,
) -> np.ndarray:
    """Hourly prices consistent with the energy reconstruction (adjoint).

    ``operator`` replaces ``R`` (e.g. a masked solar operator) and
    ``active`` restricts the identity to those nodes (e.g. the daylight
    nodes; the night nodes carry ``u_k = 0``).

    ``prices`` are the node prices in $/MWh (the written duals), so the
    raw block duals are ``λ_k = p_k·w_k``.  The cell prices ``p`` solve
    the adjoint system ``Rᵀ·Δh·p = λ`` — then for ANY rebuilt quantity
    ``g = R·u``: ``Σ_h p_h·g_h·Δh = λᵀu = Σ_k p_k·u_k·w_k`` exactly
    (revenues, congestion rents, total cost).  Among the solutions:
    ``smooth = 0`` → minimum norm ``p = Δh·R(Rᵀ Δh² R)⁻¹λ``; ``smooth > 0``
    → minimum ``‖p‖² + smooth·‖second differences of p‖²`` (cyclic over
    the week), which damps oscillation.  Needs cells ≥ nodes (use a
    half-hourly ``step`` for N > 168).
    """
    r = reconstruction_matrix(grid, step) if operator is None else operator
    ncell, nnode = r.shape
    if ncell < nnode:
        raise ValueError(
            f"{nnode} nodes need at least as many cells (have {ncell}); "
            "use a finer step"
        )
    lam = np.asarray(prices, dtype=float) * grid.durations
    if active is not None:
        sel = np.asarray(active, dtype=bool)
        r, lam = r[:, sel], lam[sel]
    a = r.T * step  # (nodes × cells): a @ p = λ
    if smooth <= 0.0:
        return np.asarray(a.T @ np.linalg.solve(a @ a.T, lam), dtype=float)
    lap = (
        np.roll(np.eye(ncell), -1, axis=1)
        - 2 * np.eye(ncell)
        + np.roll(np.eye(ncell), 1, axis=1)
    )
    q = np.eye(ncell) + smooth * lap.T @ lap
    # min ½pᵀQp s.t. a p = λ  →  p = Q⁻¹aᵀ(a Q⁻¹ aᵀ)⁻¹λ
    qi_at = np.linalg.solve(q, a.T)
    return qi_at @ np.linalg.solve(a @ qi_at, lam)


def gram_matrix(degree: int, window: float = 168.0) -> np.ndarray:
    """``G_ij = ∫ T_i T_j dt`` over the window (``dt = window/2·dx``).

    Closed form: ``T_i T_j = (T_{i+j} + T_{|i−j|})/2``, ``∫T_m dx = 0``
    (odd m) or ``2/(1−m²)`` (even m)."""

    def mom(m: np.ndarray) -> np.ndarray:
        out = np.zeros(m.shape)
        even = m % 2 == 0
        out[even] = 2.0 / (1.0 - m[even].astype(float) ** 2)
        return out

    k = np.arange(degree + 1)
    i, j = np.meshgrid(k, k, indexing="ij")
    return np.asarray(
        0.5 * (mom(i + j) + mom(np.abs(i - j))) * window / 2.0, dtype=float
    )


def node_vandermonde(grid: NodeGrid) -> np.ndarray:
    """``V_kj = T_j(x_k)`` (square: nodes × coefficients)."""
    x = _x(grid.times, grid.window)
    return cheb.chebvander(x, len(x) - 1)


def periodic_basis(n_coef: int) -> np.ndarray:
    """Orthonormal basis ``B`` of the C0-periodic coefficient subspace
    ``{c : p(−1) = p(1)}`` = ``{c : Σ_{j odd} c_j = 0}``."""
    r = np.array([((-1.0) ** j - 1.0) for j in range(n_coef)])
    q, _ = np.linalg.qr(np.column_stack((r, np.eye(n_coef))))
    return q[:, 1:n_coef]


def price_coefficients(
    prices: np.ndarray,
    grid: NodeGrid,
    smooth: float = 0.0,
    periodic: bool = False,
) -> np.ndarray:
    """Chebyshev coefficients ``a`` of the price polynomial (dual adjoint).

    ``prices`` are node prices in $/MWh, so the raw block duals are
    ``λ = p_k·w_k``.  ``a`` solves ``G a = Vᵀ λ``: then for every ``g`` in
    the node space (``u = V c``) ``∫ p·g dt = aᵀ G c = λᵀ u`` exactly
    (revenue, rents, total cost), independent of any hourly grid.
    ``smooth > 0`` adds ``smooth·Σ (k/n)^4 a_k²`` relative to the
    Gram scale (a damped, no longer exact, variant).

    ``periodic``: the quantities live in the C0-periodic subspace
    (``u_0 = u_N``); the price is solved in the SAME subspace,
    ``(BᵀGB) z = BᵀVᵀλ``, ``a = B z``, so the identity holds for every
    periodic ``g``.  Only ``λ_0 + λ_N`` enters (the merged end price)."""
    v = node_vandermonde(grid)
    g = gram_matrix(v.shape[1] - 1, grid.window)
    lam = np.asarray(prices, dtype=float) * grid.durations
    if smooth > 0.0:
        k = np.arange(v.shape[1], dtype=float)
        g = g + smooth * np.trace(g) / len(k) * np.diag((k / max(k[-1], 1.0)) ** 4)
    if periodic:
        b = periodic_basis(v.shape[1])
        z = np.linalg.solve(b.T @ g @ b, b.T @ (v.T @ lam))
        return b @ z
    return np.linalg.solve(g, v.T @ lam)


def rebuild_price_coef(
    prices: np.ndarray,
    grid: NodeGrid,
    step: float = 1.0,
    smooth: float = 0.0,
    periodic: bool = False,
    average: bool = True,
) -> np.ndarray:
    """Cell prices of the coefficient-space adjoint (default price
    reconstruction): cell AVERAGES ``∫_cell p / Δ`` (as the half-hourly
    AEMO data are defined), or ``p`` at the cell midpoints with
    ``average=False``."""
    a = price_coefficients(prices, grid, smooth, periodic)
    edges = _edges(grid, step)
    if average:
        prim = cheb.chebval(_x(edges, grid.window), cheb.chebint(a))
        return np.diff(prim) * (grid.window / 2.0) / step
    mid = edges[:-1] + step / 2.0
    return np.asarray(cheb.chebval(_x(mid, grid.window), a), dtype=float)


def rebuild_price(
    prices: np.ndarray,
    grid: NodeGrid,
    step: float = 1.0,
    method: str = "cell",
    periodic: bool = False,
) -> np.ndarray:
    """Hourly (``step``) prices from the node prices ``π_k = λ_k / w_k``.

    ``method`` (``price_reconstruction``): ``"cell"`` (default) — ``π_k``
    constant on the quadrature cell ``[τ_{k−1}, τ_k]``, an output cell's
    price is the overlap-weighted mean, so ``∫ π·g dt = λᵀu`` for every
    cell-constant ``g`` and nonnegative node prices stay nonnegative;
    ``"adjoint"`` — the coefficient-space adjoint
    (:func:`rebuild_price_coef`)."""
    if method == "cell" or not grid.polynomial:
        return _cell_constant(np.asarray(prices, dtype=float), grid, step)
    if method == "adjoint":
        return rebuild_price_coef(prices, grid, step, periodic=periodic)
    raise ValueError(f"unknown price reconstruction {method!r}")


def revenue_integral(
    gen: np.ndarray, price: np.ndarray, grid: NodeGrid, periodic: bool = False
) -> float:
    """Exact ``∫ p·g dt`` of the price polynomial and the generation
    interpolant (equals ``Σ_k p_k g_k w_k``)."""
    c = node_polynomial(gen, grid)
    a = price_coefficients(price, grid, periodic=periodic)
    return float(a @ gram_matrix(len(c) - 1, grid.window) @ c)


@dataclass
class RevenueCheck:
    """Revenue identity of one unit over the window."""

    nodes: float  #: Σ_k λ_k g_k Δt_k
    rebuilt: float  #: Σ_h p_h g_h Δh
    gap: float  #: rebuilt − nodes

    @property
    def rel_gap(self) -> float:
        """Gap relative to the node revenue."""
        return self.gap / max(abs(self.nodes), 1e-12)


def revenue_check(
    gen: np.ndarray,
    price: np.ndarray,
    grid: NodeGrid,
    step: float = 1.0,
    method: str = "cell",
    smooth: float = 0.0,
    periodic: bool = False,
    gen_bounds: tuple[float, float] | None = None,
    interp: str | None = None,
) -> RevenueCheck:
    """Compare the node revenue with the revenue of the rebuilt series.

    ``method``: ``"cell"`` (default: cell-constant node prices with the
    cell-constant generation — exact up to the output cells that
    straddle a quadrature-cell edge), ``"coef"`` (coefficient-space
    adjoint with the polynomial generation: exact for the continuous
    integral, the hourly sum leaves a small midpoint-rule residual),
    ``"adjoint"`` (hourly-grid adjoint: exact for the hourly sum),
    ``"interp"`` (naive polynomial node-price interpolation) or
    ``"linear"`` (piecewise-linear, as the paper's post-processing).
    ``interp``: generation reconstruction (default ``"cell"`` with
    ``method="cell"``, else ``"poly"``).  ``gen_bounds``: rebuild the
    generation with the bounded (non-negative, energy-preserving)
    reconstruction."""
    nodes = float(np.sum(np.asarray(price) * np.asarray(gen) * grid.durations))
    gi = interp or ("cell" if method == "cell" else "poly")
    g = rebuild_energy(gen, grid, step, bounds=gen_bounds, interp=gi)
    if not grid.polynomial:  # piecewise blocks: block prices are exact
        p = rebuild_point(price, grid, step)
    elif method == "cell":
        p = rebuild_price(price, grid, step)
    elif method == "linear":
        p = rebuild_point_linear(price, grid, step)
    elif method == "coef":
        p = rebuild_price_coef(price, grid, step, smooth, periodic)
    elif method == "adjoint":
        p = rebuild_price_adjoint(price, grid, step, smooth)
    else:
        p = rebuild_point(price, grid, step)
    rebuilt = float(np.sum(p * g) * step)
    return RevenueCheck(nodes, rebuilt, rebuilt - nodes)


def block_ends(grid: NodeGrid) -> np.ndarray:
    """``τ_k = Σ_{j≤k} w_j``: the time a block's storage level refers to.

    The LP's recursion ``e_k = e_{k−1} + f_k·w_k`` makes ``e_k`` the level
    at the END of block k (≈ ``∫_0^{τ_k} f``), not at the node ``t_k``
    (which lies inside ``[τ_{k−1}, τ_k]``; ``|t_k − τ_k|`` ≤ 2.1 h at
    N = 64, 0.8 h at N = 168)."""
    return np.cumsum(np.asarray(grid.durations, dtype=float))


def level_position(rule: str = "block") -> str:
    """Where the LP level ``e_k`` sits for a storage-integration rule:
    ``"end"`` (block end ``τ_k``) for ``block``, ``"node"`` (``t_k``) for
    the banded rules."""
    return "end" if rule == "block" else "node"


def rebuild_level(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    levels: np.ndarray,
    grid: NodeGrid,
    step: float = 0.5,
    initial: float | None = None,
    at: str | None = None,
    rule: str = "block",
) -> np.ndarray:
    """Storage level at the cell midpoints: piecewise linear through
    ``(0, initial)`` and ``(τ_k, e_k)`` (block ends, the ``block``
    recursion), or through ``(t_k, e_k)`` at the nodes for the banded
    rules.  ``at`` defaults to :func:`level_position` of ``rule`` (the
    element's ``storage_integration``).  ``initial`` defaults to the last
    level (closed weekly cycle)."""
    at = at or level_position(rule)
    lv = np.asarray(levels, dtype=float)
    if at == "node":
        mid = _edges(grid, step)[:-1] + step / 2.0
        return np.asarray(np.interp(mid, grid.times, lv), dtype=float)
    e0 = float(lv[-1]) if initial is None else float(initial)
    mid = _edges(grid, step)[:-1] + step / 2.0
    out: np.ndarray = np.interp(
        mid, np.concatenate(([0.0], block_ends(grid))), np.concatenate(([e0], lv))
    )
    return out


def cumulative_integration_matrix(grid: NodeGrid) -> np.ndarray:
    """Dense ``S = V J V⁻¹``: ``(S f)_k = ∫_0^{t_k} p_f`` exactly at the
    nodes (``p_f`` the node interpolant).  The LP keeps the sparse
    recursion; this is the deferred dense option (and a reference)."""
    v = node_vandermonde(grid)
    n = v.shape[1]
    x = _x(grid.times, grid.window)
    cols = []
    for j in range(n):
        e = np.zeros(n)
        e[j] = 1.0
        cols.append(cheb.chebval(x, cheb.chebint(e, lbnd=-1.0)))
    jmat = np.column_stack(cols) * grid.window / 2.0  # nodes × coefficients
    return jmat @ np.linalg.inv(v)


@dataclass
class LevelCheck:
    """Storage level between the nodes."""

    max_above: float  #: max excess over ``hi`` of the rebuilt level
    max_below: float  #: max shortfall under ``lo``
    violations: int  #: cells out of bounds


def level_check(
    levels: np.ndarray,
    grid: NodeGrid,
    lo: float,
    hi: float,
    step: float = 0.5,
    tol: float = 1e-6,
    at: str | None = None,
) -> LevelCheck:
    """Bound violations of the storage level between block ends / nodes
    (see :func:`rebuild_level`)."""
    lv = rebuild_level(levels, grid, step, at=at)
    above = np.maximum(lv - hi, 0.0)
    below = np.maximum(lo - lv, 0.0)
    scale = max(abs(hi), 1.0) * tol
    return LevelCheck(
        float(above.max(initial=0.0)),
        float(below.max(initial=0.0)),
        int(np.sum((above > scale) | (below > scale))),
    )


def to_month(series: np.ndarray, days: int) -> float:
    """Month total of a rebuilt weekly energy series (× days / 7)."""
    return float(np.sum(series)) * days / 7.0
