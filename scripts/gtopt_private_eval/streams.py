# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Stream-specific discounting of a project's cash flows (optional).

The base evaluation discounts every cash-flow component at one project
WACC.  The finance literature (Robichek & Myers 1966; Fama 1977;
Awerbuch 1993) discounts each stream at a rate matching its own
systematic risk instead: a contracted revenue near the cost of debt, a
merchant margin higher, a fuel bill near the riskless rate.  See
``docs/methods/private-investment-literature.md`` §6–§7.

Configuration (``finance.stream_rates``, per-project override
``projects.<name>.stream_rates`` merged on top; absent = off)::

    "stream_rates": {
      "method": "rate",            # or "certainty_equivalent"
      "rates": {"contract": 0.05, "energy": 0.09, "fuel": 0.03},
      "haircuts": {"energy": 0.15},  # certainty_equivalent only
      "risk_free": 0.03,           # certainty_equivalent only
      "map": {"energy": "contract"}  # component -> stream overrides
    }

* ``rate``: component ``c`` of stream ``s`` in year ``y`` is discounted
  at ``rates[s]`` (unlisted streams at the project WACC).  The market
  model may remap components (``MarketModel.stream_map``): ``cl`` nets
  fuel / VOM with the energy revenue and puts the fixed O&M with the
  capital.
* ``certainty_equivalent``: a revenue becomes ``v·(1 − h_s)``, a cost
  ``v·(1 + h_s)``, and every flow is discounted at ``risk_free``.
  Use a rate **or** a haircut for a stream, never both.

Capex (year 0) is not discounted.  With several rates the IRR of the
flows says nothing about the stream NPV, so the report gives the
**equivalent single rate**: the rate at which the single-rate NPV of the
same flows equals the stream NPV.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .finance import CashFlowResult, irr
from .params import deep_merge

#: default component → stream classification
DEFAULT_STREAM_OF: dict[str, str] = {
    # merchant energy at the nodal price
    "energy": "energy",
    "congestion_rent": "energy",
    "scarcity_excluded": "energy",
    # contracted / regulated revenue
    "cfd_difference": "contract",
    "regulated_avi": "contract",
    "regulated_coma": "contract",
    # capacity remuneration
    "capacity": "capacity",
    "sufficiency": "capacity",
    "ro_refund": "capacity",
    # ancillary services
    "sscc": "sscc",
    "reserves": "sscc",
    "co2": "co2",
    # fuel-like variable costs
    "variable": "fuel",
    "vom_extra": "fuel",
    "green_tax": "fuel",
    # fixed costs
    "fixed_om": "om",
    "insurance": "om",
    "warranty": "om",
    "guarantee_fee": "om",
    "cen_payment_guarantee": "om",
    "supply_contract_bond": "om",
    # network charges
    "transmission_injection": "charges",
    "withdrawal_charges": "charges",
}

METHODS = ("rate", "certainty_equivalent")

#: stream of the known fixed costs discounted with the capital: at the
#: project WACC (the annuity's rate) unless ``rates.capital`` is given
CAPITAL = "capital"


@dataclass
class StreamResult:
    """Stream-discounted NPV of one project."""

    method: str
    npv: float
    equivalent_rate: float | None
    #: stream -> {"rate", "haircut", "pv", "components"}
    streams: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "npv": self.npv,
            "equivalent_rate": self.equivalent_rate,
            "streams": self.streams,
            "warnings": list(self.warnings),
        }


