# SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""cen2gtopt — fetch CEN's published real-operation data on demand
and write a canonical operation feed (gtopt_canonical_feed schema)
that gtopt_marginal_units --input-kind feed-parquet can consume
unchanged.

v1 is strictly an on-demand, single-shot CLI: no background loop,
no recurring runner, no streaming-append.
"""

from __future__ import annotations

__all__ = ["cli"]


def cli() -> int:
    """Console-script entry point. Returns process exit code."""
    from cen2gtopt.main import cli as _cli  # noqa: PLC0415

    return _cli()
