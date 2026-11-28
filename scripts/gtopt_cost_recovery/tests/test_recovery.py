# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Unit tests of the cost-recovery arithmetic on synthetic outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from gtopt_cost_recovery import (
    ProjectRecovery,
    annual_capcost,
    capital_recovery_factor,
    compute_recovery,
)
from gtopt_cost_recovery.main import print_report
from gtopt_cost_recovery.recovery import _stage_table, _stage_value

from .conftest import sim, write_case, write_stream


def test_crf_closed_forms() -> None:
    g = 1.08**30
    assert capital_recovery_factor(0.08, 30) == pytest.approx(0.08 * g / (g - 1))
    assert capital_recovery_factor(0.0, 20) == pytest.approx(0.05)
    assert capital_recovery_factor(0.06, None) == pytest.approx(0.06)
    assert capital_recovery_factor(0.06, 0) == pytest.approx(0.06)
    assert capital_recovery_factor(-0.5, None) == 0.0
    assert capital_recovery_factor(-2.0, 10) == 0.0


def test_annual_capcost_precedence() -> None:
    elem: dict[str, Any] = {"annual_capcost": 5.0, "overnight_capcost": 100.0}
    assert annual_capcost(elem, 0, 0.05) == 5.0
    elem = {"overnight_capcost": 100.0, "economic_life": 20, "wacc": 0.1}
    assert annual_capcost(elem, 0, 0.05) == pytest.approx(
        100.0 * capital_recovery_factor(0.1, 20)
    )
    elem = {"overnight_capcost": [100.0, 200.0], "economic_life": 20}
    assert annual_capcost(elem, 1, 0.05) == pytest.approx(
        200.0 * capital_recovery_factor(0.05, 20)
    )
    assert annual_capcost({}, 0, 0.05) == 0.0
    battery = {"power_annual_capcost": 7.0}
    assert annual_capcost(battery, 0, 0.0, prefix="power_") == 7.0


def test_stage_value_shapes(caplog: pytest.LogCaptureFixture) -> None:
    assert _stage_value(None, 0) is None
    assert _stage_value(True, 0) is None
    assert _stage_value([1.0, 2.0], 5) == 2.0
    assert _stage_value([[1.0]], 0) is None
    assert _stage_value([], 0) is None
    assert _stage_value("file", 0) is None
    assert "file-scheduled" in caplog.text


def test_stage_table_discounting() -> None:
    planning = {"simulation": sim([4380, 4380, 4380, 4380], stages=2)}
    table = _stage_table(planning, 0.1)
    assert table[1] == (0, 8760.0, pytest.approx(1.0))
    assert table[2][2] == pytest.approx(1 / 1.1)
    planning["simulation"]["stage_array"][1]["discount_factor"] = 0.5
    assert _stage_table(planning, 0.0)[2][2] == pytest.approx(0.5)
    assert _stage_table({"simulation": {"block_array": [{"duration": 3}]}}, 0.0)[1] == (
        0,
        3.0,
        1.0,
    )


def test_generator_zero_profit(gen_case: Callable[..., Path]) -> None:
    rep = compute_recovery(gen_case())
    (p,) = rep.projects
    assert p.kind == "Generator" and p.name == "g"
    assert p.installed == 50.0 and p.new == 50.0 and p.built
    assert p.revenue == pytest.approx(2000.0)
    assert p.opex == pytest.approx(1000.0)
    assert p.capex == pytest.approx(1000.0)
    assert p.profit == pytest.approx(0.0)
    assert p.make_whole == 0.0
    assert p.value_per_unit == pytest.approx(20.0)
    # 87600 $/MW-yr over 2 h = 20 $/MW
    assert p.cost_per_unit == pytest.approx(20.0)
    assert p.net_value_per_unit == pytest.approx(0.0)
    assert rep.obj_value == pytest.approx(2500.0)
    assert rep.market.load_payments == pytest.approx(3500.0)
    assert rep.market.generator_revenue == pytest.approx(3500.0)
    assert rep.market.residual == pytest.approx(0.0)
    assert not rep.existing


