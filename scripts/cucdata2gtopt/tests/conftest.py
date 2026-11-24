# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Fixtures of the cucdata2gtopt tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ._synthetic import make_workbook


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    """Path of a fresh synthetic workbook."""
    return make_workbook(tmp_path / "CUC_test.xlsx")
