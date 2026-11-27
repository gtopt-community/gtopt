# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tier 3: solve ``cases/invest_screening_1b`` with the gtopt binary.

Skipped when no binary is found (``GTOPT_BIN`` / PATH / build dirs).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gtopt_cost_recovery import compute_recovery
from gtopt_shared.gtopt_binary import find_gtopt_binary

from .conftest import CASES


@pytest.mark.integration
def test_live_screening_lp_pays_for_itself(tmp_path: Path) -> None:
    binary = find_gtopt_binary()
    if binary is None:
        pytest.skip("gtopt binary not found (set GTOPT_BIN)")
    case = CASES / "invest_screening_1b"
    out = tmp_path / "lp"
    proc = subprocess.run(
        [
            binary,
            "invest_screening_lp.json",
            "--set",
            f"output_directory={out}",
            "--write-out",
            "all",
        ],
        cwd=case,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    rep = compute_recovery(out)
    for p in rep.projects:
        if p.built:
            assert abs(p.profit) <= 1e-6 * p.total_capex + 1e-3
    assert rep.total_capex > 0.0
