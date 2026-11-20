"""Unit tests for the gtopt JSON writer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from psse2gtopt.gtopt_writer import (
    _STAR_BUS_OFFSET,
    build_planning,
    write_planning,
)
from psse2gtopt.raw_parser import parse_raw


@pytest.fixture(scope="module")
def synthetic_planning(synthetic_raw: Path) -> dict:
    return build_planning(parse_raw(synthetic_raw), name="synthetic")


def test_top_level_schema(synthetic_planning: dict) -> None:
    assert set(synthetic_planning) == {"options", "simulation", "system"}
    mo = synthetic_planning["options"]["model_options"]
    assert mo["use_single_bus"] is False
    assert mo["use_kirchhoff"] is True
    assert mo["demand_fail_cost"] == pytest.approx(1000.0)


def test_simulation_single_snapshot(synthetic_planning: dict) -> None:
    sim = synthetic_planning["simulation"]
    assert len(sim["block_array"]) == 1
    assert len(sim["stage_array"]) == 1
    assert len(sim["scenario_array"]) == 1


def test_isolated_bus_dropped(synthetic_planning: dict) -> None:
    names = {b["name"] for b in synthetic_planning["system"]["bus_array"]}
    # Bus 5 is isolated (IDE=4) and must not appear; a star bus is added.
    assert "b5" not in names
    assert "b1" in names and "b2" in names and "b3" in names
    assert any(
        b["uid"] >= _STAR_BUS_OFFSET for b in synthetic_planning["system"]["bus_array"]
    )


def test_generator_filtering(synthetic_planning: dict) -> None:
    gens = synthetic_planning["system"]["generator_array"]
    names = {g["name"] for g in gens}
    # Two in-service generators with pmax>0; OOS + condenser dropped.
    assert len(gens) == 2
    assert "g1_1" in names and "g2_1" in names
    g1 = next(g for g in gens if g["name"] == "g1_1")
    assert g1["pmax"] == pytest.approx(200.0)
    assert g1["gcost"] == pytest.approx(10.0)


def test_demand_filtering(synthetic_planning: dict) -> None:
    demands = synthetic_planning["system"]["demand_array"]
    # In-service positive loads on live buses: bus3=100, bus4=50, bus6=20.
    assert len(demands) == 3
    by_bus = {d["bus"]: d["lmax"][0][0] for d in demands}
    assert by_bus["b3"] == pytest.approx(100.0)
    assert by_bus["b4"] == pytest.approx(50.0)
    assert by_bus["b6"] == pytest.approx(20.0)


def test_three_winding_star_expansion(synthetic_planning: dict) -> None:
    lines = synthetic_planning["system"]["line_array"]
    star_lines = [line for line in lines if line["name"].startswith("t1_3_6_")]
    assert len(star_lines) == 3
    # Star reactances: Z1=0.15 (bus1), Z3=0.05 (bus3), Z6=0.25 (bus6).
    by_a = {line["bus_a"]: line["reactance"] for line in star_lines}
    assert by_a["b1"] == pytest.approx(0.15)
    assert by_a["b3"] == pytest.approx(0.05)
    assert by_a["b6"] == pytest.approx(0.25)
    # All three connect to the same star bus.
    star_buses = {line["bus_b"] for line in star_lines}
    assert len(star_buses) == 1


def test_zero_reactance_floored(synthetic_planning: dict) -> None:
    lines = synthetic_planning["system"]["line_array"]
    tie = next(line for line in lines if line["name"] == "l1_2_3")
    assert abs(tie["reactance"]) == pytest.approx(1.0e-5)


def test_no_dangling_references(synthetic_planning: dict) -> None:
    system = synthetic_planning["system"]
    names = {b["name"] for b in system["bus_array"]}
    for line in system["line_array"]:
        assert line["bus_a"] in names
        assert line["bus_b"] in names
    for gen in system["generator_array"]:
        assert gen["bus"] in names
    for demand in system["demand_array"]:
        assert demand["bus"] in names


def test_single_bus_mode(synthetic_raw: Path) -> None:
    planning = build_planning(parse_raw(synthetic_raw), name="cp", single_bus=True)
    assert planning["options"]["model_options"]["use_single_bus"] is True
    assert planning["options"]["model_options"]["use_kirchhoff"] is False
    assert "line_array" not in planning["system"]


def test_custom_gcost(synthetic_raw: Path) -> None:
    planning = build_planning(parse_raw(synthetic_raw), name="x", gcost=42.5)
    for gen in planning["system"]["generator_array"]:
        assert gen["gcost"] == pytest.approx(42.5)


def test_rating_set_selection(synthetic_raw: Path) -> None:
    case = parse_raw(synthetic_raw)
    tmax = {}
    for rs in ("A", "B", "C"):
        planning = build_planning(case, name="r", rating_set=rs)
        line = next(
            line
            for line in planning["system"]["line_array"]
            if line["name"] == "l1_2_1"
        )
        tmax[rs] = line["tmax_ab"]
    assert tmax["A"] == pytest.approx(200.0)
    assert tmax["B"] == pytest.approx(250.0)
    assert tmax["C"] == pytest.approx(300.0)


def test_nomenclatura_names(synthetic_raw: Path) -> None:
    case = parse_raw(synthetic_raw)
    codes = {"GEN": "Generador", "SLACK": "Referencia", "LOAD138": "CargaUno"}
    planning = build_planning(case, name="n", nomenclatura=codes)
    system = planning["system"]
    names = {b["name"] for b in system["bus_array"]}
    assert "Generador-PV" in names  # GEN-PV -> Generador-PV
    assert "Referencia" in names  # SLACK -> Referencia
    # References stay consistent with the renamed buses.
    bus_names = names
    for line in system["line_array"]:
        assert line["bus_a"] in bus_names and line["bus_b"] in bus_names
    for gen in system["generator_array"]:
        assert gen["bus"] in bus_names


def test_ldm_merit_costs(synthetic_raw: Path) -> None:
    case = parse_raw(synthetic_raw)
    # Bus 2 is "GEN-PV"; rank it first, leave bus 1 ("SLACK") unmatched.
    planning = build_planning(
        case, name="m", gcost=10.0, gcost_step=1.0, ldm_order=["GEN-PV"]
    )
    gens = {g["name"]: g for g in planning["system"]["generator_array"]}
    assert gens["g2_1"]["gcost"] == pytest.approx(10.0)  # rank 0
    # Unmatched generator is placed after the whole merit list.
    assert gens["g1_1"]["gcost"] == pytest.approx(11.0)


def test_write_planning_roundtrip(synthetic_planning: dict, tmp_path: Path) -> None:
    out = tmp_path / "out" / "plan.json"
    written = write_planning(synthetic_planning, out)
    assert written == out
    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded["system"]["name"] == "synthetic"
    assert len(reloaded["system"]["bus_array"]) == len(
        synthetic_planning["system"]["bus_array"]
    )


_ZIP_RAW = """\
0,   100.00, 32, 0, 1, 60.00     / ZIP-load fixture
ZIP LOADS: CONSTANT-CURRENT AND NEGATIVE LOADS
SECOND TITLE
     1,'A           ', 500.0000,3,   1,   1,   1,1.05000,   0.0000
     2,'B           ', 500.0000,1,   1,   1,   1,0.90000,  -5.0000
 0 /End of Bus data, Begin Load data
     2,'1 ',1,   1,   1,    10.000,     0.000,   100.000,     0.000,    50.000,     0.000,   1,1
     1,'1 ',1,   1,   1,     0.000,     0.000,  -200.000,     0.000,     0.000,     0.000,   1,1
     2,'2 ',0,   1,   1,     0.000,     0.000,   999.000,     0.000,     0.000,     0.000,   1,1
 0 /End of Load data, Begin Fixed shunt data
 0 /End of Fixed shunt data, Begin Generator data
     1,'1 ',   100.000,     0.000,   100.000,  -100.000,1.05000,     0,   500.000, 0.00000E+0, 2.00000E-1, 0.00000E+0, 0.00000E+0,1.00000,1,  100.0,   500.000,     0.000,   1,1.0000
 0 /End of Generator data, Begin Branch data
     1,     2,'1 ', 1.00000E-3, 1.00000E-2, 0.00000E+0,    0.00,    0.00,    0.00,  0.00000,  0.00000,  0.00000,  0.00000,1,1,   0.00,   1,1.0000
 0 /End of Branch data, Begin Transformer data
 0 /End of Transformer data
