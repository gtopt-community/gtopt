# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""plexos2gtopt converter defaults — bundled reference-file resolution.

Helpers that locate bundled CEN-Chile-specific reference files.  They
ship as package data of :mod:`gtopt_shared` (``gtopt_shared/data/...``,
see ``[tool.setuptools.package-data]`` in ``scripts/pyproject.toml``) so
they are present for source-tree, editable and wheel installs alike.
Kept separate from :mod:`plexos2gtopt.parsers` so the resolution logic
has one home.
"""

from __future__ import annotations

from pathlib import Path

from gtopt_shared.country import emissions_overlay_path
from gtopt_shared.resources import MissingDataFileError

#: Country whose default emissions overlay plexos2gtopt applies when no
#: ``--emissions-file`` is given (``gtopt_shared/data/countries/cl.toml``
#: names the overlay file).
DEFAULT_EMISSIONS_COUNTRY = "cl"


def resolve_default_emissions_file() -> Path:
    """Return the path to the bundled CEN-Chile emissions overlay.

    Used by :func:`plexos2gtopt.plexos2gtopt.plexos2gtopt` when the
    operator did NOT pass an explicit ``--emissions-file``: plexos2gtopt
    is CEN-Chile-shaped (PLEXOS DBs in this codebase come from CEN's
    PCP archive), so the Chilean overlay named in the ``cl`` country
    file (``cen_chile.json``) is the right default.  It is a superset of
    the bare IPCC-2006 defaults
    (``gtopt_shared/data/ipcc_emission_factors.json``).

    Raises:
        gtopt_shared.resources.MissingDataFileError: when the bundled file
            is missing (broken install).  Pass ``--emissions-file`` to use
            another table explicitly.
    """
    path = emissions_overlay_path(DEFAULT_EMISSIONS_COUNTRY)
    if path is None:  # pragma: no cover - cl.toml always names one
        raise MissingDataFileError("country 'cl' names no emissions overlay")
    return path
