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

from gtopt_shared.resources import package_data_file

#: Path components of the CEN-Chile emissions overlay relative to the
#: ``gtopt_shared`` package directory.
CEN_CHILE_EMISSIONS_PARTS: tuple[str, ...] = ("data", "emissions", "cen_chile.json")


def resolve_default_emissions_file() -> Path:
    """Return the path to the bundled CEN-Chile emissions overlay.

    Used by :func:`plexos2gtopt.plexos2gtopt.plexos2gtopt` when the
    operator did NOT pass an explicit ``--emissions-file``: plexos2gtopt
    is CEN-Chile-shaped (PLEXOS DBs in this codebase come from CEN's
    PCP archive), so the CEN-Chile defaults file is the right default.
    It is a superset of the bare IPCC-2006 defaults
    (``gtopt_shared/data/ipcc_emission_factors.json``).

    Raises:
        gtopt_shared.resources.MissingDataFileError: when the bundled file
            is missing (broken install).  Pass ``--emissions-file`` to use
            another table explicitly.
    """
    return package_data_file(
        "gtopt_shared",
        *CEN_CHILE_EMISSIONS_PARTS,
        purpose="CEN-Chile emissions overlay",
    )
