# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for the streaming readers in ``_reader``.

The same logical data set is written in both the *wide* (``uid:N``
columns) and the *long* (``uid`` + ``value``) hive layouts, including
all-null columns / rows and a null ``uid`` so every defensive ``None``
guard of the aggregators is exercised.  Every aggregator is checked
against a hand-computed expectation.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gtopt_check_output import _reader as rd

_DUR = {1: 2.0, 2: 3.0}


def _write(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def _wide_schema(uids: tuple[int, ...]) -> pa.Schema:
    fields = [pa.field("block", pa.int32())]
    fields += [pa.field(f"uid:{u}", pa.float64()) for u in uids]
    return pa.schema(fields)


def _wide_part(root: Path, phase: int, data: dict[str, list]) -> None:
    uids = tuple(int(k.split(":")[1]) for k in data if k.startswith("uid:"))
    table = pa.table(data, schema=_wide_schema(uids))
    _write(root / f"scene=0/phase={phase}/part.parquet", table)


_LONG_SCHEMA = pa.schema(
    [
        pa.field("scenario", pa.int32()),
        pa.field("stage", pa.int32()),
        pa.field("block", pa.int32()),
        pa.field("uid", pa.int32()),
        pa.field("value", pa.float64()),
    ]
)


def _long_part(root: Path, phase: int, rows: list[tuple]) -> None:
    cols = list(zip(*rows)) if rows else [[], [], []]
    blocks, uids, values = (list(c) for c in cols)
    table = pa.table(
        {
            "scenario": [0] * len(blocks),
            "stage": [1] * len(blocks),
            "block": blocks,
            "uid": uids,
            "value": values,
        },
        schema=_LONG_SCHEMA,
    )
    _write(root / f"scene=0/phase={phase}/part.parquet", table)


@pytest.fixture(name="wide")
def _wide(tmp_path: Path) -> rd.pads.Dataset:
    root = tmp_path / "W" / "x_sol.parquet"
    _wide_part(
        root,
        0,
        {
            "block": [1, 2],
            "uid:1": [10.0, -5.0],
            "uid:2": [None, None],
            "uid:3": [1.0, 2.0],
        },
    )
    _wide_part(
        root,
        1,
        {
            "block": [1, 2],
            "uid:1": [3.0, 4.0],
            "uid:2": [None, None],
            "uid:3": [None, 7.0],
        },
    )
    ds = rd.open_dataset(tmp_path, "W/x_sol")
    assert ds is not None
    assert rd.dataset_layout(ds) == "wide"
    return ds


@pytest.fixture(name="long")
def _long(tmp_path: Path) -> rd.pads.Dataset:
    root = tmp_path / "L" / "x_sol.parquet"
    _long_part(root, 0, [(1, 1, 10.0), (2, 1, -5.0), (1, 3, 1.0), (2, 3, 2.0)])
    _long_part(root, 1, [(1, 1, 3.0), (2, 1, 4.0), (2, 3, 7.0)])
    # A partition holding only a null-uid / null-value row.
    _long_part(root, 2, [(1, None, None)])
    ds = rd.open_dataset(tmp_path, "L/x_sol")
    assert ds is not None
    assert rd.dataset_layout(ds) == "long"
    return ds


@pytest.fixture(name="no_uid")
def _no_uid(tmp_path: Path) -> rd.pads.Dataset:
    _write(
        tmp_path / "N" / "x_sol.parquet" / "scene=0/phase=0/part.parquet",
        pa.table({"block": [1], "other": [1.0]}),
    )
    ds = rd.open_dataset(tmp_path, "N/x_sol")
    assert ds is not None
    return ds


# ---------------------------------------------------------------------------
# Aggregators: both layouts against the same expectations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["wide", "long"])
def test_aggregators(layout: str, request: pytest.FixtureRequest) -> None:
    ds = request.getfixturevalue(layout)
    assert rd.streaming_uid_sum(ds) == pytest.approx(22.0)
    per_col = rd.streaming_uid_sum_per_col(ds)
    assert per_col[1] == pytest.approx(12.0)
    assert per_col[3] == pytest.approx(10.0)
    assert per_col.get(2, 0.0) == 0.0

    stats = rd.streaming_uid_stats(ds)
    assert stats["count"] == 7
    assert stats["sum"] == pytest.approx(22.0)
    assert stats["min"] == pytest.approx(-5.0)
    assert stats["max"] == pytest.approx(10.0)
    assert stats["n_neg"] == 1
    assert stats["mean"] == pytest.approx(22.0 / 7)

    w = rd.streaming_sol_weighted_sum_per_uid(ds, _DUR)
    assert w[1] == pytest.approx(23.0)
    assert w[3] == pytest.approx(29.0)

    assert rd.streaming_sol_weighted_sum(ds, _DUR) == pytest.approx(52.0)
    assert rd.streaming_sol_weighted_sum(ds, _DUR, {1: 2.0}) == pytest.approx(46.0)
    assert rd.streaming_sol_weighted_sum(
        ds, _DUR, None, abs_value=True
    ) == pytest.approx(82.0)

    sqr = rd.streaming_sqr_weighted_sum_per_uid(ds, _DUR, {1: 1.0, 2: 1.0, 3: 0.5})
    assert sqr == pytest.approx({1: 341.0, 3: 80.5})
    sqr_only1 = rd.streaming_sqr_weighted_sum_per_uid(ds, _DUR, {1: 1.0})
    assert sqr_only1 == pytest.approx({1: 341.0})

    absw = rd.streaming_abs_weighted_sum_per_uid(ds, _DUR)
    assert absw == pytest.approx({1: 53.0, 3: 29.0})


@pytest.mark.parametrize(
    "func",
    [
        rd.streaming_uid_sum,
        rd.streaming_uid_sum_per_col,
        rd.streaming_uid_stats,
    ],
)
def test_unary_aggregators_none_and_no_uid(func, no_uid: rd.pads.Dataset) -> None:
    assert not func(None)
    assert not func(no_uid)


def test_weighted_aggregators_none_and_no_uid(no_uid: rd.pads.Dataset) -> None:
    for ds in (None, no_uid):
        assert rd.streaming_sol_weighted_sum(ds, _DUR) == 0.0
        assert not rd.streaming_sqr_weighted_sum_per_uid(ds, _DUR, {1: 1.0})
        assert not rd.streaming_abs_weighted_sum_per_uid(ds, _DUR)
    assert not rd.streaming_sol_weighted_sum_per_uid(None, _DUR)
    assert not rd.streaming_sol_weighted_sum_per_uid(no_uid, _DUR)
    assert rd.streaming_pairwise_weighted_sum(None, no_uid, _DUR) == 0.0
    assert rd.streaming_pairwise_weighted_sum(no_uid, None, _DUR) == 0.0
    assert rd.streaming_pairwise_weighted_sum(no_uid, no_uid, _DUR) == 0.0


def test_stats_all_null(tmp_path: Path) -> None:
    root = tmp_path / "W" / "n.parquet"
    _wide_part(root, 0, {"block": [1], "uid:1": [None]})
    ds = rd.open_dataset(tmp_path, "W/n")
    assert rd.streaming_uid_stats(ds)["count"] == 0
    lroot = tmp_path / "L" / "n.parquet"
    _long_part(lroot, 0, [(1, 1, None)])
    lds = rd.open_dataset(tmp_path, "L/n")
    assert rd.streaming_uid_stats(lds) == {
        "count": 0,
        "sum": 0.0,
        "min": 0.0,
        "max": 0.0,
        "mean": 0.0,
        "n_neg": 0,
    }


def test_scan_batches_without_column_filter(wide: rd.pads.Dataset) -> None:
    assert not list(rd._scan_batches(None, None))  # noqa: SLF001
    batches = list(rd._scan_batches(wide, None))  # noqa: SLF001
    assert sum(b.num_rows for b in batches) == 4


# ---------------------------------------------------------------------------
# Pairwise product
# ---------------------------------------------------------------------------


def test_pairwise_long(tmp_path: Path) -> None:
    a = tmp_path / "A" / "s.parquet"
    b = tmp_path / "B" / "s.parquet"
    _long_part(a, 0, [(1, 1, 2.0), (2, 1, 3.0)])
    _long_part(b, 0, [(1, 1, 10.0), (2, 1, 1.0), (2, 9, 5.0)])
    # phase 1: no matching key -> empty merge
    _long_part(a, 1, [(1, 1, 2.0)])
    _long_part(b, 1, [(1, 2, 2.0)])
    # phase 2: empty fragment on one side
    _long_part(a, 2, [])
    _long_part(b, 2, [(1, 1, 1.0)])
    # phase 3: only present in b
    _long_part(b, 3, [(1, 1, 1.0)])
    ds_a = rd.open_dataset(tmp_path, "A/s")
    ds_b = rd.open_dataset(tmp_path, "B/s")
    # 2*10*2 + 3*1*3 = 49
    assert rd.streaming_pairwise_weighted_sum(ds_a, ds_b, _DUR) == pytest.approx(49.0)


def test_pairwise_wide(tmp_path: Path) -> None:
    a = tmp_path / "A" / "s.parquet"
    b = tmp_path / "B" / "s.parquet"
    _wide_part(a, 0, {"block": [1, 2], "uid:1": [2.0, 3.0], "uid:5": [None, None]})
    _wide_part(b, 0, {"block": [1, 2], "uid:1": [10.0, 1.0], "uid:5": [None, None]})
    # phase 1: fragment in a lacks the shared columns
    _wide_part(a, 1, {"block": [1], "uid:7": [1.0]})
    _wide_part(b, 1, {"block": [1], "uid:1": [1.0], "uid:5": [1.0]})
    # phase 2: only in b
    _wide_part(b, 2, {"block": [1], "uid:1": [1.0], "uid:5": [1.0]})
    ds_a = rd.open_dataset(tmp_path, "A/s")
    ds_b = rd.open_dataset(tmp_path, "B/s")
    assert rd.streaming_pairwise_weighted_sum(ds_a, ds_b, _DUR) == pytest.approx(49.0)


def test_pairwise_wide_no_shared(tmp_path: Path) -> None:
    _wide_part(tmp_path / "A" / "s.parquet", 0, {"block": [1], "uid:1": [1.0]})
    _wide_part(tmp_path / "B" / "s.parquet", 0, {"block": [1], "uid:2": [1.0]})
    ds_a = rd.open_dataset(tmp_path, "A/s")
    ds_b = rd.open_dataset(tmp_path, "B/s")
    assert rd.streaming_pairwise_weighted_sum(ds_a, ds_b, _DUR) == 0.0


def test_pairwise_single_files_never_pair(tmp_path: Path) -> None:
    """Single-file datasets have no ``.parquet/`` key suffix: no pairing."""
    for name in ("a", "b"):
        _write(tmp_path / f"{name}.parquet", pa.table({"block": [1], "uid:1": [1.0]}))
    ds_a = rd.open_dataset(tmp_path, "a")
    ds_b = rd.open_dataset(tmp_path, "b")
    assert rd.streaming_pairwise_weighted_sum(ds_a, ds_b, _DUR) == 0.0
    for name in ("la", "lb"):
        _write(
            tmp_path / f"{name}.parquet",
            pa.table(
                {
                    "scenario": [0],
                    "stage": [1],
                    "block": [1],
                    "uid": [1],
                    "value": [1.0],
                }
            ),
        )
    ds_la = rd.open_dataset(tmp_path, "la")
    ds_lb = rd.open_dataset(tmp_path, "lb")
    assert rd.streaming_pairwise_weighted_sum(ds_la, ds_lb, _DUR) == 0.0


# ---------------------------------------------------------------------------
# open_dataset / read_table layouts and error paths
# ---------------------------------------------------------------------------


class TestOpenDataset:
    """Format precedence and failure handling of ``open_dataset``."""

    def test_single_parquet_file(self, tmp_path: Path) -> None:
        _write(tmp_path / "x.parquet", pa.table({"block": [1], "uid:1": [4.0]}))
        ds = rd.open_dataset(tmp_path, "x")
        assert rd.streaming_uid_sum(ds) == 4.0

    def test_corrupt_parquet(self, tmp_path: Path) -> None:
        (tmp_path / "x.parquet").write_bytes(b"not parquet")
        assert rd.open_dataset(tmp_path, "x") is None

    def test_csv_shards_nested_and_top_level(self, tmp_path: Path) -> None:
        (tmp_path / "Gen").mkdir()
        for i in (0, 1):
            (tmp_path / "Gen" / f"g_s0_p{i}.csv").write_text("block,uid:1\n1,2.5\n")
            (tmp_path / f"top_s0_p{i}.csv").write_text("block,uid:1\n1,1.5\n")
        assert rd.streaming_uid_sum(rd.open_dataset(tmp_path, "Gen/g")) == 5.0
        assert rd.streaming_uid_sum(rd.open_dataset(tmp_path, "top")) == 3.0

    def test_single_csv_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "Gen").mkdir()
        (tmp_path / "Gen" / "g.csv").write_text("block,uid:1\n1,2.0\n")
        assert rd.streaming_uid_sum(rd.open_dataset(tmp_path, "Gen/g")) == 2.0

    def test_missing(self, tmp_path: Path) -> None:
        assert rd.open_dataset(tmp_path, "Gen/none") is None

    def test_bad_csv(self, tmp_path: Path) -> None:
        (tmp_path / "bad_s0_p0.csv").write_bytes(b"")
        assert rd.open_dataset(tmp_path, "bad") is None


