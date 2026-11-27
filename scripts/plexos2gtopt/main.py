#!/usr/bin/env python3
"""CLI entry-point for ``plexos2gtopt``.

Mirrors :mod:`sddp2gtopt.main` where it makes sense: ``--info`` and
``--validate`` for quick inspection, the no-flag default for full
conversion to a gtopt planning JSON.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from gtopt_config import add_color_argument
from gtopt_shared.cli_flags import (
    LegacyToggle,
    add_legacy_profile_argument,
    add_lift_line_caps_argument,
    add_line_losses_mode_argument,
    add_loss_cost_eps_argument,
    add_loss_secant_segments_argument,
    add_no_lift_lines_argument,
    add_scale_theta_argument,
    add_soft_storage_bounds_argument,
    add_use_single_bus_argument,
    apply_legacy_profile,
)
from gtopt_shared.operator import add_operator_argument, select_operator
from gtopt_shared.results_writer import (
    RESULTS_AUTO,
    add_results_arguments,
    load_planning,
    report_import,
    results_output_dir,
)
from gtopt_shared.sanity_report import add_sanity_report_argument
from gtopt_shared.cli_signals import (
    install_termination_handlers,
    signal_handler as _signal_handler,  # noqa: F401  re-export for back-compat / tests
)
from gtopt_shared.state_snapshot import (
    write_plexos2gtopt_readme,
    write_state_snapshot,
)
from gtopt_shared.source_versions import add_source_version_argument

from ._commitment_policy import UC_RELAX_MODES
from ._horizon import BOUNDARY_CUT_MODES, END_TARGET_MODES, parse_constraint_rules
from ._plexos_versions import SOURCE_FORMAT, versions_help
from ._renewable_floor import RENEWABLE_FLOOR_MODES
from ._reserve_load import reserve_load_factor_arg
from .auto_lift_lines import DEFAULT_THRESHOLD
from ._native_reserve import (
    DEFAULT_RESERVE_LOAD_FACTOR,
    RESERVE_INERTIA_TERMS,
    RESERVE_LOAD_TERMS,
    RESERVE_MODELS,
)
from ._gas_tanks import GAS_TANK_MODES, GAS_USABLE_MODES
from ._lng_inflexible import (
    DEFAULT_LNG_INFLEXIBLE_MODE,
    DEFAULT_LNG_VALUE_SOURCE,
    LNG_INFLEXIBLE_MODES,
    LNG_VALUE_SOURCES,
)
from .info_display import display_plexos_info
from .parsers import (
    EL0_LINES_MODES,
    LINE_UNENFORCED_FACTOR,
    PERIOD_RHS_MODES,
    UnresolvedConstraintReferenceError,
)
from .plexos2gtopt import (
    _resolve_output_paths,
    compare_plexos_bundle,
    convert_plexos_bundle,
    validate_plexos_bundle,
)


__version__ = "0.1.0"

#: ``--legacy-profile`` toggle sets (``--plexos-legacy`` = ``cen``).  The
#: default (no profile) is the clean gtopt formulation: exogenous CPF load
#: term, inertia from commitment, native BESS reserve, enforced line
#: ratings, hard reserves (VoRS switches and surplus slacks dropped), free
#: renewable curtailment.  ``cen`` replicates CEN's PLEXOS PCP model,
#: quirks included, for reconciliation with its published prices.
PLEXOS_LEGACY_PROFILES: dict[str, tuple[LegacyToggle, ...]] = {
    "cen": (
        LegacyToggle(
            "reserve_load_term",
            "endogenous",
            "CPF load term = Generation_SEN as an LP column (every MWh "
            "earns sum(beta*mu): negative LMPs)",
            ("--reserve-load-term",),
        ),
        LegacyToggle(
            "reserve_inertia_term",
            "endogenous",
            "inertia = dispatch-based provisions, as Inertia_SEN",
            ("--reserve-inertia-term",),
        ),
        LegacyToggle(
            "bess_reserve",
            "uc",
            "BESS SSCC rows as PLEXOS user constraints",
            ("--bess-reserve",),
        ),
        LegacyToggle(
            "el0_lines",
            "unenforced",
            "EL=0 / below-threshold line ratings never enforced",
            ("--el0-lines",),
        ),
        LegacyToggle(
            "cf_comp_equality",
            True,
            "BAT_*_CF_GEN_COMP / CF_LOAD_COMP Sense unset read as '='",
        ),
        LegacyToggle(
            "reserve_surplus_equality",
            True,
            "Sense-0 reserve balances kept as equalities with the priced "
            "-Slack surplus (CPF5mUp_Slack 1 $/MW) instead of '>='",
        ),
        LegacyToggle(
            "no_plp_embalses",
            True,
            "no PLP embalses.csv cross-reference patch",
            ("--plp-embalses", "--no-plp-embalses"),
        ),
        LegacyToggle(
            "renewable_floors",
            "plexos",
            "Min Stable Level / forced-ON (Commit=+1) floors kept on "
            "solar, wind and run-of-river units",
            ("--renewable-floors",),
        ),
    ),
}


_DESCRIPTION = """\
Convert a CEN PCP daily PLEXOS bundle to gtopt JSON format.

Reads the PLEXOS XML object database (``DBSEN_PRGDIARIO.xml``) plus
the per-class CSV time-series shipped in ``DATOS{date}.zip``.
``--info`` displays a bundle summary, ``--validate`` runs a schema
sanity check, and ``-i / --input-bundle`` + ``-o / --output-dir``
performs the full conversion (generators, lines, batteries,
reservoirs, waterways, turbines, flow rights, reserve zones, user
constraints, commitments — the full PLEXOS object catalog).
"""

_EPILOG = (
    """\
Examples:
  plexos2gtopt --info  pcp_2026-04-22/source/DATOS20260422.zip.xz
  plexos2gtopt --validate pcp_2026-04-22/source/
  plexos2gtopt -i pcp_2026-04-22/source/PLEXOS20260422.zip -o gtopt_PLEXOS20260422

