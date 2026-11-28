# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Cost recovery of a risk-averse (``risk_measure = cvar``) run.

The duals of a mean-CVaR LP are risk-adjusted: the price written for
scenario s is ζ_s = π̃_s / p_s times the scenario's own marginal value.
Built projects recover opex + capex exactly under the risk-adjusted
measure π̃ (``risk_weights="auto"``); under the true probabilities p
(``"probability"``) the tail project shows a loss: the risk premium.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtopt_cost_recovery import compute_recovery
from gtopt_cost_recovery.main import main
from gtopt_cost_recovery.recovery import (
    RiskScenario,
    _rescale_duals,
    apply_risk_weights,
    read_risk_measure_csv,
)
from gtopt_pricing_uplift._data import Case

from .conftest import CASES

CVAR = "invest_cvar_1b/golden"


def _golden(variant: str) -> Path:
    path = CASES / CVAR / variant
    if not (path / "planning.json").is_file():
        pytest.skip(f"golden {variant} not present")
    return path


def _risk(scen: int, p: float, pi: float) -> RiskScenario:
    return RiskScenario(
        scenario=scen,
        probability=p,
        cost=1.0,
        excess=0.0,
        dual=pi - 0.5 * p,
        risk_probability=pi,
        risk_factor=pi / p if p > 0 else 0.0,
        eta=1.0,
        alpha=0.8,
        weight=0.5,
    )


def test_read_risk_measure_csv(tmp_path: Path) -> None:
    assert not read_risk_measure_csv(tmp_path)
    (tmp_path / "risk_measure.csv").write_text(
        "scene,phase,scenario,probability,cost,eta,excess,dual,"
        "risk_probability,risk_factor,alpha,weight,measure,rank_by,tail\n"
        "1,1,2,0.25,10,8,2,0.125,0.25,1,0.9,0.5,trim,index,lower\n",
        encoding="utf-8",
    )
    (row,) = read_risk_measure_csv(tmp_path)
    assert row.scenario == 2
    assert row.probability == pytest.approx(0.25)
    assert row.eta == pytest.approx(8.0)
    assert row.alpha == pytest.approx(0.9)
    assert (row.measure, row.rank_by, row.tail) == ("trim", "index", "lower")


def test_apply_risk_weights_and_rescale() -> None:
    case = Case(planning={}, input_dir=Path("."))
    case.scenario_weight = {1: 0.5, 2: 0.3, 3: 0.2}
    risk = [_risk(1, 0.5, 0.25), _risk(2, 0.3, 0.15), _risk(3, 0.2, 0.6)]
    scale = apply_risk_weights(case, risk, risk_adjusted=False)
    assert case.scenario_weight[3] == pytest.approx(0.2)  # p kept
    assert scale[3] == pytest.approx(0.2 / 0.6)
    scale = apply_risk_weights(case, risk)
    assert case.scenario_weight[3] == pytest.approx(0.6)
    assert sum(case.scenario_weight.values()) == pytest.approx(1.0)
    data = {(3, 1, 1, 1): 3.0, (1, 1, 1, 1): 1.0, (9, 1, 1, 1): 7.0}
    out = _rescale_duals(data, scale)
    assert out[(3, 1, 1, 1)] == pytest.approx(1.0)
    assert out[(1, 1, 1, 1)] == pytest.approx(2.0)
    assert out[(9, 1, 1, 1)] == pytest.approx(7.0)  # unknown scenario
    assert _rescale_duals(data, {}) is data
    # zero risk weight -> unpriced scenario
    zero = apply_risk_weights(case, [_risk(1, 0.5, 0.0), _risk(2, 0.5, 1.0)])
    assert zero[1] == 0.0
    assert not apply_risk_weights(case, [_risk(1, 0.5, 0.0)])


def test_bad_risk_weights(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="risk_weights"):
        compute_recovery(tmp_path, risk_weights="bogus")


@pytest.mark.parametrize("variant", ["lp", "worst", "mip"])
def test_cvar_zero_profit_under_risk_measure(variant: str) -> None:
    rep = compute_recovery(_golden(variant))
    assert rep.risk and rep.risk_adjusted
    assert sum(r.risk_probability for r in rep.risk) == pytest.approx(1.0)
    built = [p for p in rep.projects if p.built]
    assert built
    if variant == "mip":
        # fixed-integer prices: no project is paid more than its cost
        assert rep.total_make_whole >= 0.0
        return
    for p in built:
        assert abs(p.profit) <= 1e-6 * max(p.total_capex, 1.0) + 1e-3, p.name
    for p in rep.projects:
        if not p.built:
            assert p.net_value_per_unit <= 1e-6 * max(p.cost_per_unit, 1.0)
    assert rep.market.residual == pytest.approx(
        0.0, abs=1e-6 * max(rep.market.load_payments, 1.0)
    )


def test_cvar_risk_premium_under_true_probabilities() -> None:
    adj = compute_recovery(_golden("lp"))
    raw = compute_recovery(_golden("lp"), risk_weights="probability")
    assert not raw.risk_adjusted
    peak_adj = next(p for p in adj.projects if p.name == "peak")
    peak_raw = next(p for p in raw.projects if p.name == "peak")
    # the peaker sized for the tail does not pay under p: risk premium > 0
    assert peak_raw.profit < -1e-6 * peak_adj.total_capex
    assert peak_adj.profit == pytest.approx(0.0, abs=1e-6 * peak_adj.total_capex)


def test_expected_golden_unchanged() -> None:
    rep = compute_recovery(_golden("expected"))
    assert not rep.risk
    for p in rep.projects:
        if p.built:
            assert abs(p.profit) <= 1e-6 * max(p.total_capex, 1.0) + 1e-3


def test_cli_prints_risk_table(capsys: pytest.CaptureFixture[str]) -> None:
    path = _golden("lp")
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "risk measure (social): cvar" in out
    assert "risk-adjusted" in out
    assert main([str(path), "--risk-weights", "probability"]) == 0
    assert "probabilities p_s" in capsys.readouterr().out
