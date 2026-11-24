# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``PowerSystemsTestData`` commits whose ``118-Bus/`` data ``nrel118_to_gtopt`` reads.

The NREL-118 CSVs carry no version of their own; their identity is the
upstream commit.  The converter downloads from the pinned
:data:`nrel118_to_gtopt._data.DEFAULT_REF` (``--ref`` picks another), so
the commit is detected from

* the :data:`MARKER` file :func:`nrel118_to_gtopt._data.populate_cache`
  writes into a cache it fills from scratch (naming the ``--ref`` used), or
* the ``HEAD`` of a ``PowerSystemsTestData`` git checkout given as the
  cache (its root or its ``118-Bus/`` directory).

A cache filled by hand, or before the marker existed, is undetectable.

``tested`` is empty on purpose: the pinned commit is the one the
converter was validated against by hand, but nothing in the repository
runs it on the real ``118-Bus`` data (the unit tests use a synthetic
3-bus cache in the upstream CSV formats, and the C++ emission test uses
a hand-picked fleet subset).
"""

from __future__ import annotations

from pathlib import Path

from gtopt_shared.source_versions import SourceFormat

from ._data import DEFAULT_REF, SOURCE_MARKER

SOURCE_FORMAT = SourceFormat(
    name="NREL-118 PowerSystemsTestData",
    supported=(DEFAULT_REF,),
    tested=(),
    default=DEFAULT_REF,
    ordered=False,
)

#: Cache file naming the git ref the cache was downloaded from.
MARKER = SOURCE_MARKER


def _read(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _git_head(git_dir: Path) -> str | None:
    """Commit id ``HEAD`` of *git_dir* points to (loose or packed ref)."""
    head = _read(git_dir / "HEAD")
    if head is None or not head.startswith("ref:"):
        return head
    ref = head.removeprefix("ref:").strip()
    loose = _read(git_dir / ref)
    if loose is not None:
        return loose
    packed = _read(git_dir / "packed-refs") or ""
    for line in packed.splitlines():
        sha, _, name = line.partition(" ")
        if name == ref:
            return sha
    return None


def detect_version(cache_dir: str | Path) -> str | None:
    """Upstream commit of *cache_dir* (marker or git checkout), else ``None``."""
    root = Path(cache_dir)
    marker = _read(root / MARKER)
    if marker is not None:
        return marker
    # A checkout root (holding 118-Bus/) or its 118-Bus/ directory.
    if (root / "118-Bus").is_dir() and (root / ".git").is_dir():
        return _git_head(root / ".git")
    if root.name == "118-Bus" and (root.parent / ".git").is_dir():
        return _git_head(root.parent / ".git")
    return None
