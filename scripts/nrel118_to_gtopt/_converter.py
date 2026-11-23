# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Core conversion logic for the NREL-118 → gtopt port.

Builds a **full-network, multi-block, multi-stage** gtopt case from the
NREL-118 test system (Peña, Martinez-Anido, Hodge 2018) CSVs:

* 118 buses, 186 lines (DC power flow: ``reactance`` in p.u. on a
  100 MVA base, ``tmax_ab`` / ``tmax_ba`` from ``Max/Min Flow``; bus 69
  is the angle reference, as in the NREL-Sienna loader),
* every generator at its bus (``Generators.csv``); thermal SRMC =
  VO&M + collapsed heat rate × fuel price, IPCC AR6 CO₂ factors through
  ``Fuel`` / ``EmissionFactor``,
* regional hourly load (``LoadR{1,2,3}DA.csv``) distributed to the buses
  by the per-bus load participation factors (``partfact.csv``) and
  written as ``Demand/lmax.parquet``,
* hourly availability as ``GeneratorProfile/profile.parquet``: wind /
  solar / run-of-river hydro from their DA MW series, ``Hydro 36-39``
  from their monthly MW, and the monthly energy budgets of the
  energy-limited ``Hydro 01-15`` shaped into an hourly cap,
* the hour → block → stage layout from :class:`gtopt_shared.time_grid.TimeGrid`.

Heat rate.  ``Generators.csv`` ships a five-band incremental heat-rate
model::

    HR_Base [MMBTU/hr]      — no-load fuel consumption
    HR_Inc_Band_k [BTU/kWh] — incremental fuel rate over MW band k
    Load_Point_Band_k [MW]  — cumulative MW breakpoint at the top of band k
    Min Stable Level [MW]   — pmin

gtopt's ``expand_fuel_emission_sources`` (``source/system.cpp``) requires a
SCALAR heat_rate; we collapse to a pmax-weighted segment-average
heat_rate at converter time::

    fuel_consumed [MMBTU/hr] = HR_Base
                             + Σ_k HR_Inc[k] [BTU/kWh] × band_width_k [MW] × 1e-3
    heat_rate [GJ/MWh]       = fuel_consumed / pmax × 1.055
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from gtopt_shared.time_grid import TimeGrid, write_block_schedule
from nrel118_to_gtopt._data import (
    REGIONS,
    profile_remote_path,
    read_hourly,
    read_rows,
    read_table,
    resolve,
)

logger = logging.getLogger(__name__)

# 1 MMBTU = 1.055 GJ; 1 BTU/kWh = 1.055e-3 GJ/MWh
MMBTU_PER_GJ = 1.055
BTU_PER_KWH_TO_GJ_PER_MWH = MMBTU_PER_GJ * 1.0e-3  # 0.001055 GJ/MWh per BTU/kWh

#: Hours in the NREL-118 study year (2024, a leap year).
YEAR_HOURS = 8784
YEAR_START = datetime.datetime(2024, 1, 1)

#: ``Line.voltage`` paired with a per-unit ``reactance``: gtopt's DC power
#: flow susceptance is ``V² / X``, so ``V = 10`` gives ``100 / x_pu`` —
#: the 100 MVA per-unit susceptance (same convention as pp2gtopt).
LINE_VOLTAGE_PU_100MVA = 10.0
#: Angle-reference bus of the NREL-Sienna loader (``data_118bus.jl``).
REFERENCE_BUS = 69

# IPCC AR6 WG3 Table A.III.2 combustion emission factors (tCO2/GJ).
# Renewable / nuclear carriers are explicit zeros so the converter can
# emit a Fuel + EmissionFactor row without per-MWh CO2 contribution.
IPCC_AR6_TCO2_PER_GJ: dict[str, float] = {
    "coal": 0.0946,  # subcritical bituminous
    "natural_gas": 0.0561,  # CT/CC/ICE common
    "diesel": 0.0741,  # gas/diesel oil
    "biomass": 0.0,  # biogenic-zero per IPCC AFOLU
    "nuclear": 0.0,
    "geothermal": 0.0,
    "hydro": 0.0,
    "wind": 0.0,
    "solar": 0.0,
    "other": 0.0741,  # conservative — treat unknown thermal as diesel-class
}

# Fuel prices [$/MMBtu] of the NREL-Sienna loader ``data_118bus.jl`` (the
# dataset's own production-cost setup): NG 5.4, coal 1.8, oil 21,
# biomass 2.4, geothermal 0.  ``other`` (ST Other) is priced as gas.
SIENNA_FUEL_PRICE_USD_PER_MMBTU: dict[str, float] = {
    "coal": 1.8,
    "natural_gas": 5.4,
    "diesel": 21.0,
    "biomass": 2.4,
    "nuclear": 0.72,
    "geothermal": 0.0,
    "hydro": 0.0,
    "wind": 0.0,
    "solar": 0.0,
    "other": 5.4,
}

