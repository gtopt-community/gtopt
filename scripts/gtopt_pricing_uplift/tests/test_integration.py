# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tier 3: the uplift tool on the ``cases/uc_pricing_*`` e2e cases.

Hand-computed uplift (see ``cases/uc_pricing_fixed/README.md``):

=========== ============ ========== ====== =======
mode        λ (h1, h2)   make-whole LOC    total
=========== ============ ========== ====== =======
fixed       (10, 10)     3200       0      3200
fast_start  (60, 10)     600        1300   1900
relaxed     (60, 12.5)   600        1100   1700
=========== ============ ========== ====== =======

``test_goldens_*`` reads the committed e2e goldens (no solver needed);
``test_live_gtopt_*`` runs the gtopt binary (skipped when it is absent or
predates ``--pricing-mode``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gtopt_pricing_uplift import compute_uplift
from gtopt_shared.gtopt_binary import find_gtopt_binary

from .conftest import CASES, UPLIFT

MODES = ("fixed", "fast_start", "relaxed")


@pytest.mark.parametrize("mode", MODES)
def test_goldens_hand_computed_uplift(mode: str) -> None:
    case = CASES / f"uc_pricing_{mode}"
    rep = compute_uplift(case / "output", planning=case / f"uc_pricing_{mode}.json")
    mwp, loc, total = UPLIFT[mode]
    assert rep.pricing_mode == mode
    assert rep.applied_modes == [mode]
    assert rep.pricing_fallbacks == 0
    assert rep.make_whole == pytest.approx(mwp)
    assert rep.lost_opportunity == pytest.approx(loc)
    assert rep.total == pytest.approx(total)
    assert rep.prices.negative_node_hours == pytest.approx(0.0)


def test_goldens_relaxed_minimises_uplift() -> None:
    totals = {}
    for mode in MODES:
        case = CASES / f"uc_pricing_{mode}"
        totals[mode] = compute_uplift(
            case / "output", planning=case / f"uc_pricing_{mode}.json"
        ).total
    assert min(totals, key=totals.__getitem__) == "relaxed"
    assert totals["relaxed"] < totals["fast_start"] < totals["fixed"]


def _gtopt_with_pricing() -> str | None:
    binary = find_gtopt_binary()
    if binary is None:
        return None
    try:
        help_text = subprocess.run(
            [binary, "--help"], capture_output=True, text=True, check=False, timeout=60
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return binary if "--pricing-mode" in help_text else None


@pytest.mark.integration
@pytest.mark.parametrize("mode", MODES)
def test_live_gtopt_uplift(mode: str, tmp_path: Path) -> None:
    binary = _gtopt_with_pricing()
    if binary is None:
        pytest.skip("gtopt binary with --pricing-mode not found (set GTOPT_BIN)")
    case = CASES / "uc_pricing_fixed"
    out = tmp_path / mode
    proc = subprocess.run(
        [
            binary,
            "uc_pricing_fixed.json",
            "--pricing-mode",
            mode,
            "--output-directory",
            str(out),
            "--quiet",
        ],
        cwd=case,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    rep = compute_uplift(out)
    mwp, loc, total = UPLIFT[mode]
    assert rep.pricing_mode == mode  # --pricing-mode overrides the JSON
    assert rep.make_whole == pytest.approx(mwp)
    assert rep.lost_opportunity == pytest.approx(loc)
    assert rep.total == pytest.approx(total)
