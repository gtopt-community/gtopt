# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""gtopt_private_eval — would a private investor build the plan?

Reads a solved gtopt expansion run and evaluates, from a private
investor's point of view, every project the plan selected: yearly
revenues under a **market model** (``energy_only``, ``capacity_market``,
``reliability_option``, ``cfd``, ``cl`` — Chile), operating costs (fuel
and VOM as modelled, fixed O&M as % of capex, market charges), EBITDA,
depreciation and EBIT, and the **pre-tax** NPV / IRR / payback / LCOE at
the project's private WACC, next to gtopt's planning view (annuity and
margin from ``gtopt_cost_recovery``).  See
``docs/methods/private-investor-evaluation.md``.
"""

from .case_data import ProjectData, SystemData, load_system  # noqa: F401
from .evaluate import EvalReport, ProjectEvaluation, evaluate  # noqa: F401
from .finance import build_cash_flows, crf, irr, npv, payback  # noqa: F401
from .main import main  # noqa: F401
from .markets import MARKETS, MarketModel, get_market  # noqa: F401
from .params import Params, load_config  # noqa: F401
