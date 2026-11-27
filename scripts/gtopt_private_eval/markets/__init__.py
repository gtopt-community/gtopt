# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Market models: how a project is paid.  Registry by name.

=====================  ===================================================
``energy_only``        nodal energy (+ reserve duals), scarcity included
``capacity_market``    + clearing price × derated capacity (PJM, GB, ...)
``reliability_option`` + premium − Σ max(λ − strike, 0)·derated MW
``cfd``                + two-sided CfD (strike − λ)·energy
``cl``                 Chile: CMg + potencia de suficiencia + SSCC − charges
=====================  ===================================================
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import Component, Components, EvalContext, MarketModel
from .capacity_market import CapacityMarket
from .cfd import ContractForDifference
from .cl import Chile
from .energy_only import EnergyOnly
from .reliability_option import ReliabilityOption

MARKETS: dict[str, type[MarketModel]] = {
    m.name: m
    for m in (
        EnergyOnly,
        CapacityMarket,
        ReliabilityOption,
        ContractForDifference,
        Chile,
    )
}


def get_market(name: str, params: Mapping[str, Any]) -> MarketModel:
    """Instantiate the market model ``name`` with its parameter block."""
    try:
        cls = MARKETS[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown market {name!r}; choose one of {', '.join(sorted(MARKETS))}"
        ) from exc
    return cls(params)


__all__ = [
    "MARKETS",
    "Component",
    "Components",
    "EvalContext",
    "MarketModel",
    "get_market",
]
