# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""AMM (Guatemala) operator profile.

Data sources are declared in ``sources.toml`` (see
:mod:`gtopt_operators.registry`); download them with
``gtopt-fetch amm ...``.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from gtopt_operators.registry import OperatorInfo, load_registry
from gtopt_shared.operator import BaseOperatorProfile

#: Identity of the operator (id, name, country, time zone).
OPERATOR: OperatorInfo = load_registry("amm").operator

#: AMM extra files next to an SDDP/NCP case.
_AMM_FILES = ("restmex.csv", "preciosmex.csv")
#: PSR files of the AMM deployment carry a ``gu`` system suffix
#: (``cgndgu.dat``, ``cprmxhgu_u.dat``, ...).
_GU_RX = re.compile(r"^c\w*gu(_\w+)?\.(dat|csv)$", re.IGNORECASE)


class AmmProfile(BaseOperatorProfile):
    """AMM conventions (detection only so far)."""

    def __init__(self) -> None:
        super().__init__("amm")

    def detect(
        self,
        fmt: str,
        input_path: Path | None,
        args: argparse.Namespace | None,
    ) -> str | None:
        """SDDP case with AMM files; PSS/E with AMM side sheets."""
        if fmt == "psse" and args is not None:
            for flag in ("nomenclatura", "ldm"):
                if getattr(args, flag, None):
                    return f"--{flag} given"
            return None
        if fmt != "sddp" or input_path is None or not input_path.is_dir():
            return None
        names = sorted(p.name for p in input_path.iterdir())
        for name in names:
            if name.lower() in _AMM_FILES:
                return f"{name} in the case"
        for name in names:
            if _GU_RX.match(name):
                return f"AMM system suffix 'gu' ({name})"
        return None


PROFILE = AmmProfile()
