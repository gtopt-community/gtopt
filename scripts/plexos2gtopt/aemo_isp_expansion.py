"""AEMO ISP PLEXOS model → gtopt multi-year **expansion** case (prototype).

Builds a generation-expansion planning case from the AEMO ISP DLT XML
with **monthly stages** on top of the weekly dispatch converter
(:mod:`plexos2gtopt.aemo_isp`):

* Time: one stage per calendar month of the planning (financial) years
  ``first_fy .. first_fy + years - 1`` (FY ending year, e.g. 2028 =
  2027-28).  Each stage is one **representative day** of 24 hour-of-day
  blocks whose duration is the number of days of the month (the block
  value is the month mean of that hour).  Energy, cost, reservoir
  balances and capex (``stage.duration`` = month hours) are exact
  monthly totals; batteries use gtopt's ``daily_cycle`` (the default),
  which rescales the energy balance by ``24 / stage_duration`` and
  closes every stage with ``efin == eini`` — a daily cycle repeated over
  the month.  ``--rep-days 2`` splits every month into a **high** and a
  **low net-load** representative day (daily demand − available wind
  and solar, split at the month median), one stage each, block duration
  = the days of the group; energy and costs stay exact month totals.
* Phases: none (one monolithic LP) by default; ``--phases month`` /
  ``year`` writes one phase per month / per FY for the SDDP method, in
  which case installed capacity (``capainst`` / ``capacost``) and the
  reservoir volumes are the state variables.
* Reservoirs: the volume is a state coupled across all stages (the water
  value comes from that coupling); there is no per-stage ``efin ==
  eini``, no terminal water value and no ``efin_cost`` — the terminal
  condition is 0 (free end volume) at the horizon end only.  Only what
  the PLEXOS data imposes is kept: Min / Max Volume, and storages with
  ``End Effects Method`` = Recycle or a ``Target Week`` (pondages, PHES
  upper reservoirs, the empty irrigation dams) cycle back to their
  initial volume within every stage (gtopt ``daily_cycle``).
  Batteries (existing and candidates, incl. PHES) are written with an
  explicit ``daily_cycle``: the state of charge closes within each
  representative day.  This deliberately overrides PLEXOS's ``End
  Effects Method`` = Free (set on every ISP battery): representative days
  are not consecutive days, so carrying charge from one to the next would
  move energy between unrelated days (and between months).
  ``--fill-years N`` (CLI default 1) appends N unreported FYs to damp the
  end effect, e.g. FY31 for an FY28-FY30 study; the stats record
  ``report_years`` / ``fill_years``.
* Network augmentations (REZ transmission, DNSP and system-strength
  options) are expansion **decision variables** here: the weekly
  converter's ``--odp-augmentations`` (which fixes the builds of an ISP
  development path from the outlook workbook) is never applied to the
  expansion case.  Pumped-storage units of the weekly hydro model (pump
  waterway + Pump + pump-load Demand) are carried over unchanged.
* Existing fleet: every month is converted with the weekly converter on
  a window = the whole month, so commissioning, retirements (dated
  ``Units``), dated line limits (CDP network), fuel prices and traces
  follow the month; the hourly schedules are folded to hour-of-day
  means.
* Candidates: every generator with ``Units = 0`` and ``Max Units Built``
  > 0 on some day of the horizon (new-entrant REZ / ODR / DREZ / gas /
  biomass and the ``Network Augmentation`` pseudo-generators — REZ
  transmission, DNSP and system-strength options, which carry no energy
  and only enter constraints).  gtopt expansion fields: ``capacity`` 0,
  ``expcap`` = ``Max Capacity`` (1 MW in the ISP), ``expmod`` = ``Max
  Units Built`` on the first month of each FY from its first year
  (builds happen at FY start, as in the yearly PLEXOS LT plan),
  ``capmax`` = cumulative ``Max Units Built``; ``annual_capcost`` =
  ``Build Cost``·1000·CRF(``WACC``, ``Economic Life``) + ``FO&M``·1000
  [$/MW-yr], the build-year cost being locked in by gtopt's ``capacost``
  accumulator (PLEXOS ``Always Annualize Build Cost``, no end effects).
  Energy candidates carry a ``GeneratorProfile`` (rating trace × (1 −
  Aux Incr), hour-of-day mean per month).
* PLEXOS constraints: the weekly engine in expansion mode — candidate
  capacity terms become ``generator(...).capainst`` and the pure
  capacity / build constraints (``RHS Year`` in MW) become stage-scope
  user constraints; per-block constraints keep their per-block RHS
  (hour-of-day mean), ``RHS Day`` constraints become a stage energy sum
  (× days), period constraints keep the month share.

Not modelled yet (see the scoping report): battery / pumped-hydro
expansion (gtopt expands batteries on the energy axis only), inter-
regional transmission expansion (the CDP 4 augmentations are input),
yearly energy targets as cross-month accumulators, retirements as
decisions, unit outages, purchasers / hydrogen.

Run as ``python -m plexos2gtopt.aemo_isp_expansion --help``.
"""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from gtopt_shared.point_sampling import (
    POINT_STORAGE_INTEGRATION,
    point_storage_options,
)

from gtopt_shared import safe_xml

from . import aemo_isp_constraints as isp_c
from .aemo_isp import (
    IspCase,
    IspCaseOptions,
    IspResolver,
    _gen_type,
    _json_default,
    _single_child,
    build_dispatch_case,
)
from .isp_traces import TraceLocator, steps_per_day, trace_resolution
from .plexos_xml import PlexosDb, load_xml
from .week_sampler import (
    AUDIT_TAG,
    GUARD_STATS,
    WEEK_HOURS,
    BlockGrid,
    ChebyshevSampler,
    NestedChebyshevRefine,
    PeakRampSampler,
    RampRefineSampler,
    UniformSampler,
    WeekSampler,
    choose_order,
    parse_ramp_refine,
    parse_week_start,
    reset_guard_stats,
    select_window,
)

logger = logging.getLogger(__name__)

#: Marker RHS of a stage where a merged constraint is absent (internal; the
#: written constraint gets a per-stage ``active_stages`` schedule instead).
INACTIVE_RHS = 1.0e9

#: Default discount rate when the XML has no ``LT Plan`` (AEMO: 7 % real).
DEFAULT_DISCOUNT_RATE = 0.07

#: Battery fields that become per-(stage, block) inline matrices.
_BATTERY_TB_FIELDS = ("emin", "emax", "pmax_charge", "pmax_discharge")
#: battery schedules written as Battery/<field>.parquet (read by BatteryLP
#: itself under the battery uid)
_BATTERY_FILE_FIELDS = ("emin", "emax")

_OP_RE = re.compile(r"\s(<=|>=|=)\s(\S+)\s*$")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _feasible_cycle_eini(name: str, eini: float, item: dict[str, Any]) -> float:
    """Clip a daily-cycle battery's ``eini`` into every stage's bounds.

    gtopt closes each daily-cycle stage with ``efin == eini`` and chains the
    stages, so the level at every stage boundary equals ``eini``: it must
    lie within each stage's ``[emin, emax]`` at its first and last block.
    A unit that retires (or is mothballed) mid-horizon has ``emax = 0`` in
    those stages, which made the LP infeasible (the ``storage_close`` row of
    the first zero-capacity stage)."""
    emax = item.get("emax")
    emin = item.get("emin")
    hi = eini
    lo = 0.0
    if isinstance(emax, list) and emax:
        hi = min(min(float(r[0]), float(r[-1])) for r in emax)
    if isinstance(emin, list) and emin:
        lo = max(max(float(r[0]), float(r[-1])) for r in emin)
    clipped = float(min(max(eini, lo), max(hi, lo)))
    if abs(clipped - eini) > 1e-9:
        logger.warning(
            "battery %s: eini %.6g outside the stage-boundary range [%.6g, %.6g]"
            " of its daily cycle (retirement / zero-capacity stages); using %.6g",
            name,
            eini,
            lo,
            hi,
            clipped,
        )
    return clipped


def month_starts(first_fy: int, years: int) -> list[date]:
    """First day of every month of FY ``first_fy`` (ending June) onwards."""
    out: list[date] = []
    for k in range(12 * years):
        m = 7 + k
        y = first_fy - 1 + (m - 1) // 12
        out.append(date(y, (m - 1) % 12 + 1, 1))
    return out


def days_in_month(day: date) -> int:
    """Number of days of ``day``'s month."""
    return calendar.monthrange(day.year, day.month)[1]


def hod_mean(arr: np.ndarray, days: int) -> np.ndarray:
    """Hour-of-day mean (24 values) of an hourly array of ``days`` days."""
    a = np.asarray(arr, dtype=float)
    out: np.ndarray = np.nanmean(a.reshape(days, 24), axis=0)
    return out


