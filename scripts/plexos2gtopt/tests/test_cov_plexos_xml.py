# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for :mod:`plexos2gtopt.plexos_xml`."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from plexos2gtopt.plexos_xml import load_xml


def _rows(table: str, *rows: str) -> str:
    return "".join(f"<{table}>{r}</{table}>" for r in rows)


_XML = (
    '<?xml version="1.0" standalone="yes"?>'
    '<MasterDataSet xmlns="http://tempuri.org/MasterDataSet.xsd">'
    + _rows(
        "t_class",
        "<class_id>1</class_id><name>System</name>",
        "<class_id>2</class_id><name>Generator</name>",
        "<class_id>3</class_id><name>Model</name>",
        "<class_id>4</class_id><name>Scenario</name>",
        "<class_id>5</class_id><name>Horizon</name>",
        "<class_id>6</class_id><name>Line</name>",
        "<class_id>x</class_id><name>Bad</name>",
        "<class_id>7</class_id>",
    )
    + _rows(
        "t_category",
        "<category_id>1</category_id><name>Solar</name>",
        "<name>nameless</name>",
    )
    + _rows(
        "t_object",
        "<object_id>1</object_id><class_id>1</class_id><name>System</name>",
        "<object_id>2</object_id><class_id>2</class_id><name>G1</name>"
        "<category_id>1</category_id>",
        "<object_id>3</object_id><class_id>3</class_id><name>M_other</name>",
        "<object_id>4</object_id><class_id>3</class_id>"
        "<name>PRGdia_Full_Definitivo</name>",
        "<object_id>5</object_id><class_id>4</class_id><name>S1</name>",
        "<object_id>6</object_id><class_id>5</class_id><name>H</name>",
        "<object_id>7</object_id><class_id>2</class_id><name>G2</name>",
        "<object_id>8</object_id><class_id>6</class_id><name>L1</name>",
        "<object_id>9</object_id><name>broken</name>",
    )
    + _rows(
        "t_collection",
        "<collection_id>10</collection_id><parent_class_id>1</parent_class_id>"
        "<child_class_id>2</child_class_id><name>Generators</name>",
        "<collection_id>11</collection_id><parent_class_id>3</parent_class_id>"
        "<child_class_id>4</child_class_id><name>Scenarios</name>",
        "<collection_id>12</collection_id><parent_class_id>3</parent_class_id>"
        "<child_class_id>5</child_class_id><name>Horizons</name>",
        "<collection_id>13</collection_id><parent_class_id>1</parent_class_id>",
    )
    + _rows(
        "t_membership",
        "<membership_id>100</membership_id><collection_id>10</collection_id>"
        "<parent_object_id>1</parent_object_id><child_object_id>2</child_object_id>",
        "<membership_id>101</membership_id><collection_id>11</collection_id>"
        "<parent_object_id>4</parent_object_id><child_object_id>5</child_object_id>",
        "<membership_id>102</membership_id><collection_id>12</collection_id>"
        "<parent_object_id>4</parent_object_id><child_object_id>6</child_object_id>",
        "<membership_id>103</membership_id><collection_id>99</collection_id>"
        "<parent_object_id>1</parent_object_id><child_object_id>8</child_object_id>",
        "<membership_id>104</membership_id><collection_id>10</collection_id>",
    )
    + _rows(
        "t_property",
        "<property_id>1</property_id><collection_id>10</collection_id>"
        "<name>Max Capacity</name>",
        "<property_id>2</property_id><collection_id>10</collection_id>"
        "<name>Heat Rate</name>",
        "<property_id>3</property_id><collection_id>10</collection_id>"
        "<name>Empty</name>",
        "<property_id>4</property_id><name>orphan</name>",
    )
    + _rows(
        "t_data",
        "<data_id>1</data_id><membership_id>100</membership_id>"
        "<property_id>1</property_id><value>1E+30</value>",
        "<data_id>2</data_id><membership_id>100</membership_id>"
        "<property_id>2</property_id><value>10</value><period_id>1</period_id>",
        "<data_id>3</data_id><membership_id>100</membership_id>"
        "<property_id>2</property_id><value>20</value>",
        "<data_id>5</data_id><membership_id>100</membership_id>"
        "<property_id>2</property_id><value>30</value>",
        "<data_id>4</data_id><membership_id>100</membership_id>"
        "<property_id>2</property_id><value>40</value>",
        "<data_id>6</data_id><membership_id>100</membership_id>"
        "<property_id>2</property_id><value>abc</value>",
        "<data_id>7</data_id><membership_id>100</membership_id>"
        "<property_id>2</property_id><value></value>",
        "<data_id>8</data_id><membership_id>100</membership_id>",
    )
    + _rows(
        "t_band",
        "<data_id>3</data_id><band_id>2</band_id>",
        "<data_id>5</data_id><band_id>2</band_id>",
        "<data_id>4</data_id><band_id>2</band_id>",
        "<data_id>9</data_id>",
    )
    + _rows(
        "t_date_from",
        "<data_id>2</data_id><date>2025-01-01T00:00:00</date>",
        "<data_id>3</data_id><date>not-a-date</date>",
        "<data_id>4</data_id>",
    )
    + _rows(
        "t_tag",
        "<data_id>2</data_id><object_id>5</object_id>",
        "<data_id>2</data_id>",
    )
    + _rows(
        "t_text",
        "<data_id>2</data_id><class_id>76</class_id><value> H1-8 </value>",
        "<data_id>3</data_id><class_id>76</class_id><value>  </value>",
        "<class_id>76</class_id><value>H1</value>",
        "<data_id>5</data_id><class_id>12</class_id><value>Traces\\a.csv</value>",
    )
    + _rows(
        "t_attribute",
        "<attribute_id>1</attribute_id><name>Date From</name>",
        "<attribute_id>2</attribute_id><name>Step Count</name>",
    )
    + _rows(
        "t_attribute_data",
        "<object_id>6</object_id><attribute_id>2</attribute_id><value>7</value>",
        "<object_id>6</object_id><attribute_id>1</attribute_id><value></value>",
        "<object_id>6</object_id><attribute_id>1</attribute_id><value>45658</value>",
    )
    + "</MasterDataSet>"
)


