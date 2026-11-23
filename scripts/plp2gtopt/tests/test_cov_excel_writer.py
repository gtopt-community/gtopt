"""Edge branches of :mod:`plp2gtopt.excel_writer`."""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from plp2gtopt import excel_writer as ew


def test_uid_to_name_maps_skip_rules() -> None:
    planning = {
        "system": {
            "name": "x",
            "options": [{"uid": 1, "name": "a"}],
            "bus_array": [{"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"}],
            "line_array": [{"uid": 1}],
            "demand_array": [{"uid": 1, "name": "d"}, {"uid": 2, "name": "d"}],
            "empty_array": [],
        }
    }
    maps = ew._build_uid_to_name_maps(planning)  # pylint: disable=protected-access
    assert maps == {"Bus": {"uid:1": "b1", "uid:2": "b2"}}


def test_collect_field_order_without_igtopt(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("igtopt"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "igtopt.template_builder", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert ew._collect_field_order("bus_array") == []  # pylint: disable=protected-access


def test_records_and_options() -> None:
    assert ew._records_to_df([], "bus_array").empty  # pylint: disable=protected-access
    df = ew._records_to_df(  # pylint: disable=protected-access
        [{"uid": 1, "name": "a", "seg": [1, 2], "x": None}], "zzz_array"
    )
    assert df.loc[0, "seg"] == "[1,2]"
    pairs = ew._flatten_options(  # pylint: disable=protected-access
        {
            "sddp_options": {"a": None, "b": {"c": 1}, "d": 2},
            "lst": [1],
            "none": None,
            "s": "v",
        }
    )
    assert pairs == [("b", '{"c": 1}'), ("d", 2), ("lst", "[1]"), ("s", "v")]


class _WS:
    def __init__(self) -> None:
        self.cells: dict[tuple[int, int], Any] = {}
        self.column_dimensions: dict[str, Any] = {}
        self.freeze_panes = ""

    def cell(self, row: int, column: int, value: Any = None) -> Any:
        self.cells[(row, column)] = value

        class _C:
            font = None
            fill = None

        return _C()


class _Dim:
    width = 0


def test_sheets_skip_empty_and_nan() -> None:
    styles = {"HEADER_FONT": None, "HEADER_FILL": None}
    ws = _WS()
    ew._add_data_sheet(ws, "x_array", [], styles, str)  # pylint: disable=protected-access
    ew._add_timeseries_sheet(ws, "X@y", pd.DataFrame(), styles, str)  # pylint: disable=protected-access
    assert not ws.cells

    class _Cols(dict[str, Any]):
        def __missing__(self, key: str) -> Any:
            self[key] = _Dim()
            return self[key]

    ws.column_dimensions = _Cols()
    df = pd.DataFrame(
        {
            "stage": pd.Series([np.int64(1), np.int64(2)], dtype=object),
            "uid:1": pd.Series([np.float64("nan"), np.float64(2.5)], dtype=object),
            "obj": [None, "s"],
        }
    )
    ew._add_timeseries_sheet(ws, "X@y", df, styles, str)  # pylint: disable=protected-access
    assert (2, 2) not in ws.cells and ws.cells[(3, 2)] == pytest.approx(2.5)
    assert (2, 3) not in ws.cells


def test_build_excel_parquet_variants(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    out_dir = tmp_path / "case"
    (out_dir / "Demand").mkdir(parents=True)
    (out_dir / "Demand" / "nested").mkdir()
    pd.DataFrame({"stage": [1], "uid:1": [2.0]}).to_parquet(
        out_dir / "Demand" / "lmax.parquet"
    )
    pd.DataFrame({"stage": [1]}).iloc[0:0].to_parquet(
        out_dir / "Demand" / "empty.parquet"
    )
    (out_dir / "Demand" / "bad.parquet").write_text("junk", encoding="utf-8")
    pd.DataFrame({"a": [1]}).to_parquet(out_dir / "Demand" / "nested" / "n.parquet")
    planning: dict[str, Any] = {
        "options": {"x": 1},
        "simulation": {},
        "system": {"demand_array": [{"uid": 1, "name": "D1", "bus": 1}]},
    }
    target = tmp_path / "x" / "case.xlsx"
    ew.build_plp_excel(planning, out_dir, target, {})
    import openpyxl  # pylint: disable=import-outside-toplevel

    wb = openpyxl.load_workbook(target)
    assert "Demand@lmax" in wb.sheetnames
    assert "Demand@empty" not in wb.sheetnames
    assert wb["Demand@lmax"].cell(row=1, column=2).value == "D1"


def test_build_excel_requires_openpyxl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    with pytest.raises(ImportError, match="openpyxl is required"):
        ew.build_plp_excel({}, tmp_path, tmp_path / "o.xlsx", {})
