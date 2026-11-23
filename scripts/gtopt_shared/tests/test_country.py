# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for the per-country regulatory defaults."""

from __future__ import annotations

from typing import Any

import pytest

from gtopt_shared.cost_defaults import DEFAULT_SCC
from gtopt_shared.country import (
    CountryError,
    available_countries,
    emissions_overlay_path,
    load_country,
    parse_country,
)


def _base(**extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": 1,
        "country": "zz",
        "name": "Z",
        "currency": "USD",
        "timezone": "UTC",
    }
    data.update(extra)
    return data


def test_bundled_countries_parse() -> None:
    assert {"au", "cl", "co", "gt", "pe"} <= set(available_countries())
    for cc in available_countries():
        d = load_country(cc)
        assert d.code == cc
        if d.unserved_energy is not None:
            assert d.unserved_energy.source and d.unserved_energy.url.startswith(
                "https://"
            )


def test_chile_values() -> None:
    cl = load_country("CL")
    assert cl.timezone == "America/Santiago"
    assert cl.hydro_year_start_month == 4
    ue = cl.unserved_energy
    assert ue is not None and ue.kind == "tiers"
    assert [t.depth for t in ue.tiers] == [0.05, 0.10, 0.20, 1.00]
    assert ue.single_value == pytest.approx(795.11)
    assert ue.short_duration_cost == pytest.approx(13730.0)
    # DEFAULT_SCC now comes from the Chilean country file (unchanged value).
    assert cl.social_carbon_price == DEFAULT_SCC == pytest.approx(35.0)
    path = emissions_overlay_path("cl")
    assert path is not None and path.name == "cen_chile.json"


def test_single_value_kinds() -> None:
    pe = load_country("pe").unserved_energy
    assert pe is not None and pe.single_value == pytest.approx(746.0)
    assert load_country("co").unserved_energy is None
    assert emissions_overlay_path("co") is None


def test_operator_countries_have_files() -> None:
    from gtopt_operators import available_operators, load_registry

    for op in available_operators():
        load_country(load_registry(op).operator.country)


def test_unknown_country() -> None:
    with pytest.raises(CountryError, match="no country file for 'xx'"):
        load_country("xx")


def test_minimal_and_errors() -> None:
    assert parse_country(_base(), "zz").unserved_energy is None
    bad = _base(
        schema_version=2,
        country="yy",
        currency="",
        extra=1,
        hydro_year_start_month=13,
        carbon={"social_price": "x"},
        unserved_energy={
            "kind": "tiers",
            "tiers": [
                {"depth": 0.5, "cost": 10.0},
                {"depth": 0.2, "cost": 5.0},
                "bad",
                {"depth": True, "cost": 1.0},
            ],
            "short_duration_cost": "x",
        },
    )
    with pytest.raises(CountryError) as info:
        parse_country(bad, "zz")
    msg = str(info.value)
    for needle in (
        "schema_version",
        "unknown key(s) ['extra']",
        "country must be 'zz'",
        "currency must be",
        "hydro_year_start_month",
        "[carbon].social_price must be a number",
        "[unserved_energy].unit",
        "[unserved_energy].tiers[2] must be a table",
        "[unserved_energy].tiers[3].depth must be a number",
        "depths must increase",
        "costs must be non-decreasing",
        "short_duration_cost must be a number",
    ):
        assert needle in msg, needle


@pytest.mark.parametrize(
    ("table", "needle"),
    [
        ("nope", "[unserved_energy] must be a table"),
        ({"kind": "x", "unit": "u", "source": "s"}, "kind must be 'single' or 'tiers'"),
        ({"kind": "tiers", "unit": "u", "source": "s"}, "tiers must be a non-empty"),
        ({"kind": "single", "unit": "u", "source": "s"}, "cost must be a number"),
    ],
)
def test_unserved_energy_errors(table: Any, needle: str) -> None:
    with pytest.raises(
        CountryError, match=needle.replace("[", r"\[").replace("]", r"\]")
    ):
        parse_country(_base(unserved_energy=table), "zz")
