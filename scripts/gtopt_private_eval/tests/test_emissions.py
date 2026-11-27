# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Marginal emission factors and CO2 value."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from gtopt_private_eval.case_data import load_system
from gtopt_private_eval.emissions import (
    co2_tonnes,
    co2_value,
    marginal_emission_factors,
    read_mef_csv,
)


def test_mef_marginal_unit(gen_case: Callable[..., Path]) -> None:
    sysd = load_system(gen_case())
    mef = marginal_emission_factors(sysd, {"g": 0.0, "old": 0.9})
    # block 1: old (30 $/MWh) sets 30; block 2: g (10) sets 10
    assert mef[((1, 1, 1), 1)] == pytest.approx(0.9)
    assert mef[((1, 1, 2), 1)] == pytest.approx(0.0)
    g = sysd.projects[0]
    avoided, own = co2_tonnes(g, mef, 0.5)
    assert avoided == pytest.approx(0.9 * 50 * 4380)
    assert own == pytest.approx(0.5 * 50 * 8760)
    assert co2_value(g, mef, 0.5, 10.0) == pytest.approx(10 * (avoided - own))


def test_mef_fallback_and_no_zone(gen_case: Callable[..., Path]) -> None:
    sysd = load_system(gen_case())
    # move the price off every unit cost: the closest running unit is used
    for k in list(sysd.price):
        sysd.price[k] += 3.0
    mef = marginal_emission_factors(sysd, {"g": 0.1, "old": 0.9})
    assert mef[((1, 1, 1), 1)] == pytest.approx(0.9)
    assert mef[((1, 1, 2), 1)] == pytest.approx(0.1)
    empty = marginal_emission_factors(sysd, {"g": 0.1}, exclude={"g", "old"})
    assert set(empty.values()) == {0.0}


def test_read_mef_csv(tmp_path: Path) -> None:
    p = tmp_path / "mef.csv"
    p.write_text("scenario,stage,block,bus,mef\n1,1,2,7,0.42\n", encoding="utf-8")
    assert read_mef_csv(p) == {((1, 1, 2), 7): 0.42}
