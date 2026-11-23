#!/usr/bin/env python3
"""Main entry point for pandapower to gtopt conversion."""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, NoReturn

import numpy as np
import pandas as pd

from gtopt_config import (
    add_color_argument,
    add_log_level_argument,
    configure_logging,
    get_version,
)
from gtopt_shared.json_utils import strip_internal_keys
from gtopt_shared.cli_flags import (
    add_demand_fail_cost_argument,
    add_scale_objective_argument,
    add_use_kirchhoff_argument,
    add_use_single_bus_argument,
)

from gtopt_shared.time_grid import (
    DEFAULT_START,
    STAGE_PERIODS,
    BlockGrid,
    TimeGrid,
    calendar_stage_of_hour,
    chronological_groups,
    hour_of_day_groups,
    hour_timestamps,
    ldc_groups,
    parse_hour_range,
)

from .convert import (
    DEFAULT_SOLVER,
    SUPPORTED_SOLVERS,
    _SUPPORTED_FORMATS,
    TimeSeriesData,
    convert,
    load_network,
)
from .profiles import (
    ProfileError,
    load_spec,
    profiles_from_net_controllers,
    ResolvedProfiles,
    read_profile_table_with_times,
    resolve_profiles,
)

logger = logging.getLogger(__name__)

__version__ = get_version()

# Supported pandapower standard test networks (CLI name → pandapower function name)
_NETWORKS: dict[str, str] = {
    "ieee30b": "case_ieee30",
    "case4gs": "case4gs",
    "case5": "case5",
    "case6ww": "case6ww",
    "case9": "case9",
    "case14": "case14",
    "case33bw": "case33bw",
    "case57": "case57",
    "case118": "case118",
}

_DEFAULT_NETWORK = "ieee30b"

_DESCRIPTION = f"""\
Convert a pandapower network to gtopt JSON format.

Input sources (mutually exclusive):
  -f FILE        Load a pandapower network file ({_SUPPORTED_FORMATS}).
  --matpower F   Same as -f, for a MATPOWER .m / .mat case.
  -n NAME        Use a built-in pandapower test network (see --list-networks).
                 Default: {_DEFAULT_NETWORK}

Without profiles, writes a self-contained single-snapshot gtopt JSON.
With --profiles (hourly load / renewable series) it writes a multi-block,
multi-stage case: <OUT>.json plus an input directory <OUT stem>/ holding
Demand/lmax.parquet and GeneratorProfile/profile.parquet, and a
pp2gtopt_state.json snapshot that --from-state replays.
"""

_EPILOG = """
examples:
  # Convert the default IEEE 30-bus built-in network → ieee30b.json
  pp2gtopt

  # Convert a saved pandapower JSON file
  pp2gtopt -f my_network.json -o my_case.json

  # Convert a MATPOWER case file
  pp2gtopt -f case39.m -o case39.json

  # Convert a pandapower Excel workbook
  pp2gtopt -f network.xlsx -o network.json

  # Use a specific built-in test network
  pp2gtopt -n case14 -o ieee14b.json

  # List all available built-in networks
  pp2gtopt --list-networks

  # One week of hourly profiles, daily stages (7 stages x 24 blocks)
  pp2gtopt -n case118 --profiles week.csv --hours 0:168 --stage-hours 24 \\
      -o case118_week.json

  # Full year, 4-hour blocks, weekly stages, column mapping from a spec
  pp2gtopt --matpower case.m --profiles year.parquet \\
      --profiles-spec map.yaml --block-hours 4 --stage-hours 168 -o year.json

  # Full year -> 12 monthly stages x 12 chronological 2-h blocks of a
  # typical day (hours 0-1, 2-3, ..., 22-23), SDDP
  pp2gtopt -f net.json --profiles year.csv --stage-period month \\
      --blocks-per-stage 12 --method sddp -o monthly.json

  # Replay a previous time-series conversion
  pp2gtopt --from-state year/pp2gtopt_state.json
"""

# argparse dests holding file paths: stored relative to the state file
# and resolved against it on ``--from-state`` reload.
_PATH_ARGS: tuple[str, ...] = (
    "file",
    "matpower",
    "output",
    "profiles",
    "profiles_spec",
)


