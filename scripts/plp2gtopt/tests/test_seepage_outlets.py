"""Tests for ``JunctionWriter._ensure_seepage_outlets``.

A forced seepage (``q = c + s·efin``, equality row) into a junction whose
outlets are capped below the largest ``q`` the seepage line can produce
turns the outlet capacity into a hidden upper bound on the source
reservoir volume and a negative junction dual (CIPRESES → FILT_CIPRESES:
outlet 20.1 m³/s, first tramo extrapolated to a full lake ≈ 30 m³/s).
The writer gives such junctions a free drain; ``plp_legacy`` keeps PLP's
capped topology.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, cast

import pytest

from ..junction_writer import HydroSystemOutput, JunctionWriter
from .test_junction_writer import MockCentralParser


def _system(outlet_fmax: Any = 20.1, emax: Any = 151.8) -> Dict[str, Any]:
    """CIPRESES-like seepage: reservoir → FILT → OJOS (capped outlet)."""
    outlet: Dict[str, Any] = {
        "uid": 2,
        "name": "FILT_gen",
        "junction_a": "FILT",
        "junction_b": "OJOS",
    }
    if outlet_fmax is not None:
        outlet["fmax"] = outlet_fmax
    return {
        "junction_array": [
            {"uid": 1, "name": "RSV", "drain": False},
            {"uid": 2, "name": "FILT", "drain": False},
            {"uid": 3, "name": "OJOS", "drain": True},
        ],
        "waterway_array": [
            {"uid": 1, "name": "filt_RSV", "junction_a": "RSV", "junction_b": "FILT"},
            outlet,
        ],
        "turbine_array": [],
        "reservoir_array": [
            {
                "uid": 1,
                "name": "RSV",
                "junction": "RSV",
                "emin": 4.7,
                "emax": emax,
                "capacity": 151.8,
            }
        ],
        # Same coefficients plp2gtopt emits for CIPRESES (first tramo
        # rebuilt so that q(vmin) = 0).
        "reservoir_seepage_array": [
            {
                "uid": 1,
                "name": "RSV_seepage_1",
                "waterway": "filt_RSV",
                "reservoir": "RSV",
                "slope": 0.2065027,
                "constant": -0.9739411,
                "segments": [
                    {"volume": 0.0, "slope": 0.2065027, "constant": -0.9739411},
                    {"volume": 70.0, "slope": 0.019689188, "constant": 12.10300518},
                    {"volume": 98.0, "slope": 0.077987072, "constant": 6.389812555},
                ],
            }
        ],
    }


def _filt(system: Dict[str, Any]) -> Dict[str, Any]:
    return next(j for j in system["junction_array"] if j["name"] == "FILT")


def _run(writer: JunctionWriter, system: Dict[str, Any]) -> None:
    # pylint: disable-next=protected-access
    writer._ensure_seepage_outlets(cast(HydroSystemOutput, system))


def _writer(**options: Any) -> JunctionWriter:
    return JunctionWriter(central_parser=MockCentralParser([]), options=options)


def test_capped_outlet_gets_free_drain(caplog: pytest.LogCaptureFixture) -> None:
    """q_max ≈ 0.2065·151.8 − 0.97 ≈ 30.4 > 20.1 → free drain added."""
    system = _system()
    writer = _writer()
    with caplog.at_level(logging.WARNING):
        _run(writer, system)
    filt = _filt(system)
    assert filt["drain"] is True
    # No cost: the drain is a free outlet, not a penalty.
    assert "drain_cost" not in filt
    # Bounded by the over-forecast q_max − outlet capacity.
    assert filt["drain_capacity"] == pytest.approx(0.2065027 * 151.8 - 0.9739411 - 20.1)
    assert writer._seepage_drain_names == ["FILT"]  # pylint: disable=protected-access
    assert any("added a free drain" in r.message for r in caplog.records)


def test_large_enough_outlet_is_left_alone() -> None:
    system = _system(outlet_fmax=40.0)
    _run(_writer(), system)
    assert _filt(system)["drain"] is False


def test_unbounded_outlet_is_left_alone() -> None:
    system = _system(outlet_fmax=None)
    _run(_writer(), system)
    assert _filt(system)["drain"] is False


def test_schedule_outlet_is_treated_as_capped() -> None:
    """A per-stage fmax schedule is still a cap: add the drain."""
    system = _system(outlet_fmax="fmax")
    _run(_writer(), system)
    assert _filt(system)["drain"] is True
    assert "drain_capacity" not in _filt(system)


def test_emax_schedule_falls_back_to_capacity() -> None:
    system = _system(emax="emax")
    _run(_writer(), system)
    assert _filt(system)["drain"] is True


def test_plp_legacy_keeps_cap(caplog: pytest.LogCaptureFixture) -> None:
    system = _system()
    with caplog.at_level(logging.WARNING):
        _run(_writer(plp_legacy=True), system)
    assert _filt(system)["drain"] is False
    assert any("plp-legacy keeps the cap" in r.message for r in caplog.records)