def test_generator_missing_money_and_existing(gen_case: Callable[..., Path]) -> None:
    rep = compute_recovery(gen_case(capacost_rate=750.0), include_existing=True)
    (p,) = rep.projects
    assert p.capex == pytest.approx(1500.0)
    assert p.profit == pytest.approx(-500.0)
    assert p.make_whole == pytest.approx(500.0)
    assert p.profit_over_capex == pytest.approx(-1 / 3)
    assert rep.total_make_whole == pytest.approx(500.0)
    (old,) = rep.existing
    assert old.name == "old" and old.margin == pytest.approx(0.0)
    d = rep.to_dict()
    assert d["total_profit"] == pytest.approx(-500.0)
    assert d["projects"][0]["make_whole"] == pytest.approx(500.0)


def test_existing_capacity_is_prorated(gen_case: Callable[..., Path]) -> None:
    # 25 MW existing of 50 installed: half the margin belongs to the build.
    rep = compute_recovery(gen_case(capacost_rate=250.0, capacity=25))
    (p,) = rep.projects
    assert p.existing == 25.0 and p.new == 25.0
    assert p.margin_new == pytest.approx(500.0)
    assert p.profit == pytest.approx(0.0)


def test_per_year_scaling(gen_case: Callable[..., Path]) -> None:
    rep = compute_recovery(gen_case(), per_year=True)
    assert rep.scale == pytest.approx(4380.0)
    assert rep.projects[0].revenue == pytest.approx(2000.0 * 4380.0)
    assert rep.market.load_payments == pytest.approx(3500.0 * 4380.0)


def test_gcost_fallback_without_srmc(gen_case: Callable[..., Path]) -> None:
    rdir = gen_case()
    rep = compute_recovery(rdir)
    assert rep.projects[0].opex == pytest.approx(1000.0)  # gcost 10


def test_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="planning"):
        compute_recovery(tmp_path)
    write_case(tmp_path, {"simulation": sim([1])}, {})
    with pytest.raises(FileNotFoundError, match="balance_dual"):
        compute_recovery(tmp_path)


def test_line_rent_and_candidate(tmp_path: Path) -> None:
    planning = {
        "options": {"annual_discount_rate": 0.0},
        "simulation": sim([1, 1]),
        "system": {
            "bus_array": [{"uid": 1, "name": "a"}, {"uid": 2, "name": "b"}],
            "line_array": [
                {
                    "uid": 1,
                    "name": "ab",
                    "bus_a": "a",
                    "bus_b": "b",
                    "capacity": 0,
                    "expcap": 10,
                    "expmod": 10,
                    "annual_capcost": 43800.0,
                    "tcost": 1.0,
                },
                {
                    "uid": 2,
                    "name": "cand",
                    "bus_a": 1,
                    "bus_b": 2,
                    "expcap": 10,
                    "expmod": 1,
                    "overnight_capcost": 1.0e7,
                    "economic_life": 40,
                    "wacc": 0.08,
                },
            ],
        },
    }
    streams = {
        "Bus/balance_dual": [
            (1, 1, 1, 1, 10.0),
            (1, 1, 1, 2, 20.0),
            (1, 1, 2, 1, 10.0),
            (1, 1, 2, 2, 10.0),
        ],
        "Line/flow_sol": [(1, 1, 1, 1, 40.0), (1, 1, 2, 1, -10.0)],
        "Line/capainst_sol": [(0, 1, 0, 1, 40.0)],
        "Line/capacost_sol": [(0, 1, 0, 1, 200.0)],
        "Line/capacityp_dual": [(1, 1, 1, 1, 10.0)],
        "Line/capacityn_dual": [(1, 1, 1, 1, -0.0)],
    }
    rep = compute_recovery(write_case(tmp_path, planning, streams))
    ab, cand = rep.projects
    assert ab.revenue == pytest.approx(400.0)
    assert ab.opex == pytest.approx(50.0)  # tcost on |f|
    assert ab.capex == pytest.approx(400.0)
    assert ab.value_per_unit == pytest.approx(9.0)  # spread 10 − tcost 1
    assert ab.cost_per_unit == pytest.approx(10.0)
    assert not cand.built and cand.new == 0.0
    assert cand.value_per_unit == pytest.approx(10.0)
    assert cand.cost_per_unit == pytest.approx(
        1.0e7 * capital_recovery_factor(0.08, 40) * 2 / 8760
    )
    assert cand.net_value_per_unit < 0.0
    assert rep.market.congestion_rent == pytest.approx(400.0)


