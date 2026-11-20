# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for :mod:`gtopt_shared.testing.external_cases`."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtopt_shared.testing.external_cases import (
    CASES_DIR_ENV,
    external_case_dir,
    cases_root,
    external_case_path,
    missing_case_reason,
)


def test_external_case_dir_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "some_external_case").mkdir()
    monkeypatch.setenv(CASES_DIR_ENV, str(tmp_path))
    assert external_case_dir("some_external_case") == tmp_path / "some_external_case"


def test_external_case_dir_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CASES_DIR_ENV, str(tmp_path))
    assert external_case_dir("no_such_case_anywhere_xyz") is None
    monkeypatch.delenv(CASES_DIR_ENV)
    assert external_case_dir("no_such_case_anywhere_xyz") is None


def test_unset_env_gives_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CASES_DIR_ENV, raising=False)
    assert cases_root() is None
    assert external_case_path("chile/medium_long_term/plp_2y") is None


def test_missing_dir_gives_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(CASES_DIR_ENV, str(tmp_path / "nope"))
    assert cases_root() is None
    assert external_case_path("a/b") is None


def test_full_relpath(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "chile" / "medium_long_term" / "plp_2y" / "source"
    target.mkdir(parents=True)
    monkeypatch.setenv(CASES_DIR_ENV, str(tmp_path))
    assert cases_root() == tmp_path
    assert external_case_path("chile/medium_long_term/plp_2y/source") == target
    assert external_case_path("chile/medium_long_term/plp_5y/source") is None


def test_application_folder_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GTOPT_CASES_DIR pointing at <repo>/chile/medium_long_term."""
    case = tmp_path / "gtopt_case_2y"
    case.mkdir()
    monkeypatch.setenv(CASES_DIR_ENV, str(tmp_path))
    assert external_case_path("chile/medium_long_term/gtopt_case_2y") == case


def test_missing_case_reason_mentions_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CASES_DIR_ENV, raising=False)
    msg = missing_case_reason("x/y")
    assert "x/y" in msg
    assert CASES_DIR_ENV in msg
    assert "get_cases.sh" in msg
