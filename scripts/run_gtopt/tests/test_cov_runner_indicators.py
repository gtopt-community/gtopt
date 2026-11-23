# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for run_gtopt._runner energy indicators and the results summary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from run_gtopt import _runner
from run_gtopt._runner import (
    _compute_energy_indicators,
    _count_output_classes,
    _count_output_files,
    _format_mb,
    _format_size,
    _report_solution_plain,
    _resource_summary_rows,
    _total_dir_size,
    report_solution,
)

_SOL_HEADER = "scene,phase,status,obj_value,scale_objective\n"


def _write(results: Path, stem: str, df: pd.DataFrame) -> None:
    path = results / f"{stem}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _long(rows: list[tuple[int, int, int, int, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["scenario", "stage", "block", "uid", "value"])


def _planning(tmp_path: Path, **overrides: Any) -> Path:
    data: dict[str, Any] = {
        "options": {"scale_objective": 10.0},
        "simulation": {
            "block_array": [
                {"uid": 1, "duration": 2.0},
                {"uid": 2, "duration": 3.0},
            ],
            "stage_array": [
                {"uid": 1, "first_block": 0, "count_block": 1},
                {"uid": 2, "first_block": 1, "count_block": -1, "discount_factor": 0.5},
            ],
        },
        "system": {
            "generator_array": [
                {"uid": 1, "bus": 1, "type": "thermal"},
                {"uid": 2, "bus": 1},
                "not-a-dict",
            ],
            "demand_array": [
                {"uid": 10, "bus": 1, "fcost": 1000.0},
                {"uid": 11, "bus": 2, "fcost": 2000},
                "skip",
            ],
        },
    }
    data.update(overrides)
    path = tmp_path / "planning.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture(name="full_results")
def _full_results(tmp_path: Path) -> Path:
    res = tmp_path / "results"
    # Two scenarios -> expected value is the mean over scenarios.
    _write(
        res,
        "Generator/generation_sol",
        _long(
            [
                (1, 1, 1, 1, 100.0),
                (2, 1, 1, 1, 100.0),
                (1, 2, 2, 2, 50.0),
                (2, 2, 2, 2, 50.0),
            ]
        ),
    )
    _write(
        res,
        "Generator/srmc_sol",
        _long(
            [
                (1, 1, 1, 1, 10.0),
                (2, 1, 1, 1, 10.0),
                (1, 2, 2, 2, 20.0),
                (2, 2, 2, 2, 20.0),
            ]
        ),
    )
    _write(
        res,
        "Demand/load_sol",
        _long(
            [
                (1, 1, 1, 10, 90.0),
                (1, 2, 2, 10, 40.0),
                (1, 1, 1, 99, 500.0),  # battery-like, not in demand_array
            ]
        ),
    )
    _write(res, "Demand/fail_sol", _long([(1, 1, 1, 10, 5.0)]))
    _write(
        res,
        "Bus/balance_dual",
        _long([(1, 1, 1, 1, 30.0), (1, 2, 2, 1, 50.0), (1, 1, 1, 2, 70.0)]),
    )
    return res