def test_line_flowp_flown(tmp_path: Path) -> None:
    planning = {
        "simulation": sim([2]),
        "system": {
            "bus_array": [{"uid": 1, "name": "a"}, {"uid": 2, "name": "b"}],
            "line_array": [
                {"uid": 1, "name": "ab", "bus_a": "a", "bus_b": "b", "capmax": 50}
            ],
        },
    }
    streams = {
        "Bus/balance_dual": [(1, 1, 1, 1, 10.0), (1, 1, 1, 2, 15.0)],
        "Line/flowp_sol": [(1, 1, 1, 1, 30.0)],
        "Line/flown_sol": [(1, 1, 1, 1, 10.0)],
    }
    rep = compute_recovery(write_case(tmp_path, planning, streams))
    (ab,) = rep.projects
    assert ab.revenue == pytest.approx(5.0 * 20.0 * 2.0)


def test_battery_two_axes(tmp_path: Path) -> None:
    planning = {
        "options": {"annual_discount_rate": 0.05},
        "simulation": sim([1, 1]),
        "system": {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "generator_array": [
                {"uid": 1, "name": "cheap", "bus": "b1", "gcost": 10},
                {"uid": 2, "name": "bess_gen", "bus": "b1", "gcost": 1.0},
            ],
            "demand_array": [
                {"uid": 1, "name": "load", "bus": "b1"},
                {"uid": 2, "name": "bess_dem", "bus": "b1"},
            ],
            "battery_array": [
                {
                    "uid": 1,
                    "name": "bess",
                    "capacity": 0,
                    "expcap": 10,
                    "expmod": 10,
                    "annual_capcost": 8760.0,
                    "charge_cost": 2.0,
                    "power_overnight_capcost": 1000.0,
                    "economic_life": 10,
                    "wacc": 0.1,
                }
            ],
            "converter_array": [
                {
                    "uid": 1,
                    "name": "bess_conv",
                    "capacity": 0,
                    "expcap": 10,
                    "expmod": 10,
                }
            ],
        },
    }
    streams = {
        "Bus/balance_dual": [(1, 1, 1, 1, 10.0), (1, 1, 2, 1, 50.0)],
        "Generator/generation_sol": [(1, 1, 2, 2, 20.0), (1, 1, 1, 1, 50.0)],
        "Demand/load_sol": [
            (1, 1, 1, 2, 20.0),
            (1, 1, 1, 1, 30.0),
            (1, 1, 2, 1, 20.0),
        ],
        "Battery/capainst_sol": [(0, 1, 0, 1, 20.0)],
        "Battery/capacost_sol": [(0, 1, 0, 1, 20.0)],
        "Battery/capacity_dual": [(1, 1, 1, 1, 1.0)],
        "Converter/capainst_sol": [(0, 1, 0, 1, 20.0)],
        "Converter/capacost_sol": [(0, 1, 0, 1, 100.0)],
        "Converter/capacity_dual": [(1, 1, 2, 1, 3.0)],
    }
    rep = compute_recovery(write_case(tmp_path, planning, streams))
    (b,) = rep.projects
    assert b.kind == "Battery" and b.unit == "MWh"
    assert b.revenue == pytest.approx(20 * 50 - 20 * 10)
    assert b.opex == pytest.approx(20 * 1.0 + 20 * 2.0)
    assert b.capex == pytest.approx(40.0)
    assert b.power_new == pytest.approx(20.0)
    assert b.power_capex == pytest.approx(200.0)
    assert b.total_capex == pytest.approx(240.0)
    assert b.power_value_per_unit == pytest.approx(3.0)
    assert b.power_cost_per_unit == pytest.approx(
        1000.0 * capital_recovery_factor(0.1, 10) * 2 / 8760
    )
    assert b.profit == pytest.approx(800 - 60 - 240)
    # The battery's charge/discharge are not load / generator revenue.
    assert rep.market.load_payments == pytest.approx(300.0 + 1000.0)
    assert rep.market.generator_revenue == pytest.approx(500.0)
    assert rep.market.battery_net_revenue == pytest.approx(800.0)
    assert rep.market.residual == pytest.approx(0.0)


