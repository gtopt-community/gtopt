"""Tests for plexos2gtopt.uc_cycle (--uc-cycle / --long-transition)."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from plexos2gtopt.main import make_parser
from plexos2gtopt.uc_cycle import apply_uc_cycle, find_long_units, stage_cycle_hours


def _planning(hours: int = 24) -> dict[str, Any]:
    return {
        "simulation": {
            "block_array": [{"uid": i + 1, "duration": 1.0} for i in range(hours)],
            "stage_array": [
                {
                    "uid": 1,
                    "first_block": 0,
                    "count_block": hours,
                    "chronological": True,
                }
            ],
        },
        "system": {
            "commitment_array": [
                {
                    "uid": 1,
                    "name": "uc_coal",
                    "min_up_time": 72.0,
                    "min_down_time": 48.0,
                    "initial_status": 1.0,
                },
                {"uid": 2, "name": "uc_gt", "min_up_time": 2.0},
            ]
        },
    }


def test_cycle_hours_and_period_hours() -> None:
    p = _planning(24)
    assert stage_cycle_hours(p) == [24.0]
    p["simulation"]["stage_array"][0]["period_hours"] = 168.0
    assert stage_cycle_hours(p) == [168.0]


def test_no_cycle_is_a_no_op() -> None:
    p = _planning()
    assert not apply_uc_cycle(p, cycle="none", long_transition="on")
    assert "cycle" not in p["system"]["commitment_array"][0]


def test_flag_off_warns_and_leaves_undeclared(caplog: pytest.LogCaptureFixture) -> None:
    p = _planning()
    with caplog.at_level(logging.WARNING):
        longs = apply_uc_cycle(p, cycle="stage")
    assert {(u.name, u.constraint) for u in longs} == {
        ("uc_coal", "min_up_time"),
        ("uc_coal", "min_down_time"),
    }
    cms = p["system"]["commitment_array"]
    assert all(cm["cycle"] == "stage" for cm in cms)
    assert "initial_status" not in cms[0]
    assert "long_transition" not in cms[0]
    assert "undeclared" in caplog.text


@pytest.mark.parametrize("mode", ["on", "off", "no_uc"])
def test_flag_declares_only_long_units(mode: str) -> None:
    p = _planning()
    apply_uc_cycle(p, cycle="stage", long_transition=mode)
    cms = p["system"]["commitment_array"]
    assert cms[0]["long_transition"] == mode
    assert "long_transition" not in cms[1]


def test_week_fits() -> None:
    p = _planning(168)
    apply_uc_cycle(p, cycle="stage")
    assert not find_long_units(p)


def test_invalid_mode() -> None:
    with pytest.raises(ValueError):
        apply_uc_cycle(_planning(), cycle="stage", long_transition="maybe")


def test_cli_flags() -> None:
    parser = make_parser()
    args = parser.parse_args(["x", "--uc-cycle", "stage", "--long-transition", "no_uc"])
    assert args.uc_cycle == "stage"
    assert args.long_transition == "no_uc"
