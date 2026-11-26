# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Main entry point for run_gtopt — smart gtopt wrapper."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

from gtopt_config import get_version
from gtopt_shared.status import STATUS_FILE, read_status
from gtopt_shared.gtopt_binary import (
    find_gtopt_binary,
    find_plexos2gtopt,
    find_plp2gtopt,
)

from ._attach import (
    list_runs,
    print_run_list,
    resolve_target,
    run_attach_loop,
)
from ._checks import (
    available_checks,
    run_postflight_checks,
    run_preflight_checks,
)
from ._detect import (
    CaseType,
    detect_case_type,
    infer_gtopt_dir,
    infer_plexos_gtopt_dir,
    plexos_stem,
)
from ._environment import detect_compression_codec, detect_cpu_count
from ._outcome import NOT_CONVERGED, STOPPED, classify
from ._paths import remove_stale_run_files, resolve_output_dir
from ._runner import (
    report_solution,
    run_gtopt,
    run_plexos2gtopt,
    run_plp2gtopt,
)
from ._sanitize import sanitize_json
from ._tui import UI_MODES, is_interactive, set_ui_mode

__version__ = get_version()

log = logging.getLogger(__name__)


_DESCRIPTION = """\
Smart wrapper for the gtopt solver.

Detects PLP and gtopt case directories, runs conversions when needed,
and passes appropriate runtime options to the solver.

Checks (see --list-checks; --no-check disables all of them):
  json_syntax    JSON syntax validation               (pre, default on)
  input_files    input file existence and readability (pre, default on)
  output_dir     output directory writability         (pre, default on)
  check_json     gtopt_check_json --info + full validation
                                                      (pre, opt-in: slow;
                                    --enable-check check_json)
  check_lp       analyse error LP files with gtopt_check_lp  (post, on)
  check_output   validate the solver output                  (post, on)

Examples:
  run_gtopt plp_case_2y            # PLP case: convert + solve
  run_gtopt gtopt_case_2y          # gtopt case: solve directly
  run_gtopt gtopt_case_2y.json     # explicit JSON file: pass to gtopt
  run_gtopt                        # auto-detect from CWD
"""

_EPILOG = """\
Passing options to gtopt:
  Everything after a bare "--" goes to the gtopt binary unchanged:
    run_gtopt case -- --solver highs
    run_gtopt case -- --set sddp_options.max_iterations=20
    run_gtopt case -- --set use_single_bus=true --stats
  See "gtopt --help" for the solver's own options.

Watching a run:
  run_gtopt --list           live gtopt runs (from the run registry)
  run_gtopt --attach [PID]   this dashboard on an already-running gtopt
  gtopt_monitor --status-file <output>/solver_status.json
                             charts of the same status file (matplotlib;
                             --no-gui prints one line per poll)

Interrupting a run:
  1st Ctrl-C   graceful stop: SDDP/cascade finish the current iteration
               and save cuts (a monolithic solve cannot stop early)
  2nd Ctrl-C   terminate gtopt (SIGTERM)
  3rd Ctrl-C   kill gtopt (SIGKILL); also sent 10 s after an unanswered
               SIGTERM.  In the dashboard, 's' = graceful stop and 'q' =
               terminate (asks y/N once the run is older than a minute).

Exit status:
  0        gtopt succeeded (also after a graceful stop; the final banner
           says whether the run converged)
  1        gtopt: non-optimal solution or CLI error
  2        gtopt: input error; run_gtopt: usage error, missing gtopt /
           converter binary, failed pre-flight checks or JSON sanitizing
  3        gtopt: internal error (unexpected exception, solver crash)
  N        a converter's (plp2gtopt / plexos2gtopt) non-zero exit code
  4        only with --fail-on-not-converged: gtopt exited 0 but the SDDP
           run did not converge (iteration limit or graceful stop)
  127      the gtopt binary could not be started
  128+N    gtopt was killed by signal N (134 SIGABRT, 137 SIGKILL / out
           of memory, 139 SIGSEGV, 143 SIGTERM); a Ctrl-C before gtopt
           starts (pre-flight, conversion) exits 130
"""


