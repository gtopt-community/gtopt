# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Test helpers shared across gtopt converter test suites (issue #507).

Currently exports:

* :func:`assert_snapshot` — JSON snapshot assertion with a
  ``PYTEST_UPDATE_GOLDEN`` regenerate path.
* :func:`assert_golden_file` — file-level golden equality check.
* :func:`assert_text_snapshot` — verbatim text golden (rendered frames).

Use these in any new converter test that needs JSON regression coverage.
"""

from __future__ import annotations

from gtopt_shared.testing.snapshot import (
    assert_golden_file,
    assert_snapshot,
    assert_text_snapshot,
    canonicalise_json,
)


__all__ = [
    "assert_golden_file",
    "assert_snapshot",
    "assert_text_snapshot",
    "canonicalise_json",
]
