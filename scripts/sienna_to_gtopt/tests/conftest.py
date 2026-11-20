"""Shared fixtures for the ``sienna_to_gtopt`` tests.

The Sienna test data is not redistributed with gtopt: it is fetched
from the pinned upstream commit on first use and cached (see
:mod:`sienna_to_gtopt._bundle`).  Tests that need it depend on
:func:`require_sienna_5bus`, which skips them when the bundle is
neither cached nor downloadable (offline, ``GTOPT_OFFLINE=1``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sienna_to_gtopt._bundle import BundleUnavailableError, extract_bundle


@pytest.fixture(scope="session")
def require_sienna_5bus() -> Path:
    """Extracted ``sienna_5bus/`` dir; skip when the data is unavailable."""
    try:
        case_dir = extract_bundle("5bus")
    except BundleUnavailableError as exc:
        pytest.skip(f"Sienna 5-bus test data unavailable: {exc}")
    return case_dir
