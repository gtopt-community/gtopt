"""Typical-week samplers for the ISP expansion case (option 1).

A month is represented by ONE chronological typical week (168 h) whose
blocks may have non-uniform real durations.  A *sampler* turns the
traces of the chosen week window into blocks:

* :meth:`WeekSampler.grid` -> :class:`BlockGrid` (real durations that add
  up to the window length, and the node time of every block);
* :meth:`WeekSampler.sample` -> one value per block for a trace given at
  a fixed step (hourly or half-hourly) over the window.

Samplers:

* :class:`UniformSampler` — N-hour blocks (N = 1, 2, ...), block means.
* :class:`PeakRampSampler` — 2 h slots, the daily net-load peak slot and
  the steepest net-load ramp slots (solar ramps) split into 1 h blocks.
* :class:`ChebyshevSampler` — Chebyshev–Gauss / –Gauss–Lobatto nodes on
  the window, block durations = the matching quadrature weights
  (Fejér / Clenshaw–Curtis), values from an energy-conserving Chebyshev
  fit of the trace (``values``, default ``fct``).  A periodic Lobatto
  set emits N blocks (node N ≡ node 0 of the next cycle is dropped and
  its weight added to node 0).  The range guard keeps masked night /
  calm nodes at zero and reports the energy it cannot restore
  (:data:`GUARD_STATS`).

The stage built from a week has ``period_hours = 168`` and block
durations ``real_duration × days_in_month / 7`` (weights), so the month's
energy and costs are kept while storages cycle over the real week.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache
from datetime import date, timedelta
from typing import Literal, Protocol

import logging

import numpy as np
from numpy.polynomial import chebyshev as cheb

WEEK_HOURS = 168.0
#: default window start of every typical-week sampler (weekday 0=Mon..6=Sun,
#: hour): Wednesday 12:00 -> next Wednesday 12:00, a week centred on the
#: weekend, so the weekend (the low-load days that drive storage cycles
#: and curtailment) sits in the middle of the window, away from the ends
WEEK_START: tuple[int, float] = (2, 12.0)

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def parse_week_start(spec: str) -> tuple[int, float]:
    """``"wed:12"`` / ``"mon:0"`` / ``"2:12.5"`` -> (weekday, hour)."""
    day, _, hour = spec.strip().lower().partition(":")
    if day in _WEEKDAYS:
        wd = _WEEKDAYS.index(day)
    elif day.isdigit() and int(day) < 7:
        wd = int(day)
    else:
        raise ValueError(f"bad week start {spec!r}: weekday mon..sun or 0..6")
    h = float(hour) if hour else 0.0
    if not 0.0 <= h < 24.0:
        raise ValueError(f"bad week start {spec!r}: hour in [0, 24)")
    return wd, h


logger = logging.getLogger(__name__)


@dataclass
class GuardStats:
    """Range-guard / energy-rescale statistics of the sample calls."""

    calls: int = 0
    #: calls whose node values left the range
    clipped: int = 0
    #: energy the guard could not restore (no room on the free nodes),
    #: in the series' units × h
    energy_lost: float = 0.0
    #: energy correction factors of the fits
    rescale_factors: list[float] = field(default_factory=list)


#: module-wide statistics (reset by the caller)
GUARD_STATS = GuardStats()


def reset_guard_stats() -> None:
    """Zero :data:`GUARD_STATS`."""
    GUARD_STATS.calls = 0
    GUARD_STATS.clipped = 0
    GUARD_STATS.energy_lost = 0.0
    GUARD_STATS.rescale_factors = []


@dataclass(frozen=True)
class BlockGrid:
    """Blocks of one sampled period (real hours)."""

    durations: np.ndarray  #: real duration of every block [h]
    times: np.ndarray  #: node time of every block from the window start [h]
    #: commitment cell of every block (refined block weeks: the index of
    #: the base block it splits, so the binaries stay on the base grid)
    cells: np.ndarray | None = None

    @property
    def n(self) -> int:
        """Number of blocks."""
        return len(self.durations)

    @property
    def starts(self) -> np.ndarray:
        """Real start time of every block."""
        return np.concatenate(([0.0], np.cumsum(self.durations)[:-1]))


class WeekSampler(Protocol):
    """Pluggable week sampler (see the module docstring)."""

    name: str
    #: window start inside the week: (weekday 0=Mon..6=Sun, hour)
    window_start: tuple[int, float]

    def grid(self, ref: np.ndarray | None = None, step: float = 1.0) -> BlockGrid:
        """Block grid; ``ref`` (e.g. net load at ``step``) may shape it."""
        ...  # pylint: disable=unnecessary-ellipsis

    def sample(
        self,
        series: np.ndarray,
        step: float,
        grid: BlockGrid,
        bounds: tuple[float, float] | None = None,
    ) -> np.ndarray:
        """Block values of ``series`` (window samples at ``step`` hours)."""
        ...  # pylint: disable=unnecessary-ellipsis


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def cell_means(series: np.ndarray, step: float, edges: np.ndarray) -> np.ndarray:
    """Exact means of a piecewise-constant series over the cells ``edges``."""
    y = np.asarray(series, dtype=float)
    cum = np.concatenate(([0.0], np.cumsum(y) * step))
    t = np.arange(len(y) + 1) * step
    e = np.interp(edges, t, cum)
    return np.diff(e) / np.diff(edges)


def select_window(
    net_load: np.ndarray,
    first_day: date,
    window_start: tuple[int, float],
    step: float = 1.0,
    rule: Literal["peak", "energy"] = "peak",
    window_hours: float = WEEK_HOURS,
) -> int:
    """Index (in samples) of the chosen week window inside a month series.

    Candidates start at every ``window_start`` (weekday, hour) of the
    month whose window fits in the series.  ``rule = "peak"``: the window
    holding the month's net-load peak (else the one with the highest
    peak); ``"energy"``: the highest net energy.
    """
    y = np.asarray(net_load, dtype=float)
    per_day = int(round(24.0 / step))
    n_win = int(round(window_hours / step))
    wd, hour = window_start
    cands = []
    for d in range(len(y) // per_day):
        if (first_day + timedelta(days=d)).weekday() != wd:
            continue
        i0 = d * per_day + int(round(hour / step))
        if i0 + n_win <= len(y):
            cands.append(i0)
    if not cands:
        raise ValueError("no complete week window in the series")
    if rule == "energy":
        return max(cands, key=lambda i: float(np.sum(y[i : i + n_win])))
    peak = int(np.argmax(y))
    for i in cands:
        if i <= peak < i + n_win:
            return i
    return max(cands, key=lambda i: float(np.max(y[i : i + n_win])))


# ---------------------------------------------------------------------------
# uniform / peak-ramp samplers
# ---------------------------------------------------------------------------


@dataclass
class UniformSampler:
    """``hours``-hour blocks over the week window (default Wednesday
    12:00, :data:`WEEK_START`), block means.

    ``point = True`` (spec ``uniform:H:point``): the same grid emitted as
    a POINT stage, the power-based formulation of Morales-España et al.
    (OR Spectrum 2015; Applied Energy 2017): node ``k`` at the centre of
    its ``H``-hour cell holds the instantaneous power there (the cell mean
    of the trace, so every energy is the quadrature of the node powers)
    and storages integrate between the nodes with the point-stage rule
    (``storage_integration``) instead of the block recursion."""

    hours: float = 1.0
    window_hours: float = WEEK_HOURS
    window_start: tuple[int, float] = WEEK_START
    name: str = "uniform"
    point: bool = False

    def grid(self, ref: np.ndarray | None = None, step: float = 1.0) -> BlockGrid:
        del ref, step
        n = int(round(self.window_hours / self.hours))
        dur = np.full(n, self.hours)
        return BlockGrid(dur, (np.arange(n) + 0.5) * self.hours)

    def sample(
        self,
        series: np.ndarray,
        step: float,
        grid: BlockGrid,
        bounds: tuple[float, float] | None = None,
    ) -> np.ndarray:
        del bounds
        edges = np.concatenate((grid.starts, [grid.starts[-1] + grid.durations[-1]]))
        return cell_means(series, step, edges)


@dataclass
class PeakRampSampler:
    """2 h slots; per 24 h of the window (noon to noon with the default
    :data:`WEEK_START`) the net-load peak slot and the ``fine - 1``
    steepest-ramp slots are split into 1 h blocks (constant block count
    ``7 · (12 + fine)``).  Values are block means."""

    fine: int = 4
    window_start: tuple[int, float] = WEEK_START
    name: str = "peak_ramp"

    def grid(self, ref: np.ndarray | None = None, step: float = 1.0) -> BlockGrid:
        if ref is None:
            raise ValueError("PeakRampSampler needs the net-load reference")
        hourly = cell_means(ref, step, np.arange(169.0))
        durs: list[float] = []
        for d in range(7):
            day = hourly[24 * d : 24 * (d + 1)]
            slot_ramp = np.array(
                [
                    abs(day[min(2 * s + 2, 23)] - day[max(2 * s - 1, 0)])
                    for s in range(12)
                ]
            )
            peak_slot = int(np.argmax(day)) // 2
            order = [peak_slot] + [
                int(s) for s in np.argsort(-slot_ramp, kind="stable") if s != peak_slot
            ]
            fine = set(order[: self.fine])
            for s in range(12):
                durs.extend([1.0, 1.0] if s in fine else [2.0])
        dur = np.array(durs)
        starts = np.concatenate(([0.0], np.cumsum(dur)[:-1]))
        return BlockGrid(dur, starts + dur / 2)

    def sample(
        self,
        series: np.ndarray,
        step: float,
        grid: BlockGrid,
        bounds: tuple[float, float] | None = None,
    ) -> np.ndarray:
        del bounds
        edges = np.concatenate((grid.starts, [WEEK_HOURS]))
        return cell_means(series, step, edges)


# ---------------------------------------------------------------------------
# Ramp-window refinement (sunrise / sunset)
# ---------------------------------------------------------------------------


def smooth_cell_means(series: np.ndarray, step: float, edges: np.ndarray) -> np.ndarray:
    """Means of ``series`` (samples of length ``step``) over ``edges``,
    energy-conserving and shape-preserving below the data resolution.

    The cumulative energy ``E(t)`` is known exactly at the sample edges;
    it is interpolated with a monotone cubic (PCHIP) and the cell means
    are ``ΔE / Δt``.  Every sample's energy is kept exactly (``E`` passes
    through the data), cells on the data grid reproduce
    :func:`cell_means`, and a nonnegative series (monotone ``E``) never
    gives negative sub-step means (PCHIP preserves monotonicity)."""
    from scipy.interpolate import (  # pylint: disable=import-outside-toplevel
        PchipInterpolator,
    )

    y = np.nan_to_num(np.asarray(series, dtype=float))
    cum = np.concatenate(([0.0], np.cumsum(y) * step))
    t = np.arange(len(y) + 1) * step
    e = PchipInterpolator(t, cum)(np.clip(edges, t[0], t[-1]))
    return np.asarray(np.diff(e) / np.diff(edges), dtype=float)


def ramp_events(ref: np.ndarray, step: float, day_hours: float = 24.0) -> np.ndarray:
    """Times [h] of the steepest net-load fall (sunrise) and rise (sunset)
    in every ``day_hours`` chunk of ``ref`` (samples of length ``step``).

    Detected from the data, not the clock: the event of a chunk is the
    sample edge between the two samples with the largest |Δ net load|
    of each sign, so it follows the season and the location.  Every day
    gives exactly two events (a constant block count per stage); a day
    without both a fall and a rise (flat net load) falls back to the
    nominal 06:00 / 18:00 of a noon-to-noon day (chunk + 18 h / + 6 h)."""
    y = np.nan_to_num(np.asarray(ref, dtype=float))
    d = np.diff(y)  # d[i] = y[i+1] − y[i], at edge (i + 1)·step
    per = int(round(day_hours / step))
    events: list[float] = []
    for a in range(0, len(y), per):
        chunk = d[a : a + per]
        lo = int(np.argmin(chunk)) if len(chunk) else 0
        hi = int(np.argmax(chunk)) if len(chunk) else 0
        if len(chunk) and chunk[lo] < 0.0 < chunk[hi]:
            events += [(a + lo + 1) * step, (a + hi + 1) * step]
        else:
            events += [a * step + 6.0, a * step + 18.0]
    return np.array(sorted(events))


def fixed_windows(
    events: np.ndarray, half_width: float, base_hours: float, period: float
) -> list[tuple[float, float]]:
    """One window per event, all of the same length L = ⌈2W / H⌉·H on
    the base grid (H-hour blocks), centred on the event as closely as
    the grid allows and pushed apart so they never overlap or leave
    ``[0, period]``: every stage gets the same number of refined blocks
    (the converter's (stage, block) layout is rectangular)."""
    length = np.ceil(2.0 * half_width / base_hours - 1e-9) * base_hours
    starts = sorted(
        float(
            np.clip(
                np.round((e - length / 2) / base_hours) * base_hours,
                0.0,
                period - length,
            )
        )
        for e in events
    )
    for i in range(1, len(starts)):
        starts[i] = max(starts[i], starts[i - 1] + length)
    for i in range(len(starts) - 1, -1, -1):
        hi = period - length if i == len(starts) - 1 else starts[i + 1] - length
        starts[i] = min(starts[i], hi)
    if starts and starts[0] < -1e-9:
        raise ValueError("ramp windows do not fit in the period")
    return [(a, a + length) for a in starts]


def refine_edges(
    edges: np.ndarray, windows: list[tuple[float, float]], resolution: float
) -> np.ndarray:
    """``edges`` plus a ``resolution`` grid inside every window (anchored
    at the window start).  The span and every original edge are kept, so
    the durations still sum to the period."""
    extra = [np.arange(a, b + 0.5 * resolution, resolution) for a, b in windows]
    allv = np.concatenate([np.asarray(edges, dtype=float), *extra])
    allv = allv[(allv >= edges[0] - 1e-9) & (allv <= edges[-1] + 1e-9)]
    allv = np.sort(allv)
    keep = np.concatenate(([True], np.diff(allv) > 1e-6))
    return np.asarray(allv[keep], dtype=float)


@dataclass
class RampRefineSampler:
    """A block (or ``uniform:H:point``) sampler refined to ``resolution``
    hours (``--ramp-refine 30m|15m``) within ``± half_width`` of every
    sunrise / sunset net-load ramp (:func:`ramp_events`).

    Outside the windows the base sampler's blocks are unchanged; inside,
    the cells are split to ``resolution``; durations still sum to the
    period.  Values are :func:`smooth_cell_means` (exact cell means when
    the data are as fine as the cells, e.g. 30 min on the half-hourly
    AEMO traces; energy-conserving PCHIP below that, e.g. 15 min)."""

    base: UniformSampler
    resolution: float = 0.5
    half_width: float = 1.0
    name: str = "ramp_refine"
    #: the base sampler's window start / length (set from ``base``)
    window_start: tuple[int, float] = field(init=False)
    window_hours: float = field(init=False)

    def __post_init__(self) -> None:
        self.window_start = self.base.window_start
        self.window_hours = float(getattr(self.base, "window_hours", WEEK_HOURS))

    @property
    def point(self) -> bool:
        """Point stage when the base is ``uniform:H:point``."""
        return bool(getattr(self.base, "point", False))

    def grid(self, ref: np.ndarray | None = None, step: float = 1.0) -> BlockGrid:
        if ref is None:
            raise ValueError("RampRefineSampler needs the net-load reference")
        g = self.base.grid(ref, step)
        period = float(np.sum(g.durations))
        edges = np.concatenate((g.starts, [period]))
        wins = fixed_windows(
            ramp_events(ref, step), self.half_width, self.base.hours, period
        )
        fine = refine_edges(edges, wins, self.resolution)
        dur = np.diff(fine)
        mids = fine[:-1] + dur / 2
        cells = np.searchsorted(edges, mids, side="right") - 1
        return BlockGrid(dur, mids, cells=cells)

    def sample(
        self,
        series: np.ndarray,
        step: float,
        grid: BlockGrid,
        bounds: tuple[float, float] | None = None,
    ) -> np.ndarray:
        del bounds
        edges = np.concatenate((grid.starts, [grid.starts[-1] + grid.durations[-1]]))
        return smooth_cell_means(series, step, edges)


def parse_ramp_refine(spec: str) -> float | None:
    """``--ramp-refine`` value → resolution [h]: ``30m`` / ``on`` → 0.5,
    ``15m`` → 0.25, ``off`` → None (also ``0.5h``-style hours)."""
    s = spec.strip().lower()
    if s in ("", "off", "none", "0"):
        return None
    if s == "on":  # default resolution (Chebyshev: nested nodes)
        return 0.5
    if s.endswith("m"):
        res = float(s[:-1]) / 60.0
    else:
        res = float(s.rstrip("h"))
    if not 0.0 < res < 1.0:
        raise ValueError(f"--ramp-refine must be below 1 h, got {spec!r}")
    return res


# ---------------------------------------------------------------------------
# Chebyshev sampler
# ---------------------------------------------------------------------------


def chebyshev_nodes(n: int, kind: Literal["gauss", "lobatto"]) -> np.ndarray:
    """Chebyshev nodes on ``x ∈ [−1, 1]`` in ascending order.

    ``gauss``: N = n nodes ``−cos(π(2k−1)/(2n))``, k = 1..n;
    ``lobatto``: n + 1 nodes ``−cos(πk/n)``, k = 0..n.
    """
    if kind == "gauss":
        k = np.arange(1, n + 1)
        return -np.cos(np.pi * (2 * k - 1) / (2 * n))
    if kind == "lobatto":
        k = np.arange(0, n + 1)
        return -np.cos(np.pi * k / n)
    raise ValueError(f"unknown node kind {kind!r}")


def chebyshev_moments(degree: int) -> np.ndarray:
    """``∫_{−1}^{1} T_k(x) dx`` for k = 0..degree (0 odd, 2/(1−k²) even)."""
    k = np.arange(degree + 1, dtype=float)
    out = np.zeros(degree + 1)
    even = k % 2 == 0
    out[even] = 2.0 / (1.0 - k[even] ** 2)
    return out


def interpolatory_weights(x: np.ndarray) -> np.ndarray:
    """Weights on ``[−1, 1]`` that integrate every polynomial of degree
    ``len(x) − 1`` exactly (Fejér's first rule on Gauss nodes,
    Clenshaw–Curtis on Lobatto nodes); solved in the Chebyshev basis."""
    deg = len(x) - 1
    vt = cheb.chebvander(x, deg).T  # (k, node)
    return np.linalg.solve(vt, chebyshev_moments(deg))


@dataclass
class ChebFit:
    """Diagnostics of one Chebyshev projection."""

    coef: np.ndarray
    node_values: np.ndarray
    energy: float  #: energy of the original series over the window
    energy_nodes: float  #: Σ w_k p(t_k) (after bound handling)
    max_dev: float  #: max |p − y| on the sample grid
    rms_dev: float
    overshoot_count: int = 0
    overshoot_max: float = 0.0
    #: ``proj``: relative size of the energy correction of the projection
    energy_correction: float = 0.0
    #: ``lsw``: True when the additive c_0 fallback replaced the rescale
    rescale_fallback: bool = False


@dataclass
class ChebyshevSampler:  # pylint: disable=too-many-instance-attributes
    """Chebyshev typical-week sampler (PROVISIONAL, see module docstring).

    Window centred on the weekend: Wednesday 12:00 → Wednesday 12:00.
    ``n``: Gauss → n nodes, polynomial degree n − 1; Lobatto → n + 1
    nodes, degree n.  ``values``: see the field below (default ``fct``).
    ``durations``: ``"quadrature"`` (default), ``"trapezoid"`` or
    ``"voronoi"``.  ``periodic``: None, ``"C0"`` (p(start) = p(end)) or
    ``"C1"`` (also p'); a periodic Lobatto set emits N blocks: node N
    (≡ node 0 of the cycle) is dropped and its weight added to node 0.
    ``odd_terms`` (EXPERIMENTAL, never a default): ``"all"``, ``"none"``
    or K (keep the first K odd coefficients only).  Dropping odd terms
    imposes EVEN SYMMETRY about the window centre, p(t) = p(T − t) — not
    periodicity; the periodicity conditions are the C0 / C1 rows.
    ``bound_mode`` for traces with physical bounds: ``"report"``,
    ``"clip_rescale"`` (clip node values, restore the energy on the free
    headroom) or ``"bounded"`` (bound-constrained least squares).
    """

    n: int = 96
    kind: Literal["gauss", "lobatto"] = "lobatto"
    #: node values: ``wcell`` (default) = data mean over the node's
    #: quadrature cell (cells of length w_k partition the window ⇒ exact
    #: energy, bounds kept); ``ls`` = energy-constrained LS projection of
    #: the samples — ill-conditioned for N ≳ 2√(samples) (equispaced data:
    #: cond 1e5 at N = 96, 1e15 at N = 168 on 336 half-hours), the node
    #: values at the window ends blow up; ``interp``; ``mean`` (Voronoi)
    #: ``proj`` = continuous orthogonal Chebyshev projection of the
    #: cell-mean step function with a minimal energy correction (stable
    #: for any N; see :func:`chebyshev_projection`)
    #: ``fct`` (default) = fast Chebyshev transform of the data's exact
    #: antiderivative + truncation + energy rescale
    #: (:func:`fct_coefficients`); ``fct_point`` = DCT of point samples
    #: of the step function (aliasing, comparison only);
    #: ``lsw`` = generalized inverse ``c = G⁺y`` with the Chebyshev cell
    #: measure ``W = diag(Δθ_j)`` and the exact Gram ``diag(π, π/2, …)``
    #: then a multiplicative energy rescale (:func:`lsw_operator`);
    #: ``lswd`` = discrete weighted normal equations ``(AᵀWA)⁻¹AᵀW`` and
    #: ``lsu`` = plain ``W = I`` LS (both unstable for N ≳ 64,
    #: comparison only)
    values: Literal[
        "wcell",
        "lsw",
        "lswd",
        "lsu",
        "proj",
        "ls",
        "interp",
        "mean",
        "fct",
        "fct_point",
        "l1",
    ] = "fct"
    #: Lobatto resampling size M_f of the fast Chebyshev transform
    fct_points: int = 4096
    #: ``mask`` (default): nodes whose quadrature cell has zero data (solar
    #: at night) are set to exactly 0 and the energy is restored on the
    #: other nodes only; ``off`` keeps the fitted (leaking) values
    solar_night: Literal["mask", "off"] = "mask"
    #: ``clearsky``: fit only the clearness ratio k = y / S of traces with
    #: zero periods, S = the window's daily envelope (max over the days
    #: of each time of day, exactly 0 at night); node = S(t_k)·k̂(t_k)
    solar_basis: Literal["plain", "clearsky"] = "plain"
    #: block durations: ``quadrature`` (default: Clenshaw–Curtis on the
    #: Lobatto points / Fejér on Gauss — interpolatory weights, exact on
    #: the node polynomial, all positive; diagonal per-block weights),
    #: ``trapezoid`` (the user's paper code: ``(t_{k+1} − t_{k−1})/2``,
    #: half intervals at the window ends — for Lobatto this equals the
    #: Voronoi cells) or ``voronoi`` (cells between node midpoints)
    durations: Literal["trapezoid", "quadrature", "voronoi"] = "quadrature"
    #: ``values = l1`` (the paper's ``get_energy_coeffs``): number of
    #: Lobatto sample points per node (1 = as many samples as
    #: coefficients, i.e. interpolation; 2 = the paper's oversampled
    #: demand variant) and the data taken at each sample: ``point`` (the
    #: half-hourly value at the point) or ``cell`` (mean over the
    #: point's trapezoid cell)
    l1_oversample: int = 1
    l1_y: Literal["point", "cell"] = "point"
    periodic: Literal["C0", "C1"] | None = None
    odd_terms: Literal["all", "none"] | int = "all"
    #: node values outside the bounds (default: the window's data range):
    #: ``clip_rescale`` (default) clips and restores the energy on the
    #: headroom, ``bounded`` refits with bounds (``ls`` only), ``report``
    #: leaves them (Gibbs overshoot at solar sunrise / sunset reaches
    #: ~0.19 of the peak with ``lsw`` / ``proj``)
    bound_mode: Literal["report", "clip_rescale", "bounded"] = "clip_rescale"
    window_hours: float = WEEK_HOURS
    window_start: tuple[int, float] = WEEK_START
    name: str = "chebyshev"
    last_fit: ChebFit | None = field(default=None, repr=False)

    # -- grid ---------------------------------------------------------------
    @property
    def degree(self) -> int:
        """Polynomial degree matching the node set."""
        return self.n - 1 if self.kind == "gauss" else self.n

    def nodes_x(self) -> np.ndarray:
        """Nodes on [−1, 1]."""
        return chebyshev_nodes(self.n, self.kind)

    def quadrature_weights(self) -> np.ndarray:
        """Quadrature weights scaled to the window (sum = window_hours)."""
        return interpolatory_weights(self.nodes_x()) * self.window_hours / 2.0

    @property
    def drops_end_node(self) -> bool:
        """Periodic Lobatto set: node N repeats node 0 of the cycle."""
        return self.periodic in ("C0", "C1") and self.kind == "lobatto" and self.n >= 2

    def grid(self, ref: np.ndarray | None = None, step: float = 1.0) -> BlockGrid:
        """Block grid; a periodic Lobatto set has N blocks (node N dropped,
        its weight added to node 0)."""
        full = self.full_grid(ref, step)
        if not self.drops_end_node:
            return full
        dur = np.asarray(full.durations, dtype=float)[:-1].copy()
        dur[0] += float(full.durations[-1])
        return BlockGrid(dur, np.asarray(full.times, dtype=float)[:-1])

    def full_grid(self, ref: np.ndarray | None = None, step: float = 1.0) -> BlockGrid:
        """All nodes of the set (N + 1 for Lobatto), end node included."""
        del ref, step
        t = (self.nodes_x() + 1.0) * self.window_hours / 2.0
        if self.durations == "quadrature":
            dur = self.quadrature_weights()
        elif self.durations == "trapezoid":
            dur = trapezoid_weights(t, 0.0, self.window_hours)
        else:
            edges = np.concatenate(([0.0], (t[1:] + t[:-1]) / 2, [self.window_hours]))
            dur = np.diff(edges)
        return BlockGrid(dur, t)

    # -- projection ---------------------------------------------------------
    def _columns(self) -> np.ndarray:
        k = np.arange(self.degree + 1)
        if self.odd_terms == "all":
            return k
        keep_odd = 0 if self.odd_terms == "none" else int(self.odd_terms)
        odd = k[k % 2 == 1][:keep_odd]
        return np.sort(np.concatenate((k[k % 2 == 0], odd)))

    def fit(
        self,
        series: np.ndarray,
        step: float,
        bounds: tuple[float, float] | None = None,
    ) -> ChebFit:
        """Energy-constrained LS projection of ``series`` (window samples)."""
        y = np.asarray(series, dtype=float)
        th = self.window_hours
        cols = self._columns()
        # the samples are interval means (AEMO half-hourly / hourly
        # values): fit the CELL MEANS of p, so Σ y·Δs is exactly ∫p for
        # a series that is the cell-mean sampling of a polynomial
        v = _cell_mean_matrix_cached(len(y), step, th, self.degree)[:, cols]
        energy = float(np.sum(y) * step)
        mom = chebyshev_moments(self.degree)[cols]
        rows = [mom * th / 2.0]
        rhs = [energy]
        if self.periodic in ("C0", "C1"):
            r = (cols % 2 == 1).astype(float)
            if r.any():
                rows.append(r)
                rhs.append(0.0)
        if self.periodic == "C1":
            r = np.where(cols % 2 == 0, cols.astype(float) ** 2, 0.0)
            if r.any():
                rows.append(r)
                rhs.append(0.0)
        a = np.array(rows)
        b = np.array(rhs)
        coef = _eq_lsq(v, y, a, b)
        xn = self.nodes_x()
        vn = cheb.chebvander(xn, self.degree)[:, cols]
        w = self.quadrature_weights()
        nodes = vn @ coef
        cnt, omax = 0, 0.0
        if bounds is not None:
            lo, hi = bounds
            over = np.maximum(nodes - hi, 0.0) + np.maximum(lo - nodes, 0.0)
            cnt = int(np.sum(over > 1e-9 * max(1.0, abs(hi))))
            omax = float(np.max(over)) if len(over) else 0.0
            if cnt and self.bound_mode == "clip_rescale":
                nodes = clip_rescale(nodes, w, lo, hi, energy)
            elif cnt and self.bound_mode == "bounded":
                coef = _bounded_lsq(v, y, a, b, vn, lo, hi, coef)
                nodes = vn @ coef
        dev = v @ coef - y
        full = np.zeros(self.degree + 1)
        full[cols] = coef
        fit = ChebFit(
            coef=full,
            node_values=nodes,
            energy=energy,
            energy_nodes=float(w @ nodes),
            max_dev=float(np.max(np.abs(dev))),
            rms_dev=float(np.sqrt(np.mean(dev**2))),
            overshoot_count=cnt,
            overshoot_max=omax,
        )
        self.last_fit = fit
        return fit

    def _shape_coefficients(self, coef: np.ndarray, energy: float) -> np.ndarray:
        """``odd_terms`` / ``periodic`` on fitted coefficients (fct, lsw)."""
        if self.odd_terms == "all" and self.periodic is None:
            return coef
        return constrain_coefficients(
            coef, energy, self.window_hours, self.odd_terms, self.periodic
        )

    def fit_fct(
        self,
        series: np.ndarray,
        step: float,
        bounds: tuple[float, float] | None = None,
    ) -> ChebFit:
        """FCT + truncation + energy rescale (``values = fct``)."""
        y = np.asarray(series, dtype=float)
        coef = fct_coefficients(
            y,
            step,
            self.window_hours,
            self.degree,
            self.fct_points,
            antiderivative=self.values != "fct_point",
        )
        energy = float(np.sum(y) * step)
        coef = self._shape_coefficients(coef, energy)
        coef, factor, fallback = rescale_energy(
            coef, energy, self.window_hours, bool(y.min() < 0.0 < y.max())
        )
        a = _cell_mean_matrix_cached(len(y), step, self.window_hours, self.degree)
        return self._finish(y, coef, energy, bounds, a, factor, fallback)

    def fit_lsw(
        self,
        series: np.ndarray,
        step: float,
        bounds: tuple[float, float] | None = None,
        mode: str = "lsw",
    ) -> ChebFit:
        """Generalized-inverse LS fit + energy rescale (``values = lsw``)."""
        y = np.asarray(series, dtype=float)
        a, _, gplus = lsw_operator(len(y), step, self.window_hours, self.degree, mode)
        coef = gplus @ y
        energy = float(np.sum(y) * step)
        coef = self._shape_coefficients(coef, energy)
        coef, factor, fallback = rescale_energy(
            coef, energy, self.window_hours, bool(y.min() < 0.0 < y.max())
        )
        return self._finish(y, coef, energy, bounds, a, factor, fallback)

    def _finish(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        y: np.ndarray,
        coef: np.ndarray,
        energy: float,
        bounds: tuple[float, float] | None,
        a: np.ndarray,
        factor: float,
        fallback: bool,
    ) -> ChebFit:
        w = self.quadrature_weights()
        nodes = cheb.chebval(self.nodes_x(), coef)
        cnt, omax = 0, 0.0
        if bounds is not None:
            lo, hi = bounds
            over = np.maximum(nodes - hi, 0.0) + np.maximum(lo - nodes, 0.0)
            cnt = int(np.sum(over > 1e-9 * max(1.0, abs(hi))))
            omax = float(np.max(over)) if len(over) else 0.0
            if cnt and self.bound_mode != "report":
                nodes = clip_rescale(nodes, w, lo, hi, energy)
        dev = a @ coef - y
        fit = ChebFit(
            coef=coef,
            node_values=nodes,
            energy=energy,
            energy_nodes=float(w @ nodes),
            max_dev=float(np.max(np.abs(dev))),
            rms_dev=float(np.sqrt(np.mean(dev**2))),
            overshoot_count=cnt,
            overshoot_max=omax,
            energy_correction=factor,
            rescale_fallback=fallback,
        )
        self.last_fit = fit
        return fit

    def fit_proj(
        self,
        series: np.ndarray,
        step: float,
        bounds: tuple[float, float] | None = None,
    ) -> ChebFit:
        """Continuous Chebyshev projection (``values = proj``)."""
        y = np.asarray(series, dtype=float)
        coef, corr = chebyshev_projection(y, step, self.window_hours, self.degree)
        xn = self.nodes_x()
        w = self.quadrature_weights()
        nodes = cheb.chebval(xn, coef)
        energy = float(np.sum(y) * step)
        cnt, omax = 0, 0.0
        if bounds is not None:
            lo, hi = bounds
            over = np.maximum(nodes - hi, 0.0) + np.maximum(lo - nodes, 0.0)
            cnt = int(np.sum(over > 1e-9 * max(1.0, abs(hi))))
            omax = float(np.max(over)) if len(over) else 0.0
            if cnt and self.bound_mode != "report":
                nodes = clip_rescale(nodes, w, lo, hi, energy)
        v = _cell_mean_matrix_cached(len(y), step, self.window_hours, self.degree)
        dev = v @ coef - y
        fit = ChebFit(
            coef=coef,
            node_values=nodes,
            energy=energy,
            energy_nodes=float(w @ nodes),
            max_dev=float(np.max(np.abs(dev))),
            rms_dev=float(np.sqrt(np.mean(dev**2))),
            overshoot_count=cnt,
            overshoot_max=omax,
            energy_correction=corr,
        )
        self.last_fit = fit
        return fit

    def sample(
        self,
        series: np.ndarray,
        step: float,
        grid: BlockGrid,
        bounds: tuple[float, float] | None = None,
    ) -> np.ndarray:
        y = np.asarray(series, dtype=float)
        if bounds is None and len(y):
            bounds = (float(y.min()), float(y.max()))
        drop = self.drops_end_node and len(grid.durations) == self.n
        if drop:
            grid = self.full_grid()
        self.last_fit = None
        poly = self.values in (
            "ls",
            "fct",
            "fct_point",
            "lsw",
            "lswd",
            "lsu",
            "proj",
            "l1",
            "interp",
        )
        clear = poly and self.solar_basis == "clearsky" and bool(np.any(y == 0.0))
        if clear:
            nodes = clearsky_nodes(self, y, step)
        else:
            nodes = self._sample_values(y, step, grid, bounds)
        # the node energy Σ d_k v_k (block durations d — trapezoid by
        # default, not the polynomial's exact quadrature) is restored to
        # the data energy; night nodes are zeroed first (mask)
        if poly:
            nodes = mask_night(
                nodes,
                y,
                step,
                grid.durations,
                bounds,
                self.window_hours,
                mask=self.solar_night == "mask" or clear,
            )
        if not np.all(np.isfinite(nodes)):
            raise ValueError("Chebyshev sampler: non-finite node values")
        durations = np.asarray(grid.durations, dtype=float)
        night = (
            night_nodes(y, step, durations, self.window_hours)
            if poly and len(y) and (self.solar_night == "mask" or clear)
            else np.zeros(len(nodes), dtype=bool)
        )
        if self.periodic in ("C0", "C1") and self.kind == "lobatto" and len(nodes) > 1:
            nodes = _merge_periodic_ends(nodes, y, step, durations)
            if drop:
                # node N ≡ node 0: one block of weight w_0 + w_N
                night = np.concatenate(([night[0] or night[-1]], night[1:-1]))
                nodes = nodes[:-1]
                durations = np.concatenate(
                    ([durations[0] + durations[-1]], durations[1:-1])
                )
        GUARD_STATS.calls += 1
        if self.last_fit is not None and self.last_fit.energy_correction not in (
            0.0,
            1.0,
        ):
            GUARD_STATS.rescale_factors.append(float(self.last_fit.energy_correction))
        if poly and len(y):
            # last: the end merge may rescale the interior nodes
            nodes = _clip_to_range(nodes, y, durations, bounds, frozen=night)
        if _AUDIT_PATH:
            _audit(self, y, step, nodes, bounds)
        return nodes

    def _sample_values(
        self,
        y: np.ndarray,
        step: float,
        grid: BlockGrid,
        bounds: tuple[float, float] | None,
    ) -> np.ndarray:
        if self.values == "ls":
            return self.fit(y, step, bounds).node_values
        if self.values in ("fct", "fct_point"):
            return self.fit_fct(y, step, bounds).node_values
        if self.values in ("lsw", "lswd", "lsu"):
            return self.fit_lsw(y, step, bounds, self.values).node_values
        if self.values == "proj":
            return self.fit_proj(y, step, bounds).node_values
        if self.values == "l1":
            return l1_energy_values(self, y, step)
        if self.values == "wcell":
            edges = np.concatenate(([0.0], np.cumsum(grid.durations)))
            edges[-1] = self.window_hours
            return cell_means(y, step, edges)
        if self.values == "interp":
            s = (np.arange(len(y)) + 0.5) * step
            return np.asarray(np.interp(grid.times, s, y), dtype=float)
        t = grid.times
        edges = np.concatenate(([0.0], (t[1:] + t[:-1]) / 2, [self.window_hours]))
        return cell_means(y, step, edges)


def night_nodes(
    series: np.ndarray, step: float, durations: np.ndarray, window: float
) -> np.ndarray:
    """Nodes whose quadrature cell (lengths ``durations``) has zero data."""
    edges = np.concatenate(([0.0], np.cumsum(durations)))
    edges[-1] = window
    return cell_means(np.abs(np.asarray(series, dtype=float)), step, edges) <= 0.0


def _clip_to_range(
    nodes: np.ndarray,
    series: np.ndarray,
    durations: np.ndarray,
    bounds: tuple[float, float] | None,
    frozen: np.ndarray | None = None,
) -> np.ndarray:
    """Final range guard of the node values (energy kept).

    Upper limit: the physical bound when finite (installed capacity /
    pmax), else the data max (line ratings, demand, inflows — an
    unbounded energy restore must not exceed the data).  Lower limit: 0
    for non-negative data, else the data min (mixed-sign series keep
    their sign range).  ``frozen`` nodes (masked night / calm nodes) keep
    their value and get no room for the energy restore."""
    y = np.asarray(series, dtype=float)
    lo = 0.0 if float(y.min()) >= 0.0 else float(y.min())
    hi = float(y.max())
    if bounds is not None:
        if np.isfinite(bounds[0]):
            lo = max(lo, float(bounds[0]))
        if np.isfinite(bounds[1]):
            hi = float(bounds[1])
    v = np.asarray(nodes, dtype=float)
    if v.min() >= lo and v.max() <= hi:
        return v
    GUARD_STATS.clipped += 1
    d = np.asarray(durations, dtype=float)
    return clip_rescale(v, d, lo, hi, float(d @ v), frozen=frozen)


def _merge_periodic_ends(
    nodes: np.ndarray, series: np.ndarray, step: float, durations: np.ndarray
) -> np.ndarray:
    """One value at the end instants ``t_0`` and ``t_N`` (the same instant
    of a periodic cycle), before node N is dropped.

    Weighted mean of the two end nodes (keeps ``Σ d·v``).  If the mask
    zeroed one end (no data there), both ends are zeroed and the energy
    is restored multiplicatively on the other nodes."""
    d = np.asarray(durations, dtype=float)
    v = np.asarray(nodes, dtype=float).copy()
    y = np.asarray(series, dtype=float)
    nonneg = bool(len(y)) and float(y.min()) >= 0.0
    if nonneg and (v[0] == 0.0 or v[-1] == 0.0) and v[0] != v[-1]:
        v[0] = v[-1] = 0.0
        e = float(d @ v)
        energy = float(np.sum(y) * step)
        if e > 0.0:
            v[1:-1] *= energy / e
        return np.asarray(v, dtype=float)
    end = (d[0] * v[0] + d[-1] * v[-1]) / (d[0] + d[-1])
    v[0] = v[-1] = end
    return np.asarray(v, dtype=float)


#: ``GTOPT_SAMPLER_AUDIT=<path>``: append one JSON line per Chebyshev
#: sample call (data / node / reconstructed-cell ranges, before and after
#: the node fixes) — the range-validation report reads it.
_AUDIT_PATH = __import__("os").environ.get("GTOPT_SAMPLER_AUDIT", "")
#: series kind of the current sample calls (set by the converter)
AUDIT_TAG = {"tag": ""}


@lru_cache(maxsize=16)
def _audit_cells(
    x_nodes: tuple[float, ...], n_samples: int, step: float, window: float
) -> np.ndarray:
    x = np.asarray(x_nodes)
    vinv = np.linalg.inv(cheb.chebvander(x, len(x) - 1))
    edges = 2.0 * np.arange(n_samples + 1) * step / window - 1.0
    return cell_mean_matrix(edges, len(x) - 1) @ vinv


def _audit_durations(sampler: ChebyshevSampler) -> np.ndarray:
    return np.asarray(sampler.grid().durations, dtype=float)


def _audit(
    sampler: ChebyshevSampler,
    y: np.ndarray,
    step: float,
    nodes: np.ndarray,
    bounds: tuple[float, float] | None,
) -> None:
    import json  # pylint: disable=import-outside-toplevel

    r = _audit_cells(
        tuple(sampler.nodes_x()), len(y), float(step), sampler.window_hours
    )
    # the pure truncated FCT (no rescale / clip / mask): "before any fix"
    trunc_c = fct_coefficients(y, step, sampler.window_hours, sampler.degree)
    raw = cheb.chebval(sampler.nodes_x(), trunc_c)
    cells_raw = r @ raw
    # a periodic Lobatto set emits N nodes (node N ≡ node 0 dropped):
    # the cell operator needs the full set
    full = nodes if len(nodes) == r.shape[1] else np.append(nodes, nodes[0])
    cells = r @ full
    dur = _audit_durations(sampler)
    hi = bounds[1] if bounds is not None else np.inf
    hi = min(hi, float(y.max())) if not np.isfinite(hi) else hi
    if float(y.min()) >= 0.0:
        fixed = clip_rescale(
            cells, np.full(len(cells), step), 0.0, hi, float(nodes @ dur)
        )
    else:
        fixed = cells
    rec = {
        "tag": AUDIT_TAG["tag"],
        "n": len(full) - 1,
        "ymin": float(y.min()),
        "ymax": float(y.max()),
        "energy": float(y.sum() * step),
        "raw_min": float(raw.min()),
        "raw_max": float(raw.max()),
        "raw_neg": int(np.sum(raw < -1e-9)),
        "node_min": float(nodes.min()),
        "node_max": float(nodes.max()),
        "node_neg": int(np.sum(nodes < -1e-9)),
        "cell_raw_min": float(cells_raw.min()),
        "cell_raw_max": float(cells_raw.max()),
        "cell_raw_neg": int(np.sum(cells_raw < -1e-9)),
        "cell_min": float(cells.min()),
        "cell_max": float(cells.max()),
        "cell_neg": int(np.sum(cells < -1e-9)),
        "cell_neg_energy": float(-np.sum(np.minimum(cells, 0.0)) * step),
        "flips": int(np.sum((np.sign(cells) * np.sign(y) < 0) & (np.abs(y) > 1e-9))),
        "data_sign_changes": int(np.sum(np.diff(np.sign(y[np.abs(y) > 1e-9])) != 0)),
        "hi": float(hi),
        "node_above_hi": int(np.sum(nodes > hi + 1e-9 * max(1.0, abs(hi)))),
        "cell_above_hi": int(np.sum(cells > hi + 1e-9 * max(1.0, abs(hi)))),
        "fix_min": float(fixed.min()),
        "fix_above_hi": int(np.sum(fixed > hi + 1e-9 * max(1.0, abs(hi)))),
        "fix_energy_err": float(
            abs(fixed.sum() * step - nodes @ dur) / max(1e-9, abs(nodes @ dur))
        ),
        "fix_cell_change_rel": float(
            np.abs(fixed - cells).max() / max(1e-9, float(np.abs(y).max()))
        ),
    }
    with open(_AUDIT_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def mask_night(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    nodes: np.ndarray,
    series: np.ndarray,
    step: float,
    durations: np.ndarray,
    bounds: tuple[float, float] | None,
    window: float,
    mask: bool = True,
) -> np.ndarray:
    """Zero the night nodes; restore the node energy on the others.

    ``mask``: nodes whose cell has zero data are set to 0.  Then
    ``Σ d·v`` (block durations ``d``) is restored to the series energy —
    multiplicatively for single-signed data, then :func:`clip_rescale`
    into the bounds (which also closes any remaining gap on the
    headroom): night stays exactly 0 and the block energy is exact."""
    y = np.asarray(series, dtype=float)
    v = np.asarray(nodes, dtype=float).copy()
    night = (
        night_nodes(y, step, durations, window)
        if mask
        else np.zeros(len(v), dtype=bool)
    )
    v[night] = 0.0
    day = ~night
    energy = float(np.sum(y) * step)
    w = np.asarray(durations, dtype=float)
    e_day = float(w[day] @ v[day])
    single = y.min() >= 0.0 or y.max() <= 0.0
    if single and e_day * energy > 0.0:
        v[day] *= energy / e_day
    elif not day.all() or bounds is None:
        v[day] += (energy - e_day) / max(float(w[day].sum()), 1e-300)
    if bounds is not None:
        lo = max(bounds[0], 0.0) if night.any() else bounds[0]
        v[day] = clip_rescale(v[day], w[day], lo, bounds[1], energy)
    return np.asarray(v, dtype=float)


def trapezoid_weights(t: np.ndarray, start: float, end: float) -> np.ndarray:
    """Trapezoid weights of points ``t`` (the paper's ``int_trapezoide``).

    Interior ``(t_{k+1} − t_{k−1})/2``; the end points get half the
    distance to their neighbour plus the gap to the window edge (zero
    for Lobatto, whose ends are the window ends)."""
    t = np.asarray(t, dtype=float)
    if len(t) == 1:
        return np.array([end - start])
    mid = np.concatenate(([start], (t[1:] + t[:-1]) / 2.0, [end]))
    return np.diff(mid)


def l1_energy_values(
    sampler: ChebyshevSampler, series: np.ndarray, step: float
) -> np.ndarray:
    """Node values of the paper's L1 energy fit (``get_energy_coeffs``).

    Samples ``y_i`` at ``M = l1_oversample·(N+1)`` Chebyshev–Lobatto
    points (half-hourly value at the point, or the mean over its
    trapezoid cell), coefficients ``c`` (as many as the sampler's nodes):

        min Σ_i |A_i c − y_i| + e_t
        s.t. A c ≥ 0 (when the data are ≥ 0),  tᵀA c − e_t = tᵀy, e_t ≥ 0

    with ``A_ij = T_j(x_i)`` and ``t`` the trapezoid weights (the energy
    error is one-sided, as in the paper).  Solved with HiGHS
    (``scipy.optimize.linprog``).  With ``l1_oversample = 1`` the fit
    interpolates the point samples.  Returns ``p(t_k)`` at the nodes.
    """
    from scipy.optimize import linprog  # pylint: disable=import-outside-toplevel

    y = np.asarray(series, dtype=float)
    th = sampler.window_hours
    ncoef = sampler.degree + 1
    m = max(ncoef, sampler.l1_oversample * len(sampler.nodes_x()))
    xs = chebyshev_nodes(m - 1, "lobatto")
    ts = (xs + 1.0) * th / 2.0
    tw = trapezoid_weights(ts, 0.0, th)
    if sampler.l1_y == "cell":
        edges = np.concatenate(([0.0], np.cumsum(tw)))
        edges[-1] = th
        ys = cell_means(y, step, edges)
    else:
        idx = np.clip((ts / step).astype(int), 0, len(y) - 1)
        ys = y[idx]
    a = cheb.chebvander(xs, ncoef - 1)
    if m == ncoef:
        coef = np.linalg.solve(a, ys)
    else:
        # vars: c (free, ncoef), e+ (m), e- (m), et (1)
        nv = ncoef + 2 * m + 1
        cost = np.concatenate((np.zeros(ncoef), np.ones(2 * m), [1.0]))
        a_eq = np.zeros((m + 1, nv))
        a_eq[:m, :ncoef] = a
        a_eq[:m, ncoef : ncoef + m] = np.eye(m)
        a_eq[:m, ncoef + m : ncoef + 2 * m] = -np.eye(m)
        a_eq[m, :ncoef] = tw @ a
        a_eq[m, -1] = -1.0
        b_eq = np.concatenate((ys, [float(tw @ ys)]))
        a_ub = b_ub = None
        if ys.min() >= 0.0:
            a_ub = np.zeros((m, nv))
            a_ub[:, :ncoef] = -a
            b_ub = np.zeros(m)
        bnds = [(None, None)] * ncoef + [(0.0, None)] * (2 * m + 1)
        res = linprog(
            cost,
            A_ub=a_ub,
            b_ub=b_ub,
            A_eq=a_eq,
            b_eq=b_eq,
            bounds=bnds,
            method="highs",
        )
        if not res.success:
            raise RuntimeError(f"L1 energy fit failed: {res.message}")
        coef = res.x[:ncoef]
    return np.asarray(cheb.chebval(sampler.nodes_x(), coef), dtype=float)


def daily_envelope(series: np.ndarray, step: float) -> np.ndarray:
    """Max over the window's days of each time of day (tiled)."""
    y = np.asarray(series, dtype=float)
    spd = int(round(24.0 / step))
    nd = len(y) // spd
    env = y[: nd * spd].reshape(nd, spd).max(axis=0)
    out = np.tile(env, nd + 1)[: len(y)]
    return out


def clearsky_nodes(
    sampler: ChebyshevSampler,
    series: np.ndarray,
    step: float,
    envelope: np.ndarray | None = None,
) -> np.ndarray:
    """Node values ``S(t_k)·k̂(t_k)`` of a solar-shaped trace.

    ``S`` is ``envelope`` (default :func:`daily_envelope`), exactly 0 at
    night; the clearness ``k = y / S`` is fitted with the sampler's
    polynomial method (night samples filled by linear interpolation of
    the daylight ``k`` — they are multiplied by 0) and clipped to
    ``[0, max k]``.  Energy restoration and night masking follow in
    :meth:`ChebyshevSampler.sample`."""
    y = np.asarray(series, dtype=float)
    env = daily_envelope(y, step) if envelope is None else np.asarray(envelope, float)
    day = env > 0.0
    k = np.zeros_like(y)
    k[day] = y[day] / env[day]
    idx = np.arange(len(y))
    if day.any() and not day.all():
        k[~day] = np.interp(idx[~day], idx[day], k[day])
    inner = replace(
        sampler, solar_basis="plain", solar_night="off", bound_mode="report"
    )
    g = inner.grid()
    kn = inner.sample(k, step, g, (0.0, max(float(k.max()), 1e-12)))
    kn = np.clip(kn, 0.0, max(float(k.max()), 0.0))
    j = np.clip((g.times / step).astype(int), 0, len(y) - 1)
    return np.asarray(env[j] * kn, dtype=float)


def constrain_coefficients(
    coef: np.ndarray,
    energy: float,
    window: float,
    odd_terms: str | int = "all",
    periodic: str | None = None,
) -> np.ndarray:
    """Coefficient-space ``odd_terms`` and ``periodic`` (+ energy).

    Odd coefficients beyond the first K (``odd_terms``) are zeroed; then
    the minimal change ``δ`` in the Chebyshev-weighted norm
    ``δᵀΓδ`` (``Γ = diag(π, π/2, …)``, kept columns only) satisfies
    ``R(c + δ) = r``: energy ``Σ m_k c_k·T/2 = E``, C0 ``p(1) = p(−1)``
    (``Σ_odd c_k = 0``) and C1 ``p′(1) = p′(−1)`` (``Σ_even k² c_k = 0``).
    Closed form ``δ = Γ⁻¹Rᵀ(RΓ⁻¹Rᵀ)⁻¹(r − Rc)``.
    """
    c = np.asarray(coef, dtype=float).copy()
    k = np.arange(len(c))
    keep = np.ones(len(c), dtype=bool)
    if odd_terms != "all":
        n_odd = 0 if odd_terms == "none" else int(odd_terms)
        odd = k[k % 2 == 1]
        keep[odd[n_odd:]] = False
        c[~keep] = 0.0
    rows = [chebyshev_moments(len(c) - 1) * window / 2.0]
    rhs = [energy]
    if periodic in ("C0", "C1") and np.any(keep & (k % 2 == 1)):
        rows.append((k % 2 == 1).astype(float))
        rhs.append(0.0)
    if periodic == "C1":
        rows.append(np.where(k % 2 == 0, k.astype(float) ** 2, 0.0))
        rhs.append(0.0)
    r = np.array(rows)[:, keep]
    ginv = np.full(len(c), 2.0 / np.pi)
    ginv[0] = 1.0 / np.pi
    gk = ginv[keep]
    lhs = (r * gk) @ r.T
    resid = np.array(rhs) - r @ c[keep]
    c[keep] += gk * (r.T @ np.linalg.lstsq(lhs, resid, rcond=None)[0])
    return np.asarray(c, dtype=float)


def fct_coefficients(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    series: np.ndarray,
    step: float,
    window: float,
    degree: int,
    points: int = 4096,
    antiderivative: bool = True,
) -> np.ndarray:
    """Chebyshev coefficients of the data by the fast Chebyshev transform.

    The exact antiderivative ``F(x) = ∫_{−1}^x f`` of the half-hourly step
    function (piecewise linear, continuous) is sampled at the ``points + 1``
    Chebyshev–Lobatto points ``x_j = cos(πj/M)``; DCT-I gives its
    interpolant's coefficients in O(M log M), ``chebder`` gives f's, and
    the series is truncated at ``degree``.  ``F(1) − F(−1)`` (the energy)
    is kept by the untruncated series; sampling F rather than f avoids
    aliasing the jumps.  ``antiderivative = False`` samples f itself
    (comparison).  Converges to the continuous projection as M grows
    (max coefficient difference ~5e-6 at M = 4096).
    """
    from scipy.fft import dct  # pylint: disable=import-outside-toplevel

    y = np.asarray(series, dtype=float)
    m = int(points)
    xj = np.cos(np.pi * np.arange(m + 1) / m)
    edges = 2.0 * np.arange(len(y) + 1) * step / window - 1.0
    if antiderivative:
        cum = np.concatenate(([0.0], np.cumsum(y * np.diff(edges))))
        vals = np.interp(xj, edges, cum)
    else:
        k = np.clip(np.searchsorted(edges, xj, side="right") - 1, 0, len(y) - 1)
        vals = y[k]
    c = dct(vals, type=1) / m
    c[0] /= 2.0
    c[-1] /= 2.0
    if antiderivative:
        c = cheb.chebder(c)
    out = np.zeros(degree + 1)
    nk = min(degree + 1, len(c))
    out[:nk] = c[:nk]
    return out


@lru_cache(maxsize=32)
def lsw_operator(
    n_samples: int, step: float, window: float, degree: int, mode: str = "lsw"
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(A, W, G⁺)`` of the cell LS fit ``c = G⁺ y`` (cached).

    ``A_jk`` = mean of ``T_k`` over data cell j in x (exact cell
    integral), ``W = diag(Δθ_j)`` the Chebyshev measure of each cell
    (``θ = arccos x``).

    * ``lsw``: ``G⁺ = Γ⁻¹ A_θᵀ W`` with ``A_θ`` the θ-means of
      ``cos kθ`` over the cells and ``Γ = diag(π, π/2, …)`` the EXACT
      Chebyshev Gram matrix — the generalized inverse of the
      step-function data in the weighted norm, identical to the
      continuous projection (``values = proj``) and stable for any N.
    * ``lswd``: discrete weighted normal equations ``(AᵀWA)⁻¹AᵀW``.
      Equispaced cells are too wide in θ near x = ±1 to resolve high
      modes, so ``AᵀWA`` is singular in practice (cond 7e3 at N = 64,
      1e10 at N = 96, 1e18 at N = 128).
    * ``lsu``: plain LS (``W = I``), cond(AᵀA) = cond(A)².

    Solved by ``solve`` / ``lstsq``, never an explicit inverse.
    """
    edges = 2.0 * np.arange(n_samples + 1) * step / window - 1.0
    a = _cell_mean_matrix_cached(n_samples, step, window, degree)
    theta = np.arccos(np.clip(edges, -1.0, 1.0))
    dth = theta[:-1] - theta[1:]
    if mode == "lsu":
        w = np.ones(n_samples)
        gplus = np.linalg.lstsq(a, np.eye(n_samples), rcond=None)[0]
    elif mode == "lswd":
        w = dth
        aw = a.T * w
        gplus = np.linalg.solve(aw @ a, aw)
    else:
        w = dth
        k = np.arange(1, degree + 1)[:, None]
        sk = np.sin(k * theta[None, :]) / k
        bt = np.vstack((dth[None, :], sk[:, :-1] - sk[:, 1:]))  # = A_θᵀ W
        gam = np.full(degree + 1, np.pi / 2.0)
        gam[0] = np.pi
        gplus = bt / gam[:, None]
    for arr in (w, gplus):
        arr.flags.writeable = False
    return a, w, gplus


def rescale_energy(
    coef: np.ndarray, energy: float, window: float, mixed_sign: bool = False
) -> tuple[np.ndarray, float, bool]:
    """Scale ``c`` so that ``∫p dt`` equals ``energy`` (multiplicative).

    Falls back to an additive change of ``c_0`` when the fitted energy is
    tiny or the data series has both signs (``mixed_sign``; a ratio
    would be meaningless).
    Returns ``(coef, factor, fallback)``."""
    mom = chebyshev_moments(len(coef) - 1) * window / 2.0
    e_fit = float(mom @ coef)
    if (
        not mixed_sign
        and abs(e_fit) > 1e-9 * max(abs(energy), 1.0)
        and e_fit * energy > 0
    ):
        factor = energy / e_fit
        return coef * factor, factor, False
    out = coef.copy()
    out[0] += (energy - e_fit) / window
    return out, 1.0, True


def chebyshev_projection(
    series: np.ndarray, step: float, window: float, degree: int
) -> tuple[np.ndarray, float]:
    """Orthogonal Chebyshev projection of the cell-mean step function.

    ``f`` is the piecewise-constant function of the samples (interval
    means).  With ``x = cos θ``, ``c_k = (2/π)∫_0^π f(cos θ) cos kθ dθ``
    (``1/π`` for ``k = 0``) is integrated EXACTLY per cell —
    ``∫ cos kθ = sin kθ / k`` — at cost O(M·N) and without any linear
    solve, so it is stable for every N.  Truncated at ``degree``, the
    projection does not keep ``∫ p dx = ∫ f dx``; the minimal change in
    the Chebyshev-weighted norm (``‖c‖² = π c_0² + π/2 Σ c_k²``) restores
    it along ``W⁻¹ m`` (``m_k = ∫T_k dx``).  Returns the coefficients and
    the correction size relative to ``‖c‖``.
    """
    y = np.asarray(series, dtype=float)
    edges = 2.0 * np.arange(len(y) + 1) * step / window - 1.0
    theta = np.arccos(np.clip(edges, -1.0, 1.0))  # decreasing
    k = np.arange(1, degree + 1)[:, None]
    sk = np.sin(k * theta[None, :]) / k  # (degree × M+1)
    coef = np.empty(degree + 1)
    coef[0] = float(y @ (theta[:-1] - theta[1:])) / np.pi
    coef[1:] = (2.0 / np.pi) * ((sk[:, :-1] - sk[:, 1:]) @ y)
    mom = chebyshev_moments(degree)
    target = float(np.sum(y) * step) * 2.0 / window  # ∫ f dx
    winv = np.full(degree + 1, 2.0 / np.pi)
    winv[0] = 1.0 / np.pi
    d = winv * mom
    delta = (target - mom @ coef) / float(mom @ d) * d
    corr = float(np.linalg.norm(delta) / max(np.linalg.norm(coef), 1e-300))
    return coef + delta, corr


def cell_mean_matrix(edges: np.ndarray, degree: int) -> np.ndarray:
    """``M[j, k]`` = mean of ``T_k`` over ``[edges[j], edges[j+1]]``."""
    out = np.empty((len(edges) - 1, degree + 1))
    dx = np.diff(edges)
    for k in range(degree + 1):
        e = np.zeros(k + 1)
        e[k] = 1.0
        prim = cheb.chebval(edges, cheb.chebint(e))
        out[:, k] = np.diff(prim) / dx
    return out


@lru_cache(maxsize=32)
def _cell_mean_matrix_cached(
    n_samples: int, step: float, window: float, degree: int
) -> np.ndarray:
    edges = 2.0 * np.arange(n_samples + 1) * step / window - 1.0
    out = cell_mean_matrix(edges, degree)
    out.flags.writeable = False
    return out


def _eq_lsq(v: np.ndarray, y: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """min ‖v c − y‖ s.t. a c = b — null-space method (QR of aᵀ)."""
    m = a.shape[0]
    q, r = np.linalg.qr(a.T, mode="complete")
    r1 = r[:m, :m]
    # particular solution in the range of aᵀ
    z1 = np.linalg.solve(r1.T, b)
    c0 = q[:, :m] @ z1
    n_basis = q[:, m:]
    z2, *_ = np.linalg.lstsq(v @ n_basis, y - v @ c0, rcond=None)
    return np.asarray(c0 + n_basis @ z2, dtype=float)


def _bounded_lsq(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    v: np.ndarray,
    y: np.ndarray,
    a: np.ndarray,
    b: np.ndarray,
    vn: np.ndarray,
    lo: float,
    hi: float,
    c0: np.ndarray,
) -> np.ndarray:
    """LS with equality rows and node bounds ``lo ≤ vn c ≤ hi`` (SLSQP)."""
    from scipy.optimize import minimize  # pylint: disable=import-outside-toplevel

    vtv = v.T @ v
    vty = v.T @ y
    cons = [
        {"type": "eq", "fun": lambda c: a @ c - b, "jac": lambda c: a},
        {"type": "ineq", "fun": lambda c: vn @ c - lo, "jac": lambda c: vn},
        {"type": "ineq", "fun": lambda c: hi - vn @ c, "jac": lambda c: -vn},
    ]
    res = minimize(
        lambda c: 0.5 * c @ vtv @ c - vty @ c,
        c0,
        jac=lambda c: vtv @ c - vty,
        constraints=cons,
        method="SLSQP",
        options={"maxiter": 500, "ftol": 1e-12},
    )
    return np.asarray(res.x)


def clip_rescale(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    values: np.ndarray,
    weights: np.ndarray,
    lo: float,
    hi: float,
    energy: float,
    frozen: np.ndarray | None = None,
) -> np.ndarray:
    """Clip to ``[lo, hi]`` and restore ``Σ w·v = energy`` on the headroom.

    The energy gap is spread proportionally to each node's remaining
    headroom (towards ``hi`` for a deficit, towards ``lo`` for a surplus),
    so the result stays inside the bounds whenever the energy is
    attainable.  ``frozen`` nodes (masked night / calm nodes) keep their
    value and get zero room.  Energy that cannot be restored (no room) is
    logged and added to ``GUARD_STATS.energy_lost``.
    """
    v = np.clip(values, lo, hi)
    w = np.asarray(weights, dtype=float)
    free = (
        np.ones(len(v), dtype=bool)
        if frozen is None
        else ~np.asarray(frozen, dtype=bool)
    )
    if frozen is not None:
        v[~free] = np.asarray(values, dtype=float)[~free]
    gap = energy - float(w @ v)
    if gap == 0.0:
        return v
    room = np.where(free, (hi - v) if gap > 0 else (v - lo), 0.0)
    if not np.all(np.isfinite(room[free])):
        # unbounded side: spread the gap uniformly on the free nodes
        wf = float(w[free].sum())
        if wf > 0.0:
            return np.asarray(v + np.where(free, gap / wf, 0.0), dtype=float)
        room = np.zeros(len(v))
    cap = float(w @ room)
    if cap <= 0.0 or abs(gap) > cap:
        lost = abs(gap) - max(cap, 0.0)
        GUARD_STATS.energy_lost += lost
        logger.warning(
            "range guard: %.6g of %.6g energy not restored (no room on the free nodes)",
            lost,
            abs(energy),
        )
        if cap <= 0.0:
            return v
    return np.asarray(v + np.sign(gap) * room * min(1.0, abs(gap) / cap), dtype=float)


# --- order selection (VRE-driven) ------------------------------------------


def degree_of(n: int, kind: str) -> int:
    """Polynomial degree of an N-node set (N − 1 Gauss, N Lobatto)."""
    return n - 1 if kind == "gauss" else n


def tail_rms(full: np.ndarray, degree: int) -> float:
    """Truncation-error estimate from the coefficient tail.

    Parseval in the Chebyshev norm: ``‖f − p_n‖² = π/2 Σ_{k>n} c_k²``;
    the window-mean square of ``T_k`` is ≈ 1/2, so the RMS over the
    window is ≈ ``sqrt(Σ_{k>n} c_k² / 2)`` (no evaluation needed)."""
    tail = np.asarray(full)[degree + 1 :]
    return float(np.sqrt(0.5 * np.sum(tail**2)))


def trace_errors(
    series: np.ndarray,
    step: float,
    degree: int,
    window: float = WEEK_HOURS,
    full: np.ndarray | None = None,
) -> tuple[float, float, float]:
    """(estimated RMS, sample RMS, hourly-ramp RMS) of the degree fit.

    The fit is the FCT truncation with the energy rescale (no clipping);
    the sample RMS compares cell means with the samples, the ramp RMS the
    hour-to-hour changes of the hourly means."""
    y = np.asarray(series, dtype=float)
    if full is None:
        full = fct_coefficients(y, step, window, 4095)
    c = np.zeros(degree + 1)
    nk = min(degree + 1, len(full))
    c[:nk] = full[:nk]
    energy = float(np.sum(y) * step)
    c, _, _ = rescale_energy(c, energy, window, bool(y.min() < 0.0 < y.max()))
    fit = _cell_mean_matrix_cached(len(y), step, window, degree) @ c
    dev = fit - y
    per_h = max(1, int(round(1.0 / step)))
    nh = len(y) // per_h
    fh = fit[: nh * per_h].reshape(nh, per_h).mean(axis=1)
    yh = y[: nh * per_h].reshape(nh, per_h).mean(axis=1)
    ramp = np.diff(fh) - np.diff(yh)
    return (
        tail_rms(full, degree),
        float(np.sqrt(np.mean(dev**2))),
        float(np.sqrt(np.mean(ramp**2))) if len(ramp) else 0.0,
    )


@dataclass
class OrderChoice:
    """Result of :func:`choose_order`."""

    n: int  #: chosen node count (the largest candidate if none meets tol)
    met: bool  #: whether ``tol`` was met
    #: per candidate N: (weighted estimate, weighted sample RMS, weighted
    #: ramp RMS, worst single-trace sample RMS) in capacity-factor units
    table: dict[int, tuple[float, float, float, float]]


def choose_order(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    traces: list[np.ndarray],
    capacities: list[float],
    step: float,
    tol: float = 0.03,
    kind: str = "lobatto",
    candidates: tuple[int, ...] = (32, 48, 64, 96, 128, 168, 224, 336),
    metric: Literal["rms", "ramp"] = "rms",
    window: float = WEEK_HOURS,
) -> OrderChoice:
    """Smallest N whose capacity-weighted VRE error is ≤ ``tol``.

    Errors are RMS of (fit − data) over the window divided by the trace's
    installed capacity (capacity-factor units), averaged with capacity
    weights; ``metric = "ramp"`` uses the hourly-ramp error instead
    (energy-weighted, important for storage / thermal ramping).  The
    coefficient-tail estimate screens the candidates; the chosen N is
    verified on the samples (and the next candidate tried if it fails).
    """
    caps = np.array([max(float(c), 1e-9) for c in capacities])
    fulls = [fct_coefficients(np.asarray(y, float), step, window, 4095) for y in traces]
    energy = np.array([max(float(np.sum(y) * step), 0.0) for y in traces])
    wr = caps / caps.sum()
    we = energy / energy.sum() if energy.sum() > 0 else wr
    table: dict[int, tuple[float, float, float, float]] = {}
    for n in candidates:
        deg = degree_of(n, kind)
        est = float(np.sum(wr * np.array([tail_rms(f, deg) for f in fulls]) / caps))
        errs = np.array(
            [
                trace_errors(y, step, deg, window, f)
                for y, f in zip(traces, fulls, strict=True)
            ]
        )
        rms = float(np.sum(wr * errs[:, 1] / caps))
        ramp = float(np.sum(we * errs[:, 2] / caps))
        worst = float(np.max(errs[:, 1] / caps)) if len(errs) else 0.0
        table[n] = (est, rms, ramp, worst)
        if (ramp if metric == "ramp" else rms) <= tol:
            return OrderChoice(n, True, table)
    return OrderChoice(candidates[-1], False, table)


# ---------------------------------------------------------------------------
# Nested Chebyshev refinement (sunrise / sunset)
# ---------------------------------------------------------------------------


def nested_weights(
    t_fine: np.ndarray, w_fine: np.ndarray, keep: np.ndarray
) -> np.ndarray:
    """Weights on the kept subset of a fine node set: the fine weights,
    each dropped node's weight split between its two kept neighbours by
    linear interpolation, (t_r − t_j, t_j − t_l)/(t_r − t_l).

    Local and positive (the fine Clenshaw–Curtis weights are positive);
    the sum is kept and the rule stays exact on linear functions (it is
    the fine rule applied to the piecewise-linear interpolant of the
    kept values at the dropped nodes)."""
    keep = np.asarray(keep, dtype=bool)
    if not (keep[0] and keep[-1]):
        raise ValueError("the end nodes must be kept")
    idx = np.flatnonzero(keep)
    w = np.zeros(len(t_fine))
    w[idx] = w_fine[idx]
    for j in np.flatnonzero(~keep):
        r = idx[np.searchsorted(idx, j)]
        lft = idx[np.searchsorted(idx, j) - 1]
        span = t_fine[r] - t_fine[lft]
        w[lft] += w_fine[j] * (t_fine[r] - t_fine[j]) / span
        w[r] += w_fine[j] * (t_fine[j] - t_fine[lft]) / span
    return np.asarray(w[idx], dtype=float)


@dataclass
class NestedChebyshevRefine:
    """Chebyshev–Gauss–Lobatto week of order N refined with the nested
    order-2N nodes inside the sunrise / sunset ramp windows.

    The 2N Lobatto set contains every N node (θ_k = πk/N are the even
    θ_j = πj/(2N)); the odd 2N nodes are the θ midpoints.  All N + 1
    nodes are kept plus the ``per_event`` odd nodes nearest to every
    ramp event (:func:`ramp_events`): a constant node count per stage.  Weights:
    :func:`nested_weights` on the 2N Clenshaw–Curtis weights (positive,
    sum = window, exact on linears; exact for degree ≤ 2N where no node
    is dropped).  Node values: the 2N sampler's values at the kept nodes
    (the same order-2N fit), so the windows see the finer polynomial and
    the rest of the week the same fit restricted to the N nodes."""

    base: ChebyshevSampler
    #: nested nodes added per event (the K odd 2N nodes nearest to it)
    per_event: int = 2
    name: str = "chebyshev_nested"
    point: bool = True
    window_start: tuple[int, float] = field(init=False)
    window_hours: float = field(init=False)

    def __post_init__(self) -> None:
        if self.base.kind != "lobatto" or self.base.durations != "quadrature":
            raise ValueError(
                "nested refinement needs Lobatto nodes, quadrature weights"
            )
        self.window_start = self.base.window_start
        self.window_hours = self.base.window_hours

    @property
    def fine(self) -> ChebyshevSampler:
        """The order-2N sampler whose nodes nest the base ones."""
        return replace(self.base, n=2 * self.base.n)

    def keep_mask(self, ref: np.ndarray, step: float) -> np.ndarray:
        """Kept nodes of the full 2N + 1 set: every even (order-N) node
        plus, per ramp event, the ``per_event`` odd nodes nearest to it
        (a constant count per stage; distinct across events)."""
        t = self.fine.full_grid().times
        keep = np.arange(len(t)) % 2 == 0
        odd = np.flatnonzero(~keep)
        if self.fine.drops_end_node:
            odd = odd[odd < len(t) - 1]
        for e in ramp_events(ref, step):
            free = odd[~keep[odd]]
            pick = free[np.argsort(np.abs(t[free] - e), kind="stable")]
            keep[pick[: self.per_event]] = True
        return np.asarray(keep, dtype=bool)

    def grid(self, ref: np.ndarray | None = None, step: float = 1.0) -> BlockGrid:
        if ref is None:
            raise ValueError("NestedChebyshevRefine needs the net-load reference")
        full = self.fine.full_grid()
        keep = self.keep_mask(ref, step)
        w = nested_weights(full.times, full.durations, keep)
        t = full.times[keep]
        if self.fine.drops_end_node:
            w = np.concatenate(([w[0] + w[-1]], w[1:-1]))
            t = t[:-1]
        return BlockGrid(w, t)

    def sample(
        self,
        series: np.ndarray,
        step: float,
        grid: BlockGrid,
        bounds: tuple[float, float] | None = None,
    ) -> np.ndarray:
        fine = self.fine
        vals = fine.sample(series, step, fine.grid(), bounds)
        t_all = fine.full_grid().times
        t_vals = t_all[: len(vals)]
        pick = np.searchsorted(t_vals, np.asarray(grid.times) - 1e-9)
        if not np.allclose(t_vals[pick], grid.times, atol=1e-9):
            raise ValueError("grid is not a subset of the nested 2N nodes")
        return np.asarray(vals[pick], dtype=float)
