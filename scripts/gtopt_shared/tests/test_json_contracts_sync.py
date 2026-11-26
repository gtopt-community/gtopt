# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Drift gate: every Python mirror of gtopt's C++ JSON contract.

The C++ ``json_data_contract<T>`` member lists (``include/gtopt/json``)
are the source of truth.  This module fails when any Python copy drifts
from them:

* the bundled snapshot ``gtopt_shared/data/json_contracts.json``
  (regenerate with ``python -m gtopt_shared.json_contracts --update``);
* igtopt's sheet sets and template sheet list, and ``FIELD_META``
  (per-sheet columns, both directions);
* ``options_meta`` partitions (flat options → ``model_options`` /
  ``sddp_options`` / ... routing, both directions) and the options sheet;
* the FutureCost deprecated-alias table vs ``future_cost_aliases.cpp``;
* gtopt_check_json's element map.

A synthetic workbook carrying every template column is also converted
with igtopt and the emitted JSON is walked against the contract, so a
column or option that gtopt would reject cannot ship.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from gtopt_shared import json_contracts as jc
from gtopt_shared import options_meta
from gtopt_shared.field_meta import FIELD_META

REPO_ROOT = jc.find_repo_root(Path(__file__))

needs_checkout = pytest.mark.skipif(
    REPO_ROOT is None, reason="gtopt C++ headers not available (wheel install)"
)

#: System arrays deliberately absent from the Excel template / FIELD_META.
TEMPLATE_EXCLUDED_ARRAYS = frozenset(
    {
        # Nested variable_array / constraint_array blocks — JSON only.
        "user_model_array",
    }
)

#: FIELD_META entries that describe an ``options`` sub-object, not an array.
FIELD_META_OPTION_ENTRIES = {
    "variable_scales": "VariableScale",
    "solver_options": "SolverOptions",
}


def _types() -> dict[str, Any]:
    return jc.load_snapshot()["types"]


def _all_arrays() -> dict[str, str]:
    return {**jc.array_types("Simulation"), **jc.array_types("System")}


# ── snapshot ───────────────────────────────────────────────────────────────


@needs_checkout
def test_snapshot_is_fresh() -> None:
    """The shipped snapshot equals a fresh parse of the C++ headers."""
    assert REPO_ROOT is not None
    fresh = jc.build_snapshot(REPO_ROOT)
    assert jc.load_snapshot() == json.loads(json.dumps(fresh)), (
        "gtopt_shared/data/json_contracts.json is stale — run "
        "`python -m gtopt_shared.json_contracts --update` from scripts/"
    )


def test_snapshot_covers_the_planning_tree() -> None:
    types = _types()
    assert set(types["Planning"]["nested"]) >= {"options", "simulation", "system"}
    assert jc.array_types("System")["future_cost_array"] == "FutureCost"
    assert "model_options" in types["PlanningOptions"]["nested"]
    for info in types.values():
        assert set(info["nested"]) <= set(info["fields"])
        assert set(info["nested"].values()) <= set(types)


def test_parse_rejects_duplicate_contracts(tmp_path: Path) -> None:
    body = (
        "template<> struct json_data_contract<Foo> "
        '{ using type = json_member_list<json_number<"uid", Uid>>; };\n'
    )
    (tmp_path / "json_a.hpp").write_text(body)
    (tmp_path / "json_b.hpp").write_text(body)
    with pytest.raises(ValueError, match="duplicate"):
        jc.parse_contract_headers(tmp_path)


# ── key walker ─────────────────────────────────────────────────────────────


def test_walker_flags_top_level_model_option_with_hint() -> None:
    issues = jc.find_schema_problems({"options": {"use_single_bus": True}})
    assert [(i.kind, i.path) for i in issues] == [("unknown", "options.use_single_bus")]
    assert "options.model_options" in issues[0].message