def test_compute_indicators_full(tmp_path: Path, full_results: Path) -> None:
    ind = _compute_energy_indicators(full_results, _planning(tmp_path))
    assert ind["scale_objective"] == pytest.approx(10.0)
    # 100 MW * 2 h + 50 MW * 3 h = 350 MWh
    assert ind["generated_twh"] == pytest.approx(350e-6)
    # stage 2 discounted by 0.5: 200 + 150*0.5
    assert ind["discounted_energy_twh"] == pytest.approx(275e-6)
    assert ind["gentech::thermal"] == pytest.approx(200e-6)
    assert ind["gentech::other"] == pytest.approx(150e-6)
    # 100*10*2 + 50*20*3 = 5000
    assert ind["production_cost"] == pytest.approx(5000.0)
    # uid 99 filtered out: 90*2 + 40*3 = 300 MWh
    assert ind["served_twh"] == pytest.approx(300e-6)
    assert ind["lcoe_op_per_served"] == pytest.approx(5000.0 / 300.0)
    assert ind["shed_twh"] == pytest.approx(10e-6)
    assert ind["unserved_cost"] == pytest.approx(10.0 * 1500.0)
    assert ind["losses_twh"] == pytest.approx(50e-6)
    assert ind["losses_pct_served"] == pytest.approx(100.0 * 50.0 / 300.0)
    # load uid 10 on bus 1: 90*30*2 + 40*50*3
    assert ind["withdrawal_cost"] == pytest.approx(5400.0 + 6000.0)
    # gen uids 1, 2 on bus 1: 100*30*2 + 50*50*3
    assert ind["injection_value"] == pytest.approx(6000.0 + 7500.0)
    assert ind["avg_lmp"] == pytest.approx((30 * 180 + 50 * 120) / 300.0)
    assert ind["lmp_min"] == pytest.approx(30.0)
    assert ind["lmp_max"] == pytest.approx(70.0)


def test_compute_indicators_annual_discount_rate(
    tmp_path: Path, full_results: Path
) -> None:
    plan = _planning(
        tmp_path,
        options={"model_options": {"annual_discount_rate": 0.1}},
    )
    ind = _compute_energy_indicators(full_results, plan)
    assert ind["scale_objective"] == pytest.approx(1000.0)
    assert ind["discounted_energy_twh"] < 275e-6


def test_compute_indicators_without_planning_json(full_results: Path) -> None:
    """Regression: a missing planning JSON used to raise NameError on
    ``data`` and silently drop every indicator after ``generated_twh``."""
    ind = _compute_energy_indicators(full_results, None)
    assert ind["scale_objective"] == pytest.approx(1000.0)
    assert ind["generated_twh"] == pytest.approx(150e-6)
    # No demand_array -> every load row counts (uid 99 included).
    assert ind["served_twh"] == pytest.approx(630e-6)
    assert ind["shed_twh"] == pytest.approx(5e-6)
    assert "unserved_cost" not in ind  # no fcost without the JSON
    assert "avg_lmp" in ind
    assert "withdrawal_cost" not in ind  # bus mapping unknown


def test_compute_indicators_wide_layout(tmp_path: Path) -> None:
    res = tmp_path / "results"
    wide = pd.DataFrame(
        {
            "scenario": [1, 2, 1],
            "block": [1, 1, 2],
            "uid:1": [10.0, 30.0, 5.0],
            "uid:2": [1.0, 1.0, 1.0],
        }
    )
    _write(res, "Generator/generation_sol", wide)
    # srmc present but wide -> production cost 0 -> omitted.
    _write(res, "Generator/srmc_sol", wide)
    # Wide without scenario column, and no uid columns at all.
    _write(res, "Demand/load_sol", pd.DataFrame({"block": [1], "uid:10": [4.0]}))
    _write(res, "Line/loss_sol", pd.DataFrame({"block": [1], "x": [1.0]}))
    _write(res, "Demand/fail_sol", pd.DataFrame({"stage": [1], "uid:10": [1.0]}))
    ind = _compute_energy_indicators(res, None)
    # block1: mean(11, 31)=21, block2: 6 ; durations default 1
    assert ind["generated_twh"] == pytest.approx(27e-6)
    assert "production_cost" not in ind
    assert ind["served_twh"] == pytest.approx(4e-6)
    assert ind["losses_twh"] == pytest.approx(0.0)
    assert "losses_pct_served" not in ind
    assert "shed_twh" not in ind


