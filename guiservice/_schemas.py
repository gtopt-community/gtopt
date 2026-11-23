"""Schema dictionaries that drive the GTOpt GUI.

Three exports:

* ``OPTIONS_SCHEMA``      — global solver / I/O options metadata.
* ``ELEMENT_SCHEMAS``     — per-element-type field metadata.
* ``ELEMENT_TO_ARRAY_KEY``— maps GUI element type → JSON ``*_array`` key.
* ``MODEL_OPTION_KEYS`` / ``nest_model_options`` — flat form keys that
  belong under ``options.model_options``.

Pure data — no executable logic.  Imported by :mod:`guiservice.app` and
re-exported for back-compat with existing call sites:

    from guiservice.app import OPTIONS_SCHEMA  # still works.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Schema definitions – mirrors the C++ JSON interfaces in include/gtopt/json/
# ---------------------------------------------------------------------------

# Options schema – mirrors PlanningOptions (json_options.hpp).  Keys listed
# in ``MODEL_OPTION_KEYS`` are ModelOptions fields: the GUI shows them in
# the same flat form, but they are written to ``options.model_options``
# (gtopt rejects them at the top level of ``options``).
OPTIONS_SCHEMA = {
    # ── Input/Output ──────────────────────────────────────────────────────────
    "input_directory": {"type": "string", "label": "Input Directory"},
    "input_format": {
        "type": "select",
        "label": "Input Format",
        "options": ["", "parquet", "csv"],
    },
    "output_directory": {"type": "string", "label": "Output Directory"},
    "output_format": {
        "type": "select",
        "label": "Output Format",
        "options": ["parquet", "csv"],
    },
    "output_compression": {
        "type": "select",
        "label": "Output Compression",
        "options": ["", "gzip", "zstd", "lzo", "uncompressed"],
    },
    "use_uid_fname": {"type": "boolean", "label": "Use UID Filename"},
    # ── Solver method ─────────────────────────────────────────────────────────
    "method": {
        "type": "select",
        "label": "Method",
        "options": ["", "monolithic", "sddp", "cascade"],
    },
    # ── Model parameters ──────────────────────────────────────────────────────
    "annual_discount_rate": {"type": "number", "label": "Annual Discount Rate"},
    "demand_fail_cost": {"type": "number", "label": "Demand Fail Cost [$/MWh]"},
    "reserve_shortage_cost": {
        "type": "number",
        "label": "Reserve Shortage Cost [$/MWh]",
    },
    "hydro_spill_cost": {"type": "number", "label": "Hydro Spill Cost [$/m³]"},
    "hydro_use_value": {"type": "number", "label": "Hydro Use Value [$/m³]"},
    "scale_objective": {"type": "number", "label": "Scale Objective"},
    "scale_theta": {"type": "number", "label": "Scale Theta"},
    "kirchhoff_threshold": {"type": "number", "label": "Kirchhoff Threshold [kV]"},
    "use_line_losses": {"type": "boolean", "label": "Use Line Losses"},
    "use_kirchhoff": {"type": "boolean", "label": "Use Kirchhoff"},
    "use_single_bus": {"type": "boolean", "label": "Use Single Bus"},
    "loss_segments": {"type": "integer", "label": "Loss Segments"},
    # ── Logging ───────────────────────────────────────────────────────────────
    "log_directory": {"type": "string", "label": "Log Directory"},
    "lp_debug": {"type": "boolean", "label": "LP Debug"},
    "lp_compression": {
        "type": "select",
        "label": "LP Compression",
        "options": ["", "zstd", "gzip", "lz4", "bzip2", "xz", "uncompressed"],
    },
    "lp_only": {"type": "boolean", "label": "LP Only (build without solving)"},
    "lp_debug_scene_min": {"type": "integer", "label": "LP Debug Scene Min"},
    "lp_debug_scene_max": {"type": "integer", "label": "LP Debug Scene Max"},
    "lp_debug_phase_min": {"type": "integer", "label": "LP Debug Phase Min"},
    "lp_debug_phase_max": {"type": "integer", "label": "LP Debug Phase Max"},
    # ── Constraint handling ───────────────────────────────────────────────────
    "constraint_mode": {
        "type": "select",
        "label": "Constraint Mode",
        "options": ["", "strict", "normal"],
    },
}

# ModelOptions fields shown in the flat options form (json_model_options.hpp).
MODEL_OPTION_KEYS = frozenset(
    {
        "demand_fail_cost",
        "reserve_shortage_cost",
        "hydro_spill_cost",
        "hydro_use_value",
        "scale_objective",
        "scale_theta",
        "kirchhoff_threshold",
        "use_line_losses",
        "use_kirchhoff",
        "use_single_bus",
        "loss_segments",
    }
)

# Legacy GUI option keys → canonical ModelOptions keys.
LEGACY_OPTION_KEYS = {
    "reserve_fail_cost": "reserve_shortage_cost",
    "hydro_fail_cost": "hydro_spill_cost",
}


def nest_model_options(options: dict) -> dict:
    """Return *options* with flat model-option keys moved under
    ``model_options`` (legacy names renamed); other keys untouched."""
    out = {k: v for k, v in options.items() if k != "model_options"}
    model = dict(options.get("model_options") or {})
    for key in list(out):
        canon = LEGACY_OPTION_KEYS.get(key, key)
        if canon in MODEL_OPTION_KEYS:
            model.setdefault(canon, out.pop(key))
    if model:
        out["model_options"] = model
    return out


ELEMENT_SCHEMAS = {
    "bus": {
        "label": "Bus",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "voltage", "type": "number", "required": False},
            {"name": "reference_theta", "type": "number", "required": False},
            {"name": "use_kirchhoff", "type": "boolean", "required": False},
        ],
    },
    "generator": {
        "label": "Generator",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "bus", "type": "string", "required": True, "ref": "bus"},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "pmin", "type": "number_or_file", "required": False},
            {"name": "pmax", "type": "number_or_file", "required": False},
            {"name": "gcost", "type": "number_or_file", "required": False},
            {"name": "lossfactor", "type": "number_or_file", "required": False},
            {"name": "capacity", "type": "number_or_file", "required": False},
            {"name": "expcap", "type": "number_or_file", "required": False},
            {"name": "expmod", "type": "number_or_file", "required": False},
            {"name": "capmax", "type": "number_or_file", "required": False},
            {"name": "annual_capcost", "type": "number_or_file", "required": False},
            {"name": "annual_derating", "type": "number_or_file", "required": False},
        ],
    },
    "demand": {
        "label": "Demand",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "bus", "type": "string", "required": True, "ref": "bus"},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "lmax", "type": "number_or_file", "required": False},
            {"name": "lossfactor", "type": "number_or_file", "required": False},
            {"name": "fcost", "type": "number_or_file", "required": False},
            {"name": "emin", "type": "number_or_file", "required": False},
            {"name": "ecost", "type": "number_or_file", "required": False},
            {"name": "capacity", "type": "number_or_file", "required": False},
            {"name": "expcap", "type": "number_or_file", "required": False},
            {"name": "expmod", "type": "number_or_file", "required": False},
            {"name": "capmax", "type": "number_or_file", "required": False},
            {"name": "annual_capcost", "type": "number_or_file", "required": False},
            {"name": "annual_derating", "type": "number_or_file", "required": False},
        ],
    },
    "line": {
        "label": "Line",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "bus_a", "type": "string", "required": True, "ref": "bus"},
            {"name": "bus_b", "type": "string", "required": True, "ref": "bus"},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "voltage", "type": "number_or_file", "required": False},
            {"name": "resistance", "type": "number_or_file", "required": False},
            {"name": "reactance", "type": "number_or_file", "required": False},
            {"name": "lossfactor", "type": "number_or_file", "required": False},
            {"name": "tmax_ab", "type": "number_or_file", "required": False},
            {"name": "tmax_ba", "type": "number_or_file", "required": False},
            {"name": "tcost", "type": "number_or_file", "required": False},
            {"name": "capacity", "type": "number_or_file", "required": False},
            {"name": "expcap", "type": "number_or_file", "required": False},
            {"name": "expmod", "type": "number_or_file", "required": False},
            {"name": "capmax", "type": "number_or_file", "required": False},
            {"name": "annual_capcost", "type": "number_or_file", "required": False},
            {"name": "annual_derating", "type": "number_or_file", "required": False},
        ],
    },
    "battery": {
        "label": "Battery",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "input_efficiency", "type": "number_or_file", "required": False},
            {"name": "output_efficiency", "type": "number_or_file", "required": False},
            {"name": "annual_standing_loss", "type": "number_or_file", "required": False},
            {"name": "emin", "type": "number_or_file", "required": False},
            {"name": "emax", "type": "number_or_file", "required": False},
            {"name": "ecost", "type": "number_or_file", "required": False},
            {"name": "eini", "type": "number", "required": False},
            {"name": "efin", "type": "number", "required": False},
            {"name": "capacity", "type": "number_or_file", "required": False},
            {"name": "expcap", "type": "number_or_file", "required": False},
            {"name": "expmod", "type": "number_or_file", "required": False},
            {"name": "capmax", "type": "number_or_file", "required": False},
            {"name": "annual_capcost", "type": "number_or_file", "required": False},
            {"name": "annual_derating", "type": "number_or_file", "required": False},
        ],
    },
    "converter": {
        "label": "Converter",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "battery", "type": "string", "required": True, "ref": "battery"},
            {
                "name": "generator",
                "type": "string",
                "required": True,
                "ref": "generator",
            },
            {"name": "demand", "type": "string", "required": True, "ref": "demand"},
            {"name": "conversion_rate", "type": "number_or_file", "required": False},
            {"name": "capacity", "type": "number_or_file", "required": False},
            {"name": "expcap", "type": "number_or_file", "required": False},
            {"name": "expmod", "type": "number_or_file", "required": False},
            {"name": "capmax", "type": "number_or_file", "required": False},
            {"name": "annual_capcost", "type": "number_or_file", "required": False},
            {"name": "annual_derating", "type": "number_or_file", "required": False},
        ],
    },
    "junction": {
        "label": "Junction",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "drain", "type": "boolean", "required": False},
            # Drain upper bound [m³/s] (default +inf) and cost
            # [$/(m³/s·h)] (default 0); both ignored unless ``drain``.
            {"name": "drain_capacity", "type": "number", "required": False},
            {"name": "drain_cost", "type": "number", "required": False},
        ],
    },
    "waterway": {
        "label": "Waterway",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {
                "name": "junction_a",
                "type": "string",
                "required": True,
                "ref": "junction",
            },
            {
                "name": "junction_b",
                "type": "string",
                "required": True,
                "ref": "junction",
            },
            {"name": "capacity", "type": "number_or_file", "required": False},
            {"name": "lossfactor", "type": "number_or_file", "required": False},
            {"name": "fmin", "type": "number_or_file", "required": False},
            {"name": "fmax", "type": "number_or_file", "required": False},
        ],
    },
    "reservoir": {
        "label": "Reservoir",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "junction", "type": "string", "required": True, "ref": "junction"},
            {"name": "spillway_capacity", "type": "number", "required": False},
            {"name": "spillway_cost", "type": "number", "required": False},
            {"name": "capacity", "type": "number_or_file", "required": False},
            {"name": "annual_standing_loss", "type": "number_or_file", "required": False},
            {"name": "emin", "type": "number_or_file", "required": False},
            {"name": "emax", "type": "number_or_file", "required": False},
            {"name": "ecost", "type": "number_or_file", "required": False},
            {"name": "eini", "type": "number", "required": False},
            {"name": "efin", "type": "number", "required": False},
            {"name": "fmin", "type": "number", "required": False},
            {"name": "fmax", "type": "number", "required": False},
            {"name": "flow_conversion_rate", "type": "number", "required": False},
        ],
    },
    "turbine": {
        "label": "Turbine",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "waterway", "type": "string", "required": False, "ref": "waterway"},
            {
                "name": "generator",
                "type": "string",
                "required": True,
                "ref": "generator",
            },
            {"name": "drain", "type": "boolean", "required": False},
            {"name": "production_factor", "type": "number_or_file", "required": False},
            {"name": "capacity", "type": "number_or_file", "required": False},
        ],
    },
    "flow": {
        "label": "Flow (Inflow)",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "direction", "type": "integer", "required": False},
            {"name": "junction", "type": "string", "required": False, "ref": "junction"},
            {"name": "discharge", "type": "number_or_file", "required": False},
        ],
    },
    "seepage": {
        "label": "ReservoirSeepage",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "waterway", "type": "string", "required": True, "ref": "waterway"},
            {
                "name": "reservoir",
                "type": "string",
                "required": True,
                "ref": "reservoir",
            },
            {"name": "slope", "type": "number", "required": False},
            {"name": "constant", "type": "number", "required": False},
        ],
    },
    "generator_profile": {
        "label": "Generator Profile",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {
                "name": "generator",
                "type": "string",
                "required": True,
                "ref": "generator",
            },
            {"name": "profile", "type": "number_or_file", "required": True},
            {"name": "scost", "type": "number_or_file", "required": False},
        ],
    },
    "demand_profile": {
        "label": "Demand Profile",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "demand", "type": "string", "required": True, "ref": "demand"},
            {"name": "profile", "type": "number_or_file", "required": True},
            {"name": "scost", "type": "number_or_file", "required": False},
        ],
    },
    "reserve_zone": {
        "label": "Reserve Zone",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "urreq", "type": "number_or_file", "required": False},
            {"name": "drreq", "type": "number_or_file", "required": False},
            {"name": "urcost", "type": "number_or_file", "required": False},
            {"name": "drcost", "type": "number_or_file", "required": False},
        ],
    },
    "reserve_provision": {
        "label": "Reserve Provision",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {
                "name": "generator",
                "type": "string",
                "required": True,
                "ref": "generator",
            },
            {"name": "reserve_zones", "type": "string", "required": True},
            {"name": "urmax", "type": "number_or_file", "required": False},
            {"name": "drmax", "type": "number_or_file", "required": False},
            {"name": "ur_capacity_factor", "type": "number_or_file", "required": False},
            {"name": "dr_capacity_factor", "type": "number_or_file", "required": False},
            {
                "name": "ur_provision_factor",
                "type": "number_or_file",
                "required": False,
            },
            {
                "name": "dr_provision_factor",
                "type": "number_or_file",
                "required": False,
            },
            {"name": "urcost", "type": "number_or_file", "required": False},
            {"name": "drcost", "type": "number_or_file", "required": False},
        ],
    },
    "reservoir_discharge_limit": {
        "label": "Reservoir Discharge Limit",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {
                "name": "waterway",
                "type": "string",
                "required": True,
                "ref": "waterway",
            },
            {
                "name": "reservoir",
                "type": "string",
                "required": True,
                "ref": "reservoir",
            },
        ],
    },
    "reservoir_production_factor": {
        "label": "Reservoir Production Factor",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {
                "name": "turbine",
                "type": "string",
                "required": True,
                "ref": "turbine",
            },
            {
                "name": "reservoir",
                "type": "string",
                "required": True,
                "ref": "reservoir",
            },
            {"name": "mean_production_factor", "type": "number", "required": False},
        ],
    },
    "flow_right": {
        "label": "Flow Right",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "purpose", "type": "string", "required": False},
            {
                "name": "junction_a",
                "type": "string",
                "required": False,
                "ref": "junction",
            },
            {
                "name": "junction_b",
                "type": "string",
                "required": False,
                "ref": "junction",
            },
            {"name": "direction", "type": "integer", "required": False},
            {"name": "discharge", "type": "number_or_file", "required": False},
            {"name": "fmax", "type": "number_or_file", "required": False},
            {"name": "consumptive", "type": "boolean", "required": False},
            {"name": "use_average", "type": "boolean", "required": False},
            {"name": "fcost", "type": "number_or_file", "required": False},
            {"name": "uvalue", "type": "number_or_file", "required": False},
            {"name": "priority", "type": "number", "required": False},
        ],
    },
    "volume_right": {
        "label": "Volume Right",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "purpose", "type": "string", "required": False},
            {
                "name": "reservoir",
                "type": "string",
                "required": False,
                "ref": "reservoir",
            },
            {"name": "emin", "type": "number_or_file", "required": False},
            {"name": "emax", "type": "number_or_file", "required": False},
            {"name": "ecost", "type": "number_or_file", "required": False},
            {"name": "eini", "type": "number", "required": False},
            {"name": "efin", "type": "number", "required": False},
            {"name": "demand", "type": "number_or_file", "required": False},
            {"name": "fmax", "type": "number_or_file", "required": False},
            {"name": "fail_cost", "type": "number", "required": False},
            {"name": "priority", "type": "number", "required": False},
            {"name": "flow_conversion_rate", "type": "number", "required": False},
            {"name": "use_state_variable", "type": "boolean", "required": False},
        ],
    },
    "user_param": {
        "label": "User Parameter",
        "fields": [
            {"name": "name", "type": "string", "required": True},
            {"name": "value", "type": "number", "required": False},
        ],
    },
    "user_constraint": {
        "label": "User Constraint",
        "fields": [
            {"name": "uid", "type": "integer", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "active", "type": "boolean", "required": False},
            {"name": "expression", "type": "string", "required": True},
            {"name": "description", "type": "string", "required": False},
            {"name": "constraint_type", "type": "string", "required": False},
        ],
    },
}

ELEMENT_SCHEMAS["future_cost"] = {
    "label": "Future Cost",
    "fields": [
        {"name": "uid", "type": "integer", "required": True},
        {"name": "name", "type": "string", "required": True},
        {"name": "active", "type": "boolean", "required": False},
        {"name": "description", "type": "string", "required": False},
        {"name": "cuts_file", "type": "string", "required": False},
        {
            "name": "mode",
            "type": "string",
            "required": False,
        },  # noload | separated | combined | phi_expectation
        {"name": "sharing", "type": "string", "required": False},  # per_scene | shared | multicut
        {"name": "iteration_limit", "type": "integer", "required": False},
        {"name": "mean_shift", "type": "boolean", "required": False},
        {"name": "single_cut_equality", "type": "boolean", "required": False},
        {"name": "scale_alpha", "type": "number", "required": False},
        {
            "name": "missing_cut_var_mode",
            "type": "string",
            "required": False,
        },  # skip_coeff | skip_cut
        {
            "name": "valuation",
            "type": "string",
            "required": False,
        },  # end_of_horizon | present_value
        {"name": "use_user_alpha", "type": "boolean", "required": False},
        {"name": "user_alpha_uid", "type": "integer", "required": False},
    ],
}

# Map element type → JSON array key in the system section
ELEMENT_TO_ARRAY_KEY = {
    "bus": "bus_array",
    "generator": "generator_array",
    "demand": "demand_array",
    "line": "line_array",
    "battery": "battery_array",
    "converter": "converter_array",
    "junction": "junction_array",
    "waterway": "waterway_array",
    "reservoir": "reservoir_array",
    "turbine": "turbine_array",
    "flow": "flow_array",
    "seepage": "reservoir_seepage_array",
    "reservoir_discharge_limit": "reservoir_discharge_limit_array",
    "reservoir_production_factor": "reservoir_production_factor_array",
    "generator_profile": "generator_profile_array",
    "demand_profile": "demand_profile_array",
    "reserve_zone": "reserve_zone_array",
    "reserve_provision": "reserve_provision_array",
    "flow_right": "flow_right_array",
    "volume_right": "volume_right_array",
    "user_param": "user_param_array",
    "user_constraint": "user_constraint_array",
    "future_cost": "future_cost_array",
}