class TestReadTableErrors:
    """``read_table`` returns ``None`` (and logs) on unreadable inputs."""

    def test_corrupt_parquet(self, tmp_path: Path) -> None:
        (tmp_path / "x.parquet").write_bytes(b"garbage")
        assert rd.read_table(tmp_path, "x") is None

    def test_bad_csv_shard(self, tmp_path: Path) -> None:
        (tmp_path / "x_s0_p0.csv.gz").write_bytes(b"not gzip")
        assert rd.read_table(tmp_path, "x") is None

    def test_bad_single_csv(self, tmp_path: Path) -> None:
        (tmp_path / "x.csv").write_bytes(b"")
        assert rd.read_table(tmp_path, "x") is None


# ---------------------------------------------------------------------------
# Planning metadata helpers
# ---------------------------------------------------------------------------


def test_line_info_non_numeric_fields() -> None:
    planning = {
        "system": {
            "line_array": [
                {
                    "uid": 4,
                    "tmax": "tmax.parquet",
                    "resistance": "r.parquet",
                    "voltage": "v.parquet",
                }
            ]
        }
    }
    row = rd.get_line_info(planning).iloc[0]
    assert row["name"] == "uid:4"
    assert row["tmax"] == 0.0
    assert row["resistance"] == 0.0
    assert row["voltage"] == 0.0


def test_profile_info_generator_references() -> None:
    planning = {
        "system": {
            "generator_array": [{"uid": 7, "name": "solar"}],
            "generator_profile_array": [
                {"uid": 1, "generator": 7},
                {"uid": 2, "generator": "7"},
                {"uid": 3, "generator": "solar"},
                {"uid": 4, "generator": "ghost"},
            ],
        }
    }
    df = rd.get_generator_profile_info(planning)
    assert isinstance(df, pd.DataFrame)
    assert df["generator_uid"].tolist() == [7, 7, 7, -1]
    assert df["name"].tolist()[0] == "uid:1"