def test_walker_recurses_and_classifies() -> None:
    planning = {
        "options": {
            "model_options": {"use_single_bus": True, "bogus": 1},
            "sddp_options": {"boundary_cuts_file": "c.csv"},
        },
        "simulation": {"block_array": [{"uid": 1, "duration": 1, "dur": 2}]},
        "system": {
            "battery_array": [{"uid": 1, "name": "b", "gcost": 1.0}],
            "generator_array": [{"uid": 1, "name": "g", "PotMax": 1.0}],
            "reservoir_array": [
                {
                    "uid": 1,
                    "name": "r",
                    "seepage": [{"uid": 1, "segments": [{"bogus_key": 1}]}],
                }
            ],
            "future_cost_array": [{"uid": 1, "name": "fcf", "cuts_file": "c.csv"}],
        },
    }
    got = {(i.kind, i.path) for i in jc.find_schema_problems(planning)}
    assert got == {
        ("unknown", "options.model_options.bogus"),
        ("deprecated", "options.sddp_options.boundary_cuts_file"),
        ("unknown", "simulation.block_array[0].dur"),
        ("unknown", "system.battery_array[0].gcost"),
        ("alias", "system.generator_array[0].PotMax"),
        ("unknown", "system.reservoir_array[0].seepage[0].segments[0].bogus_key"),
    }


def test_deprecated_keys_are_contract_fields() -> None:
    types = _types()
    for contract, keys in jc.DEPRECATED_KEYS.items():
        missing = set(keys) - set(types[contract]["fields"])
        assert not missing, f"{contract}: {sorted(missing)} no longer parsed"
        fc_fields = set(types["FutureCost"]["fields"])
        for target in keys.values():
            assert target.split(".")[-1] in fc_fields, target


def test_removed_keys_are_contract_fields_and_flagged() -> None:
    types = _types()
    for contract, keys in jc.REMOVED_KEYS.items():
        missing = set(keys) - set(types[contract]["fields"])
        assert not missing, f"{contract}: {sorted(missing)} no longer parsed"
    issues = jc.find_schema_problems(
        {
            "options": {"model_options": {"transition_status_rows": True}},
            "system": {
                "commitment_array": [
                    {"uid": 1, "name": "c", "transition_status_rows": "always"}
                ]
            },
        }
    )
    got = {(i.kind, i.path) for i in issues}
    assert ("deprecated", "options.model_options.transition_status_rows") in got
    assert (
        "deprecated",
        "system.commitment_array[0].transition_status_rows",
    ) in got


@needs_checkout
def test_deprecated_keys_match_cpp_alias_resolver() -> None:
    """Every (section, key) alias resolved in C++ is listed, and vice versa."""
    assert REPO_ROOT is not None
    src = (REPO_ROOT / "source" / "future_cost_aliases.cpp").read_text()
    section = {
        "SddpOptions": "sddp_options",
        "MonolithicOptions": "monolithic_options",
        "Simulation": "simulation",
    }
    listed = {
        f"{section[c]}.{k}" for c, keys in jc.DEPRECATED_KEYS.items() for k in keys
    }
    import re  # pylint: disable=import-outside-toplevel

    in_cpp = set(
        re.findall(r'"((?:sddp_options|monolithic_options|simulation)\.\w+)"', src)
    )
    assert listed == in_cpp


# ── igtopt / FIELD_META ────────────────────────────────────────────────────


def test_igtopt_sheet_sets_match_contract() -> None:
    from igtopt.igtopt import (  # pylint: disable=import-outside-toplevel
        _SIMULATION_SHEETS,
        _SYSTEM_SHEETS,
    )

    assert _SIMULATION_SHEETS == set(jc.array_types("Simulation"))
    assert _SYSTEM_SHEETS == set(jc.array_types("System"))


