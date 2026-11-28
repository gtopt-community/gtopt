# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""SSCC wear cost θ per technology, service and direction from two runs.

Run A is the case **without** SSCC (no reserve requirements, or reserves at
zero cost) and run B the same case **with** SSCC.  Both are aligned by
element name and scenario / stage / block position
(:class:`gtopt_compare_runs.Run`).  For every unit the whole-horizon usage
is turned into a maintenance cost (:func:`~gtopt_usage.maintenance_table`)
and::

    ΔC_g      = C_g(B) − C_g(A)                     maintenance-cost delta
    ΔC_tech   = Σ_{g ∈ tech} ΔC_g
    ΔR_tech,p = R_tech,p(B) − R_tech,p(A)            reserve MW·h of product p
    θ_tech,p  = ΔC_tech · w_p / Σ_p' w_p' · ΔR_tech,p'   [$/MW·h]

with one product ``p = (service, direction)`` per SSCC product (CPF, CSF,
CTF, each up and down: six rows per technology when all exist), so that
``Σ_p θ_tech,p · ΔR_tech,p = ΔC_tech`` (the delta is fully allocated).
``w_p`` are the ``service_weights`` of the parameter file, per direction
(``{"cpf": {"up": 2, "dn": 1}}``) or per service, default 1 (the same θ
for every product of a technology).  One delta cannot tell the products
apart; a θ that really differs per product needs either weights or one B
run per product, each compared with A.

The known variable reserve costs gtopt writes per provision (part-load,
activation-energy and degradation adders, see
:func:`~gtopt_usage.accounting.known_cost_table`) are reported next to it as
``θ_known = Σ $ / R(B)``, and ``theta_total = theta_known + theta_maintenance``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from gtopt_compare_runs.reader import Run
from gtopt_usage.accounting import (
    HOURS_PER_YEAR,
    KNOWN_COST_TERMS,
    USAGE_COLUMNS,
    known_cost_table,
    reserve_table,
    usage_table,
)
from gtopt_usage.maintenance import ParamSet, maintenance_table

_SUM_COLS = [
    c
    for c in USAGE_COLUMNS
    if c not in ("name", "kind", "technology", "k", "stage", "year")
]


@dataclass
class ThetaReport:
    """Per-unit and per-technology deltas and the θ table."""

    units: pd.DataFrame
    technologies: pd.DataFrame
    theta: pd.DataFrame

    def write_csv(self, directory: str | Path) -> list[Path]:
        """Write ``units.csv``, ``technologies.csv`` and ``theta.csv``."""
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        paths = []
        for name, df in (
            ("units", self.units),
            ("technologies", self.technologies),
            ("theta", self.theta),
        ):
            path = out / f"{name}.csv"
            df.to_csv(path, index=False)
            paths.append(path)
        return paths

    def to_dict(self) -> dict[str, list[dict[str, object]]]:
        """JSON-ready records of the three tables."""

        def records(df: pd.DataFrame) -> list[dict[str, object]]:
            clean = df.astype(object).where(pd.notna(df), None)
            return [dict(r) for r in clean.to_dict("records")]

        return {
            "units": records(self.units),
            "technologies": records(self.technologies),
            "theta": records(self.theta),
        }

    def write_json(self, path: str | Path) -> Path:
        """Write :meth:`to_dict` as JSON."""
        p = Path(path)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return p


def horizon_usage(usage: pd.DataFrame) -> pd.DataFrame:
    """Sum a usage table over all stages (``k = -1``)."""
    if usage.empty:
        return usage
    out = usage.groupby(["name", "kind", "technology"], as_index=False)[_SUM_COLS].sum()
    out["k"] = -1
    return out


def horizon_reserve(reserve: pd.DataFrame) -> pd.DataFrame:
    """Sum a reserve table over all stages (``k = -1``)."""
    if reserve.empty:
        return reserve
    keys = ["name", "technology", "provision", "service", "direction"]
    out = reserve.groupby(keys, as_index=False)["mwh"].sum()
    out["k"] = -1
    return out


