# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""GUI schemas vs gtopt's C++ JSON contract.

Reads the contract snapshot generated from ``include/gtopt/json``
(``scripts/gtopt_shared/data/json_contracts.json``, kept fresh by
``scripts/gtopt_shared/tests/test_json_contracts_sync.py``) and checks
that the GUI never offers a field gtopt's strict parser rejects, that
model options are written under ``options.model_options``, and that
system arrays without a GUI form survive a load/save round trip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from guiservice._schemas import (
    ELEMENT_SCHEMAS,
    ELEMENT_TO_ARRAY_KEY,
    MODEL_OPTION_KEYS,
    OPTIONS_SCHEMA,
    nest_model_options,
)
from guiservice.app import _build_case_json, _planning_json_to_case_data

SNAPSHOT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "gtopt_shared"
    / "data"
    / "json_contracts.json"
)


@pytest.fixture(name="types", scope="module")
def fixture_types() -> dict:
    if not SNAPSHOT.is_file():
        pytest.skip(f"contract snapshot not found at {SNAPSHOT}")
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))["types"]


def test_element_arrays_exist(types: dict) -> None:
    system_arrays = types["System"]["nested"]
    for elem, array_key in ELEMENT_TO_ARRAY_KEY.items():
        assert array_key in system_arrays, f"{elem}: unknown array {array_key}"


@pytest.mark.parametrize("elem", sorted(ELEMENT_SCHEMAS))
def test_element_fields_are_contract_fields(types: dict, elem: str) -> None:
    contract = types["System"]["nested"][ELEMENT_TO_ARRAY_KEY[elem]]
    allowed = set(types[contract]["fields"])
    fields = [f["name"] for f in ELEMENT_SCHEMAS[elem]["fields"]]
    stale = sorted(set(fields) - allowed)
    assert not stale, f"{elem} ({contract}): fields gtopt rejects: {stale}"


def test_options_partition(types: dict) -> None:
    top = set(types["PlanningOptions"]["fields"])
    model = set(types["ModelOptions"]["fields"])
    assert MODEL_OPTION_KEYS <= model
    for key in OPTIONS_SCHEMA:
        assert key in (model if key in MODEL_OPTION_KEYS else top), key


def test_build_case_json_nests_model_options() -> None:
    case = {
        "case_name": "c",
        "options": {
            "output_format": "csv",
            "use_single_bus": True,
            "reserve_fail_cost": 5.0,
            "model_options": {"scale_objective": 10.0},
        },
        "system": {},
    }
    opts = _build_case_json(case)["options"]
    assert opts == {
        "output_format": "csv",
        "model_options": {
            "scale_objective": 10.0,
            "use_single_bus": True,
            "reserve_shortage_cost": 5.0,
        },
    }
    assert nest_model_options({"method": "sddp"}) == {"method": "sddp"}


def test_unmapped_arrays_survive_round_trip() -> None:
    planning = {
        "options": {"model_options": {"use_single_bus": True}},
        "system": {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "future_cost_array": [{"uid": 1, "name": "fcf", "cuts_file": "c.csv"}],
            "pump_array": [{"uid": 1, "name": "p1"}],
        },
    }
    case = _planning_json_to_case_data(planning, "rt")
    assert case["system"]["future_cost"][0]["cuts_file"] == "c.csv"
    assert "pump_array" in case["system_extra"]
    out = _build_case_json(case)
    assert out["system"]["pump_array"] == planning["system"]["pump_array"]
    assert out["system"]["future_cost_array"][0]["cuts_file"] == "c.csv"
    assert out["options"] == planning["options"]


def test_junction_exposes_drain_fields(types: dict) -> None:
    fields = {f["name"]: f for f in ELEMENT_SCHEMAS["junction"]["fields"]}
    for name in ("drain_capacity", "drain_cost"):
        assert name in types["Junction"]["fields"]
        assert fields[name]["type"] == "number"
        assert fields[name]["required"] is False


def test_junction_drain_fields_round_trip() -> None:
    junction = {
        "uid": 1,
        "name": "sea",
        "drain": True,
        "drain_capacity": 250.0,
        "drain_cost": 0.5,
    }
    planning = {"options": {}, "system": {"junction_array": [junction]}}
    case = _planning_json_to_case_data(planning, "rt")
    assert case["system"]["junction"][0]["drain_cost"] == 0.5
    out = _build_case_json(case)
    assert out["system"]["junction_array"] == [junction]
