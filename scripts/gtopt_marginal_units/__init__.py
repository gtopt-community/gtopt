# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""gtopt_marginal_units — identify marginal generating units per
(scenario, stage, block) or (date, hour) cell, attribute the bus
LMP and emission intensity to those units via a deterministic
recipe table, and emit a parquet dataset that supports downstream
recomputation under alternative cost / emission catalogues.

Public API:
    cli                          — argparse entry point
    MarginalUnitDataset          — read-only consumer of the output dataset
"""

from __future__ import annotations

from gtopt_marginal_units.consumer import MarginalUnitDataset

__all__ = ["MarginalUnitDataset", "cli"]


def cli() -> int:
    """Console-script entry point. Returns process exit code."""
    from gtopt_marginal_units.main import cli as _cli  # noqa: PLC0415

    return _cli()
