# SPDX-License-Identifier: BSD-3-Clause
"""CEN-Chile cogeneration reference list (single loader for all tools).

The bundled CSV ``gtopt_shared/data/cogen/cen_chile_cogen.csv`` carries
explicit cogen identifications synthesised from three sources:

1. CEN-SIP ``unidades_generadoras`` (``nombre_tecnologia ==
   'Cogeneración - *'``) — the formal CEN registry.
2. ``gtopt_shared/data/emissions/cen_chile.json`` ``generator_overrides``
   whose ``type`` starts with ``thermal:cogen``.
3. Industrial-cogen central-name PREFIXES (pulp-mill black-liquor cogen:
   CMPC, CELCO, ARAUCO, …; sulfur cogen: PAS_*; etc.), rows whose
   ``source`` column starts with ``pattern_prefix:``.

Used by plp2gtopt / plexos2gtopt (cogen ``type`` tagging) and
gtopt_marginal_units (MOER backfill exclusion).  The file is required: a
missing file raises :class:`gtopt_shared.resources.MissingDataFileError`
instead of silently classifying every unit as non-cogen.
"""

from __future__ import annotations

import csv
import functools
import logging
from pathlib import Path

from gtopt_shared.resources import package_data_file

logger = logging.getLogger(__name__)

#: Path components of the bundled CSV relative to the ``gtopt_shared``
#: package directory.
CEN_COGEN_CSV_PARTS: tuple[str, ...] = ("data", "cogen", "cen_chile_cogen.csv")


def cen_cogen_csv_path() -> Path:
    """Return the bundled CEN cogen CSV path (raises if missing)."""
    return package_data_file(
        "gtopt_shared", *CEN_COGEN_CSV_PARTS, purpose="CEN cogen reference list"
    )


def read_cen_cogen_csv(path: Path) -> tuple[frozenset[str], tuple[str, ...]]:
    """Parse a cogen reference CSV into ``(exact_names, prefixes)``."""
    names: set[str] = set()
    prefixes: list[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = str(row.get("name_upper", "")).strip().upper()
            if not name:
                continue
            source = str(row.get("source", "")).lower()
            if source.startswith("pattern_prefix:"):
                prefixes.append(name)
            else:
                names.add(name)
    if not names and not prefixes:
        raise ValueError(f"CEN cogen reference list {path} has no entries")
    return frozenset(names), tuple(prefixes)


@functools.cache
def load_cen_cogen_reference() -> tuple[frozenset[str], tuple[str, ...]]:
    """Load (and cache) the bundled ``(exact_names, prefixes)`` pair."""
    path = cen_cogen_csv_path()
    names, prefixes = read_cen_cogen_csv(path)
    logger.debug(
        "CEN cogen reference loaded from %s (%d exact names, %d prefixes)",
        path,
        len(names),
        len(prefixes),
    )
    return names, prefixes


def is_cen_cogen(gen_name: str) -> bool:
    """True iff ``gen_name`` matches the CEN cogen list (exact or prefix)."""
    name = str(gen_name).strip().upper()
    if not name:
        return False
    names, prefixes = load_cen_cogen_reference()
    if name in names:
        return True
    return any(name.startswith(p) for p in prefixes)
