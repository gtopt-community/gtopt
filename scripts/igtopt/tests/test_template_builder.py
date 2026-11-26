# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Unit tests for igtopt.template_builder."""

from __future__ import annotations

import pathlib

import pytest

from igtopt import template_builder as tb


# ---------------------------------------------------------------------------
# _find_repo_root
# ---------------------------------------------------------------------------


class TestFindRepoRoot:
    """Tests for _find_repo_root path-walking logic."""

    def test_find_repo_root_from_scripts_dir(self):
        """Starting from scripts/, finds the repo root containing include/gtopt."""
        scripts_dir = pathlib.Path(__file__).parent.parent.parent
        result = tb._find_repo_root(scripts_dir)
        assert (result / "include" / "gtopt").is_dir()

    def test_find_repo_root_from_igtopt_dir(self):
        """Starting from scripts/igtopt/, finds the repo root."""
        igtopt_dir = pathlib.Path(__file__).parent.parent
        result = tb._find_repo_root(igtopt_dir)
        assert (result / "include" / "gtopt").is_dir()

    def test_find_repo_root_fallback(self, outside_checkout_dir):
        """From a dir outside any checkout, returns that path resolved."""
        result = tb._find_repo_root(outside_checkout_dir)
        assert result == outside_checkout_dir.resolve()

    def test_find_repo_root_from_deeply_nested(self):
        """Starting from scripts/igtopt/tests/, finds the repo root."""
        tests_dir = pathlib.Path(__file__).parent
        result = tb._find_repo_root(tests_dir)
        assert (result / "include" / "gtopt").is_dir()


# ---------------------------------------------------------------------------
# _build_workbook and _list_sheets – fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def repo_root():
    """Find the real repo root (has include/gtopt/)."""
    return tb._find_repo_root(pathlib.Path(__file__).parent)


@pytest.fixture(scope="module")
def contract():
    """The bundled snapshot contract (deterministic, no gtopt binary)."""
    return tb.load_template_contract("snapshot")


@pytest.fixture(scope="module")
def generated_workbook(tmp_path_factory, contract):
    """Generate the template workbook once and share across tests in the module."""
    openpyxl = pytest.importorskip("openpyxl")
    out = tmp_path_factory.mktemp("tmpl") / "test_template.xlsx"
    tb._build_workbook(out, contract)
    return openpyxl.load_workbook(out)


# ---------------------------------------------------------------------------
# _build_workbook
# ---------------------------------------------------------------------------


class TestBuildWorkbook:
    """Tests for _build_workbook() workbook generation."""

    def test_build_workbook_creates_file(self, tmp_path):
        """_build_workbook writes a .xlsx file (default: snapshot contract)."""
        pytest.importorskip("openpyxl")
        output = tmp_path / "template.xlsx"
        tb._build_workbook(output)
        assert output.exists()
        assert output.stat().st_size > 0

    def test_build_workbook_has_introduction_sheet(self, generated_workbook):
        """Generated workbook contains the .introduction sheet."""
        assert ".introduction" in generated_workbook.sheetnames

    def test_build_workbook_has_options_sheet(self, generated_workbook):
        """Generated workbook contains the options sheet."""
        assert "options" in generated_workbook.sheetnames

    def test_build_workbook_has_simulation_sheets(self, generated_workbook):
        """Generated workbook contains at least the core simulation sheets."""
        required = {
            "block_array",
            "stage_array",
            "scenario_array",
            "phase_array",
            "scene_array",
        }
        sheets = set(generated_workbook.sheetnames)
        assert required.issubset(sheets), f"Missing sheets: {required - sheets}"

    def test_build_workbook_has_system_sheets(self, generated_workbook):
        """Generated workbook contains at least the core system sheets."""
        required = {"bus_array", "generator_array", "demand_array"}
        sheets = set(generated_workbook.sheetnames)
        assert required.issubset(sheets), f"Missing sheets: {required - sheets}"

    def test_build_workbook_covers_every_contract_array(
        self, generated_workbook, contract
    ):
        """One sheet per Simulation/System array except the excluded ones."""
        sheets = set(generated_workbook.sheetnames)
        for section in ("Simulation", "System"):
            arrays = set(contract.types[section]["nested"]) - set(
                tb.TEMPLATE_EXCLUDED_ARRAYS
            )
            arrays = {a for a in arrays if a.endswith("_array")}
            assert arrays <= sheets, sorted(arrays - sheets)

    @pytest.mark.parametrize(
        "sheet",
        [
            "gas_node_array",
            "gas_pipe_array",
            "gas_compressor_array",
            "gas_supply_array",
            "gas_demand_array",
            "gas_turbine_array",
            "lng_terminal_array",
            "hydrogen_storage_array",
            "carrier_converter_array",
            "allowance_pool_array",
            "line_commitment_array",
            "inertia_provision_array",
            "decision_variable_array",
        ],
    )
    def test_new_element_sheets_have_every_contract_column(
        self, generated_workbook, contract, sheet
    ):
        header = [c.value for c in generated_workbook[sheet][1] if c.value]
        cxx = contract.types[contract.element_type(sheet)]["fields"]
        assert set(cxx) <= set(header), sorted(set(cxx) - set(header))


