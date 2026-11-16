# SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Public re-export — see consumer.py docstring.

The user-facing import path documented in the master plan §4.10 is
``gtopt_marginal_units.recipes.MarginalUnitDataset``; this module
exposes that alias.
"""

from __future__ import annotations

from gtopt_marginal_units._consumer import MarginalUnitDataset

__all__ = ["MarginalUnitDataset"]
