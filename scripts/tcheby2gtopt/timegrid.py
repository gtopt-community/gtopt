# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Time representations of a case: hourly blocks or Chebyshev point stages.

``block``: every stage of ``H`` hours becomes ``H`` blocks of 1 h
(``duration = 1``, no ``time``); a series takes its hourly value.

``point``: every stage becomes ONE point-sampled stage
(``"sampling": "point"``) on the ``N + 1`` Chebyshev–Lobatto nodes of
``[0, H]`` (``t_0`` = stage start, ``t_N`` = stage end; ``Block.time`` =
``t_k``, ``Block.duration`` = the Clenshaw–Curtis weight ``w_k``, Σ w = H).
The grid comes from :class:`plexos2gtopt.week_sampler.ChebyshevSampler`
(``kind="lobatto"``, ``durations="quadrature"``).  Node values of an
hourly series use one of:

* ``wcell`` (default, ``ChebyshevSampler(values="wcell")``): the mean of
  the hourly step series over the node's quadrature cell (cells of
  length ``w_k`` partition ``[0, H]`` in node order).  Exact energy
  (Σ w_k v_k = Σ_h y_h) and values inside the data range by construction.
* ``interp``: linear interpolation of the hourly values (placed at the
  hour midpoints) at ``t_k`` (``ChebyshevSampler(values="interp")``) —
  the energy is NOT conserved for non-linear series.
* analytic: :meth:`TimeGrid.sample_function` evaluates a callable at the
  node times (point) or averages it over each hour (block, 8-point
  Gauss–Legendre per hour).  Used by the hand-checkable mini cases.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from numpy.typing import ArrayLike

from plexos2gtopt.week_sampler import BlockGrid, ChebyshevSampler

Mode = Literal["block", "point"]
PointValues = Literal["wcell", "interp"]


@dataclass(frozen=True)
class StageGrid:
    """Blocks of one stage (times relative to the stage start)."""

    hours: float  #: real length of the stage [h]
    durations: np.ndarray  #: block durations (point: CC weights) [h]
    times: np.ndarray | None  #: node times (point stages) [h]

    @property
    def n(self) -> int:
        return len(self.durations)


def lobatto_grid(n: int, hours: float, periodic: bool = False) -> BlockGrid:
    """Chebyshev–Lobatto nodes (``n + 1``) and CC weights on ``[0, hours]``;
    ``periodic``: ``n`` nodes (node N ≡ node 0 dropped, weight w_0 + w_N)."""
    return _sampler(n, hours, periodic).grid()


def _sampler(
    n: int, hours: float, periodic: bool, values: PointValues = "wcell"
) -> ChebyshevSampler:
    return ChebyshevSampler(
        n=n,
        kind="lobatto",
        window_hours=hours,
        durations="quadrature",
        values=values,
        periodic="C0" if periodic else None,
    )


@dataclass
class TimeGrid:
    """Stage/block layout of a case in one time representation."""

    mode: Mode
    stage_hours: list[float]
    nodes: int = 24  #: Lobatto ``N`` (``N + 1`` nodes) of point stages
    point_values: PointValues = "wcell"
    #: periodic point stages: ``N`` nodes (node N ≡ node 0 dropped)
    periodic: bool = False
    stages: list[StageGrid] = field(init=False)

    def __post_init__(self) -> None:
        self.stages = []
        for h in self.stage_hours:
            if self.mode == "block":
                nb = int(round(h))
                if abs(nb - h) > 1e-9:
                    raise ValueError(f"block stages need whole hours, got {h}")
                self.stages.append(StageGrid(float(h), np.ones(nb), None))
            else:
                g = lobatto_grid(self.nodes, float(h), self.periodic)
                self.stages.append(StageGrid(float(h), g.durations, g.times))

    # -- JSON ---------------------------------------------------------------
    def simulation(self) -> dict[str, Any]:
        """The gtopt ``simulation`` object (one scenario, no phases)."""
        blocks: list[dict[str, Any]] = []
        stages: list[dict[str, Any]] = []
        for s, st in enumerate(self.stages):
            first = len(blocks)
            for k in range(st.n):
                b: dict[str, Any] = {
                    "uid": len(blocks) + 1,
                    "duration": _r(st.durations[k]),
                }
                if st.times is not None:
                    b["time"] = _r(st.times[k])
                blocks.append(b)
            stage: dict[str, Any] = {
                "uid": s + 1,
                "first_block": first,
                "count_block": st.n,
                "chronological": True,
            }
            if self.mode == "point":
                stage["sampling"] = "point"
            stages.append(stage)
        return {
            "block_array": blocks,
            "stage_array": stages,
            "scenario_array": [{"uid": 1, "probability_factor": 1}],
        }

    # -- sampling -----------------------------------------------------------
    def sample_hourly(self, stage: int, series: ArrayLike) -> np.ndarray:
        """Block values of an hourly series of one stage."""
        y = np.asarray(series, dtype=float)
        st = self.stages[stage]
        if len(y) != int(round(st.hours)):
            raise ValueError(f"stage {stage}: expected {st.hours} h, got {len(y)}")
        if self.mode == "block":
            return y.copy()
        if np.all(y == y[0]):
            return np.full(st.n, y[0])
        sampler = _sampler(self.nodes, st.hours, self.periodic, self.point_values)
        assert st.times is not None
        return np.asarray(
            sampler.sample(y, 1.0, BlockGrid(st.durations, st.times)), dtype=float
        )

    def sample_function(
        self, stage: int, func: Callable[[np.ndarray], np.ndarray]
    ) -> np.ndarray:
        """Block values of an analytic function of the stage time ``t`` [h]:
        hourly cell means (block) or values at the nodes (point)."""
        st = self.stages[stage]
        if self.mode == "point":
            assert st.times is not None
            return np.asarray(func(st.times), dtype=float)
        xg, wg = np.polynomial.legendre.leggauss(8)
        starts = np.arange(st.n, dtype=float)
        t = starts[:, None] + 0.5 * (xg[None, :] + 1.0)
        return (np.asarray(func(t), dtype=float) * wg[None, :]).sum(axis=1) * 0.5

    def per_stage(self, values: Sequence[ArrayLike]) -> Any:
        """``[stage][block]`` list; a scalar when everything is equal."""
        arrays = [np.asarray(v, dtype=float) for v in values]
        flat = np.concatenate(arrays)
        if flat.size and np.all(flat == flat[0]):
            return _r(flat[0], 12)
        return [[_r(x, 12) for x in a] for a in arrays]


def _r(x: float, digits: int = 15) -> float:
    """Round for JSON (``digits`` significant digits; whole numbers are
    written as ints).  Grids keep 15 digits, data values 12 (drops the
    1e-14 noise of the cell means)."""
    v = float(f"{float(x):.{digits}g}")
    return int(v) if v.is_integer() and abs(v) < 1e15 else v