"""
    + versions_help()
    + "\n"
)


# ``_signal_handler`` is re-exported from gtopt_shared.cli_signals at the
# top of this module.  Existing test scaffolding that imports
# ``plexos2gtopt.main._signal_handler`` continues to work.


def _parse_cogen_must_run(raw: str | None) -> tuple[frozenset[str], bool]:
    """Parse ``--cogen-must-run`` CLI argument.

    Returns ``(names, force_all)``:
      * ``names``  — explicit names to force to ``must_run`` (frozenset)
      * ``force_all`` — True when the literal ``"all"`` / ``"ALL"`` was
        passed; downstream upgrades EVERY detected cogen to ``must_run``.

    Mixed forms (``"all,FOO"``) treat the entire spec as ``all`` — the
    universal form dominates.  Empty / None ⇒ ``(frozenset(), False)``.
    """
    if not raw:
        return frozenset(), False
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if any(t.lower() == "all" for t in tokens):
        return frozenset(), True
    return frozenset(tokens), False


def _positive_float(raw: str) -> float:
    """argparse ``type`` for a strictly positive float."""
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a number: {raw!r}") from exc
    if value <= 0.0:
        raise argparse.ArgumentTypeError(f"must be > 0: {raw!r}")
    return value


def _add_reserve_arguments(parser: argparse.ArgumentParser) -> None:
    """``--reserve-*``: CEN CPF requirement → gtopt native reserve model."""
    parser.add_argument(
        "--reserve-model",
        choices=RESERVE_MODELS,
        default="native",
        help=(
            "How CEN's reserve-requirement constraints are converted.  "
            "'native' (default): the CPF regressions "
            "(CPF_Up10Calculation_*, CPF_Up5Calculation, CPF_DownCalculation "
            "+ *MinProvision) become gtopt ReserveZone requirement planes "
            "(R >= max_k plane_k) with inertia, contingency and load terms, "
            "per-unit zone factors and dated coefficient schedules; pure "
            "provision-sum rows (CSF/CTF *MinProvision) become fixed-"
            "requirement zones.  'uc': legacy UserConstraint + "
            "DecisionVariable emulation (PLEXOS-faithful, endogenous "
            "Generation_SEN)."
        ),
    )
    parser.add_argument(
        "--reserve-load-term",
        choices=RESERVE_LOAD_TERMS,
        default="exogenous",
        help=(
            "Load term D of the native planes.  'exogenous' (default): D is "
            "a parameter, --reserve-load-factor x the hourly demand (or "
            "--reserve-system-load), moved to the row RHS: the requirement "
            "no longer credits generation and the LMP stays energy-only "
            "(CEN's criterion up to ISSCC 2021).  'endogenous': D = "
            "generation - BESS charge as an LP column, as PLEXOS' "
            "Generation_SEN (every MWh of demand earns sum(beta*mu)/1000: "
            "negative LMPs) -- for reconciliation with the PLEXOS solution."
        ),
    )
    parser.add_argument(
        "--reserve-inertia-term",
        choices=RESERVE_INERTIA_TERMS,
        default="commitment",
        help=(
            "Inertia term I of the native planes.  'commitment' (default): "
            "I = sum of H*S times the unit status, from the Inertia_SEN "
            "InertiaProvisions and the Commitments.  'exogenous': I is "
            "data, the must-run inertia (sum of H*S of the units PLEXOS "
            "forces on, Gen_Commit = 1) per hour, written as "
            "ReserveZone.system_inertia (conservative: a larger "
            "requirement).  'endogenous': I = sum of the dispatch-based "
            "inertia provisions, as PLEXOS' Inertia_SEN (PLEXOS-compatible, "
            "for reconciliation)."
        ),
    )
    parser.add_argument(
        "--reserve-load-factor",
        type=reserve_load_factor_arg,
        default=None,
        metavar="F|auto",
        help=(
            "Exogenous load term D = F x sum of demand lmax (default "
            f"{DEFAULT_RESERVE_LOAD_FACTOR}: transmission losses; "
            "PLEXOS Generation_SEN/demand is 1.022-1.046 over the CEN "
            "PCP 2024-2026 history).  'auto' estimates F from the case: "
            "PLEXOS Generation_SEN (or summed generation) / demand over the "
            "converted hours when the RES solution bundle is present, else "
            "a monthly table from the PCP history.  The factor used is "
            "logged and recorded in the provenance sidecar.  Ignored, with "
            "a warning, with --reserve-system-load or --reserve-load-term "
            "endogenous."
        ),
    )
    parser.add_argument(
        "--reserve-system-load",
        type=Path,
        default=None,
        metavar="CSV",
        help=(
            "Exogenous load term from a per-hour CSV instead of F x demand: "
            "column 'system_load' or 'value' [MW] (or 'generation_sen' [GW], "
            "e.g. PLEXOS' own Generation_SEN solution), optional 'hour' "
            "column for ordering.  Exactly one finite value > 0 per horizon "
            "hour (no padding or tiling); ignored with the endogenous load "
            "term."
        ),
    )
    parser.add_argument(
        "--reserve-north-special",
        action="store_true",
        default=False,
        help=(
            "Also convert CEN's northern-corridor rows SpecialCPF/CSF/CTF"
            "North(LW) (Include in ST Schedule = 0 in PCP, off by default) "
            "into reserve zones with the scheduled (dated RHS) requirement; "
            "the corridor-flow coupling is never emitted."
        ),
    )


#: Default ``--line-losses-mode`` (always emitted).
DEFAULT_LINE_LOSSES_MODE = "tangent_signed_flow"


def make_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for ``plexos2gtopt``."""
    parser = argparse.ArgumentParser(
        prog="plexos2gtopt",
        description=_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "positional_input",
        nargs="?",
        type=Path,
        default=None,
        help="PLEXOS bundle path: a directory, DATOS*.zip[.xz], or PLEXOS*.zip",
    )
    parser.add_argument(
        "-i",
        "--input-bundle",
        type=Path,
        default=None,
        help="alias for the positional bundle path",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="output directory for the GTOpt planning",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="logging level (default: INFO)",
    )
    parser.add_argument(
        "--from-state",
        dest="from_state",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "load every parsed argument from a prior run's "
            "``plexos2gtopt_state.json`` snapshot (written automatically to "
            "every output directory).  Reproduces the original invocation "
            "byte-for-byte unless overriden by an explicit flag on the "
            "current command line (e.g. ``--from-state foo/plexos2gtopt_state.json "
            "-o new_output_dir`` reuses every option except output dir).  "
            "See <output_dir>/README.md for the snapshot format."
        ),
    )
    parser.add_argument(
        "--info",
        dest="show_info",
        action="store_true",
        help="show a summary of the bundle and exit",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="run schema sanity checks and exit (0 = ok)",
    )
    parser.add_argument(
        "--compare",
        dest="compare_json",
        default=None,
        metavar="GTOPT_JSON",
        help=(
            "standalone re-comparison: parse the PLEXOS bundle and compare it "
            "against an already-converted GTOpt planning JSON (element counts, "
            "user-constraint families, global indicators), then exit. No "
            "conversion is performed."
        ),
    )
    parser.add_argument(
        "--no-check",
        dest="run_check",
        action="store_false",
        help=(
            "skip the post-conversion PLEXOS↔GTOpt comparison and structural "
            "validation (on by default, mirroring plp2gtopt)"
        ),
    )
    parser.add_argument(
        "--no-reservoir-flow-estimate",
        dest="reservoir_flow_estimate",
        action="store_false",
        help=(
            "disable the topology-driven reservoir extraction-flow estimate "
            "(on by default): without it, reservoirs ship with no fmin/fmax "
            "and GTOpt falls back to the generic -9000/6000 m³/s extraction "
            "defaults"
        ),
    )
    # Emissions flags live in gtopt_shared.cli_flags so plp2gtopt picks
    # up exactly the same surface (issue #507 Phase 2).
    from gtopt_shared.cli_flags import (  # noqa: PLC0415
        add_emissions_arguments,
        add_write_out_argument,
    )

    add_emissions_arguments(parser)
    parser.add_argument(
        "--emission-import-buses",
        default=None,
        metavar="BUS[,BUS...]",
        help="buses whose generators are import proxies (e.g. SEM: "
        "BETTA,France).  Their fuel-derived emissions go to a separate "
        "'<zone>_imports' EmissionZone (tracking only) and are kept out "
        "of the default zone, so market totals exclude imports",
    )
    # ``--write-out`` injects ``options.write_out`` into the planning JSON
    # so the standalone ``gtopt`` binary writes the right output streams
    # (Generator/generation_cost, Bus/balance_dual, etc.) for downstream
    # tools (``gtopt_marginal_units``, loss-audit, the LMP-attribution
    # pipeline).  plexos2gtopt overrides the shared
    # ``DEFAULT_WRITE_OUT`` to ``"all"`` so converted cases ship the full
    # audit-grade dump by default — extras (``Line/lossn_sol``, per-bus
    # heat-rate slacks, capacity-row duals) are needed for the
    # ``compare_with_plexos`` and loss-arbitrage workflows that PLEXOS
    # imports tend to hit.  Pass ``--write-out sol`` / ``--write-out
    # sol,dual,rc:Generator,Line`` to shrink the output footprint when
    # only the marginal-units pipeline is consumed.
    add_write_out_argument(parser, default="all")
    parser.add_argument(
        "--lax-uc-refs",
        action="store_true",
        help=(
            "downgrade the strict UserConstraint-reference check from a "
            "fail-hard error to a warning, silently dropping the offending "
            "terms.  Use for DEBUGGING / iterative parser work when dangling "
            "LHS refs (Vert_*, circuit-suffix lines, _SC shadows) block "
            "regeneration; the resulting JSON is internally consistent but "
            "the rescued constraints may be weakened.  Pair with "
            "`gtopt --constraint-mode debug` for the matching gtopt-side "
            "leniency."
        ),
    )
    plp_emb = parser.add_mutually_exclusive_group()
    plp_emb.add_argument(
        "--plp-embalses",
        dest="plp_embalses",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to the CEN-published ``embalses.csv`` (plain or "
            "``.xz``) used to patch reservoirs that PLP knows about but "
            "the PLEXOS XML omits (e.g. PILMAIQUEN on CEN PCP bundles).  "
            "Every name referenced by a water-value slope in "
            "``Hydro_StoWaterValues.csv`` that has no matching "
            "``Storage`` object is synthesised with the PLP-side "
            "``VolMin`` / ``VolMax`` / ``FactRendim`` so the boundary cut "
            "row is emitted instead of silently dropped.  When omitted "
            "and ``--no-plp-embalses`` is not set, the converter auto-"
            "detects ``embalses.csv[.xz]`` next to the input bundle.  "
            "Auto-detection is off under ``--legacy-profile cen`` (no "
            "cross-bundle data mixing); an explicit path still applies."
        ),
    )
    plp_emb.add_argument(
        "--no-plp-embalses",
        dest="no_plp_embalses",
        action="store_true",
        default=False,
        help=(
            "Disable the PLP cross-reference patch (PLEXOS-strict, "
            "auto-detect off).  Mirrors the WARN-only legacy behaviour: "
            "water-value slopes for non-bundle reservoirs are dropped."
        ),
    )
    add_legacy_profile_argument(
        parser,
        profiles=PLEXOS_LEGACY_PROFILES,
        alias="--plexos-legacy",
        alias_dest="plexos_legacy",
    )
    parser.add_argument(
        "--bess-reserve",
        choices=("uc", "native"),
        default="native",
        help=(
            "How CEN's 'BESS SSCC Constraints' are converted.  'uc' "
            "(legacy, PLEXOS-faithful): emit UPStorageBound/DOWNStorageBound and "
            "BAT_*_CF_GEN_COMP/CF_LOAD_COMP as user constraints (the "
            "CF_*_COMP rows couple reserve provision to bus energy and "
            "can price BESS buses negative).  'native' (default): set "
            "Battery.reserve_sustain_hours (gtopt's native BESS power-"
            "headroom + SoC-sufficiency rows), make those four families "
            "inactive and count BESS SSCC provisions at gross value."
        ),
    )
    _add_reserve_arguments(parser)
    parser.add_argument(
        "--lng-inflexible-mode",
        choices=LNG_INFLEXIBLE_MODES,
        default=DEFAULT_LNG_INFLEXIBLE_MODE,
        help=(
            "How inflexible-LNG cargo tanks are converted (2024 CEN PCP "
            "bundles: a PLEXOS Storage whose Head Storage generators are "
            "all fuel-fed thermal units, e.g. ENGIE_MEJILLONES_GNL_INF).  "
            "Both modes emit the tank as a gtopt Reservoir (eini = cargo, "
            "emax schedule from Hydro_MaxVolume incl. the drop to 0 at the "
            "deadline, state variable across SDDP phases) drained by one "
            "turbine per unit with production_factor = 1/heat_rate.  "
            "'storage' (default, PLEXOS-faithful): spill at the PLEXOS "
            "Vert_* penalty (7200 $/fuel unit) and the units pay the fuel "
            "price.  'offtake' (take-or-pay, sunk gas): gas left at the "
            "deadline is lost at no cost (free spill) and the tank's fuel "
            "price is 0 (units keep VOM + transport only); the gas "
            "opportunity cost is the reservoir's water_value_dual."
        ),
    )
    parser.add_argument(
        "--lng-inflexible-value",
        choices=LNG_VALUE_SOURCES,
        default=DEFAULT_LNG_VALUE_SOURCE,
        help=(
            "--lng-inflexible-mode offtake: terminal value of the gas left "
            "in a tank whose deadline lies BEYOND the horizon (added to the "
            "boundary future-cost cut like a water value; a tank emptied "
            "inside the horizon gets none).  'bundle' (default): the "
            "bundle's own tank-fuel price x 24 per volume unit (the CEN "
            "weekly LNG opportunity cost).  'cuts': the tank's column of "
            "--lng-inflexible-cuts (medium-term boundary cuts).  'diesel': "
            "the displaced diesel, --lng-inflexible-diesel-cost (default: "
            "median diesel SRMC of the bundle) / min heat rate x 24."
        ),
    )
    parser.add_argument(
        "--lng-inflexible-cuts",
        default=None,
        metavar="CSV",
        help=(
            "boundary_cuts.csv-format file (scene,rhs,<state>...) from a "
            "medium-term run; its column named after the tank is the "
            "tank's terminal-value coefficient (--lng-inflexible-value cuts)."
        ),
    )
    parser.add_argument(
        "--lng-inflexible-diesel-cost",
        type=float,
        default=None,
        metavar="USD_PER_MWH",
        help=(
            "Reference diesel cost [$/MWh] for --lng-inflexible-value "
            "diesel (default: median SRMC of the bundle's diesel units)."
        ),
    )
    parser.add_argument(
        "--gas-bands-as-tanks",
        choices=GAS_TANK_MODES,
        default="off",
        help=(
            "Model every capped gas Fuel band (Fuel_MaxOfftakeWeek) as a "
            "tank: a Reservoir 'gastank_<fuel>' drained by the band's units "
            "(pf = 1/HR), the units carry no fuel price, and the leftover "
            "is valued at the band price in the boundary future-cost cut "
            "(state variable across SDDP phases).  'horizon': one allotment "
            "for the horizon (= the Fuel model minus the constant "
            "sum price*allotment).  'period': one inflow per gas week "
            "(Thursday-Wednesday, from the CSV dates); INF bands lose the "
            "remainder at each week end.  Default: off."
        ),
    )
    parser.add_argument(
        "--gas-usable",
        choices=GAS_USABLE_MODES,
        default="capacity",
        help=(
            "Leftover a gas tank's future value applies to.  Each tank whose "
            "gas outlives the horizon gets its own future-cost variable "
            "alpha_gas_<tank> >= g*(V_usable - T), >= 0 (separate from the "
            "hydro boundary cut).  'capacity' (default): V_usable = min(emax "
            "at the horizon end, units' burn rate x hours until the "
            "deadline / gas-week end) — gas that cannot be burnt in time is "
            "worth 0.  'full': V_usable = emax (no saturation)."
        ),
    )
    parser.add_argument(
        "--gas-refill",
        default="instant",
        metavar="{instant,ramp:<hours>}",
        help=(
            "--gas-bands-as-tanks period: shape of each new allotment "
            "(next ship) entering the tank at the sub-period start.  "
            "'instant' (default; the bundles only give the volume): all of "
            "it in the first hour.  'ramp:<hours>': a constant unloading "
            "rate over that many hours."
        ),
    )
    add_use_single_bus_argument(parser, dialect="store_true")
    add_scale_theta_argument(parser)
    add_color_argument(parser)
    parser.add_argument(
        "--cogen-must-run",
        default=None,
        metavar="NAME[,NAME...]",
        help=(
            "Force these generator names to ``cogen_mode = must_run`` "
            "(LP-pinned pmin == pmax — the converter does NOT alter "
            "pmin / pmax here; only the C++ tag is set, so downstream "
            "audits read 'must-run cogen' and a follow-up pass can "
            "wire the actual pin from PLEXOS Fixed Load / Min Stable "
            "Level data).  Default: every CEN-cogen-detected unit gets "
            "``cogen_mode = dispatched`` (LP-free); names listed here "
            "are upgraded to ``must_run``.  Names not detected as "
            "cogen by the heuristic are still tagged when listed — "
            "the explicit override wins."
        ),
    )
    parser.add_argument(
        "--default-uc-penalty",
        type=float,
        default=None,
        help=(
            "fallback penalty ($/unit-of-RHS) to apply to every "
            "user-constraint whose PLEXOS row carries no Penalty Price. "
            "Soft-cap diagnostic: keeps the LP feasible so the solver "
            "reports per-constraint violations instead of returning "
            "infeasible. Typical: 10000."
        ),
    )
    parser.add_argument(
        "--soft-unpenalised-constraints",
        type=float,
        default=None,
        metavar="PENALTY",
        help=(
            "opt-in feasibility aid: emit the PLEXOS constraints that carry "
            "no Penalty Price (absent or -1) and that the CEN-calibrated "
            "ladder used to soften (operational floors, mixed "
            "line+commitment rows such as PandeAzucar_Polpaico_CC, reserve "
            "aggregates) as soft at PENALTY $/unit.  Default: off, i.e. "
            "they are HARD, as in PLEXOS.  Rows with a PLEXOS Penalty Price "
            "always keep it."
        ),
    )
    parser.add_argument(
        "--pampl-uc-mode",
        choices=("hard", "soft", "off"),
        default="hard",
        help=(
            "how to emit the per-family user-constraint .pampl files: "
            "'hard' (default) keeps each PLEXOS row's own penalty (rows "
            "without one stay hard); 'soft' forces a slack penalty on EVERY "
            "row (uses --default-uc-penalty, or 10000 if unset) so the MIP "
            "stays feasible and reports violations instead of going "
            "infeasible; 'off' drops the user constraints entirely (no "
            ".pampl files, no inline rows). Diagnostic: the hard PLEXOS UCs "
            "can over-constrain integer commitment into infeasibility."
        ),
    )
    parser.add_argument(
        "--pampl-uc-only",
        default=None,
        metavar="FAM[,FAM...]",
        help=(
            "emit ONLY these user-constraint families (comma-separated), "
            "dropping all others.  Diagnostic for isolating which group "
            "over-constrains the MIP — e.g. --pampl-uc-only config_exclusivity. "
            "Families: config_exclusivity, gas_offtake, commitment, reserve, "
            "security, comparison, terminal_value, operational."
        ),
    )
    parser.add_argument(
        "--pampl-uc-off",
        default=None,
        metavar="FAM[,FAM...]",
        help=(
            "drop these user-constraint families (comma-separated), keeping "
            "the rest (leave-one-out diagnostic).  Same family names as "
            "--pampl-uc-only."
        ),
    )
    parser.add_argument(
        "--uc-emit",
        choices=("pampl", "inline"),
        default="pampl",
        help=(
            "where to emit user constraints: 'pampl' (default) writes the "
            "modular per-family .pampl files (system.user_constraint_files); "
            "'inline' keeps them in system.user_constraint_array in the JSON "
            "(the legacy path).  Diagnostic: generate the case both ways and "
            "diff the LPs to check whether GTOpt's PAMPL parser and JSON "
            "parser produce the same matrix."
        ),
    )
    parser.add_argument(
        "--soft-penalty-cost",
        type=float,
        default=None,
        help=(
            "explicit $/MWh override for the shared soft-penalty used by "
            "BOTH the forced-`pmin` floor (Generator.pmin_fcost on "
            "non-renewable Fixed Load units) AND the soft line-overload / "
            "EL=1 slack cost.  When unset (default) the converter computes "
            "min(max(gcost)+1, min(VoLL)-1) from the bundle's own cost "
            "data: high enough to honour forced dispatch / re-dispatch, "
            "capped below the cheapest unserved-demand cost so load-"
            "serving always outranks it."
        ),
    )
    parser.add_argument(
        "--water-fail-cost",
        type=float,
        default=10.0,
        help=(
            "shared $/(m³/s·h) shortfall penalty applied uniformly to "
            "every soft hydro obligation emitted by the converter: "
            "soft FlowRights (Filt_Laja, Caudal_Eco_*, Riego_*, "
            "Ext_*), soft ``discharge_*min`` UserConstraints (turbine "
            "minimum-flow floors), and any future Flow slack on "
            "natural-inflow shortfall.  Default 10 matches plp2gtopt's "
            "``--water-fail-cost`` so PLEXOS- and PLP-derived JSONs "
            "use the same water-obligation pricing.  Higher values "
            "make the LP try harder to meet the soft target before "
            "violating; lower values let it skip the obligation more "
            "freely."
        ),
    )
    parser.add_argument(
        "--loss-pwl-layout",
        choices=("uniform", "equal_error", "tangent", "midpoint", "dynamic"),
        default="dynamic",
        help=(
            "Segment-layout strategy for the PWL line-loss approximation, "
            "emitted on every lossy ``Line.loss_pwl_layout`` entry.  "
            "``dynamic`` (DEFAULT): per-line layout auto-selected by the same "
            "``--loss-error-pct`` budget that drives adaptive K.  Heaviest-"
            "error contributors get ``midpoint`` (negative mean bias); the "
            "rest get ``uniform`` (positive mean bias, but presolve eliminates "
            "the loss column for a faster solve).  The system-wide signed "
            "mean error stays within the budget by construction — uniform "
            "lines' positive bias cancels midpoint lines' negative bias.  "
            "Same single ``--loss-error-pct`` knob controls everything; no "
            "magic thresholds.  Compared with an all-midpoint baseline, "
            "dynamic reduces the number of loss segments, improves LP "
            "conditioning, and tightens the LP-internal-vs-analytic loss "
            "gap.  "
            "``midpoint``: uniform-K de-biased secant (the previous default — "
            "chord shifted DOWN to mid-point tangent so the PWL is unbiased).  "
            "``uniform``: equal-width secant chords (strict UPPER bound; max "
            "chord error peaks on the outer segment — overstates losses).  "
            "``tangent``: outer-approximation tangents (lower bound, exact "
            "at touch points; inequality rows resist presolve binary-fixing "
            "— heavier MIP).  ``equal_error``: √-spaced secant chords "
            "(aliases to uniform for the convex quadratic — see "
            "line_losses.cpp seg_geom docstring)."
        ),
    )
    # ``loss_cost_eps``: UNSET by default — gtopt's built-in 0.1 $/MWh
    # applies (breaks the PWL-loss bidirectional-flow degeneracy and acts
    # as the internal v-pin the tangent_signed_flow L-secant bracket needs
    # when loss_secant_segments > 1 without SOS2).  An explicit value,
    # including ``--loss-cost-eps 0`` to disable, is emitted.
    add_loss_cost_eps_argument(parser, dialect="plexos")
    # ``loss_secant_segments`` default: UNSET (gtopt resolves S=1, the
    # single-secant chord).  Extra secants are inert in pure LP (the
    # chord is inactive at benign optima; the arbitrage ceiling is
    # S-independent), so a global S>1 would add ~3 dead columns per
    # (lossy line, block) — ~140k columns on a PCP week.  Use S>1 PER
    # LINE together with ``loss_use_sos2`` on flagged corridors, where
    # it sets the SOS2 lambda-form resolution (MIP runs).
    add_loss_secant_segments_argument(parser, default=None)
    # plexos2gtopt default = "tangent_signed_flow" (Coffrin outer
    # approximation): one signed flow column + K tangents + 1 chord
    # upper bound per (line, block); 56 % fewer LP nonzeros than the
    # legacy `bidirectional` (`fp/fn/lossp/lossn`) model AND no
    # phantom-flow degeneracy by construction.  The tangent count K is
    # sized per line by the adaptive cube-root rule, capped by
    # `--nseg-losses=10` below.
    add_line_losses_mode_argument(parser, default=DEFAULT_LINE_LOSSES_MODE)
    parser.add_argument(
        "--emin-eod-day1",
        dest="emin_eod_day1",
        action="store_true",
        default=False,
        help=(
            "enforce the PLEXOS operational reservoir floor from "
            "``Hydro_MinVolume.csv`` at the end of day 1 (hour 24) as "
            "a HARD per-block ``emin`` clamp.  Recovers the daily-"
            "coordination signal PLEXOS uses to keep reservoirs above "
            "their published operational floor at midnight of day 1.  "
            "Default OFF.  Pass ``--emin-eod-day1`` to enable.  This "
            "hard hour-24 clamp pins reservoirs near full at midnight of "
            "day 1, blocking day-1 drawdown and inflating reservoir "
            "water-value duals well above PLEXOS (e.g. CANUTILLAR 2.6×, "
            "ELTORO/L_Maule ~1.1-1.2×); disabling it brings the duals "
            "back in line with PLEXOS's storage Shadow Price.  End-of-"
            "week (last day) floors are honoured separately as a soft "
            "``efin`` + ``efin_cost`` slack.  Mid-week (hour 48, 72, …) "
            "floors stay disabled to avoid the L_Maule infeasibility "
            "chain that prompted removing per-block clamps in the first "
            "place."
        ),
    )
    parser.add_argument(
        "--no-emin-eod-day1",
        dest="emin_eod_day1",
        action="store_false",
        help="disable the hour-24 hard emin floor (now the default; "
        "see --emin-eod-day1).",
    )
    parser.add_argument(
        "--battery-efin-pin",
        dest="battery_efin_pin",
        action="store_true",
        default=True,
        help=(
            "pin every battery's end-of-horizon SoC to its initial "
            "SoC (``efin = eini``).  Default ON — forces the LP to "
            "return batteries to their starting state, preventing "
            "off-spec end-of-horizon energy banking that drives "
            "BESS net-charge by ~12 GWh on the CEN PCP weekly "
            "bundle.  Pass ``--no-battery-efin-pin`` to drop the "
            "pin and let the LP set ``efin`` freely (matches "
            "PLEXOS's flexible terminal-SoC convention)."
        ),
    )
    parser.add_argument(
        "--no-battery-efin-pin",
        dest="battery_efin_pin",
        action="store_false",
        help="drop the efin=eini pin (see --battery-efin-pin).",
    )
    parser.add_argument(
        "--drop-batteries",
        dest="drop_batteries",
        action="store_true",
        default=False,
        help=(
            "exclude ALL batteries (BESS) from the converted model, "
            "identified by their PLEXOS class ``Battery`` (NOT by "
            "name-matching).  Gates extraction, SSCC reserve provisions "
            "and every UserConstraint battery coefficient term "
            "(charge / discharge / energy / reserve provision) "
            "consistently: ``battery_array`` is emitted empty, no "
            "``provision_<bat>`` reserve rows are generated, and battery "
            "UC terms are dropped as provably-zero contributions (a "
            "dropped battery supplies 0 generation / load / reserve).  "
            "Default OFF.  Use to produce a battery-free variant of a "
            "PCP bundle without editing the source PLEXOS data."
        ),
    )
    parser.add_argument(
        "--default-storage-loss",
        dest="default_storage_loss",
        action="store_true",
        default=False,
        help=(
            "emit the converter's SYNTHETIC default annual self-discharge / "
            "evaporation loss on storage that PLEXOS leaves unset: batteries "
            "21.5%%/yr (2%%/month Li-ion, BU/NREL/IEA) and reservoirs 4%%/yr "
            "(ICOLD/World Bank Andean mid-range).  Default OFF: batteries "
            "without a PLEXOS Self Discharge Rate are written lossless "
            "(annual_standing_loss 0, the PLEXOS default) and reservoirs "
            "stay lossless.  Enable to "
            "gently regularise the storage-balance chain (LP conditioning) "
            "and document the physical loss explicitly."
        ),
    )
    # Global hard/soft reservoir-floor toggle shared with plp2gtopt.  When
    # soft (the default) each reservoir's ``efin`` floor is priced in Python
    # (``WaterValueResolver`` → ``Reservoir.efin_cost``);
    # ``--no-soft-storage-bounds`` makes every ``vol_end >= efin`` a HARD
    # constraint instead.
    add_soft_storage_bounds_argument(parser)
    parser.add_argument(
        "--nseg-losses",
        type=int,
        default=10,
        help=(
            "Piecewise-linear loss-segment / tangent count.  Default = **10** "
            "(the K parameter for the Coffrin ``tangent_signed_flow`` model, "
            "and the per-direction PWL-segment count under legacy modes).  "
            "Interpretation depends on ``--loss-error-pct``:\n"
            "  * Adaptive mode (default, ``--loss-error-pct > 0``): "
            "``--nseg-losses`` is the CEILING on the per-line K computed "
            "by the cube-root rule (``K_i ∝ L_max,i^(1/3)``), rounded UP "
            "to the next EVEN integer (an odd K wastes the zero-slope "
            "middle tangent under ``tangent_signed_flow``).\n"
            "  * Uniform mode (``--loss-error-pct 0``): every lossy line "
            "gets exactly ``--nseg-losses`` segments.\n"
            "Floor is always 2 (a single secant is degenerate).  "
            "L_max,i = R·fmax² (peak loss MW); global scale c = √(S/(4·B)) "
            "with S = Σ L^(1/3) and B = ``--loss-error-pct × Σ L_max``.  "
            "Worst-case per-segment error scales as 1/K² under the "
            "``midpoint`` de-bias."
        ),
    )
    parser.add_argument(
        "--loss-extend-overload",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "DEFAULT ON (since the Coffrin loss mode became default).  "
            "Extend each soft-cap / lifted line's PWL "
            "``loss_envelope`` from ``[0, tmax_normal]`` to "
            "``[0, headroom_factor × tmax_normal]`` (= the full lifted "
            "tmax_ab) so the segments cover the overload band the LP can "
            "actually flow into.  Adaptive K (``--loss-error-pct``) sizes "
            "K_i for the extended envelope so the worst-case secant-error "
            "budget is honoured across the wider range.\n"
            "\n"
            "When ON:\n"
            "  * EL=1 / EL=2 (hard cap, LP cannot exceed): unchanged.\n"
            "  * EL=0 soft-cap (regular):       envelope → 2 × tmax_normal.\n"
            "  * --lift-line-caps (wider band): envelope → 4 × tmax_normal.\n"
            "\n"
            "WHY ON IS NOW THE DEFAULT — the trade-off below is a "
            "PIECEWISE/secant concern only.  The current default loss mode "
            "``tangent_signed_flow`` (Coffrin) uses LOWER tangents, exact at "
            "their touch points and never over-stating loss, and "
            "``loss_cost_eps`` removes the loss-arbitrage that made the secant "
            "bias bite — so a wider envelope costs no accuracy under the "
            "default mode.  Meanwhile OFF makes the loss-PWL domain double as a "
            "flow ceiling at the ORIGINAL rating, stranding demand behind "
            "lifted corridors (e.g. ``load_AltoNorte110`` on the CEN far-north "
            "500 kV haul shed 8 GWh purely because its feeder envelope stayed "
            "122 while tmax was lifted to 610).  The legacy secant trade-off in "
            "three flow regimes (relevant only with ``--line-losses-mode "
            "piecewise``):\n"
            "\n"
            "  Regime A (f ∈ [0, tmax_normal]):  the same K segments are\n"
            "    now spread over 2× the range, so per-segment width is\n"
            "    2× wider and worst-case secant error grows as width² →\n"
            "    **4× worse** at any flow in the normal operating band.\n"
            "    Adaptive K partly compensates (K_i grows ~1.59× since\n"
            "    L_max grows 4× and K_i ∝ L_max^(1/3)), but the ceiling=6\n"
            "    clamp usually binds on heavy lines, so most of the 4×\n"
            "    degradation survives in practice.\n"
            "\n"
            "  Regime B (f ∈ (tmax_normal, (1 + 1/K) · tmax_normal)):  OFF's\n"
            "    linear extrapolation of the last segment's slope still\n"
            "    beats ON's wider-segment secant approximation.  Crossover\n"
            "    is at f = tmax_normal · (1 + 1/K):\n"
            "          K = 2  → f_cross = 1.50 × tmax_normal\n"
            "          K = 3  → f_cross = 1.33 × tmax_normal\n"
            "          K = 4  → f_cross = 1.25 × tmax_normal\n"
            "          K = 6  → f_cross = 1.17 × tmax_normal\n"
            "          K = 8  → f_cross = 1.13 × tmax_normal\n"
            "\n"
            "  Regime C (f > (1 + 1/K) · tmax_normal):  OFF's extrapolation\n"
            "    error grows quadratically as (f − tmax_normal)², so ON\n"
            "    eventually wins.  At f = 2·tmax_normal (the lifted cap),\n"
            "    OFF underestimates loss by ~25%% of the true value while\n"
            "    ON stays bounded by the same per-segment budget as below.\n"
            "\n"
            "For healthy systems with slack capacity, the LP almost never\n"
            "leaves Regime A.  Measured on the CEN PCP weekly LP-relax\n"
            "(K=4 uniform and adaptive @ 1%% / 5%% / 10%%):\n"
            "  * 0 of 175 soft-cap lines  ever cross tmax_normal\n"
            "  * max line utilization      = 80%% of tmax_normal\n"
            "  * → every operating point is in Regime A where OFF is\n"
            "    strictly better.\n"
            "Turning ON in that scenario gives 4× worse PWL fidelity in\n"
            "[0, tmax_normal] for **zero offsetting benefit** anywhere the\n"
            "LP actually operates.\n"
            "\n"
            "HEURISTIC: keep ON (default) with the Coffrin loss mode — it\n"
            "preserves feasibility on lifted corridors at no accuracy cost.\n"
            "Pass ``--no-loss-extend-overload`` only when running the legacy\n"
            "``--line-losses-mode piecewise`` on a healthy slack-capacity\n"
            "system, where the secant trade-off above favours the\n"
            "original-rating envelope."
        ),
    )
    parser.add_argument(
        "--loss-error-pct",
        type=float,
        default=0.01,
        help=(
            "Target absolute system-loss-error budget as a fraction of "
            "Σ L_max,i (the sum of per-line peak losses ``R·fmax²``).  "
            "Default **0.01** (1%%); the adaptive cube-root rule then "
            "allocates per-line ``K_i ∝ L_max,i^(1/3)`` so the LP-side "
            "PWL approximation has bounded total worst-case error in "
            "MW.  Pass ``0`` to disable adaptation and force the legacy "
            "uniform-K behaviour (every line gets ``--nseg-losses`` "
            "segments).  Higher values (e.g. ``0.02``) shrink the LP "
            "faster but accept more loss error; lower values (e.g. "
            "``0.005``) tighten accuracy at the cost of more LP "
            "non-zeros.  The per-line K stays clamped to "
            "[2, ``--nseg-losses``] regardless of budget."
        ),
    )
    # NOTE: ``--loss-tangent-top-pct`` (the loading-classified R·P²
    # percentile RANKING that put the top-P% lossiest lines on the tangent
    # layout) was REMOVED.  The ``midpoint`` de-bias + per-line
    # ``loss_envelope`` decoupling give adequate loss accuracy at K=4 without
    # the MIP-heavy hybrid tangent tier, so the ranking is obsolete.  The
    # explicit ``--loss-tangent-lines`` escape hatch remains below.
    parser.add_argument(
        "--loss-tangent-lines",
        type=str,
        default=None,
        metavar="NAME[,NAME...]",
        help=(
            "Comma-separated line names forced to the ``tangent`` loss "
            "layout (with ``--nseg-tangent`` segments); all other lines use "
            "the ``--loss-pwl-layout`` base layout.  Useful for known "
            "binding interconnections."
        ),
    )
    parser.add_argument(
        "--nseg-tangent",
        type=int,
        default=6,
        help=(
            "Segment count for ``tangent``-layout lines in hybrid mode "
            "(default 6).  Tangent uses inequality (outer-approximation) "
            "rows that resist presolve binary-fixing, so keep it coarse."
        ),
    )
    parser.add_argument(
        "--nseg-uniform",
        type=int,
        default=4,
        help=(
            "Segment count for the base (``uniform``/``midpoint``) layout "
            "lines — i.e. ALL lines except those in ``--loss-tangent-lines`` "
            "(the default).  DEFAULT 4: with the ``midpoint`` de-bias + "
            "envelope decoupling, K=4 is the loss-accuracy sweet spot; "
            "more segments add LP rows for little benefit.  These layouts "
            "use segment-variable rows that presolve handles cheaply."
        ),
    )
    parser.add_argument(
        "--loss-sos2-lines",
        type=str,
        default=None,
        metavar="NAME[,NAME...]",
        help=(
            "Comma-separated line names that get the L-secant chord "
            "(``loss_secant_segments = loss_segments``) + SOS2 "
            "fill-order enforcement (``loss_use_sos2 = true``).  Only "
            "meaningful when the line ends up in ``line_losses_mode = "
            "tangent_signed_flow`` at LP-build time.  Composes with "
            "``--loss-sos2-auto``: the final set is the UNION of both "
            "selectors (this manual list always wins over an ``off`` "
            "auto-rule)."
        ),
    )
    parser.add_argument(
        "--loss-sos2-auto",
        type=str,
        default=None,
        choices=("off", "heavy", "all-lossy"),
        help=(
            "Auto-rule that picks the offender lines for SOS2 fill-order "
            "enforcement.  ``off`` (default when unset) ⇒ no auto pick "
            "(``--loss-sos2-lines`` is the only source).  ``heavy`` ⇒ "
            "lines in the TOP QUARTILE by peak loss ``R·envelope²`` get "
            "stamped.  ``all-lossy`` ⇒ every line with ``R > 0`` and "
            "non-zero peak loss gets stamped — aggressive, MIP-heavy.  "
            "Composes with ``--loss-sos2-lines`` (union)."
        ),
    )
    parser.add_argument(
        "--auto-lift-lines",
        nargs="?",
        type=float,
        const=DEFAULT_THRESHOLD,
        default=None,
        metavar="THRESHOLD",
        help=(
            "After writing the bundle JSON, run a lifted-cap DC OPF on "
            "the first (scenario, block) and post-patch "
            "``enforce_level = 0`` onto every line whose lifted-OPF "
            "flow exceeds its rated ``tmax_ab`` by at least THRESHOLD "
            "(default 1.0 = strictly above rated).  OPF engine selected "
            "via ``--auto-lift-engine``.  Mirrors PLEXOS's voltage-"
            "conditional cap treatment on radial step-down lines (e.g. "
            "``Capricornio110->LaNegra110`` on CEN PCP, 76 MW rated).  "
            "Composes with "
            "``--lift-line-caps``: the auto-detected set is the UNION "
            "with the comma-separated names there.  Falls back to a "
            "logged warning + empty patch when the engine is "
            "unavailable.  Pass without a value to use the default "
            "threshold."
        ),
    )
    parser.add_argument(
        "--auto-lift-engine",
        choices=("pandapower", "gtopt"),
        default="pandapower",
        help=(
            "OPF engine for ``--auto-lift-lines``.  ``pandapower`` "
            "(default): runs ``pp.rundcopp`` on the first block via "
            "``gtopt2pp.convert`` (fast); needs the "
            "``pandapower`` + ``gtopt2pp`` Python packages.  ``gtopt``: "
            "runs the actual ``gtopt --no-mip`` binary on a sibling "
            "bundle with every line demoted to ``enforce_level = 0``, "
            "then reads the first-block flows from the parquet output; "
            "slower; needs only the ``gtopt`` binary "
            "in ``$PATH`` (or via ``$GTOPT_BIN``).  Use ``gtopt`` when "
            "you want the EXACT loss model + voltage assumptions the "
            "downstream solve will see, ``pandapower`` for speed."
        ),
    )
    parser.add_argument(
        "--hydro-min-mode",
        choices=("soft", "hard"),
        default="soft",
        help=(
            "How to enforce hydro per-plant minimum-generation / ramp "
            "UserConstraints (``ANTUCOmin``, ``ANTUCOmax``, "
            "``ANGOSTURAmin``, ``ELTOROmax``, ``PANGUEramp``, "
            "``MACHICURAlag*``, ``MACHICURAramp*``, ``COLBUNmax`` and "
            "similar named after a single hydro plant).  Default "
            "``soft`` ($10/MWh soft tier): the LP slacks the floor / "
            "cap when the natural-inflow / commitment combination of a "
            "given block can't satisfy it.  PLEXOS itself solves these "
            "as HARD constraints, BUT gates them internally on the "
            "unit's commit status (when PLEXOS keeps the unit OFF the "
            "min/max row is auto-relaxed).  GTOpt does NOT have a "
            "native commit-gated UC primitive yet, so hardening them "
            "without the gate makes the LP primal-infeasible at "
            "blocks where the hydro is fractionally / fully off "
            "(verified on CEN PCP 2026-04-22: ``antucomin_constraint_"
            "1263_1_1_40`` reported infeasible by the LP presolve when "
            "added to the hard list).  ``hard`` mode is available for "
            "debug / validation runs against a known-feasible "
            "horizon, BUT WILL FAIL on most CEN PCP weekly cases until "
            "gtopt grows commit-gated UC support (related: Plan 2 "
            "``_auto_promote_hydro_min_max_to_commitments`` covers "
            "ANTUCO/ELTORO when the LHS is a single ``generator(X)"
            ".generation`` term — broader patterns still fall through "
            "to this knob)."
        ),
    )
    parser.add_argument(
        "--spill-fcost",
        type=float,
        default=None,
        help=(
            "override the per-flow spill cost ($/(m³/s·h)) on every "
            "``Vert_*`` spillway waterway.  When unset (default), the "
            "converter uses PLEXOS's ``Max Flow Penalty`` property "
            "(typically 3.6 on CEN PCP).  Set this to a larger value "
            "(e.g. 1000) to discourage the LP from routing surplus "
            "water through Vert_* arcs, pushing it toward turbines / "
            "bypasses / other paths instead.  Useful for tuning the "
            "turbine-vs-spill tradeoff when comparing against PLEXOS."
        ),
    )
    parser.add_argument(
        "--use-plexos-commit",
        action="store_true",
        default=False,
        help=(
            "override per-period generator pmax with PLEXOS-solved "
            "Units Generating (pid 7) from the solution .accdb cache. "
            "Pins gen=0 at hours PLEXOS left the unit OFF.  Used to "
            "validate that GTOpt's hydro over-dispatch is driven by "
            "the missing MIP commitment decisions: applying this "
            "forces dispatch into the PLEXOS-chosen ON windows only, "
            "and downstream cascade hydro should snap to PLEXOS's "
            "observed values."
        ),
    )
    add_lift_line_caps_argument(parser, dialect="plexos")
    add_no_lift_lines_argument(parser, dialect="plexos")
    parser.add_argument(
        "--renewable-floors",
        choices=RENEWABLE_FLOOR_MODES,
        default="free",
        help=(
            "Floors of solar, wind and run-of-river commitments "
            "(PLEXOS Min Stable Level and Commit=+1 forcing).  'free' "
            "(DEFAULT, clean): dropped, so the unit can always curtail to 0 "
            "(forced-OFF blocks and the status column stay).  'plexos' "
            "(--legacy-profile cen): kept as PLEXOS gives them.  Storage "
            "hydro and units with a start-up / shut-down / no-load cost are "
            "never touched; ramp and min up / down limits stay.  The "
            "dropped units are recorded in the provenance sidecar "
            "('renewable_floors')."
        ),
    )
    parser.add_argument(
        "--el0-lines",
        choices=EL0_LINES_MODES,
        # CLI spelling of EL0_LINES_DEFAULT ("strict").
        default="enforced",
        help=(
            "How to model PLEXOS lines whose rating PLEXOS does not enforce "
            "(EL=0 'Never enforce', and EL=1 lines below the Transmission "
            "'Constraint Voltage Threshold'):\n"
            "  'enforced' (alias 'strict', DEFAULT, clean): treat EL=0 like "
            "EL=2, a hard cap at the rating; --lift-line-caps (whose default "
            "ships a curated set) lifts the known exceptions to a soft cap.\n"
            "  'unenforced' (--legacy-profile cen, mirrors PLEXOS): the "
            "rating is never "
            "enforced and carries no penalty.  GTOpt bounds every flow "
            f"column, so the cap is {LINE_UNENFORCED_FACTOR:g}x the rating "
            "(a flow AT that cap is a DC-OPF artefact, not a limit); the "
            "loss envelope stays at the rating.  Overrides --lift-line-caps "
            "for these lines; only --no-lift-lines still pins one to a hard "
            "cap.\n"
            "  'extended' (opt-in): soft cap for every EL=0 line, free up to "
            "the rating, penalised up to a headroom multiple, hard there.\n"
            "The mode is recorded in the provenance sidecar ('el0_lines')."
        ),
    )
    parser.add_argument(
        "--water-value-factor",
        metavar="RES:FACTOR[,RES:FACTOR...]",
        default=None,
        help=(
            "Apply a per-reservoir factor to the terminal water-value slope "
            "of named reservoirs in boundary_cuts.csv, moving the valuation of "
            "stored water up/down (a real economic change, NOT a numerical "
            "scale).\n"
            "  Form: 'COLBUN:0.9,RALCO:0.85' — multiply each reservoir's "
            "boundary-cut coefficient by the given factor (unlisted reservoirs "
            "keep 1.0).  A factor <1 makes hoarding inflow less rewarding so the "
            "LP turbines it (releases the over-conservation); >1 does the "
            "reverse.  Use to calibrate GTOpt's single flat terminal cut toward "
            "the observed PLEXOS dispatch on heads that over-bank (e.g. "
            "RALCO/COLBUN)."
        ),
    )
    parser.add_argument(
        "--reservoir-spillway",
        nargs="?",
        const="basic",
        default=None,
        choices=("basic", "strict"),
        help=(
            "activate Reservoir-internal spillway with cost=0 on every real "
            "reservoir.  Two modes:\n"
            "  basic (default when flag is bare): ONLY emit "
            "Reservoir.spillway_cost=0.  All other spillway mechanisms "
            "(Junction.drain, Vert_* waterways, fmax=∞+fcost>0 arcs) stay "
            "active — the LP picks whichever is cheapest, typically the "
            "free reservoir spillway.\n"
            "  strict: emit Reservoir.spillway_cost=0 AND disable every "
            "DUPLICATE spillway mechanism on real reservoirs — removes "
            "Junction.drain on real-reservoir junctions, drops any "
            "Vert_<X> waterway that survived the collapse, and clears "
            "fcost on pure-spillway waterways (fmax=∞ + fcost>0) that "
            "originate at a real reservoir.  Use this to FORCE all "
            "spillage through the reservoir's own internal drain "
            "(matches PLEXOS's hidden Storage-state spillage exactly)."
        ),
    )
    parser.add_argument(
        "--use-plexos-gen-cap",
        action="store_true",
        default=False,
        help=(
            "hard-cap per-period generator pmax to PLEXOS-solved "
            "Generation (pid 2) from the solution .accdb cache. "
            "TIGHTEST curve-fit: every block, GTOpt's LP can dispatch "
            "at most what PLEXOS dispatched in that block.  Useful for "
            "validating that GTOpt's overshoot is purely from missing "
            "per-block dispatch envelopes vs structural differences. "
            "Restricted to HYDRO TURBINE generators to avoid the "
            "ReserveProvisionLP::flat_map::at defect at zero-pmax "
            "blocks on thermal units."
        ),
    )
    parser.add_argument(
        "--use-plexos-efin",
        action="store_true",
        default=False,
        help=(
            "DEBUG: pin Reservoir.efin to the PLEXOS-SOLVED End Volume "
            "(prop 646, last period) from the solution .accdb cache, "
            "curve-fitting GTOpt's terminal storage to PLEXOS's answer. "
            "DEFAULT (flag off) is fully input-driven: the end-of-horizon "
            "target is the last-day floor from Hydro_MinVolume.csv (the "
            "operational target PLEXOS encodes by raising the min-volume "
            "floor at the final period)."
        ),
    )
    parser.add_argument(
        "--apply-generation-aux-use",
        action="store_true",
        default=False,
        help=(
            "Apply Gen_AuxUse.csv (PLEXOS generation Auxiliary Use) as "
            "Generator.lossfactor — the LP injects (1 - lossfactor) x "
            "generation to the bus, modelling station-service "
            "self-consumption.  DEFAULT (flag off) IGNORES it: the CEN PCP "
            "bundle ships the file but the source model does not apply it, "
            "so applying it adds station-service fuel burn the source "
            "model does not carry."
        ),
    )
    parser.add_argument(
        "--vert-routing",
        choices=("ocean", "cascade"),
        default="ocean",
        help=(
            "destination of every ``Vert_*`` spillway waterway. "
            "DIAGNOSTIC TOGGLE — exists solely to reproduce a "
            "strange PLEXOS behaviour (mid-cascade Vert spillage) "
            "that is not natural to our LP; not a physical "
            "modelling choice.  ``ocean`` (default): every Vert_* "
            "→ <source>_ocean drain; spillage LEAVES the topology. "
            "``cascade``: keep PLEXOS's published Tail Storage "
            "target so spillage feeds the next cascade junction "
            "(can be re-turbined downstream); falls back to ocean "
            "when PLEXOS publishes no downstream target.  Default "
            "is whichever produces dispatch closer to PLEXOS on "
            "the reference bundle (currently ``ocean``)."
        ),
    )
    parser.add_argument(
        "--spill-fcost-scale",
        type=float,
        default=1.0,
        help=(
            "multiplicative scale on PLEXOS's ``Max Flow Penalty`` for "
            "every ``Vert_*`` spillway waterway.  Default 1.0 (no "
            "rescaling).  Applied AFTER ``--spill-fcost`` (if set), so "
            "the two are composable.  Useful when the relative cost "
            "structure is right but the absolute level is wrong."
        ),
    )
    parser.add_argument(
        "--horizon-mode",
        choices=("plexos", "hourly"),
        default="plexos",
        help=(
            "block-layout mode.  ``plexos`` (default) reproduces "
            "PLEXOS's exact block distribution from the solution "
            "``.accdb`` (``t_phase_3`` table) — for CEN PCP daily "
            "that's 111 blocks over 7 days with [24, 20, 13, 14, 12, "
            "15, 13] per day.  Falls back to uniform daily blocks "
            "from ``PLEXOS_Param.xml`` band counts when no .accdb is "
            "available.  ``hourly`` emits ``--horizon-days × 24`` "
            "uniform hourly blocks (168 for a full week) with no "
            "aggregation."
        ),
    )
    parser.add_argument(
        "--horizon-days",
        type=int,
        default=None,
        choices=range(1, 8),
        metavar="N",
        help=(
            "number of consecutive days to convert (1-7).  In "
            "``--horizon-mode hourly`` defaults to 1 (legacy "
            "behaviour).  In ``--horizon-mode plexos`` the day count "
            "is derived from the PLEXOS solution and this flag is "
            "ignored."
        ),
    )
    parser.add_argument(
        "--horizon-end-targets",
        choices=END_TARGET_MODES,
        default="auto",
        help=(
            "end-of-horizon storage targets when --horizon-days N cuts the "
            "PLEXOS horizon short.  'plexos': efin = the PLEXOS solution "
            "state at the end of day N (Storage End Volume, Battery "
            "Energy; needs the RES bundle).  'eini': efin = eini.  'free': "
            "no efin.  'auto' (default): 'plexos' when the RES solution "
            "is found, else 'eini' (warned); no change on a full horizon.  "
            "Targets are clamped into [emin, emax]; batteries always get "
            "a soft target and no hard daily-cycle close."
        ),
    )
    parser.add_argument(
        "--horizon-boundary-cut",
        choices=BOUNDARY_CUT_MODES,
        default="auto",
        help=(
            "FCF boundary cut (end-of-week future cost) under --horizon-days "
            "N.  'auto' (default): drop it on a truncated horizon with "
            "plexos/eini targets, keep it otherwise.  'keep' / 'drop' force."
        ),
    )
    parser.add_argument(
        "--horizon-constraint-rules",
        default=None,
        metavar="FAMILY=RULE[,...]",
        help=(
            "multi-day constraint rules on a truncated horizon.  Families "
            "(default first): fuel_max_offtake=prorate|full|drop, "
            "fuel_min_offtake=prorate|drop, period_rhs=prorate|drop, "
            "day_indexed=keep|drop.  'prorate' keeps the weekly budget "
            "scaled to N days, 'full' allows the whole weekly budget, "
            "'drop' removes / deactivates the rows."
        ),
    )
    parser.add_argument(
        "--horizon-pin-lng-tanks",
        action="store_true",
        default=False,
        help=(
            "also pin LNG tanks (LNG terminals, *_GNL* storages, storages "
            "feeding fuel-burning units) to their end-of-day-N target.  "
            "Default: left free -- their end level is the take-or-pay "
            "decision, priced by the gas terminal value."
        ),
    )
    parser.add_argument(
        "--plexos-solution-accdb",
        type=Path,
        default=None,
        help=(
            "path to the PLEXOS solution ``.accdb`` (PLEXOS >= 9) or "
            "``.mdb`` (PLEXOS 8.x), used to extract the block layout "
            "under ``--horizon-mode plexos``.  When omitted, the "
            "converter auto-discovers a sibling ``RES<date>.zip[.xz]`` "
            "next to the input bundle and extracts the nested database."
        ),
    )
    # Shared --source-version (help generated from SOURCE_FORMAT); the
    # version detected from the XML t_config 'Version' is the default.
    add_source_version_argument(parser, SOURCE_FORMAT, dest="plexos_version")
    parser.add_argument(
        "--plexos-version",
        dest="plexos_version",
        default=None,
        metavar="X",
        help=(
            "alias of --source-version (per-version differences are "
            "listed at the end of --help)"
        ),
    )
    parser.add_argument(
        "--block-layout",
        default=None,
        metavar="CSV|SPEC",
        help=(
            "user-defined block layout for ``--horizon-mode plexos`` when "
            "no PLEXOS solution .accdb is available (the 111-block grouping "
            "lives only in the solution).  Accepts EITHER a path to a CSV "
            "with a 'duration' column (one row per block, optional "
            "'block_uid' column for order), OR an inline string: "
            "'{1:1,2:4,3:2}' (block_uid:duration) or '1,4,2' (durations in "
            "block order).  duration = consecutive 1-hour intervals per "
            "block; the converter builds the chronological grouping and "
            "sets the horizon from the total.  Falls back to uniform hourly "
            "when neither a solution nor this flag is provided."
        ),
    )
    parser.add_argument(
        "--plexos-model",
        default=None,
        metavar="NAME",
        help=(
            "materialise this PLEXOS Model of a multi-model database "
            "(e.g. the SEM PLEXOS Forecast Model) before converting: keep "
            "only data of the Model's Scenarios, resolve dated rows, "
            "timeslice patterns, Variables and Data Files at the horizon "
            "and emit per-hour schedules.  Use with --horizon-mode hourly "
            "--horizon-days N and --plexos-start-date."
        ),
    )
    parser.add_argument(
        "--plexos-start-date",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "first day of the horizon for --plexos-model (default: the "
            "database Horizon's 'Date From')"
        ),
    )
    parser.add_argument(
        "--plexos-sample",
        type=int,
        default=1,
        metavar="K",
        help=(
            "sample column (1-based) read from multi-sample PLEXOS Data "
            "Files, e.g. the weather year of a stochastic demand/wind "
            "profile (default 1)"
        ),
    )
    parser.add_argument(
        "--plexos-data-dir",
        action="append",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "directory searched (recursively) for the CSV Data Files a "
            "--plexos-model refers to; repeatable.  The input's own "
            "directory is always searched; files may be xz-compressed."
        ),
    )
    parser.add_argument(
        "--plexos-period-rhs",
        choices=PERIOD_RHS_MODES,
        default="prorate",
        help=(
            "PLEXOS Constraints with an 'RHS Week' / 'RHS Month' / 'RHS "
            "Year' budget (e.g. yearly hours-of-operation limits): "
            "'prorate' (default) emits one row summing the LHS over the "
            "horizon with RHS x horizon hours / period hours (exact when "
            "the horizon is the period, an approximation otherwise); "
            "'drop' does not convert them"
        ),
    )
    parser.add_argument(
        "--lp-relax",
        action="store_true",
        default=False,
        help=(
            "emit ``Commitment.relax = true`` on every commitment so "
            "gtopt LP-relaxes the binary status / startup / shutdown "
            "variables.  Default (since 2026-05-23) is MIP — commitments "
            "ship without ``relax`` so GTOpt enforces integrality.  "
            "LP-relaxing lets the ``<plant>_Uniq`` mutexes go fractional "
            "and understates operational cost.  Pass ``--lp-relax`` for the "
            "legacy LP-only behaviour (faster solve, looser dispatch) "
            "or for solvers without MIP support."
        ),
    )
    parser.add_argument(
        "--uc-relax",
        choices=UC_RELAX_MODES,
        default="auto",
        help=(
            "which commitments ship LP-relaxed (``Commitment.relax = true``, "
            "continuous u) in MIP mode.  'auto' (default): units PLEXOS "
            "solves as Linear Relaxation (Generator 'Unit Commitment "
            "Optimality' = 0 in the run Model) plus cost-free commitments "
            "(zero energy cost, no start-up / shut-down / no-load cost, no "
            "min up / down time, no start limit, no ramp limit — e.g. solar, "
            "wind, run-of-river with only a Min Stable Level).  'plexos': "
            "only the PLEXOS Linear-Relaxation units.  'none': every "
            "commitment integer.  Forced blocks (Gen_Commit) stay pinned "
            "either way.  See docs/scripts/plexos2gtopt.md"
        ),
    )
    parser.add_argument(
        "--uc-cycle",
        choices=("none", "stage"),
        default="none",
        help=(
            "'stage': cyclic unit commitment over each stage (typical "
            "day/week): commitments get cycle='stage' and no initial state"
        ),
    )
    parser.add_argument(
        "--long-transition",
        choices=("on", "off", "no_uc"),
        default=None,
        help=(
            "with --uc-cycle stage: declare long_transition on every unit "
            "whose min up/down time exceeds the typical period (the list "
            "is printed).  Without it those units stay undeclared and "
            "gtopt rejects the case listing them"
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    add_operator_argument(parser)
    add_sanity_report_argument(parser)
    add_results_arguments(
        parser,
        source="PLEXOS",
        what="a solution .accdb, a RES*.zip[.xz] bundle or a directory of "
        "t_*.csv solution tables as gtopt2plexos --results writes",
        auto="--plexos-solution-accdb, the RES bundle next to the input, or "
        "<input>/plexos_solution",
    )
    return parser


def _import_results(
    args: argparse.Namespace, bundle: Path, options: dict[str, Any]
) -> int:
    """``--results``: import a PLEXOS solution next to the converted case."""
    # pylint: disable=import-outside-toplevel
    from .plexos_solution import open_solution
    from .results_import import import_plexos_results, open_results

    out_dir, case_json, _name = _resolve_output_paths(
        bundle,
        options.get("output_dir"),
        options.get("output_file"),
        options.get("name"),
    )
    out = results_output_dir(Path(out_dir), "plexos", args.results_output)
    try:
        if args.results == RESULTS_AUTO:
            sol = open_solution(accdb=args.plexos_solution_accdb, input_bundle=bundle)
            local = Path(bundle) / "plexos_solution"
            if sol is None and local.is_dir():
                sol = open_results(local)
        else:
            sol = open_results(Path(args.results))
        if sol is None:
            raise FileNotFoundError("no PLEXOS solution found (pass --results PATH)")
        res = import_plexos_results(
            sol, load_planning(Path(case_json)), out, Path(out_dir)
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"error: --results: {exc}", file=sys.stderr)
        return 1
    report_import(res, out)
    return 0


def _resolve_bundle(args: argparse.Namespace) -> Path | None:
    """Pick ``-i`` / ``--input-bundle`` over the positional argument."""
    input_bundle: Path | None = args.input_bundle
    if input_bundle is not None:
        return input_bundle
    positional: Path | None = args.positional_input
    return positional


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and dispatch to the right action."""
    install_termination_handlers()

    parser = make_parser()
    args = parser.parse_args(argv)

    # ``--from-state PATH`` reload: rebuild args from a prior snapshot
    # so the current invocation reproduces the original byte-for-byte
    # (modulo any explicit overrides on the current CLI line).  The
    # snapshot file is written at the START of every plexos2gtopt run
    # to ``<output_dir>/plexos2gtopt_state.json`` — see the per-output
    # ``README.md`` for the format.
    if args.from_state is not None:
        from gtopt_shared.state_snapshot import (  # noqa: PLC0415
            apply_state_to_args,
            load_state_snapshot,
        )

        try:
            payload = load_state_snapshot(Path(args.from_state))
        except (FileNotFoundError, ValueError) as exc:
            parser.error(f"--from-state: {exc}")
        else:
            args = apply_state_to_args(
                parser,
                payload["args"],
                list(argv) if argv is not None else sys.argv[1:],
            )

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    apply_legacy_profile(
        args,
        PLEXOS_LEGACY_PROFILES,
        alias_dest="plexos_legacy",
        alias_flag="--plexos-legacy",
        argv=list(argv) if argv is not None else sys.argv[1:],
    )

    bundle = _resolve_bundle(args)
    if bundle is None:
        parser.error(
            "input bundle is required: pass it positionally or via --input-bundle"
        )
    select_operator(args.operator, "plexos", bundle, args)

    try:
        parse_constraint_rules(getattr(args, "horizon_constraint_rules", None))
    except ValueError as exc:
        parser.error(str(exc))

    options: dict[str, Any] = {
        "input_bundle": bundle,
        "output_dir": args.output_dir,
        "use_single_bus": args.use_single_bus,
        "scale_theta": args.scale_theta,
        "default_uc_penalty": args.default_uc_penalty,
        "soft_unpenalised_constraints": args.soft_unpenalised_constraints,
        "pampl_uc_mode": args.pampl_uc_mode,
        "pampl_uc_only": args.pampl_uc_only,
        "pampl_uc_off": args.pampl_uc_off,
        "uc_emit": args.uc_emit,
        "soft_penalty_cost": args.soft_penalty_cost,
        "water_fail_cost": args.water_fail_cost,
        "default_storage_loss": args.default_storage_loss,
        "spill_fcost": args.spill_fcost,
        "spill_fcost_scale": args.spill_fcost_scale,
        "vert_routing": args.vert_routing,
        "use_plexos_commit": args.use_plexos_commit,
        "use_plexos_gen_cap": args.use_plexos_gen_cap,
        "use_plexos_efin": args.use_plexos_efin,
        "apply_generation_aux_use": args.apply_generation_aux_use,
        "nseg_losses": args.nseg_losses,
        "loss_error_pct": args.loss_error_pct,
        "loss_extend_overload": args.loss_extend_overload,
        "loss_pwl_layout": args.loss_pwl_layout,
        "loss_cost_eps": args.loss_cost_eps,
        "loss_secant_segments": args.loss_secant_segments,
        "line_losses_mode": args.line_losses_mode,
        # Forwarded to ``options.write_out`` (see
        # ``gtopt_writer.build_options``).  ``getattr`` with the canonical
        # default keeps in-tree fixtures that build a minimal Namespace
        # working without rewiring.
        "write_out": getattr(args, "write_out", None),
        "hydro_min_mode": args.hydro_min_mode,
        "loss_tangent_lines": args.loss_tangent_lines,
        "nseg_tangent": args.nseg_tangent,
        "nseg_uniform": args.nseg_uniform,
        "loss_sos2_lines": args.loss_sos2_lines,
        "loss_sos2_auto": args.loss_sos2_auto,
        "emin_eod_day1": args.emin_eod_day1,
        "battery_efin_pin": args.battery_efin_pin,
        "drop_batteries": args.drop_batteries,
        "soft_storage_bounds": getattr(args, "soft_storage_bounds", True),
        "auto_lift_lines": args.auto_lift_lines,
        "auto_lift_engine": args.auto_lift_engine,
        "reservoir_spillway": args.reservoir_spillway,
        "water_value_factor": args.water_value_factor,
        "lift_line_caps": args.lift_line_caps,
        "no_lift_lines": args.no_lift_lines,
        "el0_lines": args.el0_lines,
        "renewable_floors": getattr(args, "renewable_floors", "free"),
        "horizon_mode": args.horizon_mode,
        "horizon_days": args.horizon_days,
        "horizon_end_targets": getattr(args, "horizon_end_targets", "auto"),
        "horizon_boundary_cut": getattr(args, "horizon_boundary_cut", "auto"),
        "horizon_constraint_rules": getattr(args, "horizon_constraint_rules", None),
        "horizon_pin_lng_tanks": getattr(args, "horizon_pin_lng_tanks", False),
        "block_layout": args.block_layout,
        "plexos_solution_accdb": args.plexos_solution_accdb,
        "plexos_version": getattr(args, "plexos_version", None),
        "lp_relax": args.lp_relax,
        "uc_relax": getattr(args, "uc_relax", "auto"),
        "uc_cycle": getattr(args, "uc_cycle", "none"),
        "long_transition": getattr(args, "long_transition", None),
        "plexos_model": getattr(args, "plexos_model", None),
        "plexos_start_date": getattr(args, "plexos_start_date", None),
        "plexos_sample": getattr(args, "plexos_sample", 1),
        "plexos_period_rhs": getattr(args, "plexos_period_rhs", "prorate"),
        "plexos_data_dirs": tuple(getattr(args, "plexos_data_dir", None) or ()),
        "run_check": args.run_check,
        "sanity_report": getattr(args, "sanity_report", None),
        "reservoir_flow_estimate": args.reservoir_flow_estimate,
        "compare_json": args.compare_json,
        "lax_uc_refs": args.lax_uc_refs,
        "plexos_legacy": args.plexos_legacy,
        "legacy_profile": getattr(args, "legacy_profile", None),
        "legacy_toggles": list(getattr(args, "legacy_toggles", None) or ()),
        "cf_comp_equality": getattr(args, "cf_comp_equality", False),
        "reserve_surplus_equality": getattr(args, "reserve_surplus_equality", False),
        "bess_reserve": getattr(args, "bess_reserve", "native"),
        "reserve_model": getattr(args, "reserve_model", "native"),
        "reserve_load_term": getattr(args, "reserve_load_term", "exogenous"),
        "reserve_inertia_term": getattr(args, "reserve_inertia_term", "commitment"),
        # ``None`` = not given (the default 1.05 applies; no "ignored" warning).
        "reserve_load_factor": getattr(args, "reserve_load_factor", None),
        "reserve_system_load": getattr(args, "reserve_system_load", None),
        "reserve_north_special": getattr(args, "reserve_north_special", False),
        "lng_inflexible_mode": getattr(
            args, "lng_inflexible_mode", DEFAULT_LNG_INFLEXIBLE_MODE
        ),
        "lng_inflexible_value": getattr(
            args, "lng_inflexible_value", DEFAULT_LNG_VALUE_SOURCE
        ),
        "lng_inflexible_cuts": getattr(args, "lng_inflexible_cuts", None),
        "gas_bands_as_tanks": getattr(args, "gas_bands_as_tanks", "off"),
        "gas_refill": getattr(args, "gas_refill", "instant"),
        "gas_usable": getattr(args, "gas_usable", "capacity"),
        "lng_inflexible_diesel_cost": getattr(args, "lng_inflexible_diesel_cost", None),
        "plp_embalses": args.plp_embalses,
        "no_plp_embalses": args.no_plp_embalses,
        # Emission infrastructure (cli_flags.add_emissions_arguments).
        # ``--only-emissions`` (issue #519) implies ``--emissions``
        # and triggers carbon price stamping in the synthesized zone.
        "emissions": getattr(args, "emissions", False),
        "no_emissions": getattr(args, "no_emissions", False),
        # Parse --cogen-must-run: comma-separated names OR the literal
        # "all" / "ALL" (case-insensitive) to force every detected
        # cogen to must_run.  Stored as a (names_set, force_all) tuple
        # the writer can branch on cheaply.
        "cogen_must_run": _parse_cogen_must_run(getattr(args, "cogen_must_run", None)),
        "emissions_file": getattr(args, "emissions_file", None),
        "emissions_report": getattr(args, "emissions_report", None),
        "only_emissions": getattr(args, "only_emissions", False),
        "carbon_price": getattr(args, "carbon_price", None),
        "emission_import_buses": [
            b.strip()
            for b in (getattr(args, "emission_import_buses", None) or "").split(",")
            if b.strip()
        ],
    }

    if args.compare_json:
        # Standalone re-comparison against an existing gtopt JSON; no convert.
        try:
            critical = compare_plexos_bundle(options) or 0
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        sys.exit(1 if critical > 0 else 0)

    if args.show_info:
        try:
            display_plexos_info(options)
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    if args.validate:
        sys.exit(0 if validate_plexos_bundle(options) else 1)

    if getattr(args, "results_only", False):
        if getattr(args, "results", None) is None:
            parser.error("--results-only needs --results")
        if _import_results(args, bundle, options):
            sys.exit(1)
        return

    # Pre-conversion state snapshot + README.  Resolves the output
    # directory the same way ``convert_plexos_bundle`` will (via the
    # shared ``_resolve_output_paths`` helper), creates it if needed,
    # then drops a ``plexos2gtopt_state.json`` + ``README.md`` so the
    # operator has a self-documenting bundle the moment the converter
    # starts working.  Best-effort: snapshot failure must not abort
    # the conversion.  See ``gtopt_shared.state_snapshot`` for the
    # reproducibility contract + file-format.
    try:
        _snapshot_out_dir, _, _ = _resolve_output_paths(
            bundle,
            options.get("output_dir"),
            options.get("output_file"),
            options.get("name"),
        )
        write_state_snapshot(
            output_dir=_snapshot_out_dir,
            tool_name="plexos2gtopt",
            args=args,
            tool_version=__version__,
            extra=options,
        )
        write_plexos2gtopt_readme(_snapshot_out_dir)
    except (OSError, ValueError) as exc:
        print(f"warning: failed to write state snapshot: {exc}", file=sys.stderr)

    # Default action: convert. Returns 0 on success, or the count of
    # CRITICAL findings the converter logged (so CI can gate on it).
    try:
        critical = convert_plexos_bundle(options) or 0
    except NotImplementedError as exc:
        print(f"plexos2gtopt: {exc}", file=sys.stderr)
        sys.exit(2)
    except UnresolvedConstraintReferenceError as exc:
        # A UserConstraint term referenced an element gtopt never emits.
        # Fail loudly with the FULL list — never write a bundle that
        # carries dangling references (mirrors gtopt's strict parser).
        print(
            f"error: unresolved UserConstraint reference(s):\n{exc}",
            file=sys.stderr,
        )
        sys.exit(1)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if critical > 0:
        print(
            f"error: conversion completed with {critical} CRITICAL "
            "finding(s) — fix the underlying issue before using the output.",
            file=sys.stderr,
        )
        sys.exit(1)

    if getattr(args, "results", None) is not None and _import_results(
        args, bundle, options
    ):
        sys.exit(1)


if __name__ == "__main__":
    main()
