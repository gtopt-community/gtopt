"""nrel118_to_gtopt — converter for the NREL-118 test system.

Reads the public NREL-118 (Pena, Martinez-Anido, Hodge 2018) test-system
CSVs published by NREL-Sienna's `PowerSystemsTestData` repo (fetched on
demand into a cache, never vendored) and emits a full-network,
multi-block, multi-stage gtopt case: 118 buses / 186 lines, every
generator at its bus, per-bus hourly load (``Demand/lmax.parquet``),
hourly renewable / hydro availability
(``GeneratorProfile/profile.parquet``) and IPCC AR6 CO2 factors for the
``--emissions`` accounting pipeline.

The piecewise heat-rate (Base MMBTU/hr + 5 incremental BTU/kWh bands) is
collapsed to a single scalar heat_rate (GJ/MWh at pmax); see
:func:`collapse_piecewise_heat_rate`.

Use as a module::

    python -m nrel118_to_gtopt --hours 0+168 -o week1/nrel118.json
"""

from nrel118_to_gtopt._converter import (
    BTU_PER_KWH_TO_GJ_PER_MWH,
    IPCC_AR6_TCO2_PER_GJ,
    CaseOptions,
    build_case,
    collapse_piecewise_heat_rate,
    convert,
    unit_type_to_fuel,
)

__all__ = [
    "BTU_PER_KWH_TO_GJ_PER_MWH",
    "IPCC_AR6_TCO2_PER_GJ",
    "CaseOptions",
    "build_case",
    "collapse_piecewise_heat_rate",
    "convert",
    "unit_type_to_fuel",
]