def test_operational_and_dual_cost_degenerate_inputs(tmp_path: Path) -> None:
    res = tmp_path / "results"
    # Generation lacks the "stage" key -> operational cost skipped.
    gen = pd.DataFrame(
        {"scenario": [1], "block": [1], "uid": [1], "value": [10.0]},
    )
    _write(res, "Generator/generation_sol", gen)
    _write(res, "Generator/srmc_sol", gen)
    # Load whose uid is not in the bus map -> withdrawal skipped,
    # and the LMP average falls back to the duration weighting.
    _write(res, "Demand/load_sol", _long([(1, 1, 1, 77, 5.0)]))
    _write(res, "Bus/balance_dual", _long([(1, 1, 1, 1, 40.0)]))
    plan = _planning(tmp_path)
    ind = _compute_energy_indicators(res, plan)
    assert "production_cost" not in ind
    assert "withdrawal_cost" not in ind
    # Generator uid 1 is on bus 1 -> injection = 10 * 40 * 2
    assert ind["injection_value"] == pytest.approx(800.0)
    assert ind["avg_lmp"] == pytest.approx(40.0)


def test_operational_cost_no_overlap_and_dual_missing_columns(
    tmp_path: Path,
) -> None:
    res = tmp_path / "results"
    _write(res, "Generator/generation_sol", _long([(1, 1, 1, 1, 10.0)]))
    _write(res, "Generator/srmc_sol", _long([(2, 1, 1, 1, 10.0)]))
    _write(
        res,
        "Bus/balance_dual",
        pd.DataFrame({"block": [1], "uid": [1], "value": [5.0]}),
    )
    _write(res, "Demand/load_sol", _long([(1, 1, 1, 10, 0.0)]))
    ind = _compute_energy_indicators(res, _planning(tmp_path))
    assert "production_cost" not in ind
    assert "injection_value" not in ind  # dual lacks scenario column
    assert "withdrawal_cost" not in ind
    # Zero load weights -> fall back to duration-weighted LMP.
    assert ind["avg_lmp"] == pytest.approx(5.0)


def test_dual_weighted_cost_no_matching_scenario(tmp_path: Path) -> None:
    res = tmp_path / "results"
    _write(res, "Generator/generation_sol", _long([(1, 1, 1, 1, 10.0)]))
    _write(res, "Bus/balance_dual", _long([(9, 1, 1, 1, 5.0)]))
    _write(res, "Demand/load_sol", pd.DataFrame({"block": [1], "uid:10": [1.0]}))
    ind = _compute_energy_indicators(res, _planning(tmp_path))
    assert "injection_value" not in ind
    assert ind["avg_lmp"] == pytest.approx(5.0)


def test_compute_indicators_no_block_column(tmp_path: Path) -> None:
    res = tmp_path / "results"
    _write(res, "Generator/generation_sol", pd.DataFrame({"uid": [1], "value": [1]}))
    ind = _compute_energy_indicators(res, None)
    assert ind["generated_twh"] == 0.0


def test_compute_indicators_bad_json_is_swallowed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert not _compute_energy_indicators(tmp_path, bad)


# ---------------------------------------------------------------------------
# Small formatting / inventory helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nbytes", "expected"),
    [
        (5, "5 B"),
        (2048, "2.0 KB"),
        (3 * 1024**2, "3.0 MB"),
        (2 * 1024**3, "2.0 GB"),
        (5 * 1024**4, "5.0 TB"),
    ],
)
def test_format_size(nbytes: int, expected: str) -> None:
    assert _format_size(nbytes) == expected


def test_format_mb() -> None:
    assert _format_mb(512.0) == "512 MB"
    assert _format_mb(2048.0) == "2.0 GB"


def test_inventory_helpers(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    assert not _count_output_files(missing)
    assert not _count_output_classes(missing)
    assert _total_dir_size(missing) == 0

    (tmp_path / "Generator").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "Generator" / "a.parquet").write_bytes(b"12345")
    (tmp_path / "b.csv").write_text("x", encoding="utf-8")
    (tmp_path / "c.txt").write_text("x", encoding="utf-8")
    assert _count_output_files(tmp_path) == {".parquet": 1, ".csv": 1}
    assert _count_output_classes(tmp_path) == ["Generator"]
    assert _total_dir_size(tmp_path) == 7


