# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Command line of gasnet2gtopt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gasnet2gtopt.__main__ import main, make_parser, options_from_args

from .conftest import CASES


def test_options_from_args() -> None:
    args = make_parser().parse_args(
        [
            "x.gasnet.json",
            "-o",
            "p.json",
            "--season",
            "summer",
            "--compressor",
            "series",
            "--linepack",
            "--nblocks",
            "4",
            "--segments",
            "6",
            "--fuel-model",
            "saldarriaga",
            "--cs-gamma",
            "0.05",
            "--booster",
            "on",
            "--booster-drive",
            "gas",
            "--plant-pmin",
            "G=22.7",
            "--derate",
            "--no-plant-pressure",
        ]
    )
    opts = options_from_args(args)
    assert opts.season == "summer"
    assert opts.linepack
    assert opts.nblocks == 4
    assert opts.segments == 6
    assert opts.plant_pmin == {"G": 22.7}
    assert opts.derate
    assert not opts.plant_pressure
    assert opts.booster_drive == "gas"


@pytest.mark.parametrize("legacy", [False, True])
def test_main_writes_planning_and_meta(
    tmp_path: Path, legacy: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "sub" / "planning.json"
    argv = [str(CASES / "tiny_plant.gasnet.json"), "-o", str(out), "--nblocks", "2"]
    if legacy:
        argv.append("--legacy")
    assert main(argv) == 0
    planning = json.loads(out.read_text(encoding="utf-8"))
    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))
    sysd = planning["system"]
    if legacy:
        assert "decision_variable_array" in sysd
        assert "gas_node_array" not in sysd
    else:
        assert "gas_node_array" in sysd
        assert meta["nodes"]["R"] == 4
    assert "wrote" in capsys.readouterr().out
