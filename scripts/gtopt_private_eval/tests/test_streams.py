# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Stream-specific discounting (``finance.stream_rates``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gtopt_private_eval.case_data import load_system
from gtopt_private_eval.evaluate import evaluate
from gtopt_private_eval.finance import build_cash_flows, npv
from gtopt_private_eval.main import main
from gtopt_private_eval.params import Params, load_config
from gtopt_private_eval.streams import (
    classify,
    evaluate_streams,
    stream_config,
)

from .conftest import CONFIGS, golden


def _cash(**kw: Any):  # noqa: ANN202 - test helper
    args: dict[str, Any] = {
        "capex": 1000.0,
        "life": 10,
        "wacc": 0.08,
        "revenues": {"energy": 200.0, "capacity": 50.0},
        "costs": {"variable": 60.0, "fixed_om": 20.0},
        "energy_mwh": 1.0,
    }
    args.update(kw)
    return build_cash_flows(**args)


def _pv(v: float, r: float, n: int = 10) -> float:
    return sum(v / (1.0 + r) ** y for y in range(1, n + 1))


# ── configuration ──────────────────────────────────────────────────────


def test_config_off_by_default_and_validation() -> None:
    assert stream_config({}, {}) is None
    assert stream_config({"stream_rates": {"rates": {}}}, {}) is None
    off = {"stream_rates": {"enabled": False, "rates": {"energy": 0.1}}}
    assert stream_config(off, {}) is None
    cfg = stream_config({"stream_rates": {"rates": {"energy": 0.1}}}, {})
    assert cfg is not None and cfg["method"] == "rate"
    with pytest.raises(ValueError, match="method"):
        stream_config({"stream_rates": {"method": "x", "rates": {"a": 0.1}}}, {})


def test_project_override_merges() -> None:
    fin = {"stream_rates": {"rates": {"energy": 0.1, "om": 0.05}}}
    proj = {"stream_rates": {"map": {"energy": "contract"}, "rates": {"om": 0.04}}}
    cfg = stream_config(fin, proj)
    assert cfg is not None
    assert cfg["rates"] == {"energy": 0.1, "om": 0.04}
    assert cfg["map"] == {"energy": "contract"}
    # a project-only config also works
    only = stream_config({}, {"stream_rates": {"haircuts": {"energy": 0.2}}})
    assert only is not None and only["haircuts"] == {"energy": 0.2}


def test_classify() -> None:
    assert classify("energy", {}, hedged=False) == "energy"
    assert classify("energy", {}, hedged=True) == "contract"
    assert classify("cfd_difference", {}, hedged=True) == "contract"
    assert classify("sufficiency", {}, hedged=False) == "capacity"
    assert classify("variable", {}, hedged=False) == "fuel"
    assert classify("new_thing", {}, hedged=False) == "new_thing"
    assert classify("energy", {"map": {"energy": "x"}}, hedged=True) == "x"


# ── rate method ───────────────────────────────────────────────────────


def test_single_rate_reproduces_the_base_npv() -> None:
    cash = _cash()
    res = evaluate_streams(cash, {"method": "rate", "rates": {"energy": 0.08}})
    assert res.npv == pytest.approx(cash.npv)
    assert res.equivalent_rate == pytest.approx(0.08, abs=1e-7)
    assert set(res.streams) == {"energy", "capacity", "fuel", "om"}
    assert res.streams["fuel"]["components"] == ["variable"]


def test_rate_method_by_stream() -> None:
    cash = _cash()
    rates = {"energy": 0.10, "capacity": 0.06, "fuel": 0.03, "om": 0.05}
    res = evaluate_streams(cash, {"method": "rate", "rates": rates})
    expect = (
        -1000.0 + _pv(200.0, 0.10) + _pv(50.0, 0.06) - _pv(60.0, 0.03) - _pv(20.0, 0.05)
    )
    assert res.npv == pytest.approx(expect)
    assert res.streams["energy"]["pv"] == pytest.approx(_pv(200.0, 0.10))
    assert res.streams["fuel"]["pv"] == pytest.approx(-_pv(60.0, 0.03))
    # the equivalent single rate gives the same NPV on the same flows
    assert res.equivalent_rate is not None
    assert npv(res.equivalent_rate, cash.flows) == pytest.approx(res.npv, abs=1e-6)
    assert not res.warnings
    d = res.to_dict()
    assert d["method"] == "rate" and d["npv"] == pytest.approx(expect)


def test_low_fuel_rate_lowers_the_npv() -> None:
    """Awerbuch: a low-beta fuel bill discounted near r_f costs more."""
    cash = _cash()
    base = evaluate_streams(cash, {"rates": {"fuel": 0.08}})
    low = evaluate_streams(cash, {"rates": {"fuel": 0.03}})
    assert low.npv < base.npv