# Representative US 2020s fuel prices per IPCC AR6 / EIA AEO [$/MMBtu]
# (``--fuel-prices typical``).  Matches the SRMC ranking used by
# ``test/source/test_emission_nrel118_port.cpp``.
TYPICAL_FUEL_PRICE_USD_PER_MMBTU: dict[str, float] = {
    "coal": 2.1,  # PRB sub-bituminous
    "natural_gas": 4.5,  # Henry Hub
    "diesel": 17.0,  # distillate fuel oil
    "biomass": 1.0,  # wood pellets
    "nuclear": 0.72,  # enriched UO2 fuel
    "geothermal": 0.0,
    "hydro": 0.0,
    "wind": 0.0,
    "solar": 0.0,
    "other": 4.5,
}

FUEL_PRICE_TABLES: dict[str, dict[str, float]] = {
    "sienna": SIENNA_FUEL_PRICE_USD_PER_MMBTU,
    "typical": TYPICAL_FUEL_PRICE_USD_PER_MMBTU,
}

RENEWABLE_FUELS = frozenset({"wind", "solar", "hydro", "geothermal"})
VARIABLE_FUELS = frozenset({"wind", "solar"})
COMMITMENT_MODES = ("none", "relaxed", "mip")


# Map the leading word(s) of a NREL-118 generator name to a fuel kind.
# The CSV row's first column carries human-readable names like
# "Biomass 01", "CC NG 02", "CT Oil 03" — sufficient to fingerprint the
# fuel without a separate carrier table.
def unit_type_to_fuel(name: str) -> str:
    """Return one of the keys of `IPCC_AR6_TCO2_PER_GJ`."""

    lower = name.lower()
    if lower.startswith("biomass"):
        return "biomass"
    if lower.startswith("hydro"):
        return "hydro"
    if lower.startswith("wind"):
        return "wind"
    if lower.startswith("solar"):
        return "solar"
    if lower.startswith("geo"):
        return "geothermal"
    if "nuc" in lower:
        return "nuclear"
    if "coal" in lower:
        return "coal"
    if "oil" in lower or "diesel" in lower:
        return "diesel"
    if " ng " in lower or lower.endswith(" ng") or "natural gas" in lower:
        return "natural_gas"
    return "other"


def bus_name(number: int) -> str:
    """Canonical NREL-118 bus name (``bus007``) of a bus number."""
    return f"bus{number:03d}"


def bus_number(name: str) -> int:
    """Bus number of a ``busNNN`` / ``nodeNNN`` / ``NNN`` name."""
    digits = "".join(ch for ch in name if ch.isdigit())
    if not digits:
        raise ValueError(f"cannot parse a bus number from {name!r}")
    return int(digits)


@dataclass
class NrelGen:  # pylint: disable=too-many-instance-attributes
    """Parsed row from NREL-118 ``Generators.csv``."""

    name: str
    bus: str
    pmax: float
    pmin: float
    vom: float  # $/MWh
    fuel: str
    heat_rate_gj_per_mwh: float = 0.0  # 0 ⇒ renewable / no fuel
    is_renewable: bool = False  # wind/solar/hydro/geo
    ramp_up_mw_per_min: float = 0.0  # 0 ⇒ unlimited
    ramp_down_mw_per_min: float = 0.0
    min_up_h: float = 0.0
    min_down_h: float = 0.0
    start_cost: float = 0.0  # $/start
    has_profile_file: bool = False  # ``Rating`` names an hourly data file

    @property
    def bus_uid(self) -> int:
        """Bus number (= gtopt bus uid) of the connection bus."""
        return bus_number(self.bus)


@dataclass
class NrelBus:
    """One ``Buses.csv`` row plus its regional load participation factor."""

    number: int
    region: str
    load_factor: float = 0.0

    @property
    def name(self) -> str:
        """Canonical ``busNNN`` name."""
        return bus_name(self.number)


@dataclass
class NrelLine:
    """One ``Lines.csv`` row."""

    name: str
    bus_a: int
    bus_b: int
    tmax_ab: float
    tmax_ba: float
    reactance_pu: float
    resistance_pu: float


@dataclass
class HydroBudget:
    """Monthly energy budget / capacity override of one hydro unit."""

    capacity: float | None = None  # base ``Max Capacity`` [MW]
    month_capacity: dict[int, float] = field(default_factory=dict)  # [MW]
    month_energy_gwh: dict[int, float] = field(default_factory=dict)  # [GWh]


