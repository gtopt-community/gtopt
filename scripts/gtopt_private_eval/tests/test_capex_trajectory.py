# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Capex trajectories (local base x ATB index)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtopt_private_eval.capex_trajectory import (
    ATB_2024_INDEX,
    LOCAL_BASES,
    apply_to_planning,
    capex_factor,
    index_at,
    indices_from_atb_csv,
    main,
    trajectory,
)


def test_index_interpolation() -> None:
    assert index_at(ATB_2024_INDEX, "bess", "moderate", 2025) == 1.0
    assert index_at(ATB_2024_INDEX, "bess", "moderate", 2020) == 1.0
    mid = index_at(ATB_2024_INDEX, "bess", "moderate", 2028.5)
    assert mid == pytest.approx((0.9353 + 0.8382) / 2)
    assert index_at(ATB_2024_INDEX, "bess", "moderate", 2070) == pytest.approx(0.5798)
    with pytest.raises(KeyError):
        index_at(ATB_2024_INDEX, "bess", "nope", 2030)


def test_factor_and_trajectory() -> None:
    assert capex_factor("thermal", "moderate", 2040, 2026) == 1.0
    f = capex_factor("solar_pv", "moderate", 2030, 2026)
    assert 0.8 < f < 1.0
    rows = trajectory("solar_pv", "advanced", [2026, 2035], 732.0, 2026)
    assert rows[0]["capex"] == pytest.approx(732.0)
    assert rows[1]["capex"] < 0.6 * 732.0
    # every technology falls (or stays) over time in every scenario
    for tech, scen in ATB_2024_INDEX.items():
        for name in scen:
            assert capex_factor(tech, name, 2050, 2025) <= 1.0


def test_apply_to_planning() -> None:
    planning = {
        "system": {
            "generator_array": [{"name": "pv"}, {"name": "x"}],
            "battery_array": [{"name": "b"}],
        }
    }
    changed = apply_to_planning(
        planning,
        {"pv": "solar_pv", "b": "bess", "x": "unknown"},
        [2026, 2030],
        "moderate",
        LOCAL_BASES["cl_cne_2026"],
    )
    assert changed == ["pv", "b"]
    pv = planning["system"]["generator_array"][0]["overnight_capcost"]
    assert pv[0] == pytest.approx(732000.0)
    assert pv[1] < pv[0]
    assert "overnight_capcost" not in planning["system"]["generator_array"][1]


def test_indices_from_atb_csv(tmp_path: Path) -> None:
    csv = tmp_path / "atb.csv"
    lines = [
        "core_metric_parameter,core_metric_case,technology,techdetail,"
        "scenario,core_metric_variable,value",
    ]
    for y, v in ((2025, 100.0), (2030, 80.0)):
        lines.append(f"OCC,Market,UtilityPV,Class5,Moderate,{y},{v}")
        lines.append(f"OCC,R&D,UtilityPV,Class5,Moderate,{y},{v * 2}")
    lines.append("OCC,Market,UtilityPV,Class5,Advanced,2030,50.0")
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tab = indices_from_atb_csv(csv, {"solar_pv": ("UtilityPV", "Class5")})
    assert tab["solar_pv"]["moderate"] == {2025: 1.0, 2030: 0.8}
    assert "advanced" not in tab["solar_pv"]  # no base year


def test_cli_table_json_planning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--tech", "bess", "--years", "2026", "2030"]) == 0
    assert "bess" in capsys.readouterr().out
    assert main(["--tech", "wind", "--scenario", "advanced", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["tech"] == "wind" and rows[0]["scenario"] == "advanced"
    assert main(["--tech", "coal"]) == 2  # no CNE base value
    assert main(["--tech", "coal", "--base-value", "3000", "--json"]) == 0
    capsys.readouterr()
    idx = tmp_path / "idx.json"
    idx.write_text(json.dumps({"wind": {"steps": {"2026": 1.0, "2030": 0.9}}}))
    assert (
        main(
            [
                "--tech",
                "wind",
                "--scenario",
                "steps",
                "--index-json",
                str(idx),
                "--json",
            ]
        )
        == 0
    )
    rows = json.loads(capsys.readouterr().out)
    assert rows[-1]["index"] == pytest.approx(0.9)
    plan = tmp_path / "case.json"
    plan.write_text(json.dumps({"system": {"generator_array": [{"name": "pv"}]}}))
    assert main(["--planning", str(plan), "--map", "pv=solar_pv"]) == 2
    out = tmp_path / "out.json"
    assert (
        main(
            [
                "--planning",
                str(plan),
                "--map",
                "pv=solar_pv",
                "--stage-years",
                "2026",
                "2031",
                "-o",
                str(out),
            ]
        )
        == 0
    )
    data = json.loads(out.read_text())
    assert len(data["system"]["generator_array"][0]["overnight_capcost"]) == 2
    assert (
        main(["--planning", str(plan), "--map", "pv=solar_pv", "--stage-years", "2026"])
        == 0
    )
    assert (tmp_path / "case_capex.json").is_file()
    csv = tmp_path / "atb.csv"
    csv.write_text(
        "core_metric_parameter,core_metric_case,technology,techdetail,scenario,"
        "core_metric_variable,value\n"
        "OCC,Market,UtilityPV,Class5,Moderate,2025,100\n"
        "OCC,Market,UtilityPV,Class5,Moderate,2030,90\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    assert main(["--tech", "solar_pv", "--atb-csv", str(csv), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[1]["index"] == pytest.approx(0.9 / 0.98)  # base 2026
