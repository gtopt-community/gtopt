# SPDX-License-Identifier: BSD-3-Clause
"""Tests for the PLEXOS solution summary (fake tables, no mdbtools)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from cen2gtopt import _res_summary as rs

# Two days of 2-hour intervals mapped to 2 ST blocks per day; two buses
# and one region with two cost properties.
TABLES = {
    "t_collection": pd.DataFrame(
        {
            "collection_id": [10, 20],
            "name": ["Regions", "Nodes"],
            "parent_class_id": [1, 1],
        }
    ),
    "t_unit": pd.DataFrame({"unit_id": [1, 2], "value": ["$000", "$/MWh"]}),
    "t_property": pd.DataFrame(
        {
            "property_id": [100, 101, 200],
            "collection_id": [10, 10, 20],
            "name": ["Total Generation Cost", "Generation Cost", "Price"],
            "unit_id": [1, 1, 2],
        }
    ),
    "t_object": pd.DataFrame(
        {"object_id": [1, 2, 3], "name": ["SEN", "Polpaico220", "Other220"]}
    ),
    "t_membership": pd.DataFrame(
        {"membership_id": [11, 12, 13], "child_object_id": [1, 2, 3]}
    ),
    "t_key": pd.DataFrame(
        {
            "key_id": [1, 2, 3, 4, 9],
            "membership_id": [11, 11, 12, 13, 12],
            "property_id": [100, 101, 200, 200, 200],
            "phase_id": [3, 3, 3, 3, 1],
            "sample_id": [0, 0, 0, 0, 0],
        }
    ),
    "t_phase_3": pd.DataFrame({"interval_id": [1, 2, 3, 4], "period_id": [1, 2, 3, 4]}),
    "t_period_0": pd.DataFrame(
        {
            "interval_id": [1, 2, 3, 4],
            "datetime": [
                "2025-10-19 00:00:00",
                "2025-10-19 01:00:00",
                "2025-10-20 00:00:00",
                "2025-10-20 01:00:00",
            ],
        }
    ),
    "t_data_0": pd.DataFrame(
        {
            "key_id": [1, 1, 1, 2, 3, 3, 3, 4, 4, 9],
            "period_id": [1, 2, 3, 1, 1, 2, 3, 1, 2, 1],
            "value": [10.0, 20.0, 30.0, 5.0, 50.0, -1.0, 70.0, 40.0, 42.0, 999.0],
        }
    ),
}


def table(name: str) -> pd.DataFrame:
    return TABLES[name]


def test_summarize_costs_and_cmg() -> None:
    summary, cmg = rs.summarize(table, ref_bus="Polpaico220")
    assert summary["horizon"] == {"intervals": 4, "blocks": 3}
    tgc = summary["costs"]["Total Generation Cost"]
    assert tgc == {"horizon": 60.0, "day1": 30.0, "unit": "$000"}
    assert summary["costs"]["Generation Cost"]["day1"] == 5.0
    c = summary["cmg_day1"]
    assert c["unit"] == "$/MWh" and c["buses"] == 2
    assert c["ref_mean"] == pytest.approx(24.5)
    assert c["ref_min"] == -1.0 and c["all_max"] == 50.0
    assert c["bus_hours_negative"] == 1
    assert list(cmg["bus"]) == ["Other220", "Polpaico220"]
    assert list(cmg["hours"]) == [2, 2]


def test_summarize_requires_price() -> None:
    tables = dict(TABLES)
    tables["t_property"] = TABLES["t_property"].iloc[:2]
    with pytest.raises(ValueError):
        rs.summarize(tables.__getitem__)


def test_write_summary_and_extract_accdb(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Model X Solution/Model X Solution.accdb", b"db")
    res = tmp_path / "RES20251019.zip"
    res.write_bytes(buf.getvalue())
    assert rs.extract_accdb(res, tmp_path).read_bytes() == b"db"

    summary, cmg = rs.summarize(table)
    rs.write_summary(summary, cmg, tmp_path / "out", res)
    doc = json.loads((tmp_path / "out" / "plexos_summary.json").read_text())
    assert doc["source"]["file"] == "RES20251019.zip"
    assert len(doc["source"]["sha256"]) == 64
    assert (
        (tmp_path / "out" / "plexos_cmg_by_bus.csv")
        .read_text()
        .startswith("bus,hours,mean")
    )
