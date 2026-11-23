# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge cases of the hydro / logical / clustering topology passes."""

import pytest

from gtopt_diagram import gtopt_diagram as gd
from gtopt_diagram.tests import test_diagram_new_entities as ne

TB = gd.TopologyBuilder


def _build(planning, subsystem="full", **opts):
    opts.setdefault("aggregate", "none")
    builder = TB(planning, subsystem=subsystem, opts=gd.FilterOptions(**opts))
    return builder.build()


_FIXTURES = [
    ne._FUEL_PLANNING,  # pylint: disable=protected-access
    ne._EMISSION_PLANNING,  # pylint: disable=protected-access
    ne._DV_PLANNING,  # pylint: disable=protected-access
    ne._UC_PLANNING,  # pylint: disable=protected-access
    ne._COMMIT_PLANNING,  # pylint: disable=protected-access
    ne._CC_PLANNING,  # pylint: disable=protected-access
    ne._RESERVE_PLANNING,  # pylint: disable=protected-access
    ne._PLANT_PLANNING,  # pylint: disable=protected-access
    ne._JUNC_HYDRO,  # pylint: disable=protected-access
    ne._BASIN_PLANNING,  # pylint: disable=protected-access
]


@pytest.mark.parametrize("planning", _FIXTURES)
def test_compact_logical_labels_are_bare_names(planning):
    """--compact drops the detail lines of every logical element label."""
    model = _build(planning, compact=True, draw_logical_elements=True)
    assert model.nodes
    for node in model.nodes:
        assert "\n" not in node.label or node.kind == "bus"


# ── static helpers ───────────────────────────────────────────────────────


def test_substation_prefix_and_short_line():
    assert TB._substation_prefix("CHARRUA_500KV") == "CHARRUA"
    assert TB._substation_prefix("Charrua220") == "Charrua"
    assert TB._substation_prefix("_500") is None  # empty prefix
    assert TB._substation_prefix("plain") is None
    assert TB._line_is_intra_substation({"type": "Transformer"})
    assert TB._line_is_intra_substation({"resistance": 0.001, "reactance": 0.01})
    assert not TB._line_is_intra_substation({"reactance": "bad"})
    assert not TB._line_is_intra_substation({"reactance": 0.5})


def test_hydro_plant_stem():
    assert TB._hydro_plant_stem("COLBUN_1") == "COLBUN"
    assert TB._hydro_plant_stem("ATA_CC_1_ConfTGA") == "ATA_CC_1"
    assert TB._hydro_plant_stem("PLAIN") is None


def _hydro_sys():
    return {
        "system": {
            "name": "h",
            "bus_array": [{"uid": 1, "name": "B1"}],
            "junction_array": [
                {"uid": 1, "name": "J1"},
                {"uid": 2, "name": "J2"},
            ],
            "waterway_array": [
                {"uid": 1, "name": "W12", "junction_a": 1, "junction_b": 2},
                {"uid": 2, "name": "Wnoa", "junction_b": 2},
            ],
            "generator_array": [
                {"uid": 1, "name": "G_1", "bus": 1, "pmax": 10},
                {"uid": 2, "name": "G_2", "bus": 1, "pmax": 10},
                {"uid": 3, "name": "H", "bus": 1, "pmax": 10},
            ],
            "turbine_array": [
                {
                    "uid": 1,
                    "name": "T1",
                    "generator": 1,
                    "junction_a": "J1",
                    "junction_b": "J2",
                },
                {
                    "uid": 2,
                    "name": "T2",
                    "generator": "G_2",
                    "junction_a": 1,
                    "junction_b": 2,
                },
                {"uid": 3, "name": "T3", "generator": 3, "waterway": "W12"},
                {"uid": 4, "name": "T4", "generator": 3, "waterway": "Wnoa"},
                {"uid": 5, "name": "T5", "generator": 3, "waterway": "nope"},
                {"uid": 6, "name": "T6", "generator": 3},
                {"uid": 7, "name": "T7", "generator": 3, "junction_a": "JX"},
                {"uid": 8, "name": "T8"},
                {"uid": 9, "name": "T9", "generator": 99, "junction_a": "J1"},
            ],
        }
    }


def test_turbine_upstream_junction_modes():
    planning = _hydro_sys()
    b = TB(planning, opts=gd.FilterOptions(aggregate="none"))
    turbines = planning["system"]["turbine_array"]
    up = b._turbine_upstream_junction  # pylint: disable=protected-access
    assert up(turbines[0]) == "J1"
    assert up(turbines[1]) == "J1"
    assert up(turbines[2]) == "J1"  # Mode A via waterway
    assert up(turbines[3]) is None  # waterway without junction_a
    assert up(turbines[4]) is None  # unknown waterway
    assert up(turbines[5]) is None  # no upstream info
    assert up(turbines[6]) is None  # unknown junction
    planning["system"]["waterway_array"].append(
        {"uid": 3, "name": "Wfree", "junction_a": "free_text"}
    )
    assert up({"waterway": "Wfree"}) == "free_text"


def test_auto_detect_plants_junction_and_stem():
    model = _build(_hydro_sys())
    meta = model.plant_meta
    assert "plant:G" in meta
    assert "@ J1" in meta["plant:G"]
    # type aggregation disables auto detection
    b = TB(_hydro_sys(), opts=gd.FilterOptions(aggregate="type"))
    assert not b._auto_detect_hydro_plants()  # pylint: disable=protected-access


def test_auto_detect_junction_label_without_common_stem():
    planning = _hydro_sys()
    planning["system"]["generator_array"][1]["name"] = "OTHER_9"
    planning["system"]["turbine_array"][1]["generator"] = "OTHER_9"
    model = _build(planning)
    assert any(lbl.startswith("J1 (3 units") for lbl in model.plant_meta.values())