def test_template_sheets_match_contract() -> None:
    from igtopt.template_builder import (  # pylint: disable=import-outside-toplevel
        _TEMPLATE_SIMULATION_SHEETS,
        _TEMPLATE_SYSTEM_SHEETS,
    )

    assert set(_TEMPLATE_SIMULATION_SHEETS) == set(jc.array_types("Simulation"))
    assert set(_TEMPLATE_SYSTEM_SHEETS) == (
        set(jc.array_types("System")) - TEMPLATE_EXCLUDED_ARRAYS
    )


def test_field_meta_arrays_match_contract() -> None:
    arrays = set(_all_arrays()) - TEMPLATE_EXCLUDED_ARRAYS
    meta_arrays = set(FIELD_META) - set(FIELD_META_OPTION_ENTRIES)
    assert meta_arrays == arrays, (
        f"missing: {sorted(arrays - meta_arrays)}; "
        f"stale: {sorted(meta_arrays - arrays)}"
    )


@pytest.mark.parametrize("sheet", sorted(FIELD_META))
def test_field_meta_columns_match_contract(sheet: str) -> None:
    """Every column is a contract field and every contract field has a column."""
    contract = FIELD_META_OPTION_ENTRIES.get(sheet) or _all_arrays().get(sheet)
    if contract is None:
        pytest.skip(f"{sheet} is not a contract array")
    cxx = set(_types()[contract]["fields"])
    cols = [row[0] for row in FIELD_META[sheet]]
    assert len(cols) == len(set(cols)), f"{sheet}: duplicate columns"
    stale = set(cols) - cxx
    assert not stale, f"{sheet}: columns gtopt rejects: {sorted(stale)}"
    if sheet == "solver_options":
        return  # illustrative subset; routing is checked via SOLVER_OPTION_KEYS
    missing = cxx - set(cols)
    assert not missing, f"{sheet}: contract fields without a column: {sorted(missing)}"


#: FIELD_META help-row types accepted for each contract member kind.
_KIND_TYPES = {
    "number": {"number", "integer", "integer (0/1)"},
    "bool": {"boolean (true/false)"},
    "string": {"string"},
    "custom": {"string"},
    "variant": {
        "number | array | filename",
        "integer | array | filename",
        "integer or string (uid or name)",
        "integer or string",
        "JSON array",
        "JSON object",
    },
    "array": {"JSON array"},
    "class": {"JSON object", "JSON array"},
}


@pytest.mark.parametrize(
    "sheet", sorted(set(FIELD_META) - set(FIELD_META_OPTION_ENTRIES))
)
def test_field_meta_types_match_contract_kinds(sheet: str) -> None:
    """The help-row type of every column matches the C++ member kind."""
    contract = _all_arrays().get(sheet)
    if contract is None:
        pytest.skip(f"{sheet} is not a contract array")
    kinds = _types()[contract]["kinds"]
    bad = [
        f"{field}: {jtype!r} for a {kinds[field]} member"
        for field, jtype, *_rest in FIELD_META[sheet]
        if field in kinds and jtype not in _KIND_TYPES[kinds[field]]
    ]
    assert not bad, bad


@pytest.mark.parametrize(
    "sheet", sorted(set(FIELD_META) - set(FIELD_META_OPTION_ENTRIES))
)
def test_field_meta_marks_contract_required_fields(sheet: str) -> None:
    """A non-nullable C++ member is a required (dark-blue) column."""
    contract = _all_arrays().get(sheet)
    if contract is None:
        pytest.skip(f"{sheet} is not a contract array")
    required = set(_types()[contract]["required"])
    flagged = {row[0] for row in FIELD_META[sheet] if row[2]}
    assert required <= flagged, sorted(required - flagged)


# ── live gtopt --json-schema ───────────────────────────────────────────────


@pytest.fixture(scope="module")
def live_schema() -> dict[str, Any]:
    schema = jc.gtopt_json_schema()
    if schema is None:
        pytest.skip(
            "no gtopt binary with the full `--json-schema` dump "
            "(set GTOPT_BIN to a fresh build)"
        )
    return schema


