# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage tests for :mod:`cen2gtopt._topology` helpers."""

from __future__ import annotations

from cen2gtopt._topology import _load_cen_format, _opt_float


def test_opt_float_variants() -> None:
    assert _opt_float(None) is None
    assert _opt_float("1.5") == 1.5
    assert _opt_float(2) == 2.0
    assert _opt_float("x") is None
    assert _opt_float(object()) is None


def test_line_reactance_zero_kept_absent_falls_back_to_x() -> None:
    topo = _load_cen_format(
        {
            "buses": [{"uid": 1, "name": "a"}, {"uid": 2, "name": "b"}],
            "lines": [
                {"uid": 1, "bus_a": "a", "bus_b": "b", "reactance": 0.0, "x": 9},
                {"uid": 2, "bus_a": "a", "bus_b": "b", "x": 0.2},
                {"uid": 3, "bus_a": "a", "bus_b": "b"},
            ],
        }
    )
    # 0 is a zero-impedance tie (kept); absent -> ``x`` -> else None (DC).
    assert [ln.reactance for ln in topo.lines] == [0.0, 0.2, None]
