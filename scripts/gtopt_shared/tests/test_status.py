# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for :mod:`gtopt_shared.status` (the shared solver_status reader)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gtopt_shared.status import (
    STATUS_AGE_KEY,
    STATUS_FILE,
    RunInfo,
    enrich_run_info,
    find_planning_json,
    find_status_file,
    load_status,
    planning_output_dir,
    read_status,
    registry_dir,
)


def _write(path: Path, payload: object | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")
    return path


def test_read_status_tolerates_bad_files(tmp_path: Path) -> None:
    assert read_status(None) is None
    assert read_status(tmp_path / "missing.json") is None
    assert read_status(tmp_path) is None  # a directory
    assert read_status(_write(tmp_path / "half.json", '{"status": "run')) is None
    assert read_status(_write(tmp_path / "list.json", [1, 2])) is None
    bad_utf8 = tmp_path / "bin.json"
    bad_utf8.write_bytes(b"\xff\xfe{")
    assert read_status(bad_utf8) is None
    good = _write(tmp_path / "ok.json", {"status": "running"})
    assert read_status(good) == {"status": "running"}


def test_read_status_since_skips_stale_file(tmp_path: Path) -> None:
    path = _write(tmp_path / STATUS_FILE, {"status": "converged"})
    os.utime(path, (1000.0, 1000.0))
    assert read_status(path, since=2000.0) is None
    assert read_status(path, since=500.0) == {"status": "converged"}


def test_load_status_empty_dict_and_age_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not load_status(None)
    assert not load_status(tmp_path / "missing.json")
    path = _write(tmp_path / STATUS_FILE, {"iteration": 3})
    assert load_status(path) == {"iteration": 3}
    os.utime(path, (100.0, 100.0))
    monkeypatch.setattr("gtopt_shared.status.time.time", lambda: 160.0)
    stamped = load_status(path, stamp_age=True)
    assert stamped[STATUS_AGE_KEY] == pytest.approx(60.0)

    def _stat(self: Path, *_args: object, **_kwargs: object) -> os.stat_result:
        raise OSError(f"{self} vanished")

    # read_status (no *since*) never stats; only the age stamp does.
    monkeypatch.setattr(Path, "stat", _stat)
    # The file vanished after being read: no age, data kept.
    assert load_status(path, stamp_age=True) == {"iteration": 3}


def test_find_planning_json_order(tmp_path: Path) -> None:
    case = tmp_path / "case"
    case.mkdir()
    assert find_planning_json(case) is None
    other = _write(case / "a_other.json", {})
    assert find_planning_json(case) == other
    sanitized = _write(case / "z_sanitized.json", {})
    assert find_planning_json(case) == sanitized
    assert find_planning_json(case, prefer_sanitized=False) == other
    named = _write(case / "case.json", {})
    assert find_planning_json(case) == named
    assert find_planning_json(named) == named
    empty = tmp_path / "empty"
    (empty / "x.json").mkdir(parents=True)
    assert find_planning_json(empty) is None


def test_planning_output_dir(tmp_path: Path) -> None:
    assert planning_output_dir(None) is None
    assert planning_output_dir(tmp_path / "missing.json") is None
    rel = _write(tmp_path / "rel.json", {"options": {"output_directory": "res"}})
    assert planning_output_dir(rel) == tmp_path / "res"
    absolute = tmp_path / "abs_out"
    abs_json = _write(
        tmp_path / "abs.json", {"options": {"output_directory": str(absolute)}}
    )
    assert planning_output_dir(abs_json) == absolute
    assert planning_output_dir(_write(tmp_path / "n.json", {"options": {}})) is None
    assert planning_output_dir(_write(tmp_path / "l.json", [1])) is None
    assert planning_output_dir(_write(tmp_path / "o.json", {"options": "x"})) is None
    assert planning_output_dir(_write(tmp_path / "b.json", "{bad")) is None


def test_find_status_file_candidates(tmp_path: Path) -> None:
    case = tmp_path / "case"
    _write(case / "case.json", {"options": {"output_directory": "declared"}})
    # Nothing written yet: canonical path in the first candidate.
    assert find_status_file(case) == case / "declared" / STATUS_FILE
    explicit = tmp_path / "cli_out"
    assert find_status_file(case, explicit) == explicit / STATUS_FILE
    # Conventional dirs, legacy names.
    legacy = _write(case / "results" / "sddp_status.json", {})
    assert find_status_file(case) == legacy
    mono = _write(case / "output" / "monolithic_status.json", {})
    assert find_status_file(case) == mono  # "output" before "results"
    current = _write(case / "declared" / STATUS_FILE, {})
    assert find_status_file(case) == current
    # The JSON file itself as case_dir.
    assert find_status_file(case / "case.json") == current
    assert find_status_file(case, explicit) == current


def test_registry_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert registry_dir() == tmp_path / "xdg" / "gtopt" / "runs"
    monkeypatch.delenv("XDG_CACHE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert registry_dir() == tmp_path / ".cache" / "gtopt" / "runs"
    monkeypatch.setenv("HOME", "")
    assert registry_dir() == Path(".") / "gtopt" / "runs"


def test_enrich_run_info(tmp_path: Path) -> None:
    out = tmp_path / "case" / "output"
    info = enrich_run_info(RunInfo(pid=7, output_dir=out))
    assert info.status is None and info.case_name is None
    _write(
        out / STATUS_FILE,
        {
            "status": "running",
            "iteration": 4,
            "max_iterations": 10,
            "lower_bound": 1.0,
            "upper_bound": 2.0,
            "gap": 0.5,
            "elapsed_s": 12.5,
            "method": "sddp",
        },
    )
    info = enrich_run_info(RunInfo(pid=7, output_dir=out))
    assert (info.status, info.iteration, info.max_iterations) == ("running", 4, 10)
    assert (info.lower_bound, info.upper_bound, info.gap) == (1.0, 2.0, 0.5)
    assert info.elapsed_s == 12.5 and info.method == "sddp"
    assert info.case_name == "case"
