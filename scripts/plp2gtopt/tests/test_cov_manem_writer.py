"""Fallback branches of :class:`plp2gtopt.manem_writer.ManemWriter`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from plp2gtopt.manem_parser import ManemParser
from plp2gtopt.manem_writer import ManemWriter


class _Centrals:
    def __init__(self, by_num: dict[int, Any], by_name: dict[str, Any]) -> None:
        self.by_num = by_num
        self.by_name = by_name

    def get_item_by_number(self, number: int) -> Any:
        if number not in self.by_num:
            raise KeyError(number)
        return self.by_num[number]

    def get_item_by_name(self, name: str) -> Any:
        if name not in self.by_name:
            raise KeyError(name)
        return self.by_name[name]


def _writer(tmp_path: Path, centrals: Any) -> ManemWriter:
    path = tmp_path / "plpmanem.dat"
    path.write_text("# n\n0\n", encoding="latin-1")
    parser = ManemParser(path)
    parser.parse()
    return ManemWriter(parser, centrals)


def test_base_value_for_column(tmp_path: Path) -> None:
    assert _writer(tmp_path, None)._base_value_for_column("emin", "uid:1") == 0.0
    centrals = _Centrals(
        {1: {"emin": 3.0}, 2: {"emin": "bad"}},
        {"7": {"emin": 9.0}, "LAKE": {"emax": 5.0}},
    )
    writer = _writer(tmp_path, centrals)
    base = writer._base_value_for_column  # pylint: disable=protected-access
    assert base("emin", "uid:1") == pytest.approx(3.0)
    assert base("emin", "uid:2") == 0.0
    assert base("emin", "uid:7") == pytest.approx(9.0)  # number miss -> name
    assert base("emin", "LAKE") == 0.0  # field missing
    assert base("emax", "LAKE") == pytest.approx(5.0)
    assert base("emin", "GHOST") == 0.0


def test_to_dataframe_wraps_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = _writer(tmp_path, None)

    def boom(_field: str, _items: Any) -> Any:
        raise RuntimeError("bad")

    monkeypatch.setattr(writer, "_create_dataframe_for_field", boom)
    items = [
        {
            "name": "A",
            "stage": np.array([1]),
            "emin": np.array([1.0]),
            "emax": np.array([2.0]),
        }
    ]
    with pytest.raises(ValueError, match="Failed to create DataFrames"):
        writer.to_dataframe(items)
