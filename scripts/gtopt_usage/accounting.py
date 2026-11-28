# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Usage accounting of one gtopt run (expected values over scenarios).

Every metric is an expectation over the scenarios of the run (normalised
probabilities) and is aggregated per element and stage; the ``year`` column
counts 8760 h years from the start of the horizon.

Streams read (``<Class>/<field>``):

=================================  ===========================================
``Generator/generation_sol``       energy [MWh] = Σ p · h
``Generator/usage_on_sol``         operating hours [h] = Σ on · h
``Generator/usage_ramp_sol``       ramp mileage [MW] = Σ |Δp|
``Commitment/startup_sol``         starts = Σ v
``Commitment/shutdown_sol``        shutdowns = Σ w
``Commitment/usage_at_pmin_sol``   hours at the committed minimum = Σ ·h
``ReserveProvision/uprovision_sol`` up reserve held [MW·h] = Σ r · h
``ReserveProvision/dprovision_sol`` down reserve held [MW·h]
``Battery/finp_sol``, ``fout_sol``  charge / discharge [MWh]
=================================  ===========================================

The ``usage_*`` streams need ``options.output_usage_metrics = true``.
Without ``usage_on`` the operating hours fall back to ``generation > ε``;
without ``usage_ramp`` / ``usage_at_pmin`` those metrics read 0.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd

from gtopt_compare_runs.reader import Run

logger = logging.getLogger(__name__)

#: Hours of one accounting year.
HOURS_PER_YEAR = 8760.0
#: Dispatch above which a block counts as operating (fallback path) [MW].
ON_EPS = 1e-6

#: Columns of :func:`usage_table`.
USAGE_COLUMNS = [
    "name",
    "kind",
    "technology",
    "k",
    "stage",
    "year",
    "hours",
    "energy_mwh",
    "pmin_hours",
    "starts",
    "shutdowns",
    "ramp_mw",
    "reserve_up_mwh",
    "reserve_dn_mwh",
    "charge_mwh",
    "discharge_mwh",
    "throughput_mwh",
    "efc",
]

#: Columns of :func:`reserve_table`.
RESERVE_COLUMNS = [
    "name",
    "technology",
    "provision",
    "service",
    "direction",
    "k",
    "year",
    "mwh",
]

#: Columns of :func:`known_cost_table`.
KNOWN_COST_COLUMNS = [
    "name",
    "technology",
    "provision",
    "service",
    "direction",
    "term",
    "k",
    "year",
    "cost",
]

#: Known variable reserve-cost terms written by gtopt per provision
#: (``ReserveProvision/<dir>_<term>_cost_sol``, $/h).
KNOWN_COST_TERMS = ("partload", "activation", "degradation")


# ── helpers ─────────────────────────────────────────────────────────────


def _ref_name(ref: Any, uid_names: Mapping[int, str]) -> str | None:
    """Element name of a uid-or-name reference."""
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return uid_names.get(ref, f"uid:{ref}")
    if isinstance(ref, str):
        return ref
    return None


def stage_years(run: Run, hours_per_year: float = HOURS_PER_YEAR) -> np.ndarray:
    """Year (1-based) of every stage, from the cumulative stage start hours."""
    hours = np.asarray(run.grid.durations, dtype=float)
    out = np.ones(run.grid.n_stages, dtype=np.int64)
    start = 0.0
    for k, st in enumerate(run.grid.stages):
        out[k] = int(start // hours_per_year) + 1
        start += float(hours[list(st.blocks)].sum())
    return out


def _probability(run: Run, df: pd.DataFrame) -> np.ndarray:
    """Normalised scenario probability of every cell of ``df``."""
    probs: np.ndarray = np.asarray(run.weights, dtype=float)
    return np.asarray(probs[df["s"].to_numpy(dtype=int)], dtype=float)


def _agg(run: Run, cls: str, fld: str, per_hour: bool = True) -> pd.Series:
    """``Σ value · weight`` of a stream by ``(name, k)``.

    ``per_hour``: weight = block hours x probability (energies, hours);
    else probability only (counts: starts, mileage).
    """
    df = run.cells(cls, fld)
    if df.empty:
        return pd.Series(dtype=float)
    w = df["weight"].to_numpy() if per_hour else _probability(run, df)
    return df.assign(v=df["value"].to_numpy() * w).groupby(["name", "k"])["v"].sum()


def _rename(series: pd.Series, mapping: Mapping[str, str]) -> pd.Series:
    """Re-key the ``name`` level of ``(name, k)`` and sum duplicates."""
    if series.empty:
        return series
    idx = series.index
    names = [mapping.get(n, n) for n in idx.get_level_values(0)]
    keyed = pd.Series(
        series.to_numpy(),
        index=pd.MultiIndex.from_arrays(
            [names, idx.get_level_values(1)], names=["name", "k"]
        ),
    )
    return keyed.groupby(level=["name", "k"]).sum()


def commitment_generators(run: Run) -> dict[str, str]:
    """Commitment name -> generator name."""
    gens = run.names("Generator")
    out: dict[str, str] = {}
    for el in run.elements("Commitment"):
        gen = _ref_name(el.get("generator"), gens)
        if gen is not None and "name" in el:
            out[str(el["name"])] = gen
    return out


def provision_info(run: Run) -> dict[str, tuple[str, str]]:
    """ReserveProvision name -> ``(generator name, service)``.

    The service is the provision's zone name, or the zone names joined by
    ``+`` when one provision serves several (nested) zones.
    """
    gens = run.names("Generator")
    zones = run.names("ReserveZone")
    out: dict[str, tuple[str, str]] = {}
    for el in run.elements("ReserveProvision"):
        name = el.get("name")
        gen = _ref_name(el.get("generator"), gens)
        if name is None or gen is None:
            continue
        refs = el.get("reserve_zones") or []
        if isinstance(refs, str):
            refs = [r for r in refs.replace(":", ",").split(",") if r]
        znames = []
        for ref in refs:
            zn = _ref_name(int(ref) if str(ref).isdigit() else ref, zones)
            if zn is not None:
                znames.append(zn)
        out[str(name)] = (gen, "+".join(znames) or "unzoned")
    return out


def _energy_capacity(el: Mapping[str, Any]) -> float | None:
    """Usable energy capacity [MWh] of a battery element, when scalar."""
    for key in ("energy_capacity", "capacity", "emax"):
        val = el.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val) if val > 0 else None
        if isinstance(val, list):
            flat = np.asarray(val, dtype=float).ravel()
            if flat.size and np.nanmax(flat) > 0:
                return float(np.nanmax(flat))
    return None


