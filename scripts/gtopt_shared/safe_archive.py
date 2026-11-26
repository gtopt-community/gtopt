# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Bounded, path-safe extraction of the archives the converters read.

Operator bundles (PLEXOS / PLP zips, ``.xz`` / ``.zst`` wrappers, tar
archives) come from outside the repository.  These helpers put a ceiling
on how much data an archive may expand to and keep every extracted path
inside its destination directory:

* :func:`check_zip` -- member count and total uncompressed size of a zip;
* :func:`extractall_zip` / :func:`extract_zip_member` -- :mod:`zipfile`
  extraction after :func:`check_zip` (``zipfile`` already strips absolute
  paths and ``..`` components);
* :func:`extractall_tar` -- tar extraction with the ``data`` filter (or an
  equivalent check on Pythons without extraction filters);
* :func:`copy_limited` -- stream copy (``lzma`` / ``zstd`` / ``gzip``
  decompression, zip members) that stops past a byte budget;
* :func:`safe_member_name` / :func:`safe_child` -- reduce an untrusted
  name to one path component / join it under a directory.

The limits are generous (they only stop pathological inputs) and can be
raised through ``$GTOPT_MAX_EXTRACT_BYTES`` and
``$GTOPT_MAX_ARCHIVE_MEMBERS``.
"""

from __future__ import annotations

import os
import re
import tarfile
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import IO

#: Default ceiling on the bytes one archive may expand to (256 GiB).
DEFAULT_MAX_BYTES = 256 * 1024**3
#: Default ceiling on the number of members of one archive.
DEFAULT_MAX_MEMBERS = 200_000

ENV_MAX_BYTES = "GTOPT_MAX_EXTRACT_BYTES"
ENV_MAX_MEMBERS = "GTOPT_MAX_ARCHIVE_MEMBERS"

_CHUNK = 1 << 20
#: tarfile extraction filters (PEP 706) are available.
_TAR_HAS_FILTERS = hasattr(tarfile, "data_filter")


class ArchiveLimitError(ValueError):
    """An archive exceeds the extraction limits or escapes its target."""


def _env_int(name: str, default: int, environ: Mapping[str, str] | None) -> int:
    env = os.environ if environ is None else environ
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ArchiveLimitError(f"${name}={raw!r} is not an integer") from exc
    if value <= 0:
        raise ArchiveLimitError(f"${name} must be positive, got {value}")
    return value


def max_extract_bytes(environ: Mapping[str, str] | None = None) -> int:
    """Byte budget for one archive (``$GTOPT_MAX_EXTRACT_BYTES``)."""
    return _env_int(ENV_MAX_BYTES, DEFAULT_MAX_BYTES, environ)


def max_archive_members(environ: Mapping[str, str] | None = None) -> int:
    """Member budget for one archive (``$GTOPT_MAX_ARCHIVE_MEMBERS``)."""
    return _env_int(ENV_MAX_MEMBERS, DEFAULT_MAX_MEMBERS, environ)


def _hint() -> str:
    return f" (raise ${ENV_MAX_BYTES} / ${ENV_MAX_MEMBERS} if the input is trusted)"


def check_zip(
    zf: zipfile.ZipFile,
    members: Iterable[zipfile.ZipInfo] | None = None,
    *,
    max_bytes: int | None = None,
    max_members: int | None = None,
) -> int:
    """Validate the declared sizes of *members* (default: all); return the total.

    ``zipfile`` never reads past a member's declared ``file_size``, so the
    declared total is a hard bound on what extraction writes.
    """
    infos = list(zf.infolist() if members is None else members)
    limit_n = max_archive_members() if max_members is None else max_members
    limit_b = max_extract_bytes() if max_bytes is None else max_bytes
    name = zf.filename or "<zip>"
    if len(infos) > limit_n:
        raise ArchiveLimitError(
            f"{name}: {len(infos)} members exceed the limit of {limit_n}{_hint()}"
        )
    total = sum(i.file_size for i in infos)
    if total > limit_b:
        raise ArchiveLimitError(
            f"{name}: expands to {total} bytes, above the limit of {limit_b}{_hint()}"
        )
    return total


def extractall_zip(
    zf: zipfile.ZipFile,
    dest: str | os.PathLike[str],
    members: Iterable[str | zipfile.ZipInfo] | None = None,
) -> None:
    """``zf.extractall(dest, members)`` after :func:`check_zip`."""
    if members is None:
        infos = zf.infolist()
    else:
        infos = [
            m if isinstance(m, zipfile.ZipInfo) else zf.getinfo(m) for m in members
        ]
    check_zip(zf, infos)
    # zipfile strips absolute paths and ".." itself; sizes checked above.
    zf.extractall(dest, infos)  # noqa: S202


def extract_zip_member(
    zf: zipfile.ZipFile,
    member: str | zipfile.ZipInfo,
    dest: str | os.PathLike[str],
) -> str:
    """``zf.extract(member, dest)`` after :func:`check_zip` on that member."""
    info = member if isinstance(member, zipfile.ZipInfo) else zf.getinfo(member)
    check_zip(zf, [info])
    return zf.extract(info, dest)


def copy_limited(
    src: IO[bytes],
    dst: IO[bytes],
    *,
    max_bytes: int | None = None,
    what: str = "stream",
) -> int:
    """Copy *src* to *dst*; raise once more than *max_bytes* were read."""
    limit = max_extract_bytes() if max_bytes is None else max_bytes
    total = 0
    while chunk := src.read(_CHUNK):
        total += len(chunk)
        if total > limit:
            raise ArchiveLimitError(
                f"{what}: decompressed data exceeds the limit of {limit} bytes{_hint()}"
            )
        dst.write(chunk)
    return total


def _tar_member_ok(member: tarfile.TarInfo, dest: Path) -> bool:
    if not (member.isfile() or member.isdir()):
        return False  # links, devices, fifos
    target = (dest / member.name).resolve()
    return target == dest or dest in target.parents


def extractall_tar(tar: tarfile.TarFile, dest: str | os.PathLike[str]) -> None:
    """Extract *tar* under *dest*: regular files and directories only.

    Uses the ``data`` extraction filter where available; otherwise checks
    every member itself.  The member count and declared sizes are bounded
    like :func:`check_zip`.
    """
    root = Path(dest).resolve()
    name = os.fsdecode(tar.name) if tar.name else "<tar>"
    members = tar.getmembers()
    limit_n = max_archive_members()
    limit_b = max_extract_bytes()
    if len(members) > limit_n:
        raise ArchiveLimitError(
            f"{name}: {len(members)} members exceed the limit of {limit_n}{_hint()}"
        )
    total = sum(m.size for m in members if m.isfile())
    if total > limit_b:
        raise ArchiveLimitError(
            f"{name}: expands to {total} bytes, above the limit of {limit_b}{_hint()}"
        )
    bad = [m.name for m in members if not _tar_member_ok(m, root)]
    if bad:
        raise ArchiveLimitError(f"{name}: unsafe members refused: {bad[:5]}")
    if _TAR_HAS_FILTERS:
        tar.extractall(root, members=members, filter="data")
    else:  # Python without extraction filters (< 3.10.12 / 3.11.4)
        tar.extractall(root, members=members)  # noqa: S202 - members checked


_UNSAFE_CHARS = re.compile(r"[\x00-\x1f]")


def safe_member_name(name: str) -> str:
    """Reduce an untrusted (archive / portal / element) name to a file name.

    Takes the last component under both POSIX and Windows separators and
    refuses names that are empty, ``.``/``..`` or hold control characters.
    """
    base = PureWindowsPath(PurePosixPath(name).name).name
    if base in {"", ".", ".."} or _UNSAFE_CHARS.search(base):
        raise ArchiveLimitError(f"unsafe file name {name!r}")
    return base


def safe_child(directory: str | os.PathLike[str], name: str) -> Path:
    """``directory / name`` where *name* must stay a direct child."""
    return Path(directory) / safe_member_name(name)


def copy_file_limited(
    src: IO[bytes], dst_path: str | os.PathLike[str], *, what: str = ""
) -> int:
    """Write *src* to *dst_path* with :func:`copy_limited`; unlink on failure."""
    out = Path(dst_path)
    try:
        with out.open("wb") as fo:
            return copy_limited(src, fo, what=what or str(out))
    except ArchiveLimitError:
        out.unlink(missing_ok=True)
        raise


__all__ = [
    "ArchiveLimitError",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_MEMBERS",
    "ENV_MAX_BYTES",
    "ENV_MAX_MEMBERS",
    "check_zip",
    "copy_file_limited",
    "copy_limited",
    "extract_zip_member",
    "extractall_tar",
    "extractall_zip",
    "max_archive_members",
    "max_extract_bytes",
    "safe_child",
    "safe_member_name",
]