@dataclass
class Conversion:  # pylint: disable=too-many-instance-attributes
    """End-to-end conversion artefacts (used by both the CLI and tests).

    Hourly arrays cover the selected window ``[hour_start, hour_stop)``.
    """

    hour_start: int
    hour_stop: int
    buses: list[NrelBus]
    lines: list[NrelLine]
    generators: list[NrelGen]
    bus_load_mw: dict[int, np.ndarray]  # bus uid → hourly MW
    gen_avail_mw: dict[str, np.ndarray]  # gen name → hourly available MW
    gen_capacity: dict[str, float]  # gen name → effective capacity [MW]
    vg_scale: float = 1.0  # wind/solar capacity multiplier applied
    notes: list[str] = field(default_factory=list)

    @property
    def n_hours(self) -> int:
        """Number of hours in the selected window."""
        return self.hour_stop - self.hour_start

    @property
    def total_load_mw(self) -> np.ndarray:
        """System hourly load [MW] (sum over buses)."""
        if not self.bus_load_mw:
            return np.zeros(self.n_hours)
        return np.sum(list(self.bus_load_mw.values()), axis=0)

    @property
    def bus_count(self) -> int:
        """Number of buses."""
        return len(self.buses)

    @property
    def gen_count(self) -> int:
        """Number of generators."""
        return len(self.generators)

    @property
    def line_count(self) -> int:
        """Number of lines."""
        return len(self.lines)


def collapse_piecewise_heat_rate(
    hr_base_mmbtu_per_hr: float,
    hr_inc_bands_btu_per_kwh: list[float],
    load_points_mw: list[float],
    pmin: float,
    pmax: float,
) -> float:
    """Reduce NREL-118 piecewise HR to a SCALAR heat_rate at pmax.

    Returns the pmax-weighted segment-average heat_rate in **GJ/MWh**.
    Renewable / hydro rows ship empty HR columns; callers must filter
    those out *before* invoking this function.

    The bands form a cumulative segment chain starting at ``pmin``:
    segment k covers ``[load_points[k-1], load_points[k]]`` with
    ``load_points[0] = pmin``.  Incremental band rates apply to the
    extra MW added in that segment relative to the previous breakpoint.
    """

    if pmax <= 0.0:
        return 0.0

    fuel_mmbtu_per_hr = hr_base_mmbtu_per_hr
    prev_breakpoint = pmin if pmin > 0.0 else 0.0
    for band_idx, breakpoint_mw in enumerate(load_points_mw):
        if breakpoint_mw <= prev_breakpoint:
            continue  # empty / missing band
        band_width = breakpoint_mw - prev_breakpoint
        if band_idx >= len(hr_inc_bands_btu_per_kwh):
            break
        inc_btu_per_kwh = hr_inc_bands_btu_per_kwh[band_idx]
        # BTU/kWh × MW = 1000 BTU/h → ÷ 1e6 for MMBTU/hr: × 1e-3.
        fuel_mmbtu_per_hr += inc_btu_per_kwh * band_width * 1.0e-3
        prev_breakpoint = breakpoint_mw

    heat_rate_mmbtu_per_mwh = fuel_mmbtu_per_hr / pmax
    return heat_rate_mmbtu_per_mwh * MMBTU_PER_GJ