def test_live_schema_matches_snapshot(live_schema: dict[str, Any]) -> None:
    """The compiled contract (gtopt --json-schema) equals the header snapshot.

    Catches what the header parser cannot see (macro-generated members,
    renamed types) and a stale snapshot; together with the FIELD_META
    tests above it fails whenever a C++ field is missing from igtopt.
    """
    live = jc.types_from_json_schema(live_schema)
    problems = jc.contract_diff(live, _types())
    assert not problems, "\n".join(problems)


def test_live_schema_template_is_fully_documented(
    live_schema: dict[str, Any],
) -> None:
    from igtopt.template_builder import (  # pylint: disable=import-outside-toplevel
        TemplateContract,
    )

    contract = TemplateContract(
        types=jc.types_from_json_schema(live_schema), source="gtopt"
    )
    gaps = {
        sheet: contract.undocumented(sheet) + contract.stale(sheet)
        for section in ("Simulation", "System")
        for sheet in contract.arrays(section)
    }
    assert not {k: v for k, v in gaps.items() if v}


# ── options partitions ─────────────────────────────────────────────────────

_PARTITIONS = (
    ("SDDP_OPTION_KEYS", "SddpOptions", frozenset()),
    ("MONOLITHIC_OPTION_KEYS", "MonolithicOptions", frozenset()),
    ("SOLVER_OPTION_KEYS", "SolverOptions", frozenset()),
    ("MODEL_OPTION_KEYS", "ModelOptions", options_meta.LEGACY_OPTION_KEY_ALIASES),
    # ``validation`` is a nested JSON-only block; ``solver_name`` (the solver
    # pin gtopt records in gtopt_state.json) would clash with the flat
    # sheet's ``solver_`` -> ``solver_options`` prefix, so it is JSON-only.
    (
        "LP_MATRIX_OPTION_KEYS",
        "LpMatrixOptions",
        frozenset({"validation", "solver_name"}),
    ),
    ("GAS_OPTION_KEYS", "GasOptions", frozenset()),
    ("SANITY_OPTION_KEYS", "SanityOptions", frozenset()),
    ("CASCADE_OPTION_KEYS", "CascadeOptions", frozenset()),
)


@pytest.mark.parametrize("name,contract,waived", _PARTITIONS)
def test_option_partitions_equal_contract(
    name: str, contract: str, waived: frozenset[str]
) -> None:
    """A contract field missing from its partition would be emitted at the
    top level of ``options`` (rejected); a stale key would be rejected too."""
    py = set(getattr(options_meta, name))
    cxx = set(_types()[contract]["fields"])
    assert (py - waived) == (cxx - waived), (
        f"{name}: stale {sorted(py - cxx - waived)}, "
        f"missing {sorted(cxx - py - waived)}"
    )


def test_legacy_aliases_are_dialect_aliases() -> None:
    aliases = set(jc.load_snapshot()["dialect_aliases"])
    assert options_meta.LEGACY_OPTION_KEY_ALIASES <= aliases


def _nest_options(flat: dict[str, Any]) -> dict[str, Any]:
    from igtopt.igtopt import (  # pylint: disable=import-outside-toplevel
        _nest_sub_options,
        _pop_future_cost_options,
    )

    future_cost = _pop_future_cost_options(flat)
    simulation: dict[str, Any] = {}
    planning: dict[str, Any] = {
        "options": _nest_sub_options(flat, simulation),
        "simulation": simulation,
    }
    if future_cost:
        planning["system"] = {
            "future_cost_array": [{"uid": 1, "name": "fcf", **future_cost}]
        }
    return planning


def test_options_sheet_rows_are_accepted() -> None:
    """Every row of igtopt's options sheet lands on a canonical key."""
    flat = {
        key: (1 if default is None else default)
        for key, _d, default in options_meta.OPTIONS_FIELDS
    }
    issues = jc.find_schema_problems(_nest_options(flat))
    assert not issues, [i.message for i in issues]