def test_project_properties_edge_cases() -> None:
    p = ProjectRecovery(kind="Generator", name="x", uid=1, unit="MW")
    assert not p.built
    assert p.profit_over_capex is None
    assert p.margin_new == 0.0
    p.revenue, p.existing, p.installed, p.new = 10.0, 5.0, 5.0, 0.0
    assert p.margin_new == pytest.approx(0.0)
    assert p.make_whole == 0.0


def test_pricing_csv_modes(gen_case: Callable[..., Path]) -> None:
    rdir = gen_case()
    (rdir / "pricing.csv").write_text(
        "scene,phase,pricing_mode,applied_mode,fallback,attempted,"
        "relaxed_columns,pinned_columns,status,reason\n"
        "0,0,relaxed,relaxed,0,1,1,0,0,\n",
        encoding="utf-8",
    )
    assert compute_recovery(rdir).applied_modes == ["relaxed"]


def test_bad_solution_csv(gen_case: Callable[..., Path]) -> None:
    rdir = gen_case()
    (rdir / "solution.csv").write_text("scene,obj_value\n0,abc\n", encoding="utf-8")
    assert compute_recovery(rdir).obj_value is None
    (rdir / "solution.csv").unlink()
    assert compute_recovery(rdir).obj_value is None


def _with_profile(rdir: Path, profile: Any) -> Path:
    path = rdir / "planning.json"
    planning = json.loads(path.read_text(encoding="utf-8"))
    planning["system"]["generator_profile_array"] = [
        {"uid": 9, "name": "other", "generator": "old", "profile": 0.1},
        {"uid": 1, "name": "gp", "generator": "g", "profile": profile},
    ]
    path.write_text(json.dumps(planning), encoding="utf-8")
    return rdir


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        # margin per MW: (30 − 10)·a1·1 h + (10 − 10)·a2·1 h
        ([[[0.5, 1.0]]], 10.0),  # [scenario][stage][block]
        ([[0.25, 1.0]], 5.0),  # [stage][block]
        (0.75, 15.0),  # scalar
        ([], 20.0),  # empty: availability 1
    ],
)
def test_profile_factor_scales_unit_margin(
    gen_case: Callable[..., Path], profile: Any, expected: float
) -> None:
    rep = compute_recovery(_with_profile(gen_case(), profile))
    assert rep.projects[0].value_per_unit == pytest.approx(expected)


def test_capacity_profile_array_of_planning_json(
    gen_case: Callable[..., Path],
) -> None:
    rdir = gen_case()
    path = rdir / "planning.json"
    planning = json.loads(path.read_text(encoding="utf-8"))
    planning["system"]["capacity_profile_array"] = [
        {"uid": 2, "owner_kind": "battery", "owner": "g", "profile": 0.0},
        {"uid": 1, "owner_kind": "generator", "owner": "g", "profile": [[[0.5]]]},
    ]
    path.write_text(json.dumps(planning), encoding="utf-8")
    assert compute_recovery(rdir).projects[0].value_per_unit == pytest.approx(10.0)


def test_profile_from_file_assumes_one(
    gen_case: Callable[..., Path], caplog: pytest.LogCaptureFixture
) -> None:
    rep = compute_recovery(_with_profile(gen_case(), "profile_file"))
    assert rep.projects[0].value_per_unit == pytest.approx(20.0)
    assert "not inline" in caplog.text
    # warned once per generator, not once per period
    assert caplog.text.count("not inline") == 1


