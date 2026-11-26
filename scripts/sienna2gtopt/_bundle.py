"""Fetch, cache and extract the NREL-Sienna test-data bundles.

The Sienna test data (``PowerSystemsTestData``) carries no licence, so
gtopt does **not** redistribute it.  Instead each bundle is described by
a manifest (:data:`MANIFEST`: upstream repository, pinned commit, file
list and SHA-256 of every file) and built on demand:

1. :func:`fetch_bundle` downloads the listed files from the pinned
   upstream commit, checks every SHA-256, and packs them into
   ``sienna_<size>.tar.zst`` in the cache directory -- the same archive
   layout the bundles always had (``sienna_<size>/<file>``).
2. :func:`extract_bundle` extracts that archive into a temporary
   directory (``$TMPDIR`` or ``~/tmp``) and returns the case directory.

Cache directory, in order of precedence:

* ``$GTOPT_SIENNA_DATA_DIR`` -- a directory holding pre-built
  ``sienna_<size>.tar.zst`` archives (e.g. staged for air-gapped CI);
* ``$XDG_CACHE_HOME/gtopt/sienna`` or ``~/.cache/gtopt/sienna``.

Set ``GTOPT_OFFLINE=1`` to forbid downloads; a missing archive then
raises :class:`BundleUnavailableError` (the tests skip on it).
"""

from __future__ import annotations

import functools
import hashlib
import io
import os
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from gtopt_shared import safe_archive

try:
    import zstandard
except ImportError:  # zstandard is in scripts/pyproject.toml; guarded for bare installs
    zstandard = None  # type: ignore[assignment]

#: Upstream repository (``NREL-Sienna/...`` redirects here) and the
#: pinned commit every file below is fetched from.
UPSTREAM_REPO = "Sienna-Platform/PowerSystemsTestData"
UPSTREAM_COMMIT = "a2ecae9ff3cfe6d219744f1b10e4edd4eeecabc9"
RAW_URL = "https://raw.githubusercontent.com/{repo}/{commit}/{path}"

#: bundle name -> list of (upstream path, sha256).  The archive member
#: name is ``sienna_<bundle>/<basename of the upstream path>``.
MANIFEST: dict[str, list[tuple[str, str]]] = {
    "5bus": [
        (
            "5-Bus/branch.csv",
            "ec96a25e934547b8b55329e2fd4796b0fd48d7a15151d515d971aaa06d89218f",
        ),
        (
            "5-Bus/bus.csv",
            "ea4ff6bd0df31497cefdfd326612544eeea5471b2a098339faf60c42314eff89",
        ),
        (
            "5-Bus/gen.csv",
            "d8bc87bb3a3b96a613c2e798ec0d04e0f9aa83eb1126df3a90840347792eacdd",
        ),
        (
            "5-Bus/generator_mapping.yaml",
            "502fdda9d336ff54f0a4dd9a3a2e619590a30c03a31e112bd8e3e8f4c1ed7586",
        ),
        (
            "5-Bus/modifier.jl",
            "4c3201f1ae62a6f47e366a8c50efaadb0dc21209b09e50ff55ce0dc28d03f5bc",
        ),
        (
            "5-Bus/reserves.csv",
            "e03f398cd5332e68cbffe685783ac7d9301a206e84de9959ae9198094ee116c1",
        ),
        (
            "5-Bus/storage.csv",
            "01df79d186e1a9944208ff103d1b8fb2a3cacfb7567577162c394dd5ebf7093f",
        ),
        (
            "5-Bus/Hydro_Upstream_Input.csv",
            "c4a622476019489874d8d2b0af682536a9831b6016970d22320d2ac30c9aa660",
        ),
        (
            "5-Bus/user_descriptors.yaml",
            "ae28ce3a146fb45e9759c71e86d5063d1efdaa1d4015b0768842674dbba606ff",
        ),
        (
            "5-Bus/user_descriptors_var_cost.yaml",
            "9a1a2e3e1365448198efe0a2a7ed5cdb937b8617cd363bbeb3ee918400a25eaa",
        ),
        (
            "5-Bus/timeseries_pointers_da.json",
            "f5cc48d3ba05837ca23a29c69eba6df9a7adac7dfe0bcdfe45980972555931a1",
        ),
        (
            "5-Bus/timeseries_pointers_rt.json",
            "1881b6578c8529d5f66f14d99a7922a3094b8f51f55d92de89f70a93c1989c1c",
        ),
        (
            "psy_data/data_5bus_pu.jl",
            "616e59f1bc6fdb2ba77504a9cd6b39b029a67024f61165f2762f57dffabb5152",
        ),
    ],
    "14bus": [
        (
            "psy_data/data_14bus_pu.jl",
            "56ae59557caad83f23129483330af0fd29e2777a57444575070e25645e289052",
        ),
    ],
}

#: Bundles that carry the CSV tables (``bus.csv``, ``gen.csv``, ...) the
#: variant builders read via :func:`sienna2gtopt._reader.load_case`.  The
#: other bundles hold only PowerSystems.jl data files (``*.jl``), used by
#: the Julia golden tools, not by the CLI converter.
CSV_BUNDLES: tuple[str, ...] = tuple(
    name
    for name, files in MANIFEST.items()
    if any(path.endswith("/bus.csv") for path, _ in files)
)

_DOWNLOAD_TIMEOUT_S = 30.0


class BundleUnavailableError(RuntimeError):
    """A Sienna bundle is neither cached nor downloadable (e.g. offline)."""


