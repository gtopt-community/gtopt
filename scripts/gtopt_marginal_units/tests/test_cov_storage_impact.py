# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge cases of ``gtopt-marginal-units storage-impact``."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from gtopt_marginal_units import storage_impact as si

KEY = ["scenario", "stage", "block"]


def _plan(system: dict) -> dict:
    return {
        "simulation": {
            "block_array": [{"uid": 1, "duration": 2}],
            "scenario_array": [{"uid": 1, "probability_factor": 0}],
        },
        "system": system,
    }


def test_ref_to_uid_forms():
    by_name = {"b": 5}
    assert si._ref_to_uid(None, by_name) is None
    assert si._ref_to_uid(3, by_name) == 3
    assert si._ref_to_uid("b", by_name) == 5
    assert si._ref_to_uid("uid:8", by_name) == 8
    assert si._ref_to_uid("12", by_name) == 12
    assert si._ref_to_uid("zz", by_name) is None
    assert si._ref_to_uid(1.5, by_name) is None


def test_storage_units_resolution_paths(caplog):
    system = {
        "bus_array": [{"uid": 1, "name": "b1"}],
        "generator_array": [{"uid": 40, "name": "g", "bus": None}],
        "demand_array": [{"uid": 30, "name": "d", "bus": "b1"}],
        "battery_array": [
            {"uid": 7, "name": "via_demand"},
            {"uid": 8, "name": "orphan"},
        ],
        "converter_array": [
            {"battery": "via_demand", "generator": "g", "demand": "d"},
            {"battery": "unknown"},
        ],
    }
    units = si.storage_units(_plan(system))
    assert [(u.uid, u.bus_uid, u.gen_uid, u.dem_uid) for u in units] == [(7, 1, 40, 30)]
    assert "battery orphan has no bus" in caplog.text
    assert si.scenario_weights(_plan(system)) == {1: 0.0}


def test_series_missing_uid_or_table(tmp_path: Path):
    assert list(si._series(tmp_path, "Generator/generation_sol", None).columns) == [
        *KEY,
        "mw",
    ]
    assert si._series(tmp_path, "Generator/generation_sol", 3).empty


def test_dispatch_skips_units_without_output(tmp_path: Path):
    unit = si.StorageUnit(uid=7, name="b", bus_uid=1, gen_uid=None, dem_uid=None)
    out = si.storage_dispatch(tmp_path, _plan({}), [unit])
    assert out.empty
    assert "net_mwh" in out.columns


def test_emission_impact_missing_mer_warns(caplog):
    unit = si.StorageUnit(uid=7, name="b", bus_uid=1, gen_uid=None, dem_uid=None)
    dispatch = pd.DataFrame(
        {
            "storage_uid": [7],
            "scenario": [1],
            "stage": [1],
            "block": [1],
            "discharge_mwh": [2.0],
            "charge_mwh": [0.0],
            "net_mwh": [2.0],
        }
    )
    mer = pd.DataFrame(columns=[*KEY, "bus_uid", "mer_t_per_mwh"]).astype(float)
    out = si.emission_impact(dispatch, mer, [unit])
    assert out["impact_t"].tolist() == [-0.0]
    assert "have no MER" in caplog.text


def test_read_mer_from_dataset(tmp_path: Path):
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    pd.DataFrame(
        {
            "scenario": [1.0],
            "stage": [1.0],
            "block": [1.0],
            "bus_uid": [3.0],
            "recomputed_emission_intensity": [float("nan")],
        }
    ).to_parquet(tmp_path / "bus_emission_intensity_recipe.parquet")
    mer = si.read_mer(tmp_path)
    assert mer.to_dict("records") == [
        {"scenario": 1, "stage": 1, "block": 1, "bus_uid": 3, "mer_t_per_mwh": 0.0}
    ]


def test_summarise_unit_without_dispatch():
    unit = si.StorageUnit(uid=9, name="idle", bus_uid=1, gen_uid=None, dem_uid=None)
    empty = pd.DataFrame(
        columns=[
            "storage_uid",
            "scenario",
            "discharge_mwh",
            "charge_mwh",
            "impact_t",
            "mer_t_per_mwh",
        ]
    ).astype(float)
    row = si.summarise(empty, empty, [unit], {}).iloc[0]
    assert row["PE_t"] == 0.0 and row["BE_t"] == 0.0


def test_main_errors_and_baseline(tmp_path: Path, monkeypatch, capsys):
    with pytest.raises(FileNotFoundError, match="pass --planning"):
        si.main(["--project-output", str(tmp_path), "--mer", str(tmp_path)])
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan({"bus_array": []})), encoding="utf-8")
    assert (
        si.main(
            [
                "--project-output",
                str(tmp_path),
                "--mer",
                str(tmp_path),
                "--planning",
                str(plan_path),
            ]
        )
        == 1
    )
    # one battery with direct bus, project dispatch only; baseline empty
    system = {
        "bus_array": [{"uid": 1, "name": "b1"}],
        "battery_array": [{"uid": 7, "name": "bat", "bus": "b1"}],
    }
    plan_path.write_text(json.dumps(_plan(system)), encoding="utf-8")
    proj = tmp_path / "proj"
    (proj / "Battery").mkdir(parents=True)
    pd.DataFrame(
        {"scenario": [1], "stage": [1], "block": [1], "uid": [7], "value": [3.0]}
    ).to_csv(proj / "Battery/fout_sol.csv", index=False)
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setattr(
        si,
        "read_mer",
        lambda _p: pd.DataFrame(
            {
                "scenario": [1],
                "stage": [1],
                "block": [1],
                "bus_uid": [1],
                "mer_t_per_mwh": [0.5],
            }
        ),
    )
    table = tmp_path / "t.csv"
    rc = si.main(
        [
            "--project-output",
            str(proj),
            "--baseline-output",
            str(base),
            "--mer",
            str(tmp_path),
            "--planning",
            str(plan_path),
            "--csv",
            str(table),
        ]
    )
    assert rc == 0
    assert pd.read_csv(table)["run"].tolist() == ["project"]
    assert "ER_t" in capsys.readouterr().out