# ---------------------------------------------------------------------------
# TemplateContract
# ---------------------------------------------------------------------------


class TestTemplateContract:
    """Columns come from the contract; FIELD_META supplies the help text."""

    def _fake(self) -> tb.TemplateContract:
        return tb.TemplateContract(
            types={
                "System": {
                    "fields": ["name", "bus_array", "user_model_array"],
                    "nested": {"bus_array": "Bus", "user_model_array": "UserModel"},
                },
                "Bus": {
                    "fields": ["uid", "name", "brand_new"],
                    "required": ["uid", "name", "brand_new"],
                    "kinds": {"uid": "number", "name": "string", "brand_new": "bool"},
                    "nested": {},
                },
            },
            source="fake",
        )

    def test_arrays_skip_excluded_and_scalars(self):
        assert self._fake().arrays("System") == ["bus_array"]
        assert not self._fake().arrays("Simulation")

    def test_undocumented_field_gets_generated_column(self):
        c = self._fake()
        rows = {r[0]: r for r in c.sheet_fields("bus_array")}
        assert rows["brand_new"][1] == "boolean (true/false)"
        assert rows["brand_new"][2] is True
        assert "undocumented" in rows["brand_new"][3]
        assert c.undocumented("bus_array") == ["brand_new"]

    def test_stale_field_meta_columns_are_dropped(self):
        c = self._fake()
        cols = [r[0] for r in c.sheet_fields("bus_array")]
        assert "voltage" not in cols  # FIELD_META column the fake rejects
        assert "voltage" in c.stale("bus_array")

    def test_unknown_sheet_falls_back_to_field_meta(self):
        c = self._fake()
        assert c.element_type("no_such_array") is None
        assert not c.stale("no_such_array")
        assert c.sheet_fields("block_array") == list(tb.FIELD_META["block_array"])

    def test_build_array_sheets_warns_on_drift(self, caplog):
        pytest.importorskip("openpyxl")
        import openpyxl  # pylint: disable=import-outside-toplevel

        wb = openpyxl.Workbook()
        with caplog.at_level("WARNING"):
            tb._build_array_sheets(
                wb, tb._WorkbookStyles(), ["bus_array"], self._fake()
            )
        assert "bus_array.brand_new" in caplog.text
        assert "bus_array.voltage" in caplog.text

    def test_snapshot_contract_has_no_undocumented_columns(self, contract):
        for section in ("Simulation", "System"):
            for sheet in contract.arrays(section):
                assert not contract.undocumented(sheet), sheet
                assert not contract.stale(sheet), sheet


# ---------------------------------------------------------------------------
# _list_sheets
# ---------------------------------------------------------------------------