@pytest.fixture(name="db_path")
def _db_path(tmp_path: Path) -> Path:
    p = tmp_path / "db.xml"
    p.write_text(_XML, encoding="utf-8")
    return p


def test_tables_skip_malformed_rows(db_path: Path) -> None:
    db = load_xml(db_path)
    assert db.class_id("Bad") is None and 7 not in db.classes_by_id
    assert db.categories_by_id == {1: "Solar"}
    assert "broken" not in {o.name for o in db.objects}
    assert {c.collection_id for c in db.collections} == {10, 11, 12}
    assert 104 not in {m.membership_id for m in db.memberships}
    assert {p.property_id for p in db.properties} == {1, 2, 3}
    assert {r.data_id for r in db.data_rows} == {1, 2, 3, 4, 5}
    assert db.tag_for_data == {2: [5]}
    assert db.band_for_data == {3: 2, 5: 2, 4: 2}
    assert db.text_for_data == {2: "H1-8", 5: "Traces\\a.csv"}
    rows = {r.data_id: r for r in db.data_rows}
    assert rows[2].timeslice == "H1-8" and rows[3].timeslice is None
    assert rows[2].date_from == datetime(2025, 1, 1)
    assert rows[3].date_from is None
    assert db.horizon_start == datetime(2025, 1, 1)


def test_lookup_helpers(db_path: Path) -> None:
    db = load_xml(db_path)
    assert db.objects_of_class("Nope") == []
    assert db.collection_for("System", "Nope") is None
    assert db.collection_for_named("System", "Nope", "x") is None
    assert db.collection_for_named("System", "Generator", "Other") is None
    assert db.parent_to_children(11) == {4: [5]}
    coll = db.collection_for_named("System", "Generator", "Generators")
    assert coll is not None and coll.collection_id == 10
    assert [m.membership_id for m in db.memberships_of(12)] == [102]
    assert db.object_by_id()[2].name == "G1"
    # Sentinel collapses to default unless keep_sentinel.
    assert db.static_property("Generator", 2, "Max Capacity", default=-1.0) == -1.0
    assert db.static_property(
        "Generator", 2, "Max Capacity", keep_sentinel=True
    ) == pytest.approx(1e30)
    assert db.static_property("Generator", 2, "Heat Rate") == 10.0
    assert db.static_property("Line", 8, "Heat Rate", default=3.0) == 3.0
    assert db.static_property("Generator", 2, "Nope", default=4.0) == 4.0
    assert db.static_property("Generator", 7, "Heat Rate", default=5.0) == 5.0
    assert db.static_property("Generator", 2, "Empty", default=6.0) == 6.0
    assert db.system_membership_id("Nope", 2) is None


def test_band_values(db_path: Path) -> None:
    db = load_xml(db_path)
    # Band 2 has three rows: the lowest data_id (3) wins.
    assert db.band_values("Generator", 2, "Heat Rate") == [(1, 10.0), (2, 20.0)]
    assert db.band_values("Line", 8, "Heat Rate") == []
    assert db.band_values("Generator", 2, "Nope") == []
    assert db.band_values("Generator", 7, "Heat Rate") == []


def test_active_scenarios(db_path: Path) -> None:
    db = load_xml(db_path)
    assert db.active_scenario_ids() == {5}
    assert db.active_scenario_ids("PRGdia_Full_Definitivo") == {5}
    assert db.active_scenario_ids("M_other") == set()
    assert db.active_scenario_ids("missing") == set()
    db.classes_by_name.pop("Scenario")
    assert db.active_scenario_ids() == set()
    db.classes_by_name.pop("Model")
    assert db.active_scenario_ids() == set()


def test_no_system_class(tmp_path: Path) -> None:
    p = tmp_path / "nosys.xml"
    p.write_text(_XML.replace("<name>System</name>", "<name>Sys</name>", 1))
    db = load_xml(p)
    assert db.system_membership_id("Generator", 2) is None
    assert db.horizon_start is not None


def test_no_horizon_date(tmp_path: Path) -> None:
    p = tmp_path / "nohz.xml"
    p.write_text(_XML.replace("<value>45658</value>", "<value></value>"))
    assert load_xml(p).horizon_start is None


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_xml(tmp_path / "nope.xml")
