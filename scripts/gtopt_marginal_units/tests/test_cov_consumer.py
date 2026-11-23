# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""MarginalUnitDataset queries on a hand-written synthetic dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from gtopt_marginal_units import recipes
from gtopt_marginal_units._consumer import MarginalUnitDataset


def _write_dataset(root: Path, extras: dict[str, Any] | None = None) -> Path:
    (root / "attribution").mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps({"producer": "x", "extras": extras or {}}), encoding="utf-8"
    )
    key = {"scenario": [1, 1], "stage": [1, 2], "block": [1, 1]}
    pd.DataFrame(
        {**key, "bus_uid": [1, 1], "zone_lmp": [10.0, 20.0], "gen_uid": [5, 6]}
    ).to_parquet(root / "attribution/per_bus.parquet")
    pd.DataFrame({**key, "zone_id": [0, 0], "zone_lmp": [10.0, 20.0]}).to_parquet(
        root / "attribution/per_zone.parquet"
    )
    pd.DataFrame(
        {
            **key,
            "bus_uid": [1, 1],
            "marginal_gen_uids": [[5], []],
            "marginal_weights": [[1.0], []],
            "formula_constant": [0.0, 99.0],
            "recomputed_lmp": [10.0, 99.0],
        }
    ).to_parquet(root / "bus_price_recipe.parquet")
    pd.DataFrame(
        {
            **key,
            "bus_uid": [1, 1],
            "marginal_gen_uids": [[5], None],
            "marginal_weights": [[1.0], None],
            "formula_constant": [0.0, 0.0],
            "recomputed_emission_intensity": [0.5, 0.0],
        }
    ).to_parquet(root / "bus_emission_intensity_recipe.parquet")
    pd.DataFrame(
        {
            "scenario": [1, 1, 1, 1],
            "stage": [1, 1, 1, 2],
            "block": [1, 1, 1, 1],
            "zone_id": [0, 0, 0, 0],
            "rank": [0, 1, -1, 0],
            "gen_uid": [5, 6, 7, 6],
            "gen_name": ["a", "b", "c", "b"],
            "hypothetical_lmp": [10.0, 30.0, 5.0, 20.0],
        }
    ).to_parquet(root / "merit_ladder.parquet")
    return root


def test_open_errors_and_repr(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="manifest.json"):
        MarginalUnitDataset(tmp_path)
    ds = MarginalUnitDataset.open(_write_dataset(tmp_path / "d"))
    assert "MarginalUnitDataset(root=" in repr(ds)
    assert recipes.MarginalUnitDataset is MarginalUnitDataset


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, 0.0), ("2.5", 2.5), ("junk", None)]
)
def test_carbon_price_manifest(tmp_path: Path, raw: Any, expected: float | None):
    ds = MarginalUnitDataset(
        _write_dataset(tmp_path, {"carbon_price_usd_per_ton": raw})
    )
    if expected is None:
        with pytest.raises(ValueError, match="not coercible"):
            ds.carbon_price_usd_per_ton()
    else:
        assert ds.carbon_price_usd_per_ton() == expected


def test_bus_lmp_stage_filter_and_carbon(tmp_path: Path):
    ds = MarginalUnitDataset(_write_dataset(tmp_path))
    assert ds.bus_lmp(stage=2)["zone_lmp"].tolist() == [20.0]
    with pytest.raises(ValueError, match="non-negative"):
        ds.bus_lmp(carbon_price=-1.0)
    out = ds.bus_lmp(stage=1, carbon_price=10.0)
    assert out["lmp_with_carbon"].tolist() == [15.0]


def test_bus_lmp_carbon_without_emission_table(tmp_path: Path):
    root = _write_dataset(tmp_path)
    (root / "bus_emission_intensity_recipe.parquet").unlink()
    ds = MarginalUnitDataset(root)
    out = ds.bus_lmp(carbon_price=10.0)
    assert "lmp_with_carbon" not in out.columns
    assert ds.bus_emission_intensity().empty
    # combined view falls back to the non-empty LMP frame
    assert list(ds.bus_lmp_and_emission().columns)[-1] == "zone_lmp"
    with pytest.raises(FileNotFoundError, match="recipe table missing"):
        ds.recompute_emission({5: 1.0})
    # recompute_lmp with carbon but no emission recipe returns plain LMPs
    assert "lmp_with_carbon" not in ds.recompute_lmp({5: 1.0}, carbon_price=5.0)


def test_marginal_units_filters(tmp_path: Path):
    ds = MarginalUnitDataset(_write_dataset(tmp_path))
    assert len(ds.marginal_units()) == 2
    assert len(ds.marginal_units(bus_uid=1, cell_key=(1, 2, 1))) == 1
    assert len(ds.marginal_units(cell_key=(None,))) == 2


def test_merit_ladder_and_outage(tmp_path: Path):
    ds = MarginalUnitDataset(_write_dataset(tmp_path))
    assert len(ds.merit_ladder()) == 4
    sub = ds.merit_ladder(cell_key=(1, 1, 1), zone_id=0, depth=0)
    assert sub["gen_uid"].tolist() == [5]
    assert len(ds.merit_ladder(cell_key=(None,))) == 4
    out = ds.outage_sensitivity(5)
    assert out["replacing_gen_uid"].tolist() == [6]
    assert out["lmp_post_outage"].tolist() == [30.0]
    assert ds.outage_sensitivity(42).empty
    (tmp_path / "merit_ladder.parquet").unlink()
    assert ds.merit_ladder().empty
    assert ds.outage_sensitivity(5).empty


def test_recompute_lmp_paths(tmp_path: Path):
    ds = MarginalUnitDataset(_write_dataset(tmp_path))
    plain = ds.recompute_lmp({5: 12.0})
    assert plain["zone_lmp_recomputed"].tolist() == [12.0, 99.0]
    assert plain["lmp_delta"].tolist() == [2.0, 0.0]
    missing = ds.recompute_lmp({})
    assert pd.isna(missing["zone_lmp_recomputed"].iloc[0])
    captured = ds.recompute_lmp({5: 12.0}, carbon_price=10.0)
    assert captured["lmp_with_carbon"].tolist() == [17.0, 99.0]
    override = ds.recompute_lmp({5: 12.0}, carbon_price=10.0, unit_emissions={5: 1.0})
    assert override["lmp_with_carbon"].tolist() == [22.0, 99.0]


def test_recompute_lmp_emission_column_absent(tmp_path: Path):
    root = _write_dataset(tmp_path)
    em = pd.read_parquet(root / "bus_emission_intensity_recipe.parquet")
    em = em.drop(columns=["recomputed_emission_intensity"])
    em.to_parquet(root / "bus_emission_intensity_recipe.parquet")
    ds = MarginalUnitDataset(root)
    out = ds.recompute_lmp({5: 1.0}, carbon_price=10.0)
    assert "lmp_with_carbon" not in out.columns
    rec = ds.recompute_emission({5: 2.0})
    assert "emission_delta" not in rec.columns


def test_bus_lmp_and_emission_variants(tmp_path: Path):
    ds = MarginalUnitDataset(_write_dataset(tmp_path))
    out = ds.bus_lmp_and_emission(stage=1, carbon_price=4.0)
    assert out["lmp_with_carbon"].tolist() == [12.0]
    assert "lmp_with_carbon" not in ds.bus_lmp_and_emission()
    # per_bus without zone_lmp -> emission frame returned
    pd.DataFrame({"bus_uid": [1]}).to_parquet(tmp_path / "attribution/per_bus.parquet")
    em_only = ds.bus_lmp_and_emission()
    assert "emission_intensity_t_per_mwh" in em_only.columns