def test_profile_referenced_by_uid(gen_case: Callable[..., Path]) -> None:
    rdir = _with_profile(gen_case(), [[[0.5, 1.0]]])
    path = rdir / "planning.json"
    planning = json.loads(path.read_text(encoding="utf-8"))
    gen = planning["system"]["generator_array"][0]
    planning["system"]["generator_profile_array"][0]["generator"] = gen["uid"]
    path.write_text(json.dumps(planning), encoding="utf-8")
    assert compute_recovery(rdir).projects[0].value_per_unit == pytest.approx(10.0)


def test_demand_project_social_value(tmp_path: Path) -> None:
    # Desal plant: 10 MW built, value 300 $/MWh, prices 100 / 50 over 2 h.
    planning = {
        "options": {"model_options": {"demand_fail_cost": 1000}},
        "simulation": sim([1, 1]),
        "system": {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "generator_array": [{"uid": 1, "name": "g", "bus": "b1"}],
            "demand_array": [
                {
                    "uid": 7,
                    "name": "desal",
                    "bus": "b1",
                    "lmax": 20,
                    "fcost": 300,
                    "capacity": 0,
                    "expcap": 10,
                    "expmod": 2,
                    "annual_capcost": 1_752_000.0,
                },
                {"uid": 8, "name": "city", "bus": "b1", "lmax": 5},
            ],
        },
    }
    rdir = write_case(
        tmp_path / "desal",
        planning,
        {
            "Bus/balance_dual": [(1, 1, 1, 1, 100.0), (1, 1, 2, 1, 50.0)],
            "Generator/generation_sol": [(1, 1, 1, 1, 15.0), (1, 1, 2, 1, 15.0)],
            "Demand/load_sol": [
                (1, 1, 1, 7, 10.0),
                (1, 1, 2, 7, 10.0),
                (1, 1, 1, 8, 5.0),
                (1, 1, 2, 8, 5.0),
            ],
            "Demand/capainst_sol": [(0, 1, 0, 7, 10.0)],
            "Demand/capacost_sol": [(0, 1, 0, 7, 2000.0)],
        },
    )
    rep = compute_recovery(rdir)
    (p,) = rep.projects
    assert p.kind == "Demand" and p.name == "desal" and p.new == 10.0
    assert p.revenue == pytest.approx(300.0 * 20)  # social value of service
    assert p.opex == pytest.approx(100.0 * 10 + 50.0 * 10)
    assert p.capex == pytest.approx(4000.0)
    assert p.profit == pytest.approx(6000.0 - 1500.0 - 4000.0)
    assert p.value_per_unit == pytest.approx(200.0 + 250.0)
    assert p.cost_per_unit == pytest.approx(400.0)  # 1.752 M$/yr · 2/8760
    assert rep.market.residual == pytest.approx(0.0)


def test_demand_value_falls_back_to_global_fail_cost(tmp_path: Path) -> None:
    planning = {
        "options": {"demand_fail_cost": 500},
        "simulation": sim([1]),
        "system": {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "demand_array": [
                {"uid": 1, "name": "d", "bus": "b1", "expcap": 1, "expmod": 5}
            ],
        },
    }
    rdir = write_case(
        tmp_path / "d",
        planning,
        {
            "Bus/balance_dual": [(1, 1, 1, 1, 100.0)],
            "Demand/load_sol": [(1, 1, 1, 1, 2.0)],
        },
    )
    (p,) = compute_recovery(rdir).projects
    assert p.revenue == pytest.approx(1000.0)
    planning["options"] = {}
    rdir = write_case(
        tmp_path / "d0",
        planning,
        {
            "Bus/balance_dual": [(1, 1, 1, 1, 100.0)],
            "Demand/load_sol": [(1, 1, 1, 1, 2.0)],
        },
    )
    (p0,) = compute_recovery(rdir).projects
    # no fail cost anywhere: gtopt's default 1000 $/MWh
    assert p0.revenue == pytest.approx(2000.0)


