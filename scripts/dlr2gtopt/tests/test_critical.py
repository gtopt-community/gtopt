# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Matus 2012 greedy critical-span selection."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

from dlr2gtopt import _critical as crit


def ratings(seed: int = 3, hours: int = 500, spans: int = 12) -> np.ndarray:
    """Spans share a common signal; span 4 is lowest most of the time and
    span 9 binds in a distinct regime (every 20th hour)."""
    rng = np.random.default_rng(seed)
    common = 700 + 150 * rng.standard_normal(hours)[:, None]
    m = common + 80 + 40 * rng.standard_normal((hours, spans))
    m[:, 4] = common[:, 0] + 5 * rng.standard_normal(hours)
    m[::20, 9] = common[::20, 0] - 60
    return m


def test_greedy_finds_binding_spans_in_order() -> None:
    m = ratings()
    steps = crit.greedy(m, target_rho=0.999)
    assert steps[0].span == 4
    assert steps[1].span == 9
    assert all(b.rho >= a.rho - 1e-12 for a, b in zip(steps, steps[1:]))


def test_greedy_max_spans_one_and_target() -> None:
    m = ratings()
    one = crit.greedy(m, target_rho=1.1, max_spans=1)
    assert [s.span for s in one] == [4]
    stop = crit.greedy(m, target_rho=0.5)
    assert len(stop) == 1
    full = crit.greedy(m, target_rho=2.0)
    assert len(full) == m.shape[1]
    with pytest.raises(ValueError):
        crit.greedy(np.zeros((3, 0)))


def test_pearson_edge_cases() -> None:
    a = np.array([1.0, 2.0, 3.0])
    assert crit.pearson(a, 2 * a) == pytest.approx(1.0)
    assert crit.pearson(np.ones(3), np.ones(3)) == 1.0
    assert crit.pearson(np.ones(3), a) == 0.0


def test_equidistant() -> None:
    assert crit.equidistant(199, 1) == [99]
    assert crit.equidistant(10, 5) == [1, 3, 5, 7, 9]
    assert crit.equidistant(3, 3) == [0, 1, 2]
    m = ratings()
    n = crit.equidistant_needed(m, 0.9)
    assert 1 <= n <= m.shape[1]
    assert crit.equidistant_needed(m, 2.0) == m.shape[1]


def test_residual_and_segments() -> None:
    m = ratings()
    perfect = crit.residual(m, list(range(m.shape[1])))
    assert perfect.rho == pytest.approx(1.0)
    assert perfect.mae == pytest.approx(0.0)
    assert perfect.p_over == 0.0
    r = crit.residual(m, [0])
    assert r.bias > 0 and r.p_over > 0.5 and r.ratio_q < 1.0
    segs = crit.segment_errors(m, [list(range(0, 6)), list(range(6, 12))], max_spans=1)
    assert segs[0][0] == [4]
    assert len(segs[1][0]) == 1 and segs[1][0][0] >= 6
    # default: no cap, each segment runs until target_rho
    halves = [list(range(0, 6)), list(range(6, 12))]
    free = crit.segment_errors(m, halves, target_rho=2.0)
    assert [len(sel) for sel, _ in free] == [6, 6]
    capped = crit.segment_errors(m, halves, max_spans=2, target_rho=2.0)
    assert [len(sel) for sel, _ in capped] == [2, 2]


def test_trace_and_n_needed() -> None:
    m = ratings()
    pts = crit.trace(m, until_rho=0.999)
    assert [p.span for p in pts[:2]] == [4, 9]
    assert pts[-1].rho >= 0.999 or len(pts) == m.shape[1]
    assert crit.n_needed(pts, 0.0) == 1
    assert crit.n_needed(pts, 2.0) is None
    assert pts[0].mae >= pts[-1].mae
    capped = crit.trace(m, until_rho=2.0, max_n=3)
    assert len(capped) == 3


def test_plot_trace_with_stub_matplotlib(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mpl = mock.MagicMock()
    fig, a1, a2 = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
    mpl.pyplot.subplots.return_value = (fig, (a1, a2))
    monkeypatch.setitem(sys.modules, "matplotlib", mpl)
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", mpl.pyplot)
    pts = crit.trace(ratings(), until_rho=0.99, max_n=5)
    crit.plot_trace(pts, str(tmp_path / "t.png"), cap=3, title="x")
    fig.savefig.assert_called_once()
    a1.axvline.assert_called_once()
    crit.plot_trace(pts, str(tmp_path / "t.png"))
    assert a1.axvline.call_count == 1


def test_plot_trace_without_matplotlib(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "matplotlib", None)
    with pytest.raises(RuntimeError, match="matplotlib"):
        crit.plot_trace([], "x.png")