def test_auto_detect_stem_groups_by_bus_and_without_bus():
    planning = {
        "system": {
            "name": "stems",
            "bus_array": [{"uid": 1, "name": "B1"}, {"uid": 2, "name": "B2"}],
            "generator_array": [
                {"uid": 1, "name": "ATA_1", "bus": 1},
                {"uid": 2, "name": "ATA_2", "bus": 1},
                {"uid": 3, "name": "ATA_3", "bus": 2},  # alone on its bus
                {"uid": 4, "name": "NB_A"},
                {"uid": 5, "name": "NB_B", "bus": ""},
                {"uid": 6, "name": "SOLO_1"},
                {"uid": 7, "name": "EXP_1", "bus": 2},
                {"uid": 8, "name": "EXP_2", "bus": 2},
                {"uid": 9},
            ],
            "plant_array": [
                {
                    "uid": 1,
                    "name": "EXP",
                    "generator_names": ["EXP_1", "EXP_2"],
                    "pmax": 120.0,
                    "n_units": 2,
                    "uniq_mutex": True,
                },
                {"uid": 2, "name": "", "generator_names": ["X"]},
                {"uid": 3, "name": "EMPTY", "generator_names": ["missing"]},
            ],
        }
    }
    b = TB(planning, opts=gd.FilterOptions(aggregate="none"))
    groups = b._auto_detect_hydro_plants()  # pylint: disable=protected-access
    assert groups["plant:ATA"][1] == ["ATA_1", "ATA_2"]
    assert "@ B1" in groups["plant:ATA"][0]
    assert groups["plant:NB"][0] == "NB (2 units · auto)"
    assert "plant:SOLO" not in groups
    assert "plant:EXP" not in groups
    model = b.build()
    assert model.plant_meta["plant:EXP"] == "EXP · pmax=120 · n_units=2 · uniq"
    assert "plant:EMPTY" not in model.plant_meta


def test_auto_detect_stem_claimed_by_junction_pass():
    planning = _hydro_sys()
    # same stem "G" on another bus — already claimed by the junction pass
    planning["system"]["bus_array"].append({"uid": 2, "name": "B2"})
    planning["system"]["generator_array"] += [
        {"uid": 10, "name": "G_7", "bus": 2},
        {"uid": 11, "name": "G_8", "bus": 2},
        {"uid": 12, "name": "G_A"},
        {"uid": 13, "name": "G_B"},
    ]
    b = TB(planning, opts=gd.FilterOptions(aggregate="none"))
    groups = b._auto_detect_hydro_plants()  # pylint: disable=protected-access
    assert groups["plant:G"][1] == ["G_1", "G_2", "H"]


def test_substations_union_and_skips():
    planning = {
        "system": {
            "name": "subs",
            "bus_array": [
                {"uid": 1, "name": "CHA_500KV"},
                {"uid": 2, "name": "CHA_220KV"},
                {"uid": 3, "name": "CHA_66KV"},
                {"uid": 4, "name": "OTHER_220KV"},
                {"uid": 5, "name": "plain"},
                {"uid": 6},
            ],
            "line_array": [
                {"uid": 1, "bus_a": 1, "bus_b": 2, "type": "transformer"},
                {"uid": 2, "bus_a": "CHA_220KV", "bus_b": 3, "reactance": 0.01},
                {"uid": 3, "bus_a": 2, "bus_b": 4, "type": "transformer"},
                {"uid": 4, "bus_a": 5, "bus_b": 6, "type": "transformer"},
                {"uid": 5, "bus_a": 1, "bus_b": 4, "reactance": 1.0},
            ],
        }
    }
    model = _build(planning)
    meta = model.plant_meta
    assert meta["substation:CHA"] == "CHA substation (3 buses)"
    assert not any("OTHER" in k for k in meta)


def test_substations_none_when_no_named_buses():
    model = _build({"system": {"bus_array": [{"uid": 1}, {"uid": 2}]}})
    assert not model.plant_meta


def test_lng_terminal_and_legacy_reserve_zone_string():
    planning = {
        "system": {
            "name": "lng",
            "bus_array": [{"uid": 1, "name": "B1"}],
            "generator_array": [
                {"uid": 1, "name": "CC_1", "bus": 1, "pmax": 100},
                {"uid": 2, "name": "CC_2", "bus": 1, "pmax": 100},
            ],
            "lng_terminal_array": [
                {
                    "uid": 1,
                    "name": "LT",
                    "generators": [
                        {"generator": 1, "heat_rate": 7.5},
                        {"generator": 2},
                        {"heat_rate": 1.0},
                    ],
                },
            ],
            # a junction keeps the "full" subsystem (reserve zones are only
            # drawn there)
            "junction_array": [{"uid": 1, "name": "J"}],
            "reserve_zone_array": [{"uid": 1, "name": "Z1"}, {"uid": 2, "name": "Z2"}],
            "reserve_provision_array": [
                {"uid": 1, "generator": 1, "reserve_zones": "Z1: Z2 :"},
                {"uid": 2, "generator": 2, "reserve_zones": ""},
            ],
        }
    }
    model = _build(planning, draw_logical_elements=True)
    lng = next(n for n in model.nodes if n.kind == "lng_terminal")
    assert lng.label == "[LNG] LT:1"  # no emax / sendout
    labels = [e.label for e in model.edges]
    assert "fuel\n7.5 m³/MWh" in labels
    assert "fuel" in labels
    assert labels.count("reserve") == 2