def _ely_case(tmp_path: Path, to_carrier: str = "hydrogen") -> Path:
    planning = {
        "options": {},
        "simulation": sim([1, 1]),
        "system": {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "hydrogen_node_array": [{"uid": 3, "name": "h2"}],
            "generator_array": [{"uid": 1, "name": "g", "bus": "b1"}],
            "carrier_converter_array": [
                {
                    "uid": 1,
                    "name": "ely",
                    "from_carrier": "electric",
                    "to_carrier": to_carrier,
                    "from_node": "b1",
                    "to_node": "h2" if to_carrier != "electric" else "b1",
                    "efficiency": 0.5,
                    "ocost": 2,
                    "capacity": 0,
                    "expcap": 10,
                    "expmod": 2,
                }
            ],
        },
    }
    return write_case(
        tmp_path / f"ely_{to_carrier}",
        planning,
        {
            "Bus/balance_dual": [(1, 1, 1, 1, 10.0), (1, 1, 2, 1, 60.0)],
            "HydrogenNode/balance_dual": [(1, 1, 1, 3, 100.0), (1, 1, 2, 3, 100.0)],
            "Generator/generation_sol": [(1, 1, 1, 1, 10.0)],
            "CarrierConverter/input_sol": [(1, 1, 1, 1, 10.0)],
            "CarrierConverter/capainst_sol": [(0, 1, 0, 1, 10.0)],
            "CarrierConverter/capacost_sol": [(0, 1, 0, 1, 19.0)],
        },
    )


def test_carrier_converter_project(tmp_path: Path) -> None:
    rep = compute_recovery(_ely_case(tmp_path))
    (p,) = rep.projects
    assert p.kind == "CarrierConverter" and p.name == "ely"
    assert p.revenue == pytest.approx(100.0 * 0.5 * 10)  # H2 sold
    assert p.opex == pytest.approx((10.0 + 2.0) * 10)  # power + ocost
    assert p.capex == pytest.approx(19.0 * 2)
    # margin per MW: block 1: 50 − 10 − 2 = 38; block 2: 50 − 60 − 2 < 0
    assert p.value_per_unit == pytest.approx(38.0)
    # market: the generator's 100 $ = the electrolyser's 100 $ purchase
    assert rep.market.converter_net_revenue == pytest.approx(-100.0)
    assert rep.market.residual == pytest.approx(0.0)


