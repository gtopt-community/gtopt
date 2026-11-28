# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""gtopt to OpenDSS export, for AC validation of gtopt dispatches.

Writes a gtopt planning (optionally with its solved dispatch) as a
balanced three-phase OpenDSS script; :mod:`gtopt2opendss.ac` solves it
through the DSS C-API (``opendssdirect.py``, optional) and compares the
AC voltages and losses with gtopt's LinDistFlow solution.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .convert import Dispatch, planning_to_dss, read_dispatch

__all__ = ["Dispatch", "planning_to_dss", "read_dispatch"]
