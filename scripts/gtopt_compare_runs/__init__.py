# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""gtopt_compare_runs: compare two gtopt output directories (run A vs run B).

The results importers (``plp2gtopt``, ``sddp2gtopt``, ``plexos2gtopt``
``--results``) write PLP / PSR SDDP / PLEXOS solutions as gtopt output
directories, so comparing gtopt against those tools is comparing two gtopt
runs.  :func:`compare_runs` aligns two runs by element name and by
scenario / stage / block position and reports, per stream, totals and
differences.
"""

from gtopt_compare_runs.engine import (
    CompareOptions,
    Report,
    StreamResult,
    compare_runs,
)
from gtopt_compare_runs.reader import Run

__all__ = ["CompareOptions", "Report", "Run", "StreamResult", "compare_runs"]
