# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``--legacy`` output (DecisionVariable / UserConstraint) vs the
prototype's expected values, solved by the independent scipy LP."""

from __future__ import annotations

from typing import Any

import pytest

from gasnet2gtopt.common import BuildOptions
from gasnet2gtopt.legacy import build_legacy, dv, fmt

from . import lp_ref
from .conftest import LIT_CASES, VARIANTS, lit_options, load_case, load_lit
from .conftest import variant_options


def test_formatting_helpers() -> None:
    assert fmt(1.0) == "1"
    assert fmt(-0.5) == "-0.5"
    assert "pi_S" in dv("pi_S")


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_tiny_variant_matches_expected(
    name: str, tiny_expected: dict[str, Any]
) -> None:
    case, _, _, node, gen = VARIANTS[name]
    planning, meta = build_legacy(load_case(case), variant_options(name))
    assert meta["nodes"]
    sol = lp_ref.solve(planning)
    assert sol.status == 0
    exp = tiny_expected[name]
    assert sol.objective == pytest.approx(exp["objective"], rel=1e-5)
    assert sol.gen[gen] == pytest.approx(exp["generation"], abs=0.01)
    assert node == exp["gas_price_node"]


@pytest.mark.parametrize("name", sorted(LIT_CASES))
def test_literature_matches_expected(name: str, lit_expected: dict[str, Any]) -> None:
    planning, _ = build_legacy(load_lit(name), lit_options())
    sol = lp_ref.solve(planning)
    assert sol.status == 0
    exp = lit_expected[name]
    assert sol.objective == pytest.approx(exp["objective"], rel=1e-5)
    for n, p in exp["pressure"].items():
        assert sol.pressure(n)[0] == pytest.approx(p, abs=0.01)


def test_summer_and_overrides_build() -> None:
    opts = BuildOptions(
        season="summer",
        compressor="series",
        segments=4,
        nblocks=2,
        cs_gamma=0.05,
        plant_pmin={"G_T": 36.0},
    )
    planning, _ = build_legacy(load_case("tiny_compressor"), opts)
    assert planning["system"]["name"] == "tiny_compressor_summer_series"
    assert lp_ref.solve(planning).status == 0
