# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""General validation checks: uniqueness, SDDP options, boundary cuts, AI."""

import copy
import csv
from typing import Any

from gtopt_shared.json_contracts import array_types, find_schema_problems
from gtopt_shared.future_cost import (
    effective_future_cost,
    resolve_legacy_future_cost_keys,
)

from gtopt_check_json._checks_common import (
    Finding,
    Severity,
    _ELEMENT_ARRAYS,
    _extract_scalar_values,
    _get_name_set,
    _get_uid_set,
)


def check_uid_uniqueness(planning: dict[str, Any]) -> list[Finding]:
    """Check that all UIDs are unique within each element class."""
    findings: list[Finding] = []
    sys = planning.get("system", {})
    sim = planning.get("simulation", {})

    # System elements
    for array_key, label in _ELEMENT_ARRAYS.items():
        uid_map = _get_uid_set(sys, array_key)
        for uid, indices in uid_map.items():
            if len(indices) > 1:
                findings.append(
                    Finding(
                        check_id="uid_uniqueness",
                        severity=Severity.CRITICAL,
                        message=(f"{label}: duplicate uid={uid} at indices {indices}"),
                    )
                )

    # Simulation elements
    for array_key, label in array_types("Simulation").items():
        uid_map = _get_uid_set(sim, array_key)
        for uid, indices in uid_map.items():
            if len(indices) > 1:
                findings.append(
                    Finding(
                        check_id="uid_uniqueness",
                        severity=Severity.CRITICAL,
                        message=(f"{label}: duplicate uid={uid} at indices {indices}"),
                    )
                )

    return findings


def check_name_uniqueness(planning: dict[str, Any]) -> list[Finding]:
    """Check that all names are unique within each element class."""
    findings: list[Finding] = []
    sys = planning.get("system", {})

    for array_key, label in _ELEMENT_ARRAYS.items():
        name_map = _get_name_set(sys, array_key)
        for name, indices in name_map.items():
            if len(indices) > 1:
                findings.append(
                    Finding(
                        check_id="name_uniqueness",
                        severity=Severity.CRITICAL,
                        message=(
                            f"{label}: duplicate name='{name}' at indices {indices}"
                        ),
                    )
                )

    return findings


def check_demand_lmax_nonneg(planning: dict[str, Any]) -> list[Finding]:
    """Check that all Demand lmax values are non-negative."""
    findings: list[Finding] = []
    sys = planning.get("system", {})

    for idx, demand in enumerate(sys.get("demand_array", [])):
        lmax = demand.get("lmax")
        values = _extract_scalar_values(lmax)
        neg_vals = [v for v in values if v < 0]
        if neg_vals:
            name = demand.get("name", f"index {idx}")
            findings.append(
                Finding(
                    check_id="demand_lmax_nonneg",
                    severity=Severity.WARNING,
                    message=(
                        f"Demand '{name}' (uid={demand.get('uid')}): "
                        f"negative lmax value(s): {neg_vals}"
                    ),
                )
            )

    return findings


_MAX_FAIL_SEGMENTS = 16


