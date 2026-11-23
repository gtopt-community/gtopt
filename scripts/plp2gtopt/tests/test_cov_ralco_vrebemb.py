"""Tests for :mod:`plp2gtopt.ralco_parser` and :mod:`plp2gtopt.vrebemb_parser`."""

from __future__ import annotations

from pathlib import Path

import pytest

from plp2gtopt.ralco_parser import RalcoParser
from plp2gtopt.vrebemb_parser import VrebembParser


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="latin-1")
    return path


def test_ralco_parses_segments_with_unit_conversion(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "plpralco.dat",
        "# Nombre Lago Ralco\n'RALCO'\n# Numero de Segmentos\n2\n"
        "# Vcota b a\n0.0  100.0  6.9868d-5\n500000.0  50.0  1.0E-4\n",
    )
    parser = RalcoParser(path)
    parser.parse()
    assert parser.num_reservoir_discharge_limits == 1
    entry = parser.reservoir_discharge_limits[0]
    assert entry["reservoir"] == "RALCO"
    seg0, seg1 = entry["segments"]
    assert seg0["slope"] == pytest.approx(6.9868e-5 * 1000.0)
    assert seg0["intercept"] == pytest.approx(100.0)
    assert seg1["volume"] == pytest.approx(500.0)
    found = parser.get_reservoir_discharge_limit_by_reservoir("RALCO")
    assert found is not None and found["name"] == "RALCO"
    assert parser.get_reservoir_discharge_limit_by_reservoir("NOPE") is None


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("# only comments\n", "empty or malformed"),
        ("'RALCO'\n", "num_segments"),
        ("'RALCO'\n2\n0.0 1.0 2.0\n", "segment data"),
        ("'RALCO'\n1\n0.0 1.0\n", "too few fields"),
    ],
)
def test_ralco_malformed(tmp_path: Path, text: str, match: str) -> None:
    parser = RalcoParser(_write(tmp_path / "plpralco.dat", text))
    with pytest.raises(ValueError, match=match):
        parser.parse()


def test_vrebemb_parses_costs(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "plpvrebemb.dat",
        "# Numero Embalses\n2\n# Nombre\n'COLBUN'\n# Vol\n1553245\n# Costo\n0.1\n"
        "'RAPEL'\n1000\n2.5\n",
    )
    parser = VrebembParser(path)
    parser.parse()
    assert parser.num_vrebs == 2
    assert [v["name"] for v in parser.vrebs] == ["COLBUN", "RAPEL"]
    assert parser.get_cost("RAPEL") == pytest.approx(2.5)
    assert parser.get_cost("NOPE") is None


def test_vrebemb_empty(tmp_path: Path) -> None:
    parser = VrebembParser(_write(tmp_path / "plpvrebemb.dat", "# nothing\n"))
    with pytest.raises(ValueError, match="empty or malformed"):
        parser.parse()