def day_masks(net_load: np.ndarray, rep_days: int) -> list[np.ndarray]:
    """Split the days of a month into ``rep_days`` representative-day groups.

    ``rep_days = 1``: every day.  ``rep_days = 2``: **high / low net-load**
    days (daily demand minus available wind + solar, split at the month
    median).  Net load, not weekday / weekend, drives what an expansion
    plan has to cover — firming and storage are sized by the
    low-renewable, high-demand days that a single month-mean day averages
    away.  Both groups are kept non-empty.
    """
    nd = len(net_load)
    if rep_days <= 1 or nd < 2:
        return [np.ones(nd, dtype=bool)]
    if rep_days != 2:
        raise ValueError(f"rep_days must be 1 or 2, got {rep_days}")
    order = np.argsort(-np.asarray(net_load, dtype=float), kind="stable")
    high = np.zeros(nd, dtype=bool)
    high[order[: (nd + 1) // 2]] = True
    return [high, ~high]


def rep_mean(arr: np.ndarray, mask: np.ndarray, spd: int = 24) -> np.ndarray:
    """Time-of-day mean (``spd`` values) over the days selected by ``mask``."""
    nd = len(mask)
    a = np.asarray(arr, dtype=np.float64)[: nd * spd].reshape(nd, spd)
    out: np.ndarray = np.nanmean(a[mask], axis=0)
    return out


@dataclass
class StageSpec:
    """One stage of a month: how it represents the month's samples."""

    days: int  #: days represented
    durations: np.ndarray  #: weighted block durations [h]
    #: month series (hourly) → one value per block (``bounds`` optional)
    reduce: Callable[..., np.ndarray]
    #: mean of the represented samples of a month series
    mean: Callable[[np.ndarray], float]
    period_hours: float | None = None
    times: np.ndarray | None = None
    cycle_group: int | None = None
    cycle_weight: float | None = None
    window_start: int | None = None  #: week window start (hour of month)
    #: commitment cell per block (refined block weeks; see BlockGrid)
    cells: np.ndarray | None = None

    @property
    def nb(self) -> int:
        """Number of blocks."""
        return len(self.durations)


def make_sampler(spec: str) -> WeekSampler:
    """Week sampler from a spec string.

    ``uniform:H`` (H-hour blocks), ``chrono:D`` (hourly blocks over the
    first D days of the month: chronological reference), ``peak_ramp:K`` (2 h slots, K fine
    slots per day), ``chebyshev:N[:gauss|lobatto][:key=value...]`` with
    keys ``values`` (fct|fct_point|l1|lsw|lswd|lsu|proj|wcell|ls|interp|
    mean), ``fct_points`` (M_f), ``durations`` (quadrature|trapezoid|
    voronoi), ``periodic`` (C0 default|C1|none), ``odd_terms`` (all|none|K), ``bound_mode``
    (report|clip_rescale|bounded), ``solar_night`` (mask|off),
    ``solar_basis`` (plain|clearsky).
    """
    parts = spec.split(":")
    kind = parts[0]
    if kind == "uniform":
        if len(parts) > 2 and parts[2] != "point":
            raise ValueError(f"bad uniform sampler {spec!r}: uniform:H[:point]")
        return UniformSampler(
            hours=float(parts[1]) if len(parts) > 1 else 1.0,
            point=len(parts) > 2,
        )
    if kind == "chrono":
        # chronological reference: hourly blocks over the first D days of
        # every month (D = 28 fits every month), scaled to the month
        ndays = int(parts[1]) if len(parts) > 1 else 28
        return UniformSampler(
            hours=1.0, window_hours=24.0 * ndays, window_start=(-1, 0.0), name="chrono"
        )
    if kind == "peak_ramp":
        return PeakRampSampler(fine=int(parts[1]) if len(parts) > 1 else 4)
    if kind == "chebyshev":
        kw: dict[str, Any] = {"n": int(parts[1]) if len(parts) > 1 else 96}
        for part in parts[2:]:
            if part in ("gauss", "lobatto"):
                kw["kind"] = part
                continue
            key, _, val = part.partition("=")
            if (key == "odd_terms" and val not in ("all", "none")) or key in (
                "fct_points",
                "l1_oversample",
            ):
                kw[key] = int(val)
            elif key in (
                "values",
                "durations",
                "periodic",
                "odd_terms",
                "bound_mode",
                "solar_night",
                "solar_basis",
                "l1_y",
            ):
                kw[key] = val
            else:
                raise ValueError(f"unknown chebyshev option {part!r}")
        # Storages close the weekly cycle (daily_cycle over period_hours):
        # t_0 and t_N are the same instant of the cyclic week, so the
        # sampled values must agree there — periodic C0 by default
        # (``periodic=none`` to disable).
        kw.setdefault("periodic", "C0")
        if kw["periodic"] in ("none", "off"):
            kw["periodic"] = None
        return ChebyshevSampler(**kw)
    raise ValueError(f"unknown week sampler {spec!r}")


def split_auto_order(spec: str) -> tuple[str, float, str] | None:
    """``chebyshev:auto[:tol=T][:metric=rms|ramp]...`` → (spec without the
    auto keys, tol, metric); ``None`` for a fixed-order spec."""
    parts = spec.split(":")
    if parts[0] != "chebyshev" or len(parts) < 2 or parts[1] != "auto":
        return None
    tol, metric, rest = 0.03, "rms", []
    for part in parts[2:]:
        key, _, val = part.partition("=")
        if key == "tol":
            tol = float(val)
        elif key == "metric":
            if val not in ("rms", "ramp"):
                raise ValueError(f"unknown order metric {val!r}")
            metric = val
        else:
            rest.append(part)
    return ":".join(["chebyshev", "{n}", *rest]), tol, metric


def is_point_sampler(sampler: WeekSampler) -> bool:
    """True when the sampler's blocks are point samples (Chebyshev nodes
    or ``uniform:H:point``): the stage gets ``sampling = "point"``."""
    return isinstance(sampler, (ChebyshevSampler, NestedChebyshevRefine)) or bool(
        getattr(sampler, "point", False)
    )


def with_ramp_refine(
    sampler: WeekSampler, spec: str, half_width: float = 1.0
) -> WeekSampler:
    """``sampler`` refined around the sunrise / sunset ramps
    (:class:`RampRefineSampler`); ``spec`` ``"off"`` returns it as is.

    Block weeks need a ``uniform:H`` base (fixed-length windows keep the
    block count constant across stages).  A Chebyshev (Lobatto) week of
    order N gets the 2 nested order-2N nodes nearest to every event
    (:class:`NestedChebyshevRefine`); resolution and width are then
    unused."""
    res = parse_ramp_refine(spec)
    if res is None:
        return sampler
    if isinstance(sampler, ChebyshevSampler):
        # nested 2N Lobatto nodes at the events (resolution unused)
        return NestedChebyshevRefine(base=sampler)
    if not isinstance(sampler, UniformSampler):
        raise ValueError(
            "--ramp-refine needs a uniform or chebyshev sampler, got "
            f"{getattr(sampler, 'name', sampler)!r}"
        )
    return RampRefineSampler(base=sampler, resolution=res, half_width=half_width)


def with_week_start(sampler: WeekSampler, spec: str) -> WeekSampler:
    """``sampler`` with its window starting at ``spec`` (``"wed:12"``);
    the chronological ``chrono:D`` window (weekday < 0) is kept."""
    if sampler.window_start[0] < 0:
        return sampler
    return replace(sampler, window_start=parse_week_start(spec))  # type: ignore[type-var]


def make_day_sampler(spec: str) -> ChebyshevSampler:
    """Chebyshev sampler of one representative day (24 h window from
    00:00, periodic: the day closes its storage cycle); ``spec`` as for
    :func:`make_sampler` (``chebyshev:...`` only)."""
    samp = make_sampler(spec)
    if not isinstance(samp, ChebyshevSampler):
        raise ValueError(f"--day-sampler needs a chebyshev spec, got {spec!r}")
    return replace(samp, window_hours=24.0, window_start=(0, 0.0))


#: candidate orders of ``--day-sampler chebyshev:auto`` (24 h window)
DAY_ORDERS = (4, 6, 8, 12, 16, 24, 32, 48)


def vre_window_traces(
    system: dict[str, Any],
    ws: dict[tuple[str, str], Any],
    i0: int,
    n_win: int,
) -> tuple[list[np.ndarray], list[float]]:
    """Solar and wind pmax traces of a window with their capacities."""
    ys, caps = [], []
    for g in system["generator_array"]:
        if g.get("type") not in ("solar", "wind"):
            continue
        arr = ws.get(("Generator", "pmax"), {}).get(g["uid"])
        if arr is None:
            continue
        y = np.nan_to_num(np.asarray(arr, dtype=float))[i0 : i0 + n_win]
        cap = g.get("capacity")
        cap = float(cap) if isinstance(cap, (int, float)) and cap > 0 else 0.0
        cap = cap or float(y.max(initial=0.0))
        if cap > 0 and np.any(y):
            ys.append(y)
            caps.append(cap)
    return ys, caps


def weekday_masks(first_day: date, nd: int) -> list[np.ndarray]:
    """Weekday (Mon–Fri) and weekend day masks of a month."""
    wd = np.array([(first_day + timedelta(days=k)).weekday() for k in range(nd)])
    return [wd < 5, wd >= 5]


def month_layout(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    opts: ExpansionOptions,
    m: int,
    day0: date,
    nd: int,
    net_load: np.ndarray,
    sampler: WeekSampler | None,
    day_sampler: ChebyshevSampler | None = None,
) -> list[StageSpec]:
    """Stages of month ``m`` (representative days or one typical week)."""
    spd = steps_per_day()
    step = 24.0 / spd
    if sampler is not None:
        win_h = float(getattr(sampler, "window_hours", WEEK_HOURS))
        n_win = int(round(win_h / step))
        if sampler.window_start[0] < 0:
            # chronological window from the first hour of the month
            i0 = 0
            if n_win > len(net_load):
                raise ValueError(f"window of {win_h} h longer than {day0:%Y-%m}")
        else:
            i0 = select_window(
                net_load,
                day0,
                sampler.window_start,
                step,
                opts.week_rule,  # type: ignore[arg-type]
            )
        grid = sampler.grid(net_load[i0 : i0 + n_win], step)

        def reduce_w(
            arr: np.ndarray, bounds: tuple[float, float] | None = None
        ) -> np.ndarray:
            a = np.nan_to_num(np.asarray(arr, dtype=float)[i0 : i0 + n_win])
            return sampler.sample(a, step, grid, bounds)

        def mean_w(arr: np.ndarray) -> float:
            return float(np.nanmean(np.asarray(arr, dtype=float)[i0 : i0 + n_win]))

        return [
            StageSpec(
                days=nd,
                durations=grid.durations * nd * 24.0 / win_h,
                reduce=reduce_w,
                mean=mean_w,
                period_hours=win_h,
                times=grid.times if is_point_sampler(sampler) else None,
                cells=None if is_point_sampler(sampler) else grid.cells,
                window_start=int(round(i0 * step)),
            )
        ]
    nrep = max(1, opts.rep_days)
    if opts.rep_kind == "weekday" and nrep == 2:
        masks = weekday_masks(day0, nd)
    else:
        masks = day_masks(net_load[: nd * spd].reshape(nd, spd).sum(axis=1), nrep)
    out = []
    for mk in masks:
        days = int(mk.sum())

        def mean_d(arr: np.ndarray, mk: np.ndarray = mk) -> float:
            a = np.asarray(arr, dtype=float)[: nd * spd].reshape(nd, spd)
            return float(np.nanmean(a[mk]))

        if day_sampler is not None:
            dgrid = day_sampler.grid(rep_mean(net_load, mk, spd), step)

            def reduce_c(
                arr: np.ndarray,
                bounds: tuple[float, float] | None = None,
                mk: np.ndarray = mk,
                dgrid: BlockGrid = dgrid,
            ) -> np.ndarray:
                a = np.nan_to_num(rep_mean(arr, mk, spd))
                return day_sampler.sample(a, step, dgrid, bounds)

            reduce_d: Callable[..., np.ndarray] = reduce_c
            durations = np.asarray(dgrid.durations, dtype=float) * days
            period, times = 24.0, np.asarray(dgrid.times, dtype=float)
        else:

            def reduce_h(
                arr: np.ndarray,
                bounds: tuple[float, float] | None = None,
                mk: np.ndarray = mk,
            ) -> np.ndarray:
                del bounds
                return rep_mean(arr, mk, spd)

            reduce_d = reduce_h
            durations, period, times = np.full(24, float(days)), None, None
        out.append(
            StageSpec(
                days=days,
                durations=durations,
                reduce=reduce_d,
                mean=mean_d,
                period_hours=period,
                times=times,
                cycle_group=(m + 1) if opts.cycle_group and nrep > 1 else None,
                cycle_weight=(
                    round(7.0 * days / nd, 9) if opts.cycle_group and nrep > 1 else None
                ),
            )
        )
    return out


def life_factor(life: float, remaining_years: float) -> float:
    """Annuity scale when ``life`` ends before the horizon (else 1)."""
    if life <= 0 or remaining_years <= life:
        return 1.0
    return life / remaining_years


def crf(rate: float, life: float) -> float:
    """Capital recovery factor (annuity per unit of present value)."""
    if life <= 0:
        return 1.0
    if abs(rate) < 1e-12:
        return 1.0 / life
    return float(rate / (1.0 - (1.0 + rate) ** (-life)))


def annual_capcost(
    build_cost_kw: float, wacc_pct: float, life: float, fom_kw_yr: float
) -> float:
    """Annualised capacity cost [$/MW-yr] from PLEXOS $/kW data."""
    return 1000.0 * (build_cost_kw * crf(wacc_pct / 100.0, life) + fom_kw_yr)


def split_expression(expr: str) -> tuple[str, str, float]:
    """``"LHS op RHS"`` → (LHS, op, RHS)."""
    m = _OP_RE.search(expr)
    if m is None:
        raise ValueError(f"cannot split constraint expression: {expr!r}")
    return expr[: m.start()].strip(), m.group(1), float(m.group(2))


def lt_plan_discount_rate(xml: Path) -> float | None:
    """``LT Plan`` ``Discount Rate`` attribute [p.u.] (None if absent)."""
    root = safe_xml.parse(xml).getroot()
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    cls = {
        c.findtext(ns + "class_id"): c.findtext(ns + "name")
        for c in root.iter(ns + "t_class")
    }
    lt_objs = {
        o.findtext(ns + "object_id")
        for o in root.iter(ns + "t_object")
        if cls.get(o.findtext(ns + "class_id")) == "LT Plan"
    }
    attrs = {
        a.findtext(ns + "attribute_id")
        for a in root.iter(ns + "t_attribute")
        if a.findtext(ns + "name") == "Discount Rate"
        and cls.get(a.findtext(ns + "class_id")) == "LT Plan"
    }
    for ad in root.iter(ns + "t_attribute_data"):
        if (
            ad.findtext(ns + "object_id") in lt_objs
            and ad.findtext(ns + "attribute_id") in attrs
        ):
            return float(ad.findtext(ns + "value") or 0.0) / 100.0
    return None


# ---------------------------------------------------------------------------
# options / candidates
# ---------------------------------------------------------------------------


@dataclass
class ExpansionOptions:
    """User options for :func:`build_expansion_case`."""

    xml: Path
    trace_roots: list[Path]
    first_fy: int = 2028
    years: int = 3
    #: extra FYs appended after ``years`` to damp the end effect of the
    #: free terminal reservoir volume (and of builds late in the
    #: horizon); solved like any other year but not reported
    fill_years: int = 0
    name: str = "isp_expansion"
    #: "none" (monolithic), "month" or "year" (one SDDP phase each)
    phases: str = "none"
    #: representative days per month (1: month mean; 2: high / low
    #: net-load days), one stage each
    rep_days: int = 1
    discount_rate: float | None = None
    include_hydro: bool = True
    include_batteries: bool = True
    constraints: bool = True
    demand_fail_cost: float | None = None
    #: slack cost for the hard yearly capacity constraints (PLEXOS Penalty
    #: Price -1); None keeps them hard.  SDDP needs them soft: a month
    #: cannot repair the capacity a previous FY built (feasibility cuts on
    #: a capacity state converge one coordinate per iteration).
    capacity_penalty: float | None = None
    #: yearly value of the existing fleet folded into the capacity
    #: constraints: mean | end | tightest (see :func:`_fy_capacity_rhs`)
    capacity_year_fleet: str = "mean"
    #: add battery / pumped-hydro storage candidates (power + energy)
    battery_candidates: bool = True
    #: restrict candidates to categories starting with one of these
    candidate_prefixes: tuple[str, ...] = ()
    #: representative-day split for ``rep_days = 2``: "netload" (high /
    #: low net-load days) or "weekday" (weekdays / weekend days)
    rep_kind: str = "netload"
    #: option 3: the representative days of a month form ONE weighted
    #: storage cycle (``Stage.cycle_group``, weights = days per typical
    #: week, proportional to the days represented)
    cycle_group: bool = False
    #: option 1: one chronological typical week per month instead of
    #: representative days — sampler spec, see :func:`make_sampler`
    #: ("" = representative days)
    week_sampler: str = ""
    #: Chebyshev nodes on each representative day instead of 24 hourly
    #: blocks (``chebyshev:N[...]`` or ``chebyshev:auto[...]``, 24 h
    #: periodic window; "" = hourly blocks)
    day_sampler: str = ""
    #: week selection rule: "peak" (week holding the month's net-load
    #: peak) or "energy" (highest net energy)
    week_rule: str = "peak"
    #: start of the typical-week window, "<weekday>:<hour>" (default
    #: Wednesday 12:00: the week is centred on the weekend); applies to
    #: every week sampler except the chronological ``chrono:D`` window
    week_start: str = "wed:12"
    #: refine the week to 30 / 15 min around sunrise / sunset net-load
    #: ramps ("30m" | "15m" | "off"); block and uniform:H:point samplers
    ramp_refine: str = "off"
    #: half width [h] of every ramp window (± around the event)
    ramp_refine_width: float = 1.0
    #: storage_integration of batteries and reservoirs on point stages
    #: ("none": emit nothing, gtopt's per-type defaults)
    point_storage_integration: str = POINT_STORAGE_INTEGRATION
    #: samples per day the typical week is sampled from (48: the
    #: half-hourly AEMO traces, needed for Chebyshev N > 168; 24: hourly)
    week_steps_per_day: int = 48


@dataclass
class Candidate:
    """One expansion candidate generator."""

    name: str
    oid: int
    bus: str
    category: str
    unit_mw: float
    wacc: float
    life: float
    fom: float
    vom: float
    aux: float
    heat_rate: float | None
    fuel_oid: int | None
    energy: bool  # False: network-augmentation capacity token
    #: per stage: cumulative max units, build cost $/kW, buildable flag
    max_units: list[float] = field(default_factory=list)
    build_cost: list[float] = field(default_factory=list)
    #: "generator" or "battery" (battery / pumped-hydro storage candidate)
    kind: str = "generator"
    #: storage: energy per unit [MWh] (``Capacity``) and efficiencies
    unit_mwh: float = 0.0
    eff_in: float = 1.0
    eff_out: float = 1.0


def find_candidates(
    res: IspResolver, months: list[date], prefixes: tuple[str, ...] = ()
) -> list[Candidate]:
    """Generators with ``Units`` 0 and ``Max Units Built`` > 0 in the horizon."""
    gen_node = _single_child(res, "Generator", "Nodes")
    gen_fuel = _single_child(res, "Generator", "Fuels")
    nodes = {o.object_id: o.name for o in res.objects("Node")}
    out: list[Candidate] = []
    for o in sorted(res.objects("Generator"), key=lambda o: o.name):
        cat = res.category(o)
        if cat.startswith("Load Subtracters"):
            continue
        if prefixes and not cat.startswith(prefixes):
            continue
        mub = [
            res.scalar(o.object_id, "Max Units Built", d, 0.0) or 0.0 for d in months
        ]
        if max(mub) <= 0:
            continue
        if (res.scalar(o.object_id, "Units", months[0], 0.0) or 0.0) > 0:
            continue
        bus = nodes.get(gen_node.get(o.object_id, -1))
        if bus is None:
            logger.warning("candidate %s has no node, skipped", o.name)
            continue
        unit = res.scalar(o.object_id, "Max Capacity", months[-1], 1.0) or 1.0
        cost = [res.scalar(o.object_id, "Build Cost", d, 0.0) or 0.0 for d in months]
        energy = not cat.startswith("Network Augmentation")
        out.append(
            Candidate(
                name=o.name,
                oid=o.object_id,
                bus=bus,
                category=cat,
                unit_mw=unit,
                wacc=res.scalar(o.object_id, "WACC", months[0], 7.0) or 7.0,
                life=res.scalar(o.object_id, "Economic Life", months[0], 30.0) or 30.0,
                fom=res.scalar(o.object_id, "FO&M Charge", months[0], 0.0) or 0.0,
                vom=res.scalar(o.object_id, "VO&M Charge", months[0], 0.0) or 0.0,
                aux=(res.scalar(o.object_id, "Aux Incr", months[0], 0.0) or 0.0)
                / 100.0,
                heat_rate=res.scalar(o.object_id, "Heat Rate", months[0]),
                fuel_oid=gen_fuel.get(o.object_id),
                energy=energy,
                max_units=mub,
                build_cost=cost,
            )
        )
    return out


def find_battery_candidates(
    res: IspResolver, months: list[date], prefixes: tuple[str, ...] = ()
) -> list[Candidate]:
    """Batteries / PHES with ``Units`` 0 and ``Max Units Built`` > 0.

    One PLEXOS unit = ``Max Power`` MW and ``Capacity`` MWh; ``Build Cost``
    is $/kW of power, so the whole annuity sits on the power axis and the
    energy axis follows through ``energy_power_ratio`` (fixed duration).
    """
    bat_node = _single_child(res, "Battery", "Nodes")
    nodes = {o.object_id: o.name for o in res.objects("Node")}
    out: list[Candidate] = []
    for o in sorted(res.objects("Battery"), key=lambda o: o.name):
        cat = res.category(o)
        if prefixes and not cat.startswith(prefixes):
            continue
        mub = [
            res.scalar(o.object_id, "Max Units Built", d, 0.0) or 0.0 for d in months
        ]
        if max(mub) <= 0:
            continue
        if (res.scalar(o.object_id, "Units", months[0], 0.0) or 0.0) > 0:
            continue
        bus = nodes.get(bat_node.get(o.object_id, -1))
        if bus is None:
            logger.warning("battery candidate %s has no node, skipped", o.name)
            continue

        def val(prop: str, default: float, oid: int = o.object_id) -> float:
            v = res.scalar(oid, prop, months[0], default)
            return default if v is None else float(v)

        out.append(
            Candidate(
                name=o.name,
                oid=o.object_id,
                bus=bus,
                category=cat,
                unit_mw=val("Max Power", 1.0) or 1.0,
                wacc=val("WACC", 7.0),
                life=val("Economic Life", 20.0),
                fom=val("FO&M Charge", 0.0),
                vom=val("VO&M Charge", 0.0),
                aux=0.0,
                heat_rate=None,
                fuel_oid=None,
                energy=True,
                max_units=mub,
                build_cost=[
                    res.scalar(o.object_id, "Build Cost", d, 0.0) or 0.0 for d in months
                ],
                kind="battery",
                unit_mwh=val("Capacity", 0.0),
                eff_in=val("Charge Efficiency", 100.0) / 100.0,
                eff_out=val("Discharge Efficiency", 100.0) / 100.0,
            )
        )
    return out


def fy_start_stages(months: list[date], rep_days: int = 1) -> list[bool]:
    """Per stage: first stage of a FY (July, or the horizon start)."""
    return [
        k == 0 and (m == 0 or d.month == 7)
        for m, d in enumerate(months)
        for k in range(rep_days)
    ]


def build_stages(months: list[date], cand: Candidate, rep_days: int = 1) -> list[bool]:
    """Stages where ``cand`` may build: FY start (July) or horizon start."""
    return [
        f and cand.max_units[s // rep_days] > 0
        for s, f in enumerate(fy_start_stages(months, rep_days))
    ]


def capainst_stages(
    months: list[date], cand: Candidate, rep_days: int = 1
) -> list[bool]:
    """Stages where gtopt creates the candidate's ``capainst`` column."""
    out: list[bool] = []
    seen = False
    for b in build_stages(months, cand, rep_days):
        seen = seen or b
        out.append(seen)
    return out


# ---------------------------------------------------------------------------
# case builder
# ---------------------------------------------------------------------------


@dataclass
class _UcVariant:
    """One distinct LHS of a merged user constraint."""

    base: dict[str, Any]
    op: str
    rhs: dict[int, Any] = field(default_factory=dict)  # stage → float | (24,)


def _fold_uc(
    uc: dict[str, Any], rhs_h: np.ndarray | None, spec: StageSpec
) -> tuple[str, dict[str, Any], str, Any]:
    """Window user constraint → (key, base dict, op, stage rhs).

    ``spec`` tells which samples of the month the stage represents.
    """
    days = spec.days
    lhs, op, rhs = split_expression(uc["expression"])
    base = {
        k: v for k, v in uc.items() if k not in ("uid", "expression", "rhs", "_rhs")
    }
    if uc.get("daily_sum"):
        # RHS Day → one stage energy sum over the representative day × days
        base.pop("daily_sum", None)
        base["scope"] = "stage"
        weight = "" if uc.get("constraint_type") == "raw" else "dur[b] * "
        lhs = f"sum{{b in stage}} {weight}({lhs})"
        mean_day = spec.mean(rhs_h) if rhs_h is not None else rhs
        return f"stage|{lhs}", {**base, "_lhs": lhs}, op, mean_day * days
    if uc.get("scope") == "stage":
        return f"stage|{lhs}", {**base, "_lhs": lhs}, op, rhs
    AUDIT_TAG["tag"] = "uc_rhs"
    prof = spec.reduce(rhs_h) if rhs_h is not None else np.full(spec.nb, rhs)
    return f"block|{lhs}", {**base, "_lhs": lhs}, op, prof


#: how the yearly capacity constraint aggregates the monthly RHS (each
#: month folds the fleet in service on its first day)
CAPACITY_YEAR_FLEET = ("mean", "end", "tightest")


def _fy_capacity_rhs(
    rhs: np.ndarray,
    fy_start: list[bool],
    op: str,
    off: float,
    fleet: str = "mean",
) -> np.ndarray:
    """Yearly capacity constraint → one row at each FY's first stage.

    Installed capacity only changes at the FY build stage, so the PLEXOS
    ``RHS Year`` constraint is enforced once per FY; the other months are
    inactive.  This keeps every month feasible for any state the build
    stage hands over — needed by SDDP, where a later month cannot change
    the capacity decided in July.  The existing fleet folded into the
    monthly RHS changes during the year (units commissioned or retired
    mid-year); ``fleet`` picks the yearly value: ``mean`` (default: the
    FY's time-averaged installed capacity, PLEXOS' yearly quantity),
    ``end`` (the last live month) or ``tightest`` (the most restrictive
    month — a ≥ target then sees only the 1-July fleet, which makes e.g.
    the 2026 ISP ``CIS Dispatchable SA`` FY2028 target infeasible next to
    the hard ``Adelaide Battery Limit``).
    """
    if fleet not in CAPACITY_YEAR_FLEET:
        raise ValueError(f"unknown capacity year fleet {fleet!r}")
    out = np.full_like(rhs, off)

    def pick(v: np.ndarray) -> float:
        if fleet == "mean":
            return float(np.mean(v))
        if fleet == "end":
            return float(v[-1])
        return float(np.min(v) if op == "<=" else np.max(v))

    for s, first in enumerate(fy_start):
        if not first:
            continue
        fy = [s]
        for k in range(s + 1, len(fy_start)):
            if fy_start[k]:
                break
            fy.append(k)
        live = [k for k in fy if abs(rhs[k, 0]) < abs(off)]
        if live:
            out[s] = pick(rhs[live, 0])
    return out


def _battery_candidate(
    c: Candidate,
    bstage: list[bool],
    nrep: int,
    opts: ExpansionOptions,
    fy_index: list[int],
) -> dict[str, Any]:
    """gtopt Battery with power + energy expansion for a storage candidate.

    Units of ``Max Power`` MW / ``Capacity`` MWh; the annuity
    (``Build Cost`` $/kW of power) is charged on the power axis and the
    energy axis is tied to it by ``energy_power_ratio`` = duration [h].
    """
    n_st = len(bstage)
    mub = [c.max_units[s // nrep] for s in range(n_st)]
    pmax = max(mub) * c.unit_mw
    cap = [
        round(
            annual_capcost(c.build_cost[s // nrep], c.wacc, c.life, c.fom)
            * life_factor(c.life, opts.years - fy_index[s]),
            3,
        )
        for s in range(n_st)
    ]
    expmod = [mub[s] if bstage[s] else 0.0 for s in range(n_st)]
    phes = "PHES" in c.category
    entry: dict[str, Any] = {
        "name": c.name,
        "bus": c.bus,
        "type": "candidate_" + ("phes" if phes else "battery"),
        "description": f"storage expansion candidate [{c.category}]",
        "input_efficiency": c.eff_in,
        "output_efficiency": c.eff_out,
        "emin": 0.0,
        "emax": max(mub) * c.unit_mwh,
        "pmax_charge": pmax,
        "pmax_discharge": pmax,
        "capacity": 0.0,
        "expcap": c.unit_mwh,
        "expmod": expmod,
        "capmax": [m * c.unit_mwh for m in mub],
        "annual_capcost": 0.0,
        "power_capacity": 0.0,
        "power_expcap": c.unit_mw,
        "power_expmod": expmod,
        "power_capmax": [m * c.unit_mw for m in mub],
        "power_annual_capcost": cap,
        "energy_power_ratio": c.unit_mwh / c.unit_mw,
    }
    if phes:
        # Pumped hydro has no electrochemical self-discharge: pin 0 so the
        # gtopt Li-ion default
        # (``model_options.battery_annual_standing_loss``) does not drain
        # the upper reservoir.
        entry["annual_standing_loss"] = 0.0
    return entry


def _net_load(
    system: dict[str, Any], ws: dict[tuple[str, str], Any], nd: int
) -> np.ndarray:
    """Demand minus available wind + solar of the month window (one value
    per trace step)."""
    n = nd * steps_per_day()
    net = np.zeros(n)
    for d in system["demand_array"]:
        arr = ws.get(("Demand", "lmax"), {}).get(d["uid"])
        if arr is not None:
            net += np.nan_to_num(np.asarray(arr, float)[:n])
    for g in system["generator_array"]:
        if g.get("type") not in ("solar", "wind"):
            continue
        arr = ws.get(("Generator", "pmax"), {}).get(g["uid"])
        if arr is not None:
            net -= np.nan_to_num(np.asarray(arr, float)[:n])
    return net


def _auto_orders(
    opts: ExpansionOptions,
    months: list[date],
    roots: list[Path],
    db: PlexosDb,
    auto: tuple[str, float, str],
) -> list[int]:
    """Pre-pass of ``chebyshev:auto``: the smallest N per month whose
    capacity-weighted solar + wind fit error is ≤ tol (the case uses the
    maximum: every stage needs the same block count)."""
    _, tol, metric = auto
    probe = with_week_start(make_sampler(auto[0].format(n=64)), opts.week_start)
    assert isinstance(probe, ChebyshevSampler)
    step = 24.0 / steps_per_day()
    n_win = int(round(WEEK_HOURS / step))
    out = []
    for day0 in months:
        nd = days_in_month(day0)
        wk = build_dispatch_case(
            IspCaseOptions(
                xml=opts.xml,
                trace_roots=roots,
                start=day0,
                days=nd,
                name=opts.name,
                include_hydro=False,
                include_batteries=False,
                isp=isp_c.ConstraintOptions(constraints=False),
            ),
            db=db,
        )
        system = wk.planning["system"]
        net = _net_load(system, wk.schedules, nd)
        i0 = select_window(
            net,
            day0,
            probe.window_start,
            step,
            opts.week_rule,  # type: ignore[arg-type]
        )
        ys, caps = vre_window_traces(system, wk.schedules, i0, n_win)
        if not ys:
            out.append(32)
            continue
        ch = choose_order(
            ys,
            caps,
            step,
            tol=tol,
            kind=probe.kind,
            metric=metric,  # type: ignore[arg-type]
        )
        if not ch.met:
            logger.warning("chebyshev auto: tol %g not met in %s", tol, day0)
        out.append(ch.n)
    return out


def _auto_day_orders(
    opts: ExpansionOptions,
    months: list[date],
    roots: list[Path],
    db: PlexosDb,
    auto: tuple[str, float, str],
) -> list[int]:
    """Pre-pass of ``--day-sampler chebyshev:auto``: per month, the
    smallest N (of :data:`DAY_ORDERS`) whose capacity-weighted solar +
    wind error on every representative-day mean profile is ≤ tol."""
    _, tol, metric = auto
    probe = make_day_sampler(auto[0].format(n=24))
    spd = steps_per_day()
    step = 24.0 / spd
    nrep = max(1, opts.rep_days)
    out = []
    for day0 in months:
        nd = days_in_month(day0)
        wk = build_dispatch_case(
            IspCaseOptions(
                xml=opts.xml,
                trace_roots=roots,
                start=day0,
                days=nd,
                name=opts.name,
                include_hydro=False,
                include_batteries=False,
                isp=isp_c.ConstraintOptions(constraints=False),
            ),
            db=db,
        )
        system = wk.planning["system"]
        net = _net_load(system, wk.schedules, nd)
        if opts.rep_kind == "weekday" and nrep == 2:
            masks = weekday_masks(day0, nd)
        else:
            masks = day_masks(net[: nd * spd].reshape(nd, spd).sum(axis=1), nrep)
        ys_all, caps = vre_window_traces(system, wk.schedules, 0, nd * spd)
        n_month = DAY_ORDERS[0]
        for mk in masks:
            ys = [rep_mean(y, mk, spd) for y in ys_all]
            keep = [(y, c) for y, c in zip(ys, caps, strict=True) if np.any(y)]
            if not keep:
                continue
            ch = choose_order(
                [y for y, _ in keep],
                [c for _, c in keep],
                step,
                tol=tol,
                kind=probe.kind,
                candidates=DAY_ORDERS,
                metric=metric,  # type: ignore[arg-type]
                window=24.0,
            )
            if not ch.met:
                logger.warning("chebyshev day auto: tol %g not met in %s", tol, day0)
            n_month = max(n_month, ch.n)
        out.append(n_month)
    return out


def _guard_summary() -> dict[str, Any]:
    """Range-guard / energy-rescale statistics of the Chebyshev sampling."""
    factors = np.asarray(GUARD_STATS.rescale_factors, dtype=float)
    return {
        "calls": GUARD_STATS.calls,
        "clipped": GUARD_STATS.clipped,
        "energy_lost": GUARD_STATS.energy_lost,
        "rescale_count": int(factors.size),
        "rescale_max_dev": float(np.max(np.abs(factors - 1.0)))
        if factors.size
        else 0.0,
    }


def build_expansion_case(opts: ExpansionOptions, db: PlexosDb | None = None) -> IspCase:
    """Build the monthly-stage expansion case for ``opts``.

    Representative days read the traces hourly; a typical week reads
    them at ``week_steps_per_day`` (half-hourly by default)."""
    sampled = opts.week_sampler or opts.day_sampler
    spd = opts.week_steps_per_day if sampled else 24
    with trace_resolution(spd):
        return _build_expansion_case(opts, db)


def _build_expansion_case(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    opts: ExpansionOptions, db: PlexosDb | None = None
) -> IspCase:
    """Implementation of :func:`build_expansion_case`."""
    reset_guard_stats()
    report_years = opts.years
    opts = replace(opts, years=opts.years + opts.fill_years, fill_years=0)
    db = db or load_xml(opts.xml)
    roots = list(opts.trace_roots) or [Path(opts.xml).parent]
    res = IspResolver(db, TraceLocator(roots))
    months = month_starts(opts.first_fy, opts.years)
    auto = split_auto_order(opts.week_sampler) if opts.week_sampler else None
    auto_orders: list[int] = []
    if auto is not None:
        auto_orders = _auto_orders(opts, months, roots, db, auto)
        week_spec = auto[0].format(n=max(auto_orders))
        logger.info(
            "chebyshev auto order (tol %g, %s): per month %s -> N = %d",
            auto[1],
            auto[2],
            auto_orders,
            max(auto_orders),
        )
    else:
        week_spec = opts.week_sampler
    sampler = (
        with_week_start(make_sampler(week_spec), opts.week_start) if week_spec else None
    )
    if sampler is not None:
        sampler = with_ramp_refine(sampler, opts.ramp_refine, opts.ramp_refine_width)
    if opts.day_sampler and sampler is not None:
        raise ValueError("--day-sampler and --week-sampler are exclusive")
    day_sampler = None
    if opts.day_sampler:
        dauto = split_auto_order(opts.day_sampler)
        if dauto is not None:
            auto_orders = _auto_day_orders(opts, months, roots, db, dauto)
            day_spec = dauto[0].format(n=max(auto_orders))
            logger.info(
                "chebyshev day auto order (tol %g, %s): per month %s -> N = %d",
                dauto[1],
                dauto[2],
                auto_orders,
                max(auto_orders),
            )
        else:
            day_spec = opts.day_sampler
        day_sampler = make_day_sampler(day_spec)
        week_spec = "day:" + day_spec
    nrep = 1 if sampler is not None else max(1, opts.rep_days)
    if opts.rep_kind == "weekday" and sampler is None and nrep != 2:
        raise ValueError("rep_kind 'weekday' needs rep_days = 2")
    n_st = len(months) * nrep
    #: blocks per stage (set from the first month's layout)
    nb = 24
    ndays = [days_in_month(d) for d in months]
    #: days represented by each stage (filled per month below)
    stage_days = [0] * n_st
    month_specs: list[list[StageSpec]] = []
    fy_start = fy_start_stages(months, nrep)
    fy_index = list(np.cumsum(fy_start) - 1)
    cands = find_candidates(res, months, opts.candidate_prefixes)
    if opts.include_batteries and opts.battery_candidates:
        cands += find_battery_candidates(res, months, opts.candidate_prefixes)
    cap_on = {c.name: capainst_stages(months, c, nrep) for c in cands}
    stats: dict[str, Any] = defaultdict(int)
    sched: dict[tuple[str, str], dict[int, np.ndarray]] = defaultdict(dict)

    rate = opts.discount_rate
    if rate is None:
        rate = (
            lt_plan_discount_rate(Path(opts.xml)) if Path(opts.xml).exists() else None
        )
        rate = DEFAULT_DISCOUNT_RATE if rate is None else rate

    # ---- per-month dispatch windows ------------------------------------
    gens: dict[str, dict[str, Any]] = {}
    gen_pmax: dict[str, np.ndarray] = {}
    gen_gcost: dict[str, np.ndarray] = {}
    dems: dict[str, dict[str, Any]] = {}
    dem_lmax: dict[str, np.ndarray] = {}
    lines: dict[str, dict[str, Any]] = {}
    line_t: dict[tuple[str, str], np.ndarray] = {}
    bats: dict[str, dict[str, Any]] = {}
    bat_tb: dict[tuple[str, str], np.ndarray] = {}
    hydro: dict[str, list[dict[str, Any]]] = {}
    inflow: dict[str, np.ndarray] = {}
    ucs: dict[str, dict[str, _UcVariant]] = defaultdict(dict)
    buses: list[dict[str, Any]] = []
    dvars: dict[str, dict[str, Any]] = {}
    gen_obj = {o.name: o for o in res.objects("Generator")}
    bat_obj = {o.name: o for o in res.objects("Battery")}
    #: existing installed capacity per stage (Units × Max Capacity / Power)
    inst: dict[str, tuple[str, str, str, np.ndarray]] = {}
    voll = opts.demand_fail_cost
    groups: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for m, (day0, nd) in enumerate(zip(months, ndays)):
        s0 = m * nrep
        names = frozenset(c.name for c in cands if cap_on[c.name][s0])
        wopts = IspCaseOptions(
            xml=opts.xml,
            trace_roots=roots,
            start=day0,
            days=nd,
            name=opts.name,
            include_hydro=opts.include_hydro,
            include_batteries=opts.include_batteries,
            demand_fail_cost=opts.demand_fail_cost,
            isp=isp_c.ConstraintOptions(
                constraints=opts.constraints,
                purchasers=False,
                emissions=False,
                fuels=False,
                rez_buses=False,
                long_periods="prorate",
                expansion=True,
            ),
            expansion_names=names,
        )
        wk = build_dispatch_case(wopts, db=db)
        system = wk.planning["system"]
        voll = wk.planning["options"]["model_options"]["demand_fail_cost"]
        ws = wk.schedules
        specs = month_layout(
            opts, m, day0, nd, _net_load(system, ws, nd), sampler, day_sampler
        )
        if m == 0:
            nb = specs[0].nb
        if len(specs) != nrep or any(sp.nb != nb for sp in specs):
            raise ValueError("every stage needs the same number of blocks")
        month_specs.append(specs)
        for k, sp in enumerate(specs):
            stage_days[s0 + k] = sp.days
        stg = range(s0, s0 + nrep)
        if m == 0:
            buses = system["bus_array"]
            hydro = {
                k: list(system.get(k, []))
                for k in (
                    "junction_array",
                    "reservoir_array",
                    "waterway_array",
                    "turbine_array",
                    "flow_array",
                    "pump_array",
                )
            }
        else:
            # hydro topology is the union over the months: units
            # commissioned later (e.g. Snowy 2.0 turbines and pumps) must
            # keep their water coupling; before commissioning their
            # generator / pump-load capacity is 0 in the schedules
            for key, elems in hydro.items():
                known = {e["name"] for e in elems}
                for e in system.get(key, []):
                    if e["name"] not in known:
                        elems.append({**e, "uid": len(elems) + 1})
                        stats[f"hydro_added_later_{key}"] += 1
        for k, v in wk.stats.get("constraint_groups", {}).items():
            for r, n in v.items():
                groups[k][r] += n
        for g in system["generator_array"]:
            name = g["name"]
            o = gen_obj[name]
            inst.setdefault(
                name, ("Generator", res.category(o), g.get("type", ""), np.zeros(n_st))
            )[3][stg] = (res.scalar(o.object_id, "Units", day0, 0.0) or 0.0) * (
                res.scalar(o.object_id, "Max Capacity", day0, 0.0) or 0.0
            )
            if name not in gens:
                gens[name] = {
                    k: v
                    for k, v in g.items()
                    if k not in ("uid", "pmax", "capacity", "gcost")
                }
                gen_pmax[name] = np.zeros((n_st, nb))
                gen_gcost[name] = np.full(n_st, float(g["gcost"]))
            cap = g.get("capacity")
            ghi = float(cap) if isinstance(cap, (int, float)) else np.inf
            AUDIT_TAG["tag"] = f"gen_pmax:{g.get('type', '')}"
            for k, sp in enumerate(specs):
                gen_pmax[name][s0 + k] = sp.reduce(
                    ws[("Generator", "pmax")][g["uid"]], (0.0, ghi)
                )
            gen_gcost[name][stg] = float(g["gcost"])
        for d in system["demand_array"]:
            name = d["name"]
            if name not in dems:
                dems[name] = {k: v for k, v in d.items() if k not in ("uid",)}
                dem_lmax[name] = np.zeros((n_st, nb))
            arr = ws.get(("Demand", "lmax"), {}).get(d["uid"])
            if arr is None:
                # demand without a trace (pump load: capacity only) —
                # its capacity as a flat lmax for this month, 0 in the
                # months where it does not exist yet
                arr = np.full(nd * steps_per_day(), float(d.get("capacity", 0.0)))
            AUDIT_TAG["tag"] = "demand_lmax"
            for k, sp in enumerate(specs):
                dem_lmax[name][s0 + k] = sp.reduce(arr, (0.0, np.inf))
        for ln in system["line_array"]:
            name = ln["name"]
            if name not in lines:
                lines[name] = {k: v for k, v in ln.items() if k != "uid"}
                for f in ("tmax_ab", "tmax_ba"):
                    line_t[(name, f)] = np.zeros((n_st, nb))
            AUDIT_TAG["tag"] = "line_tmax"
            for f in ("tmax_ab", "tmax_ba"):
                for k, sp in enumerate(specs):
                    line_t[(name, f)][s0 + k] = sp.reduce(
                        ws[("Line", f)][ln["uid"]], (0.0, np.inf)
                    )
        for b in system.get("battery_array", []):
            name = b["name"]
            inst.setdefault(
                name,
                ("Battery", res.category(bat_obj[name]), "battery", np.zeros(n_st)),
            )[3][stg] = float(b["pmax_discharge"])
            if name not in bats:
                bats[name] = {
                    k: v
                    for k, v in b.items()
                    if k not in ("uid", *_BATTERY_TB_FIELDS, "eini", "efin")
                }
                bats[name]["_first"] = s0
                bats[name]["_eini"] = b.get("eini")
                for f in _BATTERY_TB_FIELDS:
                    bat_tb[(name, f)] = np.zeros((n_st, nb))
            for f in _BATTERY_TB_FIELDS:
                bat_tb[(name, f)][stg] = float(b[f])
        for fl in system.get("flow_array", []):
            arr = ws.get(("Flow", "discharge"), {}).get(fl["uid"])
            if arr is not None:
                q = inflow.setdefault(fl["name"], np.zeros((n_st, nb)))
                AUDIT_TAG["tag"] = "inflow"
                for k, sp in enumerate(specs):
                    q[s0 + k] = sp.reduce(arr, (0.0, np.inf))
        for dv in system.get("decision_variable_array", []):
            dvars.setdefault(dv["name"], {k: v for k, v in dv.items() if k != "uid"})
        uc_rhs = ws.get(("UserConstraint", "rhs"), {})
        for uc in system.get("user_constraint_array", []):
            if "[" in uc["expression"]:
                stats["uc_skipped_profile_coefficient"] += 1
                continue
            try:
                folded = [_fold_uc(uc, uc_rhs.get(uc["uid"]), sp) for sp in specs]
            except ValueError:
                stats["uc_skipped_unparsed"] += 1
                continue
            for k, (key, base, op, rhs) in enumerate(folded):
                var = ucs[uc["name"]].setdefault(key, _UcVariant(base=base, op=op))
                var.rhs[s0 + k] = rhs
        logger.info(
            "month %s: %d gens, %d ucs, %d candidates with capainst",
            day0,
            len(system["generator_array"]),
            len(system.get("user_constraint_array", [])),
            len(names),
        )

    # ---- assemble ---------------------------------------------------------
    generator_array: list[dict[str, Any]] = []
    for name, g in gens.items():
        uid = len(generator_array) + 1
        # file-backed schedules of the base (hourly) conversion other than
        # the regenerated pmax / gcost (e.g. a fixed-load ``pmin``) have no
        # monthly table here: drop them rather than reference missing files.
        for fld in [k for k, v in g.items() if v == k and k not in ("pmax", "gcost")]:
            logger.warning("generator %s: dropped file field %s", name, fld)
            stats["dropped_file_fields"] += 1
            g = {k: v for k, v in g.items() if k != fld}
        generator_array.append(
            {
                **g,
                "uid": uid,
                "pmax": "pmax",
                "gcost": "gcost",
                "capacity": round(float(gen_pmax[name].max()), 6),
            }
        )
        sched[("Generator", "pmax")][uid] = gen_pmax[name]
        sched[("Generator", "gcost")][uid] = np.repeat(
            gen_gcost[name][:, None], nb, axis=1
        )
        stats[f"existing_{g.get('type', 'other')}_max_mw"] += float(
            gen_pmax[name].max()
        )
    profile_array: list[dict[str, Any]] = []
    gen_fuel_price: dict[int, list[float]] = {}
    battery_candidates: list[dict[str, Any]] = []
    for c in cands:
        if c.kind == "battery":
            battery_candidates.append(
                _battery_candidate(
                    c, build_stages(months, c, nrep), nrep, opts, fy_index
                )
            )
            stats["candidates"] += 1
            stats["candidates_storage"] += 1
            continue
        uid = len(generator_array) + 1
        bstage = build_stages(months, c, nrep)
        # Economic life shorter than the rest of the horizon: gtopt's
        # capacost keeps charging the annuity to the horizon end, so scale
        # it by life / remaining years (same undiscounted total payment).
        cap = [
            annual_capcost(c.build_cost[s // nrep], c.wacc, c.life, c.fom)
            * life_factor(c.life, opts.years - fy_index[s])
            for s in range(n_st)
        ]
        price = [0.0] * n_st
        if c.heat_rate and c.fuel_oid is not None:
            price = gen_fuel_price.setdefault(
                c.fuel_oid,
                [
                    res.scalar(c.fuel_oid, "Price", months[s // nrep], 0.0) or 0.0
                    for s in range(n_st)
                ],
            )
        gcost = [round(c.vom + (c.heat_rate or 0.0) * p, 6) for p in price]
        generator_array.append(
            {
                "uid": uid,
                "name": c.name,
                "bus": c.bus,
                "type": "candidate_" + _gen_type(c.category, c.name),
                "description": f"expansion candidate [{c.category}]",
                # gcost is a (stage, block) schedule: one row per stage
                "gcost": [[v] * nb for v in gcost] if len(set(gcost)) > 1 else gcost[0],
                "capacity": 0.0,
                "expcap": c.unit_mw,
                "expmod": [
                    c.max_units[s // nrep] if bstage[s] else 0.0 for s in range(n_st)
                ],
                "capmax": [c.max_units[s // nrep] * c.unit_mw for s in range(n_st)],
                "annual_capcost": [round(v, 3) for v in cap],
            }
        )
        prof = np.zeros((n_st, nb))
        if c.energy:
            for m, (day0, nd) in enumerate(zip(months, ndays)):
                r = res.hourly(c.oid, "Rating", day0, nd)
                stg = range(m * nrep, (m + 1) * nrep)
                if r is None or not np.any(np.nan_to_num(r) > 0):
                    prof[stg] = 1.0
                    continue
                rf = res.hourly(c.oid, "Rating Factor", day0, nd)
                if rf is not None:
                    r = r * np.where(np.isnan(rf), 100.0, rf) / 100.0
                AUDIT_TAG["tag"] = f"candidate:{c.category}"
                for k, sp in enumerate(month_specs[m]):
                    prof[m * nrep + k] = np.clip(
                        sp.reduce(np.nan_to_num(r), (0.0, c.unit_mw)) / c.unit_mw,
                        0.0,
                        1.0,
                    )
            prof *= 1.0 - c.aux
        pid = len(profile_array) + 1
        profile_array.append(
            {
                "uid": pid,
                "name": f"profile_{c.name}",
                "generator": c.name,
                "profile": "profile",
            }
        )
        sched[("GeneratorProfile", "profile")][pid] = prof
        stats["candidates"] += 1
        stats["candidates_energy" if c.energy else "candidates_network"] += 1
        stats["candidate_max_mw"] += max(c.max_units) * c.unit_mw

    demand_array: list[dict[str, Any]] = []
    for name, d in dems.items():
        uid = len(demand_array) + 1
        demand_array.append({**d, "uid": uid, "lmax": "lmax"})
        sched[("Demand", "lmax")][uid] = dem_lmax[name]
    line_array: list[dict[str, Any]] = []
    for name, ln in lines.items():
        uid = len(line_array) + 1
        line_array.append(
            {**ln, "uid": uid, "tmax_ab": "tmax_ab", "tmax_ba": "tmax_ba"}
        )
        for f in ("tmax_ab", "tmax_ba"):
            sched[("Line", f)][uid] = line_t[(name, f)]
    battery_array: list[dict[str, Any]] = []
    for name, b in bats.items():
        item = {k: v for k, v in b.items() if not k.startswith("_")}
        item["uid"] = len(battery_array) + 1
        tables = {
            f: [[round(float(v), 6) for v in row] for row in bat_tb[(name, f)]]
            for f in _BATTERY_TB_FIELDS
        }
        if b["_first"] == 0 and b["_eini"] is not None:
            item["eini"] = _feasible_cycle_eini(name, b["_eini"], tables)
        for f in _BATTERY_TB_FIELDS:
            if f in _BATTERY_FILE_FIELDS:
                # the level bounds go to Battery/<field>.parquet (inline
                # tables made the planning JSON large); the power limits
                # stay inline: gtopt moves them to the battery's synthetic
                # generator / demand, whose uids are not the battery's
                item[f] = f
                sched[("Battery", f)][item["uid"]] = np.asarray(tables[f], dtype=float)
            else:
                item[f] = tables[f]
        item["daily_cycle"] = True
        battery_array.append(item)
    for item in battery_candidates:
        if item["name"] in bats:
            logger.warning("battery candidate %s is also existing", item["name"])
            continue
        battery_array.append(
            {**item, "uid": len(battery_array) + 1, "daily_cycle": True}
        )
    flow_array: list[dict[str, Any]] = []
    for fl in hydro.get("flow_array", []):
        if fl["name"] in inflow:
            item = {**fl, "uid": len(flow_array) + 1}
            flow_array.append(item)
            sched[("Flow", "discharge")][item["uid"]] = inflow[fl["name"]]
    # Reservoirs: the water value couples the stages through the volume
    # state; only the horizon end has a terminal condition, and it is 0
    # (no efin, no efin_cost — the fill year(s) damp the end effect).
    # Kept from the PLEXOS data: Min/Max Volume, ``End Effects Method`` =
    # Recycle (2) and ``Target Week`` -> the volume cycles back to its
    # initial value within every stage (``daily_cycle``: each stage is a
    # representative day repeated over its days).
    storage_oid = {o.name: o.object_id for o in res.objects("Storage")}
    for r in hydro.get("reservoir_array", []):
        r.pop("efin", None)
        r.pop("efin_cost", None)
        oid = storage_oid.get(r["name"])
        if oid is None:
            continue
        eem = res.scalar(oid, "End Effects Method", months[0], 0.0) or 0.0
        if int(eem) == 2 or res.has(oid, "Target Week"):
            r["daily_cycle"] = True
            stats["reservoirs_cycled"] += 1
        else:
            stats["reservoirs_free_terminal"] += 1

    user_constraint_array: list[dict[str, Any]] = []
    for name, variants in sorted(ucs.items()):
        multi = len(variants) > 1
        for k, var in enumerate(variants.values()):
            if var.op == "=" and len(var.rhs) < n_st:
                stats["uc_skipped_equality_partial"] += 1
                continue
            uid = len(user_constraint_array) + 1
            base = dict(var.base)
            lhs = base.pop("_lhs")
            is_capacity = base.pop("_capacity", False)
            off = INACTIVE_RHS if var.op == "<=" else -INACTIVE_RHS
            stage_scope = base.get("scope") == "stage"
            first = next(iter(var.rhs.values()))
            item = {
                **base,
                "uid": uid,
                "name": f"{name}_v{k + 1}" if multi else name,
                "expression": f"{lhs} {var.op} {isp_c._fmt(float(np.max(first)))}",
            }
            rhs = np.full((n_st, nb), off)
            for s, v in var.rhs.items():
                rhs[s] = v
            if is_capacity and opts.capacity_penalty and "penalty" not in base:
                item["penalty"] = opts.capacity_penalty
            if is_capacity:
                rhs = _fy_capacity_rhs(
                    rhs, fy_start, var.op, off, opts.capacity_year_fleet
                )
            # stages without the constraint: per-stage ``active_stages`` (no LP
            # row) instead of a huge never-binding RHS
            live = np.abs(rhs[:, 0]) < abs(off)
            if not live.any():
                stats["uc_skipped_never_active"] += 1
                continue
            if not live.all():
                item["active_stages"] = [int(v) for v in live]
                rhs[~live] = rhs[live][0]
            item["expression"] = f"{lhs} {var.op} {isp_c._fmt(float(rhs[live][0, 0]))}"
            if stage_scope:
                vals = rhs[:, 0]
                if not np.allclose(vals, vals[0]):
                    # TB matrix (one row per stage; a stage row needs
                    # only one value: the stage-scope row reads block 0)
                    item["rhs"] = [[round(float(v), 6)] * nb for v in vals]
            elif not np.allclose(rhs, rhs.flat[0]):
                item["rhs"] = "rhs"
                sched[("UserConstraint", "rhs")][uid] = rhs
            user_constraint_array.append(item)

    # ---- simulation -------------------------------------------------------
    block_array: list[dict[str, Any]] = []
    stage_array: list[dict[str, Any]] = []
    all_specs = [sp for specs in month_specs for sp in specs]
    for s, sp in enumerate(all_specs):
        stage: dict[str, Any] = {
            "uid": s + 1,
            "first_block": nb * s,
            "count_block": nb,
        }
        if sp.period_hours is not None:
            stage["period_hours"] = sp.period_hours
        if sp.times is not None:
            # Chebyshev nodes: block values are point samples at Block.time
            stage["sampling"] = "point"
        if sp.cycle_group is not None:
            stage["cycle_group"] = sp.cycle_group
            stage["cycle_weight"] = sp.cycle_weight
        stage_array.append(stage)
        for h in range(nb):
            blk: dict[str, Any] = {
                "uid": nb * s + h + 1,
                "duration": round(float(sp.durations[h]), 9),
            }
            if sp.times is not None:
                blk["time"] = round(float(sp.times[h]), 9)
            if sp.cells is not None:
                # refined blocks share their base block's commitment
                blk["commitment_cell"] = int(sp.cells[h])
            block_array.append(blk)
    simulation: dict[str, Any] = {
        "block_array": block_array,
        "stage_array": stage_array,
        "scenario_array": [{"uid": 1, "probability_factor": 1.0}],
    }
    if opts.phases != "none":
        per = nrep if opts.phases == "month" else 12 * nrep
        simulation["phase_array"] = [
            {"uid": k + 1, "first_stage": k * per, "count_stage": per}
            for k in range(n_st // per)
        ]

    system_out: dict[str, Any] = {
        "name": opts.name,
        "bus_array": buses,
        "demand_array": demand_array,
        "generator_array": generator_array,
        "generator_profile_array": profile_array,
        "line_array": line_array,
    }
    if battery_array:
        system_out["battery_array"] = battery_array
    for key, elems in hydro.items():
        elems = flow_array if key == "flow_array" else elems
        if elems:
            system_out[key] = elems
    if user_constraint_array:
        system_out["user_constraint_array"] = user_constraint_array
    if dvars:
        system_out["decision_variable_array"] = [
            {**dv, "uid": i + 1} for i, dv in enumerate(dvars.values())
        ]
    planning = {
        "options": {
            "input_directory": ".",
            "input_format": "parquet",
            "output_format": "parquet",
            "annual_discount_rate": rate,
            "model_options": {
                "use_single_bus": False,
                "use_kirchhoff": False,
                "demand_fail_cost": voll,
                "scale_objective": 1000.0,
                **(
                    point_storage_options(opts.point_storage_integration)
                    if any(st.get("sampling") == "point" for st in stage_array)
                    else {}
                ),
            },
        },
        "simulation": simulation,
        "system": system_out,
    }
    stats.update(
        {
            "first_fy": opts.first_fy,
            "years": opts.years,
            "report_years": report_years,
            "fill_years": opts.years - report_years,
            "stages": n_st,
            "rep_days": nrep,
            "stage_days": stage_days,
            "blocks": nb * n_st,
            "blocks_per_stage": nb,
            "steps_per_day": steps_per_day(),
            "period": week_spec or f"rep_days:{nrep}:{opts.rep_kind}",
            "chebyshev_auto_orders": auto_orders,
            "range_guard": _guard_summary(),
            "cycle_group": opts.cycle_group,
            "week_start": opts.week_start if sampler is not None else None,
            "ramp_refine": opts.ramp_refine if sampler is not None else None,
            "week_window_start_hour": [
                sp.window_start for sp in all_specs if sp.window_start is not None
            ],
            "phases": len(simulation.get("phase_array", [])) or 1,
            "discount_rate": rate,
            "generators": len(generator_array),
            "batteries": len(battery_array),
            "user_constraints": len(user_constraint_array),
            "constraint_groups": {k: dict(v) for k, v in sorted(groups.items())},
            "existing_installed_mw": [
                {
                    "name": n,
                    "class": c,
                    "category": cat,
                    "type": t,
                    "mw": [round(float(v), 3) for v in mw],
                }
                for n, (c, cat, t, mw) in sorted(inst.items())
            ],
            "candidate_categories": {c.name: c.category for c in cands},
        }
    )
    return IspCase(planning=planning, schedules=dict(sched), stats=dict(stats))


# ---------------------------------------------------------------------------
# writer + CLI
# ---------------------------------------------------------------------------

#: classes whose schedules are per-(scenario, stage, block)
_STB_CLASSES = {"Flow", "GeneratorProfile"}


def write_expansion_case(case: IspCase, out_dir: Path, name: str) -> Path:
    """Write ``<out_dir>/<name>.json`` and the (stage, block) Parquet files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for (cls, fld), cols in case.schedules.items():
        if not cols:
            continue
        frames = []
        for uid, arr in sorted(cols.items()):
            a = np.asarray(arr, dtype=np.float64)
            n_st, nb = a.shape
            stage = np.repeat(np.arange(1, n_st + 1, dtype=np.int32), nb)
            block = np.arange(1, n_st * nb + 1, dtype=np.int32)
            df = pd.DataFrame(
                {
                    "stage": stage,
                    "block": block,
                    "uid": np.full(n_st * nb, uid, dtype=np.int32),
                    "value": np.round(a.reshape(-1), 6),
                }
            )
            if cls in _STB_CLASSES:
                df.insert(0, "scenario", np.ones(len(df), dtype=np.int32))
            frames.append(df)
        d = out_dir / cls
        d.mkdir(parents=True, exist_ok=True)
        pd.concat(frames, ignore_index=True).to_parquet(
            d / f"{fld}.parquet", index=False
        )
    path = out_dir / f"{name}.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(case.planning, fh, indent=1, allow_nan=False, default=_json_default)
        fh.write("\n")
    return path


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m plexos2gtopt.aemo_isp_expansion",
        description="Build a monthly-stage gtopt expansion case from an AEMO ISP PLEXOS XML.",
    )
    p.add_argument("xml", type=Path)
    p.add_argument("-t", "--trace-root", type=Path, action="append", default=[])
    p.add_argument(
        "--first-fy",
        type=int,
        default=2028,
        help="first FY (ending year, 2028 = 2027-28)",
    )
    p.add_argument("--years", type=int, default=3, help="reported FYs")
    p.add_argument(
        "--fill-years",
        type=int,
        default=1,
        help="unreported FYs appended to damp end effects (default 1)",
    )
    p.add_argument("--phases", choices=("none", "month", "year"), default="none")
    p.add_argument(
        "--rep-days",
        type=int,
        choices=(1, 2),
        default=1,
        help="representative days per month (2: high / low net-load days)",
    )
    p.add_argument(
        "--rep-kind",
        choices=("netload", "weekday"),
        default="netload",
        help="split of --rep-days 2: high/low net load or weekdays/weekend",
    )
    p.add_argument(
        "--cycle-group",
        action="store_true",
        help="option 3: the representative days of a month form one "
        "weighted storage cycle (Stage.cycle_group)",
    )
    p.add_argument(
        "--week-sampler",
        default="",
        help="option 1: one typical week per month; uniform:H[:point], "
        "peak_ramp:K or chebyshev:N[:gauss|lobatto][:key=value]; "
        "chrono:D = hourly blocks over the first D days of every month "
        "(chronological reference) "
        "(Chebyshev is provisional)",
    )
    p.add_argument(
        "--day-sampler",
        default="",
        help="Chebyshev nodes on each representative day instead of 24 "
        "hourly blocks: chebyshev:N[:key=value] or "
        "chebyshev:auto[:tol=T][:metric=rms|ramp] (VRE fit rule; 24 h "
        "periodic window, half-hourly traces; provisional)",
    )
    p.add_argument(
        "--week-start",
        default="wed:12",
        help="start of the typical-week window, <weekday>:<hour> "
        "(default wed:12: Wednesday 12:00 to Wednesday 12:00, a week "
        "centred on the weekend); not used by chrono:D",
    )
    p.add_argument(
        "--ramp-refine",
        default="off",
        help="refine the typical week to 30m or 15m blocks within "
        "± --ramp-refine-width h of each sunrise / sunset net-load ramp "
        "(detected per day from the data); block and uniform:H:point "
        "samplers only (default off)",
    )
    p.add_argument(
        "--point-storage-integration",
        default=POINT_STORAGE_INTEGRATION,
        choices=(
            "lg4_pos",
            "lg4",
            "lg6_pos",
            "lg6",
            "trapezoid",
            "cc",
            "clenshaw_curtis",
            "block",
            "none",
        ),
        help="storage_integration of batteries and reservoirs on point "
        "(Chebyshev / uniform:H:point) stages (default cc = Clenshaw-Curtis, "
        "one node per row, the sparsest and fastest; lg4_pos / trapezoid add "
        "envelope rows; none = gtopt's per-type defaults)",
    )
    p.add_argument(
        "--ramp-refine-width",
        type=float,
        default=1.0,
        help="half width [h] of every ramp-refinement window (default 1)",
    )
    p.add_argument(
        "--week-rule",
        choices=("peak", "energy"),
        default="peak",
        help="typical week: the one with the month's net-load peak or the "
        "highest net energy",
    )
    p.add_argument(
        "--discount-rate",
        type=float,
        default=None,
        help="default: LT Plan Discount Rate",
    )
    p.add_argument("--no-hydro", action="store_true")
    p.add_argument("--no-batteries", action="store_true")
    p.add_argument("--no-isp-constraints", action="store_true")
    p.add_argument("--demand-fail-cost", type=float, default=None)
    p.add_argument(
        "--capacity-year-fleet",
        choices=CAPACITY_YEAR_FLEET,
        default="mean",
        help="yearly value of the existing fleet in the capacity "
        "constraints: mean (time-averaged over the FY, default), end "
        "(last month) or tightest (most restrictive month)",
    )
    p.add_argument(
        "--capacity-penalty",
        type=float,
        default=None,
        help="slack cost of the hard yearly capacity constraints "
        "(recommended with --phases month|year)",
    )
    p.add_argument(
        "--candidate-prefix",
        action="append",
        default=[],
        help="keep candidates whose category starts with this (repeatable)",
    )
    p.add_argument("-o", "--output-dir", type=Path, required=True)
    p.add_argument("-n", "--name", default=None)
    p.add_argument("-l", "--log-level", default="INFO")
    return p.parse_args(argv)


def _git_revision() -> str | None:
    """Commit of the converter checkout (None outside a git tree)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level, format="%(asctime)s %(levelname)s %(message)s"
    )
    name = args.name or f"isp_exp_fy{args.first_fy}_{args.years}y"
    opts = ExpansionOptions(
        xml=args.xml,
        trace_roots=[args.xml.parent, *args.trace_root],
        first_fy=args.first_fy,
        years=args.years,
        fill_years=args.fill_years,
        name=name,
        phases=args.phases,
        rep_days=args.rep_days,
        rep_kind=args.rep_kind,
        cycle_group=args.cycle_group,
        week_sampler=args.week_sampler,
        day_sampler=args.day_sampler,
        week_rule=args.week_rule,
        week_start=args.week_start,
        ramp_refine=args.ramp_refine,
        ramp_refine_width=args.ramp_refine_width,
        point_storage_integration=args.point_storage_integration,
        discount_rate=args.discount_rate,
        include_hydro=not args.no_hydro,
        include_batteries=not args.no_batteries,
        constraints=not args.no_isp_constraints,
        demand_fail_cost=args.demand_fail_cost,
        capacity_penalty=args.capacity_penalty,
        capacity_year_fleet=args.capacity_year_fleet,
        candidate_prefixes=tuple(args.candidate_prefix),
    )
    case = build_expansion_case(opts)
    # provenance: the exact command and converter revision, so a published
    # case can be regenerated with the same pinned flags
    case.stats["command"] = [
        "python3",
        "-m",
        "plexos2gtopt.aemo_isp_expansion",
        *(sys.argv[1:] if argv is None else list(argv)),
    ]
    case.stats["converter_revision"] = _git_revision()
    path = write_expansion_case(case, args.output_dir, name)
    (args.output_dir / f"{name}.stats.json").write_text(
        json.dumps(case.stats, indent=1, sort_keys=True, default=_json_default) + "\n"
    )
    logger.info("wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
