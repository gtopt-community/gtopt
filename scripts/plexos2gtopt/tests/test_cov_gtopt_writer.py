"""Branch-coverage tests for :mod:`plexos2gtopt.gtopt_writer`.

Each test pins one behaviour of a per-class array builder or a
system-level post-processing pass on a tiny synthetic input.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from plexos2gtopt import gtopt_writer as gw
from plexos2gtopt.entities import (
    BatterySpec,
    BoundaryCutSpec,
    BundleSpec,
    CommitmentSpec,
    FlowRightSpec,
    FuelSpec,
    GeneratorSpec,
    LineSpec,
    PlexosCase,
    ProductionFactorSegmentSpec,
    ReserveProvisionSpec,
    ReserveSpec,
    ReservoirProductionFactorSpec,
    ReservoirSpec,
    TurbineSpec,
    UserConstraintSpec,
    WaterwaySpec,
)

LAYOUT2 = ((1, 2), (3, 4))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def test_field_peak_and_numeric_leaves() -> None:
    assert gw._field_peak(None, 7.0) == 7.0
    assert gw._field_peak(3) == 3.0
    assert gw._field_peak([[1.0, 5.0], [2.0, "x"]]) == 5.0
    assert gw._field_peak([], 4.0) == 4.0
    assert gw._field_peak("abc", 2.0) == 2.0
    assert list(gw._iter_numeric_leaves([1, [2.5, None]])) == [1.0, 2.5]


def test_fill_forward_defined_leading_and_trailing_zeros() -> None:
    assert gw._fill_forward_defined([0.0, 0.0, 3.0, 0.0, 4.0]) == [
        3.0,
        3.0,
        3.0,
        3.0,
        4.0,
    ]
    assert gw._fill_forward_defined([0.0, 0.0]) == [0.0, 0.0]


def test_per_block_field_value_collapses_after_aggregation() -> None:
    # Varies hourly but every block averages to the same value.
    assert gw._per_block_field_value(9.0, (1.0, 3.0, 3.0, 1.0), LAYOUT2) == 9.0
    assert gw._per_block_field_value(9.0, (1.0, 1.0, 3.0, 3.0), LAYOUT2) == [[1.0, 3.0]]


def test_aggregate_to_blocks_reducers() -> None:
    hourly = [1.0, 4.0, 2.0, 6.0]
    assert gw._aggregate_to_blocks(hourly, LAYOUT2, reducer="max") == [4.0, 6.0]
    assert gw._aggregate_to_blocks(hourly, LAYOUT2, reducer="sum") == [5.0, 8.0]
    assert gw._aggregate_to_blocks(hourly, LAYOUT2, reducer="min") == [1.0, 2.0]
    assert gw._aggregate_to_blocks(hourly, ((9,),)) == [0.0]


def test_commit_status_code_and_aggregation() -> None:
    assert gw._commit_status_code(1) == 1.0
    assert gw._commit_status_code(0) == 0.0
    assert gw._commit_status_code(-1) == -1.0
    assert gw._aggregate_commit_status((1, 0, -1), ()) == [1.0, 0.0, -1.0]
    layout = ((1, 2, 3), (4, 5), (9,))
    # block 1: modal 1; block 2: tie 0/1 -> free; block 3: no hours -> free
    assert gw._aggregate_commit_status((1, 1, 0, 0, 1), layout) == [
        1.0,
        -1.0,
        -1.0,
    ]


def test_scale_tmax_matrix() -> None:
    assert gw._scale_tmax(2.0, 3.0) == 6.0
    assert gw._scale_tmax([[1.0, 2.0]], 2.0) == [[2.0, 4.0]]


def test_int_loss_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GTOPT_NSEG_X", "abc")
    assert gw._int_loss_env("GTOPT_NSEG_X", 5) == 5
    monkeypatch.setenv("GTOPT_NSEG_X", "0")
    assert gw._int_loss_env("GTOPT_NSEG_X", 5) == 5
    monkeypatch.setenv("GTOPT_NSEG_X", "8")
    assert gw._int_loss_env("GTOPT_NSEG_X", 5) == 8


def test_resolve_loss_layout_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    line = LineSpec(object_id=1, name="L1", bus_from="a", bus_to="b")
    monkeypatch.setenv("GTOPT_LOSS_TANGENT_LINES", "L1, other")
    monkeypatch.setenv("GTOPT_NSEG_TANGENT", "4")
    assert gw._resolve_loss_layout(line) == ("tangent", 4)
    monkeypatch.delenv("GTOPT_LOSS_TANGENT_LINES")
    monkeypatch.setenv("GTOPT_LOSS_PWL_LAYOUT", "bogus")
    assert gw._resolve_loss_layout(line)[0] == "uniform"
    monkeypatch.setenv("GTOPT_LOSS_PWL_LAYOUT", "dynamic")
    assert gw._resolve_loss_layout(line)[0] == "uniform"


def test_soft_penalty_cost_paths() -> None:
    assert gw.soft_penalty_cost([5.0], [100.0], override=42.0) == 42.0
    assert gw.soft_penalty_cost([], []) == 1.0
    assert gw.soft_penalty_cost([[5.0, 80.0]], [50.0]) == 49.0


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def _gen(**kw: object) -> GeneratorSpec:
    base: dict[str, object] = {"object_id": 1, "name": "G", "bus_name": "b"}
    base.update(kw)
    return GeneratorSpec(**base)  # type: ignore[arg-type]


def test_generator_pmin_and_lossfactor() -> None:
    out = gw.build_generator_array((_gen(pmax=100.0, pmin=10.0, aux_use=0.05),))
    assert out[0]["pmin"] == 10.0
    assert out[0]["lossfactor"] == 0.05


def test_generator_fixed_load_with_profile_pmax() -> None:
    gen = _gen(
        pmax=100.0,
        pmax_profile=(100.0, 100.0, 50.0, 50.0),
        fixed_load_profile=(10.0, 10.0, 0.0, 0.0),
    )
    out = gw.build_generator_array((gen,), block_layout=LAYOUT2)
    entry = out[0]
    assert isinstance(entry["pmax"], list)
    assert entry["pmax"][0][1] == pytest.approx(50.0)


def test_generator_cogen_modes() -> None:
    gens = (
        _gen(name="ARAUCO_1", pmax=10.0),
        _gen(object_id=2, name="NOT_COGEN", pmax=10.0),
        _gen(object_id=3, name="CELCO_1", pmax=10.0),
    )
    out = gw.build_generator_array(
        gens, cogen_must_run=frozenset({"NOT_COGEN"}), cogen_must_run_all=True
    )
    assert [e.get("cogen_mode") for e in out] == ["must_run"] * 3
    out = gw.build_generator_array(gens)
    assert [e.get("cogen_mode") for e in out] == ["dispatched", None, "dispatched"]


def test_generator_inherits_emission_and_degenerate_pmax() -> None:
    gen = _gen(pmax=1e-9, inherits_emission_from="PARENT")
    out = gw.build_generator_array((gen,))
    assert "PARENT" in out[0]["description"]
    assert out[0]["pmax"] == 0.0


# ---------------------------------------------------------------------------
# EL=1 soft caps / lines
# ---------------------------------------------------------------------------


def test_augment_el1_piecewise_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        {
            "enforce_level": 1,
            "tmax_ab": 100.0,
            "tmax_ba": [[50.0]],
            "line_losses_mode": "piecewise",
        },
        {"enforce_level": 1, "tmax_ab": [[10.0]]},
    ]
    monkeypatch.setenv("GTOPT_LOSS_EXTEND_OVERLOAD", "yes")
    assert gw.augment_el1_with_soft_caps(lines, overload_penalty=7.0) == 2
    assert lines[0]["loss_envelope"] == 300.0
    assert lines[0]["tmax_ab"] == 300.0
    assert lines[0]["tmax_ba"] == [[50.0]]
    assert lines[1]["tmax_ab"] == [[10.0]]
    assert "tmax_ba" not in lines[1]

    monkeypatch.setenv("GTOPT_LOSS_EXTEND_OVERLOAD", "0")
    ln = [{"enforce_level": 1, "tmax_ab": 20.0, "line_losses_mode": "piecewise"}]
    gw.augment_el1_with_soft_caps(ln, overload_penalty=1.0)
    assert ln[0]["loss_envelope"] == 20.0


def test_line_profiles_in_service_and_soft_cap_matrix() -> None:
    line = LineSpec(
        object_id=1,
        name="L",
        bus_from="a",
        bus_to="b",
        tmax_ab=100.0,
        tmin_ab=-80.0,
        tmax_ab_profile=(100.0, 100.0, 120.0, 120.0),
        tmin_ab_profile=(-80.0, -80.0, -60.0, -60.0),
        in_service_profile=(1, 0, 1, 1),
        soft_cap=True,
    )
    out = gw.build_line_array((line,), block_layout=LAYOUT2)
    e = out[0]
    assert e["tmax_ba"] == [[gw._LINE_SOFT_HARD_FACTOR * v for v in (80.0, 60.0)]]
    assert e["in_service"] == [[0, 1]]
    assert e["loss_envelope"] == pytest.approx(120.0)

    # no block layout: raw profile, all-in-service profile omitted
    line2 = LineSpec(
        object_id=2,
        name="L2",
        bus_from="a",
        bus_to="b",
        tmax_ab=100.0,
        tmin_ab_profile=(-80.0, -60.0),
        in_service_profile=(1, 1),
    )
    e2 = gw.build_line_array((line2,))[0]
    assert e2["tmax_ba"] == [[80.0, 60.0]]
    assert "in_service" not in e2


def test_line_soft_cap_zero_ratings() -> None:
    line = LineSpec(
        object_id=1,
        name="L",
        bus_from="a",
        bus_to="b",
        tmax_ab=0.0,
        soft_cap=True,
    )
    e = gw.build_line_array((line,))[0]
    assert "tmax_ab" not in e


def test_line_sos2_secant_and_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GTOPT_NSEG_LOSSES", "7")
    line = LineSpec(
        object_id=1,
        name="L",
        bus_from="a",
        bus_to="b",
        tmax_ab=100.0,
        resistance=0.01,
        loss_use_sos2=True,
        loss_secant_segments=3,
    )
    e = gw.build_line_array((line,))[0]
    assert e["line_losses_mode"] == "tangent_signed_flow"
    assert e["loss_secant_segments"] == 3
    assert e["loss_use_sos2"] is True
    assert e["loss_segments"] == 7

    monkeypatch.setenv("GTOPT_LOSS_PWL_LAYOUT", "uniform")
    line2 = LineSpec(
        object_id=2,
        name="L2",
        bus_from="a",
        bus_to="b",
        tmax_ab=100.0,
        resistance=0.01,
    )
    e2 = gw.build_line_array((line2,), line_losses_mode="piecewise")[0]
    assert "loss_pwl_layout" not in e2
    assert e2["loss_envelope"] == 100.0


def test_line_peak_tmax_list_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise the nested ``_peak_tmax`` helper with a DLR matrix rating.
    line = LineSpec(
        object_id=1,
        name="L",
        bus_from="a",
        bus_to="b",
        tmax_ab=100.0,
        tmax_ab_profile=(100.0, 100.0, 140.0, 140.0),
        resistance=0.01,
    )
    e = gw.build_line_array((line,), block_layout=LAYOUT2)[0]
    assert e["loss_envelope"] == pytest.approx(140.0)


# ---------------------------------------------------------------------------
# Batteries / fuels
# ---------------------------------------------------------------------------


def test_battery_clamps_and_cycle_anchor(caplog: pytest.LogCaptureFixture) -> None:
    bats = (
        BatterySpec(object_id=1, name="B1", bus_name="b", emax=10.0, eini=20.0),
        BatterySpec(object_id=2, name="B2", bus_name="b", emin=5.0, eini=1.0),
        BatterySpec(
            object_id=3,
            name="B3",
            bus_name="b",
            emax=10.0,
            efin=5.0,
            max_cycles_day=1.0,
            reserve_sustain_hours=2.0,
        ),
    )
    with caplog.at_level(logging.WARNING):
        out = gw.build_battery_array(bats, LAYOUT2, apply_default_loss=True)
    assert out[0]["eini"] == 10.0
    assert out[1]["eini"] == 5.0
    assert "clamping" in caplog.text
    b3 = out[2]
    assert b3["efin_cost"] == gw._BATTERY_EFIN_COST
    assert b3["daily_cycle"] is False
    assert b3["use_state_variable"] is True
    assert b3["reserve_sustain_hours"] == 2.0
    assert b3["annual_standing_loss"] == 0.215


def test_fuel_subtype() -> None:
    out = gw.build_fuel_array((FuelSpec(object_id=1, name="gas", subtype="lng"),))
    assert out[0]["subtype"] == "lng"


# ---------------------------------------------------------------------------
# Hydro
# ---------------------------------------------------------------------------


def test_water_value_resolver_skips_unknown_and_nonbinding() -> None:
    res = (ReservoirSpec(object_id=1, name="R1", emax=100.0),)
    turbines = (
        TurbineSpec(generator_name="G0", reservoir_name="GHOST"),
        TurbineSpec(generator_name="G1", reservoir_name="R1", production_factor=2.0),
        TurbineSpec(generator_name="G2", reservoir_name="R1", production_factor=1.0),
    )
    cut = BoundaryCutSpec(fcf=1.0, slopes={"R1": -5.0, "NOPE": 3.0})
    resolver, numbers = gw.build_water_value_resolver(
        reservoirs=res,
        turbines=turbines,
        generator_entries=[],
        demand_entries=[],
        boundary_cut=cut,
    )
    assert numbers == {"R1": 1}
    assert resolver is not None


def test_reservoir_profiles_and_flags() -> None:
    reservoirs = (
        ReservoirSpec(
            object_id=1,
            name="R1",
            emin_profile=(1.0, 2.0),
            emax_profile=(10.0, 20.0),
            spill_flow_penalty=3.0,
        ),
        ReservoirSpec(object_id=2, name="R2"),
        ReservoirSpec(object_id=3, name="R3", emax=50.0),
    )
    out = gw.build_reservoir_array(reservoirs, apply_default_loss=True)
    assert out[0]["emin"] == [[1.0, 2.0]]
    assert out[0]["emax"] == [[10.0, 20.0]]
    assert out[0]["spillway_cost"] == 3.0
    assert out[1]["emax"] == 0.0
    assert out[2]["annual_standing_loss"] == 0.03


def test_apply_default_water_fail_nonpositive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gw, "default_water_fail_value", lambda _a: 0.0)
    entries = [{"efin": 1.0, "efin_cost": 5.0}, {"efin": 1.0}]
    assert gw.apply_default_water_fail(entries) == 0
    assert "efin_cost" not in entries[1]


def test_waterway_profile_and_inactive() -> None:
    wws = (
        WaterwaySpec(
            object_id=1,
            name="W1",
            storage_from="A",
            storage_to="B",
            forced_flow_profile=(1.0, 3.0, 5.0, 7.0),
        ),
        WaterwaySpec(
            object_id=2,
            name="W2",
            storage_from="A",
            storage_to="B",
            forced_flow_profile=(1.0, 3.0),
            pin_fmax_from_profile=False,
            fmin_fcost=9.0,
        ),
        WaterwaySpec(
            object_id=3,
            name="W3",
            storage_from="A",
            storage_to="B",
            inactive=True,
            fmin_fcost=9.0,
        ),
    )
    out = gw.build_waterway_array(wws, LAYOUT2)
    assert out[0]["fmin"] == [[2.0, 6.0]]
    assert out[0]["fmax"] == [[2.0, 6.0]]
    out2 = gw.build_waterway_array(wws)
    assert out2[1]["fmin"] == [[1.0, 3.0]]
    assert "fmax" not in out2[1]
    assert out2[1]["fmin_fcost"] == 9.0
    assert out2[2]["fmin"] == 0.0 and out2[2]["fmax"] == 0.0
    assert "fmin_fcost" not in out2[2]


def test_turbine_downstream_resolution() -> None:
    wws = (
        WaterwaySpec(object_id=1, name="W1", storage_from="R1", storage_to="R2"),
        WaterwaySpec(object_id=2, name="W1b", storage_from="R1", storage_to="R9"),
        WaterwaySpec(object_id=3, name="W3", storage_from="R3"),
        WaterwaySpec(object_id=4, name="W4"),
    )
    turbines = (
        TurbineSpec(generator_name="G1", reservoir_name="R1"),
        TurbineSpec(generator_name="G3", reservoir_name="R3"),
    )
    out = gw.build_turbine_array(turbines, wws, extra_waterways=[])
    assert out[0]["junction_b"] == "R2"
    assert "junction_b" not in out[1]


def test_reservoir_production_factor_array() -> None:
    rpf = ReservoirProductionFactorSpec(
        uid=1,
        name="rpf",
        turbine_name="T",
        reservoir_name="R",
        segments=(ProductionFactorSegmentSpec(volume=1.0, slope=2.0, constant=3.0),),
    )
    out = gw.build_reservoir_production_factor_array((rpf,))
    assert out[0]["uid"] == 1
    assert out[0]["reservoir"] == "R"


def test_reserve_zone_shapes() -> None:
    rsv = (ReserveSpec(object_id=1, name="Z", ur_requirement=(1.0, 2.0, 3.0, 4.0)),)
    assert gw.build_reserve_zone_array(rsv, block_layout=LAYOUT2)[0]["urreq"] == [
        [1.5, 3.5]
    ]
    tiled = gw.build_reserve_zone_array(
        (ReserveSpec(object_id=1, name="Z", ur_requirement=(1.0, 2.0)),),
        block_count=4,
    )
    assert tiled[0]["urreq"] == [[1.0, 2.0, 1.0, 2.0]]
    padded = gw.build_reserve_zone_array(
        (ReserveSpec(object_id=1, name="Z", ur_requirement=(1.0, 2.0, 3.0)),),
        block_count=4,
    )
    assert padded[0]["urreq"] == [[1.0, 2.0, 3.0, 0.0]]


# ---------------------------------------------------------------------------
# User constraints / flow rights / commitments / provisions
# ---------------------------------------------------------------------------


def test_user_constraint_rhs_shapes_and_fallback_penalty() -> None:
    ucs = (
        UserConstraintSpec(name="a", expression="x <= 1", rhs_profile=(1.0, 2.0)),
        UserConstraintSpec(name="b", expression="x <= 1", rhs_profile=(1.0,)),
        UserConstraintSpec(name="c", expression="x <= 1", rhs_profile=(1.0, 2.0, 3.0)),
    )
    out = gw.build_user_constraint_array(ucs, default_penalty=50.0, block_count=2)
    assert out[0]["rhs"] == [[1.0, 2.0]]
    assert out[1]["rhs"] == [[1.0, 1.0]]
    assert out[2]["rhs"] == [[1.0, 2.0]]
    assert out[0]["penalty"] == 50.0
    out = gw.build_user_constraint_array(
        (UserConstraintSpec(name="d", expression="x", rhs_profile=(1.0, 3.0, 5.0)),),
        block_layout=((1,), (2, 3)),
    )
    assert out[0]["rhs"] == [[1.0, 4.0]]


def test_flow_right_target_profile() -> None:
    frs = (
        FlowRightSpec(name="F", junction_name="J", target_profile=(1.0, 3.0, 5.0, 7.0)),
    )
    wws = (
        WaterwaySpec(object_id=1, name="Vert_A", storage_from="A", storage_to="B"),
        WaterwaySpec(object_id=2, name="Vert_A2", storage_from="A", storage_to="C"),
        WaterwaySpec(object_id=3, name="Other", storage_from="A", storage_to="C"),
    )
    out = gw.build_flow_right_array(frs, block_layout=LAYOUT2, waterways=wws)
    assert out[0]["target"] == [[2.0, 6.0]]
    out = gw.build_flow_right_array(frs)
    assert out[0]["target"] == [[1.0, 3.0, 5.0, 7.0]]


def test_commitment_profiles_and_starts() -> None:
    c = CommitmentSpec(
        generator_name="G",
        ramp_up_profile=(1.0, 3.0, 5.0, 7.0),
        ramp_down_profile=(2.0, 2.0, 4.0, 4.0),
        startup_ramp=5.0,
        pmin_profile=(1.0, 1.0, 3.0, 3.0),
        commit_status_profile=(1, 1, 1, 1),
        max_starts=2,
        min_starts=1,
        starts_scope="day",
    )
    e = gw.build_commitment_array((c,), block_layout=LAYOUT2)[0]
    assert e["ramp_up"] == [[2.0, 6.0]]
    assert e["ramp_down"] == [[2.0, 4.0]]
    assert e["startup_ramp"] == 5.0
    assert e["pmin"] == [[1.0, 3.0]]
    assert e["must_run"] is True
    assert e["max_starts"] == 2 and e["min_starts"] == 1
    assert e["starts_scope"] == "day"

    c2 = CommitmentSpec(
        generator_name="G2",
        ramp_up=4.0,
        ramp_down=6.0,
        ramp_up_profile=(1.0, 3.0),
        ramp_down_profile=(1.0, 3.0),
        pmin_profile=(1.0, 3.0),
        commit_status_profile=(-1, -1),
        max_starts=3,
        starts_scope="week",
    )
    e2 = gw.build_commitment_array((c2,))[0]
    assert e2["ramp_up"] == [[1.0, 3.0]]
    assert e2["pmin"] == [[1.0, 3.0]]
    assert "fixed_status" not in e2 and "must_run" not in e2
    assert "min_starts" not in e2

    c3 = CommitmentSpec(
        generator_name="G3",
        ramp_up=4.0,
        ramp_down=6.0,
        commit_status_profile=(0, -1),
        min_starts=1,
        starts_scope="day",
    )
    e3 = gw.build_commitment_array((c3,))[0]
    assert e3["ramp_up"] == 4.0 and e3["ramp_down"] == 6.0
    assert e3["fixed_status"] == [[0.0, -1.0]]
    assert "max_starts" not in e3


def test_commitment_fixed_status_all_free_after_aggregation() -> None:
    c = CommitmentSpec(generator_name="G", commit_status_profile=(1, 0))
    e = gw.build_commitment_array((c,), block_layout=((1, 2),))[0]
    assert "fixed_status" not in e


def test_reserve_provision_factor_profile() -> None:
    p = ReserveProvisionSpec(
        generator_name="G",
        reserve_zones=("Z",),
        urmax=10.0,
        ur_provision_factor_profile=(1.0, 0.5, 0.5, 0.5),
    )
    out = gw.build_reserve_provision_array((p,), LAYOUT2)
    assert out[0]["ur_provision_factor"] == [[0.75, 0.5]]
    out = gw.build_reserve_provision_array((p,))
    assert out[0]["ur_provision_factor"] == [[1.0, 0.5, 0.5, 0.5]]


# ---------------------------------------------------------------------------
# System post-processing passes
# ---------------------------------------------------------------------------


def _ocean_system() -> dict[str, object]:
    return {
        "junction_array": [
            {"uid": 1, "name": "R1"},
            {"uid": 2, "name": "R1_ocean", "drain": True},
            {"uid": 3, "name": "R2"},
            {"uid": 4, "name": "R2_ocean", "drain": True},
        ],
        "waterway_array": [
            {"uid": 10, "name": "Vert_R1", "junction_a": 1, "junction_b": "R1_ocean"},
            {"uid": 11, "name": "Gen_R1", "junction_a": "R1", "junction_b": 3},
            {"uid": 12, "name": "Vert_X", "junction_a": "NOPE", "junction_b": 2},
            {"uid": 13, "name": "Vert_R2", "junction_a": "R2", "junction_b": "R2"},
            {"uid": 14, "name": "Vert_R1b", "junction_a": 1.5, "junction_b": 2},
        ],
        "reservoir_array": [
            {"name": "R1", "emax": [[0.0, 10.0]]},
            {"name": "R2", "emax": 5.0, "spillway_cost": 7.0},
        ],
        "flow_right_array": [
            {"name": "F1", "junction_b": 2, "bypass_cost": 1.0},
            {"name": "F2", "junction_b": None},
            {"name": "F3", "junction_b": "R2"},
        ],
    }


def test_internalise_real_reservoir_ocean_spill(
    caplog: pytest.LogCaptureFixture,
) -> None:
    system = _ocean_system()
    with caplog.at_level(logging.INFO):
        assert gw._internalise_real_reservoir_ocean_spill(system) == 1
    names = [w["name"] for w in system["waterway_array"]]  # type: ignore[index,union-attr]
    assert "Vert_R1" not in names
    junc_names = [j["name"] for j in system["junction_array"]]  # type: ignore[index,union-attr]
    assert "R1_ocean" not in junc_names
    r1 = system["reservoir_array"][0]  # type: ignore[index]
    assert r1["spillway_cost"] == 0.0
    f1 = system["flow_right_array"][0]  # type: ignore[index]
    assert "junction_b" not in f1 and "bypass_cost" not in f1
    assert system["flow_right_array"][2]["junction_b"] == "R2"  # type: ignore[index]


def test_internalise_noop_when_nothing_converts() -> None:
    system = {
        "junction_array": [{"uid": 1, "name": "R1"}],
        "waterway_array": [{"uid": 1, "name": "Vert_R1", "junction_a": "R1"}],
        "reservoir_array": [{"name": "R1", "emax": 1.0}],
    }
    assert gw._internalise_real_reservoir_ocean_spill(system) == 0


def test_collapse_orphan_drain_blocked_refs() -> None:
    system = {
        "junction_array": [
            {"uid": 1, "name": "A_sink", "drain": True},
            {"uid": 2, "name": "B_sink", "drain": True},
            {"uid": 3, "name": "C_sink", "drain": True},
            {"uid": 4, "name": "D_sink", "drain": True},
            {"uid": 5, "name": "E_sink", "drain": True},
        ],
        "waterway_array": [
            {"junction_a": 1, "junction_b": 9.5},
            {"junction_a": "X", "junction_b": 5},
        ],
        "turbine_array": [{"junction_a": "B_sink", "junction_b": "Q"}],
        "reservoir_array": [{"junction": "C_sink"}],
        "flow_right_array": [{"junction_a": "D_sink"}],
    }
    assert gw._collapse_orphan_drain_outflows(system) == 1
    names = {j["name"] for j in system["junction_array"]}  # type: ignore[union-attr]
    assert names == {"A_sink", "B_sink", "C_sink", "D_sink"}


def test_drop_large_reservoir_spillways_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import gtopt_shared.reservoir_flow as rf  # noqa: PLC0415

    monkeypatch.setattr(
        rf, "drop_large_reservoir_spillways", lambda *a, **k: ["Vert_A", "Vert_B"]
    )
    system = {"waterway_array": [{"name": "Vert_A"}], "user_constraint_array": []}
    with caplog.at_level(logging.INFO):
        gw._drop_large_reservoir_spillways(system, {"A": 1e9}, 1.0)
    assert "dropped 2" in caplog.text


# ---------------------------------------------------------------------------
# build_planning / IO helpers
# ---------------------------------------------------------------------------


def test_build_planning_rpf_and_el1_softening(caplog: pytest.LogCaptureFixture) -> None:
    case = PlexosCase(
        bundle=BundleSpec(),
        generators=(_gen(pmax=10.0, vom_charge=5.0),),
        lines=(
            LineSpec(
                object_id=1,
                name="L",
                bus_from="b",
                bus_to="c",
                tmax_ab=50.0,
                enforce_limits=1,
            ),
        ),
        reservoir_production_factors=(
            ReservoirProductionFactorSpec(
                uid=1, name="rpf", turbine_name="T", reservoir_name="R"
            ),
        ),
    )
    with caplog.at_level(logging.INFO):
        planning = gw.build_planning(case, name="t")
    system = planning["system"]
    assert system["reservoir_production_factor_array"][0]["name"] == "rpf"
    assert system["line_array"][0]["tmax_normal_ab"] == 50.0
    assert "softened 1 EL=1" in caplog.text


def test_install_solver_param_files_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty = tmp_path / "solvers"
    empty.mkdir()
    monkeypatch.setattr(gw, "_BUNDLED_SOLVERS_DIR", empty)
    with pytest.raises(gw.MissingDataFileError):
        gw.install_solver_param_files(tmp_path / "out")


def test_parse_water_value_factor_skips_blank_tokens() -> None:
    assert gw.parse_water_value_factor("A:2, ,B:0.5,") == {"A": 2.0, "B": 0.5}


def test_write_boundary_cut_csv_warns_ignored_factor(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cut = BoundaryCutSpec(fcf=10.0, slopes={"R1": 2.0})
    with caplog.at_level(logging.WARNING):
        name = gw.write_boundary_cut_csv(
            cut,
            frozenset({"R1"}),
            tmp_path,
            water_value_factor={"R1": 2.0, "GHOST": 3.0},
        )
    assert name == "boundary_cuts.csv"
    assert "GHOST" in caplog.text
    assert "0,10,-4" in (tmp_path / name).read_text()


def test_annotate_descriptions_user_constraints() -> None:
    system = {"user_constraint_array": [{"name": "u", "expression": "x"}]}
    gw._annotate_element_descriptions(system)
    uc = system["user_constraint_array"][0]
    assert "type" not in uc
    assert uc["description"]


def test_pampl_unique_ident_and_window_directive(tmp_path: Path) -> None:
    ucs = [
        {
            "name": "a-b",
            "expression": "x <= 1",
            "directive": {"kind": "rolling", "window_hours": 4},
        },
        {"name": "a_b", "expression": "y <= 1", "rhs": [[1.0, 2.0]]},
    ]
    files, remaining = gw.write_user_constraint_pampl(ucs, tmp_path)
    assert not remaining
    text = "".join((tmp_path / f).read_text() for f in files)
    assert "window_hours=4" in text
    assert "constraint a_b_2 rhs [1, 2]:" in text


def test_misc_fallthrough_branches() -> None:
    # Reservoir with neither emax nor zero eini keeps emax unset.
    out = gw.build_reservoir_array((ReservoirSpec(object_id=1, name="R", eini=5.0),))
    assert "emax" not in out[0]
    # Battery with explicit annual_standing_loss keeps it under the default-loss path.
    bat = BatterySpec(
        object_id=1,
        name="B",
        bus_name="b",
        emax=10.0,
        max_cycles_day=1.0,
        annual_standing_loss=0.1,
    )
    assert gw.build_battery_array((bat,), apply_default_loss=True)[0][
        "annual_standing_loss"
    ] == pytest.approx(0.1)
    # Waterway with no bounds at all.
    ww = WaterwaySpec(object_id=1, name="W", storage_from="A", storage_to="B")
    assert "fmax" not in gw.build_waterway_array((ww,))[0]
    # Flow on a non-candidate junction does not block the collapse.
    system = {
        "junction_array": [{"uid": 1, "name": "A_sink", "drain": True}],
        "waterway_array": [{"junction_b": "A_sink"}],
        "flow_array": [{"junction": "OTHER"}],
    }
    assert gw._collapse_orphan_drain_outflows(system) == 1


def test_water_value_resolver_positive_slope_has_no_cut_value() -> None:
    res = (ReservoirSpec(object_id=1, name="R1", emax=100.0),)
    resolver, _ = gw.build_water_value_resolver(
        reservoirs=res,
        turbines=(),
        generator_entries=[{"gcost": 3.0}],
        demand_entries=[{"fcost": 500.0}],
        boundary_cut=BoundaryCutSpec(fcf=1.0, slopes={"R1": 5.0}),
    )
    assert resolver is not None


def test_commitment_fast_start_flag() -> None:
    """PLEXOS Run Up Rate vs Min Stable Level decides ``fast_start``."""
    fast = CommitmentSpec(generator_name="GT", pmin=20.0, startup_ramp=60.0)
    slow_ramp = CommitmentSpec(generator_name="CC", pmin=200.0, startup_ramp=60.0)
    slow_mut = CommitmentSpec(
        generator_name="COAL", pmin=20.0, startup_ramp=60.0, min_up_time=8.0
    )
    no_ramp = CommitmentSpec(generator_name="D", pmin=5.0)
    profile = CommitmentSpec(
        generator_name="P",
        pmin_profile=(10.0, 80.0),
        startup_ramp=60.0,
        min_up_time=1.0,
        min_down_time=1.0,
    )
    assert gw.plexos_fast_start(fast) is True
    assert gw.plexos_fast_start(slow_ramp) is False
    assert gw.plexos_fast_start(slow_mut) is None
    assert gw.plexos_fast_start(no_ramp) is None
    assert gw.plexos_fast_start(profile) is False  # peak floor 80 > 60
    out = gw.build_commitment_array((fast, slow_ramp, slow_mut, no_ramp))
    assert [e.get("fast_start") for e in out] == [True, False, None, None]
