# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""pandapower versions whose networks ``pp2gtopt`` converts.

Every ``pandapowerNet`` carries the pandapower version that created it
(``net.version``: the writer of a ``.json`` / ``.xlsx`` file, the data
version of a bundled ``pandapower.networks`` case, or the running
library for a MATPOWER import), so the version is always detectable once
the network is loaded.  Versions are declared per minor series
(``"3.4"`` covers ``3.4.0``).

``supported`` follows the ``pandapower>=2.13.0`` requirement in
``scripts/pyproject.toml`` up to the newest series released when this
table was written; ``tested`` lists the series the test suite exercises:
the bundled ``pandapower.networks`` cases (``case14``, ``case_ieee30``,
... carry ``3.4.0``) and the networks the tests build with the installed
library (3.5).  2.13 - 3.3 are accepted by the requirement but not run.
"""

from __future__ import annotations

from typing import Any

from gtopt_shared.source_versions import SourceFormat

SOURCE_FORMAT = SourceFormat(
    name="pandapower",
    supported=("2.13", "2.14", "3.0", "3.1", "3.2", "3.3", "3.4", "3.5"),
    tested=("3.4", "3.5"),
    default="3.5",
)


def detect_version(net: Any) -> str | None:
    """``net.version`` as a string, or ``None`` when the net has none."""
    version = getattr(net, "version", None)
    if version is None:
        try:
            version = net["version"]
        except (KeyError, TypeError):
            return None
    text = str(version).strip()
    return text or None
