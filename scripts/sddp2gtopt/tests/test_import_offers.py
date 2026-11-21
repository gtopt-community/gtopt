"""Interconnection imports are unavailable in hours with no offer.

PRECIOSMEX blank/0 for a unit (or its inherited INTER-SAL series) means no
offer that hour; without PRECIOSMEX a 0 fuel cost means no offer at all.
RESTMEX fixed / floor schedules keep the unit available.
"""

from __future__ import annotations

import pytest

from sddp2gtopt.dat_loader import _apply_import_offers
from sddp2gtopt.entities import StudySpec, ThermalSpec
from sddp2gtopt.gtopt_writer import _amm_override


def _imp(
    name: str,
    pmax: float,
    prices: list[float] | None = None,
    cost: float = 0.0,
    tipo: str = "",
    amm: list[float | None] | None = None,
) -> ThermalSpec:
    return ThermalSpec(
        code=1,
        name=name,
        reference_id=1,
        pmax=pmax,
        is_import=True,
        g_segments=[(pmax, cost)],
        gcost_profile=list(prices or []),
        amm_tipo=tipo,
        amm_profile=list(amm or []),
    )


def test_zero_price_hours_close_the_import() -> None:
    """Billed hours priced, look-ahead hours 0: capped to 0 there."""
    t = _imp("ESA-I", 310.0, prices=[250.0, 250.0, 0.0, 0.0])
    _apply_import_offers([t], has_price_file=True)
    assert t.max_gen == [310.0, 310.0, 0.0, 0.0]
    assert t.pmax == pytest.approx(310.0)


def test_existing_hourly_cap_is_kept_where_offered() -> None:
    t = _imp("MEX-I2", 30.0, prices=[10.5, 0.0, 12.0])
    t.max_gen = [20.0, 25.0, 30.0]
    _apply_import_offers([t], has_price_file=True)
    assert t.max_gen == [20.0, 0.0, 30.0]


def test_never_offered_unit_is_dropped() -> None:
    """A unit priced 0 in every hour (e.g. MEX-I in the Guatemala PDD)."""
    t = _imp("MEX-I", 120.0, prices=[0.0] * 6)
    _apply_import_offers([t], has_price_file=True)
    assert t.pmax == pytest.approx(0.0)


def test_fixed_schedule_hours_stay_available() -> None:
    """EDC-I: RESTMEX '=' 120 MW in hours 2-3, priced only in hours 0-1."""
    t = _imp(
        "EDC-I",
        120.0,
        prices=[40.9, 40.9, 0.0, 0.0, 0.0],
        tipo="=",
        amm=[None, None, 120.0, 120.0, None],
    )
    _apply_import_offers([t], has_price_file=True)
    assert t.max_gen == [120.0, 120.0, 120.0, 120.0, 0.0]


def test_without_price_file_zero_fuel_cost_means_no_offer() -> None:
    free = _imp("IMP-01", 310.0, cost=0.0)
    priced = _imp("INTER-SAL", 310.0, cost=250.0)
    _apply_import_offers([free, priced], has_price_file=False)
    assert free.pmax == pytest.approx(0.0)
    assert priced.pmax == pytest.approx(310.0)


def test_domestic_units_untouched() -> None:
    t = ThermalSpec(
        code=9, name="TERMO", reference_id=9, pmax=100.0, gcost_profile=[0.0, 0.0]
    )
    _apply_import_offers([t], has_price_file=True)
    _apply_import_offers([t], has_price_file=False)
    assert t.pmax == pytest.approx(100.0)
    assert not t.max_gen


def test_amm_fixed_schedule_falls_back_to_hourly_cap() -> None:
    """Outside the RESTMEX hours the ceiling is the hourly offer cap, not the
    installed capacity (otherwise a closed import reopens)."""
    study = StudySpec(stage_type=1, num_stages=1, num_blocks=4)
    t = _imp("EDC-I", 120.0, tipo="=", amm=[None, 120.0, None, None])
    t.max_gen = [120.0, 120.0, 0.0, 0.0]
    pmin, pmax = _amm_override(t, [[120.0, 120.0, 0.0, 0.0]], study)
    assert pmin == [[0.0, 120.0, 0.0, 0.0]]
    assert pmax == [[120.0, 120.0, 0.0, 0.0]]
