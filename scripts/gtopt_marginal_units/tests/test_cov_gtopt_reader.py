# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Planning-JSON topology reader and output-dir cells reader edge cases."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from gtopt_marginal_units._gtopt_reader import (
    _classify_kind,
    _is_cogen,
    _merge_signed_flow,
    _scalar_or_max,
    _wide_to_long,
    cells_from_gtopt_output,
    demand_fail_cost_by_bus,
    topology_from_planning,
)
from gtopt_marginal_units.errors import (
    ExpansionNotSupportedError,
    InputValidationError,
)


def _plan(**system: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"bus_array": [{"uid": 1, "name": "b1"}]}
    base.update(system)
    return {"system": base}


def _gen(uid: int, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"uid": uid, "name": f"G{uid}", "bus": 1, "pmax": 10}
    out.update(kw)
    return out


# ---------------------------------------------------------------------------
# topology_from_planning
# ---------------------------------------------------------------------------


def test_bus_reference_forms():
    plan = _plan(
        generator_array=[_gen(1, bus=1.0), _gen(2, bus="b1"), _gen(3, bus="uid:7")]
    )
    topo = topology_from_planning(plan)
    assert [g.bus_uid for g in topo.generators] == [1, 1, 7]
    with pytest.raises(InputValidationError, match="cannot resolve"):
        topology_from_planning(_plan(generator_array=[_gen(1, bus="nowhere")]))
    with pytest.raises(InputValidationError, match="cannot resolve"):
        topology_from_planning(_plan(generator_array=[_gen(1, bus=None)]))


def test_emission_sources_parquet_rates_and_skips(tmp_path: Path, caplog):
    out = tmp_path / "out"
    (out / "EmissionSource").mkdir(parents=True)
    pd.DataFrame({"uid": [1, 1], "value": [0.4, 0.6]}).to_parquet(
        out / "EmissionSource" / "rate_sol.parquet"
    )
    zones = [
        {
            "uid": 1,
            "emissions": [
                {"emission": "co2", "weight": 1.0},
                {"emission": "nox", "weight": 0.0},
            ],
        }
    ]
    sources = [
        {"uid": 1, "generator": 1, "zone": 1, "emission": "co2", "rate": 9.0},
        {"uid": 2, "generator": 1, "zone": 1, "emission": "nox", "rate": 1.0},
        {"uid": 3, "generator": 2, "zone": 1, "emission": "co2", "rate": 0.0},
    ] + [{"uid": 10 + i} for i in range(5)]  # malformed rows
    plan = _plan(
        emission_zone_array=zones,
        emission_source_array=sources,
        generator_array=[_gen(1, type="thermal:gas"), _gen(2, type="thermal:gas")],
    )
    with caplog.at_level(logging.INFO):
        topo = topology_from_planning(plan, output_dir=out)
    assert topo.generators[0].emission_rate == pytest.approx(0.5)
    assert topo.generators[1].emission_rate is None
    assert "additional malformed rows" in caplog.text
    assert "1 thermal generators have no emission_rate" in caplog.text


def test_emission_rate_parquet_read_failure_falls_back(tmp_path: Path, caplog):
    out = tmp_path / "out"
    (out / "EmissionSource").mkdir(parents=True)
    (out / "EmissionSource" / "rate_sol.parquet").write_text("bad", encoding="utf-8")
    plan = _plan(
        emission_zone_array=[{"uid": 1, "emissions": [{"emission": "co2"}]}],
        emission_source_array=[
            {"uid": 1, "generator": 1, "zone": 1, "emission": "co2", "rate": 0.3}
        ],
        generator_array=[_gen(1)],
    )
    topo = topology_from_planning(plan, output_dir=out)
    assert topo.generators[0].emission_rate == pytest.approx(0.3)
    assert "read failed" in caplog.text


def test_malformed_emission_zone_entries_are_dropped(caplog):
    """Regression: a zone emission entry without ``emission`` (or a zone
    without ``uid``) used to raise KeyError in ``zone_weights``, while malformed
    ``emission_source_array`` rows were already dropped with a warning."""
    zones = [
        {
            "uid": 1,
            "emissions": [
                {"weight": 5.0},  # no "emission" key
                {"emission": "co2", "weight": 2.0},
            ],
        },
        {"name": "no_uid", "emissions": [{"emission": "co2"}]},
    ]
    sources = [
        {"uid": 1, "generator": 1, "zone": 1, "emission": "co2", "rate": 0.5},
        {"uid": 2, "generator": 1, "zone": 1, "emission": "nox", "rate": 1.0},
    ]
    plan = _plan(
        emission_zone_array=zones,
        emission_source_array=sources,
        generator_array=[_gen(1, type="thermal:gas")],
    )
    with caplog.at_level(logging.WARNING):
        topo = topology_from_planning(plan)
    assert topo.generators[0].emission_rate == pytest.approx(1.0)
    assert caplog.text.count("emission entry malformed") == 1
    assert "zone dropped" in caplog.text