def test_contract_vs_merchant() -> None:
    cash = _cash(revenues={"energy": 200.0})
    rates = {"energy": 0.10, "contract": 0.05}
    merchant = evaluate_streams(cash, {"rates": rates})
    contract = evaluate_streams(cash, {"rates": rates, "map": {"energy": "contract"}})
    assert contract.npv > merchant.npv
    assert contract.streams["contract"]["rate"] == 0.05


def test_cfd_energy_is_contracted() -> None:
    cash = _cash(revenues={"energy": 150.0, "cfd_difference": 50.0})
    res = evaluate_streams(cash, {"rates": {"contract": 0.05, "energy": 0.1}})
    assert set(res.streams) >= {"contract"}
    assert "energy" not in res.streams
    assert res.streams["contract"]["components"] == ["energy", "cfd_difference"]


def test_no_capex_no_equivalent_rate() -> None:
    cash = _cash(capex=0.0)
    res = evaluate_streams(cash, {"rates": {"energy": 0.1}})
    assert res.equivalent_rate is None


# ── certainty-equivalent method ──────────────────────────────────────


def test_certainty_equivalent() -> None:
    cash = _cash()
    cfg = {
        "method": "certainty_equivalent",
        "risk_free": 0.03,
        "haircuts": {"energy": 0.2, "om": 0.1},
    }
    res = evaluate_streams(cash, cfg)
    expect = (
        -1000.0
        + _pv(200.0 * 0.8, 0.03)
        + _pv(50.0, 0.03)
        - _pv(60.0, 0.03)
        - _pv(20.0 * 1.1, 0.03)
    )
    assert res.npv == pytest.approx(expect)
    assert res.streams["energy"]["haircut"] == 0.2
    assert res.streams["capacity"]["rate"] == 0.03


# ── warnings ─────────────────────────────────────────────────────────


def test_warning_merchant_wacc_on_unlisted_streams() -> None:
    cash = _cash(wacc=0.10)
    res = evaluate_streams(cash, {"rates": {"energy": 0.10, "fuel": 0.03}})
    assert any("merchant energy rate" in w for w in res.warnings)
    assert "capacity" in " ".join(res.warnings)


def test_warning_contract_above_merchant() -> None:
    res = evaluate_streams(_cash(), {"rates": {"energy": 0.06, "contract": 0.09}})
    assert any("contract rate" in w for w in res.warnings)


def test_warning_rates_and_haircuts_mixed() -> None:
    ce = evaluate_streams(
        _cash(),
        {
            "method": "certainty_equivalent",
            "haircuts": {"energy": 0.1},
            "rates": {"energy": 0.1},
        },
    )
    assert any("count risk twice" in w for w in ce.warnings)
    rt = evaluate_streams(_cash(), {"rates": {"energy": 0.1}, "haircuts": {"x": 0.1}})
    assert any("ignores 'haircuts'" in w for w in rt.warnings)


def test_warning_risk_adjusted_prices() -> None:
    rate = evaluate_streams(
        _cash(), {"rates": {"energy": 0.1}}, risk_adjusted_prices=True
    )
    assert any("risk_adjusted_prices" in w for w in rate.warnings)
    ce = evaluate_streams(
        _cash(),
        {"method": "certainty_equivalent", "haircuts": {"energy": 0.1}},
        risk_adjusted_prices=True,
    )
    assert any("haircuts count risk twice" in w for w in ce.warnings)
    # risk-free everywhere: consistent, no warning
    rf = {"energy": 0.03, "capacity": 0.03, "fuel": 0.03, "om": 0.03}
    clean = evaluate_streams(
        _cash(), {"rates": rf, "risk_free": 0.03}, risk_adjusted_prices=True
    )
    assert not any("risk_adjusted_prices" in w for w in clean.warnings)
    ce0 = evaluate_streams(
        _cash(),
        {"method": "certainty_equivalent", "haircuts": {"energy": 0.0}},
        risk_adjusted_prices=True,
    )
    assert not ce0.warnings


def test_warning_unknown_map_component() -> None:
    res = evaluate_streams(
        _cash(), {"rates": {"energy": 0.1}, "map": {"nonexistent": "energy"}}
    )
    assert any("unknown components nonexistent" in w for w in res.warnings)


# ── on the battery golden and through the CLI ───────────────────────


def _bat_eval(cfg_name: str, **finance: Any):  # noqa: ANN202 - test helper
    cfg = load_config(CONFIGS / cfg_name)
    cfg.setdefault("finance", {}).update(finance)
    rep = evaluate(
        load_system(golden("invest_bat_1b/golden/lp")), Params(cfg), "energy_only"
    )
    (bess,) = [p for p in rep.projects if p.name == "bess"]
    return rep, bess


