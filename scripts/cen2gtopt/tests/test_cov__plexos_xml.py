# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for :mod:`cen2gtopt._plexos_xml` on tiny synthetic XML databases."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from cen2gtopt import _plexos_xml as px
from cen2gtopt.tests.cov_plexos_helpers import DEFAULT_XML, plexos_xml


def _write(tmp_path: Path, text: str, name: str = "db.xml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _mem(mid: object, cid: object, parent: object, child: object) -> dict[str, object]:
    return {
        "membership_id": mid,
        "collection_id": cid,
        "parent_object_id": parent,
        "child_object_id": child,
    }


def test_parse_objects_skips_malformed(tmp_path: Path) -> None:
    xml = plexos_xml(
        objects=[
            (1, "G1", 2),
            (None, "noid", 2),
            ("x", "badid", 2),
            (3, "badclass", "y"),
            (4, "F1", 4),
        ]
    )
    root = px._load_root(str(_write(tmp_path, xml)))
    objs = px._parse_objects(root)
    assert objs.object_name_by_id == {1: "G1", 4: "F1"}
    assert objs.names_of_class(px.CLASS_FUEL) == {4: "F1"}
    assert not objs.names_of_class(px.CLASS_NODE)


def test_parse_memberships_filters(tmp_path: Path) -> None:
    xml = plexos_xml(
        objects=[],
        memberships=[
            _mem(1, 7, 1, 10),
            _mem(2, 8, 1, 10),  # other collection
            {"membership_id": 3, "parent_object_id": 1, "child_object_id": 2},
            {"membership_id": 4, "collection_id": 7, "parent_object_id": 1},
            _mem(5, 7, "x", 10),
        ],
    )
    root = px._load_root(str(_write(tmp_path, xml)))
    assert px._parse_memberships(root, collection_id=7) == [(1, 10)]


def test_parse_memberships_skips_non_numeric_collection_id(tmp_path: Path) -> None:
    """Regression: a non-numeric ``collection_id`` raised ValueError instead
    of being skipped like every other malformed field in this module."""
    xml = plexos_xml(
        objects=[],
        memberships=[_mem(1, "abc", 1, 10), _mem(2, 7, 2, 20), _mem(3, "", 3, 30)],
    )
    root = px._load_root(str(_write(tmp_path, xml)))
    assert px._parse_memberships(root, collection_id=7) == [(2, 20)]


def test_node_map_skips_non_numeric_collection_id(tmp_path: Path) -> None:
    """A Generator→Node t_collection with a bad id is skipped; the next
    well-formed one is used."""
    xml = plexos_xml(
        objects=[(1, "G1", 2), (20, "N1", 22)],
        memberships=[_mem(1, 12, 1, 20)],
        collections=[
            {"collection_id": "bad", "parent_class_id": 2, "child_class_id": 22},
            {"collection_id": 12, "parent_class_id": 2, "child_class_id": 22},
        ],
    )
    assert px.parse_unit_node_map(_write(tmp_path, xml)) == {"G1": "N1"}


def test_unit_fuel_and_node_maps(tmp_path: Path) -> None:
    path = _write(tmp_path, DEFAULT_XML)
    assert px.parse_unit_fuel_map(path) == {"ANGAMOS_1": "Carbon_Ang"}
    assert px.parse_unit_node_map(path) == {"ANGAMOS_1": "Crucero220"}
    s = px.summary(path).iloc[0].to_dict()
    assert s == {
        "n_generators": 3,
        "n_fuels": 2,
        "n_nodes": 1,
        "n_unit_fuel_pairs": 1,
        "n_unit_node_pairs": 1,
    }


def test_maps_skip_unknown_objects(tmp_path: Path) -> None:
    xml = plexos_xml(
        objects=[(1, "G1", 2), (10, "F1", 4), (20, "N1", 22)],
        memberships=[
            _mem(1, 7, 99, 10),  # unknown generator
            _mem(2, 7, 1, 98),  # unknown fuel
            _mem(3, 30, 1, 97),  # unknown node
            _mem(4, 30, 1, 20),
            _mem(5, 30, 1, 20),  # duplicate → first kept
        ],
        collections=[
            {"collection_id": 29, "parent_class_id": "bad", "child_class_id": 22},
            {"collection_id": 28, "parent_class_id": 2, "child_class_id": 4},
            {"collection_id": 30, "parent_class_id": 2, "child_class_id": 22},
        ],
    )
    path = _write(tmp_path, xml)
    assert not px.parse_unit_fuel_map(path)
    assert px.parse_unit_node_map(path) == {"G1": "N1"}


def test_node_map_without_collection(tmp_path: Path) -> None:
    xml = plexos_xml(objects=[(1, "G1", 2)])
    assert not px.parse_unit_node_map(_write(tmp_path, xml))


def test_generator_input_properties(tmp_path: Path) -> None:
    xml = plexos_xml(
        objects=[(1, "G1", 2), (2, "G2", 2), (3, "F1", 4)],
        memberships=[
            _mem(100, 1, 0, 1),
            _mem(101, 1, 0, 2),
            _mem(102, 7, 1, 3),  # not the Generator collection
            _mem("bad", 1, 0, 2),
        ],
        properties=[
            {"property_id": 11, "name": "Max Capacity", "collection_id": 1},
            {"property_id": 12, "name": "Heat Rate", "collection_id": 1},
            {"property_id": 13, "name": "Max Capacity", "collection_id": 7},
            {"property_id": 14, "name": "Units", "collection_id": 1},
            {"property_id": "x", "name": "Start Cost", "collection_id": 1},
            {"property_id": 15, "collection_id": 1},
        ],
        data=[
            {"membership_id": 100, "property_id": 11, "value": 250},
            {"membership_id": 100, "property_id": 12, "value": "9.5"},
            {"membership_id": 101, "property_id": 11, "value": "n/a"},
            {"membership_id": 101, "property_id": 14, "value": 1},
            {"membership_id": 999, "property_id": 11, "value": 1},
            {"membership_id": "m", "property_id": 11, "value": 1},
            {"membership_id": 101, "property_id": "p", "value": 1},
        ],
    )
    out = px.parse_generator_input_properties(str(_write(tmp_path, xml)))
    assert out == {"G1": {"Max Capacity": 250.0, "Heat Rate": 9.5}}


def test_generator_input_properties_none_of_interest(tmp_path: Path) -> None:
    xml = plexos_xml(
        objects=[(1, "G1", 2)],
        properties=[{"property_id": 11, "name": "Units", "collection_id": 1}],
    )
    assert not px.parse_generator_input_properties(str(_write(tmp_path, xml)))


def test_load_root_is_memoised(tmp_path: Path) -> None:
    path = str(_write(tmp_path, DEFAULT_XML))
    assert px._load_root(path) is px._load_root(path)


def test_load_root_rejects_garbage(tmp_path: Path) -> None:
    with pytest.raises(ET.ParseError):
        px._load_root(str(_write(tmp_path, "<oops")))
