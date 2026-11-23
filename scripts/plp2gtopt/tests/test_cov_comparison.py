"""Branch coverage of :func:`plp2gtopt._comparison._plp_indicators`."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from plp2gtopt._comparison import _plp_element_counts, _plp_indicators


class _Centrals:
    def __init__(self, centrals: list[dict[str, Any]]) -> None:
        self.centrals = centrals
        self.num_centrals = len(centrals)
        self.centrals_of_type: dict[str, list[dict[str, Any]]] = {}
        for c in centrals:
            self.centrals_of_type.setdefault(c["type"], []).append(c)

    def get_central_by_name(self, name: str) -> dict[str, Any] | None:
        return next((c for c in self.centrals if c["name"] == name), None)

    @staticmethod
    def avg_falla_cost() -> float:
        return 123.0


class _Blocks:
    num_blocks = 2

    @staticmethod
    def get_item_by_number(num: int) -> dict[str, Any] | None:
        return {"duration": 2.0} if num == 1 else None


def _flow(name: str, data: Any, nh: int = 2) -> dict[str, Any]:
    return {
        "name": name,
        "flow": data,
        "block": np.array([1, 2]),
        "num_hydrologies": nh,
    }


def _parser() -> SimpleNamespace:
    centrals = [
        {"name": "F", "type": "falla", "bus": 1, "pmax": 9.0},
        {"name": "Z", "type": "termica", "bus": 0, "pmax": 9.0, "emin": "x"},
        {"name": "Q", "type": "termica", "bus": 1, "pmax": "ref"},
        {"name": "H", "type": "embalse", "bus": 1, "pmax": 10.0, "emin": 5.0},
        {"name": "T", "type": "termica", "bus": 1, "pmax": 20.0, "pmin": 2.0},
        {"name": "S", "type": "pasada", "bus": 1, "pmax": 5.0, "afluent": 1.0},
        {"name": "B", "type": "bateria", "bus": 1, "pmax": 3.0},
        {"name": "B_LOAD", "type": "termica", "bus": 1, "pmax": 0.0},
        {"name": "G", "type": "termica", "bus": 1, "pmax": 4.0},
        {"name": "R", "type": "embalse", "bus": 1, "pmax": 1.0, "emin": 7.0},
        {"name": "P0", "type": "pasada", "bus": 0, "pmax": 0.0},
    ]
    lines = [
        {"name": "L0", "bus_a": 0, "bus_b": 1},
        {"name": "Lsame", "bus_a": 1, "bus_b": 1},
        {"name": "Lman", "bus_a": 1, "bus_b": 2},
        {"name": "L1", "bus_a": 1, "bus_b": 2, "tmax_ab": 10.0, "tmax_ba": "x"},
    ]
    demands = [
        {"blocks": [1, 2, 3], "values": [5.0, 6.0]},
        {"blocks": [1, 9], "values": [1.0, 1.0]},
        {"blocks": None, "values": None},
    ]
    flows = [
        _flow("Ghost", np.ones((2, 2))),
        _flow("P0", np.ones((2, 2))),
        _flow("H", None),
        _flow("H", np.ones((2, 2)), nh=0),
        _flow("S", np.ones((2, 2))),  # equals fill value -> skipped
        _flow("H", np.array([[2.0, 4.0], [6.0, 8.0]])),
    ]
    return SimpleNamespace(
        parsed_data={
            "central_parser": _Centrals(centrals),
            "line_parser": SimpleNamespace(lines=lines, num_lines=4),
            "manli_parser": SimpleNamespace(manlis=[{"name": "Lman"}, {"name": ""}]),
            "demand_parser": SimpleNamespace(demands=demands, num_demands=3),
            "block_parser": _Blocks(),
            "aflce_parser": SimpleNamespace(
                flows=flows, items=[{"num_hydrologies": 2}]
            ),
        }
    )


_PLANNING = {
    "system": {
        "generator_array": [
            {"name": "G", "type": "solar", "pmin": "sched"},
            {"name": "T", "type": "thermal", "pmin": 1.5},
        ],
        "reservoir_array": [{"name": "R", "emin": "emin"}],
        "battery_array": [{"name": "B"}],
    }
}


def test_plp_indicators_full() -> None:
    ind = _plp_indicators(_parser(), None, _PLANNING)  # type: ignore[arg-type]
    assert ind["total_gen_capacity_mw"] == pytest.approx(43.0)
    assert ind["renewable_capacity_mw"] == pytest.approx(4.0)
    assert ind["hydro_capacity_mw"] == pytest.approx(16.0)
    assert ind["total_gen_min_stable_mw"] == pytest.approx(1.5)
    assert ind["total_reservoir_min_vol"] == pytest.approx(5.0)
    assert ind["total_line_capacity_mw"] == pytest.approx(10.0)
    assert ind["first_block_demand_mw"] == pytest.approx(6.0)
    assert ind["last_block_demand_mw"] == pytest.approx(6.0)
    assert ind["total_energy_mwh"] == pytest.approx(18.0)
    assert ind["first_block_affluent_avg"] == pytest.approx(3.0)
    assert ind["avg_fcost"] == pytest.approx(123.0)


def test_plp_indicators_hydrology_filter() -> None:
    ind = _plp_indicators(_parser(), [1], None)  # type: ignore[arg-type]
    assert ind["first_block_affluent_avg"] == pytest.approx(4.0)
    ind = _plp_indicators(_parser(), [7], None)  # type: ignore[arg-type]
    assert ind["first_block_affluent_avg"] == 0.0


def test_plp_indicators_empty() -> None:
    ind = _plp_indicators(SimpleNamespace(parsed_data={}))  # type: ignore[arg-type]
    assert ind["total_gen_capacity_mw"] == 0.0
    assert "avg_fcost" not in ind


def test_element_counts_without_centrals() -> None:
    parser = _parser()
    del parser.parsed_data["central_parser"]
    counts = _plp_element_counts(parser)  # type: ignore[arg-type]
    assert counts["hydrologies"] == 2
    counts = _plp_element_counts(_parser())  # type: ignore[arg-type]
    assert counts["sub_embalse"] == 2
