"""Edge-case tests for :mod:`plp2gtopt.base_writer`."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from plp2gtopt.base_writer import BaseWriter, convert_tree_to_long


class _Writer(BaseWriter):
    """Concrete writer around plain item lists."""


class _Units:
    def __init__(self, units: dict[str, dict[str, Any]]) -> None:
        self._units = units

    def get_item_by_name(self, name: str) -> dict[str, Any] | None:
        return self._units.get(name)


def test_items_and_json_array_defaults() -> None:
    w = _Writer()
    assert w.get_items() is None
    assert not w.to_json_array()
    assert w.to_json_array([{"a": 1}]) == [{"a": 1}]
    assert w._build_json_item(a=1, b=None) == {"a": 1}  # pylint: disable=protected-access


def test_write_to_file_error(tmp_path: Path) -> None:
    w = _Writer()
    w.items = [{"bad": {1, 2}}]  # sets are not JSON serialisable
    with pytest.raises((IOError, TypeError)):
        w.write_to_file(tmp_path / "out" / "x.json")


def test_dataframe_skips_and_fills() -> None:
    w = _Writer(options={"use_uid_label": False})
    units = _Units(
        {
            "A": {"number": 1, "type": "termica", "pmax": 9.0},
            "B": {"number": 2, "type": "falla"},
            "C": {"number": 3, "type": "termica"},
        }
    )
    items = [
        {"name": "A", "values": [1.0, 2.0], "block": [1, 2]},
        {"name": "B", "values": [1.0], "block": [1]},  # skipped type
        {"name": "C", "values": [], "block": []},  # no data
        {"name": "Z", "values": [1.0], "block": [1]},  # unknown unit
    ]
    # pylint: disable=protected-access
    df = w._create_dataframe(
        items,
        units,  # type: ignore[arg-type]
        None,
        value_field="values",
        index_field="block",
        index_name="block",
        fill_field="pmax",
        skip_types=("falla",),
        value_oper=lambda v: v * 2,
    )
    assert list(df.columns) == ["A:1"]
    assert df["A:1"].tolist() == [2.0, 4.0]
    assert w._create_dataframe([], None, None, "v", "b").empty


def test_compression_and_format_helpers() -> None:
    w = _Writer(options={"compression": "none", "compression_level": 3})
    assert w.get_compression() is None
    assert w.get_compression_level() == 3
    assert w.get_compression_kwargs() == {"compression_level": 3}
    assert w.get_compression_level({"compression_level": 0}) is None
    assert w.get_output_format({"output_format": "csv"}) == "csv"
    assert w.pcol_name("A", "busname") == "busname"


def test_get_last_stage_variants() -> None:
    assert _Writer()._get_last_stage() == sys.maxsize  # pylint: disable=protected-access
    w = _Writer(options={"last_stage": "bad"})
    assert w._get_last_stage() == sys.maxsize  # pylint: disable=protected-access
    w = _Writer(options={"last_stage": 0, "last_time": 10})
    blocks = [
        {"accumulated_time": 5.0, "stage": 1},
        {"accumulated_time": 12.0, "stage": 2},
    ]
    assert w._get_last_stage(blocks) == 2  # pylint: disable=protected-access
    w = _Writer(options={"last_stage": 4, "last_time": 100})
    assert w._get_last_stage(blocks) == 4  # pylint: disable=protected-access


def test_as_output_layout_keeps_wide_when_requested() -> None:
    df = pd.DataFrame({"block": [1], "uid:1": [5.0]})
    w = _Writer(options={"layout": "wide"})
    assert w._as_output_layout(df) is df  # pylint: disable=protected-access
    w = _Writer(options={"excel_output": True})
    assert w._as_output_layout(df) is df  # pylint: disable=protected-access


def test_convert_tree_to_long_csv_and_bad_files(tmp_path: Path) -> None:
    pd.DataFrame({"block": [1, 2], "uid:1": [1.0, 2.0]}).to_csv(
        tmp_path / "field.csv", index=False
    )
    pd.DataFrame({"block": [1], "duration": [2.0]}).to_csv(
        tmp_path / "structural.csv", index=False
    )
    (tmp_path / "broken.parquet").write_bytes(b"not parquet")
    (tmp_path / "empty.csv").write_bytes(b"")
    pd.DataFrame({"block": [1], "duration": [2.0]}).to_parquet(
        tmp_path / "structural.parquet"
    )
    n = convert_tree_to_long(tmp_path, {"compression": "none", "compression_level": 1})
    assert n == 1
    long_df = pd.read_csv(tmp_path / "field.csv")
    assert set(long_df.columns) >= {"uid", "value"}
