"""Tests for the CEN ``mantlin.csv`` line-plan file (leemanli.f NArcManLin2)."""

import lzma

import numpy as np
import pandas as pd

from ..block_parser import BlockParser
from ..line_parser import LineParser
from ..manli_parser import ManliParser
from ..manli_writer import ManliWriter

_EMPTY_MANLI = (
    "# Archivo de mantenimientos de lineas (plpmanli.dat)\n"
    "# numero de lineas con matenimientos\n"
    " 0 \n"
)

_HEADER = "#NomLin,EtaIni,EtaFin,ManALin,ManBLin,VNomLin,ResLin,XImpLin,FOpeLin\n"


def _write_case(tmp_path, manli_text, mantlin_text, compress=False):
    manli_f = tmp_path / "plpmanli.dat"
    manli_f.write_text(manli_text)
    if compress:
        with lzma.open(tmp_path / "mantlin.csv.xz", "wt") as fh:
            fh.write(mantlin_text)
    else:
        (tmp_path / "mantlin.csv").write_text(mantlin_text)
    parser = ManliParser(manli_f)
    parser.parse()
    return parser


def _line_parser(tmp_path, specs):
    """LineParser stub: specs = [(name, number, r, x, voltage, tmax)]."""
    parser = LineParser.__new__(LineParser)
    parser.file_path = tmp_path / "plpcnfli.dat"
    parser._data = [
        {
            "name": n,
            "number": num,
            "r": r,
            "x": x,
            "voltage": v,
            "tmax_ab": t,
            "tmax_ba": t,
            "operational": 1,
        }
        for n, num, r, x, v, t in specs
    ]
    parser._name_index_map = {s[0]: i for i, s in enumerate(specs)}
    parser._number_index_map = {s[1]: i for i, s in enumerate(specs)}
    return parser


def _block_parser(tmp_path, n_stages, blocks_per_stage):
    parser = BlockParser.__new__(BlockParser)
    parser.file_path = tmp_path / "plpblo.dat"
    n = n_stages * blocks_per_stage
    parser._data = [
        {
            "number": i + 1,
            "stage": i // blocks_per_stage + 1,
            "duration": 1.0,
            "accumulated_time": float(i + 1),
        }
        for i in range(n)
    ]
    parser._name_index_map = {}
    parser._number_index_map = {i + 1: i for i in range(n)}
    parser.stage_number_map = {i + 1: i // blocks_per_stage + 1 for i in range(n)}
    return parser


def test_mantlin_ranges_expand_per_block(tmp_path):
    """Block ranges expand to per-block arrays; later rows win."""
    parser = _write_case(
        tmp_path,
        _EMPTY_MANLI,
        _HEADER + "L1,1,4,0,0,220,1.0,10.0,FALSE\n"
        "L1,3,4,800,700,220,1.0,10.0,TRUE\n"
        "L1,4,4,1600,1600,220,0.5,5.0,TRUE\n",
    )
    m = parser.get_manli_by_name("L1")
    assert m is not None
    np.testing.assert_array_equal(m["block"], [1, 2, 3, 4])
    np.testing.assert_allclose(m["tmax_ab"], [0, 0, 800, 1600])
    np.testing.assert_allclose(m["tmax_ba"], [0, 0, 700, 1600])
    np.testing.assert_array_equal(m["operational"], [0, 0, 1, 1])
    np.testing.assert_allclose(m["x"], [10, 10, 10, 5])
    np.testing.assert_allclose(m["r"], [1, 1, 1, 0.5])


def test_mantlin_overrides_plpmanli_and_reads_xz(tmp_path):
    """mantlin.csv(.xz) is read after plpmanli.dat and overrides it."""
    manli = " 1\n'L1'\n   2\n   001  50.0  50.0  T\n   005  60.0  60.0  T\n"
    parser = _write_case(
        tmp_path,
        manli,
        _HEADER + "L1,1,2,0,0,220,1,10,FALSE\nL2,2,2,10,10,110,2,20,TRUE\n",
        compress=True,
    )
    assert parser.num_manlis == 2
    m1 = parser.get_manli_by_name("L1")
    assert m1 is not None
    np.testing.assert_array_equal(m1["block"], [1, 2, 5])
    np.testing.assert_allclose(m1["tmax_ab"], [0, 0, 60])
    np.testing.assert_array_equal(m1["operational"], [0, 0, 1])
    # plpmanli-only block has no electrical override
    assert np.isnan(m1["x"][2])
    m2 = parser.get_manli_by_name("L2")
    assert m2 is not None
    np.testing.assert_array_equal(m2["block"], [2])


def test_mantlin_absent_keeps_plpmanli(tmp_path):
    """Without mantlin.csv the parser behaves as before."""
    manli_f = tmp_path / "plpmanli.dat"
    manli_f.write_text(" 1\n'L1'\n   1\n   001  50.0  50.0  T\n")
    parser = ManliParser(manli_f)
    parser.parse()
    m = parser.get_manli_by_name("L1")
    assert m is not None
    assert "x" not in m


def test_writer_emits_stage_reactance_only_when_changed(tmp_path):
    """Per-stage reactance/resistance files only for lines that change."""
    parser = _write_case(
        tmp_path,
        _EMPTY_MANLI,
        _HEADER + "L1,1,2,100,100,220,1.0,10.0,TRUE\n"
        "L1,3,4,100,100,220,0.5,5.0,TRUE\n"
        "L2,1,4,0,0,220,2.0,20.0,FALSE\n",
    )
    lines = _line_parser(
        tmp_path,
        [("L1", 1, 1.0, 10.0, 220.0, 100.0), ("L2", 2, 2.0, 20.0, 220.0, 50.0)],
    )
    blocks = _block_parser(tmp_path, n_stages=2, blocks_per_stage=2)
    out = tmp_path / "Line"
    cols = ManliWriter(parser, lines, blocks).to_parquet(out)

    assert cols["reactance"] == ["uid:1"]
    assert cols["resistance"] == ["uid:1"]
    assert not cols["voltage"]
    assert not (out / "voltage.parquet").exists()
    x = pd.read_parquet(out / "reactance.parquet")
    x1 = x[x["uid"] == 1].sort_values("stage")
    assert list(x1["stage"]) == [1, 2]
    np.testing.assert_allclose(x1["value"], [10.0, 5.0])
    # L2 is out of service on every block through mantlin.csv
    act = pd.read_parquet(out / "active.parquet")
    assert set(act[act["uid"] == 2]["value"]) == {0}
