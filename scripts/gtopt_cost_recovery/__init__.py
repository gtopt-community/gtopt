# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""gtopt_cost_recovery — does the optimal plan pay for itself?

Reads a solved gtopt case (``planning.json`` + the output streams written
with ``--write-out all``) and, for every expandable project (Generator,
Line, Battery with its power axis), reconciles the money at the marginal
prices of the solution:

* **revenue** ``Σ λ·q·Δ`` — energy sales of a generator, congestion rent
  ``(λ_b − λ_a)·f`` of a line, arbitrage ``λ·(discharge − charge)`` of a
  battery;
* **opex** ``Σ c·q·Δ`` (generation / discharge / charge / transfer cost);
* **capex** — the annualised investment the LP charged for the NEW
  capacity (``capacost`` × stage hours × discount);
* **profit** ``= (revenue − opex)·new/installed − capex``.

In an LP every project built strictly inside its bounds has profit 0 (the
capacity dual pays its annuity: complementary slackness); a candidate not
built has a marginal value per unit (price-taker margin
``Σ max(λ − c, 0)·Δ``, line spread ``Σ |λ_b − λ_a|·Δ``) not above its
cost per unit.  With integer modules (MIP) the fixed-integer prices
leave a profit gap; ``make_whole = max(0, −profit)`` is the uplift a built project needs.

All money is in the objective's currency, probability-weighted and
discounted with the stage discount factors (the duals gtopt writes are
unfolded to undiscounted $/MWh); ``per_year=True`` rescales a
representative horizon to one year (× 8760 / horizon hours).
"""

from .main import main  # noqa: F401
from .recovery import (  # noqa: F401
    MarketBalance,
    ProjectRecovery,
    RecoveryReport,
    annual_capcost,
    capital_recovery_factor,
    compute_recovery,
)
