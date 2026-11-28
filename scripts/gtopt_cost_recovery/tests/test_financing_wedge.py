# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Financing wedge: annuity at the candidate's wacc − annuity at the social
rate (TIMES "INV+"), reported per project and in total."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from gtopt_cost_recovery import compute_recovery
from gtopt_cost_recovery.main import main
from gtopt_cost_recovery.recovery import (
    ProjectRecovery,
    RecoveryReport,
    capital_recovery_factor as crf,
    financing_wedge_fraction,
)

from .conftest import CASES, sim, write_case, write_stream


def test_fraction_precedence_and_values() -> None:
    elem: dict[str, Any] = {
        "overnight_capcost": 1000.0,
        "economic_life": 20,
        "wacc": 0.09,
    }
    assert financing_wedge_fraction(elem, 0, 0.06) == pytest.approx(
        1.0 - crf(0.06, 20) / crf(0.09, 20)
    )
    # wacc = social rate → no wedge; wacc below it → negative wedge
    assert financing_wedge_fraction({**elem, "wacc": 0.06}, 0, 0.06) == pytest.approx(
        0.0
    )
    assert financing_wedge_fraction({**elem, "wacc": 0.05}, 0, 0.06) < 0.0
    # unset wacc falls back to the social rate in gtopt → 0
    no_wacc = {k: v for k, v in elem.items() if k != "wacc"}
    assert financing_wedge_fraction(no_wacc, 0, 0.06) == 0.0
    # explicit annuity wins in gtopt → n/a
    assert financing_wedge_fraction({**elem, "annual_capcost": 5.0}, 0, 0.06) is None
    # no overnight cost or no life → n/a
    assert financing_wedge_fraction({"economic_life": 20, "wacc": 0.1}, 0, 0.06) is None
    assert (
        financing_wedge_fraction({"overnight_capcost": 1.0, "wacc": 0.1}, 0, 0.06)
        is None
    )
    assert financing_wedge_fraction({**elem, "economic_life": 0}, 0, 0.06) is None
    # degenerate private rate (CRF 0) → n/a
    assert financing_wedge_fraction({**elem, "wacc": -1.0}, 0, 0.06) is None
    # power axis uses the prefixed cost, shared life and wacc
    power = {"power_overnight_capcost": 10.0, "economic_life": 20, "wacc": 0.09}
    assert financing_wedge_fraction(power, 0, 0.06, "power_") == pytest.approx(
        1.0 - crf(0.06, 20) / crf(0.09, 20)
    )
    # stage schedules: the stage's own values
    sched = {"overnight_capcost": [1.0, 1.0], "economic_life": 10, "wacc": [0.0, 0.1]}
    assert financing_wedge_fraction(sched, 0, 0.0) == pytest.approx(0.0)
    assert financing_wedge_fraction(sched, 1, 0.0) > 0.0


def test_generator_wedge_from_overnight(gen_case: Callable[..., Path]) -> None:
    # fixture social rate 0 → CRF(0, 10) = 0.1; LP capex = 1000 $
    rdir = gen_case(
        annual_capcost=None, overnight_capcost=1.0, economic_life=10, wacc=0.1
    )
    rep = compute_recovery(rdir)
    (g,) = rep.projects
    frac = 1.0 - 0.1 / crf(0.1, 10)
    assert g.capex == pytest.approx(1000.0)
    assert g.financing_wedge == pytest.approx(1000.0 * frac)
    assert g.social_capex == pytest.approx(1000.0 * (1.0 - frac))
    assert rep.total_financing_wedge == pytest.approx(1000.0 * frac)
    d = rep.to_dict()
    assert d["total_financing_wedge"] == pytest.approx(1000.0 * frac)
    assert d["projects"][0]["social_capex"] == pytest.approx(1000.0 * (1.0 - frac))
    # per-year scaling applies to the wedge too
    yearly = compute_recovery(rdir, per_year=True)
    assert yearly.projects[0].financing_wedge == pytest.approx(
        1000.0 * frac * 8760.0 / 2.0
    )


def test_generator_wedge_with_fixed_om(gen_case: Callable[..., Path]) -> None:
    """capacost_sol carries annuity + fixed O&M; the wedge uses the annuity.

    5 modules × 10 MW built, F = 8760 $/MW-yr = 1 $/MW-h → fixed O&M
    50 $/h; capacost 550 $/h over 2 h = 1100 = annuity 1000 + O&M 100.
    """
    rdir = gen_case(
        capacost_rate=550.0,
        annual_capcost=None,
        overnight_capcost=1.0,
        economic_life=10,
        wacc=0.1,
        fixed_om_cost=8760.0,
    )
    write_stream(rdir, "Generator/expmod_sol", [(0, 1, 0, 1, 5.0)])
    (g,) = compute_recovery(rdir).projects
    frac = 1.0 - 0.1 / crf(0.1, 10)
    assert g.fixed_om == pytest.approx(100.0)
    assert g.capex == pytest.approx(1000.0)
    assert g.financing_wedge == pytest.approx(1000.0 * frac)


