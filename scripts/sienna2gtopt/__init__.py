"""sienna2gtopt — port NREL-Sienna 5-bus PowerSimulations.jl cases to gtopt.

This converter reads the NREL-Sienna 5-bus test data -- fetched on
demand from the pinned upstream ``PowerSystemsTestData`` commit and
cached as ``~/.cache/gtopt/sienna/sienna_5bus.tar.zst`` (see
:mod:`sienna2gtopt._bundle`; the data is not redistributed with
gtopt) -- and emits a gtopt planning JSON capturing the matching
topology + dispatch fixture.

The three variants intentionally exercise **NON-EMISSION** features
that don't have peer-reviewed integration-test coverage today:

* ``cascading_hydro`` — Junction + Waterway + Turbine + Reservoir
  cascade chain (gtopt's unique hydro topology vs. PowerSystems.jl's
  flat ``HydroEnergyReservoir`` adjacency table).
* ``monitored_line``  — exactly one transmission line carries an
  enforced thermal limit; the rest are unconstrained
  (``Line.enforce_level = 0``).  Mirrors PowerSimulations.jl
  ``MonitoredLine``.
* ``hvdc``            — one branch is converted to an HVDC link by
  dropping its ``reactance``; the LP omits the Kirchhoff KVL row
  for the DC line.
* ``reserves``        — PowerSimulations reserve benchmarks
  (``c_sys5_uc[_re]`` with ``add_reserves``): unit commitment plus
  RangeReserve up / down zones and the ORDC as stacked priced zones,
  built from the ``system_data`` block of a
  ``tools/sienna_solve_golden.jl`` golden (no CSV bundle).

Use as a module:

    python -m sienna2gtopt cascading_hydro -o cascading.json
    python -m sienna2gtopt monitored_line  -o mline.json
    python -m sienna2gtopt hvdc            -o hvdc.json
    python -m sienna2gtopt reserves --golden tools/sienna_goldens/reserves_uc.json
"""

from typing import Any, Callable

from sienna2gtopt._battery_degeneracy import build_battery_degeneracy
from sienna2gtopt._bundle import (
    MANIFEST,
    BundleUnavailableError,
    available_bundles,
    extract_bundle,
    fetch_bundle,
)
from sienna2gtopt._cascading_hydro import build_cascading_hydro
from sienna2gtopt._fuel_cost_ts import build_fuel_cost_ts
from sienna2gtopt._hvdc import build_hvdc
from sienna2gtopt._interruptible_load import build_interruptible_load
from sienna2gtopt._monitored_line import build_monitored_line
from sienna2gtopt._pumped_storage import build_pumped_storage
from sienna2gtopt._reserves import build_reserves
from sienna2gtopt._reader import (
    SiennaBranch,
    SiennaBus,
    SiennaCase,
    SiennaGen,
    parse_branches,
    parse_buses,
    parse_generators,
    parse_hydro_upstream,
    parse_user_descriptors,
)
from sienna2gtopt._wecc_240 import build_wecc_240

VariantBuilder = Callable[..., dict[str, Any]]
VARIANTS: dict[str, VariantBuilder] = {
    "cascading_hydro": build_cascading_hydro,
    "monitored_line": build_monitored_line,
    "hvdc": build_hvdc,
    "pumped_storage": build_pumped_storage,
    "battery_degeneracy": build_battery_degeneracy,
    "interruptible_load": build_interruptible_load,
    "fuel_cost_ts": build_fuel_cost_ts,
    "wecc_240": build_wecc_240,
    # Takes a sienna_solve_golden.jl reserve golden, not the CSV bundle.
    "reserves": build_reserves,
}

__all__ = [
    "BundleUnavailableError",
    "MANIFEST",
    "SiennaBranch",
    "SiennaBus",
    "SiennaCase",
    "SiennaGen",
    "VARIANTS",
    "VariantBuilder",
    "available_bundles",
    "build_battery_degeneracy",
    "build_cascading_hydro",
    "build_fuel_cost_ts",
    "build_hvdc",
    "build_interruptible_load",
    "build_monitored_line",
    "build_pumped_storage",
    "build_reserves",
    "build_wecc_240",
    "extract_bundle",
    "fetch_bundle",
    "parse_branches",
    "parse_buses",
    "parse_generators",
    "parse_hydro_upstream",
    "parse_user_descriptors",
]
