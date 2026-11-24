# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``cen2gtopt.pcp_archive`` — download CEN PLEXOS PCP base / solution files.

The Coordinador's "Programa de Operación" portal at
``https://programa.coordinador.cl/operacion/pcp/bases-modelo`` exposes
the *complete daily LP inputs and solution* of the Programa de
Coordinación de Predespacho (PCP).  These are the **ground-truth
oracle** for everything the marginal-emission pipeline reverse-
engineers from public CMG data.

Discovery mechanism
-------------------

The portal is an Angular SPA whose backend is an authenticated S3-
backed REST API at ``administracion.api.coordinador.cl/programa-
operacion/`` — the SPA authenticates with a browse-only key embedded
in its JavaScript bundle.  gtopt never stores that key: export it as
``$GTOPT_CEN_PORTAL_KEY`` (see ``gtopt_operators/cen/sources.toml``)
and it is used to call:

  GET …/programa-operacion/bucket-s3/s3/listFiles?user_key=<KEY>

…which returns ``[{ id, nombre, descripcion, enlaceDescarga,
fechaPublicacion, categoria, ... }]`` for every file currently
hosted, including pre-signed S3 URLs valid for ~12 h.

The presigned URLs let us download anonymously without per-request
auth.  When a URL expires the listing call is cheap (~0.5 s) and
returns a fresh URL.

File categories
---------------

The portal exposes ~1 900 files across these categories:

  PCP   — Programa de Coordinación de Predespacho (day-ahead full
          unit-commitment, hourly).  Includes per-day artefacts:
            * PLEXOS{YYYYMMDD}.zip      — full LP base + solution
              (DATOS{date}.zip + RES{date}.zip; 30-200 MB)
            * TCO{YYYYMMDD}.zip          — Política de Operación
            * OfertasSSCCAdj{date}.zip   — adjudicated SSCC offers
            * BasePCPDef{date}.xlsx      — input parameter sheets
  PID   — Programa Intra-Diario (hourly intra-day re-runs)
  PMP   — Programa de Coordinación de Mediano Plazo (weekly model)
  PLP   — Programa de Largo Plazo (long-term SDDP)
  IM    — Informes mensuales
  EC    — Estudios complementarios
  RE    — Reportes especiales

Use ``pcp_archive list`` to enumerate.

.. note:: Deprecated CLI.  ``gtopt-fetch cen ...`` (``gtopt_operators``)
   is the common download tool with a unified, checksummed cache; this
   module stays as the CEN portal backend and as a compatibility CLI.

Bundle structure (PLEXOS{date}.zip)
-----------------------------------

::

    PLEXOS{YYYYMMDD}.zip
    ├── DATOS{YYYYMMDD}.zip          # LP inputs
    │   ├── DBSEN_PRGDIARIO.xml      # PLEXOS XML object database
    │   │                            # (4 533 objects, 96 classes)
    │   ├── Gen_HeatRate.csv         # per-unit heat rate
    │   ├── Gen_StartCost.csv
    │   ├── Gen_MinStableLevel.csv   # pmin per unit
    │   ├── Gen_Rating.csv           # pmax per unit
    │   ├── Gen_VOMCharge.csv        # variable O&M
    │   ├── Gen_FuelTransportCharge.csv
    │   ├── Fuel_Price.csv           # monthly fuel prices
    │   ├── Fuel_MaxOfftakeWeek.csv  # TOP / take-or-pay constraints
    │   ├── Hydro_StoWaterValues.csv # water values per reservoir
    │   ├── Hydro_WaterFlows.csv     # natural inflow time series
    │   ├── Hydro_MaxVolume.csv / MinVolume.csv
    │   ├── Hydro_InitialVolume.csv
    │   ├── Hydro_AntucoBounds.csv   # Antuco-specific bounds
    │   ├── Hydro_EfficiencyIncr.csv
    │   ├── Hydro_MaxRampDay.csv
    │   ├── Lin_MaxRating.csv / Lin_MinRating.csv
    │   ├── Lin_Units.csv            # line topology + units
    │   ├── Nod_Load.csv             # per-bus per-hour load (wide)
    │   ├── Res_Requirement.csv      # reserve requirements
    │   ├── Res_Timeslice.csv
    │   ├── ReserveUsageTxCompensation.csv
    │   ├── SSCC_Activation_BESS.csv # BESS reserve participation
    │   ├── BESS_IniValue.csv        # initial state-of-charge
    │   └── CFdata/CPF/, CSF/, CTF/  # per-unit reserve capacity
    │                                # factors (primario/secundario/
    │                                # terciario)
    └── RES{YYYYMMDD}.zip            # Solution
        └── Model PRGdia_Full_Definitivo Solution/
            ├── …Solution.accdb       # PLEXOS solution Access DB
            │                         # (LMPs, dispatch, duals — the
            │                         # ground-truth marginal labels)
            ├── …Solution.zip         # supplementary
            └── …Log.txt              # solver log

