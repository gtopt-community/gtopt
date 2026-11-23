# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CEN (Chile) operator profile.

Data sources are declared in ``sources.toml`` (see
:mod:`gtopt_operators.registry`); download them with
``gtopt-fetch cen ...``.
"""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path

from gtopt_operators.registry import OperatorInfo, load_registry
from gtopt_shared.operator import BaseOperatorProfile

#: Identity of the operator (id, name, country, time zone).
OPERATOR: OperatorInfo = load_registry("cen").operator

#: PLEXOS database of the CEN daily programme.
_DBSEN_RX = re.compile(r"^DBSEN.*\.xml$", re.IGNORECASE)
#: Bundle names of the Programa de Operación portal.
_BUNDLE_RX = re.compile(r"^(PLEXOS|DATOS)\d{8}(_\d\d)?(\.zip)?$", re.IGNORECASE)


def _plexos_reason(path: Path) -> str | None:
    if _BUNDLE_RX.match(path.name):
        return f"CEN bundle name {path.name}"
    if path.is_dir():
        for child in path.iterdir():
            if _DBSEN_RX.match(child.name) or _BUNDLE_RX.match(child.name):
                return f"{child.name} in the input directory"
        return None
    if path.suffix.lower() == ".zip" and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for member in zf.namelist():
                if _DBSEN_RX.match(Path(member).name):
                    return f"{Path(member).name} inside {path.name}"
    if _DBSEN_RX.match(path.name):
        return f"CEN PLEXOS database {path.name}"
    return None


class CenProfile(BaseOperatorProfile):
    """CEN conventions (detection only so far)."""

    def __init__(self) -> None:
        super().__init__("cen")

    def detect(
        self,
        fmt: str,
        input_path: Path | None,
        args: argparse.Namespace | None,
    ) -> str | None:
        """PLP input is always CEN; PLEXOS when the CEN bundle is seen."""
        del args
        if fmt == "plp":
            return "PLP format (published by the CEN)"
        if fmt == "plexos" and input_path is not None and input_path.exists():
            return _plexos_reason(input_path)
        return None


PROFILE = CenProfile()
