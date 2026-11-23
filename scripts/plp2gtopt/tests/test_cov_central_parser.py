"""Edge-case tests for :mod:`plp2gtopt.central_parser`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from plp2gtopt.central_parser import CentralParser

_HEADER = (
    "# counts\n     3            0           0         2          0\n"
    "# Interm\n  F      F        F            F          00\n"
)


def _central(num: int, name: str, gcost: float, bus: Any) -> str:
    return (
        f"    {num} '{name}'   1    F       F       F       F           F   0   0\n"
        "          PotMin PotMax VertMin VertMax\n"
        "           000.0  100.0\n"
        "          CosVar  Rendi  Barra Genera Vertim    t ini afluen\n"
        f"           {gcost}    1.0  {bus}     0      0      0.0    0.0\n"
    )


def _parser(tmp_path: Path, body: str) -> CentralParser:
    path = tmp_path / "plpcnfce.dat"
    path.write_text(_HEADER + body, encoding="latin-1")
    parser = CentralParser(path)
    parser.parse()
    return parser


def test_falla_costs(tmp_path: Path) -> None:
    parser = _parser(
        tmp_path,
        _central(1, "TERM", 10.0, 1)
        + _central(2, "FALLA_1", 500.0, 1)
        + _central(3, "FALLA_2", 300.0, 0),
    )
    assert [c["type"] for c in parser.centrals] == ["termica", "falla", "falla"]
    assert parser.max_falla_cost() == pytest.approx(500.0)
    # Only bus > 0 rungs count for the average.
    assert parser.avg_falla_cost() == pytest.approx(500.0)
    assert parser.get_central_type(99) == "unknown"


def test_falla_costs_absent_or_bad(tmp_path: Path) -> None:
    parser = _parser(tmp_path, _central(1, "TERM", 10.0, 1))
    assert parser.max_falla_cost() == 0.0
    assert parser.avg_falla_cost() == 0.0
    parser.centrals.append({"type": "falla", "gcost": "x", "bus": "y"})
    assert parser.max_falla_cost() == 0.0
    assert parser.avg_falla_cost() == 0.0


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("    1\n", "at least 2 parts"),
        ("    1 'A'\n          PotMin PotMax\n", "Unexpected end of file"),
        ("    1 'A'\n          PotMin PotMax\n  5\n", "at least 2 values"),
        ("    1 'A'\n          CosVar\n\n", "after CosVar"),
        ("    1 'A'\n          CosVar\n  1 2 3\n", "at least 7 values"),
        ("    1 'A'\n          CosVar\n  1 2 x 4 5 6 7\n", "Invalid central data"),
    ],
)
def test_malformed_sections(tmp_path: Path, body: str, match: str) -> None:
    path = tmp_path / "plpcnfce.dat"
    path.write_text(_HEADER + body, encoding="latin-1")
    with pytest.raises(ValueError, match=match):
        CentralParser(path).parse()


def test_header_without_counts(tmp_path: Path) -> None:
    path = tmp_path / "plpcnfce.dat"
    path.write_text("1 x\n" + _central(1, "TERM", 10.0, 1), encoding="latin-1")
    parser = CentralParser(path)
    parser.parse()
    assert parser.centrals[0]["name"] == "TERM"