class TestListSheets:
    """Tests for _list_sheets() stdout output."""

    def test_list_sheets_output_to_stdout(self, contract, capsys):
        """_list_sheets prints system sheet names to stdout."""
        tb._list_sheets(contract)
        captured = capsys.readouterr()
        assert "bus_array" in captured.out
        assert "gas_pipe_array" in captured.out
        assert "bundled contract snapshot" in captured.out

    def test_list_sheets_includes_simulation_sheets(self, capsys):
        """_list_sheets (default contract) lists the simulation sheets."""
        tb._list_sheets()
        captured = capsys.readouterr()
        assert "block_array" in captured.out
        assert "stage_array" in captured.out

    def test_list_sheets_flags_undocumented(self, capsys):
        tb._list_sheets(TestTemplateContract()._fake())
        assert "(1 undocumented)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# --make-template CLI integration
# ---------------------------------------------------------------------------


class TestMakeTemplateCli:
    """Integration tests for the --make-template CLI path in igtopt.main()."""

    def test_make_template_cli_generates_file(self, tmp_path):
        """--make-template with -j writes an xlsx file."""
        pytest.importorskip("openpyxl")
        out_path = tmp_path / "t.xlsx"
        from igtopt.igtopt import main as igtopt_main

        igtopt_main(
            ["--make-template", "--schema-source", "snapshot", "-j", str(out_path)]
        )
        assert out_path.exists()
        assert out_path.stat().st_size > 0

    def test_make_template_cli_list_sheets(self, capsys):
        """--make-template --list-sheets prints sheet names without writing a file."""
        from igtopt.igtopt import main as igtopt_main

        igtopt_main(["--make-template", "--list-sheets", "--schema-source", "snapshot"])
        captured = capsys.readouterr()
        assert "bus_array" in captured.out
        assert "block_array" in captured.out

    def test_make_template_cli_header_dir_is_deprecated(self, capsys, caplog):
        """--header-dir is accepted but ignored with a warning."""
        from igtopt.igtopt import main as igtopt_main

        with caplog.at_level("WARNING"):
            igtopt_main(
                [
                    "--list-sheets",
                    "--schema-source",
                    "snapshot",
                    "--header-dir",
                    "/nonexistent",
                ]
            )
        assert "--header-dir is deprecated" in caplog.text
        assert "bus_array" in capsys.readouterr().out

    def test_make_template_cli_schema_file(self, tmp_path, capsys):
        """--schema-file drives the template from a saved schema dump."""
        import json  # pylint: disable=import-outside-toplevel

        from igtopt.igtopt import main as igtopt_main

        schema = {
            "$defs": {
                "Planning": {"properties": {"system": {"$ref": "#/$defs/System"}}},
                "System": {
                    "properties": {
                        "bus_array": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/Bus"},
                        }
                    }
                },
                "Bus": {
                    "properties": {"uid": {"type": "integer"}},
                    "required": ["uid"],
                },
            }
        }
        f = tmp_path / "schema.json"
        f.write_text(json.dumps(schema))
        igtopt_main(["--list-sheets", "--schema-file", str(f)])
        out = capsys.readouterr().out
        assert f"schema file {f}" in out
        assert "bus_array  [1 columns]" in out

    def test_make_template_cli_gtopt_unavailable(self, tmp_path):
        """--schema-source gtopt without a usable binary exits 1."""
        from igtopt.igtopt import main as igtopt_main

        with pytest.raises(SystemExit) as exc_info:
            igtopt_main(
                [
                    "--list-sheets",
                    "--schema-source",
                    "gtopt",
                    "--gtopt-bin",
                    str(tmp_path / "no-gtopt"),
                ]
            )
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# reservoir_seepage_array sheet content
# ---------------------------------------------------------------------------


class TestSddpOptionKeysSync:
    """Verify SDDP_OPTION_KEYS matches the C++ json_data_contract<SddpOptions>."""

    def test_sddp_keys_match_cpp_header(self, repo_root):
        """All fields in json_options.hpp SddpOptions appear in SDDP_OPTION_KEYS."""
        import re

        header = repo_root / "include" / "gtopt" / "json" / "json_sddp_options.hpp"
        if not header.exists():
            pytest.skip("json_sddp_options.hpp not found")

        text = header.read_text()
        # Extract the SddpOptions contract block
        sddp_match = re.search(r"json_data_contract<SddpOptions>.*?>;", text, re.DOTALL)
        assert sddp_match, "Could not find json_data_contract<SddpOptions>"

        block = sddp_match.group()
        # Find all json_*<"field_name", ...> entries
        cpp_fields = set(re.findall(r'json_\w+<"(\w+)"', block))
        assert cpp_fields, "No fields found in SddpOptions contract"

        missing = cpp_fields - tb.SDDP_OPTION_KEYS
        assert not missing, (
            f"C++ SddpOptions fields missing from SDDP_OPTION_KEYS: {missing}"
        )


