# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for gtopt_expand.pmin_flowright_expand."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from gtopt_expand import pmin_flowright_expand as pf

_CSV = "name,enabled,description\nPANGUE,true,x\n"


def _csv(tmp_path: Path, content: str = _CSV) -> Path:
    path = tmp_path / "p.csv"
    path.write_text(content, encoding="utf-8")
    return path


def _planning(**kw: Any) -> dict[str, Any]:
    gen = {"name": "PANGUE", "pmin": kw.get("pmin", 10.0)}
    if "pmin" in kw and kw["pmin"] is None:
        gen.pop("pmin")
    tur = {"name": "PANGUE", "waterway": kw.get("waterway", "PANGUE_gen")}
    tur["production_factor"] = kw.get("rendi", 2.0)
    ww = {"name": "PANGUE_gen", "junction_a": "PANGUE"}
    if kw.get("junction_b", "DOWN") is not None:
        ww["junction_b"] = kw.get("junction_b", "DOWN")
    return {
        "system": {
            "generator_array": kw.get("generators", [gen]),
            "turbine_array": kw.get("turbines", [tur]),
            "waterway_array": kw.get("waterways", [ww]),
            **kw.get("extra", {}),
        }
    }


def test_is_enabled_and_empty_spec(tmp_path):
    assert not pf._is_enabled(None)
    assert not pf._is_enabled("   ")
    assert pf._is_enabled(" Yes ") == ("yes" in pf._TRUTHY)
    path = _csv(tmp_path, "name,enabled\nA,false\nB,\n")
    assert pf.expand_pmin_flowright(_planning(), csv_path=path) == 0


def test_empty_name_row_raises(tmp_path):
    with pytest.raises(ValueError, match="empty name column"):
        pf.parse_pmin_flowright_file(_csv(tmp_path, "name,enabled\n ,true\n"))


def test_scale_pmin_shapes():
    with pytest.raises(ValueError, match="production_factor must be > 0"):
        pf._scale_pmin(1.0, rendi=0.0, central_name="C")
    assert pf._scale_pmin(None, rendi=1.0, central_name="C") == (0.0, None)
    with pytest.raises(ValueError, match="boolean"):
        pf._scale_pmin(True, rendi=1.0, central_name="C")
    col, todo = pf._scale_pmin("pmin", rendi=1.0, central_name="C")
    assert col == f"C{pf._FLOW_RIGHT_SUFFIX}" and "parquet reference" in todo
    assert pf._scale_pmin([[2.0, 4]], rendi=2.0, central_name="C") == (
        [[[1.0, 2.0]]],
        None,
    )
    with pytest.raises(ValueError, match=r"pmin\[0\] is not a list"):
        pf._scale_pmin([1.0], rendi=1.0, central_name="C")
    with pytest.raises(ValueError, match="is not numeric"):
        pf._scale_pmin([["x"]], rendi=1.0, central_name="C")
    with pytest.raises(ValueError, match="unsupported pmin shape"):
        pf._scale_pmin({"a": 1}, rendi=1.0, central_name="C")


def test_ensure_bypass_paths():
    assert pf.ensure_bypass_for_flowrights({}) == 0
    system = {
        "junction_array": [
            "junk",
            {"name": "J_spill"},
            {"name": "L_spill", "drain": True},
        ],
        "flow_right_array": [
            "junk",
            {"uid": 1},  # not soft
            {"uid": 2, "fcost": 1.0, "junction_b": "X"},  # already wired
            {"uid": 3, "fcost": 1.0, "junction_a": ""},  # bad junction
            {"uid": 4, "fail_cost": 1.0, "junction_a": "J"},  # legacy spill reuse
            {"uid": 5, "fcost": 1.0, "junction_a": "K"},  # no drain → skipped
            {"uid": 6, "fcost": 1.0, "junction_a": "L"},  # drained spill reuse
        ],
    }
    assert pf.ensure_bypass_for_flowrights(system) == 2
    assert system["junction_array"][1]["drain"] is True
    assert system["flow_right_array"][4]["junction_b"] == "J_spill"
    assert "junction_b" not in system["flow_right_array"][5]


@pytest.mark.parametrize(
    ("planning", "match"),
    [
        ({"system": []}, "missing 'system'"),
        ({"system": {"generator_array": {}}}, "generator_array"),
        ({"system": {"generator_array": [], "turbine_array": {}}}, "turbine_array"),
        (
            {
                "system": {
                    "generator_array": [],
                    "turbine_array": [],
                    "waterway_array": 1,
                }
            },
            "waterway_array",
        ),
        (
            {
                "system": {
                    "generator_array": [],
                    "turbine_array": [],
                    "waterway_array": [],
                    "flow_right_array": {},
                }
            },
            "flow_right_array",
        ),
    ],
)
def test_expand_shape_errors(tmp_path, planning, match):
    with pytest.raises(ValueError, match=match):
        pf.expand_pmin_flowright(planning, csv_path=_csv(tmp_path))


@pytest.mark.parametrize(
    ("kw", "log"),
    [
        ({"generators": []}, "generator 'PANGUE' not found"),
        ({"turbines": []}, "turbine 'PANGUE' not found"),
        ({"waterway": ""}, "has no 'waterway' field"),
        ({"waterways": []}, "not found in waterway_array"),
        ({"rendi": "abc"}, "non-numeric"),
        ({"rendi": 0.0}, "non-positive"),
        ({"junction_b": None}, "has no 'junction_b'"),
        ({"pmin": None}, "has no pmin set"),
        ({"pmin": True}, "boolean"),
    ],
)
def test_expand_skips(tmp_path, caplog, kw, log):
    with caplog.at_level(logging.INFO, logger=pf.__name__):
        assert pf.expand_pmin_flowright(_planning(**kw), csv_path=_csv(tmp_path)) == 0
    assert log in caplog.text


def test_expand_parquet_ref_logs_todo(tmp_path, caplog):
    planning = _planning(pmin="pmin")
    with caplog.at_level(logging.WARNING, logger=pf.__name__):
        assert pf.expand_pmin_flowright(planning, csv_path=_csv(tmp_path)) == 1
    assert "TODO" in caplog.text
    fr = planning["system"]["flow_right_array"][0]
    assert fr["target"] == f"PANGUE{pf._FLOW_RIGHT_SUFFIX}"
