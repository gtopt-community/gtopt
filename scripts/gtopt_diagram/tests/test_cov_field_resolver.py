# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge cases of FieldSchedResolver: dimension fallbacks, layouts, errors."""

import sys

import pandas as pd

from gtopt_diagram._field_resolver import FieldSchedResolver, _select_or_first


def _gen_dir(tmp_path):
    d = tmp_path / "Generator"
    d.mkdir(exist_ok=True)
    return d


def test_select_or_first():
    df = pd.DataFrame({"block": [3, 3, 4], "v": [1, 2, 3]})
    assert _select_or_first(df, "block", 4)["v"].tolist() == [3]
    assert _select_or_first(df, "block", 9)["v"].tolist() == [1, 2]  # first block
    assert _select_or_first(df, "stage", 1) is df  # no such column
    empty = df.iloc[0:0]
    assert _select_or_first(empty, "block", 1).empty


def test_long_layout_with_dimension_fallbacks(tmp_path):
    pd.DataFrame(
        {
            "scenario": [5, 5, 6],
            "stage": [2, 2, 2],
            "block": [7, 7, 7],
            "uid": [1, 2, 1],
            "value": [10.0, 20.0, 30.0],
        }
    ).to_parquet(_gen_dir(tmp_path) / "pmax.parquet")
    # scenario 1 / stage 1 / block 1 are absent → first values (5, 2, 7)
    res = FieldSchedResolver(tmp_path)
    assert res.resolve("Generator", "pmax", 1) == 10.0
    assert res.resolve("Generator", "pmax", 2) == 20.0
    assert res.resolve("Generator", "pmax", 3, fallback="x") == "x"
    # selecting scenario 6 picks the other row
    assert (
        FieldSchedResolver(tmp_path, scenario=6).resolve("Generator", "pmax", 1) == 30.0
    )


def test_long_layout_skips_bad_uids_and_keeps_first_row(tmp_path):
    (_gen_dir(tmp_path) / "lmax.csv").write_text("uid,value\nabc,1.0\n4,2.0\n4,3.0\n")
    res = FieldSchedResolver(tmp_path)
    assert res.resolve("Generator", "lmax", 4) == 2.0


def test_long_layout_none_cells_are_skipped():
    res = FieldSchedResolver(".")
    df = pd.DataFrame({"uid": [None, 2, 3], "value": [1.0, None, 3.0]}, dtype=object)
    assert res._extract_row(df) == {3: 3.0}  # pylint: disable=protected-access


def test_wide_layout_bad_columns_and_empty_frames():
    res = FieldSchedResolver(".")
    df = pd.DataFrame({"uid:x": [1.0], "uid:2": [5.0], "other": [9.0]})
    assert res._extract_row(df) == {2: 5.0}  # pylint: disable=protected-access
    empty = pd.DataFrame({"block": [], "uid:1": []})
    assert not res._extract_row(empty)  # pylint: disable=protected-access
    none_val = pd.DataFrame({"uid:1": [None]}, dtype=object)
    assert not res._extract_row(none_val)  # pylint: disable=protected-access


def test_unreadable_file_returns_fallback(tmp_path):
    (_gen_dir(tmp_path) / "pmax.parquet").write_bytes(b"not parquet")
    assert FieldSchedResolver(tmp_path).resolve("Generator", "pmax", 1, 0.0) == 0.0


def test_missing_optional_libraries(tmp_path, monkeypatch):
    d = _gen_dir(tmp_path)
    pd.DataFrame({"uid:1": [1.0]}).to_parquet(d / "a.parquet")
    (d / "b.csv").write_text("uid:1\n2.0\n")
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)
    monkeypatch.setitem(sys.modules, "pandas", None)
    res = FieldSchedResolver(tmp_path)
    assert res.resolve("Generator", "a", 1, "fb") == "fb"
    assert res.resolve("Generator", "b", 1, "fb") == "fb"


def test_cache_is_reused(tmp_path):
    path = _gen_dir(tmp_path) / "pmax.csv"
    path.write_text("uid:1\n1.0\n")
    res = FieldSchedResolver(tmp_path)
    assert res.resolve("Generator", "pmax", 1) == 1.0
    path.write_text("uid:1\n2.0\n")
    assert res.resolve("Generator", "pmax", 1) == 1.0  # cached
