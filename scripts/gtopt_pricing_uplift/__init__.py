# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""gtopt_pricing_uplift — uplift metrics of a solved unit-commitment case.

Scores a pricing method (``model_options.pricing_mode``: ``fixed``,
``relaxed``, ``fast_start``) by the side payments its prices require.  For
every committable unit (a generator with a ``Commitment``) it reads the MIP
dispatch, the commitment and the nodal prices of a solved gtopt run and
computes:

* **make-whole payment** ``MWP_g = max(0, -π_g)`` with the actual profit
  ``π_g = Σ_t (λ_{n(g),t} - c_{g,t})·g_t·Δt - Σ_t no-load_g·u_t·Δt
  - Σ_t (SU_g·v_t + SD_g·w_t)``;
* **lost-opportunity cost** ``LOC_g = max(0, π*_g - (π_g + MWP_g))`` with
  ``π*_g`` the profit of a price-taking self-schedule over the unit's own
  constraints (pmin / pmax when on, min up / down, initial state,
  must-run), found by dynamic programming;
* **total uplift** ``MWP_g + LOC_g`` (``= π*_g - π_g`` whenever the unit
  can always earn ``≥ 0`` by staying off).  Convex-hull pricing minimises
  the total.

It also reports price statistics: node-hours with ``λ < 0`` and
``λ < -0.1``, mean / min / max and volatility of the reference-bus price,
and the congestion rent ``Σ_l Σ_t (λ_b - λ_a)·f_l·Δt``.
"""

from .main import main  # noqa: F401
from .uplift import (  # noqa: F401
    PriceStats,
    UnitUplift,
    UpliftReport,
    compute_uplift,
)
from ._selfsched import SelfScheduleUnit, self_schedule_profit  # noqa: F401
