"""Unit tests: PLP loss tramos -> tangent_signed_flow tangent count.

PLP ``plpcnfli.dat`` Num.Tramos (``LinNFlu``) is the number of flow tramos
PER DIRECTION (``leecnfli.f``); losses are modelled only when the global
FPerdTram flag and the per-line Mod.Perd. flag are both true.  plp2gtopt
maps them onto gtopt's per-line ``loss_segments`` / ``use_line_losses``.
"""

from __future__ import annotations

import argparse
import typing
from pathlib import Path

import pytest

from plp2gtopt.line_parser import LineParser
from plp2gtopt.line_writer import (
    DEFAULT_LOSS_TANGENTS,
    LineWriter,
    loss_segments_for,
    parse_loss_tangents,
)
from plp2gtopt.main import build_options, make_parser

TSF = "tangent_signed_flow"


def _line(number: int, name: str, *, lossy: bool, tramos: int, r: float = 2.0):
    return {
        "number": number,
        "name": name,
        "operational": 1,
        "bus_a": 1,
        "bus_b": number + 1,
        "voltage": 220.0,
        "r": r,
        "x": 10.0,
        "tmax_ab": 150.0,
        "tmax_ba": 120.0,
        "mod_perdidas": lossy,
        "num_sections": tramos,
    }


class _MockLineParser:  # pylint: disable=too-few-public-methods
    loss_allocation_mode = "split"

    def __init__(self, data, fperdtram: bool = True):
        self._data = data
        self.loss_modeling_enabled = fperdtram

    def get_all(self):
        return self._data


def _writer(tmp_path: Path, data, *, mode=TSF, fperdtram=True, **extra):
    parser: typing.Any = _MockLineParser(data, fperdtram)
    options: dict = {"output_dir": tmp_path, "model_options": {}, **extra}
    if mode is not None:
        options["model_options"]["line_losses_mode"] = mode
    return LineWriter(parser, options=options)


@pytest.mark.parametrize(
    ("mode", "spec", "tramos", "expected"),
    [
        (TSF, "tramos", 3, 3),
        (TSF, "tramos", 5, 5),
        (TSF, "double", 3, 6),
        (TSF, 8, 3, 8),
        ("piecewise_direct", "tramos", 3, 3),
        ("piecewise_direct", "double", 3, 3),
        ("piecewise_direct", 8, 3, 3),
        (None, "double", 4, 4),
        ("bidirectional", "tramos", 2, 2),
    ],
)
def test_loss_segments_for(mode, spec, tramos, expected):
    """Only tangent_signed_flow re-maps the count; others keep Num.Tramos."""
    assert loss_segments_for(mode, tramos, spec) == expected


def test_parse_loss_tangents_valid():
    """Symbolic policies (case-insensitive) and integers >= 2 are accepted."""
    assert parse_loss_tangents("tramos") == "tramos"
    assert parse_loss_tangents(" DOUBLE ") == "double"
    assert parse_loss_tangents("6") == 6
    assert parse_loss_tangents("2") == 2


@pytest.mark.parametrize("bad", ["1", "0", "-3", "many", "3.5", ""])
def test_parse_loss_tangents_invalid(bad):
    """Integers < 2 and unknown words are rejected with ArgumentTypeError."""
    with pytest.raises(argparse.ArgumentTypeError):
        parse_loss_tangents(bad)


def test_default_double_gives_k_twice_tramos(tmp_path):
    """Default policy: K = 2 x PLP Num.Tramos (one tangent per PLP tramo
    per flow direction), per line (counts may differ)."""
    data = [
        _line(1, "a", lossy=True, tramos=3),
        _line(2, "b", lossy=True, tramos=5),
        _line(3, "c", lossy=True, tramos=1),
    ]
    out = {ln["name"]: ln for ln in _writer(tmp_path, data).to_json_array()}
    assert out["a"]["loss_segments"] == 6
    assert out["b"]["loss_segments"] == 10
    # Num.Tramos = 1 -> no loss_segments (gtopt linear fallback == PLP's
    # single-tramo secant); losses stay on.
    assert "loss_segments" not in out["c"]
    assert all(ln["use_line_losses"] is True for ln in out.values())
    assert DEFAULT_LOSS_TANGENTS == "double"


def test_tramos_policy_gives_k_equal_tramos(tmp_path):
    """'tramos' -> K = Num.Tramos (the smallest LP)."""
    data = [_line(1, "a", lossy=True, tramos=3), _line(2, "b", lossy=True, tramos=5)]
    out = _writer(tmp_path, data, loss_tangents="tramos").to_json_array()
    assert [ln["loss_segments"] for ln in out] == [3, 5]


