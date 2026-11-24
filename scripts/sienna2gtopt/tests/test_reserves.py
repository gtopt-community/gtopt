"""Unit tests for the ``reserves`` variant (PowerSimulations reserve goldens).

The builder reads only the ``system_data`` block that
``tools/sienna_solve_golden.jl`` dumps into the golden, so every test
here runs on a small synthetic golden: no Julia, no Sienna data bundle.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from sienna2gtopt import VARIANTS
from sienna2gtopt.__main__ import main
from sienna2gtopt._reserves import (
    build_reserves,
    objective_offset,
    ordc_steps,
    reserve_elements,
)

HOURS = 3


def _cost(linear: float, **extra: Any) -> dict[str, Any]:
    cost = {
        "linear": linear,
        "quadratic": 0.0,
        "constant": 0.0,
        "vom": 0.0,
        "power_units": "UnitSystem.NATURAL_UNITS = 2",
    }
    cost.update(extra)
    return cost


def _golden() -> dict[str, Any]:
    """Two thermal units, one wind unit, one firm + one interruptible load."""
    services = {
        "Up1": {
            "type": "VariableReserve{ReserveUp}",
            "direction": "up",
            "available": True,
            "contributing_devices": ["G2", "G1"],
            "max_output_fraction": 1.0,
            "max_participation_factor": 1.0,
            "reserve_cost_per_mw": 1e-4,
            "modelled": True,
        },
        "Dn1": {
            "type": "VariableReserve{ReserveDown}",
            "direction": "down",
            "available": True,
            "contributing_devices": ["G2"],
            "max_output_fraction": 0.5,
            "max_participation_factor": 1.0,
            "reserve_cost_per_mw": 1e-4,
            "modelled": True,
        },
        "ORDC1": {
            "type": "ReserveDemandCurve{ReserveUp}",
            "direction": "up",
            "available": True,
            "contributing_devices": ["G1", "G2", "Ghost"],
            "max_output_fraction": None,
            "max_participation_factor": 1.0,
            "reserve_cost_per_mw": 1e-4,
            "modelled": True,
            "ordc": {
                "breakpoints_mw": [0.0, 10.0, 10.0, 30.0, 40.0],
                "slopes_per_mwh": [100.0, 50.0, 5.0, 0.0],
                "power_units": "UnitSystem.NATURAL_UNITS = 2",
            },
        },
        "Off": {"type": "VariableReserve{ReserveUp}", "available": False},
    }
    thermal = {
        "G1": {
            "bus": 1,
            "available": True,
            "pmin_mw": 10.0,
            "pmax_mw": 100.0,
            "status": True,
            "variable_cost": _cost(10.0),
            "fixed_cost": 2.0,
            "start_up_cost": 4.0,
            "shut_down_cost": 1.0,
        },
        "G2": {
            "bus": 2,
            "available": True,
            "pmin_mw": 20.0,
            "pmax_mw": 50.0,
            "status": False,
            "variable_cost": _cost(30.0, constant=3.0, vom=1.0),
            "fixed_cost": 0.0,
            "start_up_cost": None,
            "shut_down_cost": 0.0,
        },
        "Gx": {
            "bus": 2,
            "available": False,
            "pmin_mw": 0.0,
            "pmax_mw": 9.0,
            "variable_cost": _cost(1.0),
        },
    }
    return {
        "slug": "synthetic",
        "horizon_hours": HOURS,
        "objective": 1000.0,
        "reserve_requirement_mw": {"Up1": [1.0, 2.0, 3.0, 4.0], "Dn1": [5.0, 5.0, 5.0]},
        "load_on": {"IL": [1.0, 1.0, 0.0]},
        "system_data": {
            "base_power": 100.0,
            "thermal": thermal,
            "renewable": {
                "W1": {
                    "bus": 3,
                    "rating_mw": 80.0,
                    "variable_cost": _cost(0.22),
                }
            },
            "load": {"L1": {"bus": 1}},
            "interruptible_load": {
                "IL": {"bus": 2, "variable_cost": _cost(1.5), "fixed_cost": 24.0}
            },
            "timeseries_mw": {
                "PowerLoad": {"L1": [-50.0, -60.0, -70.0]},
                "RenewableDispatch": {"W1": [10.0, 20.0, 30.0]},
                "InterruptiblePowerLoad": {"IL": [-4.0, -4.0, -4.0]},
            },
            "services": services,
        },
    }


def _by_name(array: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {e["name"]: e for e in array}


def test_registered_variant() -> None:
    assert VARIANTS["reserves"] is build_reserves


def test_units_loads_and_commitments() -> None:
    payload = build_reserves(_golden())
    system = payload["system"]
    gens = _by_name(system["generator_array"])
    assert set(gens) == {"G1", "G2", "W1"}  # Gx unavailable
    # pmin binds only while committed: Commitment.pmin, never Generator.pmin
    assert gens["G1"]["pmin"] == 0.0 and gens["G1"]["pmax"] == 100.0
    assert gens["G2"]["gcost"] == pytest.approx(31.0)  # linear + VOM
    assert gens["W1"]["pmax"] == [[10.0, 20.0, 30.0]]
    assert gens["W1"]["capacity"] == 80.0
    uc = _by_name(system["commitment_array"])
    assert uc["G1_uc"]["pmin"] == 10.0
    assert uc["G1_uc"]["initial_status"] == 1.0
    assert uc["G1_uc"]["noload_cost"] == 2.0
    assert uc["G2_uc"]["initial_status"] == 0.0
    assert uc["G2_uc"]["startup_cost"] == 0.0  # None → 0
    assert uc["G2_uc"]["noload_cost"] == 3.0  # cost-curve constant term
    dem = _by_name(system["demand_array"])
    assert dem["L1"]["lmax"] == [[50.0, 60.0, 70.0]]
    assert "fcost" not in dem["L1"]
    assert dem["IL"]["fcost"] == 1.5 and dem["IL"]["lmax"] == [[4.0, 4.0, 4.0]]
    assert [b["uid"] for b in system["bus_array"]] == [1, 2, 3]
    stage = payload["simulation"]["stage_array"][0]
    assert stage["chronological"] is True and stage["count_block"] == HOURS
    opts = payload["options"]["model_options"]
    assert opts["use_single_bus"] is True and opts["scale_objective"] == 1.0


def test_range_reserves_and_stacked_ordc() -> None:
    system = build_reserves(_golden())["system"]
    zones = _by_name(system["reserve_zone_array"])
    # Zero-width and zero-price ORDC steps are dropped.
    assert set(zones) == {"ORDC1_s1", "ORDC1_s2", "Up1", "Dn1"}
    assert zones["ORDC1_s1"] | {"uid": 0} == {
        "uid": 0,
        "name": "ORDC1_s1",
        "urreq": 10.0,
        "urcost": 100.0,
    }
    assert zones["ORDC1_s2"]["urreq"] == 20.0 and zones["ORDC1_s2"]["urcost"] == 5.0
    assert zones["Up1"]["urreq"] == [[1.0, 2.0, 3.0]] and "urcost" not in zones["Up1"]
    assert zones["Dn1"]["drreq"] == [[5.0, 5.0, 5.0]]
    provs = _by_name(system["reserve_provision_array"])
    # Ghost is not a generator; providers sorted by name.
    assert "Ghost_ORDC1_s1" not in provs
    assert provs["G1_ORDC1_s1"]["urmax"] == 100.0
    assert "urcost" not in provs["G1_ORDC1_s1"]
    assert provs["G1_Up1"]["urcost"] == 1e-4
    assert provs["G2_Dn1"]["drmax"] == 25.0  # 0.5 × pmax
    assert provs["G2_Dn1"]["dr_provision_factor"] == 1.0
    assert all(p["shared_headroom"] for p in provs.values())
    uids = [p["uid"] for p in system["reserve_provision_array"]]
    assert uids == list(range(1, len(uids) + 1))
    assert provs["G1_Up1"]["reserve_zones"] == [zones["Up1"]["uid"]]


def test_ordc_omit_and_auto() -> None:
    golden = _golden()
    names = {
        z["name"]
        for z in build_reserves(golden, ordc="omit")["system"]["reserve_zone_array"]
    }
    assert names == {"Up1", "Dn1"}
    golden["system_data"]["services"]["ORDC1"]["modelled"] = False
    auto = build_reserves(golden)["system"]["reserve_zone_array"]
    assert {z["name"] for z in auto} == {"Up1", "Dn1"}
    forced = build_reserves(golden, ordc="stacked")["system"]["reserve_zone_array"]
    assert "ORDC1_s1" in {z["name"] for z in forced}
    with pytest.raises(ValueError, match="ordc mode"):
        build_reserves(golden, ordc="bogus")


def test_participation_factor_caps_provision() -> None:
    golden = _golden()
    golden["system_data"]["services"]["Up1"]["max_participation_factor"] = 0.5
    provs = _by_name(build_reserves(golden)["system"]["reserve_provision_array"])
    assert provs["G1_Up1"]["urmax"] == [[0.5, 1.0, 1.5]]


def test_no_reserves_no_arrays() -> None:
    golden = _golden()
    golden["system_data"]["services"] = {}
    system = build_reserves(golden)["system"]
    assert "reserve_zone_array" not in system
    assert "reserve_provision_array" not in system


def test_objective_offset() -> None:
    golden = _golden()
    # ORDC: 3 h × (10·100 + 20·5); IL: 1.5 × 12 MWh; on credit 24 × 2 h.
    expected = 3 * (1000.0 + 100.0) + 1.5 * 12.0 + 24.0 * 2
    assert objective_offset(golden) == pytest.approx(expected)
    assert objective_offset(golden, ordc="omit") == pytest.approx(18.0 + 48.0)
    golden["system_data"]["services"]["ORDC1"]["available"] = False
    assert objective_offset(golden) == pytest.approx(18.0 + 48.0)


def test_system_base_costs_and_ordc_steps() -> None:
    golden = _golden()
    unit = golden["system_data"]["thermal"]["G1"]
    unit["variable_cost"] = _cost(1000.0, power_units="SYSTEM_BASE")
    gens = _by_name(build_reserves(golden)["system"]["generator_array"])
    assert gens["G1"]["gcost"] == pytest.approx(10.0)
    svc = golden["system_data"]["services"]["ORDC1"]
    assert ordc_steps(svc) == [(10.0, 100.0), (20.0, 5.0)]


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda g: g.pop("system_data"), "system_data"),
        (lambda g: g.update(horizon_hours=0), "horizon_hours"),
        (
            lambda g: g["system_data"]["thermal"]["G1"]["variable_cost"].update(
                quadratic=0.1
            ),
            "quadratic",
        ),
        (
            lambda g: g["system_data"]["thermal"]["G1"]["variable_cost"].update(
                power_units="DEVICE_BASE"
            ),
            "power_units",
        ),
        (
            lambda g: g["system_data"]["timeseries_mw"].pop("PowerLoad"),
            "PowerLoad",
        ),
        (
            lambda g: g["system_data"]["timeseries_mw"]["PowerLoad"].update(L1=[1.0]),
            "steps",
        ),
        (lambda g: g["reserve_requirement_mw"].pop("Up1"), "requirement"),
        (lambda g: g["reserve_requirement_mw"].update(Up1=[1.0]), "steps"),
        (
            lambda g: g["system_data"]["services"]["ORDC1"].pop("ordc"),
            "ordc",
        ),
        (
            lambda g: g["system_data"]["services"]["ORDC1"]["ordc"].update(
                power_units="SYSTEM_BASE"
            ),
            "natural units",
        ),
        (
            lambda g: g["system_data"]["services"]["ORDC1"]["ordc"].update(
                slopes_per_mwh=[1.0]
            ),
            "breakpoint",
        ),
    ],
)
def test_invalid_goldens(mutate: Any, match: str) -> None:
    golden = copy.deepcopy(_golden())
    mutate(golden)
    with pytest.raises(ValueError, match=match):
        build_reserves(golden)


def test_reserve_elements_direct() -> None:
    zones, provs = reserve_elements({}, {}, {}, 1)
    assert not zones and not provs


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    golden_path = tmp_path / "g.json"
    golden_path.write_text(json.dumps(_golden()))
    out = tmp_path / "out.json"
    assert main(["reserves", "--golden", str(golden_path), "-o", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert len(payload["system"]["reserve_zone_array"]) == 4
    assert "reserve_zones=4" in capsys.readouterr().out
    out2 = tmp_path / "out2.json"
    argv = ["reserves", "--golden", str(golden_path), "--ordc", "omit", "-o", str(out2)]
    assert main(argv) == 0
    assert len(json.loads(out2.read_text())["system"]["reserve_zone_array"]) == 2


def test_cli_default_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    golden_path = tmp_path / "g.json"
    golden_path.write_text(json.dumps(_golden()))
    monkeypatch.chdir(tmp_path)
    assert main(["reserves", "--golden", str(golden_path)]) == 0
    assert (tmp_path / "synthetic.json").is_file()
    capsys.readouterr()


def test_cli_requires_golden(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["reserves"])
    assert "--golden" in capsys.readouterr().err