Examples
--------

::

    # List all available PLEXOS PCP files
    python -m cen2gtopt.pcp_archive list --pattern PLEXOS

    # Download one specific PCP day
    python -m cen2gtopt.pcp_archive download --name PLEXOS20260407.zip \\
        --output ~/cen_pcp/

    # Download every PLEXOS file from a date range
    python -m cen2gtopt.pcp_archive download --pattern PLEXOS \\
        --since 2026-04-01 --until 2026-04-30 --output ~/cen_pcp/

    # Show metadata for one file
    python -m cen2gtopt.pcp_archive info PLEXOS20260407.zip

    # Refresh the index (forces a fresh API call)
    python -m cen2gtopt.pcp_archive refresh

    # One PCP day laid out as DATOS<d>.zip (+ RES<d>.zip) in a directory
    # (unwraps PLEXOS<d>.zip; newer days publish DATOS/RES separately)
    python -m cen2gtopt.pcp_archive pcp-day 2026-04-07 --dest ~/pcp_0407/

    # The PLP input bundle (IPLP*) in force on a date, extracted
    python -m cen2gtopt.pcp_archive plp-in-force 2026-05-15 --dry-run
    python -m cen2gtopt.pcp_archive plp-in-force 2026-05-15 --extract ~/plp/
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import re
import shutil
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

from cen2gtopt._cen_client import (
    add_insecure_tls_argument,
    apply_insecure_tls_argument,
    redact_secrets,
    tls_verify_default,
)
from gtopt_operators.auth import (
    MissingCredentialError,
    install_http_log_redaction,
    operator_credential,
    redact,
)
from gtopt_operators.registry import endpoint_url

_LOG = logging.getLogger("cen2gtopt.pcp_archive")
install_http_log_redaction()

#: Registry names (``gtopt_operators/cen/sources.toml``).
_OPERATOR = "cen"
_PORTAL_AUTH = "portal"

#: Index endpoint of the Programa de Operación portal.
LIST_URL = endpoint_url(_OPERATOR, "portal_list")

#: Origin header the portal API expects.
PORTAL_ORIGIN = endpoint_url(_OPERATOR, "portal_web")

#: Per-file endpoint the portal's download button uses; signs a fresh
#: S3 URL on every call (the index URLs expire after 12 h).
DOWNLOAD_URL = endpoint_url(_OPERATOR, "portal_presign")

#: On-disk index cache — the listFiles response is ~1 MB JSON.
DEFAULT_INDEX_PATH = Path.home() / ".cache" / "gtopt" / "cen2gtopt" / "pcp_index.json"

#: Default download root.
DEFAULT_DOWNLOAD_ROOT = Path.home() / ".cache" / "gtopt" / "cen2gtopt" / "pcp_archive"

#: How long the cached index is considered fresh.
INDEX_FRESH_S = 6 * 3600


