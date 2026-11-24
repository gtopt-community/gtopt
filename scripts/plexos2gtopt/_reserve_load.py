"""``--reserve-load-factor auto``: the exogenous reserve load factor.

The native CPF requirement planes take their load term as a parameter,
``D = F x Σ demand lmax`` (``--reserve-load-term exogenous``), standing
in for PLEXOS's endogenous ``Generation_SEN`` (gross generation, BESS
charge excluded, the "demanda" of the ECFyDR regressions).  ``F`` is
therefore (expected gross generation − BESS charge) / demand: 1 +
transmission losses + whatever else the generation serves.  The default
stays a fixed 1.05; ``auto`` estimates it from the case itself:

1. PLEXOS solution (RES bundle) present: Σ ``Generation_SEN`` (the
   Decision Variable, GW → MW) over the converted hours, or, without it,
   Σ Generator ``Generation`` (on PCP 2026-11-07 day 1 the two agree
   within 0.03 %: 221.62 GWh vs 221.56 GWh; BESS are Battery objects,
   not Generators, so their charge is not in either), divided by
   Σ demand ``lmax`` over the same hours.
2. Otherwise the monthly table below, by the case month.

``MONTHLY_LOAD_FACTOR`` is Σ Generator Generation / (Σ node Load −
Σ battery Load) per calendar month of the CEN PCP history (dispatched
day of each run, ``pcp_hist`` database of the study/pcp-loss-eval
branch, 2024-01 .. 2026-11-22; node Load includes BESS charging, hence
the subtraction).  The ratio sat at 1.022-1.034 through 2025 and rose
to 1.035-1.046 from 2026-08 as BESS grew.  The lookup takes the case's
month in the latest year at or before the case year.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime

from .plexos_solution import PlexosSolution

logger = logging.getLogger(__name__)

AUTO = "auto"

#: (year, month) -> Σ generator generation / customer demand.
MONTHLY_LOAD_FACTOR: dict[tuple[int, int], float] = {
    (2024, 1): 1.0300,
    (2024, 2): 1.0279,
    (2024, 3): 1.0259,
    (2024, 4): 1.0253,
    (2024, 5): 1.0262,
    (2024, 6): 1.0318,
    (2024, 7): 1.0332,
    (2024, 8): 1.0331,
    (2024, 9): 1.0339,
    (2024, 10): 1.0344,
    (2024, 11): 1.0328,
    (2024, 12): 1.0315,
    (2025, 1): 1.0286,
    (2025, 2): 1.0255,
    (2025, 3): 1.0222,
    (2025, 4): 1.0222,
    (2025, 5): 1.0263,
    (2025, 6): 1.0247,
    (2025, 7): 1.0246,
    (2025, 8): 1.0256,
    (2025, 9): 1.0283,
    (2025, 10): 1.0273,
    (2025, 11): 1.0266,
    (2025, 12): 1.0267,
    (2026, 1): 1.0258,
    (2026, 2): 1.0290,
    (2026, 3): 1.0256,
    (2026, 4): 1.0228,
    (2026, 5): 1.0241,
    (2026, 6): 1.0236,
    (2026, 7): 1.0240,
    (2026, 8): 1.0354,
    (2026, 9): 1.0391,
    (2026, 10): 1.0429,
    (2026, 11): 1.0463,
}

#: Plausibility band for a solution-derived factor; outside it the
#: monthly table is used instead (warned).
_SANE = (0.9, 1.2)


def reserve_load_factor_arg(raw: str) -> float | str:
    """argparse ``type``: ``auto`` or a strictly positive float."""
    if raw.strip().lower() == AUTO:
        return AUTO
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected 'auto' or a number: {raw!r}"
        ) from exc
    if value <= 0.0:
        raise argparse.ArgumentTypeError(f"must be > 0: {raw!r}")
    return value


def monthly_load_factor(day: date) -> float:
    """Table factor for ``day``'s month (latest year <= ``day.year``)."""
    years = sorted(y for (y, m) in MONTHLY_LOAD_FACTOR if m == day.month)
    past = [y for y in years if y <= day.year]
    year = past[-1] if past else years[0]
    return MONTHLY_LOAD_FACTOR[(year, day.month)]