def _list_networks_and_exit() -> NoReturn:
    """Print available networks and exit."""
    print("Available pandapower test networks:")
    for name, fn in sorted(_NETWORKS.items()):
        marker = " (default)" if name == _DEFAULT_NETWORK else ""
        print(f"  {name:<12}  pandapower.networks.{fn}(){marker}")
    sys.exit(0)


def _log_element_counts(planning: dict[str, Any]) -> None:
    """Print element counts from the generated gtopt planning dict."""
    try:
        from gtopt_check_json._info import print_info  # noqa: PLC0415

        print_info(planning)
    except ImportError:
        # Fallback: plain logger output when _terminal is not available
        sys_data = planning.get("system", {})
        sim = planning.get("simulation", {})
        logger.info("=== Generated gtopt element counts ===")
        logger.info("  System name     : %s", sys_data.get("name", "(unnamed)"))
        logger.info("  Buses           : %d", len(sys_data.get("bus_array", [])))
        logger.info("  Generators      : %d", len(sys_data.get("generator_array", [])))
        logger.info("  Demands         : %d", len(sys_data.get("demand_array", [])))
        logger.info("  Lines           : %d", len(sys_data.get("line_array", [])))
        logger.info("  Blocks          : %d", len(sim.get("block_array", [])))
        logger.info("  Stages          : %d", len(sim.get("stage_array", [])))
        logger.info("  Scenarios       : %d", len(sim.get("scenario_array", [])))


def run_post_check(planning: dict[str, Any]) -> None:
    """Run gtopt_check_json validation on the generated planning dict.

    Prints styled element counts and system info, and if gtopt_check_json
    is available, runs run_all_checks().  Skips gracefully if
    gtopt_check_json is not installed.

    Parameters
    ----------
    planning
        The planning dict produced by convert().
    """
    _log_element_counts(planning)

    try:
        from gtopt_check_json._checks import (  # noqa: PLC0415
            run_all_checks,
            Severity,
        )
        from gtopt_check_json._terminal import (  # noqa: PLC0415
            print_finding as _pf,
            print_status,
            print_summary,
        )
    except ImportError:
        logger.debug("gtopt_check_json not available; skipping JSON validation checks")
        return

    # Check what gtopt will read (``_``-prefixed metadata is not written).
    findings = run_all_checks(
        strip_internal_keys(planning), enabled_checks=None, ai_options=None
    )

    if not findings:
        print_status("All checks passed — no issues found.", ok=True)
        return

    critical_count = 0
    warning_count = 0
    note_count = 0
    for finding in findings:
        _pf(finding.severity.name, finding.check_id, finding.message)
        if finding.severity == Severity.CRITICAL:
            critical_count += 1
        elif finding.severity == Severity.WARNING:
            warning_count += 1
        else:
            note_count += 1

    print_summary(critical_count, warning_count, note_count)


