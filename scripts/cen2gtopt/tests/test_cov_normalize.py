# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage tests for :mod:`cen2gtopt._normalize`."""

from __future__ import annotations

from datetime import datetime

from cen2gtopt._normalize import fold_chile_continental, normalize_name, stable_uid


def test_normalize_name_strips_accents_and_symbols() -> None:
    assert normalize_name("Charrúa 220 kV") == "charrua_220_kv"
    assert normalize_name("U.  N°1--A") == "u_n1_a"
    assert normalize_name("") == ""


def test_stable_uid_is_deterministic_and_31_bit() -> None:
    uid = stable_uid("Charrúa")
    assert uid == stable_uid("charrua")
    assert 0 <= uid < 2**31
    assert uid != stable_uid("Polpaico")


def test_fold_chile_continental_formats() -> None:
    assert fold_chile_continental("") is None
    assert fold_chile_continental("2025-01-02T03:04:05") == datetime(
        2025, 1, 2, 3, 4, 5
    )
    assert fold_chile_continental(" 2025-01-02 03:04 ") == datetime(2025, 1, 2, 3, 4)
    assert fold_chile_continental("2025-01-02") == datetime(2025, 1, 2)
    assert fold_chile_continental("not a date") is None
