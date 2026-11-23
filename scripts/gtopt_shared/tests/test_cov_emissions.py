# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage for :mod:`gtopt_shared.emissions` (loader, apply, overrides)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from gtopt_shared import emissions as em


def _write_defaults(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "defaults.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


_DEFAULTS = {
    "description": "test",
    "units": {"co2": "t/GJ"},
    "fuels": [
        "not-a-dict",
        {
            "name": "natural_gas",
            "aliases": ["gas", "gn"],
            "co2_combustion": 0.056,
            "ch4_combustion": 1e-6,
            "heat_content": 48.0,
        },
        {"name": "lng", "co2_combustion": 0.06, "co2_upstream": 0.01},
        {"name": "geothermal"},
        {"name": "biomass", "n2o_upstream": 4e-6},
        # Alias collision: 'gas' now resolves to 'other_gas' (logged).
        {"name": "other_gas", "aliases": ["gas"], "co2_combustion": 0.05},
    ],
    "generator_overrides": {
        "_comment": "ignored",
        "bad": "not-a-dict",
        "COGEN_1": {
            "type": "thermal:cogen",
            "kind": "cogen",
            "expected_fuel_match": "Gas_X",
        },
        "GEO_1": {"type_tag": "geothermal", "expected_fuel_match": "None"},
        "PLAIN_1": {"kind": "other"},
    },
}


@pytest.fixture(name="defaults")
def fixture_defaults(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=em.__name__):
        d = em.load_emission_defaults(_write_defaults(tmp_path, _DEFAULTS))
    assert "collides" in caplog.text
    return d


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_loader_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        em.load_emission_defaults(tmp_path / "missing.json")
    with pytest.raises(ValueError, match="expected a JSON object"):
        em.load_emission_defaults(_write_defaults(tmp_path, [1, 2]))
    with pytest.raises(ValueError, match="'fuels' list"):
        em.load_emission_defaults(_write_defaults(tmp_path, {"fuels": {}}))
    with pytest.raises(ValueError, match="without a 'name'"):
        em.load_emission_defaults(_write_defaults(tmp_path, {"fuels": [{"x": 1}]}))
    # Non-dict overrides section is ignored.
    d = em.load_emission_defaults(
        _write_defaults(tmp_path, {"fuels": [], "generator_overrides": [1]})
    )
    assert not d.generator_overrides


def test_loader_bundled_default():
    d = em.load_emission_defaults()
    assert d.fuels


def test_lookup_paths(defaults):
    assert defaults.lookup("x", subtype_hint="lng").name == "lng"
    # Unresolvable hint falls through to the full-name match.
    assert defaults.lookup("natural_gas", subtype_hint="zzz").name == "natural_gas"
    assert defaults.lookup("Gas_Plant_A").name == "other_gas"
    assert defaults.lookup("_leading") is None
    assert defaults.lookup("nothing") is None
    ov = defaults.override_for_generator("GEO_1")
    assert ov is not None and ov.expected_fuel_match is None
    assert ov.type_tag == "geothermal"


def test_report_to_dict():
    rep = em.EmissionReport(source_path="p", fuels_factor_added=("a",))
    d = rep.to_dict()
    assert d["summary"]["factor_added"] == 1
    assert d["fuels_factor_added"] == ["a"]


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def test_pollutants_present_and_co2_alias():
    assert em._fuel_pollutants_present({"emission_factors": "x"}) == frozenset()
    fuel = {
        "emission_factors": [
            "junk",
            {"emission": ""},
            {"emission": "CO2", "combustion": 0.0, "upstream": 0.0},
            {"emission": "ch4", "upstream": 1.0},
        ]
    }
    assert em._fuel_pollutants_present(fuel) == frozenset({"ch4"})
    assert not em._fuel_has_co2_factor(fuel)


def test_inject_rows_replaces_placeholders():
    factor = em.EmissionFactor(
        name="f",
        aliases=(),
        co2_combustion=0.0,
        co2_upstream=0.02,
        n2o_combustion=0.001,
        heat_content=10.0,
    )
    fuel = {
        "emission_factors": [
            "junk",
            {"emission": "co2", "combustion": 0.0, "upstream": 0.0},
            {"emission": "ch4", "combustion": 5.0},
        ],
        "heat_content": 3.0,
    }
    injected = em._inject_emission_rows(fuel, factor, skip_existing=frozenset())
    assert injected == {"co2", "n2o"}
    assert fuel["emission_factors"] == [
        "junk",
        {"emission": "ch4", "combustion": 5.0},
        {"emission": "co2", "upstream": 0.02},
        {"emission": "n2o", "combustion": 0.001},
    ]
    assert fuel["heat_content"] == 3.0  # existing NCV preserved
    bare: dict = {"emission_factors": None}
    em._inject_co2_row(bare, factor)
    assert bare["heat_content"] == 10.0


# ---------------------------------------------------------------------------
# apply_emission_defaults
# ---------------------------------------------------------------------------


def test_apply_buckets_and_synthesis(defaults, tmp_path):
    planning = {
        "system": {
            "fuel_array": [
                "junk",
                {"name": ""},
                {"name": "unknown_fuel"},
                {
                    "name": "mystery_with_rows",
                    "emission_factors": [{"emission": "co2", "combustion": 1.0}],
                },
                {"name": "geothermal"},
                {
                    "name": "geothermal",
                    "emission_factors": [{"emission": "co2", "combustion": 0.1}],
                },
                {
                    "name": "lng",
                    "emission_factors": [{"emission": "co2", "combustion": 0.07}],
                },
                {"name": "Gas_Plant", "subtype": "natural_gas"},
                {"name": "biomass"},
            ],
            "emission_array": ["junk", {"uid": 4, "name": "co2"}],
            "emission_zone_array": [
                "junk",
                {"uid": 2, "emissions": ["x", {"emission": "co2"}, {"emission": ""}]},
            ],
            "generator_array": [
                "junk",
                {"name": ""},
                {"name": "NO_OVERRIDE"},
                {"name": "COGEN_1", "fuel": {"name": "Other_Fuel"}},
                {"name": "GEO_1", "fuel": "Geo_Steam"},
                {"name": "PLAIN_1"},
            ],
        }
    }
    report_path = tmp_path / "rep" / "report.json"
    rep = em.apply_emission_defaults(planning, defaults, report_path=report_path)
    system = planning["system"]
    assert rep.fuels_unknown == ("unknown_fuel",)
    assert rep.fuels_zero_emission == ("geothermal",)
    assert set(rep.fuels_factor_preserved) == {"mystery_with_rows", "geothermal", "lng"}
    assert set(rep.fuels_factor_added) == {"Gas_Plant", "biomass"}
    assert "subtype" not in system["fuel_array"][7]
    # co2 already present → only ch4 / n2o rows appended.
    assert [r["name"] for r in system["emission_array"][1:]] == ["co2", "ch4", "n2o"]
    assert system["emission_array"][-1]["uid"] == 6
    assert rep.emission_array_already_present
    assert not rep.emission_array_created
    # Zone already covers co2 → a new multi-pollutant zone for ch4 + n2o.
    zone = system["emission_zone_array"][-1]
    assert zone["name"] == "global_ghg"
    assert zone["uid"] == 3
    assert "price" not in zone
    gens = {g["name"]: g for g in system["generator_array"][2:]}
    # Mismatched expected fuel → untouched.
    assert gens["COGEN_1"]["fuel"] == {"name": "Other_Fuel"}
    # Unconditional drop + type stamp.
    assert "fuel" not in gens["GEO_1"]
    assert gens["GEO_1"]["type"] == "geothermal"
    assert "type" not in gens["PLAIN_1"]
    assert set(rep.generator_overrides_applied) == {"GEO_1", "PLAIN_1"}
    assert json.loads(report_path.read_text())["summary"]["unknown_fuels"] == 1


def test_apply_cogen_override_stamps_mode(defaults):
    planning = {
        "system": {
            "fuel_array": [],
            "generator_array": [{"name": "COGEN_1", "fuel": "Gas_X"}],
        }
    }
    rep = em.apply_emission_defaults(planning, defaults)
    gen = planning["system"]["generator_array"][0]
    assert gen["type"] == "thermal:cogen"
    assert gen["cogen_mode"] == "dispatched"
    assert "fuel" not in gen
    assert not rep.emission_zone_created


def _hydro_system() -> dict:
    return {
        "fuel_array": [{"name": "lng"}],
        "junction_array": [{"name": "J1"}, {"name": "J2"}],
        "reservoir_array": [
            {"name": "R1", "junction": "J1"},
            {"name": "R2", "junction": "J2", "water_emission_value": 9.0},
            {"name": ""},
            {"name": "R3", "junction": "nowhere"},
        ],
        "turbine_array": [
            {
                "name": "T1",
                "junction_a": "J1",
                "junction_b": "J2",
                "production_factor": 2.0,
            }
        ],
        "future_cost_array": ["junk", {"cuts_file": "cuts.csv"}, {"name": "x"}],
    }


def test_apply_only_emissions_mode(defaults):
    planning = {
        "options": {"boundary_cuts_file": "old.csv", "nested": [{"a": 1}]},
        "system": _hydro_system(),
    }
    rep = em.apply_emission_defaults(
        planning, defaults, only_emissions=True, carbon_price=50.0
    )
    assert rep.emission_zone_created
    assert rep.emission_array_created
    system = planning["system"]
    zone = system["emission_zone_array"][0]
    assert zone["name"] == "global_co2"
    assert zone["price"] == 50.0
    assert planning["options"]["model_options"]["objective_mode"] == "emissions"
    assert "boundary_cuts_file" not in planning["options"]
    assert "cuts_file" not in system["future_cost_array"][1]
    r1, r2, _, r3 = system["reservoir_array"]
    assert r1["water_emission_value"] == pytest.approx(2.0 * 0.5 * 0.95)
    assert r2["water_emission_value"] == 9.0
    assert "water_emission_value" not in r3


def test_apply_only_emissions_default_price_from_file(tmp_path):
    path = _write_defaults(tmp_path, {"fuels": [{"name": "lng", "co2_combustion": 1}]})
    planning = {
        "options": {"model_options": {"objective_mode": "cost"}},
        "system": {"fuel_array": [{"name": "lng"}]},
    }
    em.apply_emission_defaults_from_file(planning, path, only_emissions=True)
    assert planning["system"]["emission_zone_array"][0]["price"] == 35.0
    # Pre-existing objective_mode is respected.
    assert planning["options"]["model_options"]["objective_mode"] == "cost"


def test_drop_boundary_cuts_refs_counts():
    planning = {
        "a": {"boundary_cuts_file": "x"},
        "list": [{"boundary_cuts_file": "y"}],
        "system": {"future_cost_array": None},
    }
    assert em._drop_boundary_cuts_refs(planning) == 2
    assert em._drop_boundary_cuts_refs({"system": "x"}) == 0
