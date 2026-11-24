# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Shared fixtures: the prototype's tiny and literature variants."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gasnet2gtopt.common import BuildOptions

CASES = Path(__file__).resolve().parent.parent / "cases"
LIT = CASES / "literature"

#: name -> (case, BuildOptions overrides, nblocks, price node, generator);
#: the expected values in ``cases/tiny_expected.json`` were computed by an
#: independent scipy/HiGHS LP of the prototype output.
VARIANTS: dict[str, tuple[str, dict[str, Any], int, str, str]] = {
    "mini3_N8": ("mini3", {}, 1, "T", "G_T"),
    "cmp_off": ("tiny_compressor", {}, 1, "T", "G_T"),
    "cmp_fixed": ("tiny_compressor", {"compressor": "series"}, 1, "T", "G_T"),
    "cmp_sald": (
        "tiny_compressor",
        {"compressor": "series", "fuel_model": "saldarriaga"},
        1,
        "T",
        "G_T",
    ),
    "lp_steady": ("tiny_linepack", {}, 4, "T", "G_T"),
    "lp_pack": ("tiny_linepack", {"linepack": True}, 4, "T", "G_T"),
    "plant_none": ("tiny_plant", {"plant_pressure": False}, 1, "R", "G_R"),
    "plant_hard": ("tiny_plant", {}, 1, "R", "G_R"),
    "plant_booster": ("tiny_plant", {"booster": "on"}, 1, "R", "G_R"),
    "plant_booster_gas": (
        "tiny_plant",
        {"booster": "on", "booster_drive": "gas"},
        1,
        "R",
        "G_R",
    ),
    "plant_derate": ("tiny_plant", {"derate": True}, 1, "R", "G_R"),
    "plant_derate_booster": (
        "tiny_plant",
        {"derate": True, "booster": "on"},
        1,
        "R",
        "G_R",
    ),
}

#: Literature cases (``cases/literature/expected.json``).
LIT_CASES = {
    "saldarriaga_phd_fig2_2": "saldarriaga_phd_fig2_2.gasnet.json",
    "gaslib11_radial": "gaslib11_radial.gasnet.json",
    "belgian_a1_radial": "belgian_a1_radial.gasnet.json",
}


def load_case(name: str) -> dict[str, Any]:
    """A tiny ``cases/<name>.gasnet.json`` description."""
    return json.loads((CASES / f"{name}.gasnet.json").read_text(encoding="utf-8"))


def load_lit(name: str) -> dict[str, Any]:
    """A literature description."""
    return json.loads((LIT / LIT_CASES[name]).read_text(encoding="utf-8"))


def variant_options(name: str) -> BuildOptions:
    """BuildOptions of a tiny variant."""
    _, kw, nb, _, _ = VARIANTS[name]
    return BuildOptions(nblocks=nb, **kw)


def lit_options() -> BuildOptions:
    """BuildOptions of the literature cases (stations in series)."""
    return BuildOptions(compressor="series", nblocks=1)


@pytest.fixture(scope="session")
def tiny_expected() -> dict[str, Any]:
    """``cases/tiny_expected.json``."""
    return json.loads((CASES / "tiny_expected.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def lit_expected() -> dict[str, Any]:
    """``cases/literature/expected.json``."""
    return json.loads((LIT / "expected.json").read_text(encoding="utf-8"))
