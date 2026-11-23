# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for :mod:`gtopt_shared.demand_tiers` and its CLI registrar."""

from __future__ import annotations

import argparse

import pytest

from gtopt_shared.cli_flags import add_demand_tier_segments_argument
from gtopt_shared.demand_tiers import (
    DEFAULT_TIER_SEGMENTS,
    TIER_SEGMENT_CHOICES,
    fail_segments,
    pct_fractions,
    split_fractions,
    tier_name,
    tier_uid,
    tier_uid_offset,
)


def test_choices_and_default() -> None:
    assert TIER_SEGMENT_CHOICES == ("first", "all", "split")
    assert DEFAULT_TIER_SEGMENTS == "first"


def test_tier_name_tags() -> None:
    assert tier_name("d1", 2) == "d1_falla2"
    assert tier_name("d_BUS", 3, "deficit") == "d_BUS_deficit3"


@pytest.mark.parametrize(
    ("max_uid", "offset"),
    [(0, 10), (1, 10), (9, 10), (10, 100), (99, 100), (100, 1000), (4321, 10000)],
)
def test_tier_uid_offset(max_uid: int, offset: int) -> None:
    assert tier_uid_offset(max_uid) == offset
    assert tier_uid_offset(max_uid) > max_uid


def test_tier_uid_keeps_first_and_never_collides() -> None:
    offset = tier_uid_offset(12)
    assert tier_uid(7, 1, offset) == 7
    assert tier_uid(7, 2, offset) == 107
    assert tier_uid(12, 4, offset) == 312
    uids = {tier_uid(u, k, offset) for u in range(1, 13) for k in range(1, 5)}
    assert len(uids) == 12 * 4


def test_split_fractions_normalises() -> None:
    fr = split_fractions([1.0, 3.0, 6.0])
    assert fr == pytest.approx([0.1, 0.3, 0.6])
    assert sum(fr) == 1.0
    with pytest.raises(ValueError):
        split_fractions([0.0, 0.0])


def test_pct_fractions_direct_with_open_last_segment() -> None:
    assert pct_fractions([100.0]) == [1.0]
    assert pct_fractions([2.0, 98.0]) == pytest.approx([0.02, 0.98])
    # Depths not adding to 100: the last segment takes the rest.
    assert pct_fractions([5.0, 5.0, 10.0]) == pytest.approx([0.05, 0.05, 0.9])
    assert pct_fractions([5.0, 5.0, 10.0, 90.0]) == pytest.approx(
        [0.05, 0.05, 0.1, 0.8]
    )
    assert sum(pct_fractions([3.3, 7.7, 89.0])) == 1.0


@pytest.mark.parametrize("pcts", [[], [0.0, 100.0], [-1.0, 50.0], [60.0, 40.0, 5.0]])
def test_pct_fractions_rejects_bad_depths(pcts: list[float]) -> None:
    with pytest.raises(ValueError):
        pct_fractions(pcts)


@pytest.mark.parametrize(
    ("dialect", "flag", "dest"),
    [
        ("plp", "--falla-segments", "falla_segments"),
        ("sddp", "--deficit-segments", "deficit_segments"),
    ],
)
def test_cli_registrar_dialects(dialect: str, flag: str, dest: str) -> None:
    parser = argparse.ArgumentParser()
    add_demand_tier_segments_argument(parser, dialect=dialect)  # type: ignore[arg-type]
    assert getattr(parser.parse_args([]), dest) == "first"
    assert getattr(parser.parse_args([flag, "all"]), dest) == "all"
    with pytest.raises(SystemExit):
        parser.parse_args([flag, "some"])
    help_text = parser.format_help()
    assert flag in help_text
    assert "default: first" in help_text


def test_cli_registrar_default_override() -> None:
    parser = argparse.ArgumentParser()
    add_demand_tier_segments_argument(parser, dialect="plp", default="all")
    assert parser.parse_args([]).falla_segments == "all"


def test_fail_segments_last_is_open() -> None:
    assert fail_segments([0.25, 0.75], [300.0, "fail_cost_seg2"]) == [
        {"fraction": 0.25, "cost": 300.0},
        {"cost": "fail_cost_seg2"},
    ]
    assert fail_segments([1.0], [500.0]) == [{"cost": 500.0}]
    with pytest.raises(ValueError):
        fail_segments([0.5], [1.0, 2.0])
    with pytest.raises(ValueError):
        fail_segments([], [])
