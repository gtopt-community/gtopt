# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Generic-extractor fixes for the CEN PCP reserve constraints.

Month / combined timeslices, PLEXOS Sense 0 = equality and ``*_Def``
definitions that keep their PLEXOS RHS (checked on the mini CEN-PCP
fixture through the non-native ``--reserve-model uc`` path).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from plexos2gtopt import parsers as P
from plexos2gtopt._native_reserve import slice_matcher
from plexos2gtopt.plexos_model import pattern_is_hour_only

from .test_native_reserve_e2e import _convert, _pampl


def test_expand_timeslice_month_atoms() -> None:
    """``M`` atoms select calendar months (horizon date required)."""
    nov = datetime(2026, 11, 7)
    assert P._expand_timeslice("M10-12", nov, 24) == (True,) * 24
    assert P._expand_timeslice("M4-9", nov, 24) == (False,) * 24
    # Bare numbers continue the previous kind: M1-3,11 → months 1-3 and 11.
    assert P._expand_timeslice("M1-3,11", nov, 24) == (True,) * 24
    # Without a horizon date a calendar tag is an error, never "all hours".
    with pytest.raises(ValueError, match="horizon start"):
        P._expand_timeslice("M4-9", None, 24)
    with pytest.raises(ValueError, match="horizon start"):
        P._expand_timeslice("H1;W2-6", None, 24)
    # Hour-only tags need no date.
    assert P._expand_timeslice("H2", None, 3) == (False, True, False)


def test_expand_timeslice_combined_parts() -> None:
    """``;`` ORs parts; within a part month and hour atoms are AND-ed."""
    tag = "M4-9,H10-17;M1-3,H9-19;M10-12,H9-19"
    nov = P._expand_timeslice(tag, datetime(2026, 11, 7), 24)
    assert nov == tuple(9 <= h + 1 <= 19 for h in range(24))
    jun = P._expand_timeslice(tag, datetime(2026, 6, 7), 24)
    assert jun == tuple(10 <= h + 1 <= 17 for h in range(24))
    # A horizon crossing a month boundary switches part per day.
    two = P._expand_timeslice(tag, datetime(2026, 9, 30), 48)
    assert sum(two[:24]) == 8
    assert sum(two[24:]) == 11
    # Day-of-month atoms.
    assert P._expand_timeslice("D7,H1", datetime(2026, 11, 7), 48) == (
        (True,) + (False,) * 47
    )


def test_pattern_is_hour_only() -> None:
    assert pattern_is_hour_only("H1-8,,20-24")
    assert pattern_is_hour_only("!H3")
    assert not pattern_is_hour_only("H1,M4")
    assert not pattern_is_hour_only("H1,?")


def test_expand_timeslice_negation() -> None:
    """A leading ``!`` negates the part (same grammar as the native path)."""
    assert P._expand_timeslice("!H1-22", None, 24) == (False,) * 22 + (True,) * 2


def test_expand_timeslice_matches_native_slice_matcher() -> None:
    """Parity: the extractor mask equals the native ``slice_matcher``."""
    tags = ("M4-9,H10-17;M1-3,H9-19;M10-12,H9-19", "W2-6,H8-21", "D8,H3-4", "!M11")
    for start in (datetime(2026, 11, 7), datetime(2026, 6, 6), datetime(2026, 3, 1)):
        for tag in tags:
            match = slice_matcher(tag)
            assert match is not None
            expect = tuple(
                match(start + timedelta(hours=h), (start + timedelta(hours=h)).hour + 1)
                for h in range(72)
            )
            assert P._expand_timeslice(tag, start, 72) == expect, (tag, start)


@pytest.mark.parametrize("tag", ["", " ; ", "X1-3", "H1,Q2", "5"])
def test_expand_timeslice_rejects_bad_tags(tag: str) -> None:
    """Unknown kinds and a leading bare number raise ``ValueError``."""
    with pytest.raises(ValueError, match="timeslice"):
        P._expand_timeslice(tag, None, 24)


def test_sense_op_follows_plexos() -> None:
    """PLEXOS Sense −1 ``<=``, 0 ``=``, 1 ``>=``; legacy Sense 0 is ``>=``."""
    assert P._SENSE_OP == {-1.0: "<=", 0.0: "=", 1.0: ">="}
    assert P._SENSE_ZERO_LEGACY == ">="


def test_extract_case_rejects_bad_sense_zero_op() -> None:
    """Only ``=`` / ``>=`` are valid Sense-0 operators."""
    with pytest.raises(ValueError, match="sense_zero_op"):
        P.extract_case(None, sense_zero_op="<=")  # type: ignore[arg-type]


def _block(text: str, name: str) -> str:
    m = re.search(rf"(?ms)^(?:inactive )?constraint {name}\b[^\n]*:\n.*?;$", text)
    assert m is not None, name
    return m.group(0)


def test_uc_path_reserve_rows(tmp_path: Path) -> None:
    """Non-native extractor: Def RHS, legacy Sense-0 ``>=``, month slice."""
    _, out_dir = _convert(tmp_path, reserve_model="uc")
    text = _pampl(out_dir)
    # CSF_RS_Def (Sense unset, RHS 0) defines the requirement DV: it keeps
    # RHS 0, not the reserve-requirement profile.
    csf_def = _block(text, "CSF_RS_Def")
    assert " rhs [" not in csf_def.splitlines()[0]
    assert csf_def.endswith(">= 0;")
    # uc mode keeps the legacy ``>=`` for Sense 0 (hard balances).
    assert _block(text, "CPF_Up10MinProvision").endswith(">= 0;")
    # InertiaNRMin_D's RHS 700 applies on the November part (H9-19).
    nrmin = _block(text, "InertiaNRMin_D").splitlines()[0]
    rhs = [float(v) for v in nrmin.split("rhs [")[1].split("]")[0].split(",")]
    assert rhs == [700.0 if 9 <= h + 1 <= 19 else 0.0 for h in range(24)]


def test_native_path_sense_zero_is_equality(tmp_path: Path) -> None:
    """Native mode writes PLEXOS Sense 0 / unset rows as equalities."""
    _, out_dir = _convert(tmp_path)
    text = _pampl(out_dir)
    csf_def = _block(text, "CSF_RS_Def")
    assert re.search(r"[^<>]= 0;$", csf_def), csf_def
    # An explicit Sense 1 row stays ``>=``.
    assert _block(text, "Inertia_Calculation_e1").endswith(">= -100;")
