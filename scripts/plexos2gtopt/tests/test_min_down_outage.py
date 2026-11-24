# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Min-down time vs a Commit=1 pin right after a rating-0 outage.

MACHICURA_U1 on 2021-10-24: committed h1-5 (Commit=1), rating 0 at h6-7,
pinned on again from h8, MinDownTime 4 -> the min-down row triggered by
the shutdown into the outage forbids the forced restart (infeasible even
as an LP).  An outage is not a voluntary shutdown and the pin wins.
"""

from __future__ import annotations

from plexos2gtopt.parsers import min_down_across_outages

_PMAX = (18.1,) * 5 + (0.0, 0.0) + (18.1,) * 17
_PIN = (1,) * 5 + (-1, -1) + (1,) * 17


def test_machicura_pattern_caps_to_outage_length() -> None:
    assert min_down_across_outages(4.0, _PMAX, _PIN) == 2.0


def test_no_outage_keeps_min_down() -> None:
    assert min_down_across_outages(4.0, (18.1,) * 24, _PIN) == 4.0


def test_pin_after_window_keeps_min_down() -> None:
    # Outage h6-7, unit free afterwards and pinned on only from h12:
    # the min-down window (h6-9) holds no pin -> no conflict.
    pin = (1,) * 5 + (-1,) * 6 + (1,) * 13
    assert min_down_across_outages(4.0, _PMAX, pin) == 4.0


def test_free_restart_is_not_capped() -> None:
    # No Commit=1 pin anywhere after the outage: the restart is voluntary,
    # min-down stays in force.
    pin = (1,) * 5 + (-1,) * 19
    assert min_down_across_outages(4.0, _PMAX, pin) == 4.0


def test_pin_inside_window_after_free_gap() -> None:
    # Outage h6, free h7, pinned on h8: distance from outage start = 2.
    pmax = (18.1,) * 5 + (0.0,) + (18.1,) * 18
    pin = (1,) * 5 + (-1, -1) + (1,) * 17
    assert min_down_across_outages(4.0, pmax, pin) == 2.0


def test_shortest_conflicting_outage_wins() -> None:
    pmax = list(_PMAX)
    pin = list(_PIN)
    pmax[15] = 0.0  # one-hour outage at h16, pinned on at h17
    pin[15] = -1
    assert min_down_across_outages(4.0, pmax, pin) == 1.0
