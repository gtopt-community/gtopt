# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for :mod:`gtopt_timeseries_export`."""

from __future__ import annotations

import builtins
import json
import zipfile

import openpyxl
import pytest

from gtopt_timeseries_export import export_from_dict, export_to_excel
from gtopt_timeseries_export.main import main as cli_main

_SOL = (
    "scene,phase,status,status_name,obj_value,kappa,max_kappa,"
    "scale_objective\n0,0,0,optimal,3.0,1,1,1\n"
)


def test_skips_non_dict_and_columnless_entries(tmp_path):
    results = {
        "solution": {},
        "outputs": {
            "Bad/not_a_dict": [1, 2, 3],
            "Empty/no_columns": {"columns": [], "data": []},
            "Generator/generation_sol": {
                "columns": ["block", "uid:1"],
                "data": [[1, 5.0]],
            },
        },
    }
    dest = export_from_dict(results, tmp_path / "sub" / "out.xlsx")
    wb = openpyxl.load_workbook(dest)
    assert wb.sheetnames == ["Overview", "Generator_generation_sol"]


def test_missing_openpyxl_raises_runtime_error(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "openpyxl":
            raise ImportError("no openpyxl")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="openpyxl is required"):
        export_from_dict({"solution": {}, "outputs": {}}, tmp_path / "x.xlsx")


def test_export_to_excel_zip_and_cli_tech_map(tmp_path, capsys):
    zip_path = tmp_path / "results.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("solution.csv", _SOL)
        zf.writestr("Generator/generation_sol.csv", "block,uid:1\n1,4.0\n")
    out = export_to_excel(zip_path, tmp_path / "a.xlsx")
    assert out.exists()

    tech = tmp_path / "tech.json"
    tech.write_text(json.dumps({"1": "solar"}), encoding="utf-8")
    rc = cli_main([str(zip_path), "--tech-map", str(tech)])
    assert rc == 0
    wb = openpyxl.load_workbook(tmp_path / "results.xlsx")
    values = [row[0].value for row in wb["Overview"].iter_rows()]
    assert "solar" in values
    capsys.readouterr()


def test_sheet_name_sanitize_and_collisions():
    from gtopt_timeseries_export.exporter import _safe_sheet_name

    seen = {"Overview"}
    assert _safe_sheet_name("overview", seen) == "overview_2"
    assert _safe_sheet_name("[*]", seen) == "sheet"
    long = "x" * 40
    first = _safe_sheet_name(long, seen)
    second = _safe_sheet_name(long.upper(), seen)
    assert first == "x" * 31
    assert second == "X" * 29 + "_2"
