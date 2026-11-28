# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for opendss2gtopt.dss_values."""

from __future__ import annotations

import math

import pytest

from opendss2gtopt.dss_values import (
    DssValueError,
    eval_rpn,
    length_factor_m,
    parse_bus,
    split_items,
    strip_delims,
    to_bool,
    to_float,
    to_floats,
    to_int,
    to_matrix,
)


def test_strip_delims_variants() -> None:
    assert strip_delims("[1 2]") == "1 2"
    assert strip_delims("(a)") == "a"
    assert strip_delims('"x y"') == "x y"
    assert strip_delims("'x'") == "x"
    assert strip_delims("{3}") == "3"
    assert strip_delims("plain") == "plain"
    assert strip_delims("[open") == "[open"


@pytest.mark.parametrize(
    ("expr", "value"),
    [
        ("8 1000 /", 0.008),
        ("2 3 +", 5.0),
        ("2 3 -", -1.0),
        ("2 3 *", 6.0),
        ("2 3 ^", 8.0),
        ("4 sqrt", 2.0),
        ("3 sqr", 9.0),
        ("4 inv", 0.25),
        ("1 exp ln", 1.0),
        ("100 log10", 2.0),
        ("90 sin", 1.0),
        ("0 cos", 1.0),
        ("45 tan", 1.0),
        ("1 asin", 90.0),
        ("1 acos", 0.0),
        ("1 atan", 45.0),
        ("5 chs", -5.0),
        ("pi", math.pi),
        ("1 2 swap -", 1.0),
        ("1 0 atan2", math.pi / 2),
    ],
)
def test_eval_rpn(expr: str, value: float) -> None:
    assert eval_rpn(expr) == pytest.approx(value)


@pytest.mark.parametrize("expr", ["+", "sqrt", "swap", "", "1 foo"])
def test_eval_rpn_errors(expr: str) -> None:
    with pytest.raises(DssValueError):
        eval_rpn(expr)


def test_scalars() -> None:
    assert to_float("1.5") == 1.5
    assert to_float("(8 1000 /)") == pytest.approx(0.008)
    assert to_int("2.6") == 3
    assert to_bool("Yes") and to_bool("t") and not to_bool("no")


def test_arrays_and_matrices() -> None:
    assert split_items("[1, 2 | 3]") == ["1", "2", "3"]
    assert to_floats("(1 2 3)") == [1.0, 2.0, 3.0]
    with pytest.raises(DssValueError):
        to_floats("[1 x]")
    lower = to_matrix("(1 | 2 3)", 2)
    assert lower == [[1.0, 2.0], [2.0, 3.0]]
    full = to_matrix("[1 2 2 3]", 2)
    assert full == [[1.0, 2.0], [2.0, 3.0]]
    with pytest.raises(DssValueError):
        to_matrix("[1 2]", 2)


def test_bus_and_units() -> None:
    assert parse_bus("Bus7.1.3") == ("bus7", [1, 3])
    assert parse_bus("b2") == ("b2", [])
    assert parse_bus("b2.") == ("b2", [])
    with pytest.raises(DssValueError):
        parse_bus("b.x")
    assert length_factor_m("kft") == pytest.approx(304.8)
    assert length_factor_m("NONE") == 1.0
    with pytest.raises(DssValueError):
        length_factor_m("furlong")
