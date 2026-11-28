# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""OpenDSS ``.dss`` to gtopt conversion tool.

Parses an OpenDSS script (own parser, no engine needed), reduces the
phase-coordinate circuit to a balanced single-phase equivalent and
writes a gtopt planning JSON -- a DC OPF case by default, or a
LinDistFlow distribution case (``--network-model lindistflow``) with
voltage limits, reactive power, transformer taps and capacitors.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .convert import build_planning
from .dss_parser import DssCircuit, parse_dss
from .network import Network, build_network

__all__ = [
    "DssCircuit",
    "Network",
    "build_network",
    "build_planning",
    "parse_dss",
]
