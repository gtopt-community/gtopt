# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Critical-span selection, replicating Matus et al. (2012).

Reference: M. Matus, D. Saez, M. Favley, C. Suazo-Martinez, J. Moya,
G. Jimenez-Estevez, R. Palma-Behnke, G. Olguin and P. Jorquera,
"Identification of Critical Spans for Monitoring Systems in Dynamic
Thermal Rating", IEEE Trans. Power Delivery 27(2):1002-1009, April 2012,
doi:10.1109/TPWRD.2012.2185254.  Span ratings follow IEEE Std 738-2012
(doi:10.1109/IEEESTD.2013.6692858).

The line rating is the minimum of the span ratings, ``I_line(t) = min_s
I_s(t)``.  A monitoring (or forecasting) system can only observe a few
spans M, and estimates the line rating as ``min_{s in M} I_s(t)``.  The
method picks the spans whose minimum best estimates the line rating, with
the estimation quality measured by the Pearson correlation between the two
series (the paper's confidence level): starting from the empty set, add
the span that maximises the correlation, until the target correlation is
reached (or ``max_spans``).  ``max_spans = 1`` gives the single best
representative span.

The equidistant placement (n evenly spaced spans) is the benchmark the
paper compares against.  :func:`residual` reports the error of an
estimate.  Not in the paper, all optional: ``max_spans`` (a cap),
:func:`segment_errors` (per-segment selection), :func:`trace` (the full
rho(n) curve) and ``eta`` (a safety factor taken from the low quantile of
the true/estimate ratio).  ``eta`` is off unless asked for.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


def pearson(a: FloatArray, b: FloatArray) -> float:
    """Pearson correlation (0 for a constant series)."""
    sa, sb = float(np.std(a)), float(np.std(b))
    if sa == 0.0 or sb == 0.0:
        return 1.0 if np.allclose(a, b) else 0.0
    return float(np.mean((a - a.mean()) * (b - b.mean())) / (sa * sb))


@dataclass(frozen=True)
class Step:
    """One greedy step: the span added and the correlation reached."""

    span: int
    rho: float


def greedy(
    ratings: FloatArray,
    target_rho: float = 0.9,
    max_spans: int | None = None,
) -> list[Step]:
    """Greedy critical-span selection on an (hours, spans) rating matrix.

    Returns the steps (span column index and correlation after adding it).
    Stops when ``rho >= target_rho`` or at ``max_spans``.
    """
    if ratings.ndim != 2 or ratings.shape[1] == 0:
        raise ValueError("ratings must be (hours, spans) with >= 1 span")
    truth = ratings.min(axis=1)
    n = ratings.shape[1]
    limit = n if max_spans is None else min(max_spans, n)
    cur = np.full(ratings.shape[0], np.inf)
    chosen: list[Step] = []
    used: set[int] = set()
    while len(chosen) < limit:
        best, best_rho = -1, -np.inf
        for s in range(n):
            if s in used:
                continue
            r = pearson(np.minimum(cur, ratings[:, s]), truth)
            if r > best_rho + 1e-12:
                best, best_rho = s, r
        used.add(best)
        cur = np.minimum(cur, ratings[:, best])
        chosen.append(Step(best, best_rho))
        if best_rho >= target_rho:
            break
    return chosen


def equidistant(n_spans: int, n: int) -> list[int]:
    """``n`` evenly spaced span indices (centre of each of n segments)."""
    step = n_spans / n
    return [min(int(step * (k + 0.5)), n_spans - 1) for k in range(n)]


def equidistant_needed(ratings: FloatArray, target_rho: float) -> int:
    """Smallest n whose equidistant placement reaches ``target_rho``."""
    truth = ratings.min(axis=1)
    n_spans = ratings.shape[1]
    for n in range(1, n_spans + 1):
        est = ratings[:, equidistant(n_spans, n)].min(axis=1)
        if pearson(est, truth) >= target_rho:
            return n
    return n_spans


@dataclass(frozen=True)
class Residual:
    """Error of the estimate ``min over M`` against the line rating."""

    rho: float
    bias: float  # mean (estimate - truth), same unit as the ratings
    mae: float
    p_over: float  # share of hours where the estimate exceeds the truth
    ratio_median: float
    ratio_q: float  # quantile q of truth/estimate (the optional eta)


def residual(ratings: FloatArray, chosen: list[int], q: float = 0.05) -> Residual:
    """Estimation error of the spans ``chosen`` (column indices)."""
    truth = ratings.min(axis=1)
    est = ratings[:, chosen].min(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(est > 0, truth / est, 1.0)
    return Residual(
        rho=pearson(est, truth),
        bias=float(np.mean(est - truth)),
        mae=float(np.mean(np.abs(est - truth))),
        p_over=float(np.mean(est > truth + 1e-9)),
        ratio_median=float(np.median(ratio)),
        ratio_q=float(np.quantile(ratio, q)),
    )


def segment_errors(
    ratings: FloatArray,
    segments: list[list[int]],
    max_spans: int | None = None,
    target_rho: float = 0.9,
) -> list[tuple[list[int], Residual]]:
    """Run the selection on each segment (list of span columns) separately
    and report its error within the segment.

    Each segment stops at ``target_rho``; ``max_spans`` is an optional cap
    (``None`` = no cap, ``1`` = one representative span per segment)."""
    out = []
    for seg in segments:
        sub = ratings[:, seg]
        steps = greedy(sub, target_rho, max_spans)
        chosen = [s.span for s in steps]
        out.append(([seg[c] for c in chosen], residual(sub, chosen)))
    return out


@dataclass(frozen=True)
class TracePoint:
    """Quality of the greedy set with n spans and of n equidistant spans."""

    n: int
    span: int
    rho: float
    mae: float
    bias: float
    p_over: float
    rho_equidistant: float


def trace(
    ratings: FloatArray, until_rho: float = 0.99, max_n: int | None = 50
) -> list[TracePoint]:
    """The whole greedy curve rho(n), n = 1, 2, ... until ``until_rho`` (or
    ``max_n``), with the error metrics and the equidistant benchmark."""
    steps = greedy(ratings, until_rho, max_n)
    truth = ratings.min(axis=1)
    n_spans = ratings.shape[1]
    out = []
    for n in range(1, len(steps) + 1):
        r = residual(ratings, [s.span for s in steps[:n]])
        eq = ratings[:, equidistant(n_spans, n)].min(axis=1)
        out.append(
            TracePoint(
                n=n,
                span=steps[n - 1].span,
                rho=steps[n - 1].rho,
                mae=r.mae,
                bias=r.bias,
                p_over=r.p_over,
                rho_equidistant=pearson(eq, truth),
            )
        )
    return out


def n_needed(points: list[TracePoint], target_rho: float) -> int | None:
    """Smallest n of a trace reaching ``target_rho`` (None if never)."""
    return next((p.n for p in points if p.rho >= target_rho), None)


def plot_trace(
    points: list[TracePoint], path: str, cap: int | None = None, title: str = ""
) -> None:
    """PNG of rho(n) (greedy vs equidistant) and of n needed vs target rho."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError(
            "--plot needs matplotlib (pip install 'gtopt-scripts[plot]')"
        ) from exc

    n = [p.n for p in points]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4))
    a1.plot(n, [p.rho for p in points], "o-", label="critical spans (greedy)")
    a1.plot(n, [p.rho_equidistant for p in points], "s--", label="equidistant")
    a1.set_xlabel("number of spans n")
    a1.set_ylabel("Pearson rho (estimate vs line rating)")
    targets = np.linspace(0.7, 0.95, 26)
    a2.step(
        targets,
        [n_needed(points, t) or np.nan for t in targets],
        where="post",
        label="critical spans (greedy)",
    )
    eq_needed = [
        next((p.n for p in points if p.rho_equidistant >= t), np.nan) for t in targets
    ]
    a2.step(targets, eq_needed, "--", where="post", label="equidistant")
    a2.set_xlabel("target rho")
    a2.set_ylabel("spans needed")
    if cap is not None:
        a1.axvline(cap, color="grey", lw=0.8, ls=":")
        a2.axhline(cap, color="grey", lw=0.8, ls=":", label=f"cap n = {cap}")
    for ax in (a1, a2):
        ax.grid(alpha=0.3)
        ax.legend()
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