@dataclass(slots=True)
class PcpFile:
    """One row of the listFiles response."""

    id: int
    nombre: str
    descripcion: str
    categoria: str
    path: str
    fecha_publicacion: str
    fecha_vigencia: str
    enlace_descarga: str
    vencimiento: str
    estado: str

    @classmethod
    def from_dict(cls, d: dict) -> "PcpFile":
        return cls(
            id=int(d.get("id", 0)),
            nombre=str(d.get("nombre", "")),
            descripcion=str(d.get("descripcion", "")),
            categoria=str(d.get("categoria", "")),
            path=str(d.get("path", "")),
            fecha_publicacion=str(d.get("fechaPublicacion", "")),
            fecha_vigencia=str(d.get("fechaVigencia", "")),
            enlace_descarga=str(d.get("enlaceDescarga", "")),
            vencimiento=str(d.get("vencimiento", "")),
            estado=str(d.get("estado", "")),
        )

    def date_iso(self) -> str | None:
        """Extract YYYY-MM-DD from a ``…YYYYMMDD…`` filename."""
        m = re.search(r"(\d{8})", self.nombre)
        if not m:
            return None
        s = m.group(1)
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"


# ---------------------------------------------------------------------------
# Index fetch + cache
# ---------------------------------------------------------------------------


