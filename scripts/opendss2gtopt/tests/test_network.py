# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for opendss2gtopt.network (balanced equivalent)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from opendss2gtopt.dss_parser import DssParser, parse_dss
from opendss2gtopt.dss_values import DssValueError
from opendss2gtopt.network import build_network, finalize_capacitor_ratings

DATA = Path(__file__).parent / "data"


def _net(text: str, **kw):
    return build_network(DssParser().parse_text(text), **kw)


@pytest.fixture(name="tiny")
def fixture_tiny():
    return build_network(parse_dss(DATA / "tiny_feeder.dss"))


def test_tiny_buses_and_kv(tiny) -> None:
    assert tiny.source_bus == "src" and tiny.source_pu == pytest.approx(1.02)
    assert tiny.buses["b5"].kv == pytest.approx(12.47)
    assert tiny.buses["c3"].kv == pytest.approx(4.16)
    assert tiny.source_x > 0 and tiny.source_r > 0


def test_tiny_branches(tiny) -> None:
    by = {b.name: b for b in tiny.branches}
    reg = by["rega"]
    assert reg.kind == "transformer" and len(reg.members) == 3
    assert reg.regulator is not None and reg.regulator["vreg"] == 122
    assert reg.rating_mva == pytest.approx(3 * 1.1)
    assert by["sw"].kind == "switch" and by["sw"].rating_mva is None
    lat = by["lat"]
    assert lat.nphases == 1
    # 3/n scaling: 3 x (0.6 + j0.5) ohm/km x 0.5 km
    assert lat.r == pytest.approx(0.9) and lat.x == pytest.approx(0.75)
    assert by["l1"].rating_mva == pytest.approx(math.sqrt(3) * 12.47 * 0.3)
    assert by["c2c3"].charging_mvar > 0
    sub = by["sub"]
    # %r 0.5+0.5, XHL 3 % on 3 MVA at 4.16 kV
    zb = 4.16**2 / 3.0
    assert sub.r == pytest.approx(0.01 * zb) and sub.x == pytest.approx(0.03 * zb)


def test_pos_seq_scaling_and_errors() -> None:
    text = (DATA / "tiny_feeder.dss").read_text()
    net = build_network(DssParser().parse_text(text, DATA), scaling="pos-seq")
    lat = next(b for b in net.branches if b.name == "lat")
    assert lat.r == pytest.approx(0.3)
    with pytest.raises(ValueError):
        build_network(DssParser().parse_text(text, DATA), scaling="bad")
    with pytest.raises(DssValueError):
        _net("New Load.a bus1=x")


def test_tiny_injections(tiny) -> None:
    loads = {x.name: x for x in tiny.loads}
    assert loads["d1"].p_mw == pytest.approx(0.9) and loads[
        "d1"
    ].q_mvar == pytest.approx(0.3)
    assert loads["d2"].q_mvar == pytest.approx(0.15 * math.tan(math.acos(0.9)))
    assert loads["d3"].q_mvar < 0
    assert loads["d4"].p_mw == pytest.approx(0.24)
    gens = {g.name: g for g in tiny.generators}
    assert gens["g2"].extra["qmax"] == pytest.approx(0.08)
    assert gens["pv1"].p_mw == pytest.approx(0.45) and gens["pv1"].kind == "pv"
    assert gens["pv2"].extra["qmin"] == pytest.approx(-0.04)
    st = tiny.storages[0]
    assert st.extra["kwh"] == pytest.approx(1.2) and st.extra[
        "stored"
    ] == pytest.approx(0.5)
    caps = {c.name: c for c in tiny.capacitors}
    assert caps["csw"].extra["switched"] and caps["csw"].extra["states"] == [1, 0, 0]
    assert not caps["cfix"].extra["switched"]
    finalize_capacitor_ratings(tiny)
    assert caps["cfix"].q_mvar == pytest.approx(0.3)
    assert caps["c1ph"].q_mvar == pytest.approx(
        0.05 * (12.47 / math.sqrt(3) / 7.2) ** 2
    )
    assert set(tiny.loadshapes) == {"day", "sun"}
    assert "lines" in tiny.phase_data and "loads" in tiny.phase_data


def test_misc_elements_and_warnings() -> None:
    net = _net(
        "New Circuit.m basekv=13.8 bus1=s r1=0.1 x1=0.5\n"
        "New Line.self bus1=s bus2=s\n"
        "New Line.off bus1=s bus2=z enabled=no\n"
        "New Line.a bus1=s bus2=t r1=0.1 x1=0.2 units=km length=1\n"
        "New Line.island bus1=q bus2=r r1=0.1 x1=0.2\n"
        "New Transformer.t3 windings=3 buses=[t u v] kvs=[13.8 4.16 0.48] kvas=[5000 5000 1000]\n"
        "New Transformer.t4 windings=4 buses=[t w x y]\n"
        "New Capacitor.ser bus1=t bus2=u\n"
        "New Capacitor.k bus1=u kvar=[100] numsteps=2\n"
        "New Load.kv bus1=t kW=10 kvar=5 pf=0.9\n"
        "New Load.pf1 bus1=t kW=10 pf=1\n"
        "New Load.off bus1=t enabled=false\n"
        "New Generator.g bus1=t kW=10 kvar=3 kVA=12\n"
        "New Generator.off bus1=t enabled=no\n"
        "New PVSystem.p bus1=t Pmpp=10 kvar=2\n"
        "New PVSystem.off bus1=t enabled=no\n"
        "New Storage.s bus1=t kWhstored=10\n"
        "New Storage.off bus1=t enabled=no\n"
        "New Capacitor.off bus1=t enabled=no\n"
        "New CapControl.cc capacitor=k\n"
        "New CapControl.off capacitor=k enabled=no\n"
        "New RegControl.off transformer=t3 enabled=no\n"
        "New Transformer.off buses=[t z] enabled=no\n"
        "New LoadShape.f npts=2 mult=(file=x.csv)\n"
        "New LoadShape.e npts=2\n"
        "Set voltagebases=[13.8 4.16 0.48]\n"
    )
    assert net.source_r == pytest.approx(0.1)
    names = {b.name for b in net.branches}
    assert {"t3_w1", "t3_w2", "t3_w3"} <= names and "t4" not in names
    assert net.buses["v"].kv == pytest.approx(0.48)
    joined = " ".join(net.warnings)
    for frag in (
        "degenerate",
        "3-winding",
        "unsupported",
        "series",
        "file-based",
        "not connected",
    ):
        assert frag in joined
    loads = {x.name: x for x in net.loads}
    assert loads["kv"].q_mvar == pytest.approx(
        0.01 * math.tan(math.acos(0.9))
    )  # pf set last
    assert loads["pf1"].q_mvar == 0.0
    g = net.generators[0]
    assert g.q_mvar == pytest.approx(0.003) and g.extra["smax"] == pytest.approx(0.012)
    assert net.generators[1].q_mvar == pytest.approx(0.002)
    assert net.storages[0].extra["stored"] == pytest.approx(10 / 50)
    cap = net.capacitors[0]
    assert cap.extra["switched"] and len(cap.extra["steps"]) == 2


def test_snap_far_base_and_delta_kv() -> None:
    net = _net(
        "New Circuit.m basekv=13.8 bus1=s\n"
        "New Transformer.t phases=1 buses=[s.1 d.1.2] kvs=[7.97 0.24] conns=[wye delta]\n"
        "Set voltagebases=[13.8]\n"
    )
    assert net.buses["d"].kv == pytest.approx(13.8 / (7.97 * math.sqrt(3)) * 0.24)