def _float_or(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _cell(header: dict[str, int], row: list[str], col: str) -> str:
    idx = header.get(col)
    if idx is None or idx >= len(row):
        return ""
    return row[idx].strip()


def _parse_generator_row(header: dict[str, int], row: list[str]) -> NrelGen | None:
    """Parse one NREL-118 ``Generators.csv`` row to a ``NrelGen`` instance."""

    name = _cell(header, row, "Generator Name")
    if not name:
        return None
    pmax = _float_or(_cell(header, row, "Max Capacity (MW)"))
    if pmax <= 0.0:
        return None  # offline / placeholder rows
    pmin = _float_or(_cell(header, row, "Min Stable Level (MW)"))
    vom = _float_or(_cell(header, row, "VO&M Charge ($/MWh)"))

    fuel_kind = unit_type_to_fuel(name)
    is_renewable = fuel_kind in RENEWABLE_FUELS

    heat_rate_gj_per_mwh = 0.0
    if not is_renewable and fuel_kind != "nuclear":
        hr_base = _float_or(_cell(header, row, "Heat Rate Base (MMBTU/hr)"))
        inc_bands = [
            _float_or(_cell(header, row, f"Heat Rate Inc Band {k} (BTU/kWh)"))
            for k in range(1, 6)
        ]
        load_points = [
            _float_or(_cell(header, row, f"Load Point Band {k} (MW)"))
            for k in range(1, 6)
        ]
        heat_rate_gj_per_mwh = collapse_piecewise_heat_rate(
            hr_base, inc_bands, load_points, pmin, pmax
        )

    return NrelGen(
        name=name,
        bus=_cell(header, row, "bus of connection"),
        pmax=pmax,
        pmin=pmin,
        vom=vom,
        fuel=fuel_kind,
        heat_rate_gj_per_mwh=heat_rate_gj_per_mwh,
        is_renewable=is_renewable,
        ramp_up_mw_per_min=_float_or(_cell(header, row, "Max Ramp Up (MW/min)")),
        ramp_down_mw_per_min=_float_or(_cell(header, row, "Max Ramp Down (MW/min)")),
        min_up_h=_float_or(_cell(header, row, "Min Up Time (h)")),
        min_down_h=_float_or(_cell(header, row, "Min Down Time (h)")),
        start_cost=_float_or(_cell(header, row, "Start Cost ($)")),
        has_profile_file=(
            _cell(header, row, "Rating") == name
            and profile_remote_path(name) is not None
        ),
    )


def parse_generators(generators_csv: Path) -> list[NrelGen]:
    """Read all rows of NREL-118 ``Generators.csv`` and return parsed gens."""

    header, rows = read_table(generators_csv)
    gens: list[NrelGen] = []
    for row in rows:
        parsed = _parse_generator_row(header, row)
        if parsed is not None:
            gens.append(parsed)
    return gens


def parse_buses(buses_csv: Path, partfact_csv: Path | None = None) -> list[NrelBus]:
    """Read ``Buses.csv`` (number + ``Area`` region) and the load factors.

    ``Buses.csv`` carries no load data; the per-bus regional load
    participation factors live in ``Load/partfact.csv`` (``Bus Name,
    Region, Load Participation Factor`` — they sum to 1 within each
    region).  A factor's region overrides the bus ``Area``.
    """
    header, rows = read_table(buses_csv)
    buses: dict[int, NrelBus] = {}
    for row in rows:
        num_txt = _cell(header, row, "Number")
        if not num_txt.isdigit():
            continue
        num = int(num_txt)
        buses[num] = NrelBus(number=num, region=_cell(header, row, "Area"))
    if partfact_csv is not None and partfact_csv.is_file():
        pf_header, pf_rows = read_table(partfact_csv)
        for row in pf_rows:
            name = _cell(pf_header, row, "Bus Name")
            if not name:
                continue
            num = bus_number(name)
            bus = buses.setdefault(num, NrelBus(number=num, region=""))
            region = _cell(pf_header, row, "Region")
            if region:
                bus.region = region
            bus.load_factor = _float_or(
                _cell(pf_header, row, "Load Participation Factor")
            )
    return [buses[k] for k in sorted(buses)]


def parse_lines(lines_csv: Path) -> list[NrelLine]:
    """Read ``Lines.csv`` (``Max Flow`` / ``Min Flow`` [MW], x / r [p.u.])."""
    if not lines_csv.is_file():
        return []
    header, rows = read_table(lines_csv)
    lines: list[NrelLine] = []
    for row in rows:
        name = _cell(header, row, "Line Name")
        if not name:
            continue
        fmax = _float_or(_cell(header, row, "Max Flow (MW)"))
        fmin = _float_or(_cell(header, row, "Min Flow (MW)"), -fmax)
        lines.append(
            NrelLine(
                name=name,
                bus_a=bus_number(_cell(header, row, "Bus from")),
                bus_b=bus_number(_cell(header, row, "Bus to")),
                tmax_ab=fmax,
                tmax_ba=abs(fmin),
                reactance_pu=_float_or(_cell(header, row, "Reactance (p.u.)")),
                resistance_pu=_float_or(_cell(header, row, "Resistance (p.u.)")),
            )
        )
    return lines


def _month_of_slice(text: str) -> int | None:
    slice_txt = text.strip().upper()
    if slice_txt.startswith("M") and slice_txt[1:].isdigit():
        month = int(slice_txt[1:])
        return month if 1 <= month <= 12 else None
    return None


def parse_hydro_budgets(hydro_csv: Path) -> dict[str, HydroBudget]:
    """Read ``118-hydro.csv`` monthly energy / capacity of energy-limited hydro.

    The file is a headerless PLEXOS property dump
    (``Generators,System,<name>,<property>,<value>,…,<timeslice>,…``);
    ``Max Energy Month`` [GWh] and ``Max Capacity`` [MW] carry a month
    timeslice ``M1`` .. ``M12`` (a blank timeslice = the base value).
    """
    budgets: dict[str, HydroBudget] = {}
    if not hydro_csv.is_file():
        return budgets
    for row in read_rows(hydro_csv):
        if len(row) < 11:
            continue
        name, prop = row[2].strip(), row[3].strip()
        if prop not in ("Max Energy Month", "Max Capacity"):
            continue
        value = _float_or(row[4], float("nan"))
        if not np.isfinite(value):
            continue
        month = _month_of_slice(row[10])
        budget = budgets.setdefault(name, HydroBudget())
        if prop == "Max Energy Month" and month is not None:
            budget.month_energy_gwh[month] = value
        elif prop == "Max Capacity":
            if month is None:
                budget.capacity = value
            else:
                budget.month_capacity[month] = value
    return {k: v for k, v in budgets.items() if v.month_energy_gwh}


def parse_monthly_fixed_mw(path: Path) -> dict[str, dict[int, float]]:
    """Read ``Hydro_nondispatchable.csv`` ``Fixed Load`` monthly MW per unit.

    Units that only carry a non-month (blank timeslice) row are skipped —
    in the upstream file those are the hourly-profile units (Fixed Load 0).
    A month absent for a listed unit is 0 MW.
    """
    out: dict[str, dict[int, float]] = {}
    if not path.is_file():
        return out
    header, rows = read_table(path)
    for row in rows:
        if _cell(header, row, "Property") != "Fixed Load":
            continue
        month = _month_of_slice(_cell(header, row, "Timeslice"))
        if month is None:
            continue
        name = _cell(header, row, "Generator")
        out.setdefault(name, {})[month] = _float_or(_cell(header, row, "Value"))
    return out


def hour_months(hour_start: int, hour_stop: int) -> np.ndarray:
    """Calendar month (1..12) of every hour of the window (2024 year)."""
    return np.array(
        [
            (YEAR_START + datetime.timedelta(hours=h)).month
            for h in range(hour_start, hour_stop)
        ],
        dtype=np.int32,
    )


def _shape_energy(energy: float, cap: float, weight: np.ndarray) -> np.ndarray:
    """Hourly MW with ``sum = energy``, ``≤ cap``, ∝ *weight* below the cap.

    Water-filling by bisection on the scale ``λ``: ``min(cap, λ·w)``.  If
    the budget exceeds ``cap × hours`` the result is ``cap`` everywhere.
    """
    n = weight.size
    if n == 0 or energy <= 0.0 or cap <= 0.0:
        return np.zeros(n)
    if energy >= cap * n:
        return np.full(n, cap)
    w = np.maximum(weight, 1e-9)
    lo, hi = 0.0, cap / float(w.min())
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if np.minimum(cap, mid * w).sum() < energy:
            lo = mid
        else:
            hi = mid
    return np.minimum(cap, hi * w)


def hydro_budget_availability(
    budget: HydroBudget,
    capacity: float,
    system_load_year: np.ndarray,
) -> np.ndarray:
    """Hourly available MW (whole year) of an energy-limited hydro unit.

    Each month's ``Max Energy Month`` budget is spread over the month's
    hours in proportion to the system load (peak-shaving shape), capped
    at the month's ``Max Capacity`` (or the base capacity).  The monthly
    energy is exact while staying a pure per-block bound — gtopt has no
    native multi-stage generator energy budget, so the LP cannot move
    hydro energy between hours beyond this pre-shaped cap.
    """
    months = hour_months(0, system_load_year.size)
    avail = np.zeros(system_load_year.size)
    for month in range(1, 13):
        mask = months == month
        if not mask.any():
            continue
        cap = min(budget.month_capacity.get(month, capacity), capacity)
        energy_mwh = budget.month_energy_gwh.get(month, 0.0) * 1000.0
        avail[mask] = _shape_energy(energy_mwh, cap, system_load_year[mask])
    return avail


def monthly_mw_availability(per_month: dict[int, float], n_hours: int) -> np.ndarray:
    """Expand a ``{month: MW}`` table to an hourly series (whole year)."""
    months = hour_months(0, n_hours)
    table = np.array([per_month.get(m, 0.0) for m in range(13)])
    return table[months]


def _regional_loads(cache_dir: Path) -> dict[str, np.ndarray]:
    loads: dict[str, np.ndarray] = {}
    for region in REGIONS:
        path = resolve(cache_dir, f"Load/DA/Load{region}DA.csv")
        if path.is_file():
            loads[region] = read_hourly(path, YEAR_HOURS)
    if not loads:
        raise FileNotFoundError(f"no LoadR*DA.csv regional load files in {cache_dir}")
    return loads


def _distribute_load(
    buses: list[NrelBus], loads: dict[str, np.ndarray]
) -> dict[int, np.ndarray]:
    """Per-bus hourly load = participation factor × regional load."""
    by_bus: dict[int, np.ndarray] = {}
    for region, series in loads.items():
        members = [b for b in buses if b.region == region and b.load_factor > 0.0]
        if members:
            weights = np.array([b.load_factor for b in members])
            weights = weights / weights.sum()
        else:
            # No factors for this region: spread it evenly over its buses.
            members = [b for b in buses if b.region == region]
            if not members:
                raise ValueError(f"region {region} has no buses")
            weights = np.full(len(members), 1.0 / len(members))
        for bus, wgt in zip(members, weights):
            prev = by_bus.get(bus.number)
            add = series * wgt
            by_bus[bus.number] = add if prev is None else prev + add
    return dict(sorted(by_bus.items()))


def _availability(
    gen: NrelGen,
    cache_dir: Path,
    n_year: int,
    tables: tuple[dict[str, HydroBudget], dict[str, dict[int, float]]],
    system_load_year: np.ndarray,
    notes: list[str],
) -> tuple[float, np.ndarray | None]:
    """Effective capacity + hourly availability (whole year) of *gen*."""
    budgets, fixed_mw = tables
    cap = gen.pmax
    if gen.has_profile_file:
        path = resolve(cache_dir, profile_remote_path(gen.name) or "")
        if not path.is_file():
            notes.append(f"{gen.name}: profile file missing, full availability")
            return cap, None
        avail = np.clip(read_hourly(path, n_year), 0.0, None)
        if avail.size < n_year:
            avail = np.pad(avail, (0, n_year - avail.size))
        # Some DA series peak above ``Max Capacity`` (the PLEXOS
        # ``Rating`` file overrides it); keep the MW series exact.
        return max(cap, float(avail.max(initial=0.0))), avail
    if gen.name in budgets:
        budget = budgets[gen.name]
        if budget.capacity is not None and budget.capacity > 0.0:
            cap = budget.capacity
        return cap, hydro_budget_availability(budget, cap, system_load_year)
    if gen.name in fixed_mw:
        return cap, np.minimum(monthly_mw_availability(fixed_mw[gen.name], n_year), cap)
    return cap, None


def convert(
    cache_dir: Path,
    hours: tuple[int, int] | None = None,
    *,
    week: int | None = None,
    renewables_share: float | None = None,
) -> Conversion:
    """Read the cached CSVs and build the hourly conversion state.

    ``hours`` selects ``[start, stop)`` of the 8784-hour year (default:
    the whole year); the legacy ``week=N`` selects hours
    ``(N-1)·168 .. N·168``.  ``renewables_share`` (if given) rescales
    every wind + solar unit so their available energy over the window
    equals ``share × load energy`` (``0`` removes them) — the
    penetration knob of Peña et al. 2018.
    """
    if week is not None:
        if week < 1:
            raise ValueError("week must be >= 1")
        hours = ((week - 1) * 168, week * 168)
    loads_year = _regional_loads(cache_dir)
    n_year = min(len(v) for v in loads_year.values())
    start, stop = hours if hours is not None else (0, n_year)
    if not 0 <= start < stop <= n_year:
        raise ValueError(f"hour window [{start}, {stop}) outside [0, {n_year})")

    notes: list[str] = []
    buses = parse_buses(
        resolve(cache_dir, "Buses.csv"), resolve(cache_dir, "Load/partfact.csv")
    )
    if not any(b.load_factor > 0.0 for b in buses):
        notes.append("partfact.csv missing: regional load spread evenly per region")
    lines = parse_lines(resolve(cache_dir, "Lines.csv"))
    generators = parse_generators(resolve(cache_dir, "Generators.csv"))
    tables = (
        parse_hydro_budgets(resolve(cache_dir, "Hydro/118-hydro.csv")),
        parse_monthly_fixed_mw(resolve(cache_dir, "Hydro/Hydro_nondispatchable.csv")),
    )

    bus_load_year = _distribute_load(buses, loads_year)
    system_load_year = np.sum(list(bus_load_year.values()), axis=0)[:n_year]

    gen_avail: dict[str, np.ndarray] = {}
    gen_capacity: dict[str, float] = {}
    for gen in generators:
        cap, avail_year = _availability(
            gen, cache_dir, n_year, tables, system_load_year, notes
        )
        gen_capacity[gen.name] = cap
        if avail_year is not None:
            gen_avail[gen.name] = avail_year[start:stop]

    bus_load = {uid: s[start:stop] for uid, s in bus_load_year.items()}
    vg_scale = 1.0
    if renewables_share is not None:
        vg_names = [g.name for g in generators if g.fuel in VARIABLE_FUELS]
        vg_energy = sum(float(gen_avail[n].sum()) for n in vg_names if n in gen_avail)
        load_energy = float(np.sum(list(bus_load.values())))
        vg_scale = (
            renewables_share * load_energy / vg_energy if vg_energy > 0.0 else 0.0
        )
        for name in vg_names:
            gen_capacity[name] *= vg_scale
            if name in gen_avail:
                gen_avail[name] = gen_avail[name] * vg_scale

    return Conversion(
        hour_start=start,
        hour_stop=stop,
        buses=buses,
        lines=lines,
        generators=generators,
        bus_load_mw=bus_load,
        gen_avail_mw=gen_avail,
        gen_capacity=gen_capacity,
        vg_scale=vg_scale,
        notes=notes,
    )


def fuel_srmc(gen: NrelGen, prices: dict[str, float] | None = None) -> float:
    """Fuel part of the SRMC [$/MWh] = heat_rate × fuel price."""
    if gen.heat_rate_gj_per_mwh <= 0.0:
        return 0.0
    table = prices if prices is not None else SIENNA_FUEL_PRICE_USD_PER_MMBTU
    return gen.heat_rate_gj_per_mwh * table.get(gen.fuel, 0.0) / MMBTU_PER_GJ


def _generator_type(gen: NrelGen) -> str:
    if gen.fuel in RENEWABLE_FUELS:
        return gen.fuel
    return "thermal"


@dataclass
class CaseOptions:  # pylint: disable=too-many-instance-attributes
    """Knobs of :func:`build_case` (mirrors the CLI flags)."""

    block_hours: int = 1
    stage_hours: int | None = 24
    single_bus: bool = False
    single_phase: bool = False
    commitment: str = "none"
    demand_fail_cost: float = 1000.0
    line_losses_mode: str = "none"
    method: str = "monolithic"
    fuel_prices: str = "sienna"
    output_format: str | None = None


def _commitment_entry(
    gen: NrelGen, gen_uid: int, cap: float, opts: CaseOptions, uid: int
) -> dict[str, Any]:
    uc: dict[str, Any] = {
        "uid": uid,
        "name": f"{gen.name}_uc",
        "generator": gen_uid,
        "pmin": round(min(gen.pmin, cap), 6),
        "startup_cost": gen.start_cost,
        "min_up_time": gen.min_up_h,
        "min_down_time": gen.min_down_h,
        "initial_status": 1,
    }
    if gen.ramp_up_mw_per_min > 0.0:
        uc["ramp_up"] = round(gen.ramp_up_mw_per_min * 60.0, 6)
    if gen.ramp_down_mw_per_min > 0.0:
        uc["ramp_down"] = round(gen.ramp_down_mw_per_min * 60.0, 6)
    if opts.commitment == "relaxed":
        uc["relax"] = True
    return uc


def _network_arrays(
    conversion: Conversion,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    numbers = [b.number for b in conversion.buses]
    ref_bus = REFERENCE_BUS if REFERENCE_BUS in numbers else min(numbers)
    bus_array: list[dict[str, Any]] = []
    for bus in conversion.buses:
        entry: dict[str, Any] = {"uid": bus.number, "name": bus.name}
        if bus.number == ref_bus:
            entry["reference_theta"] = 0
        bus_array.append(entry)
    line_array = [
        {
            "uid": uid,
            "name": line.name,
            "bus_a": line.bus_a,
            "bus_b": line.bus_b,
            "voltage": LINE_VOLTAGE_PU_100MVA,
            "reactance": line.reactance_pu,
            "resistance": line.resistance_pu,
            "tmax_ab": line.tmax_ab,
            "tmax_ba": line.tmax_ba,
        }
        for uid, line in enumerate(conversion.lines, start=1)
    ]
    return bus_array, line_array


def _fuel_array(conversion: Conversion) -> tuple[list[dict[str, Any]], dict[str, int]]:
    used = sorted({g.fuel for g in conversion.generators if g.heat_rate_gj_per_mwh > 0})
    fuel_uid = {name: idx for idx, name in enumerate(used, start=1)}
    fuels = [
        {
            "uid": fuel_uid[name],
            "name": name,
            "price": 0.0,  # fuel SRMC already folded into Generator.gcost
            "emission_factors": [
                {"emission": 1, "combustion": IPCC_AR6_TCO2_PER_GJ[name]}
            ],
        }
        for name in used
    ]
    return fuels, fuel_uid


def build_case(  # pylint: disable=too-many-locals
    conversion: Conversion,
    output_json: Path,
    opts: CaseOptions | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialise the gtopt planning dict and write its Parquet schedules.

    Schedules go to ``<output_json parent>/<output_json stem>/``
    (``Demand/lmax.parquet``, ``GeneratorProfile/profile.parquet``),
    referenced through a *relative* ``options.input_directory`` so the
    case directory can be moved.  Returns ``(planning, summary)``; the
    JSON itself is written by :func:`write_json`.
    """
    opts = opts or CaseOptions()
    if opts.commitment not in COMMITMENT_MODES:
        raise ValueError(f"commitment must be one of {COMMITMENT_MODES}")
    if opts.fuel_prices not in FUEL_PRICE_TABLES:
        raise ValueError(f"fuel_prices must be one of {sorted(FUEL_PRICE_TABLES)}")
    prices = FUEL_PRICE_TABLES[opts.fuel_prices]
    grid = TimeGrid(
        n_hours=conversion.n_hours,
        block_hours=opts.block_hours,
        stage_hours=opts.stage_hours,
    )
    input_dir = output_json.parent / output_json.stem
    durations = np.full(grid.n_blocks, float(grid.block_hours))
    bus_array, line_array = _network_arrays(conversion)

    # -- demands: one per bus with a participation factor ----------------
    demand_array = [
        {
            "uid": bus_uid,
            "name": f"load_{bus_name(bus_uid)}",
            "bus": bus_uid,
            "lmax": "lmax",
        }
        for bus_uid in conversion.bus_load_mw
    ]
    write_block_schedule(
        input_dir / "Demand",
        "lmax",
        {uid: grid.aggregate(s) for uid, s in conversion.bus_load_mw.items()},
        grid,
    )

    fuel_array, fuel_uid = _fuel_array(conversion)

    # -- generators + availability profiles -------------------------------
    gen_array: list[dict[str, Any]] = []
    commitment_array: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    factor_by_uid: dict[int, np.ndarray] = {}
    capacity_by_tech: dict[str, float] = {}
    avail_mwh_by_tech: dict[str, float] = {}
    for gen in conversion.generators:
        cap = conversion.gen_capacity.get(gen.name, gen.pmax)
        if cap <= 0.0:
            continue  # renewables_share = 0 removes wind / solar
        gen_uid = len(gen_array) + 1
        tech = _generator_type(gen)
        entry: dict[str, Any] = {
            "uid": gen_uid,
            "name": gen.name,
            "type": tech,
            "bus": gen.bus_uid,
            "gcost": round(gen.vom + fuel_srmc(gen, prices), 6),
            "capacity": round(cap, 6),
            "pmax": round(cap, 6),
        }
        if gen.heat_rate_gj_per_mwh > 0 and gen.fuel in fuel_uid:
            entry["fuel"] = fuel_uid[gen.fuel]
            entry["heat_rate"] = gen.heat_rate_gj_per_mwh
        gen_array.append(entry)
        capacity_by_tech[tech] = capacity_by_tech.get(tech, 0.0) + cap

        hourly = conversion.gen_avail_mw.get(gen.name)
        if hourly is not None:
            factor = np.clip(grid.aggregate(hourly) / cap, 0.0, 1.0)
            factor_by_uid[len(profiles) + 1] = factor
            profiles.append(
                {
                    "uid": len(profiles) + 1,
                    "name": f"{gen.name}_profile",
                    "owner_kind": "generator",
                    "owner": gen_uid,
                    "profile": "profile",
                }
            )
            avail = float(np.sum(factor * durations)) * cap
        else:
            avail = cap * float(durations.sum())
        avail_mwh_by_tech[tech] = avail_mwh_by_tech.get(tech, 0.0) + avail

        if opts.commitment != "none" and tech == "thermal" and gen.pmin > 0.0:
            commitment_array.append(
                _commitment_entry(gen, gen_uid, cap, opts, len(commitment_array) + 1)
            )

    if profiles:
        write_block_schedule(
            input_dir / "GeneratorProfile",
            "profile",
            factor_by_uid,
            grid,
            with_scenario=True,
        )

    simulation = grid.simulation(one_phase_per_stage=not opts.single_phase)
    simulation["annual_discount_rate"] = 0.0
    if commitment_array:
        for stage in simulation["stage_array"]:
            stage["chronological"] = True

    system: dict[str, Any] = {
        "name": f"nrel118_h{conversion.hour_start}_{conversion.hour_stop}",
        "bus_array": bus_array,
        "line_array": line_array,
        "demand_array": demand_array,
        "generator_array": gen_array,
        "emission_array": [{"uid": 1, "name": "co2"}],
        "emission_zone_array": [
            {
                "uid": 1,
                "name": "global_co2",
                "emissions": [{"emission": 1, "weight": 1.0}],
            }
        ],
        "fuel_array": fuel_array,
    }
    if profiles:
        system["capacity_profile_array"] = profiles
    if commitment_array:
        system["commitment_array"] = commitment_array

    # ``demand_fail_cost`` MUST be finite and above the most expensive
    # thermal SRMC (Oil CT ~ $250/MWh at Sienna prices) or the LP
    # curtails load for free.
    options: dict[str, Any] = {
        "method": opts.method,
        "input_directory": output_json.stem,
        "input_format": "parquet",
        "model_options": {
            "use_single_bus": opts.single_bus,
            "use_kirchhoff": not opts.single_bus,
            "line_losses_mode": opts.line_losses_mode,
            "demand_fail_cost": opts.demand_fail_cost,
        },
    }
    if opts.output_format:
        options["output_format"] = opts.output_format

    load_blocks = grid.aggregate(conversion.total_load_mw)
    summary: dict[str, Any] = {
        "hour_window": [conversion.hour_start, conversion.hour_stop],
        "hours": grid.n_hours,
        "blocks": grid.n_blocks,
        "stages": grid.n_stages,
        "block_hours": grid.block_hours,
        "buses": len(bus_array),
        "lines": len(line_array),
        "demands": len(demand_array),
        "generators": len(gen_array),
        "profiled_generators": len(profiles),
        "commitments": len(commitment_array),
        "load_energy_mwh": float(np.sum(load_blocks * durations)),
        "peak_load_mw": float(load_blocks.max(initial=0.0)),
        "min_load_mw": float(load_blocks.min()) if load_blocks.size else 0.0,
        "capacity_mw_by_type": {k: round(v, 3) for k, v in capacity_by_tech.items()},
        "available_mwh_by_type": {k: round(v, 3) for k, v in avail_mwh_by_tech.items()},
        "vg_scale": conversion.vg_scale,
        "notes": list(conversion.notes),
    }
    return {"options": options, "simulation": simulation, "system": system}, summary


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write the planning dict as indented JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)