def test_fuel_kinds_and_derived_emission(caplog):
    fuels = [
        {"uid": 1, "name": "agua", "type": "hydro"},
        {"uid": 2, "name": "sol", "type": "solar"},
        {
            "uid": 3,
            "name": "gas",
            "type": "gas",
            "heat_content": 2.0,
            "emission_factors": [
                {"emission": "co2", "combustion": 0.5},
                {"emission": "untracked", "combustion": 9.0},
                "junk",
            ],
        },
        {
            "uid": "x",
            "name": "coal",
            "type": "coal",
            "heat_content": 1.0,
            "emission_factors": [{"emission": "co2", "combustion": 1.0}],
        },
        {"uid": 5, "type": "nuclear"},
        {"uid": 6, "name": "untyped"},
        {"uid": 7, "name": "noheat", "type": "diesel"},
        {
            "uid": 8,
            "name": "zeroef",
            "type": "diesel",
            "heat_content": 1.0,
            "emission_factors": [{"emission": "co2", "combustion": 0.0}],
        },
    ]
    zones = [
        {"uid": 1, "emissions": [{"emission": "co2", "weight": 1.0}, {"emission": ""}]}
    ]
    gens = [
        _gen(1, type="thermal", fuel=3, heat_rate=2.0),  # uid fuel ref
        _gen(2, type="thermal", fuel="coal", heat_rate=3.0),  # name ref
        _gen(3, fuel={"name": "agua"}),  # dict fuel -> hydro
        _gen(4, type="renewable", fuel=2),  # bare renewable -> profile
        _gen(5, type="thermal", fuel=3, heat_rate="file"),
        _gen(6, type="thermal", fuel=99, heat_rate=1.0),
        _gen(
            7,
            name="ORPHAN",
            type="thermal:gas",
            description="x [gtopt-meta mode_variant=secondary "
            "inherits_emission_from=G1]",
        ),
        _gen(
            8,
            type="thermal:gas",
            description="[gtopt-meta inherits_emission_from=NOPE]",
        ),
        _gen(9, type="thermal:gas", description="[gtopt-meta inherits_emission_from]"),
        _gen(10, type="thermal:gas", emission_rate=0.2, gcost=5.0),
    ]
    plan = _plan(fuel_array=fuels, emission_zone_array=zones, generator_array=gens)
    with caplog.at_level(logging.INFO):
        topo = topology_from_planning(plan)
    by_uid = {g.uid: g for g in topo.generators}
    assert by_uid[1].emission_rate == pytest.approx(2.0)
    assert by_uid[2].emission_rate == pytest.approx(3.0)
    assert by_uid[3].kind == "hydro"
    assert by_uid[4].kind == "profile"
    assert by_uid[5].emission_rate is None
    assert by_uid[7].emission_rate == pytest.approx(2.0)
    assert by_uid[10].declared_MC == 5.0
    assert "non-castable uid" in caplog.text
    assert "inherited emission_rate" in caplog.text


def test_profile_promotion_expansion_and_lines():
    plan = _plan(
        generator_array=[_gen(1), _gen(2)],
        generator_profile_array=[{"uid": 2}],
        line_array=[
            {"uid": 5, "bus_a": 1, "bus_b": "b1", "tmax": [[1, 7]], "active": False}
        ],
    )
    topo = topology_from_planning(plan)
    assert [g.kind for g in topo.generators] == ["thermal", "profile"]
    assert topo.lines[0].tmax_ab == 7.0 and not topo.lines[0].active
    with pytest.raises(ExpansionNotSupportedError):
        topology_from_planning(_plan(generator_array=[_gen(1, expansion=True)]))
    with pytest.raises(ExpansionNotSupportedError):
        topology_from_planning(
            _plan(
                line_array=[{"uid": 1, "bus_a": 1, "bus_b": 1, "expansion": {"n": 1}}]
            )
        )


