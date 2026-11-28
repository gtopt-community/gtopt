# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Daily commitment grid helpers of :mod:`gtopt_shared.point_sampling`."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtopt_shared.point_sampling import (
    STUDY_COMMITMENT_BREAKS,
    check_commitment_breaks,
    commitment_breaks_options,
    parse_commitment_breaks,
    read_commitment_breaks_file,
    stage_commitment_fields,
)

STUDY = [3.5, 7.5, 10.5, 14.5, 17.5, 23.5]


def test_study_pattern() -> None:
    """3 h sunrise / sunset cells, 4 h elsewhere, 6 h peak 17:30-23:30."""
    assert list(STUDY_COMMITMENT_BREAKS) == STUDY
    lengths = [b - a for a, b in zip(STUDY, STUDY[1:])] + [STUDY[0] + 24 - STUDY[-1]]
    assert lengths == [4, 3, 4, 3, 6, 4]


@pytest.mark.parametrize(
    "spec",
    [
        "3.5,7.5,10.5,14.5,17.5,23.5",
        "3.5;7.5;10.5;14.5;17.5;23.5",
        " 3.5 7.5 10.5 14.5 17.5 23.5 ",
        "3.5, 7.5, 10.5, 14.5, 17.5, 23.5",
        "study",
        "STUDY",
    ],
)
def test_parse_forms(spec: str) -> None:
    assert parse_commitment_breaks(spec) == STUDY


def test_parse_single_and_zero() -> None:
    assert parse_commitment_breaks("0") == [0.0]
    assert parse_commitment_breaks("12") == [12.0]


@pytest.mark.parametrize(
    ("spec", "msg"),
    [
        ("", "at least one"),
        ("6,3", "strictly increasing"),
        ("6,6", "strictly increasing"),
        ("6,24", r"\[0, 24\)"),
        ("-1,6", r"\[0, 24\)"),
        ("a,b", "cannot parse"),
    ],
)
def test_parse_rejects(spec: str, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        parse_commitment_breaks(spec)


def test_check_returns_floats() -> None:
    assert check_commitment_breaks([1, 2]) == [1.0, 2.0]


def test_read_file_forms(tmp_path: Path) -> None:
    f = tmp_path / "b.csv"
    f.write_text(
        "month,breaks\n"
        "# summer\n"
        "1,3.5 7.5 10.5 14.5 17.5 23.5\n"
        "\n"
        "jul,4.5;8.5;11.5;14.5;16.5;23.5\n"
        "December,3.5,7.5,10.5,14.5,18.5,23.5\n",
        encoding="utf-8",
    )
    table = read_commitment_breaks_file(f)
    assert table[1] == STUDY
    assert table[7] == [4.5, 8.5, 11.5, 14.5, 16.5, 23.5]
    assert table[12] == [3.5, 7.5, 10.5, 14.5, 18.5, 23.5]
    assert set(table) == {1, 7, 12}


@pytest.mark.parametrize(
    ("text", "msg"),
    [
        ("13,1 2\n", "bad month"),
        ("foo,1 2\n", "bad month"),
        ("1,1 2\n1,3 4\n", "twice"),
        ("1\n", "no breaks"),
        ("month,breaks\n", "no rows"),
        ("1,5 2\n", "strictly increasing"),
    ],
)
def test_read_file_rejects(tmp_path: Path, text: str, msg: str) -> None:
    f = tmp_path / "b.csv"
    f.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=msg):
        read_commitment_breaks_file(f)


def test_model_options_entry() -> None:
    assert not commitment_breaks_options(None)
    assert not commitment_breaks_options([])
    assert commitment_breaks_options(STUDY) == {"commitment_breaks": STUDY}


def test_stage_fields() -> None:
    # clock hour modulo 24 (Wednesday 12:00 counted from Monday 0:00)
    assert stage_commitment_fields(60.0) == {"start_hour": 12.0}
    assert stage_commitment_fields(0.0) == {"start_hour": 0.0}
    table = {1: STUDY, 7: [6.0, 18.0]}
    assert stage_commitment_fields(12.0, 7, table, None) == {
        "start_hour": 12.0,
        "commitment_breaks": [6.0, 18.0],
    }
    # month missing: falls back to the planning default (no stage field)
    assert stage_commitment_fields(12.0, 3, table, STUDY) == {"start_hour": 12.0}
    with pytest.raises(ValueError, match="no row for month 3"):
        stage_commitment_fields(12.0, 3, table, None)
    # the stage list is a copy
    out = stage_commitment_fields(0.0, 1, table, None)
    assert out["commitment_breaks"] is not table[1]
