# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Recipe builder branches: negative LMP, per-bus overrides, edge kinds."""

from __future__ import annotations

import math

import pytest

from gtopt_canonical_feed import Generator, Topology
from gtopt_marginal_units._reconstruct import ZoneR3Result
from gtopt_marginal_units._recipes import (
    _explain_em,
    _explain_lmp,
    _formula_data,
    build_recipes_for_cell,
)
from gtopt_marginal_units.constants import Confidence, FormulaKind

_CELL = (1, 1, 1, None, None, "simulated")


def _gen(uid: int, mc: float = 20.0, kind: str = "thermal", bus: int = 1):
    return Generator(
        uid=uid,
        name=f"g{uid}",
        bus_uid=bus,
        pmin=0.0,
        pmax=100.0,
        declared_MC=mc,
        kind=kind,
        emission_rate=0.4,
    )


def _zr(kind: str, uids: list[int], lam: float = 20.0, zone: int = 0):
    return ZoneR3Result(
        zone_id=zone,
        lambda_z=lam,
        formula_kind=kind,
        marginal_gen_uids=uids,
        confidence=Confidence.MERIT_ORDER,
        degenerate=False,
        reason="test",
        clamped=False,
    )


def _build(gens, zone_of, zres, **kw):
    return build_recipes_for_cell(
        cell_key=_CELL,
        topology=Topology(buses=[], generators=gens, lines=[]),
        zone_of=zone_of,
        zone_results=zres,
        **kw,
    )


def test_buses_or_gens_outside_zones_are_skipped():
    price, _ = _build(
        [_gen(1), _gen(2, bus=9)],
        {1: 0, 2: 5},
        {0: _zr(FormulaKind.SINGLE_UNIT.value, [1])},
    )
    assert [r.bus_uid for r in price] == [1]


def test_negative_lmp_storage_marginal_is_clamped():
    price, em = _build(
        [_gen(1, mc=0.0, kind="hydro")],
        {1: 0},
        {0: _zr(FormulaKind.HYDRO_MARGINAL.value, [1], lam=-5.0)},
    )
    assert price[0].formula_kind == FormulaKind.HYDRO_MARGINAL.value
    assert price[0].recomputed_value == 0.0
    assert em[0].recomputed_value == 0.0


def test_negative_lmp_without_storage_has_no_marginal():
    price, em = _build(
        [_gen(1)],
        {1: 0},
        {0: _zr(FormulaKind.SINGLE_UNIT.value, [1], lam=-5.0)},
    )
    assert price[0].formula_kind == FormulaKind.NO_MARGINAL_NEG_LMP.value
    assert price[0].marginal_gen_uids == []
    assert em[0].recomputed_value == 0.0
    # negative LMP with no marginal uid at all
    price, _ = _build([], {1: 0}, {0: _zr(FormulaKind.UNATTRIBUTED.value, [], lam=-5)})
    assert price[0].formula_kind == FormulaKind.NO_MARGINAL_NEG_LMP.value


def test_empty_island_zero_price():
    price, em = _build([], {1: 0}, {0: _zr(FormulaKind.EMPTY_ISLAND.value, [], 0.0)})
    assert price[0].recomputed_value == 0.0 and em[0].recomputed_value == 0.0


def test_bus_lmp_near_demand_fail_cost_overrides_kind():
    price, _ = _build(
        [_gen(1)],
        {1: 0},
        {0: _zr(FormulaKind.SINGLE_UNIT.value, [1])},
        lmp_by_bus={1: 1010.0},
        demand_fail_cost={1: 1000.0},
    )
    assert price[0].formula_kind == FormulaKind.DEMAND_FAIL.value
    assert price[0].recomputed_value == pytest.approx(1010.0)
    assert price[0].formula_constant == pytest.approx(1010.0)


def test_unattributed_recovers_bus_lmp():
    price, _ = _build(
        [],
        {1: 0},
        {0: _zr(FormulaKind.UNATTRIBUTED.value, [])},
        lmp_by_bus={1: 33.0},
    )
    assert price[0].recomputed_value == pytest.approx(33.0)


def test_unknown_marginal_uid_with_bus_lmp():
    price, em = _build(
        [_gen(1)],
        {1: 0},
        {0: _zr(FormulaKind.SINGLE_UNIT.value, [77])},
        lmp_by_bus={1: 20.0},
    )
    assert price[0].recomputed_value == pytest.approx(20.0)
    assert math.isnan(price[0].marginal_data[0])
    assert em[0].recomputed_value == 0.0


def test_cross_island_marginal_without_reference_lmp():
    price, em = _build(
        [_gen(1, bus=2), _gen(3, bus=1)],
        {1: 0, 2: 1},
        {
            0: _zr(FormulaKind.SINGLE_UNIT.value, [1], zone=0),
            1: _zr(FormulaKind.SINGLE_UNIT.value, [1], zone=1),
        },
        lmp_by_bus={1: 20.0, 2: 0.0},
    )
    row = next(r for r in em if r.bus_uid == 1)
    assert row.loss_factor_status == "cross_island"
    assert row.loss_factor_raw == 1.0
    assert price


def test_formula_explanations_forced_pmin():
    kind = FormulaKind.FORCED_PMIN_MARGINAL.value
    assert "forced-pmin g[4]" in _explain_lmp(kind, [4])
    assert "forced-pmin g4" in _explain_em(kind, [4])
    assert _formula_data(kind, [], {}) == ([], [], [])
