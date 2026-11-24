"""High-level conversion entry-points for ``sddp2gtopt``.

The functions here are the public façade used by :mod:`sddp2gtopt.main`:

* :func:`validate_sddp_case` — read ``psrclasses.json`` and perform
  light schema sanity checks.
* :func:`convert_sddp_case` — full conversion to gtopt JSON.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from gtopt_shared.sanity_report import run_sanity_report
from gtopt_shared.source_versions import apply_source_version

from .dat_loader import is_dat_case, load_dat_case
from .sddp_case import is_sddp_block_case, load_sddp_block_case
from .sddp_case_writer import write_sddp_case
from .gtopt_writer import build_planning, write_planning
from .parsers import (
    parse_demands,
    parse_hydro_plants,
    parse_study,
    parse_systems,
    parse_thermal_plants,
)
from .psrclasses_loader import load_psrclasses
from .source_version import SOURCE_FORMAT, detect_version


logger = logging.getLogger(__name__)


REQUIRED_COLLECTIONS = ("PSRStudy", "PSRSystem")


def validate_sddp_case(options: dict[str, Any]) -> bool:
    """Validate an SDDP case directory.

    Checks that ``psrclasses.json`` exists and parses, and that the
    minimal required collections are present and non-empty.

    Args:
        options: Conversion options. Recognised keys:

            * ``input_dir`` (required) — path to the SDDP case dir.

    Returns:
        ``True`` if the case looks well-formed, ``False`` otherwise.
        Failures are logged at ``ERROR`` level — callers should set up
        logging before invoking this function.
    """
    input_dir = options.get("input_dir")
    if input_dir is None:
        logger.error("validate_sddp_case: 'input_dir' option is required")
        return False

    # PSR SDDP block study (deme*.dat): validate via the block-case loader.
    if is_dat_case(input_dir) and is_sddp_block_case(input_dir):
        try:
            block = load_sddp_block_case(input_dir, num_scenarios=1)
        except (FileNotFoundError, ValueError) as exc:
            logger.error("validate failed: %s", exc)
            return False
        if not block.stages or not block.demands:
            logger.error("validate failed: no stages/demand in SDDP block case")
            return False
        logger.info("validation OK (SDDP .dat block case): %s", Path(input_dir))
        return True

    # Raw PSR `.dat` case (no psrclasses.json): validate via the .dat loader.
    if is_dat_case(input_dir):
        try:
            case = load_dat_case(input_dir)
        except (FileNotFoundError, ValueError) as exc:
            logger.error("validate failed: %s", exc)
            return False
        if not case.systems:
            logger.error("validate failed: no systems parsed from .dat case")
            return False
        logger.info("validation OK (.dat): %s", Path(input_dir))
        return True

    try:
        loader = load_psrclasses(input_dir)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("validate failed: %s", exc)
        return False

    ok = True
    for required in REQUIRED_COLLECTIONS:
        if loader.count(required) == 0:
            logger.error(
                "validate failed: collection '%s' is missing or empty",
                required,
            )
            ok = False

    if ok:
        logger.info("validation OK: %s", loader.path)
    return ok


def _resolve_output_paths(
    input_dir: Path,
    output_dir: Path | None,
    output_file: Path | None,
    name: str | None,
) -> tuple[Path, Path, str]:
    """Compute ``(output_dir, output_file, planning_name)`` for the run.

    Mirrors plp2gtopt's inference: if the input dir starts with
    ``sddp_``, replace the prefix with ``gtopt_`` for the default
    output dir. Otherwise default to ``./output``.
    """
    if output_dir is None:
        if input_dir.name.startswith("sddp_"):
            output_dir = input_dir.parent / ("gtopt_" + input_dir.name[5:])
        else:
            output_dir = input_dir.parent / f"gtopt_{input_dir.name}"
    if output_file is None:
        output_file = output_dir / f"{output_dir.name}.json"
    planning_name = name or output_dir.name
    return output_dir, output_file, planning_name


def _convert_sddp_block_case(input_dir: Path, options: dict[str, Any]) -> int:
    """Convert a PSR SDDP block study (see :mod:`sddp2gtopt.sddp_case`)."""
    num_scn = options.get("num_scenarios")
    deficit_segments = str(options.get("deficit_segments") or "first")
    case = load_sddp_block_case(
        input_dir,
        num_scenarios=int(num_scn) if num_scn else None,
        deficit_segments=deficit_segments,
    )
    output_dir, output_file, planning_name = _resolve_output_paths(
        input_dir,
        options.get("output_dir"),
        options.get("output_file"),
        options.get("name"),
    )
    write_sddp_case(
        case,
        Path(output_dir),
        Path(output_file),
        name=planning_name,
        method=str(options.get("method") or "sddp"),
        deficit_segments=deficit_segments,
    )
    for note in case.skipped:
        logger.info("simplified: %s", note)
    logger.info(
        "converted SDDP block case %s → %s (stages=%d, blocks=%d, scenarios=%d)",
        input_dir,
        output_file,
        len(case.stages),
        case.num_blocks,
        len(case.scenarios),
    )
    _sanity(Path(output_file), options)
    return 0


def convert_sddp_case(options: dict[str, Any]) -> int:
    """Convert an SDDP case directory to gtopt JSON.

    Reads ``psrclasses.json``, parses each known collection, and
    writes a single-bus gtopt planning JSON to disk.

    Args:
        options: Conversion options. Recognised keys:

            * ``input_dir`` (required) — path to the SDDP case dir.
            * ``output_dir`` — where to put the planning (inferred
              from ``input_dir`` when missing).
            * ``output_file`` — explicit JSON path; default is
              ``<output_dir>/<output_dir.name>.json``.
            * ``name`` — planning name (default: output dir name).
            * ``use_single_bus`` — overrides the default ``True``.
            * ``source_version`` — ``--source-version`` override of the
              detected SDDP layout generation (see
              :data:`sddp2gtopt.source_version.SOURCE_FORMAT`).

    Returns:
        Number of CRITICAL findings raised by post-conversion
        validation. ``0`` means success.
    """
    raw_input = options.get("input_dir")
    if raw_input is None:
        raise ValueError("convert_sddp_case: 'input_dir' option is required")
    input_dir = Path(raw_input)
    apply_source_version(
        SOURCE_FORMAT,
        detect_version(input_dir),
        options.get("source_version"),
        log=logger,
    )

    # PSR SDDP block study (monthly/weekly blocks, ``deme*.dat`` demand,
    # historical inflow series) → multi-scenario SDDP planning + Parquet.
    if is_dat_case(input_dir) and is_sddp_block_case(input_dir):
        return _convert_sddp_block_case(input_dir, options)

    if str(options.get("deficit_segments") or "first") != "first":
        logger.warning(
            "--deficit-segments %s only applies to SDDP block studies "
            "(sddp.dat deficit curve); this front-end keeps a single "
            "demand_fail_cost",
            options.get("deficit_segments"),
        )

    # Front-end selection: raw PSR `.dat` collection vs psrclasses.json.
    buses = None
    circuits = None
    hydro_topology = False
    if is_dat_case(input_dir):
        case = load_dat_case(
            input_dir,
            import_limit=options.get("import_limit"),
            blocks_per_stage=int(options.get("blocks_per_stage", 0)),
        )
        study = case.study
        systems = case.systems
        thermals = case.thermals
        hydros = case.hydros
        demands = case.demands
        if case.multi_bus:
            buses = case.buses
            circuits = case.circuits
            hydro_topology = case.hydro_topology
    else:
        loader = load_psrclasses(input_dir)
        study = parse_study(loader)
        systems = parse_systems(loader)
        if not systems:
            raise ValueError(f"{loader.path}: no PSRSystem entities — cannot convert")
        thermals = parse_thermal_plants(loader)
        hydros = parse_hydro_plants(loader)
        demands = parse_demands(loader)

    output_dir, output_file, planning_name = _resolve_output_paths(
        input_dir,
        options.get("output_dir"),
        options.get("output_file"),
        options.get("name"),
    )

    planning = build_planning(
        study=study,
        systems=systems,
        thermals=thermals,
        hydros=hydros,
        demands=demands,
        name=planning_name,
        buses=buses,
        circuits=circuits,
        hydro_cost=float(options.get("hydro_cost", 0.0)),
        hydro_topology=hydro_topology,
    )

    # A boundary cut (full hydro topology) ships as a sibling CSV; rewrite the
    # reference to its absolute path so gtopt resolves it regardless of the run
    # directory (gtopt passes absolute paths through unchanged).
    cut_csv = planning.pop("_boundary_cuts", None)
    if cut_csv is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        cut_path = (output_dir / "boundary_cuts.csv").resolve()
        cut_path.write_text(cut_csv, encoding="utf-8")
        # Rewrite the relative placeholder on the FutureCost element (the
        # single source of the boundary cuts for every method).
        for element in planning.get("system", {}).get("future_cost_array", []):
            if isinstance(element, dict) and "cuts_file" in element:
                element["cuts_file"] = str(cut_path)
        logger.info("wrote boundary cut: %s", cut_path)

    write_planning(planning, output_file)
    logger.info(
        "converted %s → %s "
        "(stages=%d, blocks/stage=%d, thermals=%d, hydros=%d, demands=%d)",
        input_dir,
        output_file,
        study.num_stages,
        study.num_blocks,
        len(thermals),
        len(hydros),
        len(demands),
    )
    # Auto-create the output dir even if no extra files are written so
    # downstream tools that mkdir-on-write don't surprise the user.
    output_dir.mkdir(parents=True, exist_ok=True)
    _sanity(Path(output_file), options)
    return 0


def _sanity(output_file: Path, options: dict[str, Any]) -> None:
    """Post-conversion data-quality report (never fails the conversion)."""
    run_sanity_report(output_file, json_path=options.get("sanity_report"), log=logger)
