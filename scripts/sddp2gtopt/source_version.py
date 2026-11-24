# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""PSR SDDP source-format versions understood by ``sddp2gtopt``.

The ``.dat`` parsers distinguish two generations of the PSR file layouts:

* **16** — the fixed-width layouts of SDDP releases before 17: ``dcirc``
  with or without a ``$version=`` header (columns then located from the
  ``#BOR. #BDE. .RESIS.REACT`` header), fixed-width ``ccombu`` /
  ``dbus`` / ``mcirc`` records, per-file ``$version=N`` headers.
* **17** — SDDP >= 17 additionally writes comma-separated layouts whose
  first lines carry a ``!Code,`` column header (``dcirc`` / ``mcirc`` /
  ``ccombu`` / ``gndsite``), per-table ``<units> <info>`` overrides in
  the monthly tables, and binary typical-year ``blrenw`` profiles.

Only the 17 marker is detectable: a case with at least one
``!Code,``-headed ``.dat`` is SDDP >= 17.  A case without it may be either
generation (the 17 reader accepts the fixed-width files too), so
detection returns ``None`` and the default (16) applies.  The
``psrclasses.json`` front-end carries no SDDP release either.

Both generations are exercised by ``tests/data``: ``dat_case`` is
fixed-width only (16); ``sddp_block_case`` mixes
fixed-width files with a ``!Code,`` ``gndsitett.dat`` (17).
"""

from __future__ import annotations

import lzma
from pathlib import Path

from gtopt_shared.compressed_open import compressed_open
from gtopt_shared.source_versions import SourceFormat

SOURCE_FORMAT = SourceFormat(
    name="PSR SDDP",
    supported=("16", "17"),
    tested=("16", "17"),
    default="16",
)

#: Lines at the top of a ``.dat`` scanned for the ``!Code,`` CSV header
#: (the parsers look at the same window).
_HEADER_LINES = 3


def _has_csv_header(path: Path) -> bool:
    try:
        with compressed_open(path, encoding="latin-1", errors="replace") as fh:
            for _ in range(_HEADER_LINES):
                line = fh.readline()
                if not line:
                    return False
                if line.startswith("!Code,"):
                    return True
    except (OSError, EOFError, lzma.LZMAError):  # unreadable / corrupt file
        return False
    return False


def detect_version(case_dir: str | Path) -> str | None:
    """``"17"`` when a ``.dat`` of *case_dir* uses an SDDP >= 17 CSV layout.

    Returns ``None`` otherwise (fixed-width-only case, ``psrclasses.json``
    case, or not a directory): the fixed-width layouts do not identify
    the release that wrote them.
    """
    case_dir = Path(case_dir)
    if not case_dir.is_dir():
        return None
    for path in sorted(case_dir.iterdir()):
        if path.is_file() and ".dat" in path.name.lower() and _has_csv_header(path):
            return "17"
    return None
