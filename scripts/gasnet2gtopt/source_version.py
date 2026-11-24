# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Versions of the ``*.gasnet.json`` description schema.

The gasnet description is gtopt's own format.  Its single schema so far
(``"1"``: top-level ``name`` / ``profiles`` / ``gas_network`` /
``power_system`` / optional ``gas_unit``) is what every case under
``gasnet2gtopt/cases`` (and ``cases/literature``) uses.  A description
may state it in an optional top-level ``"schema_version"`` field; the
bundled cases predate that field, so they resolve to the default.
"""

from __future__ import annotations

from typing import Any

from gtopt_shared.source_versions import SourceFormat

SOURCE_FORMAT = SourceFormat(
    name="gasnet JSON schema",
    supported=("1",),
    tested=("1",),
    default="1",
)

#: Optional top-level key of a ``*.gasnet.json`` naming its schema version.
SCHEMA_VERSION_KEY = "schema_version"


def detect_version(desc: Any) -> str | None:
    """The description's ``schema_version`` as a string, else ``None``."""
    if not isinstance(desc, dict):
        return None
    value = desc.get(SCHEMA_VERSION_KEY)
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None
