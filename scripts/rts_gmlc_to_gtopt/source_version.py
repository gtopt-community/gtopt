# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""RTS-GMLC dataset releases ``rts_gmlc_to_gtopt`` converts.

The RTS-GMLC CSVs (``GridMod/RTS-GMLC``, ``RTS_Data/``) carry no version
of their own, so the release is recorded by the converter itself: the
download URL is pinned to the release tag, and a fresh cache gets a
:data:`MARKER` file naming the release it was filled from.  A cache
filled before the marker existed (or by hand) is undetectable.

``0.2.3`` is the latest tag (identical to ``master`` when this table was
written, which the converter previously fetched).  It is exercised by the
slow CTest ``slow_rts_gmlc_reserves_inertia`` (``integration_test/``,
``GTOPT_SLOW_TESTS``), which downloads the real data; the unit tests use
synthetic CSVs.  Earlier tags (0.1 - 0.2.2) were never run through the
converter and are not declared.
"""

from __future__ import annotations

from pathlib import Path

from gtopt_shared.source_versions import SourceFormat

SOURCE_FORMAT = SourceFormat(
    name="RTS-GMLC",
    supported=("0.2.3",),
    tested=("0.2.3",),
    default="0.2.3",
)

#: Cache file naming the release the cache was downloaded from.
MARKER = "SOURCE_VERSION"


def release_tag(version: str) -> str:
    """Git tag of RTS-GMLC release *version* (``"0.2.3"`` -> ``"v0.2.3"``)."""
    return f"v{version}"


def detect_version(cache_dir: str | Path) -> str | None:
    """Release named by the cache's :data:`MARKER` file, else ``None``."""
    try:
        text = (Path(cache_dir) / MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def write_marker(cache_dir: str | Path, version: str) -> None:
    """Record that *cache_dir* was filled from release *version*."""
    (Path(cache_dir) / MARKER).write_text(f"{version}\n", encoding="utf-8")