class TestReservoirSeepageArraySheet:
    """Tests that the generated template has correct reservoir_seepage_array columns."""

    def test_reservoir_seepage_array_sheet_exists(self, generated_workbook):
        """Generated workbook contains a reservoir_seepage_array sheet."""
        assert "reservoir_seepage_array" in generated_workbook.sheetnames

    def test_reservoir_seepage_array_has_segments_column(self, generated_workbook):
        """reservoir_seepage_array sheet header row includes 'segments'."""
        ws = generated_workbook["reservoir_seepage_array"]
        headers = [cell.value for cell in next(ws.iter_rows(max_row=1))]
        assert "segments" in headers

    def test_reservoir_seepage_array_has_slope_column(self, generated_workbook):
        """reservoir_seepage_array sheet header row includes 'slope'."""
        ws = generated_workbook["reservoir_seepage_array"]
        headers = [cell.value for cell in next(ws.iter_rows(max_row=1))]
        assert "slope" in headers

    def test_reservoir_seepage_array_has_constant_column(self, generated_workbook):
        """reservoir_seepage_array sheet header row includes 'constant'."""
        ws = generated_workbook["reservoir_seepage_array"]
        headers = [cell.value for cell in next(ws.iter_rows(max_row=1))]
        assert "constant" in headers

    def test_reservoir_seepage_array_core_columns(self, generated_workbook):
        """reservoir_seepage_array sheet header contains all required columns."""
        ws = generated_workbook["reservoir_seepage_array"]
        headers = {cell.value for cell in next(ws.iter_rows(max_row=1))}
        required = {"uid", "name", "waterway", "reservoir"}
        assert required.issubset(headers), f"Missing columns: {required - headers}"


class TestStageArraySheet:
    """Tests pinning the calendar-month / chronological columns added to Stage."""

    def test_stage_array_has_month_column(self, generated_workbook):
        """stage_array header row exposes the 'month' calendar tag."""
        ws = generated_workbook["stage_array"]
        headers = [cell.value for cell in next(ws.iter_rows(max_row=1))]
        assert "month" in headers

    def test_stage_array_has_chronological_column(self, generated_workbook):
        """stage_array header row exposes the 'chronological' flag."""
        ws = generated_workbook["stage_array"]
        headers = [cell.value for cell in next(ws.iter_rows(max_row=1))]
        assert "chronological" in headers


class TestScenarioArraySheet:
    """Tests pinning the hydrology metadata column added to Scenario."""

    def test_scenario_array_has_hydrology_column(self, generated_workbook):
        """scenario_array header row exposes the 'hydrology' PLP metadata field."""
        ws = generated_workbook["scenario_array"]
        headers = [cell.value for cell in next(ws.iter_rows(max_row=1))]
        assert "hydrology" in headers


class TestDecisionVariableArraySheet:
    """Tests pinning the DecisionVariable element registered in the system."""

    def test_decision_variable_array_sheet_exists(self, generated_workbook):
        """Generated workbook contains a decision_variable_array sheet."""
        assert "decision_variable_array" in generated_workbook.sheetnames

    def test_decision_variable_array_core_columns(self, generated_workbook):
        """decision_variable_array sheet header contains the LP-column fields."""
        ws = generated_workbook["decision_variable_array"]
        headers = {cell.value for cell in next(ws.iter_rows(max_row=1))}
        required = {"uid", "name", "lower_bound", "upper_bound", "cost"}
        assert required.issubset(headers), f"Missing columns: {required - headers}"


# ---------------------------------------------------------------------------
# simulation_mode in SDDP_OPTION_KEYS
# ---------------------------------------------------------------------------