def test_many_thermals_without_emission_sample_truncated(caplog):
    plan = _plan(generator_array=[_gen(i, type="thermal:gas") for i in range(1, 8)])
    topology_from_planning(plan)
    assert "7 thermal generators have no emission_rate" in caplog.text
    assert ", ..." in caplog.text


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_demand_fail_cost_by_bus():
    plan = {
        "options": {"model_options": {"demand_fail_cost": 500}},
        "system": {
            "bus_array": [{"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"}],
            "demand_array": [
                {"bus": 1.0, "fcost": 300},
                {"bus": "b1", "fcost": 200},
                {"bus": "zz", "fcost": 1},
                {"bus": "b2", "fcost": "sched.parquet"},
            ],
        },
    }
    assert demand_fail_cost_by_bus(plan, 1000.0) == {1: 200.0, 2: 500.0}


@pytest.mark.parametrize(
    ("gen", "expected"),
    [
        ({"cogen_mode": "x"}, True),
        ({"is_cogen": True}, True),
        ({"type": "thermal:cogen_x"}, True),
        ({"name": "PAS_MEJILLONES"}, True),
        ({"name": "arauco_1"}, True),
        ({"type": "renewable:biomass"}, True),
        ({"type": "thermal:otros"}, True),
        ({"fuel": "Otros_x"}, True),
        ({"fuel": {"name": "Noracid_2"}}, True),
        ({"name": "PAS_OTHER"}, True),
        ({"name": "", "fuel": 3}, False),
    ],
)
def test_is_cogen_layers(gen: dict[str, Any], expected: bool):
    assert _is_cogen(gen) is expected


@pytest.mark.parametrize(
    ("gen", "expected"),
    [
        ({"type": "thermal"}, "thermal"),
        ({"type": "renewable:hydro"}, "hydro"),
        ({"type": "renewable:wind"}, "profile"),
        ({"type": "renewable:geothermal"}, "profile"),
        ({"type": "renewable:biogas"}, "thermal"),
        ({"type": "renewable"}, "profile"),
        ({"type": "battery"}, "battery"),
        ({"fuel": {"uid": 1}}, "hydro"),
        ({"fuel": {"uid": 9}}, "thermal"),
        ({"fuel": 2.5}, "thermal"),
    ],
)
def test_classify_kind(gen: dict[str, Any], expected: str):
    assert _classify_kind(gen, {1: "hydro"}) == expected


def test_scalar_or_max():
    assert _scalar_or_max(None, 3.0) == 3.0
    assert _scalar_or_max([1, [5, "x"], "y"]) == 5.0
    assert _scalar_or_max(["x"], 2.0) == 2.0
    assert _scalar_or_max("file", 4.0) == 4.0


def test_wide_to_long_and_signed_flow_merge():
    long = pd.DataFrame({"stage": [1], "uid": ["3"], "value": [2.0]})
    out = _wide_to_long(long, "line_uid", "flow")
    assert out is not None and out["line_uid"].tolist() == [3]
    assert _wide_to_long(pd.DataFrame({"stage": [1]}), "u", "v") is None
    neg = pd.DataFrame({"stage": [1], "block": [1], "uid:3": [4.0]})
    only_neg = _merge_signed_flow(None, neg)
    assert only_neg is not None and only_neg["flow"].tolist() == [-4.0]
    pos = pd.DataFrame({"stage": [1], "block": [1], "uid:3": [10.0]})
    both = _merge_signed_flow(pos, neg)
    assert both is not None and both["flow"].tolist() == [6.0]
    assert _merge_signed_flow(None, None) is None


def test_cells_from_output_errors_and_signed_flow(tmp_path: Path):
    with pytest.raises(InputValidationError, match="missing or empty"):
        cells_from_gtopt_output(tmp_path)
    (tmp_path / "Generator").mkdir()
    pd.DataFrame({"stage": [1], "x": [1.0]}).to_csv(
        tmp_path / "Generator/generation_sol.csv", index=False
    )
    with pytest.raises(InputValidationError, match="neither the"):
        cells_from_gtopt_output(tmp_path)
    pd.DataFrame({"stage": [1], "block": [1], "uid:1": [5.0]}).to_csv(
        tmp_path / "Generator/generation_sol.csv", index=False
    )
    (tmp_path / "Line").mkdir()
    pd.DataFrame({"stage": [1], "block": [1], "uid:9": [-3.0]}).to_csv(
        tmp_path / "Line/flow_sol.csv", index=False
    )
    cells = cells_from_gtopt_output(tmp_path)
    assert cells.flow is not None and cells.flow["flow"].tolist() == [-3.0]
    assert cells.lmp is None