def fetch_index(
    *,
    user_key: str | None = None,
    cache_path: Path | None = None,
    force: bool = False,
    timeout: int = 60,
) -> list[PcpFile]:
    """Return the list of available files.

    Uses an on-disk JSON cache (``DEFAULT_INDEX_PATH``).  Re-fetches
    when older than ``INDEX_FRESH_S`` (6 h) or when ``force=True``.
    Each call returns *fresh presigned URLs* in the live response —
    cached URLs may have expired (typically valid 12 h).  ``user_key``
    defaults to ``$GTOPT_CEN_PORTAL_KEY`` and is only read on a cache
    miss.
    """
    cache_path = cache_path or DEFAULT_INDEX_PATH
    if not force and cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < INDEX_FRESH_S:
            _LOG.debug("index cache hit (age=%.0fs)", age)
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            return [PcpFile.from_dict(d) for d in data]

    if user_key is None:
        # Only needed on a cache miss; raises MissingCredentialError
        # naming $GTOPT_CEN_PORTAL_KEY when it is not exported.
        user_key = operator_credential(_OPERATOR, _PORTAL_AUTH)
    _LOG.info("fetching CEN PCP index …")
    try:
        resp = requests.get(
            LIST_URL,
            params={"user_key": user_key},
            headers={"Origin": PORTAL_ORIGIN},
            timeout=timeout,
            verify=tls_verify_default(),
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        # The request URL carries the key: never let it reach a log.
        raise requests.RequestException(redact(str(exc))) from None
    rows = resp.json()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(rows), encoding="utf-8")
    _LOG.info("  → %d files indexed → %s", len(rows), cache_path)
    return [PcpFile.from_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def filter_files(
    files: list[PcpFile],
    *,
    name_pattern: str | None = None,
    category: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[PcpFile]:
    """AND-filter the file list."""
    out = files
    if name_pattern:
        rx = re.compile(name_pattern, re.IGNORECASE)
        out = [f for f in out if rx.search(f.nombre)]
    if category:
        out = [f for f in out if f.categoria.upper() == category.upper()]
    if since or until:
        new_out: list[PcpFile] = []
        for f in out:
            d = f.date_iso()
            if d is None:
                continue
            if since and d < since:
                continue
            if until and d > until:
                continue
            new_out.append(f)
        out = new_out
    return out


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def fresh_download_url(
    f: PcpFile,
    *,
    user_key: str | None = None,
    timeout: int = 60,
) -> str:
    """Ask the portal for a freshly signed S3 URL for ``f``.

    The ``enlaceDescarga`` in the listFiles index is signed when the file
    is published and expires after 12 h; the portal does not always
    re-sign it.  The SPA's download button instead calls
    ``presigned-url-download?encodedKey=<base64(s3 key)>``, which signs a
    new URL on every request.  Falls back to the indexed URL on failure.
    """
    key = urlparse(f.enlace_descarga).path.lstrip("/") or (f"{f.categoria}/{f.nombre}")
    try:
        if user_key is None:
            user_key = operator_credential(_OPERATOR, _PORTAL_AUTH)
        resp = requests.get(
            DOWNLOAD_URL,
            params={
                "encodedKey": base64.b64encode(key.encode()).decode(),
                "user_key": user_key,
            },
            headers={"Origin": PORTAL_ORIGIN},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        url = data.get("presignedUrlDownload") if isinstance(data, dict) else None
        if url:
            return str(url)
    except (requests.RequestException, ValueError, MissingCredentialError) as exc:
        _LOG.warning(
            "fresh URL for %s failed (%s); using index URL", key, redact(str(exc))
        )
    return f.enlace_descarga


def download_one(
    f: PcpFile,
    *,
    output_dir: Path,
    skip_existing: bool = True,
    timeout: int = 600,
) -> Path:
    """Download ``f`` into ``output_dir / f.categoria / f.nombre``.
    Returns the local path."""
    out = output_dir / f.categoria / f.nombre
    out.parent.mkdir(parents=True, exist_ok=True)
    if skip_existing and out.exists() and out.stat().st_size > 0:
        _LOG.debug("skip existing: %s", out)
        return out
    _LOG.info("download %s (%s) …", f.nombre, f.descripcion[:60])
    try:
        with requests.get(
            fresh_download_url(f),
            stream=True,
            timeout=timeout,
            verify=tls_verify_default(),
        ) as r:
            r.raise_for_status()
            tmp = out.with_suffix(out.suffix + ".part")
            with tmp.open("wb") as fp:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        fp.write(chunk)
            tmp.rename(out)
    except requests.RequestException as exc:
        # Pre-signed URLs carry S3 signatures: redact before logging.
        raise requests.RequestException(redact(str(exc))) from None
    _LOG.info("  → %s (%.1f MB)", out, out.stat().st_size / 1e6)
    return out


# ---------------------------------------------------------------------------
# PLP bundle in force on a date
# ---------------------------------------------------------------------------

#: File extensions of the PLP input files (plain or already compressed).
PLP_INPUT_EXTS = (".dat", ".csv", ".prn")

#: A file every PLP input set contains; marks the data directory.
PLP_MARKER = "plpblo.dat"


def select_plp_bundle(
    files: list[PcpFile], date: str, name: str | None = None
) -> PcpFile | None:
    """Pick the PLP input bundle in force on ``date`` (``YYYY-MM-DD``).

    PLP runs are not daily: among the ``PLP``-category files whose name
    starts with ``IPLP`` (inputs; ``OPLP`` are results) take the one with
    the latest file date on or before ``date``.  Ties (re-publications,
    or ``IPLP2026…`` vs ``IPLP_2026…``) go to the latest
    ``fecha_publicacion``.  ``name`` forces an exact file name (latest
    publication wins).
    """
    if name:
        named = [f for f in files if f.nombre == name]
        return max(named, key=lambda f: f.fecha_publicacion) if named else None
    cands = [
        f
        for f in files
        if f.categoria.upper() == "PLP"
        and f.nombre.upper().startswith("IPLP")
        and (f.date_iso() or "9999") <= date
    ]
    if not cands:
        return None
    return max(cands, key=lambda f: (f.date_iso() or "", f.fecha_publicacion))


def _is_plp_input(name: str) -> bool:
    low = name.lower()
    return any(low.endswith(e) or f"{e}." in low for e in PLP_INPUT_EXTS)


def extract_plp_inputs(bundle: Path, dest: Path) -> list[Path]:
    """Copy the PLP input files of ``bundle`` flat into ``dest``.

    ``bundle`` is a PLP zip (nested zips are unpacked too) or a
    directory.  The data directory is the shallowest one holding
    ``plpblo.dat`` (plain or compressed); its ``.dat``/``.csv``/``.prn``
    files (and their ``.xz`` parts) are copied.  Returns the copied
    paths.
    """
    with tempfile.TemporaryDirectory(prefix="plp-unpack-") as tmp:
        root = bundle
        if not bundle.is_dir():
            root = Path(tmp)
            with zipfile.ZipFile(bundle) as zf:
                zf.extractall(root)
            for inner in list(root.rglob("*.zip")):
                with zipfile.ZipFile(inner) as zf:
                    zf.extractall(inner.with_suffix(""))
        hits = sorted(
            {
                p.parent
                for p in root.rglob("*")
                if p.name.lower().startswith(PLP_MARKER)
            },
            key=lambda p: len(p.parts),
        )
        if not hits:
            raise FileNotFoundError(f"no {PLP_MARKER} found in {bundle}")
        dest.mkdir(parents=True, exist_ok=True)
        out = []
        for f in sorted(hits[0].iterdir()):
            if f.is_file() and _is_plp_input(f.name):
                out.append(Path(shutil.copy2(f, dest / f.name)))
        return out


# ---------------------------------------------------------------------------
# One PCP day: DATOS (+ RES) laid out in a directory
# ---------------------------------------------------------------------------


def _extract_member(zip_path: Path, name: str, dest: Path) -> Path | None:
    """Extract the member whose basename is ``name`` (case-insensitive)."""
    with zipfile.ZipFile(zip_path) as zf:
        for m in zf.namelist():
            if Path(m).name.lower() == name.lower():
                out = dest / Path(m).name
                with zf.open(m) as fi, out.open("wb") as fo:
                    shutil.copyfileobj(fi, fo, 1 << 20)
                return out
    return None


def fetch_pcp_day(
    date: str,
    dest: Path,
    *,
    files: list[PcpFile] | None = None,
    download_root: Path = DEFAULT_DOWNLOAD_ROOT,
) -> tuple[Path, Path | None]:
    """Lay out ``DATOS<d>.zip`` and ``RES<d>.zip`` of a PCP day in ``dest``.

    Older days are published as one ``PLEXOS<d>.zip`` wrapper (unwrapped
    here), newer ones as separate ``DATOS<d>.zip`` / ``RES<d>.zip``.
    Downloads go through the cache under ``download_root``.  Returns
    ``(datos, res)``; ``res`` is ``None`` when the solution is not
    published.
    """
    d8 = date.replace("-", "")
    files = fetch_index() if files is None else files
    by_name: dict[str, PcpFile] = {}
    for f in files:
        prev = by_name.get(f.nombre)
        if prev is None or f.fecha_publicacion > prev.fecha_publicacion:
            by_name[f.nombre] = f
    dest.mkdir(parents=True, exist_ok=True)
    datos = dest / f"DATOS{d8}.zip"
    res: Path | None = None
    if f"PLEXOS{d8}.zip" in by_name:
        wrapper = download_one(by_name[f"PLEXOS{d8}.zip"], output_dir=download_root)
        if _extract_member(wrapper, datos.name, dest) is None:
            raise FileNotFoundError(f"{datos.name} not inside {wrapper}")
        res = _extract_member(wrapper, f"RES{d8}.zip", dest)
    elif f"DATOS{d8}.zip" in by_name:
        got = download_one(by_name[f"DATOS{d8}.zip"], output_dir=download_root)
        shutil.copyfile(got, datos)
        if f"RES{d8}.zip" in by_name:
            got = download_one(by_name[f"RES{d8}.zip"], output_dir=download_root)
            res = Path(shutil.copyfile(got, dest / f"RES{d8}.zip"))
    else:
        raise FileNotFoundError(f"no PLEXOS{d8}.zip / DATOS{d8}.zip in the CEN index")
    return datos, res


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_table(rows: list[PcpFile], limit: int | None = 30) -> None:
    print(f"{'name':40s}  {'category':6s}  {'date':10s}  description")
    print("-" * 110)
    for r in rows[:limit] if limit else rows:
        print(
            f"{r.nombre:40s}  {r.categoria:6s}  "
            f"{(r.date_iso() or '?'):10s}  {r.descripcion[:60]}"
        )
    if limit and len(rows) > limit:
        print(f"… and {len(rows) - limit} more (use --limit 0 to show all)")


def _cmd_list(args: argparse.Namespace) -> int:
    files = fetch_index(force=args.refresh)
    files = filter_files(
        files,
        name_pattern=args.pattern,
        category=args.category,
        since=args.since,
        until=args.until,
    )
    files.sort(key=lambda f: (f.date_iso() or "", f.nombre), reverse=True)
    _print_table(files, limit=None if args.limit == 0 else args.limit)
    print()
    print(f"{len(files)} files matched")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    files = fetch_index()
    matches = [f for f in files if f.nombre == args.name]
    if not matches:
        # Try a substring match
        matches = [f for f in files if args.name.lower() in f.nombre.lower()]
    if not matches:
        print(f"no file matched {args.name!r}")
        return 1
    for f in matches[:5]:
        print(f"=== {f.nombre} ===")
        print(f"  category:  {f.categoria}")
        print(f"  path:      {f.path}")
        print(f"  date:      {f.date_iso()}")
        print(f"  state:     {f.estado}")
        print(f"  published: {f.fecha_publicacion}")
        print(f"  expires:   {f.vencimiento}")
        print(f"  description: {f.descripcion}")
        print()
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    files = fetch_index(force=args.refresh)
    if args.name:
        files = [f for f in files if f.nombre == args.name]
        if not files:
            print(f"no file matched {args.name!r}")
            return 1
    else:
        files = filter_files(
            files,
            name_pattern=args.pattern,
            category=args.category,
            since=args.since,
            until=args.until,
        )
    if not files:
        print("no files matched the filter")
        return 1
    print(f"will download {len(files)} files into {args.output}")
    failures = 0
    for f in files:
        try:
            download_one(
                f,
                output_dir=args.output,
                skip_existing=not args.force,
            )
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-except
            failures += 1
            _LOG.error("failed %s: %s", f.nombre, redact_secrets(exc))
    print(f"  done: {len(files) - failures} ok, {failures} failed")
    return 1 if failures else 0


def _cmd_refresh(_args: argparse.Namespace) -> int:
    files = fetch_index(force=True)
    print(f"index refreshed — {len(files)} files cached at {DEFAULT_INDEX_PATH}")
    return 0


def _cmd_pcp_day(args: argparse.Namespace) -> int:
    datos, res = fetch_pcp_day(
        args.date,
        args.dest,
        files=fetch_index(force=args.refresh),
        download_root=args.output,
    )
    print(datos)
    if res is not None:
        print(res)
    else:
        _LOG.warning("no RES%s.zip published", args.date.replace("-", ""))
    return 0


def _cmd_plp_in_force(args: argparse.Namespace) -> int:
    pick = select_plp_bundle(fetch_index(force=args.refresh), args.date, args.name)
    if pick is None:
        print(f"no IPLP bundle published on or before {args.date}", file=sys.stderr)
        return 1
    print(
        f"selected {pick.nombre} (file date {pick.date_iso()}, "
        f"published {pick.fecha_publicacion}) for {args.date}",
        file=sys.stderr,
    )
    if args.dry_run:
        print(pick.nombre)
        return 0
    local = download_one(pick, output_dir=args.output)
    if args.extract:
        n = len(extract_plp_inputs(local, args.extract))
        print(f"extracted {n} PLP input files into {args.extract}", file=sys.stderr)
    print(local)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cen2gtopt.pcp_archive",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_insecure_tls_argument(p)
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument(
        "-V", "--version", action="version", version="cen2gtopt.pcp_archive 0.1.0"
    )

    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    pl = sub.add_parser("list", help="List available files")
    pl.add_argument("--pattern", help="Regex on file name (case-insensitive).")
    pl.add_argument(
        "--category", choices=("PCP", "PID", "PMP", "PLP", "IM", "IA", "EC", "RE")
    )
    pl.add_argument("--since", help="YYYY-MM-DD (inclusive)")
    pl.add_argument("--until", help="YYYY-MM-DD (inclusive)")
    pl.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Max rows to print (0 = no limit; default 30).",
    )
    pl.add_argument(
        "--refresh", action="store_true", help="Bypass the 6 h index cache."
    )
    pl.set_defaults(func=_cmd_list)

    pi = sub.add_parser("info", help="Show metadata for one file")
    pi.add_argument("name", help="File name (or substring).")
    pi.set_defaults(func=_cmd_info)

    pd_ = sub.add_parser("download", help="Download files matching a filter")
    pd_.add_argument("--name", help="Exact file name.")
    pd_.add_argument("--pattern", help="Regex on file name.")
    pd_.add_argument(
        "--category", choices=("PCP", "PID", "PMP", "PLP", "IM", "IA", "EC", "RE")
    )
    pd_.add_argument("--since", help="YYYY-MM-DD (inclusive)")
    pd_.add_argument("--until", help="YYYY-MM-DD (inclusive)")
    pd_.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DOWNLOAD_ROOT,
        help=f"Download root (default: {DEFAULT_DOWNLOAD_ROOT})",
    )
    pd_.add_argument("--force", action="store_true", help="Overwrite existing files.")
    pd_.add_argument(
        "--refresh", action="store_true", help="Bypass the 6 h index cache."
    )
    pd_.set_defaults(func=_cmd_download)

    pr = sub.add_parser("refresh", help="Force-refresh the index cache")
    pr.set_defaults(func=_cmd_refresh)

    pp = sub.add_parser(
        "pcp-day", help="Lay out DATOS<d>.zip (+ RES<d>.zip) of one PCP day"
    )
    pp.add_argument("date", help="YYYY-MM-DD")
    pp.add_argument("--dest", type=Path, required=True, help="Target directory.")
    pp.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DOWNLOAD_ROOT,
        help=f"Download cache root (default: {DEFAULT_DOWNLOAD_ROOT})",
    )
    pp.add_argument("--refresh", action="store_true", help="Bypass the index cache.")
    pp.set_defaults(func=_cmd_pcp_day)

    pf = sub.add_parser(
        "plp-in-force",
        help="Select (and download) the PLP input bundle in force on a date",
    )
    pf.add_argument("date", help="YYYY-MM-DD")
    pf.add_argument("--name", help="Force an exact file name (IPLP_20260401.zip).")
    pf.add_argument(
        "--dry-run", action="store_true", help="Only print the selected name."
    )
    pf.add_argument(
        "--extract", type=Path, help="Copy the PLP input files into this directory."
    )
    pf.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DOWNLOAD_ROOT,
        help=f"Download cache root (default: {DEFAULT_DOWNLOAD_ROOT})",
    )
    pf.add_argument("--refresh", action="store_true", help="Bypass the index cache.")
    pf.set_defaults(func=_cmd_plp_in_force)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    apply_insecure_tls_argument(args)
    print(
        "note: cen2gtopt-pcp-archive is deprecated; use `gtopt-fetch cen ...`",
        file=sys.stderr,
    )
    if args.quiet:
        log_level = logging.WARNING
    elif args.verbose:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except MissingCredentialError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
