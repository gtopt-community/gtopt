# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Pre-tax cash-flow engine."""

from __future__ import annotations

import pytest

from gtopt_private_eval.finance import (
    YearRow,
    build_cash_flows,
    crf,
    irr,
    npv,
    payback,
)


def test_crf() -> None:
    assert crf(0.0, 10) == pytest.approx(0.1)
    assert crf(0.08, 30) == pytest.approx(0.0888274, rel=1e-6)
    with pytest.raises(ValueError):
        crf(0.08, 0)


def test_npv_irr_annuity_identity() -> None:
    # an annuity of capex*CRF(r, n) for n years has NPV 0 and IRR r
    capex, r, n = 1000.0, 0.09, 20
    flows = [-capex] + [capex * crf(r, n)] * n
    assert npv(r, flows) == pytest.approx(0.0, abs=1e-9)
    assert irr(flows) == pytest.approx(r, abs=1e-7)


def test_irr_none_without_sign_change() -> None:
    assert irr([-1.0, -1.0]) is None
    assert irr([1.0, 1.0]) is None


def test_payback() -> None:
    assert payback([-100.0, 40.0, 40.0, 40.0]) == pytest.approx(2.5)
    assert payback([-100.0, 10.0]) is None
    assert payback([5.0, 1.0]) == 0.0
    assert payback([]) is None
    disc = payback([-100.0, 60.0, 60.0], rate=0.1)
    assert disc is not None and 1.0 < disc < 2.0


def test_build_cash_flows_ebit() -> None:
    cf = build_cash_flows(
        capex=1000.0,
        life=10,
        wacc=0.08,
        revenues={"energy": 300.0, "capacity": [50.0, 50.0, 0.0]},
        costs={"fixed_om": 20.0},
        energy_mwh=1000.0,
    )
    y1, y5 = cf.years[0], cf.years[4]
    assert y1.revenue == pytest.approx(350.0)
    assert y5.revenues["capacity"] == 0.0  # last entry repeats
    assert y1.ebitda == pytest.approx(330.0)
    assert y1.depreciation == pytest.approx(100.0)
    assert y1.ebit == pytest.approx(230.0)
    assert y1.ebit_margin == pytest.approx(230.0 / 350.0)
    assert cf.flows[0] == -1000.0
    assert cf.npv == pytest.approx(npv(0.08, cf.flows))
    assert cf.irr is not None and cf.irr > 0.08
    assert cf.payback is not None and cf.discounted_payback is not None
    assert cf.equivalent_annual == pytest.approx(cf.npv * crf(0.08, 10))
    lcoe = cf.lcoe()
    assert lcoe is not None and lcoe > 0
    assert cf.totals("revenues")["energy"] == pytest.approx(3000.0)
    d = cf.to_dict()
    assert d["years"][0]["ebit"] == pytest.approx(230.0)


def test_depreciation_life_and_edge_cases() -> None:
    cf = build_cash_flows(
        capex=100.0,
        life=4,
        wacc=0.0,
        revenues={},
        costs={},
        energy_mwh=0.0,
        depreciation_life=2,
    )
    assert [r.depreciation for r in cf.years] == [50.0, 50.0, 0.0, 0.0]
    assert cf.lcoe() is None
    assert cf.years[0].ebit_margin is None
    free = build_cash_flows(
        capex=0.0, life=1, wacc=0.1, revenues={"x": []}, costs={}, energy_mwh=1.0
    )
    assert free.irr is None
    assert free.years[0].revenues["x"] == 0.0
    with pytest.raises(ValueError):
        build_cash_flows(
            capex=1.0, life=0, wacc=0.1, revenues={}, costs={}, energy_mwh=0.0
        )


def test_yearrow_to_dict() -> None:
    row = YearRow(
        year=1, revenues={"a": 2.0}, costs={"b": 1.0}, energy_mwh=3.0, depreciation=0.5
    )
    d = row.to_dict()
    assert d["ebitda"] == 1.0 and d["ebit"] == 0.5
