# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""tcheby SQLite test systems to gtopt JSON.

Converts the small validation systems of the Chebyshev scheduling research
code (Chebyshev short-term hydrothermal scheduling, ``tests/test_dbs``)
into gtopt plannings in two time representations: uniform hourly blocks
and Chebyshev–Lobatto point-sampled stages (``Stage.sampling =
"point"``).  Also builds the hand-checkable mini cases of
``cases/cheby_small/mini``.  See :mod:`tcheby2gtopt.convert` for the
element mapping.
"""

from __future__ import annotations

__version__ = "0.1.0"
