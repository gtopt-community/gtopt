# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Template builder for igtopt – generates the gtopt Excel template.

The sheet list and the columns of every sheet come from the gtopt input
contract: ``gtopt --json-schema`` when a gtopt binary is available, else
the bundled contract snapshot (:mod:`gtopt_shared.json_contracts`, a
committed parse of ``include/gtopt/json``).  Column types, help text and
example values come from :data:`FIELD_META`; a contract field without a
``FIELD_META`` row still gets a column (typed from the schema, flagged as
undocumented) and a warning, so the template never misses a field gtopt
accepts.  ``gtopt_shared/tests/test_json_contracts_sync.py`` fails when
``FIELD_META`` drifts from the contract.

The bulk of the static data (per-array field metadata, options metadata,
introduction-sheet content) lives in sibling modules:

* :mod:`igtopt._field_meta`    — ``FIELD_META`` dict keyed by array name.
* :mod:`igtopt._options_meta`  — ``*_OPTION_KEYS`` frozensets and ``_OPTIONS_FIELDS``.
* :mod:`igtopt._intro_sheet`   — ``_INTRO_LINES``, ``_EXAMPLES`` and the two
  static-content sheet builders.

Public names are re-exported from this module for back-compat with
:mod:`igtopt.igtopt` and :mod:`igtopt.tests.test_template_builder`.
"""

from __future__ import annotations

import logging
import pathlib
import sys
from dataclasses import dataclass
from typing import Any

from gtopt_shared.json_contracts import load_contract_types

from igtopt._field_meta import FIELD_META
from igtopt._intro_sheet import (
    _EXAMPLES,
    _build_boundary_cuts_sheet,
    _build_introduction_sheet,
)
from igtopt._options_meta import (
    _OPTIONS_FIELDS,
    CASCADE_OPTION_KEYS,
    MODEL_OPTION_KEYS,
    MONOLITHIC_OPTION_KEYS,
    SDDP_OPTION_KEYS,
    SIMULATION_OPTION_KEYS,
    SOLVER_OPTION_KEYS,
)

__all__ = [
    # Re-exported data
    "FIELD_META",
    "SDDP_OPTION_KEYS",
    "CASCADE_OPTION_KEYS",
    "MONOLITHIC_OPTION_KEYS",
    "SOLVER_OPTION_KEYS",
    "MODEL_OPTION_KEYS",
    "SIMULATION_OPTION_KEYS",
    # Public API
    "TEMPLATE_EXCLUDED_ARRAYS",
    "TemplateContract",
    "load_template_contract",
]

#: System arrays deliberately absent from the Excel template: nested
#: ``variable_array`` / ``constraint_array`` blocks that do not flatten
#: into one sheet — author them in JSON.
TEMPLATE_EXCLUDED_ARRAYS = frozenset({"user_model_array"})

#: FIELD_META entries that describe an ``options`` sub-object, not a sheet
#: of the contract (rendered by their own handlers).
_OPTION_META_ENTRIES = frozenset({"variable_scales", "solver_options"})

#: Help-row type of a contract field that has no FIELD_META row, by kind.
_KIND_TYPE = {
    "number": "number",
    "bool": "boolean (true/false)",
    "string": "string",
    "variant": "number | array | filename",
    "array": "JSON array",
    "class": "JSON object",
}

FieldRow = tuple[str, str, bool, str, Any]


@dataclass(frozen=True)
class TemplateContract:
    """The contract types the template is generated from, plus provenance."""

    types: dict[str, dict[str, Any]]
    source: str

    def arrays(self, section: str) -> list[str]:
        """``*_array`` keys of ``Simulation`` / ``System`` in contract order."""
        info = self.types.get(section, {})
        nested = info.get("nested", {})
        return [
            key
            for key in info.get("fields", [])
            if key.endswith("_array")
            and key in nested
            and key not in TEMPLATE_EXCLUDED_ARRAYS
        ]

    def element_type(self, sheet: str) -> str | None:
        for section in ("Simulation", "System"):
            target = self.types.get(section, {}).get("nested", {}).get(sheet)
            if target is not None:
                return str(target)
        return None

    def sheet_fields(self, sheet: str) -> list[FieldRow]:
        """Template columns of ``sheet``.

        FIELD_META rows (curated order, types, help) for every contract
        field, then a generated row for each contract field FIELD_META
        lacks.  FIELD_META columns gtopt would reject are dropped.
        """
        meta = FIELD_META.get(sheet, [])
        contract = self.element_type(sheet)
        if contract is None or contract not in self.types:
            return list(meta)
        info = self.types[contract]
        fields = list(info["fields"])
        known = set(fields)
        rows: list[FieldRow] = [row for row in meta if row[0] in known]
        have = {row[0] for row in rows}
        required = set(info.get("required", []))
        for field in fields:
            if field in have:
                continue
            kind = info.get("kinds", {}).get(field, "string")
            rows.append(
                (
                    field,
                    _KIND_TYPE.get(kind, "string"),
                    field in required,
                    "(undocumented in igtopt; see gtopt --json-schema)",
                    None,
                )
            )
        return rows

    def undocumented(self, sheet: str) -> list[str]:
        """Contract fields of ``sheet`` without a FIELD_META row."""
        have = {row[0] for row in FIELD_META.get(sheet, [])}
        return [row[0] for row in self.sheet_fields(sheet) if row[0] not in have]

    def stale(self, sheet: str) -> list[str]:
        """FIELD_META columns of ``sheet`` that are not contract fields."""
        contract = self.element_type(sheet)
        if contract is None or contract not in self.types:
            return []
        known = set(self.types[contract]["fields"])
        return [row[0] for row in FIELD_META.get(sheet, []) if row[0] not in known]


def load_template_contract(
    source: str = "snapshot",
    *,
    gtopt_bin: str | None = None,
    schema_file: pathlib.Path | None = None,
) -> TemplateContract:
    """Load the contract (see :func:`gtopt_shared.json_contracts.load_contract_types`)."""
    types, label = load_contract_types(
        source, gtopt_bin=gtopt_bin, schema_file=schema_file
    )
    return TemplateContract(types=types, source=label)


def _snapshot_arrays(section: str) -> list[str]:
    return load_template_contract("snapshot").arrays(section)


# ------------------------------------------------------------------
# Ordered sheet lists (determine workbook tab order) — contract order of
# the bundled snapshot; enforced by
# ``gtopt_shared/tests/test_json_contracts_sync.py``.
# ------------------------------------------------------------------
_TEMPLATE_SIMULATION_SHEETS = _snapshot_arrays("Simulation")
_TEMPLATE_SYSTEM_SHEETS = _snapshot_arrays("System")


def _find_repo_root(start: pathlib.Path) -> pathlib.Path:
    """Walk up the directory tree to find the repository root."""
    resolved = start.resolve()
    for parent in [resolved, *resolved.parents]:
        if (parent / "include" / "gtopt").is_dir():
            return parent
    return resolved


class _WorkbookStyles:
    """Container for all Excel template styling objects."""

    def __init__(self) -> None:
        from openpyxl.styles import Font, PatternFill

        self.title_font = Font(name="Calibri", size=16, bold=True, color="1F3864")
        self.heading_font = Font(name="Calibri", size=12, bold=True, color="2F5496")
        self.body_font = Font(name="Calibri", size=10)
        self.footer_font = Font(name="Calibri", size=9, italic=True, color="808080")
        self.header_fill = PatternFill("solid", fgColor="1F3864")
        self.alt_row_fill = PatternFill("solid", fgColor="DCE6F1")
        self.help_fill = PatternFill("solid", fgColor="EBF3FB")
        self.required_fill = PatternFill("solid", fgColor="FFF2CC")
        self.header_font_white = Font(name="Calibri", bold=True, color="FFFFFF")
        self.table_header_fill = PatternFill("solid", fgColor="4472C4")
        self.table_header_font = Font(name="Calibri", bold=True, color="FFFFFF")
        self.optional_header_fill = PatternFill("solid", fgColor="2F5496")


def _build_options_sheet(wb: Any, styles: _WorkbookStyles) -> None:
    """Create and populate the options worksheet."""
    from openpyxl.styles import Alignment, Font

    ws_opts = wb.create_sheet("options")
    ws_opts.column_dimensions["A"].width = 34
    ws_opts.column_dimensions["B"].width = 22
    ws_opts.column_dimensions["C"].width = 64

    # Header row
    for col_idx, header in enumerate(["option", "value", "description"], start=1):
        c = ws_opts.cell(row=1, column=col_idx, value=header)
        c.font = styles.header_font_white
        c.fill = styles.header_fill
        c.alignment = Alignment(horizontal="left")

    for row_idx, (key, desc, default) in enumerate(_OPTIONS_FIELDS, start=2):
        ws_opts.cell(row=row_idx, column=1, value=key).font = Font(bold=False)
        if default is not None:
            ws_opts.cell(row=row_idx, column=2, value=default)
        ws_opts.cell(row=row_idx, column=3, value=desc).font = Font(
            italic=True, color="404040"
        )

    ws_opts.freeze_panes = "A2"


def _add_data_sheet(
    wb: Any,
    styles: _WorkbookStyles,
    sheet_name: str,
    fields: list[tuple[str, str, bool, str, Any]],
    examples: list[dict[str, Any]] | None = None,
) -> None:
    """Create a data worksheet with headers, help row, and example data."""
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    ws_data = wb.create_sheet(sheet_name)

    num_fields = len(fields)
    for col_idx, (fname, ftype, required, desc, _example) in enumerate(fields, start=1):
        # Header row
        header_cell = ws_data.cell(row=1, column=col_idx, value=fname)
        header_cell.font = styles.header_font_white
        if required:
            header_cell.fill = styles.header_fill  # dark blue = required
        else:
            header_cell.fill = styles.optional_header_fill  # medium
        header_cell.alignment = Alignment(horizontal="left")

        # Help row
        help_text = f"[{ftype}]{'*' if required else ''} {desc}"
        help_cell = ws_data.cell(row=2, column=col_idx, value=help_text)
        help_cell.font = Font(name="Calibri", size=8, italic=True, color="404040")
        if required:
            help_cell.fill = styles.required_fill
        else:
            help_cell.fill = styles.help_fill

    # Column widths
    for col_idx, (fname, _ft, _req, _desc, _ex) in enumerate(fields, start=1):
        col_letter = get_column_letter(col_idx)
        ws_data.column_dimensions[col_letter].width = max(14, len(fname) + 4)

    # Example rows
    if examples:
        for row_offset, example_row in enumerate(examples):
            data_row = 3 + row_offset
            for col_idx, (fname, _ft, _req, _desc, _default_ex) in enumerate(
                fields, start=1
            ):
                val = example_row.get(fname, None)
                if val is not None:
                    ws_data.cell(row=data_row, column=col_idx, value=val)
    else:
        # Pre-fill a single blank example row using per-field defaults
        for col_idx, (_fname, _ft, _req, _desc, example) in enumerate(fields, start=1):
            if example is not None:
                ws_data.cell(row=3, column=col_idx, value=example)

    ws_data.freeze_panes = "A3"

    # Legend in a far-right column
    legend_col = num_fields + 2
    leg_label = ws_data.cell(row=1, column=legend_col, value="Legend")
    leg_label.font = Font(bold=True)
    ws_data.cell(row=2, column=legend_col, value="Dark blue = required")
    ws_data.cell(row=3, column=legend_col, value="Medium blue = optional")
    ws_data.cell(row=4, column=legend_col, value="* = required field")
    ws_data.cell(
        row=5,
        column=legend_col,
        value="number|array|filename: scalar, inline array, or filename",
    )
    ws_data.column_dimensions[get_column_letter(legend_col)].width = 46


def _build_array_sheets(
    wb: Any,
    styles: _WorkbookStyles,
    sheet_list: list[str],
    contract: TemplateContract | None = None,
) -> None:
    """Create one data sheet per array name (columns from the contract)."""
    for sheet in sheet_list:
        fields = contract.sheet_fields(sheet) if contract else FIELD_META.get(sheet, [])
        if not fields:
            continue
        if contract is not None:
            for name in contract.undocumented(sheet):
                logging.warning(
                    "%s.%s: contract field without FIELD_META row "
                    "(column added as undocumented)",
                    sheet,
                    name,
                )
            for name in contract.stale(sheet):
                logging.warning(
                    "%s.%s: FIELD_META column is not accepted by gtopt (%s) — dropped",
                    sheet,
                    name,
                    contract.source,
                )
        examples = _EXAMPLES.get(sheet)
        _add_data_sheet(wb, styles, sheet, fields, examples)


def _build_timeseries_sheets(wb: Any, styles: _WorkbookStyles) -> None:
    """Create the example time-series worksheets (Demand@lmax, etc.)."""
    # Demand@lmax
    ws_ts = wb.create_sheet("Demand@lmax")
    for col_idx, header in enumerate(
        ["scenario", "stage", "block", "d1", "d2"], start=1
    ):
        c = ws_ts.cell(row=1, column=col_idx, value=header)
        c.font = styles.header_font_white
        c.fill = styles.header_fill
    ws_ts.cell(row=2, column=1, value=1)
    ws_ts.cell(row=2, column=2, value=1)
    ws_ts.cell(row=2, column=3, value=1)
    ws_ts.cell(row=2, column=4, value=80.0)
    ws_ts.cell(row=2, column=5, value=150.0)
    ws_ts.freeze_panes = "A2"

    # GeneratorProfile@profile
    ws_ts2 = wb.create_sheet("GeneratorProfile@profile")
    for col_idx, header in enumerate(
        ["scenario", "stage", "block", "g_solar"], start=1
    ):
        c = ws_ts2.cell(row=1, column=col_idx, value=header)
        c.font = styles.header_font_white
        c.fill = styles.header_fill
    ws_ts2.cell(row=2, column=1, value=1)
    ws_ts2.cell(row=2, column=2, value=1)
    ws_ts2.cell(row=2, column=3, value=1)
    ws_ts2.cell(row=2, column=4, value=0.75)
    ws_ts2.freeze_panes = "A2"


def _build_workbook(
    output_path: pathlib.Path, contract: TemplateContract | None = None
) -> None:
    """Build the Excel template workbook and write to *output_path*.

    ``contract`` defaults to the bundled snapshot.
    """
    try:
        import openpyxl
    except ImportError as exc:
        print(
            f"Error: openpyxl is required.  Install it with: pip install openpyxl\n{exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    contract = contract or load_template_contract("snapshot")
    styles = _WorkbookStyles()
    wb = openpyxl.Workbook()

    default_sheet = wb.active
    default_sheet.title = ".introduction"
    _build_introduction_sheet(default_sheet, styles)

    _build_options_sheet(wb, styles)
    _build_array_sheets(wb, styles, contract.arrays("Simulation"), contract)
    _build_array_sheets(wb, styles, contract.arrays("System"), contract)
    _build_timeseries_sheets(wb, styles)
    _build_boundary_cuts_sheet(wb, styles)

    wb.save(output_path)
    print(
        f"Template written to: {output_path} (contract: {contract.source})",
        file=sys.stderr,
    )


def _list_sheets(contract: TemplateContract | None = None) -> None:
    """Print the sheets that would be generated (does not write a file)."""
    contract = contract or load_template_contract("snapshot")
    print(f"# contract source: {contract.source}")
    for section in ("Simulation", "System"):
        print(f"\n# {section} sheets:")
        for sheet in contract.arrays(section):
            fields = contract.sheet_fields(sheet)
            undoc = contract.undocumented(sheet)
            note = f"  ({len(undoc)} undocumented)" if undoc else ""
            print(f"  {sheet}  [{len(fields)} columns]{note}")
