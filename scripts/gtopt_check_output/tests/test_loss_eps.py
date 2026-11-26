# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Loss-eps acceptance check: bus-hours priced below −ε (no solver)."""

from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd

from gtopt_check_output._checks import check_loss_eps, run_all_checks
from gtopt_check_output._loss_arbitrage import (
    DEFAULT_LOSS_COST_EPS,
    EPS_INVALIDATED,
    detect_loss_eps_invalidated,
)

# L1: b1-b2, R/V² = 0.01; L2: b2-b3 without resistance (no loss guard).
_PLANNING = {
    "options": {},
    "system": {
        "bus_array": [
            {"uid": 1, "name": "b1"},
            {"uid": 2, "name": "b2"},
            {"uid": 3, "name": "b3"},
            {"uid": 4, "name": "island"},
        ],
        "line_array": [
            {
                "uid": 1,
                "name": "L1",
                "bus_a": "b1",
                "bus_b": "b2",
                "resistance": 0.01,
                "voltage": 1.0,
            },
            {"uid": 2, "name": "L2", "bus_a": 2, "bus_b": "b3"},
        ],
    },
}


def _write(path: Path, rows: list[tuple[int, int, int, int, float]]) -> None:
    part = path.with_suffix(".parquet") / "scene=0" / "phase=0"
    part.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=["scenario", "stage", "block", "uid", "value"])
    frame.to_parquet(part / "part.parquet", index=False)


def _results(tmp_path: Path, duals: list[tuple[int, int, int, int, float]]) -> Path:
    res = tmp_path / "results"
    # Block 1: f = 10, analytic 1.0, booked 1.5 → excess 0.5 MW.
    # Block 2: f = 10, booked 0.9 (tangent under-approximation) → clean.
    _write(res / "Line" / "flow_sol", [(1, 1, 1, 1, 10.0), (1, 1, 2, 1, 10.0)])
    _write(res / "Line" / "loss_sol", [(1, 1, 1, 1, 1.5), (1, 1, 2, 1, 0.9)])
    _write(res / "Bus" / "balance_dual", duals)
    return res


def test_default_eps_is_gtopt_builtin() -> None:
    assert DEFAULT_LOSS_COST_EPS == 0.1


def test_prices_at_or_above_minus_eps_pass(tmp_path: Path) -> None:
    res = _results(
        tmp_path,
        [
            (1, 1, 1, 1, -0.1),
            (1, 1, 1, 2, 0.0),
            (1, 1, 2, 1, 30.0),
            (1, 1, 2, 2, -0.05),
        ],
    )
    assert not detect_loss_eps_invalidated(res, _PLANNING)
    (finding,) = check_loss_eps(res, _PLANNING)
    assert (finding.severity, finding.message) == (
        "INFO",
        "loss eps holds: no bus price below -eps",
    )


def test_price_below_minus_eps_is_flagged_with_excess(tmp_path: Path) -> None:
    res = _results(
        tmp_path,
        [
            (1, 1, 1, 1, -5.0),  # b1 block 1: excess 0.5 on L1
            (1, 1, 2, 2, -0.2),  # b2 block 2: below -eps, no excess
            (1, 1, 1, 3, -9.0),  # b3: only a lossless line -> no guard
            (1, 1, 1, 4, -9.0),  # island: no line at all
        ],
    )
    viol = detect_loss_eps_invalidated(res, _PLANNING)
    assert [(v.bus, v.block) for v in viol] == [("b1", 1), ("b2", 2)]
    first, second = viol
    assert first.lam == -5.0
    assert first.eps == 0.1
    assert first.excess_mw == 0.5
    assert first.lines == ("L1",)
    assert second.excess_mw == 0.0
    assert second.lines == ()

    findings = check_loss_eps(res, _PLANNING)
    assert findings[0].severity == "WARNING"
    assert findings[0].message == (
        f"{EPS_INVALIDATED}: 2 bus-hour(s) priced below -eps (1 with excess loss)"
    )
    assert findings[1].message == (
        f"{EPS_INVALIDATED}: bus b1 block 1 lambda=-5.000 < -eps=-0.1, "
        "excess loss 0.500 MW on L1"
    )
    assert "bus b2 block 2 lambda=-0.200" in findings[2].message
    # The full report runs the eps check after the loss-arbitrage one.
    report = run_all_checks(res, _PLANNING)
    eps = [f for f in report.findings if f.check == "loss_eps"]
    assert eps[0].severity == "WARNING"
    assert len(eps) == 3


def test_line_and_model_eps_overrides(tmp_path: Path) -> None:
    res = _results(tmp_path, [(1, 1, 1, 1, -5.0)])
    model = copy.deepcopy(_PLANNING)
    model["options"] = {"model_options": {"loss_cost_eps": 6.0}}
    assert not detect_loss_eps_invalidated(res, model)
    line = copy.deepcopy(_PLANNING)
    line["system"]["line_array"][0]["loss_cost_eps"] = 2.0
    (v,) = detect_loss_eps_invalidated(res, line)
    assert v.eps == 2.0
    off = copy.deepcopy(_PLANNING)
    off["system"]["line_array"][0]["loss_cost_eps"] = 0.0
    assert not detect_loss_eps_invalidated(res, off)


def test_top_limit_and_missing_streams(tmp_path: Path) -> None:
    rows = [(1, 1, b, 1, -1.0 - b) for b in range(1, 6)]
    res = _results(tmp_path, rows)
    findings = check_loss_eps(res, _PLANNING, top=2)
    assert len(findings) == 4
    assert findings[-1].message == "  ... 3 more bus-hour(s)"
    assert "block 5" in findings[1].message  # most negative first
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not detect_loss_eps_invalidated(empty, _PLANNING)
    no_lines = {
        "options": {},
        "system": {"bus_array": _PLANNING["system"]["bus_array"]},
    }
    assert not detect_loss_eps_invalidated(res, no_lines)
    # Duals without the flow / loss streams: flagged, zero excess.
    only = tmp_path / "only"
    _write(only / "Bus" / "balance_dual", [(1, 1, 1, 1, -3.0)])
    (v,) = detect_loss_eps_invalidated(only, _PLANNING)
    assert v.excess_mw == 0.0