"""


@pytest.fixture(scope="module")
def zip_planning(tmp_path_factory: pytest.TempPathFactory) -> dict:
    raw = tmp_path_factory.mktemp("zip") / "zip.raw"
    raw.write_text(_ZIP_RAW, encoding="latin-1")
    return build_planning(parse_raw(raw), name="zip")


def test_zip_load_at_solved_voltage(zip_planning: dict) -> None:
    """PL + IP*VM + YP*VM^2 at the bus VM (constant-current WECC-240 style)."""
    demands = zip_planning["system"]["demand_array"]
    assert [d["name"] for d in demands] == ["d2_1"]
    vm = 0.9
    expected = 10.0 + 100.0 * vm + 50.0 * vm * vm
    assert demands[0]["lmax"][0][0] == pytest.approx(expected)


def test_negative_load_becomes_fixed_injection(zip_planning: dict) -> None:
    """A negative (IP-only) load is kept as a fixed zero-cost injection."""
    gens = zip_planning["system"]["generator_array"]
    inj = [g for g in gens if g["name"].startswith("inj")]
    assert len(inj) == 1
    g = inj[0]
    assert g["name"] == "inj1_1"
    assert g["bus"] == "b1"
    assert g["pmin"] == pytest.approx(200.0 * 1.05)
    assert g["pmax"] == pytest.approx(200.0 * 1.05)
    assert g["gcost"] == pytest.approx(0.0)
    uids = [x["uid"] for x in gens]
    assert len(set(uids)) == len(uids)