def test_double_policy_and_fixed_k(tmp_path):
    """'double' -> 2 x Num.Tramos; an integer -> that K on every PWL line."""
    data = [_line(1, "a", lossy=True, tramos=3), _line(2, "b", lossy=True, tramos=4)]
    dbl = _writer(tmp_path, data, loss_tangents="double").to_json_array()
    assert [ln["loss_segments"] for ln in dbl] == [6, 8]
    fixed = _writer(tmp_path, data, loss_tangents=4).to_json_array()
    assert [ln["loss_segments"] for ln in fixed] == [4, 4]


def test_piecewise_direct_keeps_tramos(tmp_path):
    """PLP-replication mode keeps the per-direction Num.Tramos verbatim."""
    data = [_line(1, "a", lossy=True, tramos=3)]
    out = _writer(
        tmp_path, data, mode="piecewise_direct", loss_tangents="double"
    ).to_json_array()
    assert out[0]["loss_segments"] == 3


def test_plp_lossless_lines_stay_lossless(tmp_path):
    """Mod.Perd. = F -> use_line_losses false and no loss_segments."""
    data = [
        _line(1, "lossy", lossy=True, tramos=3),
        _line(2, "nolossR", lossy=False, tramos=3, r=4.0),
        _line(3, "nolossR0", lossy=False, tramos=1, r=0.0),
    ]
    out = {ln["name"]: ln for ln in _writer(tmp_path, data).to_json_array()}
    assert out["lossy"]["use_line_losses"] is True
    for name in ("nolossR", "nolossR0"):
        assert out[name]["use_line_losses"] is False
        assert "loss_segments" not in out[name]


def test_global_fperdtram_false_disables_all(tmp_path):
    """FPerdTram = F (plpcnfli.dat header) -> every line lossless."""
    data = [_line(1, "a", lossy=True, tramos=3), _line(2, "b", lossy=True, tramos=2)]
    out = _writer(tmp_path, data, fperdtram=False).to_json_array()
    assert all(ln["use_line_losses"] is False for ln in out)
    assert all("loss_segments" not in ln for ln in out)


def test_parser_without_fperdtram_defaults_on(tmp_path):
    """A parser lacking loss_modeling_enabled keeps PLP's default (on)."""

    class _Bare:  # pylint: disable=too-few-public-methods
        loss_allocation_mode = "receiver"

        def get_all(self):
            return [_line(1, "a", lossy=True, tramos=3)]

    bare: typing.Any = _Bare()
    writer = LineWriter(
        bare,
        options={"output_dir": tmp_path, "model_options": {"line_losses_mode": TSF}},
    )
    out = writer.to_json_array()
    assert out[0]["use_line_losses"] is True
    assert out[0]["loss_segments"] == 6


_CNFLI = """# plpcnfli.dat
# Num.Lineas   Modela Perdidas  Perd.en.ERM   Ang. de Ref.
         2          {fperd}             'M'         1000.d0
# Nombre  F.Max.A-B  F.Max.B-A  Barra A  Barra B  Tension  R  X  Mod.Perd.  Num.Tramos  Operativa
'L1'  150.0  150.0  1  2  220.0  2.000  10.000  T  3  T
'L2'  100.0  100.0  2  3  220.0  1.000  10.000  F  4  T
"""


@pytest.mark.parametrize(("fperd", "lossy_l1"), [("T", True), ("F", False)])
def test_real_parser_round_trip(tmp_path, fperd, lossy_l1):
    """plpcnfli.dat -> LineParser -> LineWriter honours both loss flags."""
    path = tmp_path / "plpcnfli.dat"
    path.write_text(_CNFLI.format(fperd=fperd), encoding="utf-8")
    parser = LineParser(path)
    parser.parse()
    writer = LineWriter(
        parser,
        options={"output_dir": tmp_path, "model_options": {"line_losses_mode": TSF}},
    )
    out = {ln["name"]: ln for ln in writer.to_json_array()}
    assert out["L1"]["use_line_losses"] is lossy_l1
    assert ("loss_segments" in out["L1"]) is lossy_l1
    if lossy_l1:
        assert out["L1"]["loss_segments"] == 6
    assert out["L2"]["use_line_losses"] is False


def test_cli_loss_tangents_reaches_options():
    """--loss-tangents is parsed and threaded into the converter options."""
    parser = make_parser()
    args = parser.parse_args(["/tmp/plp_x", "--loss-tangents", "tramos"])
    assert args.loss_tangents == "tramos"
    assert build_options(args)["loss_tangents"] == "tramos"
    args = parser.parse_args(["/tmp/plp_x"])
    assert args.loss_tangents == "double"
    assert build_options(args)["loss_tangents"] == "double"
    args = parser.parse_args(["/tmp/plp_x", "--loss-tangents", "7"])
    assert build_options(args)["loss_tangents"] == 7
    with pytest.raises(SystemExit):
        parser.parse_args(["/tmp/plp_x", "--loss-tangents", "1"])