def test_wedge_na_with_annual_capcost(
    gen_case: Callable[..., Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rdir = gen_case()
    rep = compute_recovery(rdir)
    assert rep.projects[0].financing_wedge is None
    assert rep.projects[0].social_capex is None
    assert rep.total_financing_wedge is None
    cout, jout = tmp_path / "w.csv", tmp_path / "w.json"
    assert main([str(rdir), "--csv-out", str(cout), "--json-out", str(jout)]) == 0
    out = capsys.readouterr().out
    assert "fin. wedge" in out and "n/a" in out
    assert "TIMES INV+" not in out  # explanation only when a wedge exists
    with open(cout, encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["financing_wedge"] == "" and row["social_capex"] == ""
    assert json.loads(jout.read_text(encoding="utf-8"))["total_financing_wedge"] is None


def test_cli_prints_wedge_and_comparison(
    gen_case: Callable[..., Path], capsys: pytest.CaptureFixture[str]
) -> None:
    rdir = gen_case(
        annual_capcost=None, overnight_capcost=1.0, economic_life=10, wacc=0.1
    )
    assert main([str(rdir), str(rdir)]) == 0
    out = capsys.readouterr().out
    wedge = 1000.0 * (1.0 - 0.1 / crf(0.1, 10))
    assert f"{wedge:,.0f}" in out
    assert "TIMES INV+" in out
    assert "financing wedge" in out  # comparison row


def test_report_total_skips_unknown() -> None:
    rep = RecoveryReport(
        results_dir="x",
        horizon_hours=1.0,
        scale=1.0,
        obj_value=None,
        pricing_mode="fixed",
    )
    rep.projects = [
        ProjectRecovery(
            kind="Generator", name="a", uid=1, unit="MW", financing_wedge=5.0
        ),
        ProjectRecovery(kind="Generator", name="b", uid=2, unit="MW"),
    ]
    assert rep.total_financing_wedge == pytest.approx(5.0)


def _battery_planning(energy: dict[str, Any]) -> dict[str, Any]:
    return {
        "options": {"annual_discount_rate": 0.05},
        "simulation": sim([1, 1]),
        "system": {
            "bus_array": [{"uid": 1, "name": "b1"}],
            "generator_array": [{"uid": 2, "name": "bess_gen", "bus": "b1"}],
            "demand_array": [{"uid": 2, "name": "bess_dem", "bus": "b1"}],
            "battery_array": [
                {
                    "uid": 1,
                    "name": "bess",
                    "capacity": 0,
                    "expcap": 10,
                    "expmod": 10,
                    "power_overnight_capcost": 1000.0,
                    "economic_life": 10,
                    "wacc": 0.1,
                    **energy,
                }
            ],
            "converter_array": [
                {
                    "uid": 1,
                    "name": "bess_conv",
                    "capacity": 0,
                    "expcap": 10,
                    "expmod": 10,
                }
            ],
        },
    }


_BAT_STREAMS = {
    "Bus/balance_dual": [(1, 1, 1, 1, 10.0), (1, 1, 2, 1, 50.0)],
    "Battery/capainst_sol": [(0, 1, 0, 1, 20.0)],
    "Battery/capacost_sol": [(0, 1, 0, 1, 20.0)],
    "Converter/capainst_sol": [(0, 1, 0, 1, 20.0)],
    "Converter/capacost_sol": [(0, 1, 0, 1, 100.0)],
}


def test_battery_wedge_both_axes(tmp_path: Path) -> None:
    planning = _battery_planning({"overnight_capcost": 500.0})
    (b,) = compute_recovery(write_case(tmp_path, planning, _BAT_STREAMS)).projects
    frac = 1.0 - crf(0.05, 10) / crf(0.1, 10)
    assert b.total_capex == pytest.approx(240.0)
    assert b.financing_wedge == pytest.approx(240.0 * frac)


def test_battery_wedge_na_when_one_axis_unknown(tmp_path: Path) -> None:
    planning = _battery_planning({"annual_capcost": 8760.0})
    (b,) = compute_recovery(write_case(tmp_path, planning, _BAT_STREAMS)).projects
    assert b.financing_wedge is None


def _golden(rel: str) -> Path:
    path = CASES / rel
    if not (path / "planning.json").is_file():
        pytest.skip(f"golden {rel} not present")
    return path


def test_golden_wacc_case_wedge() -> None:
    """invest_wacc_1b: one 8760-h stage, social 6 %, per-candidate wacc."""
    path = _golden("invest_wacc_1b/golden/project_lp")
    rep = compute_recovery(path)
    planning = json.loads((path / "planning.json").read_text(encoding="utf-8"))
    elems = {g["name"]: g for g in planning["system"]["generator_array"]}
    for p in rep.projects:
        e = elems[p.name]
        social = p.new * e["overnight_capcost"] * crf(0.06, e["economic_life"])
        assert p.social_capex == pytest.approx(social, rel=1e-6, abs=1e-3), p.name
    assert rep.total_financing_wedge == pytest.approx(18_866_401.0, rel=1e-6)


def test_golden_uniform_rate_has_no_wedge() -> None:
    rep = compute_recovery(_golden("invest_wacc_1b/golden/uniform_lp"))
    assert rep.total_financing_wedge == pytest.approx(0.0)


def test_golden_multi_line_below_social_rate() -> None:
    rep = compute_recovery(_golden("invest_multi_3b/golden/lp"))
    by = {p.name: p for p in rep.projects}
    assert by["north_city"].financing_wedge is not None
    assert by["north_city"].financing_wedge < 0.0  # regulated line wacc < 6 %
    assert by["bess"].financing_wedge is not None
    assert by["bess"].financing_wedge > 0.0
