"""Edge branches of :class:`plp2gtopt.aflce_writer.AflceWriter`."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest

from plp2gtopt.aflce_writer import AflceWriter


class _Centrals:
    def __init__(self, units: dict[str, dict[str, Any]]) -> None:
        self.units = units

    def get_item_by_name(self, name: str) -> Any:
        return self.units.get(name)


def _flow(name: str, data: Any, blocks: Any = (1, 2)) -> dict[str, Any]:
    return {"name": name, "flow": data, "block": list(blocks)}


def test_to_json_array_and_empty() -> None:
    w = AflceWriter(options={})
    assert w.to_json_array([]) == []
    assert w.to_dataframe([]).empty
    item = {"name": "A", "block": np.array([1]), "flow": np.array([[1.0]])}
    assert w.to_json_array([item]) == [{"name": "A", "block": [1], "flow": [[1.0]]}]


def test_no_valid_scenarios_or_items() -> None:
    w = AflceWriter(options={}, scenarios=[{"uid": 1, "hydrology": -1}])
    assert w.to_dataframe([_flow("A", np.ones((2, 1)))]).empty
    w = AflceWriter(options={}, scenarios=[{"uid": 1, "hydrology": 0}])
    assert w.to_dataframe([_flow("A", np.ones((2, 1)))]).empty  # no central_parser


def test_filters_and_unscale_without_blocks() -> None:
    centrals = _Centrals(
        {
            "A": {"number": 1, "afluent": 1.0},
            "B": {"afluent": 2.0},  # no number -> name-keyed column
            "C": {"number": 3},
            "D": {"number": 4, "afluent": 5.0},  # constant == fill -> dropped
        }
    )
    w = AflceWriter(
        central_parser=centrals,  # type: ignore[arg-type]
        scenarios=[{"uid": 1, "hydrology": 0}],
        options={},
        pasada_unscale_map={"A": 0.5, "B": 1.0},
    )
    items = [
        _flow("GHOST", np.ones((2, 1))),
        _flow("C", None),
        _flow("C", np.ones((2, 1)), blocks=()),
        _flow("A", np.array([[4.0], [6.0]])),
        _flow("B", np.array([[1.0], [3.0]])),
        _flow("D", np.full((2, 1), 5.0)),
    ]
    df = w.to_dataframe(items)
    assert isinstance(df, pd.DataFrame)
    assert df["stage"].tolist() == [-1, -1]
    assert df["uid:1"].tolist() == pytest.approx([2.0, 3.0])
    assert not any("4" in c for c in df.columns if c.startswith("uid:"))


def test_scenario_values_1d_and_fallback() -> None:
    f = AflceWriter._scenario_values  # pylint: disable=protected-access
    one_d = np.array([1.0, 2.0])
    assert f(one_d, 0, None, np.array([1, 2]), None) is one_d
    two_d = np.array([[1.0, 2.0], [3.0, 4.0]])
    got = f(two_d, 0, {1: 1, 2: 7}, np.array([1, 2]), {1: 1, 2: 2})
    assert got.tolist() == [2.0, 3.0]  # stage 2 col 7 out of range -> static
