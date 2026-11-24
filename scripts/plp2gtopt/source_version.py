# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Known PLP input variants ``plp2gtopt`` reads.

PLP ``.dat`` files carry no version stamp and PLP has no linear version
history the parsers could key on.  What the parsers do distinguish are
*variants*: optional files and alternative layouts a case may combine.
Each variant below is grounded in a code path of :mod:`plp2gtopt`
(``plp_parser.PLPParser.parse_all`` and the individual parsers):

``CEN65``
    The core CEN PLP file set (``plpblo``, ``plpeta``, ``plpbar``,
    ``plpcnfli``, ``plpcnfce``, ``plpdem``, ``plpcosce``, ``plpmance``,
    ``plpmanli``, ``plpaflce``, ``plpextrac``, ``plpmanem``), the
    required parsers of ``plp_parser.py``.  Named after the CEN65
    PLP release.
``ESS``
    ESS storage model (Fortran ``genpdess.f``, the "PLP v6.6 Colbun ESS"
    build): ``plpess.dat`` + optional ``plpmaness.dat``.  Takes priority
    over ``BESS`` when both files exist.  Tolerates the optional
    ``[DCMod] [CenCarga]`` columns of ``plpess.dat`` and 5 or 6 fields
    (optional ``DCMod``) in ``plpmaness.dat``.
``BESS``
    Battery model (Fortran ``genpdbaterias.f``): ``plpcenbat.dat`` +
    optional ``plpmanbat.dat``; read only without ``plpess.dat``.
``maule-agreement`` / ``laja-agreement``
    Irrigation agreements ``plpmaulen.dat`` / ``plplajam.dat``.  The Maule
    reader tolerates the older layout without the second embalse flag and
    without the trailing "Penalizadores Convenio" line (defaults
    1500 / 1000).
``cenfi-legacy`` / ``cenfi-segmented``
    The two ``plpcenfi.dat`` seepage layouts: one ``slope constant`` line
    per entry, or a segment count followed by piecewise segments
    (detected per entry by the parser; a file may mix both).
``cuts-plpplaem`` / ``cuts-plpplem``
    The two names of the boundary-cut file pair (``plpplaem1/2.dat`` or
    ``plpplem1/2.dat``).

Not supported: the ``PLPv6.1_baterias`` battery fork, whose batteries come
from ``plpstrcen.dat`` (Fortran ``leebesscen.f``).  No parser reads that
file; it is detected as ``PLPv6.1-strcen`` and reported as an unsupported
variant (its batteries are not converted).

Every supported variant is exercised by the repository: ``CEN65`` by
every ``scripts/cases/plp_*`` case, ``ESS`` by ``plp_min_ess``, ``BESS`` by
``plp_min_battery``, both agreements by ``plp_min_irrigation``,
``cenfi-legacy`` by the ``plp_min_cenre`` fixture, ``cuts-plpplem`` by
``plp_hydro_4b``; ``cenfi-segmented`` and ``cuts-plpplaem`` by parser
fixtures (``tests/test_cenfi_parser.py``, ``tests/test_planos.py``).
"""

from __future__ import annotations

from pathlib import Path

from gtopt_shared.compressed_open import find_compressed_path
from gtopt_shared.source_versions import SourceFormat

from .cenfi_parser import CenfiParser
from .planos_parser import find_planos_files

_VARIANTS: tuple[tuple[str, str], ...] = (
    ("CEN65", "core CEN PLP files: plpblo/plpeta/plpbar/plpcnfce/..."),
    ("ESS", "ESS model: plpess.dat + plpmaness.dat"),
    ("BESS", "battery model: plpcenbat.dat + plpmanbat.dat"),
    ("maule-agreement", "Maule irrigation agreement: plpmaulen.dat"),
    ("laja-agreement", "Laja irrigation agreement: plplajam.dat"),
    ("cenfi-legacy", "plpcenfi.dat slope/constant lines"),
    ("cenfi-segmented", "plpcenfi.dat piecewise segments"),
    ("cuts-plpplaem", "boundary cuts plpplaem1/2.dat"),
    ("cuts-plpplem", "boundary cuts plpplem1/2.dat"),
)

SOURCE_FORMAT = SourceFormat(
    name="PLP",
    supported=tuple(tag for tag, _ in _VARIANTS),
    tested=tuple(tag for tag, _ in _VARIANTS),
    default="CEN65",
    ordered=False,
    variants=True,
    descriptions=_VARIANTS,
)

#: Detected tag of the unsupported PLPv6.1_baterias battery file.
UNSUPPORTED_STRCEN = "PLPv6.1-strcen"


def _has(case_dir: Path, name: str) -> bool:
    return find_compressed_path(case_dir / name) is not None


def _cenfi_layouts(case_dir: Path) -> list[str]:
    path = find_compressed_path(case_dir / "plpcenfi.dat")
    if path is None:
        return []
    parser = CenfiParser(path)
    try:
        parser.parse()
    except (OSError, ValueError):
        return []  # the conversion itself reports the broken file
    layouts = {entry["layout"] for entry in parser.seepages}
    return [f"cenfi-{lay}" for lay in ("legacy", "segmented") if lay in layouts]


def detect_version(input_dir: str | Path | None) -> list[str]:
    """Variant tags of the PLP case in *input_dir* (empty when none apply).

    Mirrors the file selection of ``PLPParser.parse_all``: ``BESS`` is
    only reported when ``plpess.dat`` is absent, because the parser then
    ignores ``plpcenbat.dat``.
    """
    if input_dir is None:
        return []
    case_dir = Path(input_dir)
    if not case_dir.is_dir():
        return []
    tags: list[str] = []
    if _has(case_dir, "plpcnfce.dat"):
        tags.append("CEN65")
    if _has(case_dir, "plpess.dat"):
        tags.append("ESS")
    elif _has(case_dir, "plpcenbat.dat"):
        tags.append("BESS")
    if _has(case_dir, "plpstrcen.dat"):
        tags.append(UNSUPPORTED_STRCEN)
    if _has(case_dir, "plpmaulen.dat"):
        tags.append("maule-agreement")
    if _has(case_dir, "plplajam.dat"):
        tags.append("laja-agreement")
    tags.extend(_cenfi_layouts(case_dir))
    planos = find_planos_files(case_dir)
    if planos is not None:
        prefix = "plpplaem" if planos[0].name.startswith("plpplaem") else "plpplem"
        tags.append(f"cuts-{prefix}")
    return tags
