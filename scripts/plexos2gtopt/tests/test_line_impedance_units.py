# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""PLEXOS line / transformer impedances → gtopt per-unit quantities.

PLEXOS ships ``Reactance`` and ``Resistance`` in per-unit on the 100 MVA
system base for every line AND transformer, independent of the bus kV.
gtopt reads ``x = X / V²`` [rad/MW] and ``P_loss = R·f² / V²`` [MW], so
the writer emits ``voltage = √S_base = 10`` on every line with an
impedance (NOT the physical kV of either bus): then ``x = X_pu / 100`` —
the DC power-flow angle drop per MW on the 100 MVA base — for a 500 kV
line, a 220/154 kV transformer or a 10-something kV feeder alike.

The values below are synthetic (no operator data).
"""

from __future__ import annotations

from typing import Any

import pytest

from plexos2gtopt.entities import LineSpec
from plexos2gtopt.gtopt_writer import build_line_array

S_BASE_MVA = 100.0


def _line(name: str, bus_from: str, bus_to: str, x: float, r: float) -> LineSpec:
    return LineSpec(
        object_id=1,
        name=name,
        bus_from=bus_from,
        bus_to=bus_to,
        tmax_ab=300.0,
        tmin_ab=-300.0,
        resistance=r,
        reactance=x,
    )


def _x_tau(entry: dict[str, Any]) -> float:
    v = float(entry.get("voltage", 1.0))
    return float(entry["reactance"]) / (v * v)


@pytest.mark.parametrize(
    ("name", "bus_from", "bus_to", "x_pu", "r_pu"),
    [
        # Transmission line, both ends at the same kV.
        ("A500->B500", "A500", "B500", 0.0050, 0.0004),
        # Transformer 220/154 kV: high X/R, per-unit on the system base.
        ("T220->T154", "T220", "T154", 0.0160, 0.00007),
        # Transformer 154/66 kV with a large (small-MVA) reactance.
        ("U154->U066", "U154", "U066", 1.5000, 0.1500),
        # Low-voltage feeder: the bus name says 100 kV, V = 10 is the
        # √S_base convention, not a physical 10 kV.
        ("F100->G100", "F100", "G100", 0.0050, 5e-6),
        # Generator connection placeholder: X = 1e-4 p.u., no R.
        ("Pv033->H220", "Pv033", "H220", 0.0001, 0.0),
    ],
)
def test_impedance_per_unit_on_system_base(
    name: str, bus_from: str, bus_to: str, x_pu: float, r_pu: float
) -> None:
    entry = build_line_array((_line(name, bus_from, bus_to, x_pu, r_pu),))[0]
    # Verbatim per-unit values, V = √S_base on every impedance line.
    assert entry["reactance"] == pytest.approx(x_pu)
    assert entry["voltage"] == pytest.approx(S_BASE_MVA**0.5)
    # x = X/V² is the per-MW angle drop: X_pu / S_base.
    assert _x_tau(entry) == pytest.approx(x_pu / S_BASE_MVA)
    if r_pu > 0.0:
        assert entry["resistance"] == pytest.approx(r_pu)
        # Loss at 100 MW (= 1 p.u.) is R_pu · 1² · S_base [MW].
        f_mw = S_BASE_MVA
        loss = entry["resistance"] * f_mw**2 / entry["voltage"] ** 2
        assert loss == pytest.approx(r_pu * S_BASE_MVA)
    else:
        assert "resistance" not in entry


def test_transformer_x_independent_of_bus_kv() -> None:
    """The bus kV never enters the per-unit reactance (system base)."""
    out = build_line_array(
        (
            _line("T500->T220", "T500", "T220", 0.02, 0.0),
            _line("T110->T066", "T110", "T066", 0.02, 0.0),
        )
    )
    a, b = out[0], out[1]
    assert _x_tau(a) == pytest.approx(_x_tau(b)) == pytest.approx(2e-4)


def test_zero_reactance_is_a_dc_link() -> None:
    """PLEXOS Reactance 0 omits the key (DC link), never an explicit 0."""
    entry = build_line_array((_line("D1->D2", "D1", "D2", 0.0, 0.0),))[0]
    assert "reactance" not in entry
    assert "voltage" not in entry


def test_placeholder_reactance_is_below_the_relative_tie_floor() -> None:
    """X = 1e-4 p.u. placeholders sit at x = 1e-6, the absolute floor.

    With a typical network median ``x`` of ~2e-4 the relative floor
    (``tiny_reactance_rel_threshold = 0.01``) is ~2e-6, so gtopt ties
    them instead of carrying a 1e-6 KVL coefficient.
    """
    entry = build_line_array((_line("Pv033->H220", "Pv033", "H220", 1e-4, 0.0),))[0]
    x = _x_tau(entry)
    assert x == pytest.approx(1e-6)
    typical_median = 2e-4
    assert x < 0.01 * typical_median