def technologies(run: Run) -> dict[str, str]:
    """Element name -> technology (``type``) of generators and batteries."""
    out = run.types()
    for el in run.elements("Battery"):
        if "name" in el:
            out[str(el["name"])] = str(el.get("type") or "battery")
    return out


# ── generator / battery usage ───────────────────────────────────────────


def _hours(run: Run) -> pd.Series:
    """Operating hours by ``(name, k)``; falls back to ``generation > ε``."""
    on = _agg(run, "Generator", "usage_on_sol")
    if not on.empty:
        return on
    df = run.cells("Generator", "generation_sol")
    if df.empty:
        return pd.Series(dtype=float)
    logger.warning(
        "%s: no Generator/usage_on_sol (run gtopt with "
        "options.output_usage_metrics = true); operating hours from "
        "generation > %g",
        run.label,
        ON_EPS,
    )
    ind = (df["value"].to_numpy() > ON_EPS).astype(float)
    return df.assign(v=ind * df["weight"].to_numpy()).groupby(["name", "k"])["v"].sum()


def _pmin_hours(run: Run, band: float | None) -> pd.Series:
    """Hours at (``band`` None) or near (p ≤ Pmin·(1+band)) the floor."""
    cmap = commitment_generators(run)
    if band is None:
        return _rename(_agg(run, "Commitment", "usage_at_pmin_sol"), cmap)
    pmin = run.cells("Commitment", "usage_pmin_sol")
    status = run.cells("Commitment", "status_sol")
    if pmin.empty or status.empty:
        return pd.Series(dtype=float)
    keys = ["name", "s", "k", "b"]
    gen = run.cells("Generator", "generation_sol")
    pmin = pmin.assign(name=pmin["name"].map(lambda n: cmap.get(n, n)))
    status = status.assign(name=status["name"].map(lambda n: cmap.get(n, n)))
    df = pmin.merge(status[[*keys, "value"]], on=keys, suffixes=("_pmin", "_u"))
    df = df.merge(
        gen[[*keys, "value"]].rename(columns={"value": "p"}), on=keys, how="left"
    ).fillna({"p": 0.0})
    u = df["value_u"].to_numpy()
    near = (u > 0) & (
        df["p"].to_numpy() <= df["value_pmin"].to_numpy() * u * (1.0 + band) + ON_EPS
    )
    return (
        df.assign(v=near * u * df["weight"].to_numpy())
        .groupby(["name", "k"])["v"]
        .sum()
    )


def _reserve_by_generator(run: Run, fld: str) -> pd.Series:
    info = provision_info(run)
    return _rename(
        _agg(run, "ReserveProvision", fld), {p: g for p, (g, _) in info.items()}
    )


