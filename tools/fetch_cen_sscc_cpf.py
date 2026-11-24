#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Fetch CEN SSCC documents and extract the CPF@10s reference data as CSV.

The Coordinador Eléctrico Nacional (CEN, Chile) publishes the CPF@10s
reserve requirement in two forms, both used by the gtopt reserve tests:

* the max-of-planes approximation ``REQ >= alpha - beta*Gen[GW] -
  gamma*I[GVAs] + delta*PotDesc[MW]`` (relation B.2) of the yearly
  "Minuta Modelación y Asignación de SSCC de Control de Frecuencia";
* the reference tables "Reservas requeridas CPF+ para CPF@10s" (Tablas
  5-3..5-5) of the "Informe de Servicios Complementarios".

Operator data is never committed to the repository: this script downloads
the public PDFs (URLs below), converts them with ``pdftotext -layout``
(poppler-utils) and writes the extracted values to a local cache::

    cpf10s_planes_<year>.csv   k, alpha, beta, gamma, delta
    ecfydr_cpf10s_2026.csv     pot_desc_mw, inertia_gvas, gen_mw, req_mw
    SOURCES.txt                URL and sha256 of every document used

The doctests look for these files in ``$GTOPT_CEN_SSCC_DIR``, then
``$GTOPT_CASES_DIR/chile/reference/cen_sscc``, then
``~/.cache/gtopt/cen_sscc`` and skip with a message when absent.

Usage::

    python tools/fetch_cen_sscc_cpf.py                 # default cache dir
    python tools/fetch_cen_sscc_cpf.py --out DIR
    python tools/fetch_cen_sscc_cpf.py --pdf-dir DIR   # already downloaded
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_UPLOADS = "https://www.coordinador.cl/wp-content/uploads"


@dataclass(frozen=True)
class Document:
    """One public CEN document and what to extract from it."""

    key: str
    url: str
    kind: str  # "planes" or "ecfydr"
    expected: int  # planes per set, or table rows (points)


DOCUMENTS: tuple[Document, ...] = (
    Document(
        "2024",
        f"{_UPLOADS}/2024/06/Minuta_GM-DPRO_No07-2024_Modelacion_y_"
        "Asignacion_de_SSCC_CF-ISSCC2024.pdf",
        "planes",
        8,
    ),
    Document(
        "2025",
        f"{_UPLOADS}/2025/02/20250416_Minuta_GM-DPRO_No02-2025_Modelacion_y_"
        "Asignacion_de_SSCC_CF-ISSCC2025.pdf",
        "planes",
        12,
    ),
    Document(
        "2026",
        f"{_UPLOADS}/2026/06/Minuta_GM-DPRO_No05-2026_Modelacion_y_"
        "Asignacion_de_SSCC_CF-ISSCC2026.pdf",
        "planes",
        12,
    ),
    Document(
        "2026",
        f"{_UPLOADS}/2025/12/2025.12.23-Informe_SSCC_2026-Definitivo.pdf",
        "ecfydr",
        297,
    ),
)

_NUM = r"(-?\d+(?:[.,]\d+)?)"
_NUM4 = re.compile(rf"^\s*{_NUM}\s+{_NUM}\s+{_NUM}\s+{_NUM}\s*$")
_ALPHA = ("𝜶", "α")
_DELTA = ("𝜹", "δ")
_CAPTION = re.compile(r"Tabla\s+\d+-\d+\s+Reservas requeridas CPF\+ para CPF@10s")
_POTDESC = re.compile(r"Potencia desconectada\s+(\d+)\s*\[MW\]")


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def parse_planes(text: str) -> list[tuple[float, float, float, float]]:
    """Return the (alpha, beta, gamma, delta) rows of relation (B.2).

    Each plane is printed as a header line holding the alpha..delta symbols
    followed (within two lines) by a line of exactly four numbers.
    """
    lines = text.splitlines()
    planes: list[tuple[float, float, float, float]] = []
    for i, line in enumerate(lines):
        if not (any(a in line for a in _ALPHA) and any(d in line for d in _DELTA)):
            continue
        for nxt in lines[i + 1 : i + 3]:
            if m := _NUM4.match(nxt):
                a, b, g, d = (_num(x) for x in m.groups())
                planes.append((a, b, g, d))
                break
    return planes


