# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for ``gtopt_check_json._file_reader``."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from gtopt_check_json import _file_reader as fr


def test_build_table_path_with_class_override(tmp_path: Path) -> None:
    assert fr.build_table_path(str(tmp_path), "Demand", "Gen@pmax") == str(
        tmp_path / "Gen" / "pmax"
    )
    assert fr.build_table_path(str(tmp_path), "Demand", "lmax") == str(
        tmp_path / "Demand" / "lmax"
    )


def test_try_read_table_csv_preferred_and_corrupt_fallback(tmp_path: Path) -> None:
    base = tmp_path / "Demand" / "lmax"
    base.parent.mkdir()
    # A corrupt CSV is skipped and the parquet copy is used instead.
    (tmp_path / "Demand" / "lmax.csv").write_bytes(b'\xff\xfe\x00garbage,\n"')
    pd.DataFrame({"block": [1], "uid:1": [4.0]}).to_parquet(str(base) + ".parquet")
    df = fr.try_read_table(str(base), preferred_format="csv")
    assert df is not None
    assert df["uid:1"].tolist() == [4.0]


def test_try_read_table_missing(tmp_path: Path) -> None:
    assert fr.try_read_table(str(tmp_path / "none")) is None


def test_pivot_scalar_per_element() -> None:
    df = pd.DataFrame({"uid": [1, 1, 2], "value": [5.0, 6.0, 7.0]})
    wide = fr._pivot_long_to_wide(df)  # noqa: SLF001
    assert list(wide.columns) == ["uid:1", "uid:2"]
    assert wide.iloc[0].tolist() == [5.0, 7.0]


def _write_long(tmp_path: Path) -> None:
    (tmp_path / "Demand").mkdir(exist_ok=True)
    pd.DataFrame(
        {
            "scenario": [1, 1, 2],
            "block": [1, 2, 1],
            "uid": [3, 3, 3],
            "value": [10.0, 20.0, 30.0],
        }
    ).to_csv(tmp_path / "Demand" / "lmax.csv", index=False)


def test_resolve_value_and_series(tmp_path: Path) -> None:
    _write_long(tmp_path)
    base = str(tmp_path)
    assert fr.resolve_file_sched_value(base, "Demand", "lmax", 3, None, 1, 2) == 20.0
    # No exact match -> first value.
    assert fr.resolve_file_sched_value(base, "Demand", "lmax", 3, None, 9, 9) == 10.0
    assert fr.resolve_file_sched_value(base, "Demand", "lmax", 4, None, 1, 1) is None
    assert fr.resolve_file_sched_value(base, "Demand", "none", 3, None, 1, 1) is None
    assert fr.resolve_file_sched_series(base, "Demand", "lmax", 3, None) == [
        10.0,
        20.0,
        30.0,
    ]
    assert not fr.resolve_file_sched_series(base, "Demand", "lmax", 4, None)
    assert not fr.resolve_file_sched_series(base, "Demand", "none", 3, None)


@pytest.mark.parametrize(
    ("columns", "uid", "name", "expected"),
    [
        (["uid:1"], 1, None, "uid:1"),
        (["d1"], 1, "d1", "d1"),
        (["1"], 1, "zz", "1"),
        (["x"], None, None, None),
    ],
)
def test_find_element_column(
    columns: list[str], uid: int | None, name: str | None, expected: str | None
) -> None:
    df = pd.DataFrame({c: [1.0] for c in columns})
    assert fr._find_element_column(df, uid, name) == expected  # noqa: SLF001


def test_lookup_value_empty_frame() -> None:
    df = pd.DataFrame({"scenario": pd.Series([], dtype=int), "uid:1": []})
    assert fr._lookup_value(df, 1, None, 1, None) is None  # noqa: SLF001
