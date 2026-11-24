# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Commit detection and ``--source-version`` for ``sienna2gtopt``."""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

import pytest

import sienna2gtopt.__main__ as cli
from gtopt_shared.source_versions import format_source_versions
from sienna2gtopt import _bundle
from sienna2gtopt.source_version import SOURCE_FORMAT, detect_version

_COMMIT = _bundle.UPSTREAM_COMMIT


def test_detect_real_bundle(require_sienna_5bus: Path, tmp_path: Path) -> None:
    assert detect_version(require_sienna_5bus) == _COMMIT
    tampered = tmp_path / "copy"
    shutil.copytree(require_sienna_5bus, tampered)
    (tampered / "bus.csv").write_text("changed\n")
    assert detect_version(tampered) is None


def test_detect_synthetic_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.txt").write_bytes(b"hello")
    digest = hashlib.sha256(b"hello").hexdigest()
    monkeypatch.setitem(_bundle.MANIFEST, "tiny", [("dir/a.txt", digest)])
    assert detect_version(tmp_path, "tiny") == _COMMIT
    assert detect_version(tmp_path / "missing", "tiny") is None
    assert detect_version(tmp_path, "no-such-bundle") is None


def test_help_lists_versions(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert format_source_versions(SOURCE_FORMAT) in out
    assert f"Supported: {_COMMIT} (tested: {_COMMIT}; default {_COMMIT})" in out


@pytest.mark.parametrize(
    ("override", "warned"),
    [(_COMMIT[:7], False), ("0123456789abcdef", True)],
)
def test_cli_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    override: str,
    warned: bool,
) -> None:
    def stop(_case_dir: Path) -> None:
        raise RuntimeError("stop after the version resolution")

    monkeypatch.setattr(cli, "extract_bundle", lambda _name: tmp_path)
    monkeypatch.setattr(cli, "load_case", stop)
    with caplog.at_level(logging.INFO, logger="sienna2gtopt"):
        with pytest.raises(RuntimeError, match="stop"):
            cli.main(["hvdc", "--source-version", override])
    assert f"source version: {_COMMIT} (override)" in caplog.text
    assert ("unknown version" in caplog.text) is warned
