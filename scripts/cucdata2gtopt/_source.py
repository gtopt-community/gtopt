# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Fetch and cache the CUC-data case files (pinned commit + SHA-256).

The data of G. Morales-Espana & D. A. Tejada-Arango, "Modeling the hidden
flexibility of clustered unit commitment", IEEE TPWRS 34(4) 2019, are
published at https://github.com/datejada/CUC-data without a licence, so
gtopt never redistributes them.  :func:`fetch_file` downloads a file from
the pinned upstream commit, checks its SHA-256 and stores it in the cache
directory:

* ``$GTOPT_CUCDATA_DIR`` when set (e.g. a pre-staged copy for offline CI);
* else ``$XDG_CACHE_HOME/gtopt/cucdata`` or ``~/.cache/gtopt/cucdata``.

``GTOPT_OFFLINE=1`` forbids downloads: a file missing from the cache then
raises :class:`DataUnavailableError` (the tests skip on it).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

#: Upstream repository and the pinned commit every file is fetched from.
UPSTREAM_REPO = "datejada/CUC-data"
UPSTREAM_COMMIT = "624abbbdc9f83095c02b34174e52c76f77ddfa36"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}"
RAW_URL = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"

#: file name -> SHA-256 at :data:`UPSTREAM_COMMIT`.
MANIFEST: dict[str, str] = {
    "CUC_IEEE39-busSystem.xlsx": (
        "665c1cdb4776beff0acc19e0ab6808896294006e469c5a57583ffefe6ef89514"
    ),
    "CUC_IEEE118-busSystem.xlsx": (
        "f4e87590261a80c45c29a9ea2fa759f4c9cc92a3256c07fa89d603a52034f2a3"
    ),
    "StarGenLite_Clustered-UC.gms": (
        "63aff3768e34f6e92fb5d48cc121c7462df6170f3472ca8fe4ddf558fb16fbbe"
    ),
    "ClusteredUC-Addendum.pdf": (
        "514e9973269445e95a808ea8950ee3e7a06456f8af366278bc76d1c33c909e42"
    ),
    "README.md": "137bf6c05a5c9f0f1d60e7185b965b84cd4e2d63348efe434b58dda1f9500215",
}

#: ``--system`` value -> workbook file name.
SYSTEM_FILES: dict[int, str] = {
    39: "CUC_IEEE39-busSystem.xlsx",
    118: "CUC_IEEE118-busSystem.xlsx",
}

_DOWNLOAD_TIMEOUT_S = 60.0


class DataUnavailableError(RuntimeError):
    """A CUC-data file is neither cached nor downloadable."""


def cache_dir() -> Path:
    """Directory holding the cached CUC-data files."""
    env = os.environ.get("GTOPT_CUCDATA_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "gtopt" / "cucdata"


def offline() -> bool:
    """True when ``GTOPT_OFFLINE`` forbids network access."""
    return os.environ.get("GTOPT_OFFLINE", "").strip() not in ("", "0")


def sha256_of(path: Path) -> str:
    """Hex SHA-256 of the file at *path*."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_S) as resp:  # noqa: S310
        data: bytes = resp.read()
    return data


def cached_path(name: str, directory: Path | None = None) -> Path:
    """Path of *name* inside the cache (may not exist yet)."""
    if name not in MANIFEST:
        raise KeyError(f"unknown CUC-data file {name!r}; known: {sorted(MANIFEST)}")
    return (directory or cache_dir()) / name


def fetch_file(
    name: str, directory: Path | None = None, *, force: bool = False
) -> Path:
    """Return the cached, SHA-256-verified path of CUC-data file *name*.

    Downloads it from the pinned commit when absent (or when *force*).

    Raises:
        KeyError: *name* is not in :data:`MANIFEST`.
        DataUnavailableError: not cached and downloads are disabled or fail.
        ValueError: the cached or downloaded file has the wrong SHA-256.
    """
    dest = cached_path(name, directory)
    expected = MANIFEST[name]
    if dest.exists() and not force:
        digest = sha256_of(dest)
        if digest != expected:
            raise ValueError(
                f"SHA-256 mismatch for cached {dest}: expected {expected}, "
                f"got {digest} (delete it to re-download)"
            )
        return dest
    if offline():
        raise DataUnavailableError(
            f"{name} not cached at {dest} and GTOPT_OFFLINE is set"
        )
    url = RAW_URL.format(repo=UPSTREAM_REPO, commit=UPSTREAM_COMMIT, path=name)
    try:
        data = _download(url)
    except OSError as exc:  # URLError, timeouts, DNS failures
        raise DataUnavailableError(f"cannot download {url}: {exc}") from exc
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected:
        raise ValueError(
            f"SHA-256 mismatch for {url}: expected {expected}, got {digest}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp name + atomic rename: concurrent fetchers never see a
    # partial file.
    fd, tmp_name = tempfile.mkstemp(prefix=dest.name, suffix=".part", dir=dest.parent)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    Path(tmp_name).replace(dest)
    return dest


def system_workbook(system: int, directory: Path | None = None) -> Path:
    """Cached workbook of the 39- or 118-bus system (downloaded if needed)."""
    if system not in SYSTEM_FILES:
        raise KeyError(f"unknown system {system}; available: {sorted(SYSTEM_FILES)}")
    return fetch_file(SYSTEM_FILES[system], directory)


def fetch_all(directory: Path | None = None) -> list[Path]:
    """Fetch every manifest file into the cache; return their paths."""
    return [fetch_file(name, directory) for name in MANIFEST]
