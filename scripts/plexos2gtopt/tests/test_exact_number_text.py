# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Model text (pampl RHS vectors, UC expressions) keeps full precision.

CEN's ``ANTUCOmin`` RHS 163.141898459795 MW uses the Laja water budget down
to the ELTORO / POLCURA end volumes to ~1e-11; the old ``:g`` text
``163.142`` made PCP 2025-02-17 infeasible.
"""

from __future__ import annotations

import pytest

from plexos2gtopt import parsers
from plexos2gtopt._numfmt import fmt_exact
from plexos2gtopt.gtopt_writer import write_user_constraint_pampl


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (1.0, "1"),
        (0.5, "0.5"),
        (268.8, "268.8"),
        (-3.0, "-3"),
        (1e-07, "1e-07"),
        (163.141898459795, "163.141898459795"),
        (0.1 + 0.2, "0.30000000000000004"),
        (1234567.0, "1234567.0"),
    ],
)
def test_fmt_exact_round_trips(value: float, text: str) -> None:
    assert fmt_exact(value) == text
    assert float(fmt_exact(value)) == value


def test_coefficient_keeps_full_precision() -> None:
    fmt = parsers._format_coefficient  # pylint: disable=protected-access
    assert fmt(1.0, first=True) == "1 * "
    assert fmt(-0.459802434072152, first=False) == " - 0.459802434072152 * "
    assert fmt(2.5, first=False) == " + 2.5 * "
    assert fmt(-1.0, first=True) == "-1 * "


def test_pampl_rhs_vector_is_exact(tmp_path) -> None:
    rhs = [163.141898459795] * 2 + [165.0]
    ucs = [
        {
            "uid": 1,
            "name": "ANTUCOmin",
            "expression": 'generator("ANTUCO_U1").generation >= 163.141898459795',
            "rhs": [rhs],
        }
    ]
    files, remaining = write_user_constraint_pampl(ucs, tmp_path)
    assert files and not remaining
    text = "\n".join((tmp_path / f).read_text() for f in files)
    assert "rhs [163.141898459795, 163.141898459795, 165]" in text
    assert "163.142" not in text.replace("163.141898459795", "")
