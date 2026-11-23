# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""gtopt_check_cases — check a gtopt case collection against its catalog.

Builds every case of a collection (a directory with ``catalog.json``,
e.g. a ``gtopt-cases`` checkout) with ``gtopt --lp-only`` and compares
the LP size with the catalog; optionally solves the cases with all
output kept outside the collection.
"""

from .main import main  # noqa: F401
