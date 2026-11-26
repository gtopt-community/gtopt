# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Download backends of ``gtopt-fetch``.

* ``cen_portal`` -- CEN Programa de Operación portal (index + pre-signed
  S3 URLs), on top of :mod:`cen2gtopt.pcp_archive`.  Files already in the
  legacy ``pcp_archive`` cache are reused (hard link) instead of
  downloaded again.
* ``static_url`` -- a URL template (``{date}``, ``{version}``).
* ``manual`` / ``html_index`` -- the source has no stable direct link or
  forbids automated download / redistribution: print the page and terms
  and register a user-supplied file (``--file``) in the cache.

Every file that lands in the cache is recorded in ``MANIFEST.jsonl``
(see :mod:`gtopt_operators.fetch.cache`).
"""

from __future__ import annotations

import logging
import re
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from gtopt_operators.auth import redact, strip_query
from gtopt_operators.fetch.cache import (
    ManifestEntry,
    dataset_dir,
    place,
    sha256_file,
    write_manifest,
)
from gtopt_operators.registry import DatasetSpec, SourceRegistry, compile_pattern
from gtopt_operators.tls import tls_verify
from gtopt_shared import safe_archive

if TYPE_CHECKING:  # pragma: no cover
    from cen2gtopt.pcp_archive import PcpFile

_LOG = logging.getLogger("gtopt_operators.fetch")


class FetchError(RuntimeError):
    """A dataset could not be fetched (nothing matched, bad checksum, ...)."""


class ManualActionRequired(FetchError):
    """The dataset must be downloaded by hand (see the printed page)."""


@dataclass(slots=True)
class FetchRequest:  # pylint: disable=too-many-instance-attributes
    """Arguments of one ``gtopt-fetch <op> get <dataset>`` call."""

    registry: SourceRegistry
    dataset: DatasetSpec
    day: date | None = None
    version: str = ""
    refresh: bool = False
    dry_run: bool = False
    manual_file: Path | None = None
    cache_root: Path | None = None

    @property
    def operator(self) -> str:
        """Operator id."""
        return self.registry.operator.id

    @property
    def cache_dir(self) -> Path:
        """Cache directory of the dataset."""
        return dataset_dir(self.operator, self.dataset.name, self.cache_root)

    def meta(self, **extra: str) -> dict[str, str]:
        """Common manifest metadata."""
        out = {
            "operator": self.operator,
            "dataset": self.dataset.name,
            "date": self.day.isoformat() if self.day else "",
        }
        out.update(extra)
        return out


@dataclass(slots=True)
class FetchResult:
    """Files delivered by a backend (cache paths) and their manifest rows."""

    files: list[Path] = field(default_factory=list)
    entries: list[ManifestEntry] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)  # dry-run: names only


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def http_download(url: str, dest: Path, *, timeout: int = 600) -> Path:
    """Stream ``url`` to ``dest`` atomically; errors never leak secrets."""
    import requests  # pylint: disable=import-outside-toplevel

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    _LOG.info("download %s", strip_query(url))
    try:
        with requests.get(url, stream=True, timeout=timeout, verify=tls_verify()) as r:
            r.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        fh.write(chunk)
    except requests.RequestException as exc:
        tmp.unlink(missing_ok=True)
        raise FetchError(redact(str(exc))) from None
    tmp.replace(dest)
    return dest


# ---------------------------------------------------------------------------
# cen_portal
# ---------------------------------------------------------------------------


def _latest_by_name(files: list[PcpFile]) -> list[PcpFile]:
    best: dict[str, PcpFile] = {}
    for f in files:
        prev = best.get(f.nombre)
        if prev is None or f.fecha_publicacion > prev.fecha_publicacion:
            best[f.nombre] = f
    return list(best.values())


def select_portal_files(
    files: list[PcpFile], ds: DatasetSpec, day: date
) -> list[PcpFile]:
    """Apply the dataset patterns, categories and date rule to an index."""
    ymd = day.strftime("%Y%m%d")
    cats = {c.upper() for c in ds.categories}
    pool = _latest_by_name(
        [f for f in files if not cats or f.categoria.upper() in cats]
    )
    if ds.date_rule == "exact":
        for rx in ds.regexes(ymd):
            hits = sorted(
                (f for f in pool if rx.match(f.nombre)), key=lambda f: f.nombre
            )
            if hits:
                return hits if ds.multi else hits[:1]
        return []
    strict = ds.date_rule == "latest_before"
    for rx in ds.regexes(None):
        best_key: tuple[str, str] = ("", "")
        best_file: PcpFile | None = None
        for f in pool:
            m = rx.match(f.nombre)
            if not m:
                continue
            d = m.group(1)
            if d > ymd or (strict and d == ymd):
                continue
            key = (d, f.fecha_publicacion)
            if best_file is None or key > best_key:
                best_key, best_file = key, f
        if best_file is not None:
            return [best_file]
    return []


def _companions(files: list[PcpFile], ds: DatasetSpec, day: date) -> list[PcpFile]:
    ymd = day.strftime("%Y%m%d")
    pool = _latest_by_name(files)
    out: list[PcpFile] = []
    for pat in ds.companions:
        rx = compile_pattern(pat, ymd)
        out.extend(
            sorted((f for f in pool if rx.match(f.nombre)), key=lambda f: f.nombre)
        )
    return out


def _legacy_path(f: PcpFile) -> Path:
    from cen2gtopt import pcp_archive  # pylint: disable=import-outside-toplevel

    return pcp_archive.DEFAULT_DOWNLOAD_ROOT / f.categoria / f.nombre


def _portal_get(req: FetchRequest, f: PcpFile) -> tuple[Path, ManifestEntry]:
    dest = req.cache_dir / f.nombre
    meta = req.meta(
        url=strip_query(f.enlace_descarga), published_at=f.fecha_publicacion
    )
    if dest.is_file() and dest.stat().st_size > 0:
        _LOG.info("cache hit %s", dest)
        return dest, ManifestEntry.for_file(dest, source="download", **meta)
    legacy = _legacy_path(f)
    if legacy.is_file() and legacy.stat().st_size > 0:
        _LOG.info("reuse legacy pcp_archive cache %s", legacy)
        place(legacy, dest)
        return dest, ManifestEntry.for_file(dest, source="legacy_cache", **meta)
    http_download(f.enlace_descarga, dest)
    return dest, ManifestEntry.for_file(dest, source="download", **meta)


def _unwrap_regexes(req: FetchRequest) -> list[re.Pattern[str]]:
    ymd = req.day.strftime("%Y%m%d") if req.day else None
    return [compile_pattern(p, ymd) for p in req.dataset.unwrap]


def _unwrap(
    req: FetchRequest, wrapper: Path, parent: ManifestEntry
) -> tuple[list[Path], list[ManifestEntry]]:
    regexes = _unwrap_regexes(req)
    files: list[Path] = []
    entries: list[ManifestEntry] = []
    with zipfile.ZipFile(wrapper) as zf:
        for member in zf.namelist():
            base = Path(member).name
            if not base or not any(rx.match(base) for rx in regexes):
                continue
            out = req.cache_dir / base
            with zf.open(member) as fi, out.open("wb") as fo:
                safe_archive.copy_limited(fi, fo)
            files.append(out)
            entries.append(
                ManifestEntry.for_file(
                    out,
                    source="unwrap",
                    **req.meta(
                        url=f"{parent.url}#{member}",
                        published_at=parent.published_at,
                    ),
                )
            )
    return files, entries


def fetch_cen_portal(
    req: FetchRequest,
    index: Callable[..., list[Any]] | None = None,
) -> FetchResult:
    """``cen_portal`` backend."""
    if req.day is None:
        raise FetchError(f"dataset {req.dataset.name!r} needs --date")
    if index is None:
        from cen2gtopt.pcp_archive import (  # pylint: disable=import-outside-toplevel
            fetch_index,
        )

        index = fetch_index
    files = index(force=req.refresh)
    picked = select_portal_files(files, req.dataset, req.day)
    if not picked:
        raise FetchError(
            f"no {req.operator}/{req.dataset.name} file for {req.day.isoformat()} "
            f"(rule {req.dataset.date_rule}) in the portal index"
        )
    companions = [
        f
        for f in _companions(files, req.dataset, req.day)
        if f.nombre not in {p.nombre for p in picked}
    ]
    res = FetchResult()
    if req.dry_run:
        res.planned = [f.nombre for f in picked + companions]
        return res
    req.cache_dir.mkdir(parents=True, exist_ok=True)
    cache_entries: list[ManifestEntry] = []
    for f in picked:
        path, entry = _portal_get(req, f)
        cache_entries.append(entry)
        unwrap_rx = _unwrap_regexes(req)
        if unwrap_rx and not any(rx.match(f.nombre) for rx in unwrap_rx):
            got, entries = _unwrap(req, path, entry)
            res.files.extend(got)
            res.entries.extend(entries)
            cache_entries.extend(entries)
        else:
            res.files.append(path)
            res.entries.append(entry)
    have = {p.name.lower() for p in res.files}
    for f in companions:
        if f.nombre.lower() in have:
            continue
        path, entry = _portal_get(req, f)
        res.files.append(path)
        res.entries.append(entry)
        cache_entries.append(entry)
    write_manifest(req.cache_dir, cache_entries)
    return res


# ---------------------------------------------------------------------------
# static_url
# ---------------------------------------------------------------------------


def _render(template: str, req: FetchRequest) -> str:
    return template.format(date=req.day or date.today(), version=req.version)


def _check_pinned(req: FetchRequest, path: Path) -> None:
    if req.dataset.sha256 and sha256_file(path) != req.dataset.sha256:
        raise FetchError(
            f"{path.name}: sha256 does not match the one pinned in the registry"
        )


def fetch_static_url(req: FetchRequest) -> FetchResult:
    """``static_url`` backend."""
    url = _render(req.dataset.url, req)
    name = (
        _render(req.dataset.filename, req)
        if req.dataset.filename
        else Path(urlsplit(url).path).name
    )
    if not name:
        raise FetchError(f"cannot derive a file name from {strip_query(url)}")
    res = FetchResult()
    if req.dry_run:
        res.planned = [name]
        return res
    dest = req.cache_dir / name
    if req.refresh or not dest.is_file():
        http_download(url, dest)
    _check_pinned(req, dest)
    entry = ManifestEntry.for_file(dest, **req.meta(url=strip_query(url)))
    write_manifest(req.cache_dir, [entry])
    res.files.append(dest)
    res.entries.append(entry)
    return res


# ---------------------------------------------------------------------------
# manual / html_index
# ---------------------------------------------------------------------------


def manual_instructions(req: FetchRequest) -> str:
    """Text telling the user where to get the dataset by hand."""
    ds = req.dataset
    lines = [f"{req.operator}/{ds.name}: {ds.description}".rstrip(": ")]
    if ds.url:
        lines.append(f"  url:   {_render(ds.url, req)}")
    if ds.page:
        lines.append(f"  page:  {ds.page}")
    if ds.terms:
        lines.append(f"  terms: {ds.terms}")
    if not ds.redistributable:
        lines.append(
            "  NOT redistributable: keep it local; never commit or publish it."
        )
    if ds.filename:
        lines.append(f"  expected file name: {_render(ds.filename, req)}")
    lines.append(
        f"  then: gtopt-fetch {req.operator} get {ds.name}"
        + (f" --version {req.version}" if req.version else "")
        + " --file <downloaded file>"
    )
    return "\n".join(lines)


def fetch_manual(req: FetchRequest) -> FetchResult:
    """``manual`` / ``html_index`` backend: register a user-supplied file."""
    if req.manual_file is None:
        raise ManualActionRequired(manual_instructions(req))
    src = req.manual_file.expanduser()
    if not src.is_file():
        raise FetchError(f"--file {src}: not a file")
    name = _render(req.dataset.filename, req) if req.dataset.filename else src.name
    res = FetchResult()
    if req.dry_run:
        res.planned = [name]
        return res
    dest = req.cache_dir / name
    req.cache_dir.mkdir(parents=True, exist_ok=True)
    if not (dest.exists() and dest.samefile(src)):
        shutil.copy2(src, dest)
    _check_pinned(req, dest)
    entry = ManifestEntry.for_file(
        dest,
        source="manual",
        **req.meta(
            url=_render(req.dataset.url, req) if req.dataset.url else req.dataset.page
        ),
    )
    write_manifest(req.cache_dir, [entry])
    res.files.append(dest)
    res.entries.append(entry)
    return res


BACKEND_FUNCS: dict[str, Callable[[FetchRequest], FetchResult]] = {
    "cen_portal": fetch_cen_portal,
    "static_url": fetch_static_url,
    "manual": fetch_manual,
    "html_index": fetch_manual,
}


def fetch(req: FetchRequest) -> FetchResult:
    """Dispatch ``req`` to its backend."""
    func = BACKEND_FUNCS.get(req.dataset.backend)
    if func is None:
        raise FetchError(
            f"backend {req.dataset.backend!r} of {req.operator}/{req.dataset.name} "
            "has no downloader (API endpoints are read by the operator clients, "
            "e.g. cen2gtopt)"
        )
    return func(req)
