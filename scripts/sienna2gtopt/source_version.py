# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``PowerSystemsTestData`` commits whose Sienna bundles ``sienna2gtopt`` reads.

The Sienna data carries no version of its own; its identity is the
upstream commit.  Every bundle file is listed in
:data:`sienna2gtopt._bundle.MANIFEST` with its SHA-256 at the pinned
commit :data:`sienna2gtopt._bundle.UPSTREAM_COMMIT`, so an extracted
bundle whose files all match those digests *is* that commit.  A bundle
staged by hand (``$GTOPT_SIENNA_DATA_DIR``) with different contents is
undetectable.

The pinned commit is the one the test suite runs on (the ``5bus``
bundle, ``tests/conftest.py::require_sienna_5bus``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from gtopt_shared.source_versions import SourceFormat

from ._bundle import MANIFEST, UPSTREAM_COMMIT, UPSTREAM_REPO

SOURCE_FORMAT = SourceFormat(
    name=f"Sienna {UPSTREAM_REPO}",
    supported=(UPSTREAM_COMMIT,),
    tested=(UPSTREAM_COMMIT,),
    default=UPSTREAM_COMMIT,
    ordered=False,
)


def detect_version(case_dir: str | Path, bundle: str = "5bus") -> str | None:
    """Pinned commit when every *bundle* file in *case_dir* matches its digest.

    Returns ``None`` for an unknown bundle, a missing file or any digest
    mismatch.
    """
    entries = MANIFEST.get(bundle)
    if not entries:
        return None
    root = Path(case_dir)
    for upstream_path, sha256 in entries:
        path = root / Path(upstream_path).name
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None
        if digest != sha256:
            return None
    return UPSTREAM_COMMIT