@dataclass(frozen=True)
class LoadFactorEstimate:
    """The factor used and where it came from (log + provenance)."""

    factor: float
    source: str
    generation_mwh: float | None = None
    demand_mwh: float | None = None
    hours: int | None = None

    def as_dict(self) -> dict[str, object]:
        """JSON-ready view."""
        return asdict(self)


def _solution_generation(solution: PlexosSolution, hours: int) -> tuple[float, str]:
    """Σ MWh of Generation_SEN (preferred) or of every Generator."""
    data = solution.fetch([("Decision Variable", "Value"), ("Generator", "Generation")])
    sen = [v for k, v in data.items() if k.object_name == "Generation_SEN"]
    if sen:
        total = solution.hourly_sum(sen[0], hours)
        # Generation_SEN is in GW in the CEN PCP (its constraints use
        # MW/GW coefficients); a value of tens of GW would read as MW.
        peak = max(sen[0].values(), default=0.0)
        return (total * 1000.0 if peak < 1000.0 else total), "plexos:Generation_SEN"
    gens = [
        v
        for k, v in data.items()
        if k.class_name == "Generator" and k.property_name == "Generation"
    ]
    return sum(solution.hourly_sum(v, hours) for v in gens), "plexos:generators"


def estimate_load_factor(
    demand_profiles: Iterable[Iterable[float]],
    *,
    hours: int,
    case_date: date | None,
    solution: PlexosSolution | None = None,
) -> LoadFactorEstimate:
    """Estimate ``F`` for the first ``hours`` hours of the case."""
    demand = sum(sum(list(p)[:hours]) for p in demand_profiles)
    if solution is not None and demand > 0.0:
        gen, source = _solution_generation(solution, hours)
        if gen > 0.0:
            factor = gen / demand
            if _SANE[0] <= factor <= _SANE[1]:
                return LoadFactorEstimate(factor, source, gen, demand, hours)
            logger.warning(
                "reserve load factor: solution ratio %.4f (%s) out of "
                "[%g, %g]; using the monthly table",
                factor,
                source,
                *_SANE,
            )
    if case_date is None:
        case_date = date.today()
        logger.warning(
            "reserve load factor: unknown case date; monthly table at %s",
            case_date,
        )
    return LoadFactorEstimate(
        monthly_load_factor(case_date),
        f"monthly-table:{case_date:%Y-%m}",
        None,
        demand or None,
        hours,
    )


def parse_case_date(raw: str | None) -> date | None:
    """``YYYY-MM-DD`` (or ``YYYYMMDD``) -> date; ``None`` when unparseable."""
    if not raw:
        return None
    text = str(raw).strip()
    for fmt, width in (("%Y-%m-%d", 10), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(text[:width], fmt).date()
        except ValueError:
            continue
    return None


def resolve_reserve_load_factor(
    options: Mapping[str, object],
    demand_profiles: Iterable[Iterable[float]],
    *,
    hours: int,
    case_date: date | None,
    solution: PlexosSolution | None,
) -> LoadFactorEstimate | None:
    """The ``auto`` estimate, or ``None`` when a number was given."""
    if str(options.get("reserve_load_factor", "")).lower() != AUTO:
        return None
    est = estimate_load_factor(
        demand_profiles, hours=hours, case_date=case_date, solution=solution
    )
    logger.info(
        "--reserve-load-factor auto: F = %.4f (%s%s)",
        est.factor,
        est.source,
        (
            f"; {est.generation_mwh:.0f} / {est.demand_mwh:.0f} MWh over {est.hours} h"
            if est.generation_mwh is not None and est.demand_mwh
            else ""
        ),
    )
    return est


__all__ = [
    "AUTO",
    "MONTHLY_LOAD_FACTOR",
    "LoadFactorEstimate",
    "estimate_load_factor",
    "monthly_load_factor",
    "parse_case_date",
    "reserve_load_factor_arg",
    "resolve_reserve_load_factor",
]
