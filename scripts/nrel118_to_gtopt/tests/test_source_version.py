# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Commit detection and ``--source-version`` for ``nrel118_to_gtopt``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtopt_shared.source_versions import format_source_versions
from nrel118_to_gtopt import _data
from nrel118_to_gtopt.__main__ import main, make_parser
from nrel118_to_gtopt.source_version import MARKER, SOURCE_FORMAT, detect_version

from .test_converter import write_mini_dataset

_REF = _data.DEFAULT_REF


def test_detect_marker(tmp_path: Path) -> None:
    assert detect_version(tmp_path) is None
    (tmp_path / MARKER).write_text(f"{_REF}\n")
    assert detect_version(tmp_path) == _REF


def _git(root: Path, head: str) -> Path:
    git = root / ".git"
    git.mkdir(parents=True)
    (git / "HEAD").write_text(head)
    (root / "118-Bus").mkdir()
    return git


def test_detect_detached_checkout(tmp_path: Path) -> None:
    _git(tmp_path, f"{_REF}\n")
    assert detect_version(tmp_path) == _REF
    assert detect_version(tmp_path / "118-Bus") == _REF


def test_detect_branch_loose_and_packed(tmp_path: Path) -> None:
    git = _git(tmp_path, "ref: refs/heads/master\n")
    assert detect_version(tmp_path) is None  # ref not resolvable
    (git / "packed-refs").write_text(
        f"# pack-refs with: peeled\n{_REF} refs/heads/master\n"
    )
    assert detect_version(tmp_path) == _REF
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "refs" / "heads" / "master").write_text("abcdef1234567\n")
    assert detect_version(tmp_path) == "abcdef1234567"


def test_detect_ignores_unrelated_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text(f"{_REF}\n")
    (tmp_path / "data").mkdir()
    assert detect_version(tmp_path) is None  # no 118-Bus/ beside .git
    assert detect_version(tmp_path / "data") is None


def test_populate_fresh_cache_writes_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _data, "_download", lambda _r, local, _ref: local.write_text("x\n")
    )
    _data.populate_cache(tmp_path / "c", profile_names=[], ref="abc1234")
    assert detect_version(tmp_path / "c") == "abc1234"
    mini = write_mini_dataset(tmp_path / "mini")
    _data.populate_cache(mini, profile_names=[], ref="abc1234")
    assert detect_version(mini) is None  # pre-filled cache is never stamped


def test_help_lists_versions(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        make_parser().parse_args(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert format_source_versions(SOURCE_FORMAT) in out
    assert f"(tested: none; default {_REF})" in out


def test_cli_records_version_in_state(tmp_path: Path) -> None:
    mini = write_mini_dataset(tmp_path / "cache")
    out = tmp_path / "run" / "nrel.json"
    argv = ["--cache-dir", str(mini), "--no-download", "--hours", "0+24"]
    rc = main([*argv, "-o", str(out), "--source-version", _REF[:10]])
    assert rc == 0
    state = json.loads(
        (out.parent / "nrel" / "nrel118_to_gtopt_state.json").read_text()
    )
    record = state["extra"]["source_format"]
    assert record["version"] == _REF
    assert record["override"] == _REF[:10]
    assert record["tested"] is False
    assert any("untested" in w for w in record["warnings"])