def _unit_maintenance(
    run: Run, params: ParamSet, pmin_band: float | None, hours_per_year: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    usage = horizon_usage(
        usage_table(run, hours_per_year=hours_per_year, pmin_band=pmin_band)
    )
    reserve = horizon_reserve(reserve_table(run, hours_per_year=hours_per_year))
    return maintenance_table(usage, params, reserve, hours_per_year), reserve


def derive_theta(
    base: Run,
    sscc: Run,
    params: ParamSet,
    *,
    pmin_band: float | None = None,
    hours_per_year: float = HOURS_PER_YEAR,
) -> ThetaReport:
    """θ per technology, service and direction from ``base`` (A) and ``sscc`` (B)."""
    ma, ra = _unit_maintenance(base, params, pmin_band, hours_per_year)
    mb, rb = _unit_maintenance(sscc, params, pmin_band, hours_per_year)

    cols = [
        "hours",
        "starts",
        "pmin_hours",
        "ramp_mw",
        "efc",
        "eoh",
        "events",
        "cost",
        "outage_hours",
    ]
    key = ["name", "technology"]
    left = (
        ma[key + cols] if not ma.empty else pd.DataFrame(columns=key + cols)
    ).set_index(key)
    right = (
        mb[key + cols] if not mb.empty else pd.DataFrame(columns=key + cols)
    ).set_index(key)
    units = left.join(right, how="outer", lsuffix="_a", rsuffix="_b")
    units = units.apply(pd.to_numeric, errors="coerce").fillna(0.0)
    for c in cols:
        units[f"delta_{c}"] = units[f"{c}_b"] - units[f"{c}_a"]
    units = units.reset_index()

    techs = (
        units.groupby("technology", as_index=False)[
            ["cost_a", "cost_b", "delta_cost", "delta_events", "delta_outage_hours"]
        ].sum()
        if not units.empty
        else pd.DataFrame(
            columns=[
                "technology",
                "cost_a",
                "cost_b",
                "delta_cost",
                "delta_events",
                "delta_outage_hours",
            ]
        )
    )

    theta = _theta_table(techs, ra, rb, sscc, params, hours_per_year)
    return ThetaReport(units=units, technologies=techs, theta=theta)


#: Key of a reserve product in the θ tables.
PRODUCT_KEY = ["technology", "service", "direction"]


def _reserve_by_service(reserve: pd.DataFrame) -> pd.Series:
    """Reserve MW·h per ``(technology, service, direction)``."""
    if reserve.empty:
        return pd.Series(dtype=float)
    return reserve.groupby(PRODUCT_KEY)["mwh"].sum()


def _theta_table(
    techs: pd.DataFrame,
    ra: pd.DataFrame,
    rb: pd.DataFrame,
    sscc: Run,
    params: ParamSet,
    hours_per_year: float,
) -> pd.DataFrame:
    rsa = _reserve_by_service(ra)
    rsb = _reserve_by_service(rb)
    dr = rsb.sub(rsa, fill_value=0.0) if not (rsa.empty or rsb.empty) else rsb
    known = known_cost_table(sscc, hours_per_year=hours_per_year)
    known_by = (
        known.groupby([*PRODUCT_KEY, "term"])["cost"].sum()
        if not known.empty
        else pd.Series(dtype=float)
    )
    dcost = (
        dict(zip(techs["technology"], techs["delta_cost"], strict=True))
        if not techs.empty
        else {}
    )
    term_cols = [f"theta_known_{t}" for t in KNOWN_COST_TERMS]
    columns = [
        "technology",
        "service",
        "direction",
        "reserve_mwh_b",
        "delta_reserve_mwh",
        "delta_maintenance_cost",
        "theta_maintenance",
        *term_cols,
        "theta_known",
        "theta_total",
    ]
    rows = []
    for tech in sorted({t for t, _, _ in dr.index}):
        prods = [(s, d) for t, s, d in dr.index if t == tech]
        weights = {sd: params.service_weight(*sd) for sd in prods}
        denom = sum(
            weights[sd] * max(float(dr[(tech, sd[0], sd[1])]), 0.0) for sd in prods
        )
        for s, d in prods:
            d_res = float(dr[(tech, s, d)])
            r_b = float(rsb.get((tech, s, d), 0.0))
            th_m = (
                dcost.get(tech, 0.0) * weights[(s, d)] / denom if denom > 0 else np.nan
            )
            knowns = {
                t: (float(known_by.get((tech, s, d, t), 0.0)) / r_b if r_b > 0 else 0.0)
                for t in KNOWN_COST_TERMS
            }
            th_k = sum(knowns.values())
            rows.append(
                {
                    "technology": tech,
                    "service": s,
                    "direction": d,
                    "reserve_mwh_b": r_b,
                    "delta_reserve_mwh": d_res,
                    "delta_maintenance_cost": dcost.get(tech, 0.0),
                    "theta_maintenance": th_m,
                    **{f"theta_known_{t}": v for t, v in knowns.items()},
                    "theta_known": th_k,
                    "theta_total": th_k + th_m if np.isfinite(th_m) else np.nan,
                }
            )
    return pd.DataFrame(rows, columns=columns)
