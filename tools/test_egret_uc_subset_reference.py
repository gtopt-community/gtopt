# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for tools/egret_uc_subset_reference.py (no Egret needed)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import egret_uc_subset_reference as er  # pylint: disable=wrong-import-position


def _instance() -> dict:
    gen = {
        "p_min": 10,
        "p_max": 50,
        "ramp_up_60min": 5,
        "ramp_down_60min": 5,
        "startup_capacity": 10,
        "shutdown_capacity": 10,
        "startup_cost": [[2, 7], [5, 9]],
        "p_cost": json.dumps(
            {
                "data_type": "cost_curve",
                "cost_curve_type": "polynomial",
                "values": {"0": 1, "1": 2, "2": 0.5},
            }
        ),
        "agc_capable": True,
        "non_spinning_capacity": 3,
    }
    return {
        "system": {
            "reserve_requirement": [1, 2],
            "spinning_reserve_requirement": [1, 1],
            "regulation_up_requirement": [1, 1],
        },
        "elements": {
            "generator": {"g": gen},
            "area": {"A": {"flexible_ramp_up_requirement": [1, 1], "x": 1}},
        },
    }


def test_poly_cost_and_points() -> None:
    gen = _instance()["elements"]["generator"]["g"]
    assert er.poly_cost(gen) == (1.0, 2.0, 0.5)
    pts = er.pwl_points(gen, k=2)
    assert pts == [[10.0, 71.0], [30.0, 511.0], [50.0, 1351.0]]


def test_poly_cost_rejects_piecewise() -> None:
    with pytest.raises(ValueError, match="polynomial"):
        er.poly_cost({"p_cost": {"cost_curve_type": "piecewise", "values": []}})


def test_subset_s1() -> None:
    md = _instance()
    s1 = er.subset(md, "s1")
    assert set(s1["system"]) == {"reserve_requirement"}
    assert s1["elements"]["area"]["A"] == {"x": 1}
    g = s1["elements"]["generator"]["g"]
    assert g["p_cost"]["cost_curve_type"] == "piecewise"
    assert len(g["p_cost"]["values"]) == er.SEGMENTS + 1
    assert g["ramp_up_60min"] == g["startup_capacity"] == 50
    assert g["startup_cost"] == [[2, 7]]
    assert "agc_capable" not in g and "non_spinning_capacity" not in g
    # the input is untouched
    assert "spinning_reserve_requirement" in md["system"]


def test_subset_ramps_reserve_and_hot_cold() -> None:
    md = _instance()
    s2 = er.subset(md, "s2")
    assert "reserve_requirement" not in s2["system"]
    assert s2["elements"]["generator"]["g"]["ramp_up_60min"] == 5
    s3 = er.subset(md, "s3")
    assert "reserve_requirement" in s3["system"]
    assert s3["elements"]["generator"]["g"]["shutdown_capacity"] == 10
    s4 = er.subset(md, "s4")
    assert s4["elements"]["generator"]["g"]["startup_cost"] == [[2, 7], [5, 9]]
    with pytest.raises(ValueError, match="unknown variant"):
        er.subset(md, "s9")


def test_main_writes_subsets(tmp_path: Path) -> None:
    inst = tmp_path / "tiny.json"
    inst.write_text(json.dumps(_instance()), encoding="utf-8")
    out = tmp_path / "out"
    assert er.main([str(inst), "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        f"tiny_{v}.json" for v in er.VARIANTS
    ]