def test_carrier_converter_unknown_carrier(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (p,) = compute_recovery(_ely_case(tmp_path, "water")).projects
    assert p.revenue == pytest.approx(0.0)
    assert "no node prices" in caplog.text
    # electric -> electric converter sells at the bus price
    rep = compute_recovery(_ely_case(tmp_path, "electric"))
    assert rep.market.converter_net_revenue == pytest.approx(-100.0 + 50.0)


# ── price decomposition: variable cost + capacity rent (+ failure) ─────


def _peak_case(tmp_path: Path, fail: float = 0.0, extra: Any = None) -> Path:
    """One bus, two 1-h blocks; ``g`` (10 $/MWh, 50 MW built) and ``old``.

    Block 1: both at capacity, price 40 → set cost 30 (``old``), capacity
    rent 10.  Block 2: ``old`` part-loaded at 30 sets the price → rent 0.
    """
    system: dict[str, Any] = {
        "bus_array": [{"uid": 1, "name": "b1"}],
        "generator_array": [
            {
                "uid": 1,
                "name": "g",
                "bus": "b1",
                "gcost": 10,
                "capacity": 0,
                "expcap": 10,
                "expmod": 10,
            },
            {"uid": 2, "name": "old", "bus": "b1", "gcost": 30, "capacity": 20},
        ],
        "demand_array": [{"uid": 1, "name": "d", "bus": "b1", "lmax": 100}],
    }
    streams: dict[str, Any] = {
        "Bus/balance_dual": [(1, 1, 1, 1, 40.0), (1, 1, 2, 1, 30.0)],
        "Generator/generation_sol": [
            (1, 1, 1, 1, 50.0),
            (1, 1, 2, 1, 50.0),
            (1, 1, 1, 2, 20.0),
            (1, 1, 2, 2, 5.0),
        ],
        "Generator/capainst_sol": [(0, 1, 0, 1, 50.0)],
        "Generator/capacost_sol": [(0, 1, 0, 1, 0.0)],
        "Demand/load_sol": [(1, 1, 1, 1, 70.0), (1, 1, 2, 1, 55.0)],
        "Demand/fail_sol": [(1, 1, 1, 1, fail)],
    }
    if extra is not None:
        extra(system, streams)
    return write_case(
        tmp_path / "peak",
        {"options": {}, "simulation": sim([1, 1]), "system": system},
        streams,
    )


def test_capacity_rent_split(tmp_path: Path) -> None:
    rep = compute_recovery(_peak_case(tmp_path), price_blocks=True)
    (p,) = rep.projects
    # margin: (40 − 10)·50 + (30 − 10)·50 = 2500
    assert p.margin == pytest.approx(2500.0)
    assert p.capacity_rent == pytest.approx(10.0 * 50)
    assert p.failure_rent == pytest.approx(0.0)
    assert p.variable_margin == pytest.approx(2000.0)
    b1, b2 = rep.blocks
    assert (b1["price"], b1["set_cost"], b1["capacity_rent"]) == pytest.approx(
        (40.0, 30.0, 10.0)
    )
    assert b2["capacity_rent"] == pytest.approx(0.0)
    assert rep.total_failure_rent == pytest.approx(0.0)
    assert p.to_dict()["variable_margin"] == pytest.approx(2000.0)


def test_failure_rent_is_flagged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rep = compute_recovery(_peak_case(tmp_path, fail=30.0))
    (p,) = rep.projects
    assert p.capacity_rent == pytest.approx(0.0)
    assert p.failure_rent == pytest.approx(10.0 * 50)
    assert rep.total_failure_rent == pytest.approx(500.0)
    assert "failure prices" in caplog.text
    assert not rep.blocks


def test_flexible_setters_cancel_the_rent(tmp_path: Path) -> None:
    def desal(system: dict[str, Any], streams: dict[str, Any]) -> None:
        system["demand_array"].append(
            {
                "uid": 2,
                "name": "desal",
                "bus": "b1",
                "fcost": 40,
                "capacity": 0,
                "expcap": 10,
                "expmod": 5,
            }
        )
        streams["Demand/load_sol"].append((1, 1, 1, 2, 5.0))
        streams["Demand/capainst_sol"] = [(0, 1, 0, 2, 10.0)]

    (g, _d) = compute_recovery(_peak_case(tmp_path, extra=desal)).projects
    assert g.capacity_rent == pytest.approx(0.0)


def test_storage_and_converter_setters(tmp_path: Path) -> None:
    def storage(system: dict[str, Any], streams: dict[str, Any]) -> None:
        system["battery_array"] = [{"uid": 1, "name": "bess", "bus": "b1"}]
        system["converter_array"] = [{"uid": 1, "name": "bess_conv", "capacity": 10}]
        system["generator_array"].append(
            {"uid": 3, "name": "bess_gen", "bus": "b1", "gcost": 0}
        )
        system["demand_array"].append({"uid": 3, "name": "bess_dem", "bus": "b1"})
        streams["Generator/generation_sol"].append((1, 1, 1, 3, 4.0))
        streams["Demand/load_sol"].append((1, 1, 2, 3, 4.0))

    split = compute_recovery(_peak_case(tmp_path, extra=storage), price_blocks=True)
    assert [b["setter"] for b in split.blocks] == ["storage bess", "storage bess"]

    def ely(system: dict[str, Any], streams: dict[str, Any]) -> None:
        system["carrier_converter_array"] = [
            {
                "uid": 1,
                "name": "ely",
                "from_carrier": "electric",
                "to_carrier": "hydrogen",
                "from_node": "b1",
                "capacity": 10,
            },
            {"uid": 2, "name": "fc", "from_carrier": "hydrogen"},
        ]
        streams["CarrierConverter/input_sol"] = [(1, 1, 1, 1, 3.0)]

    rep = compute_recovery(_peak_case(tmp_path / "e", extra=ely), price_blocks=True)
    assert rep.blocks[0]["setter"] == "converter ely"
    assert rep.projects[0].capacity_rent == pytest.approx(0.0)


def test_installed_from_pmax_and_unbounded(tmp_path: Path) -> None:
    def no_cap(system: dict[str, Any], streams: dict[str, Any]) -> None:
        system["generator_array"][1].pop("capacity")
        system["generator_array"][1]["pmax"] = 25
        system["generator_array"].append({"uid": 4, "name": "free", "bus": "b1"})

    rep = compute_recovery(_peak_case(tmp_path, extra=no_cap), price_blocks=True)
    assert rep.blocks[0]["capacity_rent"] == pytest.approx(10.0)


def test_gcost_fallback_in_split(tmp_path: Path) -> None:
    def other_bus(system: dict[str, Any], streams: dict[str, Any]) -> None:
        system["bus_array"].append({"uid": 2, "name": "b2"})
        streams["Bus/balance_dual"] += [(1, 1, 1, 2, 99.0), (1, 1, 2, 2, 99.0)]

    rep = compute_recovery(_peak_case(tmp_path, extra=other_bus), price_blocks=True)
    b2 = [b for b in rep.blocks if b["bus"] == 2]
    # no running unit in bus 2's zone: the price is its own set cost
    assert all(b["capacity_rent"] == pytest.approx(0.0) for b in b2)


def test_fixed_om_is_an_opex_component(
    gen_case: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """capacost = annuity (500 $/h) + fixed O&M (500 $/h) of 50 MW built."""
    rdir = gen_case(capacost_rate=1000.0, fixed_om_cost=87600.0)
    write_stream(rdir, "Generator/expmod_sol", [(0, 1, 0, 1, 5.0)])
    (p,) = compute_recovery(rdir).projects
    assert p.fixed_om == pytest.approx(50.0 * 10.0 * 2.0)  # 10 $/MW-h · 2 h
    assert p.capex == pytest.approx(1000.0)  # the annuity only
    assert p.total_opex == pytest.approx(p.opex + 1000.0)
    assert p.profit == pytest.approx(2000.0 - 1000.0 - 1000.0 - 1000.0)
    assert p.cost_per_unit == pytest.approx((87600.0 + 87600.0) * 2.0 / 8760.0)
    assert p.to_dict()["total_opex"] == pytest.approx(2000.0)
    print_report(compute_recovery(rdir))
    assert "fixed O&M 1,000" in capsys.readouterr().out


def test_battery_power_fixed_om(tmp_path: Path) -> None:
    planning = {
        "options": {},
        "simulation": sim([1]),
        "system": {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "battery_array": [
                {
                    "uid": 1,
                    "name": "bess",
                    "bus": "b1",
                    "capacity": 0,
                    "expcap": 1,
                    "expmod": 10,
                    "power_fixed_om_cost": 8760.0,
                }
            ],
            "converter_array": [
                {
                    "uid": 1,
                    "name": "bess_conv",
                    "capacity": 0,
                    "expcap": 2,
                    "expmod": 10,
                }
            ],
        },
    }
    rdir = write_case(
        tmp_path / "b",
        planning,
        {
            "Bus/balance_dual": [(1, 1, 1, 1, 10.0)],
            "Converter/expmod_sol": [(0, 1, 0, 1, 3.0)],
            "Converter/capainst_sol": [(0, 1, 0, 1, 6.0)],
            "Converter/capacost_sol": [(0, 1, 0, 1, 6.0)],
        },
    )
    (p,) = compute_recovery(rdir).projects
    assert p.fixed_om == pytest.approx(6.0)  # 6 MW · 1 $/MW-h · 1 h
    assert p.power_capex == pytest.approx(0.0)
