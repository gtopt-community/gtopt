# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Canonical options metadata for the gtopt JSON contract.

Pure data — partitions the flat ``options`` keys into the JSON
sub-objects (``sddp_options``, ``model_options``, ``simulation``,
``monolithic_options``, ``solver_options``).

The frozensets below mirror the C++ ``json_data_contract<...>`` field lists
in :file:`include/gtopt/json/*.hpp`.  Keep them in sync.

``_OPTIONS_FIELDS`` is the ordered list ``(key, description, default)``
that drives the rows of the ``options`` worksheet in igtopt's Excel
template (and any other consumer that needs the canonical option
catalog).

Lifted from ``igtopt/_options_meta.py`` to ``gtopt_shared/`` on 2026-06-06
as part of issue #507 Phase 1.  The original ``igtopt._options_meta``
module remains as a thin re-export shim for backward compatibility.
"""

from __future__ import annotations

from typing import Any

# Keys that belong inside the ``sddp_options`` JSON sub-object.
# Must match the fields in ``json_data_contract<SddpOptions>`` in
# ``include/gtopt/json/json_sddp_options.hpp``.
SDDP_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "cut_sharing_mode",
        "markov_states",
        "markov_transition",
        "cut_directory",
        "api_enabled",
        "max_iterations",
        "min_iterations",
        "convergence_tol",
        "elastic_penalty",
        "cut_recovery_mode",
        "recovery_mode",
        "save_per_iteration",
        "cuts_input_file",
        "sentinel_file",
        "elastic_mode",
        "fact_eps",
        "fact_max_cycles",
        "fcut_log",
        "multi_cut_threshold",
        "apertures",
        "num_apertures",
        "aperture_selection_mode",
        "aperture_directory",
        "aperture_system_file",
        "aperture_timeout",
        "save_aperture_lp",
        "aperture_use_manual_clone",
        "release_backends",
        "free_solver_scratch",
        "release_after_alpha",
        "aperture_drop_fcuts",
        "aperture_chunk_mode",
        "aperture_chunk_size",
        "aperture_solve_mode",
        "aperture_screen_count",
        "aperture_seed_basis",
        "basis_cross_mode",
        "boundary_cuts_file",
        "boundary_cuts_mode",
        "boundary_cut_sharing_mode",
        "boundary_cuts_mean_shift",
        "boundary_max_iterations",
        "cut_drain_mode",
        "missing_cut_var_mode",
        "max_cuts_per_phase",
        "cut_prune_interval",
        "prune_dual_threshold",
        "single_cut_storage",
        "max_stored_cuts",
        "simulation_mode",
        "state_variable_lookup_mode",
        "stationary_tol",
        "stationary_window",
        "stationary_gap_ceiling",
        "terminal_failure_threshold",
        "convergence_mode",
        "convergence_confidence",
        "cut_coeff_eps",
        "scale_alpha",
        "alpha_min",
        "update_lp_skip",
        "forward_sampling_mode",
        "integer_cuts_mode",
        "forward_solver_options",
        "forward_max_fallbacks",
        "forward_fail_stop",
        "forward_infeas_rollback",
        "backward_resolve_target",
        "aperture_cut_resolve",
        "backward_solver_options",
        "backward_max_fallbacks",
        "max_async_spread",
        "pool_max_process_swap_mb",
        "pool_swap_growth_mb",
        "low_memory_mode",
        "memory_codec",
        "lp_debug_passes",
    }
)

# Keys that belong inside the ``cascade_options`` JSON sub-object.
# Must match ``json_data_contract<CascadeOptions>``.  In the flat Excel
# sheet they carry a ``cascade_`` prefix; ``level_array`` /
# ``model_options`` / ``sddp_options`` take a JSON value in the cell.
CASCADE_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "model_options",
        "sddp_options",
        "level_array",
        "write_level_outputs",
    }
)

# Keys that belong inside the ``gas_options`` JSON sub-object.
# Must match ``json_data_contract<GasOptions>`` in
# ``include/gtopt/json/json_gas_options.hpp``.  Unique names, routed
# without a prefix.
GAS_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "gas_specific_gravity",
        "gas_temperature",
        "compressibility",
        "slp_iterations",
        "slp_tolerance",
        "slp_damping",
        "slp_min_flow",
        "slp_initial_flow",
        "slp_proximal_cost",
        "linepack_cyclic",
    }
)

# Keys that belong inside the ``sanity_options`` JSON sub-object.
# Must match ``json_data_contract<SanityOptions>``.  In the flat Excel
# sheet they carry a ``sanity_`` prefix (``sanity_enable`` → ``enable``).
SANITY_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "enable",
        "reward_fail_cost_ratio",
        "profile_tolerance",
        "line_x_outlier_factor",
        "line_x_min_class_size",
        "line_x_pu_max",
        "line_xr_max",
        "max_offenders",
    }
)

#: Flat-sheet prefixes of the sub-objects whose keys are routed by prefix
#: (``solver_time_limit`` → ``solver_options.time_limit``).
PREFIXED_OPTION_SECTIONS: dict[str, str] = {
    "solver_": "solver_options",
    "monolithic_": "monolithic_options",
    "sanity_": "sanity_options",
    "cascade_": "cascade_options",
}

# Keys that belong inside the ``monolithic_options`` JSON sub-object.
# Must match the fields in ``json_data_contract<MonolithicOptions>``.
# In the flat Excel sheet these are prefixed with ``monolithic_`` to
# distinguish them from the identically-named SDDP fields.  The
# ``boundary_*`` keys here (and in ``SDDP_OPTION_KEYS``) are DEPRECATED
# aliases of the FutureCost element fields (see
# :mod:`gtopt_shared.future_cost`); they are kept only because the C++
# contract still parses them for the alias translation.
MONOLITHIC_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "solve_mode",
        "boundary_cuts_file",
        "boundary_cuts_mode",
        "boundary_cut_sharing_mode",
        "boundary_max_iterations",
        "solver_options",
        "mip_start",
    }
)

# Keys that belong inside the ``solver_options`` JSON sub-object.
# Must match the fields in ``json_data_contract<SolverOptions>`` in
# ``include/gtopt/json/json_solver_options.hpp``.
# In the flat Excel sheet these are prefixed with ``solver_`` to
# distinguish them from other options (e.g.
# ``solver_time_limit`` → ``time_limit``).
SOLVER_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "algorithm",
        "threads",
        "presolve",
        "optimal_eps",
        "feasible_eps",
        "barrier_eps",
        "mip_gap",
        "log_level",
        "time_limit",
        "scaling",
        "log_mode",
        "max_fallbacks",
        "crossover",
        "mip_gap_abs",
        "memory_emphasis",
        "mip_polish",
        "param_file",
        "advanced_basis",
        "barrier_solver",
        "mip_lp_algorithm",
        "mip_heuristic_effort",
        "kappa_mode",
        "cold_solve_without_basis",
        "deterministic_parallel",
        "accept_optimal_infeas",
    }
)

# Keys that belong inside the ``model_options`` JSON sub-object.
# Must match the fields in ``json_data_contract<ModelOptions>``.
MODEL_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "use_single_bus",
        "use_kirchhoff",
        "use_line_losses",
        "kirchhoff_threshold",
        "loss_segments",
        "scale_objective",
        "scale_theta",
        "demand_fail_cost",
        "demand_fail_segments",
        "reserve_shortage_cost",  # renamed from reserve_fail_cost per §11.10
        "reserve_fail_cost",  # legacy alias (gtopt-legacy dialect)
        "hydro_spill_cost",  # renamed from hydro_fail_cost per §11.10
        "hydro_fail_cost",  # legacy alias (gtopt-legacy dialect)
        "hydro_use_value",
        "continuous_phases",
        "pricing_mode",
        "fast_start_max_hours",
        "transition_integrality",
        "transition_status_rows",  # removed 2026-11-26: parsed, ignored
        "startup_tier_integrality",
        "naming_dialect",
        "kirchhoff_mode",
        "line_losses_mode",
        "dc_line_reactance_threshold",
        "tiny_reactance_mode",
        "tiny_reactance_rel_threshold",
        "tie_line_losses",
        "dc_line_resistance_threshold",
        "scale_loss_link",
        "reserve_coupling",
        "loss_cost_eps",
        "loss_flow_abs_cost",
        "loss_overflow_cost",
        "loss_secant_segments",
        "loss_use_sos2",
        "loss_segment_bound",
        "line_thermal_limit_mode",
        "line_direction_mode",
        "prev_stage_link",
        "use_line_dlr",
        "dlr_limit_mode",
        "overload_slack_bound",
        "max_overload_ratio",
        "theta_max",
        "auto_scale",
        "state_violation_cost",
        "demand_fail_rhs_shift",
        "objective_mode",
        "strict_storage_emin",
        "lp_reduction",
        "allow_oversupply",
        "battery_annual_standing_loss",
        "battery_annual_loss",  # legacy alias (gtopt-legacy dialect)
        "battery_power_envelope",
        "ramp_formula",
        "ramp_wrap",
        "uc_formulation",
        "storage_integration",
        "reservoir_storage_integration",
        "battery_storage_integration",
        "long_transition",
    }
)

# Keys that belong inside the ``lp_matrix_options`` JSON sub-object.
# Must match ``json_data_contract<LpMatrixOptions>`` (the nested
# ``validation`` block is JSON-only and not part of the flat sheet).
LP_MATRIX_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "lp_coeff_ratio_threshold",
        "equilibration_method",
        "fast_sqrt_method",
        "ruiz_max_iterations",
        "ruiz_tolerance",
        "pin_logic_rows",
        "water_dual_target",
        "water_dual_clamp",
        "kvl_row_scale",
        "kvl_row_clamp",
        "kvl_x_ref",
        "compute_stats",
        "label_meta_without_names",
        "share_label_meta",
        "drop_element_collections",
    }
)

# Flat-sheet prefix for FutureCost fields (``future_cost_cuts_file`` →
# ``system.future_cost_array[0].cuts_file``).
FUTURE_COST_OPTION_PREFIX = "future_cost_"

# Keys that belong in the ``simulation`` JSON section rather than ``options``.
# ``annual_discount_rate`` is the canonical simulation-level field; its
# presence under ``options`` is deprecated (the C++ parser emits a warning).
SIMULATION_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "annual_discount_rate",
    }
)

# Keys that LEGITIMATELY appear in more than one sub-object partition.
# SDDP / monolithic both still parse the same (deprecated) boundary-cuts
# alias keys under the same JSON name; the canonical home of those
# settings is the FutureCost element (``system.future_cost_array``).  Consumers that need to
# verify partition disjointness must subtract this set first.
INTENTIONAL_OPTION_KEY_OVERLAPS: frozenset[str] = frozenset(
    {
        "boundary_cuts_file",
        "boundary_cuts_mode",
        "boundary_cut_sharing_mode",
        "boundary_max_iterations",
    }
)

# Python-side keys that are LEGACY ALIASES of canonical C++ fields
# (kept for the ``gtopt-legacy`` dialect) and therefore do NOT appear
# in ``include/gtopt/json/*.hpp``.  The C++ → Python sync gate must
# subtract these before checking subset-of-contract.
#
# Each entry has a canonical C++ counterpart in the SAME partition:
#   reserve_fail_cost → reserve_shortage_cost  (model)
#   hydro_fail_cost   → hydro_spill_cost       (model)
#   battery_annual_loss → battery_annual_standing_loss  (model)
LEGACY_OPTION_KEY_ALIASES: frozenset[str] = frozenset(
    {
        "reserve_fail_cost",
        "hydro_fail_cost",
        "battery_annual_loss",
    }
)


OPTIONS_FIELDS: list[tuple[str, str, Any]] = [
    # ------------------------------------------------------------------
    # Top-level options (stay at root of "options" JSON object)
    # ------------------------------------------------------------------
    ("input_directory", "Directory for input time-series files", "input"),
    (
        "input_format",
        "Preferred input file format: 'parquet' or 'csv'",
        "parquet",
    ),
    # ------------------------------------------------------------------
    # Model options (nested into "model_options" in JSON output)
    # ------------------------------------------------------------------
    ("demand_fail_cost", "[model] Penalty for unserved load [$/MWh]", 1000),
    (
        "reserve_shortage_cost",
        "[model] Penalty for unserved spinning reserve [$/MW]",
        5000,
    ),
    ("use_line_losses", "[model] Enable line loss modelling (true/false)", True),
    (
        "loss_segments",
        "[model] Number of piecewise-linear loss segments (1=linear only)",
        1,
    ),
    (
        "use_kirchhoff",
        "[model] Apply DC Kirchhoff OPF constraints (true/false)",
        True,
    ),
    (
        "use_single_bus",
        "[model] Copper-plate (no network) mode – ignores all line limits (true/false)",
        False,
    ),
    (
        "kirchhoff_threshold",
        "[model] Minimum bus voltage [kV] for Kirchhoff constraints",
        None,
    ),
    (
        "scale_objective",
        "[model] Divide objective coefficients by this value for solver numerics",
        1000,
    ),
    (
        "scale_theta",
        "[model] Angle variable scaling factor (default: 1000)",
        1000,
    ),
    (
        "continuous_phases",
        "[model] Phase range to LP-relax (all binary/integer vars become "
        "continuous).  Syntax: 'all', 'none' (default), '0', '1,3:5,8:'.  "
        "Equivalent to --no-mip when set to 'all'.",
        None,
    ),
    (
        "pricing_mode",
        "[model] MIP only: LP that supplies the nodal prices after the MIP: "
        "'fixed' (default, integers fixed at the incumbent), 'relaxed' "
        "(LP relaxation of the MIP) or 'fast_start' (fixed except fast-start "
        "commitments).  Dispatch is unchanged.",
        "fixed",
    ),
    (
        "fast_start_max_hours",
        "[model] Auto fast-start rule: a commitment without an explicit "
        "fast_start is fast-start when min_up_time and min_down_time are "
        "both <= this many hours (default: 1)",
        1.0,
    ),
    (
        "transition_integrality",
        "[model] Commitment startup/shutdown integrality: 'auto' (default, "
        "binary), 'continuous' or 'binary'.  Commitment.transition_integrality "
        "overrides it per unit.",
        "auto",
    ),
    (
        "startup_tier_integrality",
        "[model] Commitment hot/warm/cold start selector integrality: "
        "'continuous' (default; totally unimodular tier rows) or 'integer'.  "
        "Commitment.startup_tier_integrality overrides it per unit.",
        "continuous",
    ),
    (
        "naming_dialect",
        "[model] Enforce a naming dialect on input + output: 'gtopt', 'plp', "
        "'sddp', 'plexos', 'pypsa', 'pandapower'.  Empty = silent legacy "
        "behaviour.  Drives the input warn pass at JSON parse time and the "
        "output rename pass at planning.json write time.",
        None,
    ),
    # ------------------------------------------------------------------
    # Simulation fields (moved into "simulation" in JSON output)
    # ------------------------------------------------------------------
    (
        "annual_discount_rate",
        "[simulation] Annual discount rate for CAPEX [p.u.] (e.g. 0.10 = 10 %)",
        0.1,
    ),
    ("output_directory", "Directory for solution output files", "output"),
    ("output_format", "Output file format: 'parquet' or 'csv'", "parquet"),
    (
        "output_compression",
        "Parquet compression codec: 'gzip', 'snappy', 'zstd', or ''",
        "gzip",
    ),
    (
        "use_uid_fname",
        "Use uid-based filenames for output (true/false)",
        None,
    ),
    (
        "method",
        "Planning method: 'monolithic' (default), 'sddp', or 'cascade'",
        None,
    ),
    ("log_directory", "Directory for solver log files", "logs"),
    (
        "lp_debug",
        "Save debug LP files to log directory (true/false)",
        None,
    ),
    (
        "lp_error",
        "Write an error LP file on cell infeasibility (true/false)",
        None,
    ),
    (
        "lp_compression",
        "Compression codec for debug LP files (e.g. 'gzip')",
        None,
    ),
    ("lp_only", "Build LP without solving (true/false)", None),
    (
        "lp_coeff_ratio_threshold",
        "[lp_matrix] Warn when LP coefficient ratio exceeds this value",
        None,
    ),
    # ------------------------------------------------------------------
    # Solver options (nested into "solver_options" in JSON output)
    # In the flat Excel sheet these use a "solver_" prefix.
    # The prefix is stripped when writing the JSON sub-object.
    # ------------------------------------------------------------------
    (
        "solver_algorithm",
        "[solver] LP algorithm: 0=default, 1=primal, 2=dual, 3=barrier (default: 3)",
        3,
    ),
    (
        "solver_threads",
        "[solver] Number of parallel LP threads (0 = automatic, default: 0)",
        0,
    ),
    (
        "solver_presolve",
        "[solver] Apply LP presolve optimizations (true/false, default: true)",
        True,
    ),
    (
        "solver_time_limit",
        "[solver] Per-solve time limit in seconds; "
        "passed to LP backend (CLP setMaximumSeconds, HiGHS time_limit). "
        "0 = no limit",
        None,
    ),
    (
        "solver_optimal_eps",
        "[solver] Optimality tolerance (blank = use solver default)",
        None,
    ),
    (
        "solver_feasible_eps",
        "[solver] Feasibility tolerance (blank = use solver default)",
        None,
    ),
    (
        "solver_barrier_eps",
        "[solver] Barrier convergence tolerance (blank = use solver default)",
        None,
    ),
    (
        "solver_mip_gap",
        "[solver] Target relative MIP optimality gap (blank = use solver "
        "default).  Ignored on continuous LPs.  Backend mapping: "
        "CPX_PARAM_EPGAP, HiGHS mip_rel_gap, Gurobi MIPGap, MindOpt "
        "MIP/RelGap, CBC ratioGap.",
        None,
    ),
    (
        "solver_log_level",
        "[solver] Solver output verbosity (0 = none, default: 0)",
        0,
    ),
    # ------------------------------------------------------------------
    # SDDP options (nested into "sddp_options" in JSON output)
    # ------------------------------------------------------------------
    (
        "cut_sharing_mode",
        "[sddp] How Benders cuts are shared: 'none', 'expected', 'accumulate', or 'max'",
        None,
    ),
    (
        "cut_directory",
        "[sddp] Directory for SDDP Benders cut files",
        "cuts",
    ),
    (
        "api_enabled",
        "[sddp] Write SDDP status JSON for monitoring (true/false)",
        None,
    ),
    (
        "update_lp_skip",
        "[sddp] SDDP iterations between LP coefficient updates (e.g. "
        "volume-dependent production factors)",
        None,
    ),
    ("max_iterations", "[sddp] Maximum SDDP outer iterations", None),
    ("min_iterations", "[sddp] Minimum SDDP outer iterations", None),
    (
        "convergence_tol",
        "[sddp] SDDP convergence tolerance (gap between bounds)",
        None,
    ),
    (
        "elastic_penalty",
        "[sddp] Penalty for elastic constraint relaxation",
        None,
    ),
    (
        "cut_recovery_mode",
        "[sddp] Cut persistence mode: 'none' (default), 'keep', 'append', or 'replace'",
        None,
    ),
    (
        "recovery_mode",
        "[sddp] Recovery mode: 'none' (0), 'cuts' (1), or 'full' (2, default)",
        None,
    ),
    (
        "save_per_iteration",
        "[sddp] Save cuts after every iteration (true/false)",
        None,
    ),
    (
        "cuts_input_file",
        "[sddp] Path to pre-computed Benders cuts file",
        None,
    ),
    (
        "sentinel_file",
        "[sddp] Path to sentinel file that stops SDDP early",
        None,
    ),
    (
        "elastic_mode",
        "[sddp] Elastic filter mode: 'chinneck' (default), 'single_cut', "
        "'multi_cut', 'state_repair' (alias 'plp'), or 'farkas_recursive'",
        None,
    ),
    (
        "multi_cut_threshold",
        "[sddp] Threshold for multi-cut aggregation",
        None,
    ),
    (
        "num_apertures",
        "[sddp] Number of backward-pass apertures",
        None,
    ),
    (
        "aperture_directory",
        "[sddp] Directory for aperture definition files",
        None,
    ),
    (
        "aperture_timeout",
        "[sddp] Timeout in seconds for each aperture solve",
        None,
    ),
    (
        "save_aperture_lp",
        "[sddp] Save LP files for infeasible apertures (true/false)",
        None,
    ),
    # ------------------------------------------------------------------
    # FutureCost (FCF boundary cuts) — written to
    # ``system.future_cost_array[0]`` for EVERY method.  In the flat Excel
    # sheet these use a ``future_cost_`` prefix that is stripped when the
    # field is placed on the element.  (The legacy ``boundary_*`` /
    # ``monolithic_boundary_*`` option keys are deprecated aliases.)
    # ------------------------------------------------------------------
    (
        "future_cost_cuts_file",
        "[future_cost] Path to the boundary (future-cost) cuts CSV "
        "(relative to input_directory)",
        None,
    ),
    (
        "future_cost_mode",
        "[future_cost] Boundary cuts load mode: 'noload', 'separated', "
        "'combined', 'phi_expectation'",
        None,
    ),
    (
        "future_cost_iteration_limit",
        "[future_cost] Keep only the cuts of the last N source iterations (0=all)",
        None,
    ),
    (
        "max_cuts_per_phase",
        "[sddp] Max retained cuts per (scene,phase) LP (0=unlimited, no pruning)",
        0,
    ),
    (
        "cut_prune_interval",
        "[sddp] Iterations between cut pruning passes (requires max_cuts_per_phase>0)",
        10,
    ),
    (
        "prune_dual_threshold",
        "[sddp] Dual threshold for inactive cut detection during pruning",
        1e-8,
    ),
    (
        "single_cut_storage",
        "[sddp] Store cuts per-scene only, build combined on demand (saves memory)",
        False,
    ),
    (
        "max_stored_cuts",
        "[sddp] Max stored cuts per scene (0=unlimited; oldest dropped first)",
        0,
    ),
    (
        "simulation_mode",
        "[sddp] Forward-only evaluation of loaded cuts, no training (true/false)",
        None,
    ),
    (
        "stationary_gap_ceiling",
        "[sddp] Refuse a stationary stop while the signed gap >= this "
        "value (default: 0.02 = 2 %; negative gaps pass; 1.0 disables)",
        None,
    ),
    (
        "terminal_failure_threshold",
        "[sddp] Consecutive elastic-filter failures before terminal-skip "
        "marks a scene (default: 2; 0 disables)",
        None,
    ),
    (
        "lp_debug_passes",
        "[sddp] Comma-separated SDDP passes to dump LP files for "
        "(e.g. 'forward,backward,aperture'; default: all when --lp-debug)",
        None,
    ),
    # NOTE: Cascade options now use a hierarchical ``levels`` array
    # structure (with lp_options, solver, and transition sub-objects per
    # level).  This is too complex for the flat Excel template; cascade
    # configuration should be done directly in JSON.
    # ------------------------------------------------------------------
    # Monolithic options (nested into "monolithic_options" in JSON output)
    # In the flat Excel sheet these use a "monolithic_" prefix to avoid
    # name collisions with the SDDP options above.  The prefix is
    # stripped when writing the JSON sub-object.
    # ------------------------------------------------------------------
    (
        "monolithic_solve_mode",
        "[monolithic] Solve mode: 'monolithic' or 'relaxed'",
        None,
    ),
    # ------------------------------------------------------------------
    # Remaining contract options (one row per C++ option field).  Flat
    # routing: model / sddp / lp_matrix / gas keys by name, solver /
    # monolithic / sanity / cascade keys by their ``<section>_`` prefix.
    # Rows marked "(JSON value)" take a JSON object / array in the cell.
    # ------------------------------------------------------------------
    # -- PlanningOptions ---------------------------------------------
    (
        "output_round_decimals",
        "Round written output values to this many decimals (unset = full precision)",
        None,
    ),
    (
        "build_mode",
        'How per-cell `SystemLP` objects are assembled: `"serial"`, `"scene-parallel"`, '
        '`"full-parallel"`, `"direct-parallel"`',
        None,
    ),
    (
        "lp_fingerprint",
        "Write LP fingerprint JSON for formulation audit",
        None,
    ),
    (
        "lp_scales",
        'Write the LP scales next to each LP file: "summary" or "full"',
        None,
    ),
    (
        "lp_debug_scene_min",
        "Minimum scene UID (inclusive) for LP debug file saving",
        None,
    ),
    (
        "lp_debug_scene_max",
        "Maximum scene UID (inclusive) for LP debug file saving",
        None,
    ),
    (
        "lp_debug_phase_min",
        "Minimum phase UID (inclusive) for LP debug file saving",
        None,
    ),
    (
        "lp_debug_phase_max",
        "Maximum phase UID (inclusive) for LP debug file saving",
        None,
    ),
    (
        "variable_scales",
        'Per-variable LP scales, e.g. [{"class_name": "Reservoir", "variable": "energy", "scale":'
        " 1000}] (JSON value)",
        None,
    ),
    (
        "constraint_mode",
        'User-constraint error policy: "strict" (default, error), "normal" (warn and drop the '
        'constraint), "debug" (drop unresolved terms)',
        None,
    ),
    (
        "write_out",
        "Results to write, comma-separated: solution (sol), dual, reduced_cost (rc), all "
        "(default), none; optional ':Class' filters",
        None,
    ),
    # -- CascadeOptions ----------------------------------------------
    (
        "cascade_model_options",
        "[cascade] Global model options — serve as defaults for all levels (JSON value)",
        None,
    ),
    (
        "cascade_sddp_options",
        "[cascade] Global SDDP options — serve as defaults for all levels (JSON value)",
        None,
    ),
    (
        "cascade_level_array",
        "[cascade] Array of cascade level configurations (JSON value)",
        None,
    ),
    (
        "cascade_write_level_outputs",
        "[cascade] When `true`, every active level except the last one also runs its SDDP "
        "simulation pass and writes a complete output set into its own subdirectory",
        None,
    ),
    # -- GasOptions --------------------------------------------------
    (
        "gas_specific_gravity",
        "[gas] Gas specific gravity G (air = 1) (default 0.6)",
        None,
    ),
    ("gas_temperature", "[gas] Flowing temperature T [K] (default 288.15)", None),
    ("compressibility", "[gas] Compressibility factor Z (default 0.9)", None),
    (
        "slp_iterations",
        "[gas] SLP passes after the first monolithic solve (default 0)",
        None,
    ),
    (
        "slp_tolerance",
        "[gas] Stop when the largest relative flow change is below it (default 1e-4)",
        None,
    ),
    ("slp_damping", "[gas] ω in f₀ ← f₀ + ω (f − f₀) (default 0.5)", None),
    (
        "slp_min_flow",
        "[gas] Floor on |f₀| as a fraction of `fmax` (default 1e-3)",
        None,
    ),
    (
        "slp_initial_flow",
        "[gas] First-pass |f₀| as a fraction of `fmax` (no `reference_flow`) (default 0.1)",
        None,
    ),
    ("slp_proximal_cost", "[gas] ρ on |f̄ − f₀| (0 = off) (default 1e-6)", None),
    (
        "linepack_cyclic",
        "[gas] Each stage closes with at least its initial line pack (default true)",
        None,
    ),
    # -- LpMatrixOptions ---------------------------------------------
    (
        "equilibration_method",
        '[lp_matrix] Matrix scaling: `"none"`, `"row_max"`, `"ruiz"`',
        None,
    ),
    ("fast_sqrt_method", "[lp_matrix] Approximate sqrt used by Ruiz scaling", None),
    ("ruiz_max_iterations", "[lp_matrix] Ruiz iteration cap", None),
    ("ruiz_tolerance", "[lp_matrix] Ruiz convergence tolerance", None),
    (
        "water_dual_target",
        "[lp_matrix] Target `|y_LP|` for reservoir energy-balance rows",
        None,
    ),
    (
        "water_dual_clamp",
        "[lp_matrix] Symmetric clamp `c in [1/k, k]` for the water-row factor",
        None,
    ),
    (
        "kvl_row_scale",
        "[lp_matrix] EXPERIMENTAL: post-equilibration factor on node-angle KVL rows",
        None,
    ),
    ("kvl_row_clamp", "[lp_matrix] Clamp `c in [1/k, k]` of the KVL row factor", None),
    (
        "kvl_x_ref",
        "[lp_matrix] Reference `x_tau` of the KVL factor `c = x_ref / x_tau` (setting it also "
        "enables the factor)",
        None,
    ),
    (
        "compute_stats",
        "[lp_matrix] Compute and log coefficient min/max/ratio during `flatten()`",
        None,
    ),
    (
        "pin_logic_rows",
        "[lp_matrix] Keep row factor 1 on pure commitment-logic rows (default false)",
        None,
    ),
    (
        "label_meta_without_names",
        "[lp_matrix] Keep the label metadata when LP names are off (default false)",
        None,
    ),
    (
        "share_label_meta",
        "[lp_matrix] Share the label metadata with the flat-LP snapshot (default true)",
        None,
    ),
    (
        "drop_element_collections",
        "[lp_matrix] Drop element collections after the build under low_memory off (default true)",
        None,
    ),
    # -- ModelOptions ------------------------------------------------
    (
        "kirchhoff_mode",
        '[model] KVL formulation: `"cycle_basis"` (loop flow) or `"node_angle"` (B–θ)',
        None,
    ),
    (
        "line_losses_mode",
        '[model] Line loss formulation: `"none"`, `"linear"`, `"piecewise"`, `"bidirectional"`, '
        '`"adaptive"` (piecewise for fixed lines, bidirectional for expandable ones)',
        None,
    ),
    (
        "dc_line_reactance_threshold",
        "[model] Per-unit floor `|X/V²|` below which a line's KVL flow term is dropped (see "
        "`tiny_reactance_mode`)",
        None,
    ),
    (
        "tiny_reactance_mode",
        '[model] Lines with reactance 0 or below dc_line_reactance_threshold: "tie" '
        "(zero-impedance tie, equal bus angles) or the other modes in planning-options.md",
        None,
    ),
    (
        "dc_line_resistance_threshold",
        "[model] Per-unit resistance floor `|R/V²|` below which a transmission line's quadratic "
        "losses are dropped (the line is treated as lossless)",
        None,
    ),
    (
        "scale_loss_link",
        "[model] Row-scale factor for the line-loss linking constraint (`loss − Σ loss_k · seg_k "
        "= 0`)",
        None,
    ),
    (
        "loss_cost_eps",
        "[model] $/MWh on line-loss columns (degeneracy breaker / negative-LMP arbitrage guard)",
        None,
    ),
    (
        "loss_overflow_cost",
        "[model] $/MWh on the loss carried by the overflow tier past a decoupled "
        "`Line.loss_envelope` (flow between the envelope and `tmax`)",
        None,
    ),
    (
        "loss_secant_segments",
        "[model] Global default for `Line.loss_secant_segments` (issue #504)",
        None,
    ),
    (
        "loss_use_sos2",
        "[model] Global default for `Line.loss_use_sos2` (issue #504)",
        None,
    ),
    (
        "theta_max",
        "[model] Bound for voltage-angle variables: `θ ∈ [−theta_max, +theta_max]`",
        None,
    ),
    (
        "auto_scale",
        "[model] Enable per-element automatic scaling (reservoir energy/flow, LNG terminal "
        "energy, bus theta) that `PlanningLP` computes at construction time",
        None,
    ),
    (
        "demand_fail_segments",
        "[model] Default stepwise curtailment curve `[{fraction, cost}, …]` (scalar costs) for "
        "demands with neither `fcost` nor `fail_segments`",
        None,
    ),
    (
        "hydro_spill_cost",
        "[model] Default penalty cost for unmet hydro rights [$/m³]",
        None,
    ),
    (
        "hydro_use_value",
        "[model] Default benefit $/m³ for exercising hydro rights (falls back to `0.0` when unset)",
        None,
    ),
    (
        "state_violation_cost",
        "[model] Penalty cost for state variable violations in SDDP elastic filter [$/MWh]",
        None,
    ),
    (
        "demand_fail_rhs_shift",
        "[model] Demand-failure substitution with RHS shift",
        None,
    ),
    (
        "objective_mode",
        '[model] "cost" (default) minimises cost; "emissions" zeroes dispatch costs and charges '
        "each emission source at its zone price",
        None,
    ),
    (
        "strict_storage_emin",
        "[model] Enforce the per-stage emin floor as a hard bound on the reservoir's stage-end "
        "volume (default false)",
        None,
    ),
    (
        "lp_reduction",
        "[model] Drop provably-zero LP columns/rows at assembly (e.g. units with pmax = pmin = "
        "0); default false",
        None,
    ),
    (
        "allow_oversupply",
        "[model] Turn the bus balance into generation >= demand, so surplus energy is discarded "
        "for free (default false)",
        None,
    ),
    (
        "battery_annual_standing_loss",
        "[model] Self-discharge [p.u./year] for every Battery whose `annual_standing_loss` is "
        "unset",
        None,
    ),
    (
        "battery_power_envelope",
        "[model] Emit `p_charge/Pc_max + p_discharge/Pd_max ≤ 1` per block on every battery "
        "converter (`Converter/power_envelope`), cutting simultaneous full-power charge + "
        "discharge",
        None,
    ),
    (
        "reserve_coupling",
        "[model] How `validate_planning` treats a ReserveZone requirement coupled to dispatch",
        None,
    ),
    (
        "line_thermal_limit_mode",
        "[model] How the line thermal limit treats losses: `flow` (flow variable only, no extra "
        "row), `sending` (sending-end power `f + a·ℓ ≤ tmax`, `a` = sending-bus loss share)",
        None,
    ),
    (
        "line_direction_mode",
        "[model] Radial-line flow-direction certificate: `off` (default, today's LP), "
        "`report` (log the classification only), `structural` (one-way and dead-end "
        "radial lines lose their impossible direction / are dropped)",
        None,
    ),
    (
        "prev_stage_link",
        "[model] What a user-constraint `<attr>_prev` term reads at the first block of a stage "
        "whose previous stage is in the same phase: `stage` = that stage's last block",
        None,
    ),
    (
        "use_line_dlr",
        "[model] Global switch for the dynamic line rating: `false` ignores every `Line.dlr` "
        "block and the lines keep their static `tmax` (static-vs-DLR comparisons)",
        None,
    ),
    (
        "overload_slack_bound",
        "[model] Upper bound of the line overload slack (`overload_penalty` band): `thermal` = "
        "`min(hard − normal, max_overload_ratio × normal)`",
        None,
    ),
    (
        "max_overload_ratio",
        "[model] Overload headroom as a fraction of the normal threshold (slack ≤ +150 %, flow ≤ "
        "2.5 × normal)",
        None,
    ),
    (
        "tiny_reactance_rel_threshold",
        "[model] Relative zero-impedance tie floor on |X/V²| "
        "(default 0.01; 0 = absolute floor only)",
        None,
    ),
    (
        "tie_line_losses",
        "[model] Keep the loss model on zero-impedance ties (default false)",
        None,
    ),
    (
        "loss_flow_abs_cost",
        "[model] tangent_signed_flow: cost on |f| pinning the flow split (needs loss_cost_eps > 0)",
        None,
    ),
    (
        "loss_segment_bound",
        "[model] Cap PWL loss segments at the remaining flow cap (default true)",
        None,
    ),
    (
        "ramp_formula",
        "[model] Point-stage ramp-rate difference formula",
        None,
    ),
    (
        "ramp_wrap",
        "[model] central2 ramps: treat the stage as a cycle (default false)",
        None,
    ),
    (
        "uc_formulation",
        "[model] UC formulation: `energy_block` or `power_based`",
        None,
    ),
    (
        "storage_integration",
        "[model] Point-stage storage integration (all storages)",
        None,
    ),
    (
        "reservoir_storage_integration",
        "[model] Point-stage storage integration of reservoirs (default lg4_pos)",
        None,
    ),
    (
        "battery_storage_integration",
        "[model] Point-stage storage integration of batteries and other storages (default block)",
        None,
    ),
    (
        "long_transition",
        "[model] Default Commitment.long_transition: `on`, `off` or `no_uc`",
        None,
    ),
    (
        "dlr_limit_mode",
        "[model] Default Line.dlr.limit_mode: `replace` or `min`",
        None,
    ),
    # -- MonolithicOptions -------------------------------------------
    (
        "monolithic_solver_options",
        "[monolithic] Per-method LP solver configuration (JSON value)",
        None,
    ),
    (
        "monolithic_mip_start",
        '[monolithic] MIP-start settings object, e.g. {"enabled": true} (JSON value)',
        None,
    ),
    # -- SanityOptions -----------------------------------------------
    ("sanity_enable", "[sanity] Master switch", None),
    (
        "sanity_reward_fail_cost_ratio",
        "[sanity] Reward / demand-fail-cost ratio above which a reward is flagged",
        None,
    ),
    ("sanity_profile_tolerance", "[sanity] Slack on `[0, 1]`", None),
    (
        "sanity_line_x_outlier_factor",
        "[sanity] Allowed ratio to the kV-class median of `|X|/V²`",
        None,
    ),
    (
        "sanity_line_x_min_class_size",
        "[sanity] Minimum lines in a kV class for the outlier test",
        None,
    ),
    ("sanity_line_x_pu_max", "[sanity] Reactance ceiling [p.u., 100 MVA base]", None),
    ("sanity_line_xr_max", "[sanity] X/R ceiling", None),
    ("sanity_max_offenders", "[sanity] Offenders named per summary line", None),
    # -- SddpOptions -------------------------------------------------
    (
        "forward_sampling_mode",
        '[sddp] Forward-pass sampling: `"persistent"` (each scene-driver on its own path) or '
        '`"resampled"`',
        None,
    ),
    (
        "integer_cuts_mode",
        '[sddp] Backward cuts on integer-bearing cells: "none" (default) or "strengthened" (LP '
        "cut + Lagrangian intercept)",
        None,
    ),
    (
        "cut_drain_mode",
        '[sddp] Which cuts survive an iteration drain: "count", "iteration" (default) or "all"',
        None,
    ),
    ("scale_alpha", "[sddp] Scale of the α / `varphi_s` columns", None),
    ("alpha_min", "[sddp] Universal lower bound for α", None),
    (
        "apertures",
        "[sddp] Aperture UIDs (absent = from Phase, `[]` = pure Benders) (JSON value)",
        None,
    ),
    (
        "aperture_selection_mode",
        "[sddp] Which `num_apertures` entries are taken: `head` (first N, the wettest with "
        "`plp2gtopt` ordering), `stride` (evenly spaced), `tail` (last N)",
        None,
    ),
    (
        "aperture_system_file",
        "[sddp] Simplified system for this level's backward-pass apertures",
        None,
    ),
    (
        "aperture_use_manual_clone",
        "[sddp] Build aperture LP clones by manual copy instead of the solver-native clone "
        "(default false)",
        None,
    ),
    (
        "aperture_drop_fcuts",
        "[sddp] DEPRECATED, ignored (still parsed so old cases load)",
        None,
    ),
    (
        "aperture_chunk_mode",
        '[sddp] Backward aperture grouping: "per_scene" (default; all apertures of a (scene, '
        'phase) on one LP clone), "per_aperture", "chunked"',
        None,
    ),
    (
        "aperture_chunk_size",
        '[sddp] K apertures per task for `aperture_chunk_mode = "chunked"` (unset = auto formula)',
        None,
    ),
    (
        "aperture_solve_mode",
        '[sddp] Per-aperture solve / cut recovery: `"cold"` (barrier + crossover, exact vertex '
        'duals), `"warm"`',
        None,
    ),
    (
        "aperture_screen_count",
        '[sddp] Synthesized cuts re-solved exactly under `aperture_solve_mode = "screened"` '
        "(picked by largest |intercept correction|)",
        None,
    ),
    (
        "aperture_seed_basis",
        "[sddp] Seed each iteration's first backward aperture from the previous iteration's basis"
        " (dual warm start",
        None,
    ),
    (
        "basis_cross_mode",
        "[sddp] Cross-pass basis reuse between forward and backward solves of the same (scene, "
        "phase)",
        None,
    ),
    ("low_memory_mode", "[sddp] One of `off` or `compress`", None),
    (
        "memory_codec",
        "[sddp] Compression codec for `compress` mode (`auto` = zstd level 1, `lz4`, `snappy`, "
        "`zstd`, `gzip`, `none`)",
        None,
    ),
    (
        "cut_coeff_eps",
        "[sddp] Drop cut coefficients below this magnitude (default 0 = keep all; typical "
        "1e-12..1e-8)",
        None,
    ),
    (
        "convergence_mode",
        '[sddp] Criterion mode: `"gap_only"`, `"gap_stationary"`, `"statistical"`',
        None,
    ),
    (
        "state_variable_lookup_mode",
        '[sddp] Volume lookup for LP updates: `"warm_start"` or `"cross_phase"`',
        None,
    ),
    (
        "stationary_tol",
        "[sddp] Tolerance for stationary-gap convergence (0 = disabled)",
        None,
    ),
    ("stationary_window", "[sddp] Look-back window for stationary-gap check", None),
    (
        "convergence_confidence",
        "[sddp] Confidence level for PLP-style statistical convergence (0 = disabled)",
        None,
    ),
    (
        "forward_max_fallbacks",
        "[sddp] Algorithm fallback attempts for non-optimal forward-pass solves (default 2)",
        None,
    ),
    (
        "forward_fail_stop",
        "[sddp] Stop the forward pass of a scene at the first infeasible phase (default true; "
        "false = PLP-style backtracking)",
        None,
    ),
    (
        "forward_infeas_rollback",
        "[sddp] Roll back the cuts of a scene declared infeasible in the forward pass (default "
        "false)",
        None,
    ),
    (
        "backward_resolve_target",
        "[sddp] Re-solve each phase LP after installing its new cut in the backward pass (default"
        " false)",
        None,
    ),
    (
        "aperture_cut_resolve",
        "[sddp] Re-solve the source phase LP after installing each aperture-built cut",
        None,
    ),
    (
        "backward_max_fallbacks",
        "[sddp] Algorithm fallback attempts for non-optimal backward/aperture solves (default 0)",
        None,
    ),
    (
        "max_async_spread",
        "[sddp] Max iteration spread between asynchronous scenes (0 = synchronous, default)",
        None,
    ),
    (
        "pool_max_process_swap_mb",
        "[sddp] Process swap (VmSwap, MB) at/above which the work pool throttles dispatch, only "
        "while VmSwap keeps growing (default 2048; 0 = off)",
        None,
    ),
    (
        "pool_swap_growth_mb",
        "[sddp] `VmSwap` growth (MB per 5 s window) above which the process counts as actively "
        "swapping",
        None,
    ),
    (
        "forward_solver_options",
        '[sddp] Solver options for forward-pass solves, e.g. {"algorithm": "dual"} (JSON value)',
        None,
    ),
    (
        "backward_solver_options",
        "[sddp] Solver options for backward-pass solves (JSON value)",
        None,
    ),
    ("markov_states", "[sddp] Markov-chain state per scenario (JSON value)", None),
    ("markov_transition", "[sddp] Markov transition matrix (JSON value)", None),
    (
        "fact_eps",
        "[sddp] PLP FactEPS tolerance for `state_repair` / `farkas_recursive`",
        None,
    ),
    (
        "fact_max_cycles",
        "[sddp] PLP FactMXC per-(scene, phase) solve-cycle cap for `state_repair` / "
        "`farkas_recursive`",
        None,
    ),
    (
        "fcut_log",
        "[sddp] Write the PLP-style feasibility-cut debug log `gtopt_fcut.log` (`plpfact.log` "
        "analogue) to `log_directory`",
        None,
    ),
    (
        "release_backends",
        "[sddp] Release each solver backend after its solve (default true)",
        None,
    ),
    (
        "free_solver_scratch",
        "[sddp] Free the solver getter scratch after each solve (default true)",
        None,
    ),
    (
        "release_after_alpha",
        "[sddp] Release low-memory backends right after alpha registration (default true)",
        None,
    ),
    # -- SolverOptions -----------------------------------------------
    (
        "solver_mip_gap_abs",
        "[solver] Absolute MIP gap (objective units); MIP only",
        None,
    ),
    (
        "solver_log_mode",
        '[solver] Solver log-file policy: `"nolog"` or `"detailed"` (per-scene/phase/aperture '
        "file)",
        None,
    ),
    (
        "solver_scaling",
        '[solver] Internal solver scaling strategy (nullopt = solver default): `"none"`, '
        '`"automatic"`, `"aggressive"` (or the codes `0` / `1` / `2`)',
        None,
    ),
    (
        "solver_crossover",
        "[solver] Run crossover after a barrier solve (barrier only)",
        None,
    ),
    (
        "solver_max_fallbacks",
        "[solver] On non-optimal exit, cycle through barrier → dual → primal up to this many times",
        None,
    ),
    (
        "solver_memory_emphasis",
        "[solver] Ask the solver to economise memory (true/false)",
        None,
    ),
    (
        "solver_param_file",
        "[solver] Backend-native parameter file (CPLEX `.prm`, …), read before the typed fields "
        "above",
        None,
    ),
    (
        "solver_advanced_basis",
        "[solver] Warm-start simplex from the resident basis (true/false)",
        None,
    ),
    (
        "solver_mip_polish",
        "[solver] MIP only: fix integers at the incumbent and re-solve the LP for duals (default "
        "true)",
        None,
    ),
    (
        "solver_barrier_solver",
        "[solver] HiGHS only: interior-point code used by algorithm=barrier",
        None,
    ),
    (
        "solver_mip_lp_algorithm",
        '[solver] MIP only: algorithm of the root LP relaxation, `"default"`, `"primal"`, '
        '`"dual"` or `"barrier"` (the `barrier_solver` IPM on HiGHS)',
        None,
    ),
    (
        "solver_mip_heuristic_effort",
        "[solver] MIP only, HiGHS only: share of the MIP effort spent in primal heuristics, 0–1",
        None,
    ),
    (
        "solver_kappa_mode",
        '[solver] Basis condition number read after each solve: `"estimate"` (HiGHS Hager '
        'estimate, CPLEX `CPX_KAPPA`), `"exact"`',
        None,
    ),
    (
        "solver_cold_solve_without_basis",
        "[solver] HiGHS: solve cold with the normal settings when advanced_basis is requested but"
        " no basis exists yet (default true)",
        None,
    ),
    (
        "solver_deterministic_parallel",
        "[solver] CPLEX deterministic parallel mode (default unset = opportunistic)",
        None,
    ),
    (
        "solver_accept_optimal_infeas",
        "[solver] CPLEX: accept OPTIMAL_INFEAS as optimal (default true)",
        None,
    ),
]


# Backward-compat alias: ``OPTIONS_FIELDS`` was previously named
# ``_OPTIONS_FIELDS``; the leading underscore implied "private" but
# the data is part of the package contract (re-exported via the
# ``igtopt._options_meta`` shim).  Keep the alias so existing imports
# of ``_OPTIONS_FIELDS`` continue to work.
_OPTIONS_FIELDS = OPTIONS_FIELDS
