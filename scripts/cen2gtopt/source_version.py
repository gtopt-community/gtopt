# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CEN SIP API generation ``cen2gtopt`` is written against.

The CEN public data API (SIP, ``sipub.api.coordinador.cl``) versions each
endpoint in its path (``/costo-marginal-real/v4/findByDate``).  The
response bodies carry no API version (the ``version`` column of the
marginal-cost rows is the data status, e.g. ``REAL-DEF``), so the
version of live data is the one the client requests.

``"4"`` names the current endpoint table — the paths of
:data:`cen2gtopt._cached_extractor.ENDPOINTS` and
:mod:`cen2gtopt._sip_client`: ``v4`` for most endpoints, ``v3`` where the
SIP still serves only that (``generacion-real``, ``embalse-real``,
``costo-combustible``).  It is exercised by the recorded SIP responses in
``tests/data/sip_fixtures``.

Detection reads an on-disk fetch cache (``--cache``; layout
``<root>/sip/<endpoint>__v<N>__<method>/``, see
:func:`cen2gtopt._cached_extractor.cache_path`): ``"4"`` when every cached
SIP endpoint uses the version of the current table; the deviating major
version when some cached endpoint was fetched at another version (an
older table, or a newer API); ``None`` when no known endpoint is cached.
"""

from __future__ import annotations

import re
from pathlib import Path

from gtopt_shared.source_versions import SourceFormat

from ._cached_extractor import ENDPOINTS

SOURCE_FORMAT = SourceFormat(
    name="CEN SIP API",
    supported=("4",),
    tested=("4",),
    default="4",
)

#: Version label of the current endpoint table.
CURRENT_TABLE = "4"

_DIR_RE = re.compile(r"^(?P<base>.+?)__v(?P<major>\d+)__(?P<method>.+)$")


def _safe_segment(text: str) -> str:
    # Mirrors cen2gtopt._cached_extractor._safe_segment (cache dir names).
    return "".join(c if c.isalnum() else "_" for c in text).strip("_")


def _split(dir_name: str) -> tuple[str, int] | None:
    m = _DIR_RE.match(dir_name)
    if m is None:
        return None
    return f"{m['base']}__{m['method']}", int(m["major"])


def _expected_majors() -> dict[str, int]:
    """``{endpoint__method: major}`` of the SIP endpoints in the table."""
    names = (
        _safe_segment(spec["path"].strip("/").replace("/", "__"))
        for spec in ENDPOINTS.values()
        if spec["service"] == "sip"
    )
    return dict(parsed for name in names if (parsed := _split(name)) is not None)


def detect_version(cache_root: str | Path | None) -> str | None:
    """SIP API generation of the endpoints cached under *cache_root*."""
    if cache_root is None:
        return None
    sip_dir = Path(cache_root) / "sip"
    if not sip_dir.is_dir():
        return None
    expected = _expected_majors()
    matched = False
    deviating: set[int] = set()
    for entry in sip_dir.iterdir():
        parsed = _split(entry.name) if entry.is_dir() else None
        if parsed is None or parsed[0] not in expected:
            continue
        if parsed[1] == expected[parsed[0]]:
            matched = True
        else:
            deviating.add(parsed[1])
    if deviating:
        return str(max(deviating))
    return CURRENT_TABLE if matched else None