def stream_config(
    finance: Mapping[str, Any], project: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Merged ``stream_rates`` config of a project, ``None`` when off."""
    base = finance.get("stream_rates") or {}
    over = project.get("stream_rates") or {}
    cfg = deep_merge(base, over) if over else dict(base)
    if not cfg or cfg.get("enabled") is False:
        return None
    if not (cfg.get("rates") or cfg.get("haircuts")):
        return None
    method = str(cfg.get("method", "rate"))
    if method not in METHODS:
        raise ValueError(
            f"stream_rates.method {method!r}: expected one of {', '.join(METHODS)}"
        )
    cfg["method"] = method
    return cfg


def classify(
    component: str,
    cfg: Mapping[str, Any],
    hedged: bool,
    market_map: Mapping[str, str] | None = None,
) -> str:
    """Stream of a cash-flow component.

    Precedence: the user ``map``, then the market model's ``stream_map``
    (e.g. ``cl`` nets the declared variable cost with the energy
    revenue), then :data:`DEFAULT_STREAM_OF`.  ``hedged``: the revenues
    include a CfD difference, so the energy sold at the nodal price plus
    the difference is the contracted strike revenue; whatever would be
    an ``energy`` stream (including fuel a market nets with energy:
    contract-indexed fuel) is then ``contract``, unless mapped by the
    user.
    """
    user = cfg.get("map") or {}
    if component in user:
        return str(user[component])
    stream = (market_map or {}).get(component) or DEFAULT_STREAM_OF.get(
        component, component
    )
    if hedged and stream == "energy":
        return "contract"
    return stream


def evaluate_streams(
    cash: CashFlowResult,
    cfg: Mapping[str, Any],
    *,
    risk_adjusted_prices: bool = False,
    market_map: Mapping[str, str] | None = None,
) -> StreamResult:
    """Stream NPV and equivalent single rate of ``cash`` under ``cfg``."""
    method = str(cfg.get("method", "rate"))
    rates = {str(k): float(v) for k, v in (cfg.get("rates") or {}).items()}
    haircuts = {str(k): float(v) for k, v in (cfg.get("haircuts") or {}).items()}
    rf = float(cfg.get("risk_free", 0.0) or 0.0)
    wacc = cash.wacc
    first = cash.years[0] if cash.years else None
    hedged = first is not None and "cfd_difference" in first.revenues
    res = StreamResult(method=method, npv=-cash.capex, equivalent_rate=None)

    def entry(stream: str) -> dict[str, Any]:
        if stream not in res.streams:
            if method == "rate":
                rate, cut = rates.get(stream, wacc), 0.0
            else:
                rate, cut = rf, haircuts.get(stream, 0.0)
            res.streams[stream] = {
                "rate": rate,
                "haircut": cut,
                "pv": 0.0,
                "components": [],
            }
        return res.streams[stream]

    for row in cash.years:
        for sign, items in ((1.0, row.revenues), (-1.0, row.costs)):
            for comp, value in items.items():
                e = entry(classify(comp, cfg, hedged, market_map))
                if comp not in e["components"]:
                    e["components"].append(comp)
                factor = 1.0 - sign * e["haircut"]
                pv = sign * value * factor / (1.0 + e["rate"]) ** row.year
                e["pv"] += pv
                res.npv += pv
    flows = cash.flows
    if flows:
        shifted = [flows[0] - res.npv, *flows[1:]]
        res.equivalent_rate = irr(shifted) if cash.capex > 0 else None
    res.warnings = _warnings(
        res, method, rates, haircuts, rf, wacc, cfg, risk_adjusted_prices
    )
    return res


def _warnings(
    res: StreamResult,
    method: str,
    rates: Mapping[str, float],
    haircuts: Mapping[str, float],
    rf: float,
    wacc: float,
    cfg: Mapping[str, Any],
    risk_adjusted_prices: bool,
) -> list[str]:
    out: list[str] = []
    if method == "certainty_equivalent" and rates:
        out.append(
            "certainty_equivalent ignores 'rates' (risk is in the haircuts; "
            "a rate and a haircut on one stream count risk twice)"
        )
    if method == "rate" and haircuts:
        out.append("method 'rate' ignores 'haircuts'")
    if method == "rate":
        merchant = rates.get("energy")
        unlisted = sorted(s for s in res.streams if s not in rates and s != CAPITAL)
        if merchant is not None and unlisted and wacc >= merchant - 1e-12:
            out.append(
                f"project WACC {wacc:.2%} is at or above the merchant energy "
                f"rate {merchant:.2%}: unlisted streams ({', '.join(unlisted)}) "
                "are discounted with merchant risk -- give them their own rate "
                "or use a contracted-level WACC"
            )
        contract = rates.get("contract")
        if contract is not None and merchant is not None and contract > merchant:
            out.append(
                f"contract rate {contract:.2%} above the merchant rate "
                f"{merchant:.2%}: a contracted stream is less risky"
            )
    if risk_adjusted_prices:
        if method == "rate":
            hi = [s for s, r in rates.items() if r > rf + 1e-12]
            if hi or any(s not in rates for s in res.streams):
                out.append(
                    "prices already carry risk aversion (finance."
                    "risk_adjusted_prices): discount at the risk-free rate, "
                    "a risk premium on top counts risk twice"
                )
        elif any(h > 0 for h in haircuts.values()):
            out.append(
                "prices already carry risk aversion (finance."
                "risk_adjusted_prices): haircuts count risk twice"
            )
    unused = sorted(set(cfg.get("map") or {}) - _components(res))
    if unused:
        out.append(f"stream_rates.map: unknown components {', '.join(unused)}")
    return out


def _components(res: StreamResult) -> set[str]:
    return {c for e in res.streams.values() for c in e["components"]}
