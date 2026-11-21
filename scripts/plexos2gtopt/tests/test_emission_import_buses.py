"""Tests for ``--emission-import-buses`` (import proxies kept out of totals)."""

from __future__ import annotations

from typing import Any

import pytest

from plexos2gtopt.plexos2gtopt import split_import_emission_zones


def _planning() -> dict[str, Any]:
    return {
        "system": {
            "bus_array": [
                {"uid": 1, "name": "SEM"},
                {"uid": 2, "name": "BETTA"},
                {"uid": 3, "name": "France"},
            ],
            "emission_zone_array": [
                {
                    "uid": 1,
                    "name": "global_ghg",
                    "emissions": [{"emission": "co2", "weight": 1.0}],
                }
            ],
        }
    }


def test_split_scopes_home_zone_and_adds_tracking_twin() -> None:
    planning = _planning()
    assert split_import_emission_zones(planning, ["BETTA", "France"]) == 1
    zones = planning["system"]["emission_zone_array"]
    assert zones[0]["buses"] == ["SEM"]
    twin = zones[1]
    assert twin["name"] == "global_ghg_imports"
    assert twin["uid"] == 2
    assert twin["buses"] == ["BETTA", "France"]
    assert twin["emissions"] == [{"emission": "co2", "weight": 1.0}]
    assert "price" not in twin and "cap" not in twin


def test_split_rejects_unknown_bus() -> None:
    with pytest.raises(ValueError, match="unknown bus"):
        split_import_emission_zones(_planning(), ["Narnia"])