_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


def _signal_handler(sig, _frame):
    signame = signal.strsignal(sig)
    print(f"\nCaught signal {signame}. Exiting...", file=sys.stderr)
    sys.exit(128 + sig)


def make_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="run_gtopt",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "case",
        nargs="?",
        default=None,
        metavar="CASE",
        help=(
            "PLP directory, gtopt directory, or JSON file. "
            "When omitted, auto-detects from the current directory."
        ),
    )
    parser.add_argument(
        "-t",
        "--threads",
        type=int,
        default=None,
        metavar="N",
        help=(
            "solver thread count, written to solver_options.threads in the "
            "sanitized JSON (default: the CPU count; plain gtopt would use "
            "the solver's own default)"
        ),
    )
    parser.add_argument(
        "--solver",
        default=None,
        metavar="NAME",
        help=(
            "LP solver plugin, forwarded as gtopt --solver NAME (cplex, "
            "highs, mindopt, cbc, clp, ...; default: $GTOPT_SOLVER or "
            "gtopt's auto-detection; see 'gtopt --solvers')"
        ),
    )
    parser.add_argument(
        "--kappa-mode",
        choices=("off", "estimate", "exact"),
        default=None,
        help=(
            "basis condition number (kappa) after each solve, forwarded as "
            "gtopt --set solver_options.kappa_mode=MODE.  Use 'exact' for "
            "verification / study runs (costly on large cases); default: "
            "$GTOPT_KAPPA_MODE or gtopt's compiled default (off in release "
            "builds)"
        ),
    )
    parser.add_argument(
        "-C",
        "--compression",
        default=None,
        metavar="CODEC",
        help=(
            "override the output compression codec (Parquet: zstd, snappy, "
            "lz4, gzip, none; CSV: zstd, gzip, none).  Default: leave the "
            "case's output_compression / gtopt's own default (zstd)"
        ),
    )
    parser.add_argument(
        "--convert-dir",
        "-o",
        "--output-dir",
        dest="output_dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "where PLP / PLEXOS conversion writes the gtopt case "
            "(default: gtopt_NAME next to plp_NAME).  Not gtopt's solve "
            "output directory: use -- --set output_directory=DIR for that.  "
            "-o / --output-dir are kept as aliases"
        ),
    )
    parser.add_argument(
        "--plp-args",
        default=None,
        metavar="ARGS",
        help=(
            "extra arguments to pass to plp2gtopt "
            "(quote the whole string, e.g. '--plp-args \"-y 1 -s 5\"')"
        ),
    )
    parser.add_argument(
        "--plexos-args",
        default=None,
        metavar="ARGS",
        help=(
            "extra arguments to pass to plexos2gtopt "
            "(quote the whole string, e.g. "
            "'--plexos-args \"--pampl-uc-mode soft\"')"
        ),
    )
    parser.add_argument(
        "--check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "run the pre-/post-flight checks: JSON syntax, input files, "
            "output directory, error-LP analysis, output validation "
            "(default: enabled; the slow gtopt_check_json validation is "
            "opt-in via --enable-check check_json)"
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="abort on any warning during pre-flight checks",
    )
    all_checks = available_checks()
    all_names = sorted(set(all_checks["pre"]) | set(all_checks["post"]))
    parser.add_argument(
        "--enable-check",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "run only the named check(s) (may repeat; replaces the default "
            f"set). Available: {', '.join(all_names)}; see --list-checks"
        ),
    )
    parser.add_argument(
        "--disable-check",
        action="append",
        default=None,
        metavar="NAME",
        help="disable a specific check (may repeat)",
    )
    parser.add_argument(
        "--list-checks",
        action="store_true",
        default=False,
        help="list available checks and exit",
    )
    # ── Attach / discovery (no subprocess; observer-mode TUI) ──
    # The C++ solver writes ``$XDG_CACHE_HOME/gtopt/runs/<pid>`` on its
    # first status flush.  These flags let users see which runs are
    # live and attach the same Rich TUI without launching gtopt.
    parser.add_argument(
        "--list",
        dest="list_runs",
        action="store_true",
        default=False,
        help=(
            "list live gtopt runs (PID, case, status, gap, elapsed) and exit. "
            "Reads $XDG_CACHE_HOME/gtopt/runs/."
        ),
    )
    parser.add_argument(
        "--attach",
        nargs="?",
        const="",
        default=None,
        metavar="PID|PATH",
        help=(
            "attach the dashboard to an already-running gtopt instance. "
            "Argument is a PID from --list, or a path to the case directory "
            "/ output directory / solver_status.json; without one, attaches "
            "to the only live run (or lists them when there are several). "
            "The TUI runs in "
            "observer mode: 'q' detaches without killing the solver, 's' "
            "still requests a graceful stop."
        ),
    )
    parser.add_argument(
        "--convert-only",
        action="store_true",
        default=False,
        help="convert PLP case but do not run gtopt",
    )
    parser.add_argument(
        "--export-json",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "write the sanitized planning JSON to FILE and keep it. "
            "The sanitized JSON includes all runtime fixes "
            "(compression codec, threads, directory overrides)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="print commands without executing",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=_LOG_LEVELS,
        metavar="LEVEL",
        help="logging verbosity: DEBUG, INFO, WARNING, ERROR (default: %(default)s)",
    )
    # Deprecated: gtopt's own -l means --lp-file, so run_gtopt's -l was a
    # trap.  Still accepted (with a warning) for existing scripts.
    parser.add_argument(
        "-l",
        dest="log_level_short",
        default=None,
        choices=_LOG_LEVELS,
        metavar="LEVEL",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help=(
            "disable coloured output (also honoured: the NO_COLOR "
            "environment variable, TERM=dumb, and non-terminal output)"
        ),
    )
    parser.add_argument(
        "--fail-on-not-converged",
        action="store_true",
        default=False,
        help=(
            f"exit {EXIT_NOT_CONVERGED} when gtopt succeeds but the SDDP run "
            "did not converge (iteration limit or graceful stop); by default "
            "that is exit 0 and only the banner says so"
        ),
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        default=False,
        help=(
            "draw the dashboard with ASCII characters only (automatic when "
            "the locale is not UTF-8; also GTOPT_TUI_ASCII=1)"
        ),
    )
    parser.add_argument(
        "--ui",
        choices=UI_MODES,
        default="auto",
        help=(
            "display mode: 'tui' = live dashboard, 'plain' = line-oriented "
            "log output (for emacs, CI, script(1), pipes), 'auto' = "
            "dashboard when stdout is a terminal and TERM is not 'dumb' "
            "(default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--no-tui",
        "--plain",
        dest="ui",
        action="store_const",
        const="plain",
        help="same as --ui plain",
    )
    return parser


#: Checks that only run with --enable-check (check_json is slow: it
#: invokes gtopt_check_json).
DEFAULT_DISABLED_CHECKS = frozenset({"check_json"})

_CHECK_DESCRIPTIONS = {
    "json_syntax": "JSON syntax validation",
    "input_files": "input file existence and readability",
    "output_dir": "output directory writability",
    "check_json": "gtopt_check_json --info + full validation (slow)",
    "check_lp": "analyse error LP files with gtopt_check_lp",
    "check_output": "validate the solver output",
}


def print_check_table() -> None:
    """``--list-checks``: one row per check with stage and default."""
    checks = available_checks()
    rows = [
        (name, stage, "off" if name in DEFAULT_DISABLED_CHECKS else "on")
        for stage in ("pre", "post")
        for name in checks[stage]
    ]
    width = max([len("CHECK")] + [len(name) for name, _, _ in rows])
    print(f"{'CHECK':<{width}}  STAGE  DEFAULT  DESCRIPTION")
    for name, stage, default in rows:
        desc = _CHECK_DESCRIPTIONS.get(name, "")
        print(f"{name:<{width}}  {stage:<5}  {default:<7}  {desc}".rstrip())
    print(
        "\npre = before gtopt, post = after it.  --enable-check NAME runs "
        "only the named checks;\n--disable-check NAME skips one; "
        "--no-check skips all."
    )


#: Exit code for --fail-on-not-converged (gtopt itself never returns 4).
EXIT_NOT_CONVERGED = 4

#: File mtimes come from the kernel's coarse clock, which can lag
#: ``time.time()`` by a few milliseconds: a status file written right after
#: the run started may carry an mtime slightly *before* ``since``.  Only a
#: file older than this slack counts as stale.
_STALE_MTIME_SLACK_S = 1.0


def convergence_exit_code(rc: int, output_dir: Path | None, since: float) -> int:
    """*rc*, or :data:`EXIT_NOT_CONVERGED` when gtopt exited 0 but this
    run's ``solver_status.json`` says SDDP stopped without converging."""
    if rc != 0 or output_dir is None:
        return rc
    # None: no status, unreadable, or a stale file from an earlier run
    # (older than the start minus the coarse-mtime slack).
    status = read_status(output_dir / STATUS_FILE, since=since - _STALE_MTIME_SLACK_S)
    if status is None:
        return rc
    kind = classify(0, status).kind
    if kind in (NOT_CONVERGED, STOPPED):
        print(
            f"run_gtopt: exit {EXIT_NOT_CONVERGED}: SDDP did not converge "
            "(--fail-on-not-converged)",
            file=sys.stderr,
        )
        return EXIT_NOT_CONVERGED
    return rc


def _split_extra_args(raw: str | None) -> list[str]:
    """Split a quoted argument string into a list."""
    if not raw:
        return []
    import shlex  # noqa: PLC0415

    return shlex.split(raw)


def run_summary(
    gtopt_bin: str,
    case_name: str,
    threads: int | None,
    output_dir: Path,
    gtopt_extra: list[str],
) -> str:
    """The one pre-run line: binary, solver, threads, case, output dir."""
    solver = os.environ.get("GTOPT_SOLVER") or "auto"
    for i, arg in enumerate(gtopt_extra):
        if arg == "--solver" and i + 1 < len(gtopt_extra):
            solver = gtopt_extra[i + 1]
        elif arg.startswith("--solver="):
            solver = arg.split("=", 1)[1]
    return (
        f"gtopt {gtopt_bin} | solver {solver} | {threads} threads | "
        f"case {case_name} | output {output_dir}"
    )


def _sanitize_preview(
    json_file: Path, compression: str | None, threads: int | None
) -> tuple[Path | None, list[str]]:
    """What a real run would change in the planning JSON.

    Runs the sanitizer into a throw-away file and returns the path the
    real run would hand to gtopt (``None`` = the case itself, unchanged)
    and one ``key: old -> new`` line per changed option.
    """
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        preview = Path(tmp) / json_file.name
        out = sanitize_json(
            json_file, compression=compression, threads=threads, export_path=preview
        )
        if out is None or out == json_file:
            return None, []
        before = json.loads(json_file.read_text(encoding="utf-8")).get("options", {})
        after = json.loads(preview.read_text(encoding="utf-8")).get("options", {})

    changes: list[str] = []

    def _diff(prefix: str, old: dict, new: dict) -> None:
        for key in sorted(set(old) | set(new)):
            o, n = old.get(key), new.get(key)
            if isinstance(o, dict) or isinstance(n, dict):
                _diff(f"{prefix}{key}.", o or {}, n or {})
            elif o != n:
                changes.append(f"{prefix}{key}: {o!r} -> {n!r}")

    _diff("options.", before, after)
    return json_file.with_stem(json_file.stem + "_sanitized"), changes


def _print_dry_run(
    gtopt_bin: str,
    json_file: Path,
    gtopt_dir: Path,
    compression: str | None,
    threads: int | None,
    gtopt_extra: list[str],
) -> None:
    """Print the exact gtopt argv a real run would execute.

    Threads and compression are never gtopt flags here: a real run bakes
    them (and the absolute input/output directories) into a sanitized
    copy of the planning JSON, whose changes are listed first.
    """
    import shlex  # noqa: PLC0415

    target: Path = gtopt_dir
    if json_file.is_file():
        sanitized, changes = _sanitize_preview(json_file, compression, threads)
        for change in changes:
            print(f"[dry-run] sanitize {change}")
        if sanitized is not None:
            target = sanitized
    argv = [gtopt_bin, str(target), *gtopt_extra]
    print(f"[dry-run] {shlex.join(argv)}")


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and orchestrate the run."""
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    parser = make_parser()

    # Split on '--' to separate run_gtopt args from gtopt passthrough args
    if argv is None:
        argv = sys.argv[1:]

    gtopt_extra: list[str] = []
    if "--" in argv:
        sep = argv.index("--")
        gtopt_extra = argv[sep + 1 :]
        argv = argv[:sep]

    args = parser.parse_args(argv)
    if args.log_level_short is not None:
        print(
            "run_gtopt: warning: -l is deprecated (gtopt's -l means "
            "--lp-file); use --log-level",
            file=sys.stderr,
        )
        args.log_level = args.log_level_short
    if args.solver and not any(
        a == "--solver" or a.startswith("--solver=") for a in gtopt_extra
    ):
        gtopt_extra = ["--solver", args.solver, *gtopt_extra]
    if args.kappa_mode is not None:
        gtopt_extra = [
            *gtopt_extra,
            "--set",
            f"solver_options.kappa_mode={args.kappa_mode}",
        ]

    # ── Display: colour and dashboard selection ──
    # Rich (and every Console created by the checks / report) honours
    # NO_COLOR, so --no-color simply sets it for this process tree.
    if args.no_color:
        os.environ["NO_COLOR"] = "1"
    if args.ascii:
        os.environ["GTOPT_TUI_ASCII"] = "1"
    set_ui_mode(args.ui)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # When running interactively the Rich TUI takes over the terminal;
    # suppress pre-TUI INFO chatter so the dashboard appears cleanly.
    if is_interactive() and args.log_level == "INFO":
        for handler in logging.root.handlers:
            handler.setLevel(logging.WARNING)

    # ── List checks ──
    if args.list_checks:
        print_check_table()
        return

    # ── List live gtopt runs (no subprocess) ──
    if args.list_runs:
        print_run_list(list_runs())
        return

    # ── Attach to a running gtopt (no subprocess) ──
    # Observer mode: the same Rich TUI, but no gtopt is launched.
    # 'q' detaches without killing; 's' still writes the stop file.
    if args.attach == "":
        live = list_runs()
        if len(live) != 1:
            print_run_list(live)
            if live:
                print(
                    "error: several live gtopt runs; pass one: run_gtopt --attach PID",
                    file=sys.stderr,
                )
            sys.exit(2)
        sys.exit(run_attach_loop(live[0]))
    if args.attach is not None:
        info = resolve_target(args.attach)
        if info is None:
            print(
                f"error: could not resolve --attach target '{args.attach}'.\n"
                "  Try a numeric PID from `run_gtopt --list`, or a path "
                "to the case directory.",
                file=sys.stderr,
            )
            sys.exit(2)
        sys.exit(run_attach_loop(info))

    # ── Build enabled check set ──
    enabled_checks: set[str] | None = None
    if args.enable_check:
        enabled_checks = set(args.enable_check)
    else:
        all_names = set(available_checks()["pre"]) | set(available_checks()["post"])
        enabled_checks = all_names - DEFAULT_DISABLED_CHECKS
    if args.disable_check:
        for name in args.disable_check:
            enabled_checks.discard(name)

    # ── Environment detection ──
    threads = args.threads if args.threads is not None else detect_cpu_count()
    compression = (
        detect_compression_codec(args.compression) if args.compression else None
    )
    log.debug(
        "environment: %d CPUs, compression=%s",
        threads,
        compression or "(case/gtopt default)",
    )

    # ── Determine case directory ──
    case_path = Path(args.case) if args.case else Path.cwd()
    case_type = detect_case_type(case_path)

    if args.case is None and case_type == CaseType.PASSTHROUGH:
        print(
            "error: no case directory given and current directory is not "
            "a recognized PLP, gtopt or PLEXOS case.\n"
            "Usage: run_gtopt [CASE] [options]\n"
            "Run 'run_gtopt -h' for help.",
            file=sys.stderr,
        )
        sys.exit(2)

    log.debug("case: %s (type: %s)", case_path, case_type.value)

    # Planning-JSON basename inside ``gtopt_dir``.  PLP/gtopt cases name it
    # after the directory; PLEXOS names it after the bundle stem (set below).
    json_name: str | None = None

    # ── PLP case: convert first ──
    gtopt_dir = case_path
    if case_type == CaseType.PLP:
        gtopt_dir = args.output_dir or infer_gtopt_dir(case_path)
        log.info("PLP case detected, converting to %s", gtopt_dir)

        plp2gtopt_bin = find_plp2gtopt()
        if not plp2gtopt_bin:
            print(
                "error: plp2gtopt not found on PATH. "
                "Install with: pip install -e ./scripts",
                file=sys.stderr,
            )
            sys.exit(2)

        plp_extra = _split_extra_args(args.plp_args)
        if args.dry_run:
            cmd = [plp2gtopt_bin, str(case_path), "-o", str(gtopt_dir)]
            if plp_extra:
                cmd.extend(plp_extra)
            print(f"[dry-run] {' '.join(cmd)}")
        else:
            rc = run_plp2gtopt(plp2gtopt_bin, case_path, gtopt_dir, plp_extra)
            if rc != 0:
                sys.exit(rc)

        if args.convert_only:
            log.info("conversion complete (--convert-only), skipping solve")
            return

    # ── PLEXOS bundle: convert first (mirrors the PLP flow) ──
    if case_type == CaseType.PLEXOS:
        gtopt_dir = args.output_dir or infer_plexos_gtopt_dir(case_path)
        # plexos2gtopt names the JSON after the bundle stem, not the output
        # dir (e.g. gtopt_PLEXOS20260422/PLEXOS20260422.json).
        json_name = f"{plexos_stem(case_path)}.json"
        log.info("PLEXOS bundle detected, converting to %s", gtopt_dir)

        plexos2gtopt_bin = find_plexos2gtopt()
        if not plexos2gtopt_bin:
            print(
                "error: plexos2gtopt not found on PATH. "
                "Install with: pip install -e ./scripts",
                file=sys.stderr,
            )
            sys.exit(2)

        plexos_extra = _split_extra_args(args.plexos_args)
        if args.dry_run:
            cmd = [plexos2gtopt_bin, str(case_path), "-o", str(gtopt_dir)]
            if plexos_extra:
                cmd.extend(plexos_extra)
            print(f"[dry-run] {' '.join(cmd)}")
        else:
            rc = run_plexos2gtopt(plexos2gtopt_bin, case_path, gtopt_dir, plexos_extra)
            if rc != 0:
                sys.exit(rc)

        if args.convert_only:
            log.info("conversion complete (--convert-only), skipping solve")
            return

    # ── Passthrough: not a directory, forward as-is ──
    if case_type == CaseType.PASSTHROUGH:
        gtopt_bin = find_gtopt_binary()
        if not gtopt_bin:
            print(
                "error: gtopt binary not found.\n"
                "  Set GTOPT_BIN=/path/to/gtopt, or ensure 'gtopt' is on PATH.\n"
                "  Build from source: bash tools/setup_sandbox.sh --build\n"
                "  Or download: python tools/get_gtopt_binary.py",
                file=sys.stderr,
            )
            sys.exit(2)
        if args.dry_run:
            all_args = [str(case_path)] + gtopt_extra
            print(f"[dry-run] {gtopt_bin} {' '.join(all_args)}")
            return
        direct_output_dir = resolve_output_dir(case_path, gtopt_extra)
        direct_started_at = time.time()
        rc = run_gtopt(
            gtopt_bin,
            case_path,
            threads,
            compression,
            gtopt_extra,
            output_dir=direct_output_dir,
        )
        if args.fail_on_not_converged:
            rc = convergence_exit_code(rc, direct_output_dir, direct_started_at)
        sys.exit(rc)

    # ── Locate the planning JSON ──
    json_file = gtopt_dir / (json_name or f"{gtopt_dir.name}.json")

    # ── Pre-flight checks ──
    if args.check and json_file.is_file() and not args.dry_run:
        ok = run_preflight_checks(json_file, strict=args.strict, enabled=enabled_checks)
        if not ok:
            log.error("pre-flight checks failed, aborting")
            sys.exit(2)

    # ── Sanitize JSON (compression, threads, directories) ──
    sanitized_path: Path | None = None
    if json_file.is_file() and not args.dry_run:
        sanitized_path = sanitize_json(
            json_file,
            compression=compression,
            threads=threads,
            export_path=args.export_json,
        )
        if sanitized_path is None:
            log.error("failed to process planning JSON")
            sys.exit(2)

    # ── Find gtopt binary ──
    gtopt_bin = find_gtopt_binary()
    if not gtopt_bin:
        print("error: gtopt binary not found", file=sys.stderr)
        sys.exit(2)

    # ── Build the gtopt target ──
    # If we produced a sanitized JSON, pass it directly instead of the dir
    is_sanitized = sanitized_path is not None and sanitized_path != json_file
    gtopt_target: Path = (
        sanitized_path if is_sanitized and sanitized_path else gtopt_dir
    )

    if args.dry_run:
        _print_dry_run(
            gtopt_bin, json_file, gtopt_dir, compression, threads, gtopt_extra
        )
        return

    # ── Where gtopt will write (same precedence as gtopt itself) ──
    # gtopt receives the case directory unless a sanitized JSON copy (a
    # sibling of ``json_file``) is passed; a directory gets the
    # ``results`` default, a planning JSON the ``output`` one.
    output_dir = resolve_output_dir(
        json_file if is_sanitized else gtopt_dir, gtopt_extra
    )
    log.debug("output directory: %s", output_dir)

    # ── Drop per-run files a previous run left behind ──
    # Only solver_status.json and a stale sddp_stop_request.json are
    # removed (each one logged); results, cuts and logs are never touched,
    # so a previous run stays available to --recover / hot-start.
    remove_stale_run_files(output_dir)

    log.info(
        "%s",
        run_summary(
            gtopt_bin,
            json_file.stem if json_file.is_file() else gtopt_dir.name,
            threads,
            output_dir,
            gtopt_extra,
        ),
    )
    run_started_at = time.time()
    rc = run_gtopt(
        gtopt_bin,
        gtopt_target,
        threads,
        compression,
        gtopt_extra,
        output_dir=output_dir,
        case_name=json_file.stem if json_file.is_file() else gtopt_dir.name,
    )

    # ── Clean up sanitized JSON (unless exported) ──
    if is_sanitized and sanitized_path and not args.export_json:
        try:
            sanitized_path.unlink()
        except OSError:
            pass

    # ── Post-run report ──
    results_dir = output_dir
    planning_json_for_report = results_dir / "planning.json"
    if not planning_json_for_report.is_file():
        planning_json_for_report = json_file
    report_solution(
        results_dir, gtopt_dir, planning_json_for_report, since=run_started_at
    )

    # ── Post-flight checks (error LPs, output analysis) ──
    if args.check:
        planning_json = results_dir / "planning.json"
        if not planning_json.is_file():
            planning_json = json_file
        run_postflight_checks(
            results_dir,
            planning_json,
            gtopt_rc=rc,
            enabled=enabled_checks,
        )

    if args.fail_on_not_converged:
        rc = convergence_exit_code(rc, output_dir, run_started_at)
    sys.exit(rc)


if __name__ == "__main__":
    main()
