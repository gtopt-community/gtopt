# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Source-version detection and ``--source-version`` for ``pp2gtopt``."""

from __future__ import annotations

import json
import logging
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pandapower as pp
import pandapower.networks as pn
import pytest

import pp2gtopt.main as m
from gtopt_shared.source_versions import format_source_versions
from pp2gtopt.source_version import SOURCE_FORMAT, detect_version


def test_detect_bundled_network_version() -> None:
    net = pn.case9()
    assert detect_version(net) == str(net.version)
    # The bundled MATPOWER-derived cases are a declared tested series.
    assert SOURCE_FORMAT.is_tested(detect_version(net) or "")


def test_detect_without_version() -> None:
    assert detect_version(types.SimpleNamespace()) is None
    assert detect_version({"version": "3.4.0"}) == "3.4.0"
    assert detect_version({"version": "  "}) is None
    assert detect_version([]) is None


def test_help_lists_versions(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        m.make_parser().parse_args(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert format_source_versions(SOURCE_FORMAT) in out
    assert "(tested: 3.4, 3.5; default 3.5)" in out


def test_cli_logs_detected_version(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        rc = m.main(["-n", "case9", "-o", str(tmp_path / "c9.json"), "--no-check"])
    assert rc == 0
    assert "pandapower source version: 3." in caplog.text


def test_cli_override_recorded_in_state(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    net_json = tmp_path / "c9.json"
    pp.to_json(pn.case9(), str(net_json))
    prof = tmp_path / "p.csv"
    pd.DataFrame({"hour": np.arange(24), "load:0": 90.0}).to_csv(prof, index=False)
    out = tmp_path / "o" / "c9.json"
    with caplog.at_level(logging.INFO):
        rc = m.main(
            [
                "--file",
                str(net_json),
                "--profiles",
                str(prof),
                "-o",
                str(out),
                "--no-check",
                "--source-version",
                "2.12",
            ]
        )
    assert rc == 0
    state = json.loads((out.parent / "c9" / "pp2gtopt_state.json").read_text())
    record = state["extra"]["source_format"]
    assert record["format"] == "pandapower"
    assert record["override"] == "2.12"
    assert record["version"] == "2.13"  # closest known series
    assert any("unknown version '2.12'" in w for w in record["warnings"])
    assert state["args"]["source_version"] == "2.12"