def _gen_header(line: str) -> list[float] | None:
    """Generation columns of a table header ("7.500 8.000 ..." in GW)."""
    cells = line.split()
    if len(cells) < 3 or not all(re.fullmatch(r"\d+\.\d{3}", c) for c in cells):
        return None
    # The informe prints MW with '.' as the thousands separator.
    return [float(c.replace(".", "")) for c in cells]


def parse_ecfydr(text: str) -> list[tuple[float, float, float, float]]:
    """Return (pot_desc_mw, inertia_gvas, gen_mw, req_mw) of the CPF@10s tables."""
    lines = text.splitlines()
    out: list[tuple[float, float, float, float]] = []
    i = 0
    while i < len(lines):
        if not _CAPTION.search(lines[i]):
            i += 1
            continue
        pot = None
        gens: list[float] | None = None
        j = i
        while j < min(i + 8, len(lines)) and (pot is None or gens is None):
            if pot is None and (m := _POTDESC.search(lines[j])):
                pot = float(m.group(1))
            if gens is None:
                gens = _gen_header(lines[j].replace("[GVAs]", ""))
            j += 1
        if pot is None or gens is None:
            i += 1
            continue
        while j < len(lines):
            cells = lines[j].split()
            if not cells:
                if out and out[-1][0] == pot:
                    break
                j += 1
                continue
            if not all(re.fullmatch(r"\d+", c) for c in cells):
                break
            if len(cells) != len(gens) + 1:
                break
            inertia = float(cells[0])
            for gen, req in zip(gens, cells[1:], strict=True):
                out.append((pot, inertia, gen, float(req)))
            j += 1
        i = j
    return out


def pdf_to_text(pdf: Path) -> str:
    """Convert @p pdf with poppler's pdftotext (layout mode)."""
    exe = shutil.which("pdftotext")
    if exe is None:
        raise RuntimeError("pdftotext not found: install poppler-utils")
    res = subprocess.run(
        [exe, "-layout", str(pdf), "-"],
        check=True,
        capture_output=True,
    )
    return res.stdout.decode("utf-8", errors="replace")


def download(url: str, dest: Path) -> Path:
    """Download @p url into @p dest (kept if already present)."""
    path = dest / url.rsplit("/", 1)[-1]
    if not path.exists():
        dest.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "gtopt-fetch"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            path.write_bytes(resp.read())
    return path


def default_out_dir() -> Path:
    """The cache directory the doctests search last."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "gtopt" / "cen_sscc"


def _write_csv(path: Path, header: list[str], rows: list[tuple]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def extract(doc: Document, text: str, out: Path) -> Path:
    """Extract @p doc from its @p text into a CSV under @p out."""
    if doc.kind == "planes":
        planes = parse_planes(text)
        if len(planes) != doc.expected:
            raise ValueError(
                f"{doc.url}: found {len(planes)} planes, expected {doc.expected}"
            )
        path = out / f"cpf10s_planes_{doc.key}.csv"
        rows = [(k + 1, *p) for k, p in enumerate(planes)]
        _write_csv(path, ["k", "alpha", "beta", "gamma", "delta"], rows)
        return path
    points = parse_ecfydr(text)
    if len(points) != doc.expected:
        raise ValueError(
            f"{doc.url}: found {len(points)} table points, expected {doc.expected}"
        )
    path = out / f"ecfydr_cpf10s_{doc.key}.csv"
    _write_csv(path, ["pot_desc_mw", "inertia_gvas", "gen_mw", "req_mw"], points)
    return path


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", type=Path, default=None, help="output directory")
    ap.add_argument(
        "--pdf-dir",
        type=Path,
        default=None,
        help="directory with the PDFs already downloaded (no network)",
    )
    args = ap.parse_args(argv)
    out: Path = args.out or default_out_dir()
    out.mkdir(parents=True, exist_ok=True)
    pdf_dir: Path = args.pdf_dir or (out / "pdf")

    sources = []
    for doc in DOCUMENTS:
        name = doc.url.rsplit("/", 1)[-1]
        pdf = pdf_dir / name
        if not pdf.exists():
            if args.pdf_dir is not None:
                print(f"missing {pdf}", file=sys.stderr)
                return 1
            pdf = download(doc.url, pdf_dir)
        csv_path = extract(doc, pdf_to_text(pdf), out)
        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        sources.append(f"{csv_path.name}\t{doc.url}\tsha256:{digest}")
        print(f"wrote {csv_path}")
    (out / "SOURCES.txt").write_text("\n".join(sources) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