class TestSimulationModeKey:
    """Verify simulation_mode is present in SDDP_OPTION_KEYS."""

    def test_simulation_mode_in_sddp_keys(self):
        """simulation_mode must be in SDDP_OPTION_KEYS."""
        assert "simulation_mode" in tb.SDDP_OPTION_KEYS

    def test_apertures_in_sddp_keys(self):
        """apertures must be in SDDP_OPTION_KEYS."""
        assert "apertures" in tb.SDDP_OPTION_KEYS


# ---------------------------------------------------------------------------
# Cascade option keys
# ---------------------------------------------------------------------------


class TestCascadeOptionKeys:
    """Verify CASCADE_OPTION_KEYS is exported and cascade_options is importable."""

    def test_cascade_option_keys_exists(self):
        """CASCADE_OPTION_KEYS is a frozenset (may be empty for hierarchical config)."""
        assert isinstance(tb.CASCADE_OPTION_KEYS, frozenset)

    def test_cascade_options_sync_with_cpp(self, repo_root):
        """CascadeOptions fields in json_options.hpp are well-formed."""
        import re

        header = repo_root / "include" / "gtopt" / "json" / "json_cascade_options.hpp"
        if not header.exists():
            pytest.skip("json_cascade_options.hpp not found")

        text = header.read_text()

        # Verify CascadeOptions contract exists
        match = re.search(r"json_data_contract<CascadeOptions>.*?>;", text, re.DOTALL)
        assert match, "Could not find json_data_contract<CascadeOptions>"

        block = match.group()
        cpp_fields = set(re.findall(r'json_\w+<"(\w+)"', block))
        expected = {
            "model_options",
            "sddp_options",
            "level_array",
            "write_level_outputs",
        }
        assert expected == cpp_fields, (
            f"CascadeOptions fields mismatch: expected {expected}, got {cpp_fields}"
        )

    def test_cascade_level_fields_sync_with_cpp(self, repo_root):
        """CascadeLevel fields in json_options.hpp match expected structure."""
        import re

        header = repo_root / "include" / "gtopt" / "json" / "json_cascade_options.hpp"
        if not header.exists():
            pytest.skip("json_cascade_options.hpp not found")

        text = header.read_text()

        match = re.search(r"json_data_contract<CascadeLevel>.*?>;", text, re.DOTALL)
        assert match, "Could not find json_data_contract<CascadeLevel>"

        block = match.group()
        cpp_fields = set(re.findall(r'json_\w+<"(\w+)"', block))
        expected = {
            "uid",
            "name",
            "active",
            "model_options",
            "sddp_options",
            "transition",
            # ``system_file`` was added to ``CascadeLevel`` to support
            # the cascade-reduced multi-fidelity ladder: each
            # intermediate level may reference its own pre-reduced
            # system JSON (rather than re-deriving the reduced grid
            # at run time).  Kept here so the C++ ↔ Python field
            # sync test catches future additions.
            "system_file",
            # ``aperture_system_file`` selects the simplified system
            # solved per aperture in the SDDP backward pass (distinct
            # from the forward ``system_file``).  Part of the
            # aperture-system feature; resolution chain is
            # Phase → CascadeLevel → sddp_options → regular system.
            "aperture_system_file",
        }
        assert expected == cpp_fields, (
            f"CascadeLevel fields mismatch: expected {expected}, got {cpp_fields}"
        )

    def test_cascade_transition_fields_sync_with_cpp(self, repo_root):
        """CascadeTransition fields in json_options.hpp match expected structure."""
        import re

        header = repo_root / "include" / "gtopt" / "json" / "json_cascade_options.hpp"
        if not header.exists():
            pytest.skip("json_cascade_options.hpp not found")

        text = header.read_text()

        match = re.search(
            r"json_data_contract<CascadeTransition>.*?>;", text, re.DOTALL
        )
        assert match, "Could not find json_data_contract<CascadeTransition>"

        block = match.group()
        cpp_fields = set(re.findall(r'json_\w+<"(\w+)"', block))
        expected = {
            # The elastic state-target transition feature
            # (``inherit_targets``, ``target_rtol``, ``target_min_atol``,
            # ``target_penalty``) and the ``optimality_dual_threshold``
            # cut-activity filter were removed; the only surviving
            # transition knob is the optimality-cut inheritance flag.
            "inherit_optimality_cuts",
        }
        assert expected == cpp_fields, (
            f"CascadeTransition fields mismatch: expected {expected}, got {cpp_fields}"
        )

    def test_cascade_level_solver_fields_sync_with_cpp(self, repo_root):
        """CascadeLevelMethod fields in json_options.hpp match expected structure."""
        import re

        header = repo_root / "include" / "gtopt" / "json" / "json_cascade_options.hpp"
        if not header.exists():
            pytest.skip("json_cascade_options.hpp not found")

        text = header.read_text()

        match = re.search(
            r"json_data_contract<CascadeLevelMethod>.*?>;", text, re.DOTALL
        )
        assert match, "Could not find json_data_contract<CascadeLevelMethod>"

        block = match.group()
        cpp_fields = set(re.findall(r'json_\w+<"(\w+)"', block))
        expected = {
            "max_iterations",
            "min_iterations",
            "apertures",
            "num_apertures",
            "aperture_selection_mode",
            "convergence_tol",
            # Stationary-convergence knobs (added at the C++ JSON
            # binding pre-2026-05; this test was just stale).
            "stationary_tol",
            "stationary_gap_ceiling",
            "stationary_window",
            # Elastic-filter knobs (also long-standing).
            "elastic_penalty",
            "elastic_mode",
            # Chunked-aperture pass knobs (added to the C++ JSON binding;
            # this expected set was stale and never updated for them).
            "aperture_chunk_mode",
            "aperture_chunk_size",
            "aperture_solve_mode",
            # Screened dual-shared re-solve budget (2026-07 stochastic
            # wave; per-level override of
            # SddpOptions::aperture_screen_count).
            "aperture_screen_count",
            # Cross-iteration first-aperture basis seed (orthogonal opt-in).
            "aperture_seed_basis",
            # Per-level LP solver options for the forward / backward
            # passes (HiGHS barrier / crossover per cascade level).
            "forward_solver_options",
            "backward_solver_options",
        }
        assert expected == cpp_fields, (
            f"CascadeLevelMethod fields mismatch: expected {expected}, got {cpp_fields}"
        )


