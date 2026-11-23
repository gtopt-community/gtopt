"""Skip / management-factor / fcost branches of :class:`DemandWriter`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from plp2gtopt.demand_writer import DemandWriter


def _dem(bus: int, blocks: list[int], name: str = "D") -> dict[str, Any]:
    n = len(blocks)
    return {
        "number": bus,
        "name": f"{name}{bus}",
        "bus": bus,
        "blocks": np.array(blocks, dtype=np.int32),
        "values": np.full(n, 10.0),
        "stages": np.array([1] * n, dtype=np.int32),
        "energies": np.full(n, 5.0),
        "energy": 0.0,
    }


def test_all_demands_skipped_returns_empty(tmp_path: Path) -> None:
    """Regression: no demand on a bus > 0 must not crash ``pd.concat([])``."""
    writer = DemandWriter(options={"output_dir": tmp_path})
    items = [_dem(0, [1, 2]), _dem(3, [])]
    df, de = writer.to_dataframe(items)
    assert df.empty and de.empty
    assert not writer.to_json_array(items)
    assert not (tmp_path / "Demand" / "lmax.parquet").exists()


def test_management_factor_emits_emin(tmp_path: Path) -> None:
    writer = DemandWriter(options={"output_dir": tmp_path, "management_factor": 0.25})
    items = [_dem(1, [1, 2]), _dem(0, [1])]
    arr = writer.to_json_array(items)
    assert arr == [{"uid": 1, "name": "D1", "bus": 1, "lmax": "lmax", "emin": "emin"}]
    df, de = writer.to_dataframe([_dem(1, [1, 2])])
    assert df["uid:1"].tolist() == [7.5, 7.5]
    assert not de.empty


class _Costs:
    def __init__(self, by_name: dict[str, Any]) -> None:
        self.by_name = by_name

    def get_cost_by_name(self, name: str) -> Any:
        return self.by_name.get(name)


def test_fcost_dataframe_branches() -> None:
    writer = DemandWriter(options={})
    stages = SimpleNamespace(items=[{"number": 1}, {"number": 2}])
    centrals = SimpleNamespace(
        centrals=[
            {"name": "T", "type": "termica", "bus": 1},
            {"name": "F0", "type": "falla", "bus": 0, "gcost": 1.0},
            {"name": "F9", "type": "falla", "bus": 9, "gcost": 1.0},
            {"name": "F1a", "type": "falla", "bus": 1, "gcost": 100.0},
            {"name": "F1b", "type": "falla", "bus": 1, "gcost": 200.0},
            {"name": "F2", "type": "falla", "bus": 2, "gcost": 50.0},
        ]
    )
    costs = _Costs(
        {
            "F1a": {"stage": np.array([1, 1]), "cost": np.array([90.0, 80.0])},
        }
    )
    demands = [{"uid": 11, "bus": 1}, {"uid": 12, "bus": 2}, {"uid": 13, "bus": 7}]
    falla_by_bus = {1: {}, 2: {}}
    assert writer.to_fcost_dataframe(demands, falla_by_bus, None, stages, centrals)[
        0
    ].empty
    assert writer.to_fcost_dataframe([], falla_by_bus, costs, stages, centrals)[0].empty
    df, filed = writer.to_fcost_dataframe(
        demands, falla_by_bus, costs, stages, centrals
    )
    assert filed == {11}  # demand uids (== bus in real PLP cases)
    assert df["uid:11"].tolist() == [80.0, 100.0]
