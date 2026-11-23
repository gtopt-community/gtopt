# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Unified download cache with a sha256 manifest.

Layout::

    $GTOPT_FETCH_CACHE (default ~/.cache/gtopt/fetch)/
      <operator>/<dataset>/
        <file>                 # downloaded / registered files
        MANIFEST.jsonl         # one JSON object per file (last one wins)

Every ``MANIFEST.jsonl`` row records ``file``, ``sha256``, ``size``,
``url`` (without query string: pre-signed URLs carry signatures),
``fetched_at``, ``published_at``, ``operator``, ``dataset``, ``date`` and
``source`` (``download`` / ``legacy_cache`` / ``unwrap`` / ``manual``).
``gtopt-fetch get --out DIR`` writes the same manifest next to the
delivered files, so ``gtopt-fetch verify DIR`` re-checks them later.

Operators rarely publish checksums, so a case repository pins its inputs
with a ``sources.lock`` in ``sha256sum`` format (``<sha256>  <file>``):
``verify`` checks it too when present.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = "MANIFEST.jsonl"
LOCK_NAME = "sources.lock"

#: Environment override of the cache root.
CACHE_ENV = "GTOPT_FETCH_CACHE"


def cache_root() -> Path:
    """Root of the unified cache (``$GTOPT_FETCH_CACHE`` or ~/.cache/...)."""
    env = os.environ.get(CACHE_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "gtopt" / "fetch"


def dataset_dir(operator: str, dataset: str, root: Path | None = None) -> Path:
    """Cache directory of one dataset."""
    return (root or cache_root()) / operator / dataset


def sha256_file(path: Path) -> str:
    """Hex sha256 of a file (streamed)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> str:
    """Current UTC time, ISO-8601, second resolution."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class ManifestEntry:  # pylint: disable=too-many-instance-attributes
    """One row of ``MANIFEST.jsonl``."""

    file: str
    sha256: str
    size: int
    url: str = ""
    fetched_at: str = field(default_factory=utc_now)
    published_at: str = ""
    operator: str = ""
    dataset: str = ""
    date: str = ""
    source: str = "download"

    @classmethod
    def for_file(cls, path: Path, **meta: str) -> "ManifestEntry":
        """Build an entry for ``path`` (hashes the file)."""
        return cls(
            file=path.name,
            sha256=sha256_file(path),
            size=path.stat().st_size,
            **meta,
        )


def read_manifest(directory: Path) -> dict[str, ManifestEntry]:
    """``{file: entry}`` of ``directory/MANIFEST.jsonl`` (last row wins)."""
    path = directory / MANIFEST_NAME
    out: dict[str, ManifestEntry] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        known = {f.name: row[f.name] for f in fields(ManifestEntry) if f.name in row}
        entry = ManifestEntry(**known)
        out[entry.file] = entry
    return out


def write_manifest(directory: Path, entries: list[ManifestEntry]) -> Path:
    """Merge ``entries`` into ``directory/MANIFEST.jsonl`` (one row per file)."""
    merged = read_manifest(directory)
    for e in entries:
        merged[e.file] = e
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / MANIFEST_NAME
    tmp = path.with_suffix(".jsonl.part")
    with tmp.open("w", encoding="utf-8") as fh:
        for name in sorted(merged):
            fh.write(json.dumps(asdict(merged[name]), sort_keys=True) + "\n")
    tmp.replace(path)
    return path


def place(src: Path, dest: Path) -> Path:
    """Materialise ``src`` at ``dest``: hard link when possible, else copy."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.samefile(src):
            return dest
        dest.unlink()
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)
    return dest


def read_lock(path: Path) -> dict[str, str]:
    """Parse a ``sha256sum``-style lock: ``{file: sha256}``."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition(" ")
        out[name.strip().lstrip("*")] = digest.lower()
    return out


def write_lock(directory: Path, path: Path | None = None) -> Path:
    """Write ``sources.lock`` from the manifest of ``directory``."""
    path = path or directory / LOCK_NAME
    rows = [f"{e.sha256}  {e.file}" for e in read_manifest(directory).values()]
    path.write_text("\n".join(sorted(rows, key=lambda r: r[66:])) + "\n", "utf-8")
    return path


@dataclass(frozen=True, slots=True)
class VerifyIssue:
    """One verification failure."""

    file: str
    problem: str


def verify_dir(
    directory: Path, lock: Path | None = None
) -> tuple[int, list[VerifyIssue]]:
    """Check the files of ``directory`` against its manifest and lock.

    Returns ``(n_checked, issues)``.  The lock defaults to
    ``directory/sources.lock`` when present.
    """
    issues: list[VerifyIssue] = []
    expected: dict[str, tuple[str, int | None]] = {
        name: (e.sha256, e.size) for name, e in read_manifest(directory).items()
    }
    lock = lock or (directory / LOCK_NAME)
    if lock.is_file():
        for name, digest in read_lock(lock).items():
            prev = expected.get(name)
            if prev is not None and prev[0] != digest:
                issues.append(VerifyIssue(name, "manifest and lock disagree on sha256"))
            expected[name] = (digest, prev[1] if prev else None)
    if not expected:
        issues.append(VerifyIssue(str(directory), f"no {MANIFEST_NAME} or lock"))
        return 0, issues
    for name, (digest, size) in sorted(expected.items()):
        path = directory / name
        if not path.is_file():
            issues.append(VerifyIssue(name, "missing"))
            continue
        if size is not None and path.stat().st_size != size:
            issues.append(VerifyIssue(name, "size mismatch"))
            continue
        if sha256_file(path) != digest:
            issues.append(VerifyIssue(name, "sha256 mismatch"))
    return len(expected), issues
