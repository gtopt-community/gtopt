"""High-level conversion entry-points for ``plexos2gtopt``.

The functions here are the public façade used by
:mod:`plexos2gtopt.main`:

* :func:`validate_plexos_bundle` — light schema sanity check.
* :func:`convert_plexos_bundle` — full conversion to a gtopt planning
  JSON, end-to-end (bundle → entities → planning).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from gtopt_shared.future_cost import set_future_cost_fields
from gtopt_shared.sanity_report import run_sanity_report

from ._comparison import compare_plexos_bundle
from ._defaults import resolve_default_emissions_file
from ._horizon import HorizonOptions, apply_horizon
from ._plexos_versions import detect_version, is_solution_db, resolve_version
from ._native_reserve import NativeReserveOptions, apply_native_reserve
from ._provision_split import CapLookup, split_provisions_by_family
from ._renewable_floor import free_renewable_floors
from .gtopt_writer import (
    build_planning,
    build_provenance,
    filter_user_constraints,
    parse_water_value_factor,
    write_boundary_cut_csv,
    write_planning,
    write_provenance,
    write_user_constraint_pampl,
)
from ._reserve_load import (
    LoadFactorEstimate,
    parse_case_date,
    resolve_reserve_load_factor,
)
from .parsers import (
    el0_lines_mode,
    extract_case,
    prune_unreferenced_reserve_units,
)
from .plexos_loader import PlexosBundle, locate_bundle
from .plexos_solution import PlexosSolution, open_solution
from .plexos_xml import PlexosDb, load_xml
from .uc_cycle import apply_uc_cycle


logger = logging.getLogger(__name__)


# Cache registry of recent plexos2gtopt runs.  Each successful conversion
# appends one JSONL line; ``plp2gtopt --plexos-overlay latest`` reads the
# last line to resolve to the most recent run without the user having to
# remember the path.  Kept under ``~/.cache/gtopt/`` per the project
# convention for tool-local caches (see ``cen2gtopt`` for the pattern).
PLEXOS_RUN_REGISTRY: Path = (
    Path.home() / ".cache" / "gtopt" / "plexos2gtopt" / "runs.jsonl"
)


def _record_plexos_run(
    *,
    output_dir: Path,
    output_file: Path,
    input_path: Path,
) -> None:
    """Append one row to the plexos2gtopt run registry.

    Idempotent in spirit (a duplicate appended entry is harmless — the
    resolver reads the LAST line).  Best-effort: callers should catch
    ``OSError`` and continue on failure, because a successful conversion
    must never fail because of a cache hiccup.
    """
    import datetime  # noqa: PLC0415
    import json  # noqa: PLC0415

    PLEXOS_RUN_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    # ISO-8601 UTC stamp for human + tool consumption.
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {
        "timestamp": ts,
        "output_dir": str(output_dir.resolve()),
        "output_file": str(output_file.resolve()),
        "input_path": str(input_path.resolve()),
        "bundle_stem": output_file.stem,
    }
    with open(PLEXOS_RUN_REGISTRY, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def validate_plexos_bundle(options: dict[str, Any]) -> bool:
    """Validate a PLEXOS bundle.

    Light sanity check: the bundle path resolves to an extracted
    directory or a recognised archive, and ``DBSEN_PRGDIARIO.xml`` is
    present at the root. Deep schema validation (object counts, class
    coverage) is deferred to the per-extractor logging.

    Args:
        options: Dict with at least ``"input_bundle"`` set.

    Returns:
        ``True`` when the bundle looks well-formed.
    """
    bundle_path = options.get("input_bundle")
    if bundle_path is None:
        logger.error("validate_plexos_bundle: 'input_bundle' option is required")
        return False
    try:
        with locate_bundle(Path(bundle_path)) as bundle:
            if not bundle.xml_path.is_file():
                logger.error("validate failed: %s missing", bundle.xml_path)
                return False
            logger.info("validation OK: %s", bundle.source)
            return True
    except (FileNotFoundError, ValueError) as exc:
        logger.error("validate failed: %s", exc)
        return False


def _maybe_patch_with_plp_embalses(
    case: Any,
    options: dict[str, Any],
    input_path: Path,
) -> Any:
    """Cross-reference PLP ``embalses.csv`` to patch missing reservoirs.

    Three knobs:

    * ``--legacy-profile cen`` (options['plexos_legacy']) — disables
      the patch (strict PLEXOS semantics) unless ``--plp-embalses PATH``
      is given explicitly.
    * ``--no-plp-embalses`` (options['no_plp_embalses']) — disables
      auto-detect and explicit-path patching alike.
    * ``--plp-embalses PATH`` (options['plp_embalses']) — explicit
      path; takes precedence over auto-detect.

    When neither flag is set, auto-detect probes well-known sibling
    locations next to ``input_path`` and silently no-ops when none of
    them ship an ``embalses.csv``.
    """
    if options.get("plexos_legacy") and options.get("plp_embalses") is None:
        return case
    if options.get("no_plp_embalses"):
        return case

    from ._plp_patch import auto_detect_embalses, patch_case_with_plp_embalses

    explicit = options.get("plp_embalses")
    embalses_path: Path | None
    if explicit is not None:
        embalses_path = Path(explicit)
        if not embalses_path.exists():
            # Fall back via the auto-detect resolver in case the user
            # passed a stem without the .xz suffix.
            from ._plp_patch import find_compressed_path

            resolved = find_compressed_path(embalses_path)
            if resolved is None:
                raise FileNotFoundError(
                    f"--plp-embalses path does not exist: {embalses_path}"
                )
            embalses_path = resolved
    else:
        embalses_path = auto_detect_embalses(input_path)

    if embalses_path is None:
        return case

    return patch_case_with_plp_embalses(case, embalses_path)


def _load_reserve_db(bundle: PlexosBundle) -> PlexosDb | None:
    """Parsed PLEXOS XML for the dated reserve coefficients (``None`` if absent)."""
    path = getattr(bundle, "xml_path", None)
    if path is None or not Path(path).is_file():
        return None
    return load_xml(path)


def _family_cap_lookup(bundle: PlexosBundle) -> CapLookup | None:
    """Per-(generator, family) CFdata ``MRU`` / ``MRD`` cap, or ``None``
    when the bundle ships no ``CFdata/`` (the split then keeps the
    aggregated cap)."""
    from .parsers import (  # pylint: disable=import-outside-toplevel
        CF_RESERVE_DIRS,
        cf_maxresp_series,
    )

    root = getattr(bundle, "root", None)
    if root is None or not (Path(root) / "CFdata").is_dir():
        return None
    dirs = dict((sub, (sub, prefix)) for sub, prefix in CF_RESERVE_DIRS)

    def _lookup(gen: str, family: str, direction: str) -> tuple[float, ...]:
        if family not in dirs:
            return ()
        sub, prefix = dirs[family]
        return tuple(cf_maxresp_series(bundle, gen, sub, prefix, direction))

    return _lookup


def _materialise_plexos_model(bundle: Any, options: dict[str, Any]) -> None:
    """Swap ``bundle`` to a materialised copy of ``options['plexos_model']``.

    See :mod:`plexos2gtopt.plexos_model`.  The start date defaults to
    the ``Date From`` of the database's Horizon; the day count is the
    bundle's (``--horizon-days``).
    """
    from .plexos_model import (  # pylint: disable=import-outside-toplevel
        MaterialiseOptions,
        materialise_bundle_dir,
        parse_start_date,
    )

    start_raw = options.get("plexos_start_date")
    if start_raw:
        start = parse_start_date(str(start_raw))
    else:
        start_dt = load_xml(bundle.xml_path).horizon_start
        if start_dt is None:
            raise ValueError(
                "--plexos-model needs --plexos-start-date: the database "
                "has no Horizon 'Date From'"
            )
        start = start_dt
    data_dirs = tuple(Path(p) for p in (options.get("plexos_data_dirs") or ()))
    mopts = MaterialiseOptions(
        model=str(options["plexos_model"]),
        start=start,
        n_days=int(bundle.n_days or 1),
        sample=int(options.get("plexos_sample") or 1),
        data_dirs=(*data_dirs, Path(bundle.source).parent),
    )
    tmp, report = materialise_bundle_dir(bundle.xml_path, mopts)
    bundle._extra_cleanup.append(tmp)  # pylint: disable=protected-access
    bundle.root = Path(tmp.name)
    bundle.materialised_model = mopts.model
    options["_plexos_model_report"] = report.as_dict()


def _resolve_output_paths(
    input_path: Path,
    output_dir: Path | None,
    output_file: Path | None,
    name: str | None,
) -> tuple[Path, Path, str]:
    """Compute ``(output_dir, output_file, planning_name)`` for the run.

    Inference rule (parallel to sddp2gtopt): a ``PLEXOS{date}.zip[.xz]``
    or ``DATOS{date}.zip[.xz]`` input lands in
    ``./gtopt_PLEXOS{date}/`` by default.
    """
    stem = input_path.name
    # Strip recognised compression / archive suffixes for the slug.
    for suffix in (".zip.xz", ".zip", ".xml.xz", ".xml"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if output_dir is None:
        output_dir = input_path.parent / f"gtopt_{stem}"
    if output_file is None:
        output_file = output_dir / f"{stem}.json"
    planning_name = name or stem
    return output_dir, output_file, planning_name


def _prepare_solution_consumers(
    options: dict[str, Any],
    bundle: Any,
    case: Any,
    input_path: Path,
    resolved_accdb: Path | None,
) -> tuple[HorizonOptions, PlexosSolution | None, LoadFactorEstimate | None]:
    """Horizon options, the PLEXOS solution (if any consumer needs it) and
    the ``--reserve-load-factor auto`` estimate (written back to
    ``options['reserve_load_factor']``)."""
    from .plexos_block_layout import (  # pylint: disable=import-outside-toplevel
        infer_horizon_days_from_input,
    )

    n_days = int(bundle.n_days or 1)
    xml_path = getattr(bundle, "xml_path", None)
    full_days = infer_horizon_days_from_input(xml_path) if xml_path else None
    horizon_opts = HorizonOptions.from_options(
        options, horizon_days=n_days, full_days=full_days
    )
    wants_auto_factor = str(options.get("reserve_load_factor", "")).lower() == "auto"
    solution: PlexosSolution | None = None
    if horizon_opts.needs_solution or wants_auto_factor:
        solution = open_solution(
            accdb=resolved_accdb or options.get("plexos_solution_accdb"),
            input_bundle=input_path,
        )
    load_est = resolve_reserve_load_factor(
        options,
        (d.lmax_profile for d in case.demands),
        hours=24 * n_days,
        case_date=parse_case_date(case.bundle.bundle_date),
        solution=solution,
    )
    if load_est is not None:
        options["reserve_load_factor"] = load_est.factor
    return horizon_opts, solution, load_est


def _densify_change_point_csvs(bundle: PlexosBundle) -> None:
    """Align a change-point bundle's Data Files on its horizon (PLEXOS 8.x).

    Needs the Horizon ``Date From``; without it the files are left as
    they are (and a warning says so).
    """
    from .plexos_block_layout import (  # pylint: disable=import-outside-toplevel
        infer_horizon_days_from_input,
    )

    start = load_xml(bundle.xml_path).horizon_start
    if start is None:
        logger.warning(
            "PLEXOS 8.x change-point Data Files but no Horizon 'Date From': "
            "CSVs read as-is (first date in each file = day 1)"
        )
        return
    days = max(
        infer_horizon_days_from_input(bundle.xml_path) or 1, int(bundle.n_days or 1)
    )
    done = bundle.normalize_dated_csvs(start.date(), days)
    logger.info(
        "PLEXOS change-point Data Files densified onto the horizon "
        "(%s, %d days): %d files",
        start.date(),
        days,
        len(done),
    )


def convert_plexos_bundle(options: dict[str, Any]) -> int:
    """Convert a PLEXOS bundle to gtopt JSON.

    Args:
        options: Conversion options. Recognised keys:

            * ``input_bundle`` (required) — path to the PLEXOS bundle.
            * ``output_dir`` — output directory (inferred otherwise).
            * ``output_file`` — explicit JSON path (defaults to
              ``<output_dir>/<stem>.json``).
            * ``name`` — planning name (default: archive stem).
            * ``use_single_bus`` — overrides the multi-bus default.

    Returns:
        Number of CRITICAL findings (``0`` means success).
    """
    raw_input = options.get("input_bundle")
    if raw_input is None:
        raise ValueError("convert_plexos_bundle: 'input_bundle' option is required")
    input_path = Path(raw_input)

    # Propagate CLI-level knobs to the extractors via process env vars.
    # The :class:`ConversionOptions` dataclass is the typed source of
    # truth; ``install_env`` is the legacy back-compat bridge — every
    # consumer that still reads ``os.environ["GTOPT_*"]`` (in
    # :mod:`parsers` / :mod:`gtopt_writer`) keeps working unchanged.
    # Future PRs will migrate individual call sites to accept
    # ``opts: ConversionOptions`` directly; the env-var bridge can be
    # removed once the last reader is gone.
    from ._options import ConversionOptions

    conversion_opts = ConversionOptions.from_options_dict(options)
    conversion_opts.install_env()

    with locate_bundle(input_path) as bundle:
        if not bundle.csv_paths():
            logger.info(
                "XML-only PLEXOS input %s: no per-class CSV schedules; "
                "Pmax/Load/ratings/costs come from the XML t_data "
                "static properties",
                bundle.xml_name,
            )
        # PLEXOS version: XML t_config, or --source-version / --plexos-version
        # (logged + recorded by gtopt_shared.source_versions).
        version_provenance: dict[str, Any] = {}
        plexos_version = resolve_version(
            detect_version(bundle.xml_path),
            options.get("plexos_version"),
            provenance=version_provenance,
        )
        if plexos_version.profile.change_point_csvs:
            _densify_change_point_csvs(bundle)
        # Resolve horizon mode + day count + block layout.
        horizon_mode = options.get("horizon_mode") or "plexos"
        horizon_days_opt = options.get("horizon_days")
        block_layout: tuple[tuple[int, ...], ...] = ()

        # Resolved .accdb path (if any) — used both to load t_phase_3
        # and to dump the PLEXOS-table cache for downstream
        # comparison tools.  Kept local so the cache dump happens
        # in the same plexos2gtopt invocation, regardless of whether
        # the .accdb came from --plexos-solution-accdb or
        # auto-discovery of the RES sibling.
        resolved_accdb: Path | None = None

        if horizon_mode == "plexos":
            # Try to find the .accdb sibling and read t_phase_3.
            accdb_path = options.get("plexos_solution_accdb")
            if accdb_path is None:
                from .plexos_block_layout import (
                    auto_discover_res_zip,
                    extract_accdb_from_res_zip,
                )

                res_zip = auto_discover_res_zip(input_path)
                if res_zip is not None:
                    logger.info(
                        "found PLEXOS solution (output) bundle '%s' next to "
                        "the input (CEN naming convention) — using its "
                        "t_phase_3 PLEXOS block layout (variable-duration "
                        "blocks matching what PLEXOS solved).  Pass "
                        "--horizon-mode hourly to use a uniform "
                        "1-hour-per-block layout instead (input-only, ignores "
                        "the solution).",
                        res_zip.name,
                    )
                    resolved_accdb = extract_accdb_from_res_zip(res_zip)
                    if resolved_accdb is not None:
                        from .plexos_block_layout import load_block_layout_from_accdb

                        block_layout = load_block_layout_from_accdb(resolved_accdb)
            elif is_solution_db(accdb_path):
                from .plexos_block_layout import load_block_layout_from_accdb

                resolved_accdb = Path(accdb_path)
                block_layout = load_block_layout_from_accdb(resolved_accdb)

            # User-defined block layout (CSV file or inline "{uid:dur,...}"
            # / "d1,d2,..." string) — used when the PLEXOS solution .accdb
            # is absent, or to override it.  Lets the user reproduce a
            # variable-duration block grouping without the solution.
            if not block_layout and options.get("block_layout"):
                from .plexos_block_layout import parse_user_block_layout

                block_layout = parse_user_block_layout(str(options["block_layout"]))

            if block_layout:
                # n_days = ceil(max_interval / 24) so the CSV readers
                # extract the full horizon PLEXOS solved over.
                max_iv = max(iv for blk in block_layout for iv in blk)
                bundle.n_days = (max_iv + 23) // 24
                # Also attach the layout onto the loader bundle so
                # ``extract_reservoirs`` can size per-block emin/emax
                # profiles (BundleSpec is constructed AFTER
                # extract_case so the layout isn't otherwise visible
                # there).
                # ``PlexosBundle`` carries this as a dynamic attribute the
                # downstream extractors read via ``hasattr`` (see
                # parsers.extract_reservoirs); the loader dataclass does not
                # declare it, so the assignment is intentionally untyped.
                bundle.block_layout = block_layout  # type: ignore[attr-defined]  # ty: ignore[missing-attribute]
                logger.info(
                    "horizon-mode=plexos: %d blocks across %d days "
                    "(loaded from PLEXOS solution)",
                    len(block_layout),
                    bundle.n_days,
                )
            else:
                # Graceful fallback: ``--horizon-mode plexos`` prefers the
                # EXACT block grouping PLEXOS solved over (the t_phase_3
                # variable-duration blocks), but the 111-block structure
                # exists ONLY in the solution .accdb (it is a solve-time
                # aggregation, absent from the inputs).  When neither the
                # solution nor a user ``--block-layout`` is available, fall
                # back to UNIFORM HOURLY blocks (``horizon_days × 24``) so a
                # standard input-only convert still works — at a finer grid
                # than PLEXOS's aggregation.  Supply ``--plexos-solution-accdb``
                # / let auto-discovery find ``RES<DATE>.zip`` to match PLEXOS
                # exactly, or ``--block-layout`` to define the grouping
                # yourself.
                # Horizon length: explicit --horizon-days wins; else infer
                # from the PLEXOS Horizon object name (e.g. ``..._7d`` → 7),
                # so the uniform fallback still spans the full solve horizon
                # rather than a single day; else 1 as the last resort.
                if horizon_days_opt:
                    bundle.n_days = int(horizon_days_opt)
                    src = "--horizon-days"
                else:
                    from .plexos_block_layout import infer_horizon_days_from_input

                    inferred = infer_horizon_days_from_input(bundle.xml_path)
                    bundle.n_days = inferred if inferred else 1
                    src = (
                        "inferred from Horizon name"
                        if inferred
                        else "default (set --horizon-days N)"
                    )
                logger.warning(
                    "horizon-mode=plexos: no PLEXOS solution .accdb and no "
                    "--block-layout found; falling back to %d uniform hourly "
                    "blocks (%d days × 24, %s).  Pass --plexos-solution-accdb "
                    "/ --block-layout to reproduce PLEXOS's variable-duration "
                    "grouping.",
                    bundle.n_days * 24,
                    bundle.n_days,
                    src,
                )
        else:  # hourly
            # Horizon length: explicit --horizon-days wins; else infer from
            # the PLEXOS Horizon object name (``..._7d`` → 7), matching the
            # ``--horizon-mode plexos`` fallback so a CEN PCP 7-day bundle
            # converted with ``--horizon-mode hourly`` doesn't silently
            # truncate to a 24-block 1-day window.  Time-varying inputs
            # (Gen_Rating, Nod_Load, Fuel_MaxOfftakeWeek time-weighted
            # overlap, …) all key off ``bundle.n_days``.  Default 1 only
            # as a last resort when the Horizon name is unparseable.
            if horizon_days_opt:
                bundle.n_days = int(horizon_days_opt)
            else:
                from .plexos_block_layout import infer_horizon_days_from_input

                inferred = infer_horizon_days_from_input(bundle.xml_path)
                bundle.n_days = inferred if inferred else 1
                if not inferred:
                    logger.warning(
                        "horizon-mode=hourly: --horizon-days unset and the "
                        "PLEXOS Horizon name is unparseable; defaulting to "
                        "1 day (24 blocks).  Pass --horizon-days N to "
                        "convert a longer horizon."
                    )

        # PLEXOS solution-tables cache (mdb-export of the whole .accdb).  By
        # DESIGN the converter is INPUTS-ONLY: the *only* expected read of the
        # output .accdb is the ``t_phase_3`` block layout resolved above (111
        # blocks + their hours).  Reading the solution back is
        # ``gtopt_compare``'s job.  So the heavy cache dump runs ONLY when the
        # user explicitly opts into reading the solution via
        # ``--use-plexos-commit`` / ``--use-plexos-gen-cap`` /
        # ``--use-plexos-efin`` (the only consumers, all of which fall back
        # gracefully when the cache is absent).  Skipping it by default avoids
        # the ~2-min mdb-export + zstd pass on the multi-hundred-MB solution.
        needs_solution_cache = bool(
            options.get("use_plexos_commit")
            or options.get("use_plexos_gen_cap")
            or options.get("use_plexos_efin")
        )
        if resolved_accdb is not None and needs_solution_cache:
            from .plexos_block_layout import cache_plexos_tables

            output_dir_for_cache, _, _ = _resolve_output_paths(
                input_path,
                options.get("output_dir"),
                options.get("output_file"),
                options.get("name"),
            )
            output_dir_for_cache.mkdir(parents=True, exist_ok=True)
            cache_dir = cache_plexos_tables(resolved_accdb, output_dir_for_cache)
            bundle.accdb_path = resolved_accdb
            bundle.accdb_cache_dir = cache_dir

        if options.get("plexos_model"):
            _materialise_plexos_model(bundle, options)
        case = extract_case(
            bundle,
            lax_uc_refs=bool(options.get("lax_uc_refs")),
            # CF_*_COMP Sense unset read as '=' (``--legacy-profile cen``;
            # a programmatic ``plexos_legacy`` option still implies it).
            plexos_legacy=bool(
                options.get("cf_comp_equality") or options.get("plexos_legacy")
            ),
            period_rhs_mode=str(options.get("plexos_period_rhs") or "prorate"),
            # PLEXOS Sense 0 is an equality; written ``=`` only in native
            # mode (its balances carry both slacks), ``>=`` in uc mode.
            sense_zero_op=(
                ">=" if options.get("reserve_model", "native") == "uc" else "="
            ),
            lng_inflexible_mode=str(options.get("lng_inflexible_mode") or "storage"),
            lng_value_source=str(options.get("lng_inflexible_value") or "bundle"),
            lng_diesel_cost=options.get("lng_inflexible_diesel_cost"),
            lng_cuts_file=options.get("lng_inflexible_cuts"),
            gas_bands_as_tanks=str(options.get("gas_bands_as_tanks") or "off"),
            gas_refill=str(options.get("gas_refill") or "instant"),
            gas_usable=str(options.get("gas_usable") or "capacity"),
            soft_unpenalised_constraints=options.get("soft_unpenalised_constraints"),
        )
        # Cross-reference PLP's ``embalses.csv`` to patch any reservoir
        # the PLEXOS XML omits but a water-value slope still references
        # (e.g. PILMAIQUEN on CEN PCP bundles).  Suppressed when
        # ``--plexos-legacy`` is on (strict PLEXOS semantics — no
        # cross-bundle mixing) or when ``--no-plp-embalses`` is set.
        case = _maybe_patch_with_plp_embalses(case, options, input_path)
        # Clean: zero-cost solar / wind / run-of-river commitments lose
        # their Min Stable Level and forced-ON floors (--renewable-floors).
        case, renewable_floor_report = free_renewable_floors(
            case, str(options.get("renewable_floors") or "free")
        )
        # --horizon-days N / --reserve-load-factor auto: both may read the
        # PLEXOS solution (RES bundle); opened once, only when needed.
        horizon_opts, solution, load_est = _prepare_solution_consumers(
            options, bundle, case, input_path, resolved_accdb
        )
        if options.get("bess_reserve", "native") == "native":
            from ._bess_reserve import apply_native_bess_reserve

            case = apply_native_bess_reserve(case, bundle)
        # --reserve-model native (no-op for 'uc'): CPF regressions ->
        # ReserveZone requirement planes.
        case = apply_native_reserve(
            case,
            NativeReserveOptions.from_options(options),
            db_loader=lambda: _load_reserve_db(bundle),
        )
        # One provision per reserve family with shared headroom (PLEXOS:
        # one provision per Reserve); per-family CFdata caps.
        case = split_provisions_by_family(case, caps=_family_cap_lookup(bundle))
        # Reserve Units variables whose rows were replaced (native) or
        # dropped are held only by their own linking rows: remove them.
        case = prune_unreferenced_reserve_units(case)
        # The block layout (if any) rides on the bundle_spec so the
        # writer can pick it up.  ``extract_case`` already populated
        # the bundle_spec; we patch the layout in.
        if block_layout:
            from dataclasses import replace as _dc_replace

            case = _dc_replace(
                case,
                bundle=_dc_replace(case.bundle, block_layout=block_layout),
            )
        output_dir, output_file, planning_name = _resolve_output_paths(
            input_path,
            options.get("output_dir"),
            options.get("output_file"),
            options.get("name"),
        )
        soft_storage_bounds = bool(options.get("soft_storage_bounds", True))
        # ``cogen_must_run`` is a (names_set, force_all) tuple parsed in
        # main._parse_cogen_must_run.  Unpack to the build_planning
        # kwargs (defaults match the pre-option behaviour).
        _cogen_must_run_opt: tuple[frozenset[str], bool] = options.get(
            "cogen_must_run"
        ) or (
            frozenset(),
            False,
        )
        if isinstance(_cogen_must_run_opt, tuple) and len(_cogen_must_run_opt) == 2:
            _cogen_names, _cogen_all = _cogen_must_run_opt
        else:
            _cogen_names, _cogen_all = frozenset(), False
        planning = build_planning(
            case,
            name=planning_name,
            default_uc_penalty=options.get("default_uc_penalty"),
            lp_relax=bool(options.get("lp_relax", False)),
            uc_relax=str(options.get("uc_relax") or "auto"),
            soft_storage_bounds=soft_storage_bounds,
            soft_penalty_override=options.get("soft_penalty_cost"),
            default_storage_loss=bool(options.get("default_storage_loss", False)),
            loss_cost_eps=options.get("loss_cost_eps"),
            loss_secant_segments=options.get("loss_secant_segments"),
            line_losses_mode=options.get("line_losses_mode"),
            write_out=options.get("write_out"),
            cogen_must_run=_cogen_names,
            cogen_must_run_all=_cogen_all,
            # Same --water-value-factor applied to the boundary cut CSV, so
            # the Python-derived ``efin_cost`` overwrite matches the cut the
            # LP loads (CLI option > GTOPT_WATER_VALUE_FACTOR env fallback).
            water_value_factor=parse_water_value_factor(
                options.get("water_value_factor")
                or os.environ.get("GTOPT_WATER_VALUE_FACTOR")
            ),
        )
        apply_uc_cycle(
            planning,
            cycle=str(options.get("uc_cycle") or "none"),
            long_transition=options.get("long_transition"),
        )
        # CLI override goes into model_options (gtopt's nested layout).
        if options.get("use_single_bus"):
            model_opts = planning["options"].setdefault("model_options", {})
            model_opts["use_single_bus"] = True
            model_opts["use_kirchhoff"] = False
        # --scale-theta: opt-in manual θ scale (θ_LP = FACTOR · θ).  Unset
        # leaves it to gtopt's C++ auto_scale_theta (1/(3·median(X/V²))).
        if options.get("scale_theta") is not None:
            model_opts = planning["options"].setdefault("model_options", {})
            model_opts["scale_theta"] = float(options["scale_theta"])

        output_dir.mkdir(parents=True, exist_ok=True)
        horizon_report = apply_horizon(planning, horizon_opts, solution)
        # End-of-horizon future-cost (boundary) cut from
        # ``Hydro_StoWaterValues.csv`` (FCF intercept + per-reservoir
        # water-value slopes).  Emitted as ``boundary_cuts.csv`` and
        # wired via the FutureCost element
        # (``system.future_cost_array[0].cuts_file``) so every method
        # values terminal storage exactly like PLEXOS's FCF.
        if horizon_report.boundary_cut == "drop" and case.boundary_cut is not None:
            logger.info(
                "horizon: FCF boundary cut dropped (--horizon-days %d of %s; "
                "the cut values the end of the PLEXOS week)",
                horizon_opts.horizon_days,
                horizon_opts.full_days,
            )
        elif case.boundary_cut is not None:
            reservoir_names = frozenset(
                r["name"] for r in planning["system"].get("reservoir_array", [])
            )
            cut_file = write_boundary_cut_csv(
                case.boundary_cut,
                reservoir_names,
                output_dir,
                water_value_factor=parse_water_value_factor(
                    options.get("water_value_factor")
                    or os.environ.get("GTOPT_WATER_VALUE_FACTOR")
                ),
            )
            if cut_file is not None:
                # The FutureCost element is the single source of the
                # boundary cuts for every method.  Default mode is
                # ``separated`` (loads), so the filename alone enables it.
                set_future_cost_fields(planning, {"cuts_file": cut_file})

        # Seed the monolithic UC solve with a domain-rule-guided starting
        # commitment (the MIP warm-start pipeline: relax -> round ->
        # domain_rules -> inject) so it has a good incumbent early.  The domain
        # rules (min-up/down, commitment-logic, battery/hydro peak-injection)
        # and ``inject.effort=repair`` are on by default.  Disable per-run with
        # ``--set monolithic_options.mip_start.enabled=false``.
        mono = planning.setdefault("options", {}).setdefault("monolithic_options", {})
        mono.setdefault("mip_start", {})["enabled"] = True

        # Move user constraints into modular per-family ``.pampl`` files
        # (loaded via ``system.user_constraint_files``).  Constraints with
        # a per-block ``rhs`` profile stay inline in the JSON.
        sys_d = planning.get("system", {})
        uc_arr = sys_d.get("user_constraint_array", [])
        if uc_arr:
            uc_mode = str(options.get("pampl_uc_mode") or "hard")
            soft_pen = float(options.get("default_uc_penalty") or 10000.0)

            def _fam_set(key: str) -> frozenset[str]:
                raw = options.get(key)
                return (
                    frozenset(s.strip() for s in str(raw).split(",") if s.strip())
                    if raw
                    else frozenset()
                )

            only = _fam_set("pampl_uc_only") or None
            off_fams = _fam_set("pampl_uc_off")

            emit = str(options.get("uc_emit") or "pampl")
            if emit == "inline":
                # Legacy path: keep the (filtered) UCs inline in the JSON
                # ``user_constraint_array`` — no .pampl files.  Used to diff
                # the JSON-parsed LP against the PAMPL-parsed LP.
                kept = filter_user_constraints(
                    uc_arr,
                    mode=uc_mode,
                    force_penalty=soft_pen,
                    only=only,
                    off=off_fams,
                )
                if kept:
                    sys_d["user_constraint_array"] = kept
                else:
                    sys_d.pop("user_constraint_array", None)
                logger.info(
                    "--uc-emit=inline: kept %d UC(s) inline (no .pampl files)",
                    len(kept),
                )
            else:
                pampl_files, json_rem = write_user_constraint_pampl(
                    uc_arr,
                    output_dir,
                    mode=uc_mode,
                    force_penalty=soft_pen,
                    only=only,
                    off=off_fams,
                )
                if pampl_files:
                    sys_d["user_constraint_files"] = pampl_files
                if json_rem:
                    sys_d["user_constraint_array"] = json_rem
                else:
                    sys_d.pop("user_constraint_array", None)

        # Optional emissions fill-in (master switch ``--emissions``).
        # PLEXOS-XML-shipped CO2 factors always win; this step only
        # fills gaps on Fuel elements that lack one and synthesizes the
        # emission_array['co2'] pollutant row when missing.  Off by
        # default — symmetric with plp2gtopt --emissions.
        # ``--only-emissions`` (issue #519 pure-emissions objective)
        # implies ``--emissions`` (the emission infrastructure must be
        # present for the LP to weight dispatch by tCO2eq).  When set,
        # apply_emission_defaults stamps both the
        # ``EmissionZone.price`` (default 35.0 USD/tCO2eq Chile SCC,
        # override via --carbon-price) AND the
        # ``model_options.objective_mode = "emissions"`` so gtopt swaps
        # its LP objective from $-cost to tCO2eq.  In default cost-mode
        # runs neither is set, so dispatch is undistorted.
        # ── Emission data: always populate ─────────────────────────────
        # ``apply_emission_defaults_from_file`` does two things:
        #   1. Always populates per-Fuel ``heat_content`` + ``emission_factors``
        #      (from PLEXOS data with IPCC fallback) AND creates an
        #      ``EmissionZone`` with weights — physical data the LP doesn't
        #      use for dispatch in cost mode but downstream tools
        #      (gtopt_marginal_units, gtopt_check, audit pipelines) need
        #      to compute marginal-CO2 attribution.
        #   2. When ``only_emissions=True``: also stamps
        #      ``model_options.objective_mode = "emissions"`` and
        #      reservoir terminal carbon values — this DOES change the
        #      LP behavior (issue #519).
        #
        # We unconditionally call (1) so cost-mode planning JSONs are
        # complete for downstream attribution; (2) only fires when the
        # user explicitly asks via ``--only-emissions``.  The legacy
        # ``--emissions`` flag is no longer gating: equivalent to having
        # always been on.
        only_emissions = bool(options.get("only_emissions", False))
        no_emissions = bool(options.get("no_emissions", False))
        if only_emissions and no_emissions:
            raise ValueError(
                "--only-emissions and --no-emissions are mutually "
                "exclusive (one switches the LP objective to tCO2eq, "
                "the other strips all emission data from the JSON)."
            )
        if no_emissions:
            # Strip emission-only fields the converter would otherwise
            # leave alongside the source data.  Fuel.heat_content is
            # KEPT because it's also used by cost/heat-rate
            # conversions (gcost = heat_rate × heat_content × fuel.price).
            sys_ = planning.get("system", {})
            sys_.pop("emission_zone_array", None)
            sys_.pop("emission_source_array", None)
            for f in sys_.get("fuel_array", []) or []:
                f.pop("emission_factors", None)
            for g in sys_.get("generator_array", []) or []:
                g.pop("emission_rate", None)
                g.pop("emission_captures", None)
                g.pop("emissions", None)
        else:
            from gtopt_shared.emissions import (  # noqa: PLC0415
                apply_emission_defaults_from_file,
            )

            emissions_src = options.get("emissions_file")
            emissions_report = options.get("emissions_report")
            if emissions_report is None:
                emissions_report = output_dir / "plexos_emissions_report.json"
            # plexos2gtopt is CEN-Chile-shaped by construction (PLEXOS
            # DBs in this codebase come from CEN's PCP archive).  When
            # the user doesn't pass an explicit ``--emissions-file``,
            # default to the bundled
            # ``gtopt_shared/data/emissions/cen_chile.json`` instead of
            # the bare IPCC defaults — that file is a superset (full
            # IPCC + ``sulfur_cogen`` extra + 6 per-gen
            # ``generator_overrides`` for CERRO_PABELLON_U1/U2/U3,
            # CERRO_DOMINADOR_CS, CMPC_BUCALEMU_2, PAS_MEJILLONES).
            # A missing bundled file raises MissingDataFileError (broken
            # install) rather than silently using the IPCC defaults.
            if emissions_src is None:
                emissions_src = resolve_default_emissions_file()
            apply_emission_defaults_from_file(
                planning,
                Path(emissions_src) if emissions_src is not None else None,
                report_path=(
                    Path(emissions_report) if emissions_report is not None else None
                ),
                only_emissions=only_emissions,
                carbon_price=options.get("carbon_price"),
            )
            import_buses = list(options.get("emission_import_buses") or [])
            if import_buses:
                split_import_emission_zones(planning, import_buses)

        # Topology-driven reservoir extraction-flow estimate (shared with
        # plp2gtopt): replace the generic C++ ReservoirLP -9000/6000 m³/s
        # extraction defaults with tight, per-reservoir bounds derived from
        # the hydraulic network.  Inflows are inline arrays here so no
        # input_dir is required; ``output_dir`` is passed for parity.  On
        # by default; ``--no-reservoir-flow-estimate`` disables it.
        if options.get("reservoir_flow_estimate", True):
            from gtopt_shared.reservoir_flow import (  # noqa: PLC0415
                apply_reservoir_flow_estimates,
                widen_extraction_bounds_symmetric,
            )

            # Size the reservoir extraction ``fmax`` from the PHYSICAL
            # turbine nameplate, NOT the maintenance-reduced weekly
            # dispatch ``pmax``.  PLEXOS zeroes ``Gen_Rating`` for units
            # it holds offline (COLBUN_U2, PEHUENCHE_U1, RALCO_U2, …) so
            # their JSON ``pmax`` is 0; ``case.generator_nameplates``
            # carries their full ``Max Capacity`` so the estimator does
            # not under-size the reservoir bound.  The dispatch ``pmax``
            # in the JSON is unchanged (offline units stay 0).
            # ``case.extra_turbines`` carries the JSON turbine dicts for units
            # PLEXOS holds offline (dropped from ``turbine_array`` to avoid a
            # free-drain artefact); the estimator counts them for the reservoir
            # extraction ``fmax`` BOUND only, so a maintenance-offline second
            # unit (COLBUN_U2, PEHUENCHE_U1, RALCO_U2, …) still contributes its
            # nameplate to the bound without re-entering the dispatch JSON.
            apply_reservoir_flow_estimates(
                planning,
                input_dir=output_dir,
                generator_capacities=case.generator_nameplates or None,
                extra_turbines=case.extra_turbines or None,
            )
            # Replace the DIRECTIONAL estimate with the SAME symmetric box
            # plp2gtopt applies (gtopt_writer.py): ``[-2·e, +2·e]`` with
            # ``e = max(|fmin_est|, |fmax_est|)``.  The directional accept cap
            # (``fmin = -max_inflow``, e.g. RAPEL ``-1``) under-sizes how much
            # natural junction inflow the reservoir can take into storage, so a
            # water-short hydro unit's turbine is forced ON below its commitment
            # min-stable level → MIP infeasible (PLEXOS 2026-02-15).  Both
            # converters must apply this identically — see
            # ``test_reservoir_extraction_bounds_symmetric``.
            widen_extraction_bounds_symmetric(planning, factor=2.0)

        write_planning(planning, output_file)

        # Conversion-provenance sidecar (F5): documents each gtopt
        # element class's PLEXOS source, units, and transforms.
        provenance = build_provenance(planning, source_bundle=input_path.name)
        provenance.update(version_provenance)  # "source_format" (shared)
        provenance["plexos_profile"] = plexos_version.as_dict()
        # How unenforced PLEXOS line ratings were modelled (--el0-lines).
        provenance["el0_lines"] = el0_lines_mode()
        # Operator-replica profile (None = the clean gtopt formulation)
        # and the toggles it applied (explicit flags excluded).
        provenance["renewable_floors"] = {
            "mode": str(options.get("renewable_floors") or "free"),
            "pmin_dropped": renewable_floor_report.pmin_dropped,
            "forced_on_dropped": renewable_floor_report.forced_on_dropped,
        }
        provenance["legacy_profile"] = {
            "profile": options.get("legacy_profile"),
            "toggles": list(options.get("legacy_toggles") or ()),
        }
        # PLEXOS constraints without a Penalty Price: HARD (null) unless
        # --soft-unpenalised-constraints PENALTY re-softened them.
        provenance["soft_unpenalised_constraints"] = options.get(
            "soft_unpenalised_constraints"
        )
        provenance["horizon"] = horizon_report.as_dict()
        if load_est is not None:
            provenance["reserve_load_factor"] = load_est.as_dict()
        write_provenance(provenance, output_file.with_suffix(".provenance.json"))
        model_report = options.get("_plexos_model_report")
        if model_report:
            import json  # pylint: disable=import-outside-toplevel

            model_path = output_file.with_suffix(".plexos_model.json")
            model_path.write_text(
                json.dumps(model_report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            logger.info("wrote PLEXOS model resolution report: %s", model_path)

        # Record this successful run in the cache registry so
        # downstream tools (``plp2gtopt --plexos-overlay latest``,
        # gtopt_compare, etc.) can find the most recent output
        # without the user having to remember the path.  See
        # ``_record_plexos_run`` in this module + the matching
        # resolver in ``plp2gtopt._plexos_overlay``.
        try:
            _record_plexos_run(
                output_dir=output_dir,
                output_file=output_file,
                input_path=input_path,
            )
        except OSError as e:
            # The cache is best-effort: never fail a successful
            # conversion because of a registry hiccup.
            logger.warning(
                "Could not update plexos2gtopt run registry "
                "(--plexos-overlay latest may not see this run): %s",
                e,
            )

        # Cache was already dumped before ``extract_case`` (see
        # the pre-extract block) so the fuel-offtake-caps extractor
        # could read it; nothing more to do here.

        logger.info(
            "converted %s -> %s "
            "(nodes=%d, generators=%d, lines=%d, demands=%d, batteries=%d)",
            input_path,
            output_file,
            len(case.nodes),
            len(case.generators),
            len(case.lines),
            len(case.demands),
            len(case.batteries),
        )

        # Optional post-write step: auto-detect lines that exceed
        # their rated cap in a pandapower DC OPF of the first
        # (scenario, block) and patch ``enforce_level = 0`` onto
        # them.  See ``auto_lift_lines.detect_overloaded_lines`` for
        # the rationale (radial step-down lines that PLEXOS treats
        # as voltage-conditional and the LP would otherwise refuse
        # to dispatch above).
        auto_lift_threshold = options.get("auto_lift_lines")
        if auto_lift_threshold is not None:
            # pylint: disable=import-outside-toplevel
            from .auto_lift_lines import (
                detect_overloaded_lines,
                detect_overloaded_lines_via_gtopt,
                patch_bundle_with_lifts,
            )

            engine = options.get("auto_lift_engine", "pandapower")
            if engine == "gtopt":
                overloaded = detect_overloaded_lines_via_gtopt(
                    output_file, threshold=float(auto_lift_threshold)
                )
            else:
                overloaded = detect_overloaded_lines(
                    output_file, threshold=float(auto_lift_threshold)
                )
            if overloaded:
                n_patched = patch_bundle_with_lifts(output_file, overloaded)
                logger.info(
                    "auto-lift (%s): OPF flagged %d line(s) over %.2fx "
                    "rated; patched %d to enforce_level=0",
                    engine,
                    len(overloaded),
                    float(auto_lift_threshold),
                    n_patched,
                )
            else:
                logger.info(
                    "auto-lift (%s): OPF found no line over %.2fx rated; "
                    "nothing patched.",
                    engine,
                    float(auto_lift_threshold),
                )

    # Post-conversion comparison + structural validation (mirrors
    # plp2gtopt's run_post_check).  Prints the PLEXOS↔gtopt element / UC /
    # indicator tables and returns the CRITICAL finding count as the exit
    # code.  Opt out with ``--no-check`` (options["run_check"] = False).
    if options.get("run_check", True):
        from ._comparison import run_post_check  # noqa: PLC0415

        # Data-quality report on the case as written (after auto-lift):
        # warnings only; ``--sanity-report PATH`` also writes the JSON.
        run_sanity_report(
            output_file, json_path=options.get("sanity_report"), log=logger
        )
        return run_post_check(planning, case, output_dir=output_dir)
    return 0


__all__ = [
    "compare_plexos_bundle",
    "convert_plexos_bundle",
    "validate_plexos_bundle",
]


def split_import_emission_zones(
    planning: dict[str, Any], import_buses: list[str]
) -> int:
    """Keep import-proxy generators out of the market's emission zones.

    Every EmissionZone gets ``buses`` = the system buses NOT listed in
    ``import_buses``, and a tracking-only twin ``<zone>_imports`` (same
    pollutants, no cap / price / pool) scoped to ``import_buses``.  gtopt
    synthesises the fuel-derived EmissionSources per zone and bus scope,
    so the market total excludes imports while their tonnes stay visible
    in the ``_imports`` zone.  Returns the number of twins added.
    """
    sys_ = planning.get("system", {})
    zones = sys_.get("emission_zone_array") or []
    all_buses = [b["name"] for b in sys_.get("bus_array", []) or []]
    wanted = set(import_buses)
    unknown = sorted(wanted - set(all_buses))
    if unknown:
        raise ValueError(f"--emission-import-buses: unknown bus(es) {unknown}")
    home = [b for b in all_buses if b not in wanted]
    next_uid = max((int(z.get("uid", 0) or 0) for z in zones), default=0) + 1
    twins: list[dict[str, Any]] = []
    for zone in zones:
        if zone.get("buses"):
            continue
        zone["buses"] = home
        twins.append(
            {
                "uid": next_uid + len(twins),
                "name": f"{zone['name']}_imports",
                "emissions": [dict(e) for e in zone.get("emissions", [])],
                "buses": sorted(wanted),
            }
        )
    zones.extend(twins)
    return len(twins)