def make_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for pp2gtopt."""
    parser = argparse.ArgumentParser(
        prog="pp2gtopt",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )

    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "-f",
        "--file",
        type=Path,
        metavar="FILE",
        default=None,
        help=(f"pandapower network file to convert (supported: {_SUPPORTED_FORMATS})"),
    )
    source.add_argument(
        "--matpower",
        type=Path,
        metavar="FILE",
        default=None,
        help="MATPOWER case (.m or .mat) to convert (alias of -f)",
    )
    source.add_argument(
        "-n",
        "--network",
        metavar="NAME",
        default=None,
        choices=list(_NETWORKS),
        help=(
            "built-in pandapower test network to convert "
            f"(default: {_DEFAULT_NETWORK}; see --list-networks)"
        ),
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="FILE",
        default=None,
        help="output JSON file path (default: <stem>.json in the current directory)",
    )
    parser.add_argument(
        "--mpc-var",
        metavar="NAME",
        default="mpc",
        help="variable holding the MATPOWER struct in a .mat file (default: mpc)",
    )
    parser.add_argument(
        "--solver",
        "--method",
        dest="solver_type",
        choices=list(SUPPORTED_SOLVERS),
        default=None,
        help=(
            "planning method to embed in options.method "
            f"(choices: {list(SUPPORTED_SOLVERS)}; default: {DEFAULT_SOLVER} "
            "for a snapshot, monolithic with --profiles). "
            "When 'cascade', a default 3-level cascade_options block is also emitted."
        ),
    )
    _add_profile_arguments(parser)
    parser.add_argument(
        "--list-networks",
        action="store_true",
        help="list all available built-in pandapower test networks and exit",
    )
    parser.add_argument(
        "--check",
        dest="run_check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "run post-conversion validation via gtopt_check_json: prints "
            "element counts and basic consistency checks. "
            "Use --no-check to disable. (default: enabled)"
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    # Canonical solver-option flags from gtopt_shared.cli_flags
    # (issue #507 Phase 2 wire-up).  Defaults preserve the original
    # hardcoded pp2gtopt convert() values so existing call sites
    # produce byte-identical JSON.
    add_scale_objective_argument(parser, default=1000.0)
    add_demand_fail_cost_argument(parser, default=1000.0)
    add_use_kirchhoff_argument(parser, default=True)
    add_use_single_bus_argument(parser, default=False, dialect="boolean_optional")

    add_log_level_argument(parser)
    add_color_argument(parser)
    return parser


def _add_profile_arguments(parser: argparse.ArgumentParser) -> None:
    """Time-series (hourly profile) options."""
    grp = parser.add_argument_group(
        "time series",
        "hourly load / renewable profiles -> multi-block, multi-stage case",
    )
    grp.add_argument(
        "--profiles",
        type=Path,
        metavar="FILE",
        default=None,
        help=(
            "hourly profile table (.csv or .parquet; wide = one column per "
            "profile, or long = time/id/value rows). Columns map to elements "
            "as load:<ref>, sgen:<ref>, gen:<ref>, load@bus:<b>, "
            "load@zone:<z>, load@all, or a bare element name"
        ),
    )
    grp.add_argument(
        "--profiles-spec",
        type=Path,
        metavar="FILE",
        default=None,
        help="JSON/YAML column-mapping spec (elements, selectors, units, scale)",
    )
    grp.add_argument(
        "--profiles-from-net",
        action="store_true",
        default=False,
        help="also use ConstControl p_mw profiles stored in the network",
    )
    grp.add_argument(
        "--hours",
        metavar="RANGE",
        default=None,
        help=(
            "0-based hour window: 'A:B' (A..B-1) or 'A+N' (N hours from A); "
            "default: the whole series"
        ),
    )
    grp.add_argument(
        "--block-hours",
        type=int,
        metavar="N",
        default=1,
        help="hours averaged into one block of duration N (default: 1)",
    )
    grp.add_argument(
        "--stage-hours",
        "--stage-length",
        dest="stage_hours",
        type=int,
        metavar="H",
        default=None,
        help=(
            "hours per stage, e.g. 24 (daily) or 168 (weekly); "
            "default: one stage for the whole window"
        ),
    )
    grp.add_argument(
        "--stage-period",
        choices=list(STAGE_PERIODS),
        default=None,
        help=(
            "calendar stages: year, quarter (Jan-Mar, Apr-Jun, ...), month "
            "(calendar months), week (7-day chunks) or day; replaces "
            "--stage-hours (any fixed length)"
        ),
    )
    grp.add_argument(
        "--start-date",
        metavar="YYYY-MM-DD",
        default=None,
        help=(
            "calendar date of profile hour 0 (default: taken from a "
            f"date/time column, else {DEFAULT_START}, a non-leap year)"
        ),
    )
    grp.add_argument(
        "--block-method",
        choices=["chrono", "ldc", "hour-of-day"],
        default=None,
        help=(
            "how hours form blocks inside a stage: chrono (consecutive "
            "--block-hours chunks), hour-of-day (typical day, chronological: "
            "--blocks-per-stage consecutive groups of the hours of the day, "
            "12 -> hours 0-1, 2-3, ..., 22-23, averaged over the stage's "
            "days) or ldc (load-duration curve: hours sorted by --ldc-key, "
            "not chronological).  Default: hour-of-day when "
            "--blocks-per-stage is given, else chrono"
        ),
    )
    grp.add_argument(
        "--blocks-per-stage",
        type=int,
        metavar="X",
        default=None,
        help=(
            "blocks per stage for --block-method hour-of-day / ldc "
            "(default: 12); alone it selects hour-of-day"
        ),
    )
    grp.add_argument(
        "--ldc-key",
        choices=["net-load", "load"],
        default="net-load",
        help=(
            "ldc sort key: total load minus profiled renewable MW (default) "
            "or total load"
        ),
    )
    grp.add_argument(
        "--ldc-split",
        choices=["hours", "energy"],
        default="hours",
        help=(
            "ldc cut: equal hours per block (default) or equal area under "
            "the duration curve (shorter peak blocks)"
        ),
    )
    grp.add_argument(
        "--storage",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="convert net.storage into gtopt batteries (default: on)",
    )
    grp.add_argument(
        "--storage-efficiency",
        type=float,
        metavar="P",
        default=0.95,
        help=(
            "one-way battery efficiency when net.storage has no efficiency "
            "column (default: 0.95)"
        ),
    )
    grp.add_argument(
        "--sddp-max-iterations",
        type=int,
        metavar="N",
        default=None,
        help=(
            "sddp_options.max_iterations (default: 100 with storage, 0 = "
            "forward pass only without storage)"
        ),
    )
    grp.add_argument(
        "--sddp-convergence-tol",
        type=float,
        metavar="TOL",
        default=0.01,
        help="sddp_options.convergence_tol (default: 0.01)",
    )
    grp.add_argument(
        "--single-phase",
        action="store_true",
        default=False,
        help="one phase for all stages (default: one phase per stage)",
    )
    grp.add_argument(
        "--output-format",
        choices=["csv", "parquet"],
        default="csv",
        help="gtopt solution output format (default: csv)",
    )
    grp.add_argument(
        "--from-state",
        dest="from_state",
        type=Path,
        metavar="PATH",
        default=None,
        help=(
            "replay a pp2gtopt_state.json snapshot; flags given alongside "
            "override the stored values"
        ),
    )


def _apply_from_state(
    parser: argparse.ArgumentParser, args: argparse.Namespace, argv: list[str]
) -> argparse.Namespace:
    """Rebuild *args* from a ``--from-state`` snapshot (CLI flags win)."""
    from gtopt_shared.state_snapshot import (  # noqa: PLC0415  pylint: disable=import-outside-toplevel
        apply_state_to_args,
        load_state_snapshot,
    )

    state_path = Path(args.from_state)
    try:
        payload = load_state_snapshot(state_path)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(f"--from-state: {exc}")
    state_args = dict(payload["args"])
    base = state_path.resolve().parent
    for key in _PATH_ARGS:
        val = state_args.get(key)
        if isinstance(val, str) and not os.path.isabs(val):
            state_args[key] = os.path.normpath(base / val)
    state_args["from_state"] = None
    return apply_state_to_args(parser, state_args, argv)


def _write_state(args: argparse.Namespace, state_dir: Path, summary: Any) -> None:
    """Write ``pp2gtopt_state.json`` + README with paths relative to it."""
    from gtopt_shared.state_snapshot import (  # noqa: PLC0415  pylint: disable=import-outside-toplevel
        write_pp2gtopt_readme,
        write_state_snapshot,
    )

    snap = argparse.Namespace(**vars(args))
    snap.from_state = None
    for key in _PATH_ARGS:
        val = getattr(snap, key, None)
        if val is not None:
            setattr(
                snap, key, os.path.relpath(Path(val).resolve(), state_dir.resolve())
            )
    try:
        write_state_snapshot(
            output_dir=state_dir,
            tool_name="pp2gtopt",
            args=snap,
            tool_version=__version__,
            extra={"summary": summary},
        )
        write_pp2gtopt_readme(state_dir)
    except (OSError, ValueError) as exc:
        logger.warning("failed to write state snapshot: %s", exc)


def _build_timeseries(net: Any, args: argparse.Namespace) -> TimeSeriesData:
    """Read, select, resolve and grid the hourly profiles."""
    spec = load_spec(args.profiles_spec)
    tables: list[pd.DataFrame] = []
    file_times: pd.DatetimeIndex | None = None
    if args.profiles is not None:
        if not args.profiles.exists():
            raise ProfileError(f"profiles file not found: {args.profiles}")
        table, file_times = read_profile_table_with_times(args.profiles, spec)
        tables.append(table)
    if args.profiles_from_net:
        from_net = profiles_from_net_controllers(net)
        if from_net.empty:
            logger.warning("--profiles-from-net: network carries no ConstControl")
        else:
            tables.append(from_net)
    if not tables:
        raise ProfileError("no profile data (give --profiles or --profiles-from-net)")
    if len({len(t) for t in tables}) != 1:
        raise ProfileError("--profiles and net controller profiles differ in length")
    wide = pd.concat(tables, axis=1)
    wide = wide.loc[:, ~wide.columns.duplicated(keep="first")]

    start, stop = parse_hour_range(args.hours, len(wide))
    wide = wide.iloc[start:stop].reset_index(drop=True)
    resolved = resolve_profiles(net, wide, spec)
    if args.start_date is not None:
        times = hour_timestamps(len(wide), args.start_date)
        times = times + pd.Timedelta(hours=start)
    elif file_times is not None:
        times = file_times[start:stop]
    else:
        times = hour_timestamps(len(wide), DEFAULT_START) + pd.Timedelta(hours=start)
    grid = _make_grid(args, resolved, times)
    ts = TimeSeriesData(
        grid=grid,
        load_mw=resolved.load_mw,
        gen_factor=resolved.generator_factors(net),
        one_phase_per_stage=not args.single_phase,
        hour_times=times,
        hour_offset=start,
        storage=args.storage,
        storage_efficiency=args.storage_efficiency,
    )
    ts.summary = {
        "hour_window": [start, stop],
        "unmatched_columns": resolved.unmatched_columns,
        "column_energy_mwh": resolved.column_energy_mwh,
    }
    return ts


def _make_grid(
    args: argparse.Namespace, resolved: ResolvedProfiles, times: pd.DatetimeIndex
) -> TimeGrid | BlockGrid:
    """Chronological :class:`TimeGrid`, or a :class:`BlockGrid` for calendar
    stages / ldc / hour-of-day blocks."""
    n_hours = resolved.n_hours
    # Resolved locally (args are saved to the replay state as given).
    method = args.block_method or (
        "chrono" if args.blocks_per_stage is None else "hour-of-day"
    )
    if method == "chrono" and args.blocks_per_stage is not None:
        raise ProfileError(
            "--blocks-per-stage applies to --block-method hour-of-day / ldc; "
            "chrono blocks are --block-hours long"
        )
    n_blocks = 12 if args.blocks_per_stage is None else args.blocks_per_stage
    if args.stage_period is None and method == "chrono":
        return TimeGrid(
            n_hours=n_hours, block_hours=args.block_hours, stage_hours=args.stage_hours
        )
    if args.stage_period is not None and args.stage_hours is not None:
        raise ProfileError("use either --stage-period or --stage-hours, not both")
    if args.stage_period is not None:
        stage_of_hour = calendar_stage_of_hour(times, args.stage_period)
    elif args.stage_hours is not None:
        stage_of_hour = np.arange(n_hours, dtype=np.int64) // args.stage_hours
    else:
        stage_of_hour = np.zeros(n_hours, dtype=np.int64)
    if method == "chrono":
        group = chronological_groups(stage_of_hour, args.block_hours)
    else:
        if args.block_hours != 1:
            raise ProfileError(
                f"--block-hours applies to --block-method chrono only, not {method}"
            )
        if n_blocks <= 0:
            raise ProfileError("--blocks-per-stage must be positive")
        if method == "ldc":
            key = np.zeros(n_hours)
            for mw in resolved.load_mw.values():
                key += mw
            if args.ldc_key == "net-load":
                for mw in resolved.gen_mw.values():
                    key -= mw
            group = ldc_groups(stage_of_hour, key, n_blocks, split=args.ldc_split)
        else:
            group = hour_of_day_groups(np.asarray(times.hour), n_blocks)
    return BlockGrid.from_groups(stage_of_hour, group)


def _log_summary(summary: dict[str, Any]) -> None:
    """Log the time-series energy summary."""
    block_txt = (
        f"of {summary['block_hours']} h"
        if summary.get("block_hours")
        else f"(up to {summary['blocks_per_stage_max']} per stage)"
    )
    logger.info(
        "time series: hours [%d, %d) -> %d block(s) %s, %d stage(s), %d battery(ies)",
        summary["hour_window"][0],
        summary["hour_window"][1],
        summary["blocks"],
        block_txt,
        summary["stages"],
        summary["batteries"],
    )
    logger.info(
        "  loads: %d profiled, %d constant; energy %.1f MWh "
        "(profiled %.1f MWh); peak %.1f MW, min %.1f MW",
        summary["profiled_loads"],
        summary["constant_loads"],
        summary["load_energy_mwh"],
        summary["profiled_load_energy_mwh"],
        summary["peak_load_mw"],
        summary["min_load_mw"],
    )
    logger.info(
        "  generators: %d profiled, %.1f MWh available; %.1f MW unprofiled capacity",
        summary["profiled_generators"],
        summary["available_profiled_energy_mwh"],
        summary["thermal_capacity_mw"],
    )
    energies = summary.get("column_energy_mwh", {})
    log = logger.info if len(energies) <= 20 else logger.debug
    for col, mwh in energies.items():
        log("    profile %-24s %14.1f MWh", col, mwh)
    if summary["peak_load_mw"] > summary["thermal_capacity_mw"] + (
        summary["available_profiled_energy_mwh"] / max(summary["hours"], 1)
    ):
        logger.warning(
            "  peak load exceeds unprofiled capacity + mean renewable "
            "availability; expect demand curtailment"
        )


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the conversion.

    Returns:
        Exit code (0 = success, 1 = conversion failed, 2 = input
        file not found).  Following the gtopt CLI taxonomy used by
        plp2gtopt / sddp2gtopt / plexos2gtopt.
    """
    parser = make_parser()
    cli_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(cli_argv)
    if args.from_state is not None:
        args = _apply_from_state(parser, args, cli_argv)
    configure_logging(args)

    if args.list_networks:
        _list_networks_and_exit()

    net_file = args.file if args.file is not None else args.matpower
    if net_file is not None:
        if not net_file.exists():
            logger.error("input file not found: %s", net_file)
            return 2
        net = load_network(net_file, mpc_var=args.mpc_var)
        name = net_file.stem
    else:
        import pandapower.networks as pn  # pylint: disable=import-outside-toplevel

        network = args.network if args.network is not None else _DEFAULT_NETWORK
        fn_name = _NETWORKS[network]
        net = getattr(pn, fn_name)()
        name = network

    output = args.output if args.output is not None else Path(f"{name}.json")
    ts_mode = args.profiles is not None or args.profiles_from_net
    timeseries: TimeSeriesData | None = None
    if ts_mode:
        try:
            timeseries = _build_timeseries(net, args)
        except (ProfileError, ValueError, OSError) as exc:
            logger.error("%s", exc)
            return 1
    solver_type = args.solver_type or ("monolithic" if ts_mode else DEFAULT_SOLVER)
    planning = convert(
        output,
        net=net,
        name=name,
        solver_type=solver_type,
        scale_objective=args.scale_objective,
        demand_fail_cost=args.demand_fail_cost,
        use_kirchhoff=args.use_kirchhoff,
        use_single_bus=bool(args.use_single_bus),
        timeseries=timeseries,
        output_format=args.output_format,
        sddp_max_iterations=args.sddp_max_iterations,
        sddp_convergence_tol=args.sddp_convergence_tol,
    )
    if timeseries is not None and solver_type == "monolithic":
        logger.info(
            "monolithic: %d phase(s) solved serially%s",
            timeseries.grid.n_stages if not args.single_phase else 1,
            ""
            if timeseries.summary["batteries"]
            else " (no storage: phases are independent; --method sddp gives "
            "the same result with max_iterations=0)",
        )
    if timeseries is not None:
        _log_summary(timeseries.summary)
        args.output = output
        _write_state(args, output.parent / output.stem, timeseries.summary)

    if args.run_check:
        run_post_check(planning)

    return 0


if __name__ == "__main__":
    sys.exit(main())
