# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for tools/fetch_cen_sscc_cpf.py (synthetic text, no network)."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_cen_sscc_cpf as fc  # pylint: disable=wrong-import-position

# Synthetic layouts mimicking pdftotext -layout output (made-up numbers).
_PLANES = """
Pot. a Descon. menor o igual a 350 [MW]:
             𝜶𝟏                  𝜷𝟏                 𝜸𝟏                𝜹𝟏
           100.000              10.500              2.000             1.250
 2
Ra = 0.99
             α2                  β2                 γ2                δ2

           -5,5                  3,25               0.5               0.9
an unrelated line 1 2 3 4
"""

_TABLE = """
 Tabla 5-3 Reservas requeridas CPF+ para CPF@10s [MW] en función de la
                carga BESS y la Inercia. Potencia desconectada 400 [MW]
    Inercia            Generación Bruta [MW]
    [GVAs]     7.500    8.000    8.500
          20       508         485           464
          25       433         413           396

 Tabla 5-4 Reservas requeridas CPF+ para CPF@10s [MW] en función de la
                carga BESS y la Inercia. Potencia desconectada 350 [MW]
  [GVAs]        7.500        8.000     8.500
     20          433          410       389
Informe page footer 33 de 126
 Tabla 9-9 Reservas requeridas CPF+ para CPF@10s [MW] sin potencia
     20          1          2       3
"""


def test_parse_planes() -> None:
    assert fc.parse_planes(_PLANES) == [
        (100.0, 10.5, 2.0, 1.25),
        (-5.5, 3.25, 0.5, 0.9),
    ]


def test_parse_planes_ignores_text_without_header() -> None:
    assert not fc.parse_planes("1 2 3 4\n5 6 7 8\n")


def test_parse_ecfydr() -> None:
    pts = fc.parse_ecfydr(_TABLE)
    assert len(pts) == 9
    assert pts[0] == (400.0, 20.0, 7500.0, 508.0)
    assert pts[5] == (400.0, 25.0, 8500.0, 396.0)
    assert pts[-1] == (350.0, 20.0, 8500.0, 389.0)


def test_gen_header() -> None:
    assert fc._gen_header("7.500 8.000 12.500") == [7500.0, 8000.0, 12500.0]  # pylint: disable=protected-access
    assert fc._gen_header("20 30 40") is None  # pylint: disable=protected-access
    assert fc._gen_header("7.500") is None  # pylint: disable=protected-access


def _doc(kind: str, expected: int) -> fc.Document:
    return fc.Document("2099", "https://example.invalid/x.pdf", kind, expected)


def test_extract_writes_csv(tmp_path: Path) -> None:
    p = fc.extract(_doc("planes", 2), _PLANES, tmp_path)
    rows = list(csv.reader(p.open()))
    assert rows[0] == ["k", "alpha", "beta", "gamma", "delta"]
    assert rows[2] == ["2", "-5.5", "3.25", "0.5", "0.9"]
    q = fc.extract(_doc("ecfydr", 9), _TABLE, tmp_path)
    assert q.name == "ecfydr_cpf10s_2099.csv"
    assert len(list(csv.reader(q.open()))) == 10


def test_extract_checks_counts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="planes"):
        fc.extract(_doc("planes", 3), _PLANES, tmp_path)
    with pytest.raises(ValueError, match="table points"):
        fc.extract(_doc("ecfydr", 8), _TABLE, tmp_path)


def test_default_out_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert fc.default_out_dir() == tmp_path / "gtopt" / "cen_sscc"


def test_main_offline_missing_pdf(tmp_path: Path) -> None:
    assert fc.main(["--out", str(tmp_path), "--pdf-dir", str(tmp_path)]) == 1


def test_main_offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pdfs = tmp_path / "pdf"
    pdfs.mkdir()
    for doc in fc.DOCUMENTS:
        (pdfs / doc.url.rsplit("/", 1)[-1]).write_bytes(b"%PDF")
    monkeypatch.setattr(
        fc,
        "DOCUMENTS",
        (
            fc.Document("2099", fc.DOCUMENTS[0].url, "planes", 2),
            fc.Document("2099", fc.DOCUMENTS[3].url, "ecfydr", 9),
        ),
    )
    texts = {"planes": _PLANES, "ecfydr": _TABLE}
    calls: list[str] = []

    def fake_text(pdf: Path) -> str:
        calls.append(pdf.name)
        return texts["ecfydr" if "Informe" in pdf.name else "planes"]

    monkeypatch.setattr(fc, "pdf_to_text", fake_text)
    out = tmp_path / "out"
    assert fc.main(["--out", str(out), "--pdf-dir", str(pdfs)]) == 0
    assert len(calls) == 2
    sources = (out / "SOURCES.txt").read_text().splitlines()
    assert sources[0].startswith("cpf10s_planes_2099.csv\thttps://")
    assert "sha256:" in sources[1]
