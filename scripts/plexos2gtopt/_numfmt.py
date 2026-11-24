# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Exact (round-trip) number formatting for model text the converter writes."""

from __future__ import annotations


def fmt_exact(value: float) -> str:
    """Shortest text that parses back to exactly ``value``.

    ``f"{v:g}"`` keeps only 6 significant digits, which silently perturbs
    data-driven coefficients and RHS values: CEN's ``ANTUCOmin`` RHS
    (163.141898459795 MW) exhausts the Laja water budget down to the
    ELTORO / POLCURA end volume to ~1e-11, so the ``:g`` text ``163.142``
    makes the hard row infeasible.  Short values keep the compact ``:g``
    form (``1``, ``0.5``, ``268.8``); anything else uses ``repr``, the
    shortest round-trip representation.
    """
    text = f"{value:g}"
    return text if float(text) == value else repr(float(value))