def test_battery_contract_beats_merchant() -> None:
    _rep, base = _bat_eval("invest_bat.json")
    assert base.streams is None
    _rep, merchant = _bat_eval("invest_bat_streams_merchant.json")
    _rep, contract = _bat_eval("invest_bat_streams_contract.json")
    assert merchant.streams is not None and contract.streams is not None
    assert contract.streams.npv > base.cash.npv > merchant.streams.npv
    assert merchant.streams.equivalent_rate is not None
    assert merchant.streams.equivalent_rate > 0.08
    assert merchant.to_dict()["streams"]["method"] == "rate"


def test_battery_warnings_reach_the_report_notes() -> None:
    rep, bess = _bat_eval("invest_bat_streams_merchant.json", wacc=0.12)
    assert bess.streams is not None and not bess.streams.warnings
    rep, bess = _bat_eval("invest_bat_streams_ce.json", risk_adjusted_prices=True)
    assert bess.streams is not None
    assert any(n.startswith("bess: ") and "risk twice" in n for n in rep.notes)


def test_cli_prints_stream_columns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    jout = tmp_path / "s.json"
    rc = main(
        [
            str(golden("invest_bat_1b/golden/lp")),
            "--config",
            str(CONFIGS / "invest_bat_streams_ce.json"),
            "--include-unbuilt",
            "--json-out",
            str(jout),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "stream discounting (certainty_equivalent)" in out
    assert "eq. rate" in out and "-25%" in out
    data = json.loads(jout.read_text(encoding="utf-8"))
    by = {p["name"]: p for p in data["projects"]}
    assert by["bess"]["streams"]["method"] == "certainty_equivalent"


def test_cli_project_level_streams_only(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            str(golden("invest_screening_1b/golden/lp")),
            "--config",
            str(CONFIGS / "invest_screening.json"),
            "--set",
            'projects.base.stream_rates={"rates": {"energy": 0.1, "fuel": 0.03}}',
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    rows = [ln for ln in out.splitlines() if ln.startswith("  peak ")]
    assert rows and rows[-1].rstrip().endswith("-")
    assert any(ln.startswith("  base ") and "%" in ln[-10:] for ln in out.splitlines())


# ── market stream maps (cl: cost-based, declared variable cost) ──────


def test_market_map_precedence() -> None:
    from gtopt_private_eval.markets.cl import Chile

    mm = Chile.stream_map
    assert classify("variable", {}, hedged=False, market_map=mm) == "energy"
    assert classify("vom_extra", {}, hedged=False, market_map=mm) == "energy"
    assert classify("fixed_om", {}, hedged=False, market_map=mm) == "capital"
    # contract-indexed fuel under a CfD / PPA goes with the contract
    assert classify("variable", {}, hedged=True, market_map=mm) == "contract"
    # the separate fuel stream stays reachable with an explicit map
    user = {"map": {"variable": "fuel"}}
    assert classify("variable", user, hedged=False, market_map=mm) == "fuel"
    # other markets keep the default classification
    assert classify("variable", {}, hedged=False, market_map={}) == "fuel"


def test_cl_default_map_nets_fuel_with_energy() -> None:
    cfg = load_config(CONFIGS / "invest_cl_screening_streams.json")
    rep = evaluate(
        load_system(golden("invest_cl_screening_1b/golden/lp")), Params(cfg), "cl"
    )
    base = next(p for p in rep.projects if p.name == "base")
    assert base.streams is not None
    streams = base.streams.streams
    assert "fuel" not in streams and "om" not in streams
    assert "variable" in streams["energy"]["components"]
    assert "fixed_om" in streams["capital"]["components"]
    assert streams["capital"]["rate"] == pytest.approx(base.wacc)
    # no artificial Awerbuch penalty: the energy stream PV is the margin's
    margin = sum(
        (y.revenues["energy"] - y.costs.get("variable", 0.0)) / 1.1**y.year
        for y in base.cash.years
    )
    extra = sum(
        (
            y.revenues.get("scarcity_excluded", 0.0)
            - y.costs.get("vom_extra", 0.0)
            - y.costs.get("green_tax", 0.0)
        )
        / 1.1**y.year
        for y in base.cash.years
    )
    assert streams["energy"]["pv"] == pytest.approx(margin + extra, rel=1e-9)
    # capital at the WACC is not flagged as merchant risk on an unlisted stream
    assert not any("unlisted" in w for w in base.streams.warnings)
    # energy_only keeps fuel as its own stream
    rep2 = evaluate(
        load_system(golden("invest_cl_screening_1b/golden/lp")),
        Params(cfg),
        "energy_only",
    )
    base2 = next(p for p in rep2.projects if p.name == "base")
    assert base2.streams is not None and "fuel" in base2.streams.streams
