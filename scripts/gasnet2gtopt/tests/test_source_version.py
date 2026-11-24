# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Schema-version detection and ``--source-version`` for ``gasnet2gtopt``."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from gasnet2gtopt.__main__ import main, make_parser
from gasnet2gtopt.source_version import SOURCE_FORMAT, detect_version
from gtopt_shared.source_versions import format_source_versions

from .conftest import CASES


def test_detect_version() -> None:
    assert detect_version({"schema_version": 1}) == "1"
    assert detect_version({"schema_version": " 2 "}) == "2"
    assert detect_version({"schema_version": ""}) is None
    assert detect_version({"schema_version": True}) is None
    assert detect_version({}) is None
    assert detect_version([1]) is None


def test_bundled_cases_have_no_schema_field() -> None:
    cases = sorted(CASES.glob("**/*.gasnet.json"))
    assert cases
    for path in cases:
        assert detect_version(json.loads(path.read_text(encoding="utf-8"))) is None


def test_help_lists_versions(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        make_parser().parse_args(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert format_source_versions(SOURCE_FORMAT) in out
    assert "Supported: 1 (tested: 1; default 1)" in out


def test_meta_records_schema_version(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    desc = json.loads((CASES / "tiny_plant.gasnet.json").read_text(encoding="utf-8"))
    desc["schema_version"] = 2
    src = tmp_path / "v2.gasnet.json"
    src.write_text(json.dumps(desc), encoding="utf-8")
    out = tmp_path / "planning.json"
    with caplog.at_level(logging.INFO, logger="gasnet2gtopt"):
        assert main([str(src), "-o", str(out), "--nblocks", "2"]) == 0
    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))
    record = meta["source_format"]
    assert record["detected"] == "2"
    assert record["version"] == "1"
    assert "unknown version '2'" in caplog.text


def test_override_without_field(tmp_path: Path) -> None:
    out = tmp_path / "planning.json"
    argv = [str(CASES / "tiny_plant.gasnet.json"), "-o", str(out), "--nblocks", "2"]
    assert main([*argv, "--source-version", "1"]) == 0
    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert meta["source_format"]["override"] == "1"
    assert meta["source_format"]["warnings"] == []
