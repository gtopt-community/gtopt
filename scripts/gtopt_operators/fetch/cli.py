# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``gtopt-fetch`` -- download operator data through the source registry.

Examples::

    gtopt-fetch operators
    gtopt-fetch cen list                          # datasets of the CEN
    gtopt-fetch cen list pcp --since 2026-04-01   # published files
    gtopt-fetch cen get pcp --date 2026-04-22 --out case/source
    gtopt-fetch cen get plp_inputs --date 2026-05-15   # bundle in force
    gtopt-fetch aemo get iasr_workbook --version 2026  # manual: prints page
    gtopt-fetch aemo get iasr_workbook --version 2026 --file ~/Downloads/x.xlsx
    gtopt-fetch verify case/source                # sha256 vs manifest/lock

Downloads land in the unified cache (``$GTOPT_FETCH_CACHE``, default
``~/.cache/gtopt/fetch/<operator>/<dataset>/``) with a ``MANIFEST.jsonl``
(url, sha256, size, fetched_at, published_at).  ``--out DIR`` hard-links
(or copies) the delivered files into DIR and writes the manifest there.

Credentials are read only from the environment variables the registry
names (``gtopt-fetch <op> info <dataset>`` lists them); they are never
printed or logged.

Exit status: 0 ok, 1 error, 2 missing credential, 3 manual download
required (instructions printed on stderr).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from gtopt_operators.auth import MissingCredentialError, install_http_log_redaction
from gtopt_operators.fetch.backends import (
    FetchError,
    FetchRequest,
    ManualActionRequired,
    fetch,
    manual_instructions,
)
from gtopt_operators.fetch.cache import (
    cache_root,
    place,
    verify_dir,
    write_lock,
    write_manifest,
)
from gtopt_operators.registry import (
    RegistryError,
    SourceRegistry,
    available_operators,
    load_registry,
)

_LOG = logging.getLogger("gtopt_operators.fetch")

EXIT_OK, EXIT_ERROR, EXIT_CREDENTIAL, EXIT_MANUAL = 0, 1, 2, 3


def _iso_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {text!r}") from exc


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _cmd_operators(_args: argparse.Namespace) -> int:
    print(f"{'id':6s}  {'country':7s}  {'datasets':>8s}  name")
    for op in available_operators():
        reg = load_registry(op)
        info = reg.operator
        print(f"{op:6s}  {info.country:7s}  {len(reg.datasets):8d}  {info.name}")
    return EXIT_OK


def _list_datasets(reg: SourceRegistry) -> int:
    if not reg.datasets:
        print(f"{reg.operator.id}: no datasets registered")
        return EXIT_OK
    print(f"{'dataset':20s}  {'backend':10s}  {'date rule':19s}  description")
    for name, ds in sorted(reg.datasets.items()):
        flag = "" if ds.redistributable else "  [not redistributable]"
        print(
            f"{name:20s}  {ds.backend:10s}  {ds.date_rule:19s}  {ds.description}{flag}"
        )
    return EXIT_OK


def _list_portal(args: argparse.Namespace, reg: SourceRegistry) -> int:
    from cen2gtopt.pcp_archive import (  # pylint: disable=import-outside-toplevel
        fetch_index,
    )

    ds = reg.dataset(args.dataset)
    cats = {c.upper() for c in ds.categories}
    since = args.since.strftime("%Y%m%d") if args.since else ""
    until = args.until.strftime("%Y%m%d") if args.until else "99999999"
    rows = []
    for f in fetch_index(force=args.refresh):
        if cats and f.categoria.upper() not in cats:
            continue
        for rx in ds.regexes(None):
            m = rx.match(f.nombre)
            if m and since <= m.group(1) <= until:
                rows.append((m.group(1), f.nombre, f.categoria, f.fecha_publicacion))
                break
    for d, name, cat, pub in sorted(rows, reverse=True):
        print(f"{d[:4]}-{d[4:6]}-{d[6:]}  {name:40s}  {cat:8s}  {pub}")
    print(f"{len(rows)} files matched")
    return EXIT_OK


def _cmd_list(args: argparse.Namespace) -> int:
    reg = load_registry(args.operator)
    if not args.dataset:
        return _list_datasets(reg)
    ds = reg.dataset(args.dataset)
    if ds.backend == "cen_portal":
        return _list_portal(args, reg)
    req = FetchRequest(registry=reg, dataset=ds, version=args.version or "")
    print(manual_instructions(req) if ds.backend != "static_url" else ds.url)
    return EXIT_OK