# ---------------------------------------------------------------------------
# Resource summary
# ---------------------------------------------------------------------------


def _status(tmp_path: Path, payload: Any) -> Path:
    (tmp_path / "solver_status.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )
    return tmp_path


def test_resource_rows_missing_and_invalid(tmp_path: Path) -> None:
    assert not _resource_summary_rows(tmp_path)
    assert not _resource_summary_rows(_status(tmp_path, "{bad"))


def test_resource_rows_realtime_series(tmp_path: Path) -> None:
    rows = dict(
        _resource_summary_rows(
            _status(
                tmp_path,
                {
                    "realtime": {
                        "process_rss_mb": [100.0, 3000.0, 2000.0],
                        "cpu_loads": [50, 100],
                        "available_memory_mb": [4096.0],
                        "memory_percent": [42.0],
                    },
                    "lp_task_stats": {
                        "dispatched": 3,
                        "avg_cpu_pct": 90,
                        "avg_rss_delta_mb": 12,
                    },
                },
            )
        )
    )
    assert rows["gtopt memory (RSS)"] == "2.0 GB  (peak 2.9 GB)"
    assert rows["System free memory"] == "4.0 GB  (42% used)"
    assert rows["CPU utilization"] == "100%  (avg 75%)"
    assert rows["LP pool"] == "3 tasks  avg CPU 90%  avg ΔRSS 12 MB"


def test_resource_rows_pool_fallback(tmp_path: Path) -> None:
    rows = dict(
        _resource_summary_rows(
            _status(
                tmp_path,
                {
                    "pool_process_rss_mb": 256.0,
                    "pool_available_memory_mb": 0.0,  # stale -> ignored
                    "pool_memory_percent": 10.0,
                },
            )
        )
    )
    assert rows == {"gtopt memory (RSS)": "256 MB"}


def test_resource_rows_free_without_percent(tmp_path: Path) -> None:
    rows = dict(
        _resource_summary_rows(
            _status(tmp_path, {"realtime": {"available_memory_mb": [100.0]}})
        )
    )
    assert rows == {"System free memory": "100 MB"}


# ---------------------------------------------------------------------------
# report_solution
# ---------------------------------------------------------------------------


def _capture_table(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import gtopt_check_json._terminal as term  # pylint: disable=import-outside-toplevel

    captured: dict[str, Any] = {}

    def _fake_print_table(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(term, "print_table", _fake_print_table)
    return captured


def test_report_solution_all_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_table(monkeypatch)
    res = tmp_path / "results"
    res.mkdir()
    (res / "solution.csv").write_text(
        "scene,phase,status,obj_value,scale_objective,gtopt_version,gtopt_revision\n"
        "0,0,0,1000.0,1,1.2.3,abc123\n",
        encoding="utf-8",
    )
    (res / "logs").mkdir()
    (res / "logs" / "gtopt.log").write_text("x", encoding="utf-8")
    _status(res, {"realtime": {"cpu_loads": [10]}})
    indicators = {
        "scale_objective": 1.0,
        "generated_twh": 2.0,
        "discounted_energy_twh": 1.0,
        "gentech::hydro": 1.5,
        "gentech::solar": 0.5,
        "served_twh": 1.0,
        "shed_twh": 0.1,
        "losses_twh": 0.01,
        "losses_pct_served": 1.0,
        "production_cost": 10.0,
        "withdrawal_cost": 20.0,
        "injection_value": 30.0,
        "unserved_cost": 40.0,
        "avg_lmp": 50.0,
        "lmp_min": 1.0,
        "lmp_max": 99.0,
    }
    monkeypatch.setattr(
        _runner, "_compute_energy_indicators", lambda _r, _p: dict(indicators)
    )
    report_solution(res, tmp_path, None)
    rows = dict(captured["rows"])
    assert rows["Status"] == "OPTIMAL"
    assert rows["Objective value"] == "$1,000.00"
    assert rows["  hydro"] == "1.500 TWh (75.0%)"
    assert rows["Load shedding"] == "0.100 TWh"
    assert rows["Total losses"] == "10.0 GWh  (1.0% of served)"
    assert "/MWh served" in rows["Production cost (SRMC)"]
    assert "/MWh served" in rows["Withdrawal cost (LMP)"]
    assert "/MWh gen" in rows["Injection value (LMP)"]
    assert "/MWh unserved" in rows["Unserved demand"]
    assert rows["LMP (min/avg/max)"].startswith("min $1.00")
    assert "Avg generation cost" in rows
    assert "LCOE" in rows
    assert rows["Output files"].startswith("1 (")
    assert "CPU utilization" in rows
    assert rows["gtopt"].startswith("1.2.3")
    assert rows["Log file"].endswith("gtopt.log")


def test_report_solution_sparse_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_table(monkeypatch)
    indicators = {
        "generated_twh": 0.0,
        "gentech::wind": 0.0,
        "losses_twh": 0.5,
        "production_cost": 5.0,
        "avg_lmp": 12.0,
    }
    monkeypatch.setattr(
        _runner, "_compute_energy_indicators", lambda _r, _p: dict(indicators)
    )
    (tmp_path / "solution.csv").write_text(
        _SOL_HEADER + "0,0,7,1,1\n", encoding="utf-8"
    )
    report_solution(tmp_path)
    rows = dict(captured["rows"])
    assert rows["Status"] == "UNKNOWN (7)"
    assert rows["  wind"] == "0.000 TWh (0.0%)"
    assert rows["Total losses"] == "500.0 GWh"
    assert rows["Production cost (SRMC)"] == "$5.00"
    assert rows["LMP (min/avg/max)"] == "$12.00/MWh"
    assert "Avg generation cost" not in rows
    assert "LCOE" not in rows


def test_report_solution_falls_back_to_plain_without_rich(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import sys  # pylint: disable=import-outside-toplevel

    monkeypatch.setitem(sys.modules, "gtopt_check_json._terminal", None)
    (tmp_path / "solution.csv").write_text(
        _SOL_HEADER + "0,0,1,2.5,1\n", encoding="utf-8"
    )
    report_solution(tmp_path)
    out = capsys.readouterr().out
    assert "Status: NON-OPTIMAL / INFEASIBLE" in out
    assert "Objective: 2.5" in out


def test_report_solution_plain_variants(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _report_solution_plain(tmp_path)
    assert "UNKNOWN (None)" in capsys.readouterr().out
    (tmp_path / "solution.csv").write_text(_SOL_HEADER + "0,0,9,1,1\n", "utf-8")
    _report_solution_plain(tmp_path)
    assert "UNKNOWN (9)" in capsys.readouterr().out


def test_lmp_negative_values_and_empty_dual(tmp_path: Path) -> None:
    res = tmp_path / "results"
    _write(res, "Generator/generation_sol", _long([(1, 1, 1, 1, 10.0)]))
    _write(res, "Demand/load_sol", _long([(1, 1, 1, 10, 10.0)]))
    _write(res, "Bus/balance_dual", _long([(1, 1, 1, 1, -5.0)]))
    ind = _compute_energy_indicators(res, _planning(tmp_path))
    assert "withdrawal_cost" not in ind  # negative -> not reported
    assert "injection_value" not in ind
    assert ind["avg_lmp"] == pytest.approx(-5.0)

    empty = tmp_path / "empty"
    _write(empty, "Bus/balance_dual", _long([]))
    ind = _compute_energy_indicators(empty, None)
    assert "avg_lmp" not in ind
    assert "lmp_min" not in ind