# ---------------------------------------------------------------------------
# _nest_sub_options — cascade and simulation_mode
# ---------------------------------------------------------------------------


class TestNestSubOptionsCascade:
    """Tests for _nest_sub_options handling of cascade_options and simulation_mode."""

    def test_simulation_mode_nested_into_sddp(self):
        """simulation_mode is placed inside sddp_options by _nest_sub_options."""
        from igtopt.igtopt import _nest_sub_options

        flat = {"method": "sddp", "simulation_mode": True}
        result = _nest_sub_options(flat)
        assert "simulation_mode" not in result
        assert result["sddp_options"]["simulation_mode"] is True

    def test_cascade_options_passthrough(self):
        """cascade_options dict is passed through as-is."""
        from igtopt.igtopt import _nest_sub_options

        cascade = {
            "levels": [
                {
                    "name": "copper_plate",
                    "model_options": {"use_single_bus": True},
                    "sddp_options": {"max_iterations": 20},
                    "transition": {"inherit_optimality_cuts": True},
                }
            ]
        }
        flat = {"method": "sddp", "cascade_options": cascade}
        result = _nest_sub_options(flat)
        assert result["cascade_options"] == cascade
        assert result["cascade_options"]["levels"][0]["name"] == "copper_plate"

    def test_cascade_options_with_sddp_keys(self):
        """cascade_options coexists with flat SDDP keys."""
        from igtopt.igtopt import _nest_sub_options

        cascade = {
            "levels": [
                {
                    "name": "level_0",
                    "model_options": {"use_single_bus": True},
                }
            ]
        }
        flat = {
            "method": "sddp",
            "max_iterations": 100,
            "simulation_mode": False,
            "cascade_options": cascade,
        }
        result = _nest_sub_options(flat)
        assert result["cascade_options"] == cascade
        assert result["sddp_options"]["max_iterations"] == 100
        assert result["sddp_options"]["simulation_mode"] is False

    def test_apertures_nested_into_sddp(self):
        """apertures list is placed inside sddp_options by _nest_sub_options."""
        from igtopt.igtopt import _nest_sub_options

        flat = {"method": "sddp", "apertures": [1, 2, 3]}
        result = _nest_sub_options(flat)
        assert "apertures" not in result
        assert result["sddp_options"]["apertures"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# _nest_sub_options — solver_options
# ---------------------------------------------------------------------------


class TestNestSubOptionsSolver:
    """Tests for _nest_sub_options handling of solver_options."""

    def test_solver_prefix_time_limit(self):
        """solver_time_limit is placed inside solver_options as time_limit."""
        from igtopt.igtopt import _nest_sub_options

        flat = {"solver_time_limit": 300.0}
        result = _nest_sub_options(flat)
        assert "solver_time_limit" not in result
        assert result["solver_options"]["time_limit"] == 300.0

    def test_solver_prefix_algorithm(self):
        """solver_algorithm is placed inside solver_options as algorithm."""
        from igtopt.igtopt import _nest_sub_options

        flat = {"solver_algorithm": 2}
        result = _nest_sub_options(flat)
        assert "solver_algorithm" not in result
        assert result["solver_options"]["algorithm"] == 2

    def test_solver_prefix_multiple_keys(self):
        """Multiple solver_ keys are all nested correctly."""
        from igtopt.igtopt import _nest_sub_options

        flat = {
            "solver_algorithm": 3,
            "solver_threads": 4,
            "solver_presolve": True,
            "solver_time_limit": 600.0,
            "solver_optimal_eps": 1e-8,
            "solver_feasible_eps": 1e-7,
            "solver_barrier_eps": 1e-6,
            "solver_log_level": 1,
            "solver_mip_gap_abs": 1e-6,
        }
        result = _nest_sub_options(flat)
        so = result["solver_options"]
        assert so["algorithm"] == 3
        assert so["threads"] == 4
        assert so["presolve"] is True
        assert so["time_limit"] == 600.0
        assert so["optimal_eps"] == 1e-8
        assert so["feasible_eps"] == 1e-7
        assert so["barrier_eps"] == 1e-6
        assert so["log_level"] == 1
        assert so["mip_gap_abs"] == 1e-6

    def test_solver_options_passthrough(self):
        """Pre-nested solver_options dict is passed through."""
        from igtopt.igtopt import _nest_sub_options

        nested = {"algorithm": 1, "time_limit": 120.0}
        flat = {"solver_options": nested}
        result = _nest_sub_options(flat)
        assert result["solver_options"]["algorithm"] == 1
        assert result["solver_options"]["time_limit"] == 120.0

    def test_solver_options_merged_with_prefix(self):
        """Pre-nested and prefixed solver options are merged."""
        from igtopt.igtopt import _nest_sub_options

        flat = {
            "solver_options": {"algorithm": 1},
            "solver_time_limit": 300.0,
        }
        result = _nest_sub_options(flat)
        assert result["solver_options"]["algorithm"] == 1
        assert result["solver_options"]["time_limit"] == 300.0

    def test_solver_coexists_with_sddp_and_monolithic(self):
        """solver_options coexists with sddp_options and monolithic_options."""
        from igtopt.igtopt import _nest_sub_options

        flat = {
            "solver_time_limit": 300.0,
            "monolithic_solve_mode": "relaxed",
        }
        result = _nest_sub_options(flat)
        assert result["solver_options"]["time_limit"] == 300.0
        assert result["monolithic_options"]["solve_mode"] == "relaxed"

    def test_unknown_solver_prefix_stays_toplevel(self):
        """Keys with solver_ prefix but not in SOLVER_OPTION_KEYS stay top-level."""
        from igtopt.igtopt import _nest_sub_options

        flat = {"method": "sddp", "solver_time_limit": 60.0}
        result = _nest_sub_options(flat)
        # method is a top-level option (not in SOLVER_OPTION_KEYS)
        assert result["method"] == "sddp"
        assert result["solver_options"]["time_limit"] == 60.0
