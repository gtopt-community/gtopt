# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Source-version detection and ``--source-version`` for ``sddp2gtopt``."""

from __future__ import annotations

import logging
import lzma
from pathlib import Path

import pytest

from gtopt_shared.source_versions import format_source_versions
from sddp2gtopt.main import main, make_parser
from sddp2gtopt.source_version import SOURCE_FORMAT, detect_version

_DATA = Path(__file__).parent / "data"


def test_detect_fixture_generations(case0_dir: Path, dat_case_dir: Path) -> None:
    # SDDP >= 17 CSV layout (gndsitett.dat: ``!Code,...``).
    assert detect_version(_DATA / "sddp_block_case") == "17"
    # Fixed-width only / psrclasses.json: the release is not identifiable.
    assert detect_version(dat_case_dir) is None
    assert detect_version(case0_dir) is None


def test_detect_not_a_directory(tmp_path: Path) -> None:
    assert detect_version(tmp_path / "missing") is None
    f = tmp_path / "file.dat"
    f.write_text("!Code,Name\n")
    assert detect_version(f) is None


def test_detect_compressed_and_edge_files(tmp_path: Path) -> None:
    (tmp_path / "empty.dat").write_text("")
    (tmp_path / "short.dat").write_text("$version=2\n")
    (tmp_path / "long.dat").write_text("a\nb\nc\n!Code,late\n")  # outside window
    (tmp_path / "sub.dat").mkdir()  # a directory named like a .dat
    (tmp_path / "broken.dat.xz").write_bytes(b"not xz")
    assert detect_version(tmp_path) is None
    with lzma.open(tmp_path / "dcirc.dat.xz", "wt", encoding="latin-1") as fh:
        fh.write("$version=3\n!Code,Name,BusFrom\n")
    assert detect_version(tmp_path) == "17"


def test_help_lists_versions(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        make_parser().parse_args(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert format_source_versions(SOURCE_FORMAT) in out
    assert "Supported: 16, 17 (tested: 16, 17; default 16)" in out


def test_cli_source_version_logged(
    case_min_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        main([str(case_min_dir), "-o", str(tmp_path / "out"), "--source-version", "18"])
    assert "unknown version '18'" in caplog.text
    assert "PSR SDDP source version: 17 (override)" in caplog.text
