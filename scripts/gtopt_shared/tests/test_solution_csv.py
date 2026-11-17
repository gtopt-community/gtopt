# SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for gtopt_shared.solution_csv (physical objective resolution)."""

from __future__ import annotations

import pytest

from gtopt_shared.solution_csv import (
    CONVENTION_COLUMN,
    CONVENTION_LEGACY_RAW,
    CONVENTION_PHYSICAL,
    ScaleObjectiveMismatchError,
    default_scale_objective,
    parse_solution_csv,
    resolve_objective,
)

_CURRENT = (
    "scene,phase,status,status_name,obj_value,kappa,max_kappa,gap,gap_change,"
    "solve_ticks,solve_time_s,solve_calls,infeasible_count,scale_objective\n"
    "0,0,0,optimal,23163.42,284,284,0,1,0.01,0.002,1,0,1000\n"
    "0,1,0,optimal,99.0,284,284,0,1,0.01,0.002,1,0,1000\n"
)


def test_parse_current_layout_returns_first_row():
    row = parse_solution_csv(_CURRENT)
    assert row["obj_value"] == "23163.42"
    assert row["phase"] == "0"
    assert row["scale_objective"] == "1000"


def test_parse_legacy_key_value_layout():
    row = parse_solution_csv("obj_value,3.0\nstatus,0\n")
    assert row == {"obj_value": "3.0", "status": "0"}


def test_parse_empty_and_header_only():
    assert not parse_solution_csv("")
    assert not parse_solution_csv("scene,phase,status,obj_value\n")


def test_current_layout_is_physical_and_never_rescaled():
    obj = resolve_objective(parse_solution_csv(_CURRENT))
    assert obj is not None
    assert obj.value == pytest.approx(23163.42)
    assert obj.raw == pytest.approx(23.16342)
    assert obj.scale_objective == pytest.approx(1000.0)
    assert obj.convention == CONVENTION_COLUMN
    # The fallback is ignored when the column is present.
    obj2 = resolve_objective(parse_solution_csv(_CURRENT), fallback_scale=7.0)
    assert obj2 is not None and obj2.value == pytest.approx(23163.42)


def test_override_validates_against_column():
    row = parse_solution_csv(_CURRENT)
    obj = resolve_objective(row, scale_objective=1000.0)
    assert obj is not None and obj.value == pytest.approx(23163.42)
    with pytest.raises(ScaleObjectiveMismatchError, match="1000"):
        resolve_objective(row, scale_objective=1.0)


def test_physical_era_without_column():
    row = {"obj_value": "5000", "solve_ticks": "0.1"}
    obj = resolve_objective(row, fallback_scale=1000.0)
    assert obj is not None
    assert obj.value == pytest.approx(5000.0)
    assert obj.raw == pytest.approx(5.0)
    assert obj.convention == CONVENTION_PHYSICAL
    unknown = resolve_objective(row)
    assert unknown is not None
    assert unknown.raw is None and unknown.scale_objective is None


def test_legacy_layout_is_lp_raw():
    row = {"obj_value": "23.16342", "kappa": "1"}
    obj = resolve_objective(row, scale_objective=1000.0)
    assert obj is not None
    assert obj.value == pytest.approx(23163.42)
    assert obj.raw == pytest.approx(23.16342)
    assert obj.convention == CONVENTION_LEGACY_RAW
    # Default factor: the monolithic default.
    default = resolve_objective(row)
    assert default is not None and default.value == pytest.approx(23163.42)
    # A file known to be physical can be read with scale_objective=1.
    as_is = resolve_objective(row, scale_objective=1.0)
    assert as_is is not None and as_is.value == pytest.approx(23.16342)


@pytest.mark.parametrize("value", [None, "", "nan", "abc"])
def test_missing_or_bad_obj_value(value):
    row = {} if value is None else {"obj_value": value}
    assert resolve_objective(row) is None


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (None, None),
        ({}, 1000.0),
        ({"method": "sddp"}, 1.0),
        ({"method": "cascade"}, 1.0),
        ({"method": "sddp", "model_options": {"scale_objective": 10}}, 10.0),
        ({"scale_objective": 500}, 500.0),
    ],
)
def test_default_scale_objective(options, expected):
    assert default_scale_objective(options) == expected
