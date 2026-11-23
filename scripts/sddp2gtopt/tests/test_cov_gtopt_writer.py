"""Branch coverage for :mod:`sddp2gtopt.gtopt_writer` helpers."""

from __future__ import annotations

import pytest

from sddp2gtopt.entities import (
    BusSpec,
    CircuitSpec,
    DemandSpec,
    HydroSpec,
    StudySpec,
    ThermalSpec,
)
from sddp2gtopt.gtopt_writer import (
    _inflow_sched,
    _pmax_cap,
    _resolve_bus,
    build_demands,
    build_hydro_generators,
    build_planning,
    build_thermal_generators,
)


def test_resolve_bus_unknown_ref_falls_back() -> None:
    assert _resolve_bus(None, {1: "b1"}, "fb") == "fb"
    assert _resolve_bus(9, {1: "b1"}, "fb") == "fb"
    assert _resolve_bus(1, {1: "b1"}, "fb") == "b1"


def test_builders_require_a_bus() -> None:
    study = StudySpec()
    calls = [
        lambda **kw: build_thermal_generators([], **kw),
        lambda **kw: build_hydro_generators([], start_uid=1, **kw),
        lambda **kw: build_demands([], study, **kw),
    ]
    for call in calls:
        with pytest.raises(ValueError, match="pass bus_name or bus_by_ref"):
            call()
        with pytest.raises(ValueError, match="needs fallback_bus"):
            call(bus_by_ref={1: "b1"})


def test_pmax_cap_shapes() -> None:
    study = StudySpec(num_stages=2, num_blocks=2)
    assert _pmax_cap([], study) is None
    assert _pmax_cap([5.0, 5.0], study) == 5.0
    assert _pmax_cap([1.0, 2.0, 3.0, 4.0], study) == [[1.0, 2.0], [3.0, 4.0]]
    assert _pmax_cap([1.0, 3.0, 5.0], study) == pytest.approx(3.0)
    one = StudySpec(num_stages=1, num_blocks=2)
    assert _pmax_cap([1.0, 2.0], one) == [[1.0, 2.0]]


def test_inflow_sched_shapes() -> None:
    assert _inflow_sched([], StudySpec()) is None
    staged = StudySpec(num_stages=3, num_blocks=2)
    assert _inflow_sched([1.0, 2.0], staged) == [[[1.0, 1.0], [2.0, 2.0], [2.0, 2.0]]]
    hourly = StudySpec(num_stages=1, num_blocks=48)
    sched = _inflow_sched([1.0, 2.0], hourly)
    assert sched[0][0][:24] == [1.0] * 24 and sched[0][0][24:] == [2.0] * 24
    assert _inflow_sched([3.0, 3.0], StudySpec(num_stages=1, num_blocks=4)) == 3.0
    # Non-daily block count: spread days evenly over the blocks.
    assert _inflow_sched([1.0, 2.0], StudySpec(num_stages=1, num_blocks=4)) == [
        [[1.0, 1.0, 2.0, 2.0]]
    ]


def test_multibus_skips_unrouted_elements() -> None:
    study = StudySpec(num_stages=1, num_blocks=1, block_hours=1.0)
    buses = [BusSpec(number=1, name="B1"), BusSpec(number=2, name="B2")]
    circuits = [
        CircuitSpec(from_bus=1, to_bus=2, name="L12", reactance_pu=0.0, rating=0.0),
        CircuitSpec(from_bus=1, to_bus=9, name="L19", reactance_pu=-1e-9),
    ]
    thermals = [
        ThermalSpec(code=1, name="T1", reference_id=1, pmax=10.0, bus_number=1),
        ThermalSpec(code=2, name="T9", reference_id=2, pmax=10.0, bus_number=9),
    ]
    hydros = [
        HydroSpec(code=3, name="H1", reference_id=3, p_inst=5.0, bus_number=1),
        HydroSpec(code=4, name="H0", reference_id=4, p_inst=0.0, bus_number=1),
        HydroSpec(code=5, name="HX", reference_id=5, p_inst=5.0),
    ]
    demands = [
        DemandSpec(code=1, name="d2", reference_id=1, bus_number=2, block_values=[5.0]),
        DemandSpec(code=2, name="dx", reference_id=2, block_values=[5.0]),
    ]
    planning = build_planning(
        study=study,
        systems=[],
        thermals=thermals,
        hydros=hydros,
        demands=demands,
        name="mb",
        buses=buses,
        circuits=circuits,
    )
    system = planning["system"]
    assert [ln["name"] for ln in system["line_array"]] == ["L12_1"]
    assert system["line_array"][0]["reactance"] > 0.0
    gens = {g["name"] for g in system["generator_array"]}
    assert "T1" in gens and "H1" in gens
    assert not {"T9", "H0", "HX"} & gens
    assert len(system["demand_array"]) == 1