def cache_dir() -> Path:
    """Directory holding the ``sienna_<size>.tar.zst`` archives."""
    env = os.environ.get("GTOPT_SIENNA_DATA_DIR")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "gtopt" / "sienna"


def bundle_path(name: str) -> Path:
    """Path of the (possibly not yet built) archive for bundle *name*."""
    if name not in MANIFEST:
        raise KeyError(f"unknown sienna bundle: {name!r}; available={list(MANIFEST)}")
    return cache_dir() / f"sienna_{name}.tar.zst"


def available_bundles() -> list[str]:
    """Names of bundles whose archive is already built in the cache."""
    return sorted(name for name in MANIFEST if bundle_path(name).exists())


def _offline() -> bool:
    return os.environ.get("GTOPT_OFFLINE", "").strip() not in ("", "0")


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_S) as resp:  # noqa: S310
        return resp.read()


def _require_zstandard() -> None:
    if zstandard is None:
        raise RuntimeError(
            "zstandard is required for the Sienna bundles; install with "
            "`pip install zstandard` or the scripts/ dependencies."
        )


def _pack(name: str, payloads: list[tuple[str, bytes]]) -> bytes:
    """Return a zstd-compressed tar of ``sienna_<name>/<file>`` members."""
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        root = tarfile.TarInfo(f"sienna_{name}")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        tar.addfile(root)
        for fname, data in payloads:
            info = tarfile.TarInfo(f"sienna_{name}/{fname}")
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return zstandard.ZstdCompressor(level=19).compress(tar_buf.getvalue())


def fetch_bundle(name: str = "5bus", *, force: bool = False) -> Path:
    """Download bundle *name* from the pinned upstream commit and pack it.

    Every file is checked against its manifest SHA-256 before the
    archive is written.  Returns the archive path.  Idempotent unless
    *force* is set.

    Raises:
        KeyError: unknown bundle name.
        BundleUnavailableError: downloads are disabled (``GTOPT_OFFLINE``)
            or the network request fails.
        ValueError: a downloaded file does not match its SHA-256.
    """
    dest = bundle_path(name)
    if dest.exists() and not force:
        return dest
    if _offline():
        raise BundleUnavailableError(
            f"Sienna bundle {name!r} not cached at {dest} and GTOPT_OFFLINE is set"
        )
    _require_zstandard()

    payloads: list[tuple[str, bytes]] = []
    for upstream_path, sha256 in MANIFEST[name]:
        url = RAW_URL.format(
            repo=UPSTREAM_REPO, commit=UPSTREAM_COMMIT, path=upstream_path
        )
        try:
            data = _download(url)
        except OSError as exc:  # URLError, timeouts, DNS failures
            raise BundleUnavailableError(
                f"cannot download Sienna bundle {name!r} ({url}): {exc}"
            ) from exc
        digest = hashlib.sha256(data).hexdigest()
        if digest != sha256:
            raise ValueError(
                f"SHA-256 mismatch for {url}: expected {sha256}, got {digest}"
            )
        payloads.append((Path(upstream_path).name, data))

    dest.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp name + atomic rename: concurrent fetchers (pytest-xdist
    # workers) never see a partial archive.
    fd, tmp_name = tempfile.mkstemp(prefix=dest.name, suffix=".part", dir=dest.parent)
    with os.fdopen(fd, "wb") as fh:
        fh.write(_pack(name, payloads))
    Path(tmp_name).replace(dest)
    return dest


def _tempdir_base() -> Path:
    """Return a writable base dir for extraction.

    Honours ``$TMPDIR`` (POSIX) when set; falls back to ``~/tmp/`` so we
    don't write under ``/tmp/`` (project policy forbids it).
    """

    env = os.environ.get("TMPDIR")
    if env:
        base = Path(env)
        base.mkdir(parents=True, exist_ok=True)
        return base
    home_tmp = Path.home() / "tmp"
    home_tmp.mkdir(parents=True, exist_ok=True)
    return home_tmp


def _decompress_zst(src: Path, dst: Path) -> None:
    _require_zstandard()
    dctx = zstandard.ZstdDecompressor()
    with src.open("rb") as fh_in, dst.open("wb") as fh_out:
        with dctx.stream_reader(fh_in) as reader:
            safe_archive.copy_limited(reader, fh_out, what=str(src))


@functools.lru_cache(maxsize=None)
def extract_bundle(name: str = "5bus") -> Path:
    """Return the extracted ``sienna_<size>/`` dir of bundle *name*.

    Builds the archive first (:func:`fetch_bundle`) when it is not
    cached.  The result is cached per process so repeated calls hit the
    same tempdir; each fresh process extracts into a new tempdir.

    Raises:
        KeyError: unknown bundle name.
        BundleUnavailableError: not cached and not downloadable.
    """

    src = fetch_bundle(name)

    base = _tempdir_base()
    tempdir = Path(tempfile.mkdtemp(prefix=f"sienna_{name}_", dir=str(base)))

    # Decompress .tar.zst → .tar in tempdir, then extract.  Using
    # disk-roundtrip rather than a stream so zstandard's input chunking
    # can't deadlock against tarfile's read pattern.
    tar_path = tempdir / f"sienna_{name}.tar"
    _decompress_zst(src, tar_path)

    with tarfile.open(tar_path, mode="r") as tar:
        # Regular files and directories only, inside tempdir, bounded size
        # ("data" filter where available, an equivalent check otherwise).
        safe_archive.extractall_tar(tar, tempdir)
    tar_path.unlink()
    return tempdir / f"sienna_{name}"