#: ``options`` sub-objects and the flat-sheet prefix of their keys.
_OPTION_SECTIONS = {
    "model_options": "",
    "sddp_options": "",
    "lp_matrix_options": "",
    "gas_options": "",
    "solver_options": "solver_",
    "monolithic_options": "monolithic_",
    "sanity_options": "sanity_",
    "cascade_options": "cascade_",
}

#: Contract option fields with no options-sheet row on purpose.
_OPTIONS_SHEET_WAIVED = {
    # JSON-only nested block / solver pin recorded in gtopt_state.json
    ("LpMatrixOptions", "validation"),
    ("LpMatrixOptions", "solver_name"),
}


def test_options_sheet_has_a_row_for_every_option() -> None:
    """Every (non-deprecated) option gtopt accepts is an options-sheet row."""
    types = _types()
    rows = {key for key, _d, _v in options_meta.OPTIONS_FIELDS}
    rows |= options_meta.LEGACY_OPTION_KEY_ALIASES
    po = types["PlanningOptions"]
    missing = [f for f in po["fields"] if f not in po["nested"] and f not in rows]
    for section, contract in po["nested"].items():
        if section == "variable_scales":
            assert "variable_scales" in rows
            continue
        prefix = _OPTION_SECTIONS[section]
        for f in types[contract]["fields"]:
            if f in jc.DEPRECATED_KEYS.get(contract, {}):
                continue
            if f in jc.REMOVED_KEYS.get(contract, {}):
                continue  # tools must not emit removed keys
            if (contract, f) in _OPTIONS_SHEET_WAIVED:
                continue
            if prefix + f not in rows:
                missing.append(f"{section}.{f} (row {prefix + f})")
    assert not missing, missing


def test_options_sheet_json_rows_route_to_their_section() -> None:
    """JSON-valued option rows (objects / arrays) land on the right key."""
    flat = {
        "cascade_level_array": [{"name": "l0"}],
        "cascade_sddp_options": {"max_iterations": 3},
        "forward_solver_options": {"algorithm": "dual"},
        "monolithic_mip_start": {"enabled": True},
        "sanity_enable": False,
        "slp_iterations": 2,
    }
    planning = _nest_options(dict(flat))
    opts = planning["options"]
    assert opts["cascade_options"] == {
        "level_array": [{"name": "l0"}],
        "sddp_options": {"max_iterations": 3},
    }
    assert opts["sddp_options"]["forward_solver_options"] == {"algorithm": "dual"}
    assert opts["monolithic_options"]["mip_start"] == {"enabled": True}
    assert opts["sanity_options"] == {"enable": False}
    assert opts["gas_options"] == {"slp_iterations": 2}
    assert not jc.find_schema_problems(planning)


@pytest.mark.parametrize("name,contract,waived", _PARTITIONS)
def test_every_partition_key_routes_to_its_contract(
    name: str, contract: str, waived: frozenset[str]
) -> None:
    del contract
    prefix = {
        "SOLVER_OPTION_KEYS": "solver_",
        "SANITY_OPTION_KEYS": "sanity_",
        "CASCADE_OPTION_KEYS": "cascade_",
    }.get(name, "")
    keys = set(getattr(options_meta, name)) - waived
    keys -= set(options_meta.INTENTIONAL_OPTION_KEY_OVERLAPS)
    keys -= {
        "solver_options",
        "mip_start",
        "forward_solver_options",
        "backward_solver_options",
        # nested-object cascade keys hold a JSON value, not a scalar
        "model_options",
        "sddp_options",
    }
    flat = {f"{prefix}{k}": 1 for k in keys}
    issues = [
        i
        for i in jc.find_schema_problems(_nest_options(flat))
        if i.kind != "deprecated"
    ]
    assert not issues, [i.message for i in issues]


# ── igtopt round trip ──────────────────────────────────────────────────────

