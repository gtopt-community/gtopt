# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for opendss2gtopt.dss_parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from opendss2gtopt.dss_parser import (
    DssParseError,
    DssParser,
    parse_dss,
    strip_comment,
    tokenize,
)

DATA = Path(__file__).parent / "data"


def _parse(text: str, base: Path | None = None):
    return DssParser().parse_text(text, base)


def test_strip_comment() -> None:
    assert strip_comment("New x ! c") == "New x "
    assert strip_comment("a=1 // c") == "a=1 "
    assert strip_comment('a="x!y"') == 'a="x!y"'
    assert strip_comment("m=(1 ! 2)") == "m=(1 ! 2)"
    assert strip_comment("a=(1)) ! x") == "a=(1)) "


def test_tokenize() -> None:
    toks = tokenize("New Line.a bus1=x, bus2 = y rmatrix = (1 | 2 3) 5 'q r'")
    assert toks == [
        (None, "New"),
        (None, "Line.a"),
        ("bus1", "x"),
        ("bus2", "y"),
        ("rmatrix", "(1 | 2 3)"),
        (None, "5"),
        (None, "'q r'"),
    ]
    assert not tokenize("   ")
    assert tokenize("a=") == [("a", "")]
    with pytest.raises(DssParseError):
        tokenize("x=[1 2")


def test_tiny_feeder_objects() -> None:
    c = parse_dss(DATA / "tiny_feeder.dss")
    assert c.name == "tiny"
    src = c.get("vsource", "source")
    assert src is not None
    assert src.get("mvasc1") == "2100"  # positional after mvasc3
    assert c.get("linecode", "lc3") is not None  # redirected file
    sub = c.get("transformer", "sub")
    assert sub is not None and sub.get("xhl") == "(6 2 /)"
    assert sub.windings[1]["bus"] == "c1"
    regb = c.get("transformer", "regb")
    assert regb is not None
    assert regb.windings[0]["bus"] == "c1.2"  # like= then override
    assert regb.windings[0]["%r"] == "0.005"  # %loadloss halves
    sw = c.get("line", "sw")
    assert sw is not None and sw.get("r1") == "1e-4" and sw.get("length") == "0.001"
    assert c.get("load", "d4").get("vminpu") == "0.85"  # Class.name.prop=
    assert c.options["voltagebases"] == "[12.47, 4.16]"
    assert any(cmd.lower().startswith("solve") for cmd in c.commands)
    assert c.of("nothing") == {}


def test_continuations_and_more(tmp_path: Path) -> None:
    c = _parse(
        "New Load.a bus1=x\n~ kW=5\nmore kvar=2\nm pf=0.9\nkW=7\n",
        tmp_path,
    )
    load = c.get("load", "a")
    assert load is not None
    assert load.get("kw") == "7" and load.get("kvar") == "2" and load.get("pf") == "0.9"


def test_edit_like_and_unknown(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    c = _parse(
        "New Load.a kW=1 kvar=2\n"
        "New Load.b like=a kW=3\n"
        "Edit Load.a kW=9\n"
        "Edit Load.new kW=4\n"
        "New Load.c like=missing\n"
        "Load.zz.kW=1\n",
        tmp_path,
    )
    assert c.get("load", "b").get("kvar") == "2"
    assert c.get("load", "a").get("kw") == "9"
    assert c.get("load", "new").get("kw") == "4"
    assert "not found" in caplog.text and "undefined" in caplog.text


def test_positional_overflow(caplog: pytest.LogCaptureFixture) -> None:
    order_len = 60
    vals = " ".join(["1"] * order_len)
    c = _parse(f"New Capacitor.c bus1=x {vals}")
    assert c.get("capacitor", "c") is not None
    assert "extra value" in caplog.text


def test_set_clear_and_misc() -> None:
    c = _parse("Set voltagebases=[1 2] mode=snap\nSet x\nfoo=1\nshow voltages\n")
    assert c.options["mode"] == "snap"
    assert "foo=1" in c.commands
    c2 = _parse("New Load.a\nClear\n")
    assert c2.of("load") == {}


@pytest.mark.parametrize(
    "text",
    ["New", "New Line.a\nNew foo=bar", "New nodot", "Redirect", "~ kW=1"],
)
def test_errors(text: str) -> None:
    with pytest.raises(DssParseError):
        _parse(text)


def test_redirect_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "Codes.DSS").write_text("New LineCode.z nphases=1\n")
    c = _parse("redirect codes.dss\ncompile Codes.DSS\n", tmp_path)
    assert c.get("linecode", "z") is not None
    with pytest.raises(FileNotFoundError):
        _parse("redirect nope.dss", tmp_path)
    with pytest.raises(FileNotFoundError):
        _parse("redirect sub/nope.dss", tmp_path)
    with pytest.raises(FileNotFoundError):
        parse_dss(tmp_path / "missing.dss")


def test_block_comment_single_line() -> None:
    c = _parse(
        "/* one line */\nNew Load.a kW=1\n/* open\nNew Load.hidden\n*/ New Load.b\n"
    )
    assert c.get("load", "a") is not None
    assert c.get("load", "hidden") is None


def test_transformer_windings() -> None:
    c = _parse(
        "New Transformer.t windings=3 buses=[a b c] kvs=[115 13.8 4.16] "
        "kvas=[10 10 5] %rs=[1 1 1] conns=[delta wye wye] taps=[1 1.05 1]\n"
        "~ X12=8 X13=10 X23=6 wdg=3 conn=delta kv=4.2 windings=2\n"
    )
    t = c.get("transformer", "t")
    assert t is not None
    assert len(t.windings) == 2
    assert t.get("xhl") == "8" and t.get("xht") == "10" and t.get("xlt") == "6"
    assert t.windings[1]["tap"] == "1.05"
    c2 = _parse("New Transformer.u buses=[a b c d]\n")
    assert len(c2.get("transformer", "u").windings) == 4


def test_line_impedance_sources() -> None:
    c = _parse(
        "New Line.a linecode=x r1=1\nNew Line.b r1=1 linecode=x\n"
        "New Line.c rmatrix=[1] geometry=g\nNew Line.d geometry=g rmatrix=[1]\n"
    )
    assert c.get("line", "a").zsource == "seq"
    assert c.get("line", "b").zsource == "linecode"
    assert c.get("line", "c").zsource == "geometry"
    assert c.get("line", "d").zsource == "matrix"


def test_linegeometry_conductors() -> None:
    c = _parse(
        "New LineGeometry.g nconds=2 nphases=1 units=ft\n"
        "~ x=0 h=30\n"
        "~ cond=2 wire=n x=1 h=25\n"
    )
    g = c.get("linegeometry", "g")
    assert g is not None
    assert g.windings[0] == {"units": "ft", "x": "0", "h": "30"}
    assert g.windings[1]["wire"] == "n"


def test_verb_prefix() -> None:
    c = _parse("New Load.a\ncalcv\nNew Load.b\n")
    assert "calcv" in c.commands
    c2 = _parse("x\n")
    assert c2.commands == ["x"]
