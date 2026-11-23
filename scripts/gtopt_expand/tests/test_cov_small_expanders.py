# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for pumped_storage_expand and ror_expand."""

from __future__ import annotations

import json

import pytest

from gtopt_expand import pumped_storage_expand as ps
from gtopt_expand.ror_expand import expand_ror_from_file


def test_find_reservoir_skips_non_dicts():
    assert ps._find_reservoir(["junk", {"name": "A"}], "A") == {"name": "A"}
    assert ps._find_reservoir(["junk"], "A") is None


def test_resolve_vmin_vmax_from_emin_emax():
    assert ps._resolve_vmin_vmax({"emin": 1, "emax": 2}, "UP", [], "u") == (1.0, 2.0)


def test_expand_requires_config_and_nonblank_name():
    with pytest.raises(ValueError, match="requires a config"):
        ps.expand_pumped_storage(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cannot be empty"):
        ps.expand_pumped_storage(ps.default_config(name="x"), name="   ")


def test_ror_skips_unknown_generators(tmp_path):
    csv = tmp_path / "ror.csv"
    csv.write_text(
        "name,enabled,vmax_hm3,production_factor,pmax_mw\nA,true,1,2,3\nB,true,1,2,3\n",
        encoding="utf-8",
    )
    planning = tmp_path / "p.json"
    planning.write_text(
        json.dumps({"system": {"generator_array": [{"name": "A"}]}}), encoding="utf-8"
    )
    out = expand_ror_from_file(csv_path=csv, planning_path=planning, selection="all")
    assert [p["name"] for p in out["promoted"]] == ["A"]


def test_ror_value_parsers():
    from gtopt_expand import ror_expand as rx

    assert rx._is_enabled("  ") is True
    kw = {"name": "C", "line": 2, "field": "vmax_hm3"}
    with pytest.raises(ValueError, match="missing vmax_hm3"):
        rx._parse_positive_float("", **kw)
    with pytest.raises(ValueError, match="non-numeric"):
        rx._parse_positive_float("x", **kw)
    with pytest.raises(ValueError, match="must be > 0"):
        rx._parse_positive_float("-1", **kw)
    assert rx._parse_optional_positive_float(None, **kw) is None
    assert rx._parse_optional_positive_float(" ", **kw) is None
    with pytest.raises(ValueError, match="non-numeric"):
        rx._parse_optional_positive_float("x", **kw)
    with pytest.raises(ValueError, match="must be > 0"):
        rx._parse_optional_positive_float("0", **kw)
    with pytest.raises(ValueError, match="empty name column"):
        rx._rows_to_spec([{"name": " "}], source="s")
