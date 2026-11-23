# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for the planning time-structure diagram generators."""

import xml.etree.ElementTree as ET

from gtopt_diagram._planning_diagram import (
    _DEFAULT_PLANNING,
    _build_planning_html,
    _build_planning_mermaid,
    _build_planning_svg,
)


def _texts(svg: str) -> list[str]:
    root = ET.fromstring(svg)
    return [
        "".join(t.itertext()) for t in root.iter("{http://www.w3.org/2000/svg}text")
    ]


def test_default_planning_svg_is_valid_xml():
    svg = _build_planning_svg({})
    texts = _texts(svg)
    assert "gtopt (example) — Planning Time Structure" in texts
    assert "PHASES" in texts
    assert any("Scenario 1 (dry year)" in t for t in texts)
    assert any("discount=0.91" in t for t in texts)
    assert any(t.endswith("yr") for t in texts)  # 4380 h blocks → years


def test_missing_simulation_uses_default_arrays():
    svg = _build_planning_svg({"system": {"name": "only_sys"}})
    texts = _texts(svg)
    assert "only_sys — Planning Time Structure" in texts
    assert "PHASES" in texts  # default phases come with the default simulation


def test_custom_planning_edge_cases():
    """Unnamed elements, out-of-range stages/phases, many scenarios."""
    planning = {
        "system": {},
        "simulation": {
            "scenario_array": [{"uid": i} for i in range(1, 11)],
            "block_array": [
                {"uid": 1, "duration": 1},
                {"uid": 2, "duration": 2},
                {"uid": 3},
            ],
            "stage_array": [
                {"uid": 1, "first_block": 0, "count_block": 2},
                {"uid": 2, "first_block": 2, "count_block": 5},  # clipped
                {"uid": 3, "first_block": 9, "count_block": 1},  # out of range
            ],
            "phase_array": [
                {"uid": 1, "first_stage": 0, "count_stage": 2},
                {"uid": 2, "first_stage": 7},  # out of range → skipped
            ],
        },
    }
    texts = _texts(_build_planning_svg(planning))
    assert "gtopt — Planning Time Structure" in texts
    assert any("Scenario 1" in t for t in texts)
    assert any("and 2 more scenarios" in t for t in texts)
    assert any("Stage 1" == t for t in texts)
    assert not any("Stage 3" == t for t in texts)
    assert "Phase 1" in texts
    assert "Phase 2" not in texts
    assert any("2 block(s)" in t for t in texts)  # no discount factor
    assert "B3" in texts
    assert "1h" in texts


def test_no_phases_omits_phase_row():
    planning = {
        "simulation": {
            "scenario_array": [{"uid": 1, "name": "s"}],
            "stage_array": [{"uid": 1, "name": "st", "first_block": 0}],
            "block_array": [{"uid": 1, "name": "b", "duration": 24}],
        }
    }
    texts = _texts(_build_planning_svg(planning))
    assert "PHASES" not in texts
    assert "24h" in texts


def test_mermaid_and_html():
    mmd = _build_planning_mermaid(_DEFAULT_PLANNING)
    assert mmd.startswith("```mermaid")
    assert mmd.rstrip().endswith("```")
    assert "classDiagram" in mmd

    html = _build_planning_html({}, title="My Title")
    assert "<title>My Title</title>" in html
    assert "<svg" in html
    assert "classDiagram" in html
    assert "```" not in html
