"""Fallback branches of :class:`plp2gtopt._writer_network.NetworkMixin`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from plp2gtopt import _writer_network
from plp2gtopt.gtopt_writer import GTOptWriter


def _writer(parsed: dict[str, Any]) -> GTOptWriter:
    return GTOptWriter(SimpleNamespace(parsed_data=parsed))  # type: ignore[arg-type]


class _Demands:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items

    def get_all(self) -> list[dict[str, Any]]:
        return self.items

    def __bool__(self) -> bool:
        return True


class _Buses:
    def __init__(self) -> None:
        self.by_name = {"B1": {"number": 1}, "B2": {"number": 2}}

    def get_bus_by_name(self, name: str) -> Any:
        return self.by_name.get(name)

    def __bool__(self) -> bool:
        return True


def _dem(name: str) -> dict[str, Any]:
    return {
        "number": 1,
        "name": name,
        "blocks": np.array([1], dtype=np.int32),
        "values": np.array([3.0]),
        "stages": np.array([1], dtype=np.int32),
        "energies": np.array([3.0]),
        "energy": 0.0,
    }


class _Centrals:
    def __init__(self, centrals: list[dict[str, Any]], avg: Any) -> None:
        self.centrals = centrals
        self._avg = avg

    def avg_falla_cost(self) -> Any:
        if isinstance(self._avg, Exception):
            raise self._avg
        return self._avg


def test_demands_early_returns() -> None:
    w = _writer({})
    w.process_demands({})
    w = _writer({"demand_parser": _Demands([])})
    w.process_demands({})
    assert "demand_array" not in w.planning["system"]


@pytest.mark.parametrize(
    ("centrals", "expected"),
    [
        (None, {1: 1000.0, 2: 1000.0}),
        (_Centrals([], TypeError("mock")), {1: 1000.0, 2: 1000.0}),
        (_Centrals([], 0.0), {1: 1000.0, 2: 1000.0}),
        (
            _Centrals([{"type": "falla", "bus": 1, "gcost": 300.0}], 250.0),
            {1: 300.0, 2: 250.0},
        ),
    ],
)
def test_demand_fcost_fallbacks(
    tmp_path: Path, centrals: Any, expected: dict[int, float]
) -> None:
    parsed: dict[str, Any] = {
        "demand_parser": _Demands([_dem("B1"), _dem("B2"), _dem("GHOST")]),
        "bus_parser": _Buses(),
    }
    if centrals is not None:
        parsed["central_parser"] = centrals
    w = _writer(parsed)
    w.process_demands({"output_dir": tmp_path})
    got = {d["bus"]: d["fcost"] for d in w.planning["system"]["demand_array"]}
    assert got == pytest.approx(expected)


def test_battery_extra_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeBW:
        def __init__(self, **_kw: Any) -> None:
            self._clamped_warnings = ["clamped"]

        @staticmethod
        def process(gen: Any, dem: Any, _out: Path) -> dict[str, Any]:
            return {
                "battery_array": [{"uid": 1}],
                "generator_array": gen,
                "demand_array": dem,
                "converter_array": [{"uid": 2}],
                "regulation_reservoirs": [{"uid": 3, "name": "R"}],
                "charge_coupling_constraints": [
                    {"name": "B_charge_source", "expression": "x"},
                    {"name": "C_charge_source", "expression": "y"},
                ],
            }

    monkeypatch.setattr(_writer_network, "BatteryWriter", _FakeBW)
    w = _writer({"battery_parser": object()})
    w.planning["system"]["reservoir_array"] = [{"uid": 1, "name": "L"}]
    w.planning["system"]["user_constraint_array"] = [{"uid": 7, "name": "laja"}]
    w.process_battery({"output_dir": tmp_path})
    sysd = w.planning["system"]
    assert sysd["converter_array"] == [{"uid": 2}]
    assert [r["name"] for r in sysd["reservoir_array"]] == ["L", "R"]
    assert w.planning["_clamped_battery_warnings"] == ["clamped"]
    # Charge couplings appended after existing user constraints, fresh uids.
    assert [(u["uid"], u["name"]) for u in sysd["user_constraint_array"]] == [
        (7, "laja"),
        (8, "B_charge_source"),
        (9, "C_charge_source"),
    ]
