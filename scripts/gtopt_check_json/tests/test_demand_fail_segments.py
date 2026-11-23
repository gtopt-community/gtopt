# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for the ``demand_fail_segments`` check (stepwise VoLL curves)."""

from typing import Any

from gtopt_check_json._checks import Severity, check_demand_fail_segments


def _case(segments: Any = None, global_segments: Any = None) -> dict[str, Any]:
    demand: dict[str, Any] = {"uid": 1, "name": "d1", "bus": 1, "lmax": 100}
    if segments is not None:
        demand["fail_segments"] = segments
    case: dict[str, Any] = {"system": {"demand_array": [demand]}}
    if global_segments is not None:
        case["options"] = {"model_options": {"demand_fail_segments": global_segments}}
    return case


def test_valid_curve_no_findings() -> None:
    segs = [
        {"fraction": 0.05, "cost": 492.4},
        {"fraction": 0.05, "cost": 541.7},
        {"fraction": 0.1, "cost": [648.6, 700.0]},
        {"cost": 742.1},
    ]
    assert not check_demand_fail_segments(_case(segs))


def test_no_curve_no_findings() -> None:
    assert not check_demand_fail_segments(_case())


def test_bad_fraction_and_open_middle_are_critical() -> None:
    findings = check_demand_fail_segments(
        _case([{"fraction": 0, "cost": 1}, {"cost": 2}, {"fraction": 0.5, "cost": 3}])
    )
    msgs = " ".join(f.message for f in findings)
    assert all(f.severity == Severity.CRITICAL for f in findings)
    assert "must be in (0, 1]" in msgs
    assert "only the last is open" in msgs


def test_sum_above_one_and_missing_cost() -> None:
    findings = check_demand_fail_segments(
        _case([{"fraction": 0.7, "cost": 1}, {"fraction": 0.7}])
    )
    msgs = " ".join(f.message for f in findings)
    assert "sum to 1.4" in msgs
    assert "cost is required" in msgs


def test_empty_open_segment() -> None:
    findings = check_demand_fail_segments(
        _case([{"fraction": 1.0, "cost": 1}, {"cost": 2}])
    )
    assert any("open last segment is empty" in f.message for f in findings)


def test_decreasing_costs_warn() -> None:
    findings = check_demand_fail_segments(
        _case([{"fraction": 0.5, "cost": 900}, {"cost": 300}])
    )
    assert [f.severity for f in findings] == [Severity.WARNING]


def test_global_curve_requires_scalar_costs() -> None:
    findings = check_demand_fail_segments(
        _case(global_segments=[{"fraction": 0.5, "cost": [1, 2]}, {"cost": 3}])
    )
    assert any("must be a scalar" in f.message for f in findings)
    assert "model_options.demand_fail_segments" in findings[0].message


def test_non_list_and_too_many() -> None:
    assert check_demand_fail_segments(_case(global_segments={"cost": 1}))
    many = [{"fraction": 0.01, "cost": float(k)} for k in range(17)]
    findings = check_demand_fail_segments(_case(many))
    assert any("max 16" in f.message for f in findings)
    bad = check_demand_fail_segments(_case(["x", {"cost": 1}]))
    assert any("not an object" in f.message for f in bad)
