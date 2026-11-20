"""PLEXOS ``RHS Hour`` / ``RHS Week`` / ``RHS Month`` / ``RHS Year`` and the
``Hours of Operation Coefficient`` in :func:`extract_user_constraints`.

Shapes taken from the SEM PLEXOS Forecast Model (no SEM data here):

* ``HOURS_YEAR`` — Sense=-1, ``RHS Year``=500, ``Hours of Operation
  Coefficient`` 1 on G1 (a yearly running-hours limit) → ONE stage-scope row
  ``sum{b in stage} dur[b] * 1 * commitment("uc_G1").status <= 500·168/8760``.
* ``HUB_HOUR`` — Sense=-1, ``RHS Hour``=50, Generation Coefficient 1 on G1
  and G2 (the SEM ``Data Hub`` cap) → per-block ``G1 + G2 <= 50``.
* ``ENERGY_WEEK`` — Sense=-1, ``RHS Week``=10 GWh, Generation Coefficient 1
  on G1 → ``sum{b in stage} dur[b] * 1 * generator("G1").generation <= 10000``
  (GWh→MWh, the week IS the 168 h horizon so no pro-rating).
* ``STARTS_MONTH`` — Sense=-1, ``RHS Month``=73, Units Started Coefficient 1
  on G2 → unweighted start count ``<= 73·168/730``.
* ``HOURS_DAY`` — Sense=-1, ``RHS Day``=12, Hours of Operation Coefficient 1
  on G2 → ``daily_sum`` Δt-weighted row ``<= 12`` (hours, no ×1000).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from plexos2gtopt.entities import UserConstraintSpec
from plexos2gtopt.gtopt_writer import (
    build_user_constraint_array,
    write_user_constraint_pampl,
)
from plexos2gtopt.parsers import (
    build_period_sum_expression,
    extract_user_constraints,
    period_hours,
    unhandled_constraint_coefficient_kinds,
)
from plexos2gtopt.plexos_loader import PlexosBundle
from plexos2gtopt.plexos_xml import NS, load_xml

# (name, sense, rhs property, rhs value, [(generator, coefficient property)])
_CONSTRAINTS: tuple[tuple[str, float, str, float, list[tuple[str, str]]], ...] = (
    ("HOURS_YEAR", -1, "RHS Year", 500.0, [("G1", "Hours of Operation Coefficient")]),
    (
        "HUB_HOUR",
        -1,
        "RHS Hour",
        50.0,
        [("G1", "Generation Coefficient"), ("G2", "Generation Coefficient")],
    ),
    ("ENERGY_WEEK", -1, "RHS Week", 10.0, [("G1", "Generation Coefficient")]),
    ("STARTS_MONTH", -1, "RHS Month", 73.0, [("G2", "Units Started Coefficient")]),
    ("HOURS_DAY", -1, "RHS Day", 12.0, [("G2", "Hours of Operation Coefficient")]),
)
_SYS_PROPS = (
    "Sense",
    "RHS",
    "RHS Hour",
    "RHS Day",
    "RHS Week",
    "RHS Month",
    "RHS Year",
)
_GEN_PROPS = (
    "Generation Coefficient",
    "Units Started Coefficient",
    "Hours of Operation Coefficient",
)
_GENS = ("G1", "G2")


def _xml() -> str:
    """Build the synthetic PLEXOS XML for :data:`_CONSTRAINTS`."""
    parts = [
        '<?xml version="1.0" standalone="yes"?>',
        f'<MasterDataSet xmlns="{NS[1:-1]}">',
        "<t_class><class_id>1</class_id><name>System</name></t_class>",
        "<t_class><class_id>2</class_id><name>Generator</name></t_class>",
        "<t_class><class_id>70</class_id><name>Constraint</name></t_class>",
        "<t_object><object_id>1</object_id><class_id>1</class_id>"
        "<name>SYS</name></t_object>",
        "<t_collection><collection_id>700</collection_id>"
        "<parent_class_id>1</parent_class_id><child_class_id>70</child_class_id>"
        "<name>Constraints</name></t_collection>",
        "<t_collection><collection_id>32</collection_id>"
        "<parent_class_id>2</parent_class_id><child_class_id>70</child_class_id>"
        "<name>Constraints</name></t_collection>",
    ]
    prop_id: dict[str, int] = {}
    for i, name in enumerate(_SYS_PROPS):
        prop_id[name] = 4000 + i
        parts.append(
            f"<t_property><property_id>{4000 + i}</property_id>"
            f"<collection_id>700</collection_id><name>{name}</name></t_property>"
        )
    for i, name in enumerate(_GEN_PROPS):
        prop_id[name] = 300 + i
        parts.append(
            f"<t_property><property_id>{300 + i}</property_id>"
            f"<collection_id>32</collection_id><name>{name}</name></t_property>"
        )
    gen_oid = {g: 20 + i for i, g in enumerate(_GENS)}
    for g, oid in gen_oid.items():
        parts.append(
            f"<t_object><object_id>{oid}</object_id><class_id>2</class_id>"
            f"<name>{g}</name></t_object>"
        )
    mid = 10000
    did = 50000

    def data(membership: int, prop: str, value: float) -> None:
        nonlocal did
        did += 1
        parts.append(
            f"<t_data><data_id>{did}</data_id><membership_id>{membership}"
            f"</membership_id><property_id>{prop_id[prop]}</property_id>"
            f"<value>{value}</value></t_data>"
        )

    for k, (name, sense, rhs_prop, rhs, members) in enumerate(_CONSTRAINTS):
        coid = 100 + k
        parts.append(
            f"<t_object><object_id>{coid}</object_id><class_id>70</class_id>"
            f"<name>{name}</name></t_object>"
        )
        mid += 1
        parts.append(
            f"<t_membership><membership_id>{mid}</membership_id>"
            "<collection_id>700</collection_id><parent_object_id>1"
            f"</parent_object_id><child_object_id>{coid}</child_object_id>"
            "</t_membership>"
        )
        data(mid, "Sense", sense)
        data(mid, rhs_prop, rhs)
        for gen, coeff_prop in members:
            mid += 1
            parts.append(
                f"<t_membership><membership_id>{mid}</membership_id>"
                "<collection_id>32</collection_id><parent_object_id>"
                f"{gen_oid[gen]}</parent_object_id><child_object_id>{coid}"
                "</child_object_id></t_membership>"
            )
            data(mid, coeff_prop, 1.0)
    parts.append("</MasterDataSet>")
    return "\n".join(parts)


def _extract(tmp_path: Path, **kwargs) -> dict[str, UserConstraintSpec]:
    xml_path = tmp_path / "period.xml"
    xml_path.write_text(_xml())
    db = load_xml(xml_path)
    bundle = PlexosBundle(root=tmp_path, source=tmp_path)
    bundle.n_days = 7  # a one-week (168 h) hourly horizon
    out = extract_user_constraints(db, bundle, **kwargs)
    return {c.name: c for c in out}


def test_rhs_year_hours_of_operation_prorated_stage_row(tmp_path: Path) -> None:
    """A yearly hours-of-operation budget becomes one stage-scope Δt-weighted
    status sum with the RHS pro-rated to the 168 h horizon."""
    stats: dict[str, int] = {}
    spec = _extract(tmp_path, stats_out=stats)["HOURS_YEAR"]
    assert spec.scope == "stage"
    assert spec.daily_sum is False
    assert spec.active is None  # NOT downgraded as a binary tautology
    lhs, rhs = spec.expression.split(" <= ")
    assert lhs == 'sum{b in stage} dur[b] * 1 * commitment("uc_G1").status'
    assert float(rhs) == pytest.approx(500.0 * 168.0 / 8760.0, rel=1e-5)
    assert "APPROXIMATION" in spec.description
    assert stats["period_rhs_converted"] == 3  # + ENERGY_WEEK, STARTS_MONTH


def test_rhs_hour_is_per_block(tmp_path: Path) -> None:
    """``RHS Hour`` (SEM Data Hub) is a plain per-block bound."""
    spec = _extract(tmp_path)["HUB_HOUR"]
    assert spec.scope == ""
    assert spec.daily_sum is False
    assert spec.expression == (
        '1 * generator("G1").generation + 1 * generator("G2").generation <= 50'
    )


def test_rhs_week_energy_scaled_to_mwh(tmp_path: Path) -> None:
    """A weekly energy budget over a one-week horizon: GWh→MWh, no pro-rata."""
    spec = _extract(tmp_path)["ENERGY_WEEK"]
    assert spec.scope == "stage"
    assert spec.expression == (
        'sum{b in stage} dur[b] * 1 * generator("G1").generation <= 10000'
    )
    assert "APPROXIMATION" not in spec.description


def test_rhs_month_start_count_unweighted(tmp_path: Path) -> None:
    """Start-up counts are summed without the ``dur[b]`` weight."""
    spec = _extract(tmp_path)["STARTS_MONTH"]
    lhs, rhs = spec.expression.split(" <= ")
    assert lhs == 'sum{b in stage} 1 * commitment("uc_G2").startup'
    assert float(rhs) == pytest.approx(73.0 * 168.0 / 730.0, rel=1e-5)


def test_rhs_day_hours_of_operation_daily_hours(tmp_path: Path) -> None:
    """``RHS Day`` hours-of-operation → daily_sum, Δt-weighted, no ×1000."""
    spec = _extract(tmp_path)["HOURS_DAY"]
    assert spec.daily_sum is True
    assert spec.constraint_type == "energy"
    assert spec.expression == '1 * commitment("uc_G2").status <= 12'


def test_period_rhs_drop_mode(tmp_path: Path) -> None:
    """``--plexos-period-rhs drop`` skips the Week/Month/Year rows only."""
    stats: dict[str, int] = {}
    by_name = _extract(tmp_path, period_rhs_mode="drop", stats_out=stats)
    assert set(by_name) == {"HUB_HOUR", "HOURS_DAY"}
    assert stats["period_rhs_dropped"] == 3


def test_period_rhs_bad_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="period_rhs_mode"):
        _extract(tmp_path, period_rhs_mode="ignore")


def test_hours_of_operation_kind_is_handled(tmp_path: Path) -> None:
    """The coefficient kind no longer shows up as unconverted."""
    xml_path = tmp_path / "period.xml"
    xml_path.write_text(_xml())
    kinds = unhandled_constraint_coefficient_kinds(load_xml(xml_path))
    assert ("Generator", "Hours of Operation Coefficient") not in kinds


def test_period_hours_calendar() -> None:
    assert period_hours("week", None) == 168.0
    assert period_hours("month", datetime(2025, 2, 10)) == 28 * 24
    assert period_hours("month", datetime(2024, 2, 10)) == 29 * 24
    assert period_hours("month", datetime(2025, 12, 1)) == 31 * 24
    assert period_hours("year", datetime(2025, 1, 13)) == 8760.0
    assert period_hours("year", datetime(2024, 1, 13)) == 8784.0
    with pytest.raises(ValueError):
        period_hours("fortnight", None)


def test_build_period_sum_expression_units() -> None:
    gen = ['1 * generator("G1").generation', ' - 2 * generator("G2").generation']
    expr, unit = build_period_sum_expression(gen, "<=", 5000.0) or ("", "")
    assert unit == "energy"
    assert expr == (
        'sum{b in stage} dur[b] * 1 * generator("G1").generation'
        ' - sum{b in stage} dur[b] * 2 * generator("G2").generation <= 5000'
    )
    # Mixed MW + hours: the MW terms go to GWh so the RHS stays in PLEXOS units.
    mixed = ['-1 * commitment("uc_G1").status', ' + 1 * generator("G1").generation']
    expr, unit = build_period_sum_expression(mixed, ">=", 3.0) or ("", "")
    assert unit == "mixed"
    assert expr == (
        '-1 * sum{b in stage} dur[b] * 1 * commitment("uc_G1").status'
        ' + sum{b in stage} dur[b] * 0.001 * generator("G1").generation >= 3'
    )
    # Reserve provision has no defined period unit → not converted.
    assert (
        build_period_sum_expression(
            ['1 * reserve_provision("provision_G1").up'], "<=", 1.0
        )
        is None
    )


def test_writer_emits_scope_and_keeps_row_inline(tmp_path: Path) -> None:
    """``scope`` reaches the JSON entry and the row stays out of ``.pampl``."""
    specs = (
        UserConstraintSpec(
            name="HOURS",
            expression='sum{b in stage} dur[b] * 1 * commitment("uc_G1").status <= 9',
            scope="stage",
        ),
        UserConstraintSpec(name="PLAIN", expression='line("L").flow <= 10'),
    )
    uc_array = build_user_constraint_array(specs)
    assert uc_array[0]["scope"] == "stage"
    assert "scope" not in uc_array[1]
    _files, json_remaining = write_user_constraint_pampl(uc_array, tmp_path)
    assert [uc["name"] for uc in json_remaining] == ["HOURS"]