def _cmd_info(args: argparse.Namespace) -> int:
    reg = load_registry(args.operator)
    ds = reg.dataset(args.dataset)
    info = reg.operator
    print(f"operator:        {info.id} ({info.name}, {info.country}, {info.timezone})")
    print(f"dataset:         {ds.name}")
    for label, value in (
        ("description", ds.description),
        ("backend", ds.backend),
        ("date rule", ds.date_rule),
        ("patterns", ", ".join(ds.patterns)),
        ("categories", ", ".join(ds.categories)),
        ("url", ds.url),
        ("page", ds.page),
        ("terms", ds.terms),
        ("consumer", ds.consumer),
        ("notes", ds.notes),
    ):
        if value:
            print(f"{label + ':':16s} {value}")
    print(f"redistributable: {'yes' if ds.redistributable else 'NO'}")
    if ds.auth:
        env = " or ".join(f"${v}" for v in reg.auth[ds.auth].env)
        print(f"credential:      {env}")
    if info.geo:
        print(f"geo-restricted:  served only to {info.geo} IP addresses")
    print(f"cache:           {(args.cache or cache_root()) / info.id / ds.name}")
    return EXIT_OK


def _cmd_get(args: argparse.Namespace) -> int:
    reg = load_registry(args.operator)
    req = FetchRequest(
        registry=reg,
        dataset=reg.dataset(args.dataset),
        day=args.date,
        version=args.version or "",
        refresh=args.refresh,
        dry_run=args.dry_run,
        manual_file=args.file,
        cache_root=args.cache,
    )
    res = fetch(req)
    if args.dry_run:
        for name in res.planned:
            print(name)
        return EXIT_OK
    delivered = res.files
    if args.out is not None:
        delivered = [place(p, args.out / p.name) for p in res.files]
        write_manifest(args.out, res.entries)
    for p in delivered:
        print(p)
    return EXIT_OK


def _cmd_verify(args: argparse.Namespace) -> int:
    directory: Path = args.directory
    if args.write_lock:
        print(write_lock(directory))
        return EXIT_OK
    n, issues = verify_dir(directory, args.lock)
    for issue in issues:
        print(f"FAIL  {issue.file}: {issue.problem}")
    print(f"{max(n - len({i.file for i in issues}), 0)}/{n} files ok")
    return EXIT_ERROR if issues else EXIT_OK


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Argument parser of ``gtopt-fetch``."""
    p = argparse.ArgumentParser(
        prog="gtopt-fetch",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p.add_argument("-q", "--quiet", action="store_true", help="warnings only")
    p.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="cache root (default: $GTOPT_FETCH_CACHE or ~/.cache/gtopt/fetch)",
    )
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    po = sub.add_parser("operators", help="list the registered operators")
    po.set_defaults(func=_cmd_operators)

    pv = sub.add_parser("verify", help="check files against MANIFEST.jsonl / lock")
    pv.add_argument("directory", type=Path)
    pv.add_argument(
        "--lock", type=Path, help="sources.lock (default: DIR/sources.lock)"
    )
    pv.add_argument(
        "--write-lock",
        action="store_true",
        help="write DIR/sources.lock from the manifest instead of verifying",
    )
    pv.set_defaults(func=_cmd_verify)

    for op in available_operators():
        try:
            name = load_registry(op).operator.name
        except RegistryError as exc:  # a broken third-party registry
            _LOG.warning("%s", exc)
            continue
        pop = sub.add_parser(op, help=f"datasets of {name}")
        pop.set_defaults(operator=op)
        osub = pop.add_subparsers(dest="action", metavar="ACTION")
        osub.required = True

        pl = osub.add_parser("list", help="list datasets, or the files of one")
        pl.add_argument("dataset", nargs="?")
        pl.add_argument("--since", type=_iso_date, help="YYYY-MM-DD (inclusive)")
        pl.add_argument("--until", type=_iso_date, help="YYYY-MM-DD (inclusive)")
        pl.add_argument("--version", help="edition, for templated datasets")
        pl.add_argument("--refresh", action="store_true", help="bypass index cache")
        pl.set_defaults(func=_cmd_list)

        pi = osub.add_parser("info", help="registry entry of one dataset")
        pi.add_argument("dataset")
        pi.set_defaults(func=_cmd_info)

        pg = osub.add_parser("get", help="download one dataset into the cache")
        pg.add_argument("dataset")
        pg.add_argument("--date", type=_iso_date, help="YYYY-MM-DD")
        pg.add_argument("--version", help="edition (e.g. ISP year)")
        pg.add_argument("--out", type=Path, help="also place the files in DIR")
        pg.add_argument(
            "--file", type=Path, help="register a hand-downloaded file (manual)"
        )
        pg.add_argument("--refresh", action="store_true", help="re-download")
        pg.add_argument(
            "--dry-run", action="store_true", help="only print what would be fetched"
        )
        pg.set_defaults(func=_cmd_get)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the ``gtopt-fetch`` console script."""
    args = build_parser().parse_args(argv)
    level = logging.WARNING if args.quiet else logging.INFO
    if args.verbose:
        level = logging.DEBUG
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    # urllib3 logs full request lines (with query keys) at DEBUG.
    install_http_log_redaction()
    try:
        return int(args.func(args))
    except MissingCredentialError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CREDENTIAL
    except ManualActionRequired as exc:
        print(f"manual download required:\n{exc}", file=sys.stderr)
        return EXIT_MANUAL
    except (FetchError, RegistryError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
