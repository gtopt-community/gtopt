"""Truncated / short-field inputs for the optional hydro & storage parsers.

Every prefix of a valid file and every data line cut down to a single
token must either parse or raise a ``ValueError`` (never another
exception type), which drives the "unexpected end of file" and "too few
fields" guards of each reader.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from plp2gtopt.battery_parser import BatteryParser
from plp2gtopt.cenfi_parser import CenfiParser
from plp2gtopt.cenpmax_parser import CenpmaxParser
from plp2gtopt.cenre_parser import CenreParser
from plp2gtopt.filemb_parser import FilembParser
from plp2gtopt.gnl_parser import GnlParser
from plp2gtopt.manbat_parser import ManbatParser

_CASES = Path(__file__).resolve().parents[2] / "cases"

_CENPMAX = (
    "# n\n2\n'LakeA'\n'LakeA'\n2\n   0.0   0.05   60.0\n 600.0   0.02   90.0\n"
    "'LakeB'\n'LakeB'\n1\n   0.0   0.05   60.0\n"
)
_CENFI_SEG = (
    "# n\n1\n'Reservoir1'\n'Reservoir1'\n2\n 1 0.0 0.001 1.0\n 2 600.0 0.0005 1.3\n"
)
_GNL = (
    "1\n1  GNL_Q  150000  80000  5.0  100.0  2.5  0.01  0.90\n"
    "2\nNEHUENCO_1  0.40\nNEHUENCO_2  0.38\n2\n1  50000\n3  50000\n"
)
_MANBAT = "2\nBESS1\n2\n1  0.0  180.0\n2  10.0  150.0\nBESS2\n0\n"

_SAMPLES: list[tuple[Any, str, str]] = [
    (CenreParser, "plpcenre.dat", (_CASES / "plp_min_cenre/plpcenre.dat").read_text()),
    (
        FilembParser,
        "plpfilemb.dat",
        (_CASES / "plp_min_filemb/plpfilemb.dat").read_text(),
    ),
    (CenfiParser, "plpcenfi.dat", (_CASES / "plp_min_cenre/plpcenfi.dat").read_text()),
    (CenfiParser, "plpcenfi.dat", _CENFI_SEG),
    (CenpmaxParser, "plpcenpmax.dat", _CENPMAX),
    (
        BatteryParser,
        "plpcenbat.dat",
        (_CASES / "plp_min_battery/plpcenbat.dat").read_text(),
    ),
    (GnlParser, "plpcnfgnl.dat", _GNL),
    (ManbatParser, "plpmanbat.dat", _MANBAT),
]


def _variants(text: str) -> list[str]:
    lines = text.splitlines()
    out = ["\n".join(lines[:k]) + "\n" for k in range(len(lines))]
    for i, line in enumerate(lines):
        toks = line.split()
        if not line.lstrip().startswith("#") and len(toks) > 1:
            out.append("\n".join([*lines[:i], toks[0], *lines[i + 1 :]]) + "\n")
    return out


def _parse(cls: Any, path: Path) -> Any:
    parser = cls(path)
    parser.parse()
    return parser


@pytest.mark.parametrize(("cls", "fname", "text"), _SAMPLES)
def test_valid_sample_parses(tmp_path: Path, cls: Any, fname: str, text: str) -> None:
    path = tmp_path / fname
    path.write_text(text, encoding="latin-1")
    _parse(cls, path)


@pytest.mark.parametrize(("cls", "fname", "text"), _SAMPLES)
def test_truncated_and_short_fields(
    tmp_path: Path, cls: Any, fname: str, text: str
) -> None:
    errors = 0
    for i, variant in enumerate(_variants(text)):
        path = tmp_path / f"{i}_{fname}"
        path.write_text(variant, encoding="latin-1")
        try:
            _parse(cls, path)
        except ValueError:
            errors += 1
    assert errors > 0


@pytest.mark.parametrize("cls", [CenreParser, FilembParser, CenfiParser, CenpmaxParser])
def test_negative_count(tmp_path: Path, cls: Any) -> None:
    path = tmp_path / "x.dat"
    path.write_text("# n\n-1\n", encoding="latin-1")
    with pytest.raises(ValueError):
        _parse(cls, path)