_SAMPLE = {
    "integer": 1,
    "integer (0/1)": 1,
    "integer | array | filename": 1,
    "integer or string": 1,
    "number": 1.5,
    "string": "x",
    "boolean (true/false)": True,
    "integer or string (uid or name)": 1,
    "number | array | filename": 2.5,
}


def test_igtopt_round_trip_every_column(tmp_path: Path) -> None:
    """A workbook with every FIELD_META column converts to accepted JSON."""
    openpyxl = pytest.importorskip("openpyxl")
    from igtopt.igtopt import _run  # pylint: disable=import-outside-toplevel

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "options"
    ws.append(["option", "value"])
    ws.append(["future_cost_cuts_file", "cuts.csv"])
    ws.append(["future_cost_mode", "separated"])
    ws.append(["use_single_bus", True])
    sheets = (set(FIELD_META) - set(FIELD_META_OPTION_ENTRIES)) & set(_all_arrays())
    for sheet in sorted(sheets):
        removed = jc.REMOVED_KEYS.get(_all_arrays()[sheet], {})
        cols = [r for r in FIELD_META[sheet] if r[1] in _SAMPLE and r[0] not in removed]
        data = wb.create_sheet(sheet)
        data.append([r[0] for r in cols])
        data.append([_SAMPLE[r[1]] for r in cols])
    xlsx = tmp_path / "all.xlsx"
    wb.save(xlsx)

    args = argparse.Namespace(
        filenames=[str(xlsx)],
        json_file=tmp_path / "all.json",
        input_directory=tmp_path / "all",
        input_format="parquet",
        name="all",
        compression="gzip",
        skip_nulls=True,
        parse_unexpected_sheets=False,
        pretty=False,
        zip=False,
    )
    assert _run(args) == 0
    planning = json.loads((tmp_path / "all.json").read_text())
    issues = jc.find_schema_problems(planning)
    assert not issues, [i.message for i in issues]
    assert set(planning["system"]) >= sheets & set(jc.array_types("System"))
    # The future_cost_array sheet row wins over the future_cost_* options
    # (both land on the single FutureCost element).
    fcf = planning["system"]["future_cost_array"]
    assert len(fcf) == 1
    assert fcf[0]["cuts_file"] == _SAMPLE["string"]


# ── gtopt_check_json ───────────────────────────────────────────────────────


def test_check_json_element_map_covers_contract() -> None:
    from gtopt_check_json._checks_common import (  # pylint: disable=import-outside-toplevel
        _ELEMENT_ARRAYS,
    )

    assert set(_ELEMENT_ARRAYS) == set(jc.array_types("System"))


# ── documentation examples ─────────────────────────────────────────────────

#: Historical / design notes are not user-facing references.
_DOC_SKIP_DIRS = frozenset({"analysis", "design", "plans"})


@needs_checkout
def test_doc_json_examples_use_accepted_keys() -> None:
    """Every parseable planning JSON example in the user docs is accepted."""
    import re  # pylint: disable=import-outside-toplevel

    assert REPO_ROOT is not None
    bad: list[str] = []
    for doc in sorted((REPO_ROOT / "docs").rglob("*.md")):
        if _DOC_SKIP_DIRS & set(doc.relative_to(REPO_ROOT / "docs").parts):
            continue
        text = doc.read_text(encoding="utf-8", errors="ignore")
        for block in re.finditer(r"```(?:json|jsonc)\n(.*?)```", text, re.S):
            try:
                example = json.loads(block.group(1))
            except json.JSONDecodeError:
                continue  # illustrative fragment
            if not isinstance(example, dict) or not (
                {"options", "simulation", "system"} & set(example)
            ):
                continue
            line = text[: block.start()].count("\n") + 1
            bad += [
                f"{doc.relative_to(REPO_ROOT)}:{line}: {issue.message}"
                for issue in jc.find_schema_problems(example)
                if issue.kind == "unknown"
            ]
    assert not bad, "\n".join(bad)
