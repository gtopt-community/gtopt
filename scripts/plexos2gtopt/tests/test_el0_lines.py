# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``--el0-lines``: the PLEXOS-mirroring default, the opt-ins, the generated
``--help`` text and the provenance record."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from plexos2gtopt.main import make_parser
from plexos2gtopt.parsers import (
    EL0_LINES_DEFAULT,
    EL0_LINES_MODES,
    LINE_UNENFORCED_FACTOR,
)
from plexos2gtopt.plexos2gtopt import convert_plexos_bundle
from plexos2gtopt.tests import horizon_fixture as fx


def test_cli_default_is_enforced() -> None:
    """Without the flag, the CLI picks the clean (enforced) mode; the
    PLEXOS-mirroring one belongs to ``--legacy-profile cen``."""
    assert EL0_LINES_DEFAULT == "strict"
    assert make_parser().parse_args(["x"]).el0_lines == "enforced"


@pytest.mark.parametrize("mode", ["enforced", "strict", "extended", "unenforced"])
def test_cli_accepts_opt_in_modes(mode: str) -> None:
    assert mode in EL0_LINES_MODES
    args = make_parser().parse_args(["x", "--el0-lines", mode])
    assert args.el0_lines == mode


def test_cli_rejects_unknown_mode(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        make_parser().parse_args(["x", "--el0-lines", "never"])
    assert "--el0-lines" in capsys.readouterr().err


def test_help_says_default_is_enforced() -> None:
    """``--help`` names the clean default, ties the PLEXOS-mirroring mode
    to ``--legacy-profile cen``, lists every mode and states the cap
    factor actually written."""
    flat = re.sub(r"\s+", " ", make_parser().format_help())
    assert "'enforced' (alias 'strict', DEFAULT, clean)" in flat
    assert "'unenforced' (--legacy-profile cen, mirrors PLEXOS)" in flat
    assert "'extended' (opt-in)" in flat
    assert "Constraint Voltage Threshold" in flat
    assert f"{LINE_UNENFORCED_FACTOR:g}x the rating" in flat
    assert "provenance" in flat


@pytest.mark.parametrize(
    ("option", "expected"),
    [
        (None, "strict"),
        ("unenforced", "unenforced"),
        ("enforced", "strict"),
        ("extended", "extended"),
    ],
)
def test_convert_records_el0_mode_in_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: str | None,
    expected: str,
) -> None:
    # ``install_env`` writes os.environ directly: register the key with
    # monkeypatch first so the conversion's value is undone afterwards.
    monkeypatch.setenv("GTOPT_EL0_LINES", "")
    monkeypatch.delenv("GTOPT_EL0_LINES")
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
            "el0_lines": option,
        }
    )
    assert rc == 0
    prov = json.loads((out / "mini.provenance.json").read_text())
    assert prov["el0_lines"] == expected
