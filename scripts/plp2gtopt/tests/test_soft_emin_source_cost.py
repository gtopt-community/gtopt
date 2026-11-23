"""``plpminembh.dat`` soft-emin emission uses only the source's costs.

PLP's ``leeminembh.f`` reads a per-stage ``EmbCostoH`` for the soft
minimum-volume slack.  A zero file cost means the PLP slack is free, i.e.
the floor has no effect.  plp2gtopt must therefore not invent a price for
such stages (the legacy behaviour stamped ``--soft-emin-cost = 0.1``);
the fallback is opt-in only.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pytest

from ..junction_writer import JunctionWriter
from ..minembh_parser import MinembhParser
from .test_soft_storage_bounds import (
    MockCentralParser,
    MockStageParser,
    _make_reservoir_central,
)


class MockMinembhParser(MinembhParser):
    """Mock MinembhParser holding one reservoir's sparse soft-emin rows."""

    def __init__(self, entry: Dict[str, Any]):
        super().__init__("dummy.dat")
        self._entry = entry

    def get_minembh_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        return self._entry if self._entry.get("name") == name else None


def _entry(costs: list[float]) -> Dict[str, Any]:
    return {
        "name": "RsvA",
        "stage": np.array([1, 2, 3], dtype=np.int32),
        "vmin": np.array([10.0, 20.0, 30.0], dtype=np.float64),
        "cost": np.array(costs, dtype=np.float64),
    }


def _apply(costs: list[float], options: Dict[str, Any]) -> Dict[str, Any]:
    writer = JunctionWriter(
        central_parser=MockCentralParser([_make_reservoir_central()]),
        stage_parser=MockStageParser(num=4),
        minembh_parser=MockMinembhParser(_entry(costs)),
        options=options,
    )
    reservoir: Dict[str, Any] = {"uid": 1, "name": "RsvA"}
    writer._apply_soft_emin(reservoir, "RsvA")  # pylint: disable=protected-access
    return reservoir


def test_zero_file_cost_stage_is_skipped_by_default() -> None:
    """Stages with a free PLP slack get no floor and no invented price."""
    reservoir = _apply([5.0, 0.0, 7.0], options={})
    assert reservoir["soft_emin"] == [10.0, 0.0, 30.0, 0.0]
    assert reservoir["soft_emin_cost"] == [5.0, 0.0, 7.0, 0.0]


def test_all_zero_file_costs_emit_nothing() -> None:
    """No positive source cost anywhere → no soft_emin at all."""
    reservoir = _apply([0.0, 0.0, 0.0], options={"soft_emin_cost": 0.0})
    assert "soft_emin" not in reservoir
    assert "soft_emin_cost" not in reservoir


def test_file_costs_apply_even_when_fallback_is_zero() -> None:
    """A zero fallback must not suppress the source's positive costs."""
    reservoir = _apply([5.0, 6.0, 7.0], options={"soft_emin_cost": 0.0})
    assert reservoir["soft_emin_cost"] == [5.0, 6.0, 7.0, 0.0]


def test_opt_in_fallback_prices_zero_cost_stages() -> None:
    """``--soft-emin-cost > 0`` restores the legacy fallback explicitly."""
    reservoir = _apply([5.0, 0.0, 7.0], options={"soft_emin_cost": 0.1})
    assert reservoir["soft_emin"] == [10.0, 20.0, 30.0, 0.0]
    assert reservoir["soft_emin_cost"] == pytest.approx([5.0, 0.1, 7.0, 0.0])


def test_negative_fallback_disables_minembh() -> None:
    """``--soft-emin-cost -1`` keeps the legacy "disable" switch."""
    reservoir = _apply([5.0, 6.0, 7.0], options={"soft_emin_cost": -1.0})
    assert "soft_emin" not in reservoir


def test_cli_default_is_zero() -> None:
    """The CLI default no longer invents a soft-emin price."""
    from ..main import make_parser  # noqa: PLC0415

    args = make_parser().parse_args(["-i", "x"])
    assert args.soft_emin_cost == pytest.approx(0.0)