def usage_table(
    run: Run,
    *,
    hours_per_year: float = HOURS_PER_YEAR,
    pmin_band: float | None = None,
) -> pd.DataFrame:
    """Per-unit, per-stage usage metrics of ``run`` (see module doc).

    ``pmin_band``: count hours with ``p ≤ Pmin·u·(1 + band)`` (from
    ``Commitment/usage_pmin_sol``) instead of gtopt's at-floor indicator.
    """
    series = {
        "hours": _hours(run),
        "energy_mwh": _agg(run, "Generator", "generation_sol"),
        "pmin_hours": _pmin_hours(run, pmin_band),
        "starts": _rename(
            _agg(run, "Commitment", "startup_sol", per_hour=False),
            commitment_generators(run),
        ),
        "shutdowns": _rename(
            _agg(run, "Commitment", "shutdown_sol", per_hour=False),
            commitment_generators(run),
        ),
        "ramp_mw": _agg(run, "Generator", "usage_ramp_sol", per_hour=False),
        "reserve_up_mwh": _reserve_by_generator(run, "uprovision_sol"),
        "reserve_dn_mwh": _reserve_by_generator(run, "dprovision_sol"),
        "charge_mwh": _agg(run, "Battery", "finp_sol"),
        "discharge_mwh": _agg(run, "Battery", "fout_sol"),
    }
    present = {k: v for k, v in series.items() if not v.empty}
    if not present:
        return pd.DataFrame(columns=USAGE_COLUMNS)
    df = pd.concat(present, axis=1).fillna(0.0)
    for col in series:
        if col not in df.columns:
            df[col] = 0.0
    df = df.reset_index()
    batteries = {str(el["name"]): el for el in run.elements("Battery") if "name" in el}
    df["kind"] = np.where(df["name"].isin(list(batteries)), "battery", "generator")
    tech = technologies(run)
    df["technology"] = df["name"].map(lambda n: tech.get(n, "other"))
    years = stage_years(run, hours_per_year)
    df["year"] = years[df["k"].to_numpy(dtype=int)]
    df["stage"] = [run.grid.stages[k].uid for k in df["k"].to_numpy(dtype=int)]
    df["throughput_mwh"] = 0.5 * (df["charge_mwh"] + df["discharge_mwh"])
    caps = {n: _energy_capacity(el) for n, el in batteries.items()}
    cap = df["name"].map(lambda n: caps.get(n) or np.nan).to_numpy(dtype=float)
    df["efc"] = np.where(np.isfinite(cap), df["discharge_mwh"] / cap, 0.0)
    return df[USAGE_COLUMNS].sort_values(["name", "k"]).reset_index(drop=True)


def per_year(df: pd.DataFrame) -> pd.DataFrame:
    """Sum a :func:`usage_table` over the stages of each year."""
    if df.empty:
        return df
    keys = ["name", "kind", "technology", "year"]
    num = [c for c in USAGE_COLUMNS if c not in (*keys, "k", "stage")]
    return df.groupby(keys, as_index=False)[num].sum()


# ── reserve held and known reserve costs per service ───────────────────


def reserve_table(run: Run, *, hours_per_year: float = HOURS_PER_YEAR) -> pd.DataFrame:
    """Reserve held [MW·h] per generator, provision, service, direction, stage."""
    info = provision_info(run)
    tech = technologies(run)
    years = stage_years(run, hours_per_year)
    frames = []
    for direction, fld in (("up", "uprovision_sol"), ("dn", "dprovision_sol")):
        s = _agg(run, "ReserveProvision", fld)
        if s.empty:
            continue
        df = s.rename("mwh").reset_index().rename(columns={"name": "provision"})
        df["direction"] = direction
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=RESERVE_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df["name"] = df["provision"].map(lambda p: info.get(p, (p, ""))[0])
    df["service"] = df["provision"].map(lambda p: info.get(p, ("", "unzoned"))[1])
    df["technology"] = df["name"].map(lambda n: tech.get(n, "other"))
    df["year"] = years[df["k"].to_numpy(dtype=int)]
    return df[RESERVE_COLUMNS].reset_index(drop=True)


def known_cost_table(
    run: Run, *, hours_per_year: float = HOURS_PER_YEAR
) -> pd.DataFrame:
    """Known variable reserve costs [$] written by gtopt, per term.

    Reads ``ReserveProvision/{up,dn}_{partload,activation,degradation}_cost_sol``
    ($/h, emitted when the matching provision field is set).
    """
    info = provision_info(run)
    tech = technologies(run)
    years = stage_years(run, hours_per_year)
    frames = []
    for direction in ("up", "dn"):
        for term in KNOWN_COST_TERMS:
            s = _agg(run, "ReserveProvision", f"{direction}_{term}_cost_sol")
            if s.empty:
                continue
            df = s.rename("cost").reset_index().rename(columns={"name": "provision"})
            df["direction"] = direction
            df["term"] = term
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=KNOWN_COST_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df["name"] = df["provision"].map(lambda p: info.get(p, (p, ""))[0])
    df["service"] = df["provision"].map(lambda p: info.get(p, ("", "unzoned"))[1])
    df["technology"] = df["name"].map(lambda n: tech.get(n, "other"))
    df["year"] = years[df["k"].to_numpy(dtype=int)]
    return df[KNOWN_COST_COLUMNS].reset_index(drop=True)
