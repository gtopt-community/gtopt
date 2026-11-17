# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for :mod:`gtopt_shared.testing.external_cases`."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtopt_shared.testing.external_cases import (
    CASES_DIR_ENV,
    external_case_dir,
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


def test_missing_case_reason_mentions_env() -> None:
    assert CASES_DIR_ENV in missing_case_reason("x")
