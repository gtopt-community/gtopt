# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Private CVaR: investor risk across scenarios (hydrologies), ex post.

The verification step of a risk-aware plan.  The plan may be solved
with a *social* CVaR (a planner objective inside the LP); each project
is then checked with the *private* CVaR of its own NPV: does it pass at
the expectation (E[NPV] ≥ 0) and under the private CVaR (NPV CVaR ≥ 0)?
A project that fails the private check carries unpaid private risk: it
needs a contract, a sufficiency / capacity payment or regulated
remuneration to be financed.  Scenario weights are the probabilities of
the gtopt case (``probability_factor``), read as they are: a plan solved
with a social CVaR has risk-adjusted duals, but the private profits are
weighted with the real probabilities.

The base evaluation collapses the scenarios of the solved case into one
probability-weighted year.  This module evaluates each scenario on its
own (every year of the life sees that scenario's prices and dispatch)
and reports, per project:

* the **expectation** of the NPV (Σ p_s NPV_s) and the IRR of the
  expected flows;
* the **CVaR** of the NPV over the tail of mass ``alpha``;
* the **trimmed mean**: the expectation without the ``trim`` tail
  scenarios.

Configuration (``risk``; per project ``projects.<name>.risk`` merged on
top)::

    "risk": {"mode": "cvar",    # none (default) | cvar | trim
             "alpha": 0.2,      # tail mass of the CVaR
             "trim": 1,         # scenarios dropped by the trimmed mean
             "tail": "auto",    # auto | dry | wet | low_profit
             "hydrology_index": {"1": 0.7, "2": 1.3}}  # optional

Which hydrologies are bad for a project is not known a priori: a dry
year means less water but a higher CMg, so a hydro plant's revenue can
rise or fall (volume × price); the same holds, with other signs, for
every technology.  ``auto`` (= ``low_profit``) therefore ranks the
scenarios by the project's own simulated NPV: the tail is its
low-profit scenarios.  ``dry`` / ``wet`` are explicit user choices that
rank by a hydrology (wetness) index instead -- the user's
``hydrology_index`` ({scenario uid: value}, higher = wetter), else the
energy of the case's hydro units in each scenario; without an index
they fall back to ``low_profit`` with a note.  The investor's tail
(low profit) and a regulator's (supply shortage) need not coincide.

This ex-post investor measure is separate from a planner's CVaR inside
the LP.  Combined with risk premia in ``finance.stream_rates`` it counts
the same hydrology risk twice; the report warns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .params import deep_merge

MODES = ("none", "cvar", "trim")
TAILS = ("auto", "dry", "wet", "low_profit")


def risk_config(
    cfg: Mapping[str, Any], project: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Merged ``risk`` config of a project, ``None`` when off."""
    base = cfg.get("risk") or {}
    over = project.get("risk") or {}
    merged = deep_merge(base, over) if over else dict(base)
    mode = str(merged.get("mode", "none"))
    if mode not in MODES:
        raise ValueError(f"risk.mode {mode!r}: expected one of {', '.join(MODES)}")
    if mode == "none":
        return None
    tail = str(merged.get("tail", "auto"))
    if tail not in TAILS:
        raise ValueError(f"risk.tail {tail!r}: expected one of {', '.join(TAILS)}")
    alpha = float(merged.get("alpha", 0.2))
    if not 0.0 < alpha <= 1.0:
        raise ValueError(f"risk.alpha {alpha}: expected 0 < alpha <= 1")
    trim = int(merged.get("trim", 1))
    if trim < 0:
        raise ValueError(f"risk.trim {trim}: expected >= 0")
    merged.update(mode=mode, tail=tail, alpha=alpha, trim=trim)
    return merged


@dataclass
class RiskResult:
    """Risk measures of one project's NPV across scenarios."""

    mode: str
    tail: str  #: tail actually used (auto / fallback resolved)
    alpha: float
    trim: int
    expectation: float
    irr: float | None  #: IRR of the expected flows
    cvar: float
    trimmed_mean: float
    #: scenario uid → {"probability", "npv", "irr", "index"}
    scenarios: dict[int, dict[str, Any]] = field(default_factory=dict)
    #: scenarios worst first (the tail order used)
    order: list[int] = field(default_factory=list)
    tail_scenarios: list[int] = field(default_factory=list)
    trimmed_scenarios: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passes_expectation(self) -> bool:
        """Expected NPV ≥ 0: viable for a risk-neutral investor."""
        return self.expectation >= 0.0

    @property
    def passes_cvar(self) -> bool:
        """Private-CVaR check: NPV CVaR ≥ 0 over the low-profit tail."""
        return self.cvar >= 0.0

    @property
    def headline(self) -> float:
        """The NPV of the chosen mode."""
        return self.cvar if self.mode == "cvar" else self.trimmed_mean

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "tail": self.tail,
            "alpha": self.alpha,
            "trim": self.trim,
            "expectation": self.expectation,
            "irr": self.irr,
            "cvar": self.cvar,
            "trimmed_mean": self.trimmed_mean,
            "headline": self.headline,
            "passes_expectation": self.passes_expectation,
            "passes_cvar": self.passes_cvar,
            "scenarios": {str(k): v for k, v in self.scenarios.items()},
            "order": self.order,
            "tail_scenarios": self.tail_scenarios,
            "trimmed_scenarios": self.trimmed_scenarios,
            "notes": list(self.notes),
        }


def tail_order(
    npvs: Mapping[int, float],
    tail: str,
    index: Mapping[int, float] | None,
) -> list[int]:
    """Scenarios worst first: by NPV (low_profit), wetness (dry: driest
    first; wet: wettest first)."""
    if tail in ("dry", "wet") and index is not None:
        sign = 1.0 if tail == "dry" else -1.0
        return sorted(npvs, key=lambda s: (sign * index.get(s, 0.0), npvs[s]))
    return sorted(npvs, key=lambda s: (npvs[s], s))


def cvar(
    order: Sequence[int],
    probs: Mapping[int, float],
    values: Mapping[int, float],
    alpha: float,
) -> tuple[float, list[int]]:
    """Mean of ``values`` over the first ``alpha`` probability mass of
    ``order`` (the last scenario counted partially)."""
    mass = 0.0
    acc = 0.0
    used: list[int] = []
    for s in order:
        take = min(probs[s], alpha - mass)
        if take <= 1e-15:
            break
        acc += take * values[s]
        mass += take
        used.append(s)
    return (acc / mass if mass > 0.0 else 0.0), used


def trimmed_mean(
    order: Sequence[int],
    probs: Mapping[int, float],
    values: Mapping[int, float],
    trim: int,
) -> tuple[float, list[int]]:
    """Expectation without the first ``trim`` scenarios of ``order``."""
    cut = list(order[: min(trim, max(len(order) - 1, 0))])
    keep = [s for s in order if s not in cut]
    mass = sum(probs[s] for s in keep)
    return sum(probs[s] * values[s] for s in keep) / mass, cut


def measure(
    cfg: Mapping[str, Any],
    probs: Mapping[int, float],
    npvs: Mapping[int, float],
    irrs: Mapping[int, float | None],
    expected_irr: float | None,
    index: Mapping[int, float] | None,
) -> RiskResult:
    """Risk measures of one project from its per-scenario NPVs."""
    tail = str(cfg["tail"])
    notes: list[str] = []
    if tail == "auto":
        tail = "low_profit"
    if tail in ("dry", "wet") and index is None:
        notes.append(
            f"tail {tail}: no hydrology index (risk.hydrology_index or hydro"
            " units in the case); ranked by the project's own profit"
        )
        tail = "low_profit"
    if len(npvs) == 1:
        notes.append("one scenario: the risk measures equal the expectation")
    order = tail_order(npvs, tail, index)
    expectation = sum(probs[s] * npvs[s] for s in npvs)
    cv, used = cvar(order, probs, npvs, float(cfg["alpha"]))
    tm, cut = trimmed_mean(order, probs, npvs, int(cfg["trim"]))
    return RiskResult(
        mode=str(cfg["mode"]),
        tail=tail,
        alpha=float(cfg["alpha"]),
        trim=int(cfg["trim"]),
        expectation=expectation,
        irr=expected_irr,
        cvar=cv,
        trimmed_mean=tm,
        scenarios={
            s: {
                "probability": probs[s],
                "npv": npvs[s],
                "irr": irrs.get(s),
                "index": None if index is None else index.get(s),
            }
            for s in sorted(npvs)
        },
        order=order,
        tail_scenarios=used,
        trimmed_scenarios=cut,
        notes=notes,
    )
