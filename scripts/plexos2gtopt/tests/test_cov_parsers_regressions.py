# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Regression tests for bugs found while raising parsers.py coverage."""

from __future__ import annotations

from pathlib import Path

from plexos2gtopt import parsers as P
from plexos2gtopt.tests.cov_parsers_kit import DbKit, bundle


def test_discharge_bounds_name_with_only_bad_rows_is_ignored(tmp_path: Path) -> None:
    """A bound name whose every CSV row is unparseable must not crash.

    Before the fix the name was registered with an empty RHS series before
    the value parsed, and ``rhs_days[0]`` raised ``IndexError``.
    """
    kit = DbKit()
    gen = kit.obj("Generator", "U1")
    good = kit.obj("Constraint", "PLANTmax")
    bad = kit.obj("Constraint", "BADmax")
    kit.mem("Generator", "Constraint", gen, good, coll_name="Constraints")
    kit.mem("Generator", "Constraint", gen, bad, coll_name="Constraints")
    body = (
        "NAME,YEAR,MONTH,DAY,PERIOD,VALUE\n"
        "PLANTmax,2026,1,1,1,60\n"
        "BADmax,2026,1,1,1,n/a\n"
        "BADmax,2026\n"
    )
    b = bundle(tmp_path, {"Hydro_AntucoBounds.csv": body})
    out = P.extract_hydro_discharge_user_constraints(kit.db, b, ())
    assert [uc.name for uc in out] == ["discharge_PLANTmax"]
