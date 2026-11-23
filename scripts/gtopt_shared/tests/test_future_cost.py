# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for :mod:`gtopt_shared.future_cost` (mirrors the C++ alias rules)."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from gtopt_shared.future_cost import (
    FUTURE_COST_FIELDS,
    LEGACY_ALIASES,
    effective_future_cost,
    ensure_future_cost,
    find_future_cost,
    legacy_keys_present,
    planning_method,
    resolve_legacy_future_cost_keys,
    set_future_cost_fields,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_fields_match_cpp_contract() -> None:
    header = REPO_ROOT / "include" / "gtopt" / "json" / "json_future_cost.hpp"
    fields = re.findall(r'json_[a-z_]+<"([a-z_]+)"', header.read_text())
    assert tuple(fields) == FUTURE_COST_FIELDS


def test_aliases_match_cpp_translation() -> None:
    """Every alias appears in the C++ translation with the same target."""
    src = (REPO_ROOT / "source" / "future_cost_aliases.cpp").read_text()
    for alias in LEGACY_ALIASES:
        assert f'"{alias.dotted}"' in src, alias.dotted
        assert f"&FutureCost::{alias.target}" in src, alias.target


def test_planning_method_default() -> None:
    assert planning_method({}) == "monolithic"
    assert planning_method({"options": {"method": "cascade"}}) == "cascade"


def test_ensure_creates_and_reuses() -> None:
    pl: dict = {}
    el = ensure_future_cost(pl)
    assert el == {"uid": 1, "name": "fcf"}
    assert ensure_future_cost(pl) is el
    pl2 = {"system": {"future_cost_array": [{"uid": 3, "name": "x", "active": False}]}}
    assert find_future_cost(pl2) is None
    assert ensure_future_cost(pl2)["uid"] == 4


def test_set_fields_skips_none_and_respects_overwrite() -> None:
    pl: dict = {}
    set_future_cost_fields(pl, {"cuts_file": "a.csv", "mode": None})
    set_future_cost_fields(pl, {"cuts_file": "b.csv"}, overwrite=False)
    assert pl["system"]["future_cost_array"] == [
        {"uid": 1, "name": "fcf", "cuts_file": "a.csv"}
    ]
    with pytest.raises(KeyError):
        set_future_cost_fields(pl, {"boundary_cuts_file": "x"})


@pytest.mark.parametrize("method", ["sddp", "cascade"])
def test_sddp_aliases_for_sddp(method: str) -> None:
    pl = {
        "options": {
            "method": method,
            "sddp_options": {
                "boundary_cuts_file": "bc.csv",
                "boundary_cuts_mode": "combined",
                "boundary_cut_sharing_mode": "shared",
                "boundary_cuts_mean_shift": False,
                "boundary_max_iterations": 7,
                "missing_cut_var_mode": "skip_cut",
                "max_iterations": 3,
            },
        }
    }
    report = resolve_legacy_future_cost_keys(pl)
    assert report.ok
    assert report.element == {
        "uid": 1,
        "name": "fcf",
        "cuts_file": "bc.csv",
        "mode": "combined",
        "sharing": "shared",
        "mean_shift": False,
        "iteration_limit": 7,
        "missing_cut_var_mode": "skip_cut",
    }
    assert pl["options"]["sddp_options"] == {"max_iterations": 3}
    assert len(report.warnings) == 6
    assert not legacy_keys_present(pl)


def test_sddp_aliases_ignored_for_monolithic() -> None:
    pl = {"options": {"sddp_options": {"boundary_cuts_file": "bc.csv"}}}
    report = resolve_legacy_future_cost_keys(pl)
    assert report.element is None
    assert "ignored under method=monolithic" in report.warnings[0]
    assert not pl["options"]["sddp_options"]


def test_monolithic_and_simulation_aliases() -> None:
    pl = {
        "options": {
            "monolithic_options": {
                "boundary_cuts_file": "m.csv",
                "boundary_max_iterations": 1,
            }
        },
        "simulation": {"boundary_cuts_valuation": "present_value"},
    }
    report = resolve_legacy_future_cost_keys(pl)
    assert report.element is not None
    assert report.element["cuts_file"] == "m.csv"
    assert report.element["iteration_limit"] == 1
    assert report.element["valuation"] == "present_value"


def test_conflicts() -> None:
    pl = {
        "options": {"monolithic_options": {"boundary_cuts_file": "b.csv"}},
        "simulation": {"boundary_cuts_file": "a.csv"},
    }
    report = resolve_legacy_future_cost_keys(pl)
    assert not report.ok
    assert "simulation.boundary_cuts_file" in report.errors[0]
    assert "monolithic_options.boundary_cuts_file" in report.errors[0]

    same = {
        "options": {"method": "sddp", "sddp_options": {"boundary_cuts_file": "a"}},
        "system": {"future_cost_array": [{"uid": 1, "name": "f", "cuts_file": "a"}]},
    }
    assert resolve_legacy_future_cost_keys(same).ok


def test_effective_future_cost_does_not_mutate() -> None:
    pl = {"options": {"method": "sddp", "sddp_options": {"boundary_cuts_file": "x"}}}
    snapshot = copy.deepcopy(pl)
    assert effective_future_cost(pl)["cuts_file"] == "x"
    assert pl == snapshot
    assert not effective_future_cost({})