def _fail_segment_problems(
    segments: Any, *, scalar_costs: bool
) -> tuple[list[str], list[str]]:
    """Return ``(errors, warnings)`` for one stepwise failure curve.

    Mirrors ``check_fail_segment_list`` in ``source/validate_planning.cpp``.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(segments, list):
        return ["fail_segments must be a JSON array"], warnings
    if len(segments) > _MAX_FAIL_SEGMENTS:
        errors.append(f"{len(segments)} segments (max {_MAX_FAIL_SEGMENTS})")
    cum = 0.0
    prev_cost: float | None = None
    for k, seg in enumerate(segments):
        if not isinstance(seg, dict):
            errors.append(f"segment {k} is not an object")
            continue
        last = k + 1 == len(segments)
        frac = seg.get("fraction")
        if frac is not None:
            if not isinstance(frac, (int, float)) or not 0.0 < frac <= 1.0:
                errors.append(f"segment {k}: fraction={frac} must be in (0, 1]")
            else:
                cum += float(frac)
        elif not last:
            errors.append(f"segment {k}: fraction required (only the last is open)")
        elif cum >= 1.0 - 1e-9:
            errors.append(f"open last segment is empty (fractions sum to {cum:g})")
        cost = seg.get("cost")
        if cost is None:
            errors.append(f"segment {k}: cost is required")
        elif isinstance(cost, (int, float)):
            if prev_cost is not None and cost < prev_cost:
                warnings.append(
                    f"costs decrease at segment {k} ({cost:g} < {prev_cost:g}); "
                    "the LP curtails the cheapest segment first"
                )
            prev_cost = float(cost)
        elif scalar_costs:
            errors.append(f"segment {k}: cost must be a scalar number")
    if cum > 1.0 + 1e-9:
        errors.append(f"fractions sum to {cum:g} (> 1)")
    return errors, warnings


def check_demand_fail_segments(planning: dict[str, Any]) -> list[Finding]:
    """Validate stepwise failure curves (``Demand.fail_segments`` and
    ``model_options.demand_fail_segments``)."""
    findings: list[Finding] = []
    targets: list[tuple[str, Any, bool]] = []
    model_opts = planning.get("options", {}).get("model_options", {})
    if model_opts.get("demand_fail_segments") is not None:
        targets.append(
            (
                "model_options.demand_fail_segments",
                model_opts["demand_fail_segments"],
                True,
            )
        )
    for idx, demand in enumerate(planning.get("system", {}).get("demand_array", [])):
        segs = demand.get("fail_segments")
        if segs:
            name = demand.get("name", f"index {idx}")
            targets.append((f"Demand '{name}'", segs, False))
    for owner, segs, scalar in targets:
        errors, warnings = _fail_segment_problems(segs, scalar_costs=scalar)
        for msg in errors:
            findings.append(
                Finding(
                    check_id="demand_fail_segments",
                    severity=Severity.CRITICAL,
                    message=f"{owner}: fail_segments {msg}",
                )
            )
        for msg in warnings:
            findings.append(
                Finding(
                    check_id="demand_fail_segments",
                    severity=Severity.WARNING,
                    message=f"{owner}: fail_segments {msg}",
                )
            )
    return findings


def check_simulation_mode(planning: dict[str, Any]) -> list[Finding]:
    """Check simulation mode configuration for consistency.

    Validates:
    - ``simulation_mode=true`` with ``max_iterations > 0`` is a conflict.
    - ``simulation_mode=true`` with ``save_per_iteration=true`` warns that
      cuts are not saved in simulation mode.
    """
    findings: list[Finding] = []
    opts = planning.get("options", {})
    sddp = opts.get("sddp_options", {})

    sim_mode = sddp.get("simulation_mode", False)
    if not sim_mode:
        return findings

    max_iter = sddp.get("max_iterations")
    if max_iter is not None and max_iter > 0:
        findings.append(
            Finding(
                check_id="simulation_mode",
                severity=Severity.WARNING,
                message=(
                    f"simulation_mode is true but "
                    f"max_iterations={max_iter}; in simulation mode "
                    f"no training iterations are performed"
                ),
            )
        )

    save_per_iter = sddp.get("save_per_iteration")
    if save_per_iter is True:
        findings.append(
            Finding(
                check_id="simulation_mode",
                severity=Severity.WARNING,
                message=(
                    "simulation_mode is true but "
                    "save_per_iteration is true; cuts are not saved "
                    "in simulation mode"
                ),
            )
        )

    return findings


def check_sddp_options(planning: dict[str, Any]) -> list[Finding]:
    """Check general SDDP option consistency.

    Validates:
    - ``min_iterations > max_iterations`` is suspicious (max wins).
    - ``max_iterations=0`` without hot-start cuts is an untrained policy.
    - ``convergence_tol`` outside (0, 1) is an error.
    """
    findings: list[Finding] = []
    opts = planning.get("options", {})
    sddp = opts.get("sddp_options", {})

    # Skip if no sddp_options present
    if not sddp:
        return findings

    max_iter = sddp.get("max_iterations")
    min_iter = sddp.get("min_iterations")

    # min_iterations > max_iterations
    if min_iter is not None and max_iter is not None and min_iter > max_iter:
        findings.append(
            Finding(
                check_id="sddp_options",
                severity=Severity.WARNING,
                message=(
                    f"min_iterations ({min_iter}) > "
                    f"max_iterations ({max_iter}); "
                    f"max_iterations takes precedence"
                ),
            )
        )

    # max_iterations=0 without hot-start cuts
    if max_iter is not None and max_iter == 0:
        hot_start = sddp.get("hot_start", False)
        cut_recovery_mode = sddp.get("cut_recovery_mode", "none")
        cuts_input = sddp.get("cuts_input_file", "")
        has_cuts = (
            hot_start is True
            or (cut_recovery_mode and cut_recovery_mode != "none")
            or bool(cuts_input)
        )
        if not has_cuts:
            findings.append(
                Finding(
                    check_id="sddp_options",
                    severity=Severity.WARNING,
                    message=(
                        "max_iterations=0 without hot-start cuts; "
                        "the policy will be untrained "
                        "(forward-only with no cuts)"
                    ),
                )
            )

    # convergence_tol outside (0, 1)
    conv_tol = sddp.get("convergence_tol")
    if conv_tol is not None and (conv_tol <= 0 or conv_tol >= 1):
        findings.append(
            Finding(
                check_id="sddp_options",
                severity=Severity.CRITICAL,
                message=(f"convergence_tol={conv_tol} must be in (0, 1)"),
            )
        )

    return findings


def check_cascade_solver_type(planning: dict[str, Any]) -> list[Finding]:
    """Check cascade_options and method consistency.

    Validates:
    - ``cascade_options.level_array`` non-empty but ``method`` is not
      ``"cascade"`` --- warn about mismatch.
    - ``method == "cascade"`` but no ``cascade_options.level_array``
      --- info note that defaults will be used.
    """
    findings: list[Finding] = []
    opts = planning.get("options", {})

    method = opts.get("method", "")
    cascade = opts.get("cascade_options", {})
    levels = cascade.get("level_array", [])

    if levels and method and method != "cascade":
        findings.append(
            Finding(
                check_id="cascade_solver_type",
                severity=Severity.WARNING,
                message=(
                    f"cascade_options.level_array has {len(levels)} "
                    f"level(s) but method='{method}' "
                    f"(expected 'cascade')"
                ),
            )
        )

    if method == "cascade" and not levels:
        findings.append(
            Finding(
                check_id="cascade_solver_type",
                severity=Severity.NOTE,
                message=(
                    "method='cascade' but "
                    "cascade_options.level_array is empty; "
                    "a single default level will be used"
                ),
            )
        )

    return findings


def _build_state_variable_names(
    planning: dict[str, Any],
) -> set[str]:
    """Return the set of element names that are SDDP state variables.

    State variables come from:
    - Junctions referenced by reservoirs whose ``use_state_variable``
      is not explicitly ``false``.
    - Batteries (always state variables when present).
    """
    sys = planning.get("system", {})

    # Build junction uid -> name map
    junction_name: dict[Any, str] = {}
    for junc in sys.get("junction_array", []):
        uid = junc.get("uid")
        name = junc.get("name", "")
        if uid is not None and name:
            junction_name[uid] = name

    # Build junction name -> name (for name-based reservoir references)
    junction_by_name: set[str] = set(junction_name.values())

    state_names: set[str] = set()

    for res in sys.get("reservoir_array", []):
        if res.get("use_state_variable") is False:
            continue
        # Reservoir references a junction by uid or name
        junc_ref = res.get("junction")
        if junc_ref is None:
            # Reservoir name is itself the state variable
            name = res.get("name", "")
            if name:
                state_names.add(name)
            continue
        if isinstance(junc_ref, str):
            if junc_ref in junction_by_name:
                state_names.add(junc_ref)
        elif junc_ref in junction_name:
            state_names.add(junction_name[junc_ref])

    for bat in sys.get("battery_array", []):
        name = bat.get("name", "")
        if name:
            state_names.add(name)

    return state_names


def check_schema_keys(planning: dict[str, Any]) -> list[Finding]:
    """Report JSON keys the strict gtopt parser would reject.

    Walks the planning dict against the bundled snapshot of the C++
    ``json_data_contract`` member lists.  Unknown keys (typos, removed
    fields such as ``Battery.gcost``, model options placed directly under
    ``options`` instead of ``options.model_options``) are CRITICAL — gtopt
    refuses the file.  Naming-dialect aliases are accepted by gtopt and
    reported as a NOTE.  Deprecated boundary-cut keys are left to
    :func:`check_future_cost_aliases`.
    """
    findings: list[Finding] = []
    for issue in find_schema_problems(planning):
        if issue.kind == "unknown":
            findings.append(
                Finding(
                    check_id="schema_keys",
                    severity=Severity.CRITICAL,
                    message=issue.message,
                    action="Remove or rename the key (gtopt rejects unknown keys)",
                )
            )
        elif issue.kind == "alias":
            findings.append(
                Finding(
                    check_id="schema_keys",
                    severity=Severity.NOTE,
                    message=issue.message,
                )
            )
    return findings


def check_future_cost_aliases(planning: dict[str, Any]) -> list[Finding]:
    """Flag deprecated boundary-cut keys and conflicting FutureCost values.

    The FutureCost element (``system.future_cost_array``) is the single
    source of the FCF boundary cuts.  Every legacy key
    (``sddp_options.boundary_*`` / ``missing_cut_var_mode``,
    ``monolithic_options.boundary_*``, ``simulation.boundary_cuts_*``) is
    reported as a WARNING (deprecated, or ignored for this method); the same
    field given with different values in the element and an alias — or in
    two aliases — is CRITICAL (gtopt refuses to run).
    """
    work = copy.deepcopy(planning)
    report = resolve_legacy_future_cost_keys(work)
    findings = [
        Finding(
            check_id="future_cost_aliases",
            severity=Severity.WARNING,
            message=msg,
            action=(
                "Move the setting onto system.future_cost_array[0] "
                "(FutureCost: cuts_file, mode, sharing, mean_shift, "
                "iteration_limit, missing_cut_var_mode, valuation)"
            ),
        )
        for msg in report.warnings
    ]
    findings.extend(
        Finding(
            check_id="future_cost_aliases",
            severity=Severity.CRITICAL,
            message=msg,
            action="Configure each boundary-cut field once, on FutureCost",
        )
        for msg in report.errors
    )
    return findings


def check_boundary_cuts(
    planning: dict[str, Any],
    base_dir: str = "",
) -> list[Finding]:
    """Check that boundary-cuts CSV headers match known state variables.

    Reads the FutureCost ``cuts_file`` gtopt will load (the element, or a
    deprecated alias honoured for the planning method), parses its CSV
    header, and warns about column names that do not correspond to any
    known state variable in the model.
    """
    findings: list[Finding] = []
    cuts_file = effective_future_cost(planning).get("cuts_file", "")

    if not cuts_file:
        return findings

    # Resolve path relative to input_directory, then base_dir
    from pathlib import Path  # noqa: PLC0415

    # gtopt resolves a relative cuts_file against options.input_directory.
    # Try: as-is, base_dir/input_directory, base_dir, base_dir's parent.
    cuts_path = Path(cuts_file)
    input_dir = str((planning.get("options") or {}).get("input_directory") or "")
    if not cuts_path.is_absolute() and base_dir:
        base = Path(base_dir)
        for candidate in (
            cuts_path,
            base / input_dir / cuts_path,
            base / cuts_path,
            base.parent / cuts_path,
        ):
            if candidate.exists():
                cuts_path = candidate
                break

    if not cuts_path.exists():
        findings.append(
            Finding(
                check_id="boundary_cuts",
                severity=Severity.WARNING,
                message=f"FutureCost cuts_file not found: {cuts_path}",
                action="Verify boundary cuts file path and plpplem data",
            )
        )
        return findings

    # Read CSV header via ``csv.reader`` so quoted column names (which
    # the PyArrow-backed writer in plp2gtopt now emits by default) are
    # unquoted properly.  A naive ``split(",")`` would leave the
    # quote characters attached, breaking the ``col.lower() in
    # fixed_cols`` membership test below — symptom observed on a fresh
    # CEN-IPLP conversion as a spurious "13 unknown state variable(s)"
    # warning that named the fixed columns themselves
    # (``"iteration"``, ``"scene"``, ``"rhs"``) as unknowns.
    try:
        with open(cuts_path, encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            header_row = next(reader, None)
    except OSError as exc:
        findings.append(
            Finding(
                check_id="boundary_cuts",
                severity=Severity.WARNING,
                message=f"cannot read FutureCost cuts_file: {exc}",
                action="Check file format and permissions",
            )
        )
        return findings

    if not header_row:
        return findings

    columns = [c.strip() for c in header_row]

    # Determine where state-variable columns start.
    # Format: [iteration,]scene,rhs,StateVar1,...
    # The legacy leading ``name`` column was retired in 2026-05 but is
    # still listed as fixed so any legacy file on disk still parses.
    fixed_cols = {"name", "iteration", "scene", "rhs"}
    state_var_start = 0
    for i, col_name in enumerate(columns):
        if col_name.lower() not in fixed_cols:
            state_var_start = i
            break
    else:
        # All columns are fixed -- no state variables
        return findings

    csv_state_names = columns[state_var_start:]

    # Build known state variable names from the model
    known = _build_state_variable_names(planning)

    if not known:
        # No state variables in the model at all -- every column is
        # unknown, but that may just mean the model has none.
        # ``csv_state_names`` is never empty here: the loop above only
        # breaks on a non-fixed column.
        findings.append(
            Finding(
                check_id="boundary_cuts",
                severity=Severity.WARNING,
                action="Verify reservoir names in plpplem match plpcnfce.dat",
                message=(
                    f"FutureCost cuts_file has {len(csv_state_names)} "
                    f"state variable column(s) but the model defines "
                    f"no state variables"
                ),
            )
        )
        return findings

    unknown = [n for n in csv_state_names if n not in known]
    if unknown:
        names_str = ", ".join(unknown)
        findings.append(
            Finding(
                check_id="boundary_cuts",
                severity=Severity.WARNING,
                message=(
                    f"FutureCost cuts_file references {len(unknown)} "
                    f"unknown state variable(s): {names_str}"
                ),
                action="Verify reservoir names in plpplem match plpcnfce.dat",
            )
        )

    return findings


def check_ai_system_analysis(
    planning: dict[str, Any],
    ai_options: Any = None,
) -> list[Finding]:
    """Use AI to analyze the system for potential issues.

    This check attempts to use gtopt_diagram in mermaid format and
    gtopt2pp for pandapower analysis, then sends the combined report to
    an AI provider for review.

    Returns WARNING-level findings for any AI-detected issues.
    """
    findings: list[Finding] = []

    if ai_options is None:
        return findings

    # Try to collect system information for AI analysis
    report_parts: list[str] = []

    # Try mermaid diagram
    try:
        from gtopt_check_json._diagram_helper import (
            get_mermaid_summary,
        )

        mermaid = get_mermaid_summary(planning)
        if mermaid:
            report_parts.append("=== Network Diagram (Mermaid) ===\n" + mermaid)
    except ImportError:
        report_parts.append(
            "NOTE: gtopt_diagram not available for network visualization"
        )

    # Try pandapower validation
    try:
        from gtopt_check_json._pp_helper import (
            get_pandapower_diagnostics,
        )

        pp_report = get_pandapower_diagnostics(planning)
        if pp_report:
            report_parts.append("=== Pandapower Diagnostics ===\n" + pp_report)
    except ImportError:
        report_parts.append("NOTE: pandapower not available for grid diagnostics")

    if not report_parts:
        return findings

    # Send to AI
    try:
        from gtopt_check_lp._ai import (  # noqa: PLC0415
            _AI_DEFAULT_PROVIDER,
            query_ai,
        )

        combined = "\n\n".join(report_parts)
        prompt = (
            "You are an expert in power system planning and optimization.\n"
            "Analyze this gtopt power system case for potential issues:\n\n"
            f"{combined}\n\n"
            "Report any concerns about:\n"
            "1. Network topology issues\n"
            "2. Generator/demand sizing\n"
            "3. Missing or suspicious parameters\n"
            "4. Potential infeasibility risks\n"
            "Be concise and precise."
        )
        ok, response = query_ai(
            prompt,
            provider=getattr(ai_options, "provider", _AI_DEFAULT_PROVIDER),
            model=getattr(ai_options, "model", None) or None,
            prompt_template="{report}",
            api_key=getattr(ai_options, "key", None) or None,
            timeout=getattr(ai_options, "timeout", 60),
        )
        if ok and response:
            findings.append(
                Finding(
                    check_id="ai_system_analysis",
                    severity=Severity.WARNING,
                    message=f"AI analysis:\n{response}",
                )
            )
        elif not ok:
            findings.append(
                Finding(
                    check_id="ai_system_analysis",
                    severity=Severity.NOTE,
                    message=f"AI analysis unavailable: {response}",
                )
            )
    except ImportError:
        findings.append(
            Finding(
                check_id="ai_system_analysis",
                severity=Severity.NOTE,
                message="AI diagnostics require gtopt_check_lp package",
            )
        )

    return findings
