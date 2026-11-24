# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Source-version detection and ``--source-version`` for ``psse2gtopt``."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from gtopt_shared.source_versions import format_source_versions
from psse2gtopt.main import main, make_parser
from psse2gtopt.psse2gtopt import convert_psse_case
from psse2gtopt.raw_parser import SOURCE_FORMAT, detect_version


def test_detect_fixture_revisions(
    ieee14_raw: Path, ieee39_raw: Path, synthetic_raw: Path
) -> None:
    assert detect_version(ieee14_raw) == "32"
    assert detect_version(ieee39_raw) == "33"
    assert detect_version(synthetic_raw) == "33"
    # Every declared tested revision is exercised by a fixture.
    assert set(SOURCE_FORMAT.tested) == {"32", "33"}


def test_detect_v35_header(tmp_path: Path) -> None:
    raw = tmp_path / "v35.raw"
    raw.write_text("@!IC,SBASE,REV,XFRRAT,NXFRAT,BASFRQ\n0, 100.0, 35, 0, 0, 60.0\n")
    assert detect_version(raw) == "35"


@pytest.mark.parametrize(
    "text",
    ["", "@!IC,SBASE\n", "0, 100.0\n", "0, 100.0, abc, 0\n"],
)
def test_detect_undetectable(tmp_path: Path, text: str) -> None:
    raw = tmp_path / "bad.raw"
    raw.write_text(text)
    assert detect_version(raw) is None


def test_detect_missing_file(tmp_path: Path) -> None:
    assert detect_version(tmp_path / "missing.raw") is None


def test_help_lists_versions(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        make_parser().parse_args(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert format_source_versions(SOURCE_FORMAT) in out
    assert "Supported: 32, 33 (tested: 32, 33; default 33)" in out


def test_cli_override_logged(
    ieee14_raw: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        main(
            [
                str(ieee14_raw),
                "-o",
                str(tmp_path / "out"),
                "--source-version",
                "33",
            ]
        )
    assert "PSS/E RAW source version: 33 (override)" in caplog.text
    assert "overrides the version 32 detected" in caplog.text


def test_unknown_revision_falls_back(
    synthetic_raw: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    raw = tmp_path / "rev31.raw"
    raw.write_text(synthetic_raw.read_text().replace(", 33,", ", 31,", 1))
    with caplog.at_level(logging.INFO):
        convert_psse_case({"input": raw, "output_dir": tmp_path / "o"})
    assert "unknown version '31'" in caplog.text
    assert "closest known version 32" in caplog.text
