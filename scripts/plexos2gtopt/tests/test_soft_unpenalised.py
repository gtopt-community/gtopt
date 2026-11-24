# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``--soft-unpenalised-constraints``: PLEXOS constraints without a Penalty
Price are HARD by default; the flag is an explicit opt-in to soften them."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from plexos2gtopt.main import make_parser
from plexos2gtopt.plexos2gtopt import convert_plexos_bundle
from plexos2gtopt.tests import horizon_fixture as fx


def test_cli_default_is_off() -> None:
    assert make_parser().parse_args(["x"]).soft_unpenalised_constraints is None


def test_cli_accepts_penalty() -> None:
    args = make_parser().parse_args(["x", "--soft-unpenalised-constraints", "1e4"])
    assert args.soft_unpenalised_constraints == pytest.approx(1e4)


def test_cli_rejects_non_number(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        make_parser().parse_args(["x", "--soft-unpenalised-constraints", "soft"])
    assert "--soft-unpenalised-constraints" in capsys.readouterr().err


def test_help_says_hard_by_default() -> None:
    flat = re.sub(r"\s+", " ", make_parser().format_help())
    assert "--soft-unpenalised-constraints PENALTY" in flat
    assert "they are HARD, as in PLEXOS" in flat


@pytest.mark.parametrize("penalty", [None, 500.0])
def test_convert_records_choice_in_provenance(
    tmp_path: Path, penalty: float | None
) -> None:
    bundle = fx.write_bundle(tmp_path / "bundle")
    out = tmp_path / "out"
    rc = convert_plexos_bundle(
        {
            "input_bundle": bundle,
            "output_dir": out,
            "output_file": out / "mini.json",
            "horizon_mode": "hourly",
            "horizon_days": 1,
            "lp_relax": True,
            "run_check": False,
            "soft_unpenalised_constraints": penalty,
        }
    )
    assert rc == 0
    prov = json.loads((out / "mini.provenance.json").read_text())
    assert prov["soft_unpenalised_constraints"] == penalty
