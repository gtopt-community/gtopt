# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Planning / output readers of gtopt_pricing_uplift."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

from gtopt_pricing_uplift._data import (
    Period,
    load_case,
    load_series,
    read_pricing_csv,
    resolve_ref,
    sched_value,
)

P = Period(scenario=1, stage=2, block=5, duration=1.0, stage_index=1, block_index=0)


def test_sched_value_inline_forms(tmp_path: Path) -> None:
    kw = {"class_name": "Generator", "input_dir": tmp_path}
    assert sched_value(None, P, 1, **kw) is None
    assert sched_value(True, P, 1, **kw) is None
    assert sched_value(7, P, 1, **kw) == 7.0
    assert sched_value([1.0, 2.0], P, 1, **kw) == 2.0  # per stage
    assert sched_value([3.0], P, 1, **kw) == 3.0  # clamped
    assert sched_value([[1.0, 2.0], [4.0, 5.0]], P, 1, **kw) == 4.0
    assert sched_value([], P, 1, **kw) is None
    assert sched_value([[], []], P, 1, **kw) is None
    assert sched_value({"x": 1}, P, 1, **kw) is None


def test_sched_value_tables(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    gdir = tmp_path / "Generator"
    gdir.mkdir()
    pd.DataFrame(
        {
            "stage": [2, 2, 1],
            "block": [5, 6, 5],
            "uid": [1, 1, 1],
            "value": [11.0, 12.0, 13.0],
        }
    ).to_csv(gdir / "gcost.csv", index=False)
    pd.DataFrame(
        {"stage": [2], "block": [5], "uid:1": [21.0], "uid:2": [22.0]}
    ).to_parquet(gdir / "pmax.parquet")
    pd.DataFrame({"uid": [1], "value": [31.0]}).to_csv(
        tmp_path / "Other.csv", index=False
    )
    (tmp_path / "Other").mkdir()
    pd.DataFrame({"uid": [1], "value": [41.0]}).to_csv(
        tmp_path / "Other" / "x.csv", index=False
    )
    kw = {"class_name": "Generator", "input_dir": tmp_path}
    assert sched_value("gcost", P, 1, **kw) == 11.0
    assert sched_value("pmax", P, 2, **kw) == 22.0
    assert sched_value("Other@x", P, 1, **kw) == 41.0
    assert sched_value("gcost", P, 9, **kw) is None  # uid absent
    with caplog.at_level(logging.WARNING):
        assert sched_value("missing", P, 1, **kw) is None
    assert "not found" in caplog.text


def test_resolve_ref() -> None:
    items = [{"uid": 3, "name": "a"}, {"uid": 4, "name": "b"}]
    assert resolve_ref("b", items) == 4
    assert resolve_ref(3, items) == 3
    assert resolve_ref("7", items) == 7
    assert resolve_ref("zz", items) is None
    assert resolve_ref(True, items) is None
    assert resolve_ref(None, items) is None


def test_load_case_defaults(tmp_path: Path) -> None:
    plan = {
        "simulation": {"block_array": [{"uid": 1, "duration": 2}, {"uid": 2}]},
        "system": {"bus_array": [{"uid": 5, "name": "n5"}]},
    }
    path = tmp_path / "p.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    case = load_case(path)
    assert case.scenario_weight == {1: 1.0}
    assert [(p.block, p.duration) for p in case.periods[1]] == [(1, 2.0), (2, 1.0)]
    assert case.bus_uid == {"n5": 5}
    assert case.input_dir == tmp_path


def test_load_case_scenario_weights(tmp_path: Path) -> None:
    plan = {
        "simulation": {
            "block_array": [{"uid": 1}],
            "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
            "scenario_array": [
                {"uid": 1, "probability_factor": 3},
                {"uid": 2, "probability_factor": 1},
            ],
        },
        "options": {"input_directory": "inp"},
    }
    path = tmp_path / "p.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    case = load_case(path, case_dir=tmp_path / "case")
    assert case.scenario_weight == {1: 0.75, 2: 0.25}
    assert case.input_dir == tmp_path / "case" / "inp"


def test_load_series_layouts(tmp_path: Path) -> None:
    assert load_series(tmp_path, "Bus/balance_dual") is None
    bus = tmp_path / "Bus"
    bus.mkdir()
    # Wide layout, no scenario/stage columns.
    pd.DataFrame(
        {"block": [1, 2], "uid:1": [5.0, None], "uid:2": [6.0, 7.0]}
    ).to_parquet(bus / "balance_dual.parquet")
    s = load_series(tmp_path, "Bus/balance_dual")
    assert s == {(1, 1, 1, 1): 5.0, (1, 1, 1, 2): 6.0, (1, 1, 2, 2): 7.0}


def test_read_pricing_csv(tmp_path: Path) -> None:
    assert not read_pricing_csv(tmp_path)
    (tmp_path / "pricing.csv").write_text(
        "scene,applied_mode\n1,fixed\n", encoding="utf-8"
    )
    assert read_pricing_csv(tmp_path) == [{"scene": "1", "applied_mode": "fixed"}]
