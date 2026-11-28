# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for opendss2gtopt.impedance."""

from __future__ import annotations

import numpy as np
import pytest

from opendss2gtopt.dss_parser import DssParser
from opendss2gtopt.dss_values import DssValueError
from opendss2gtopt.impedance import (
    kron,
    line_impedance,
    linecode_data,
    seq_from_matrix,
)

# Kersting, "Distribution System Modeling and Analysis", Example 4.1:
# 336,400 26/7 ACSR phases, 4/0 6/1 ACSR neutral.
KERSTING = """
New WireData.p Rac=0.306 Runits=mi GMRac=0.0244 GMRunits=ft Diam=0.721 Radunits=in
New WireData.n Rac=0.592 Runits=mi GMRac=0.00814 GMRunits=ft Diam=0.563 Radunits=in
New LineGeometry.g nconds=4 nphases=3 reduce=yes units=ft
~ cond=1 wire=p x=0 h=29
~ cond=2 wire=p x=2.5 h=29
~ cond=3 wire=p x=7 h=29
~ cond=4 wire=n x=4 h=25
New Line.l geometry=g length=1 units=mi
"""


def _circuit(text: str):
    return DssParser().parse_text(text)


def test_kersting_example_4_1() -> None:
    c = _circuit(KERSTING)
    imp = line_impedance(c, c.get("line", "l"), 60.0)
    expected = np.array(
        [
            [0.4576 + 1.0780j, 0.1560 + 0.5017j, 0.1535 + 0.3849j],
            [0.1560 + 0.5017j, 0.4666 + 1.0482j, 0.1580 + 0.4236j],
            [0.1535 + 0.3849j, 0.1580 + 0.4236j, 0.4615 + 1.0651j],
        ]
    )
    assert np.allclose(imp.z, expected, atol=2e-3)
    assert imp.nphases == 3
    assert imp.c1() > 0


def test_sequence_and_matrix_sources() -> None:
    c = _circuit(
        "New Line.s r1=0.1 x1=0.2 r0=0.3 x0=0.6 c1=10 c0=5 length=2\n"
        "New Line.b phases=1 r1=0.1 x1=0.2 b1=3.77 b0=2 length=1\n"
        "New Line.m phases=2 rmatrix=[1 | 0.5 1] length=1\n"
        "New Line.x phases=2 xmatrix=[1 | 0.5 1] cmatrix=[2 | 0 2]\n"
        "New Line.e phases=2 rmatrix=[1]\n"
    )
    s = line_impedance(c, c.get("line", "s"), 60.0)
    assert s.z1() == pytest.approx(0.2 + 0.4j)
    assert s.c1() == pytest.approx(20e-9)
    b = line_impedance(c, c.get("line", "b"), 60.0)
    assert b.c1() == pytest.approx(3.77e-6 / (2 * np.pi * 60))
    m = line_impedance(c, c.get("line", "m"), 60.0)
    assert m.z1() == pytest.approx(0.5)
    x = line_impedance(c, c.get("line", "x"), 60.0)
    assert x.z1() == pytest.approx(0.5j)
    with pytest.raises(DssValueError):
        line_impedance(c, c.get("line", "e"), 60.0)
    c.get("line", "e").props.pop("rmatrix")
    with pytest.raises(DssValueError):
        line_impedance(c, c.get("line", "e"), 60.0)


def test_linecode_units_kron_and_ratings() -> None:
    c = _circuit(
        "New LineCode.k nphases=3 units=kft r1=1 x1=1 normamps=123 emergamps=200 kron=y\n"
        "New LineCode.m nphases=2 units=none rmatrix=[1|0|1]\n"
        "New Line.a linecode=k length=1 units=mi\n"
        "New Line.b linecode=m length=3 phases=1\n"
        "New Line.c linecode=nope\n"
    )
    n, z, _, units = linecode_data(c.get("linecode", "k"), 60.0)
    assert n == 2 and units == "kft"
    a = line_impedance(c, c.get("line", "a"), 60.0)
    assert a.normamps == 123 and a.emergamps == 200
    b = line_impedance(c, c.get("line", "b"), 60.0)
    assert b.nphases == 1 and b.z1() == pytest.approx(3.0)
    with pytest.raises(DssValueError):
        line_impedance(c, c.get("line", "c"), 60.0)
    assert z.shape == (2, 2)


def test_geometry_errors() -> None:
    base = KERSTING.replace("New Line.l geometry=g length=1 units=mi", "")
    c = _circuit(base + "New Line.u geometry=missing\n")
    with pytest.raises(DssValueError):
        line_impedance(c, c.get("line", "u"), 60.0)
    c2 = _circuit(base.replace("wire=n", "wire=zz") + "New Line.l geometry=g\n")
    with pytest.raises(DssValueError):
        line_impedance(c2, c2.get("line", "l"), 60.0)
    c3 = _circuit(
        "New WireData.w GMRac=0.01\nNew LineGeometry.g nconds=1 nphases=1\n"
        "~ cond=1 wire=w x=0 h=30\nNew Line.l geometry=g length=1 units=mi\n"
    )
    with pytest.raises(DssValueError):
        line_impedance(c3, c3.get("line", "l"), 60.0)
    c3.get("wiredata", "w").props["rac"] = "0.5"
    with pytest.raises(DssValueError):
        line_impedance(c3, c3.get("line", "l"), 60.0)
    c3.get("wiredata", "w").props["radius"] = "0.5"
    c3.get("wiredata", "w").props["radunits"] = "in"
    imp = line_impedance(c3, c3.get("line", "l"), 60.0)
    assert imp.z[0, 0].real > 0.5
    c4 = _circuit(
        "New LineGeometry.g nconds=2\n~ cond=1 x=0 h=1\nNew Line.l geometry=g\n"
    )
    with pytest.raises(DssValueError):
        line_impedance(c4, c4.get("line", "l"), 60.0)


def test_unreduced_geometry_and_helpers() -> None:
    c = _circuit(KERSTING.replace("reduce=yes", "reduce=no") + "")
    imp = line_impedance(c, c.get("line", "l"), 60.0)
    assert imp.z.shape == (4, 4)  # unreduced: the neutral stays a conductor
    m = np.array([[2.0, 1.0], [1.0, 2.0]])
    assert seq_from_matrix(m) == pytest.approx(1.0)
    assert seq_from_matrix(np.array([[3.0]])) == pytest.approx(3.0)
    assert kron(m, [1])[0, 0] == pytest.approx(1.5)
