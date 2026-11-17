"""Results viewer: accept both the legacy wide and the current long layout.

Current gtopt writes LONG tables (``scenario,stage,block,uid,value``; rows
with a zero value omitted) split into per-(scene, phase) CSV shards
``<Class>/<stem>_s<N>_p<M>.csv`` or a hive-partitioned Parquet dataset
``<Class>/<stem>.parquet/scene=N/phase=M/part.parquet``.  The viewer must
pivot those back to the wide ``uid:N`` layout its plotting code consumes,
while legacy wide files keep working unchanged.
"""

import gzip
import io
import zipfile

import pandas as pd
import pytest

from guiservice.app import _parse_results_zip, app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _zip(members: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return buf.getvalue()


def _parquet(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


# Two (scene, phase) shards; uid 2 is zero (omitted) in block 2 of stage 1
# and uid 3 only appears in phase 1.
_SHARD_P0 = '"scenario","stage","block","uid","value"\n1,1,1,1,10\n1,1,1,2,5\n1,1,2,1,11\n'
_SHARD_P1 = '"scenario","stage","block","uid","value"\n1,2,3,1,12\n1,2,3,2,6\n1,2,3,3,7.5\n'

_EXPECTED_COLUMNS = ["scenario", "stage", "block", "uid:1", "uid:2", "uid:3"]
_EXPECTED_ROWS = [
    [1, 1, 1, 10, 5, 0],
    [1, 1, 2, 11, 0, 0],
    [1, 2, 3, 12, 6, 7.5],
]


def _assert_expected(output):
    assert output["columns"] == _EXPECTED_COLUMNS
    assert len(output["data"]) == len(_EXPECTED_ROWS)
    for got, want in zip(output["data"], _EXPECTED_ROWS, strict=True):
        assert got == pytest.approx(want)


class TestLongLayout:
    def test_csv_shards_are_concatenated_and_pivoted(self):
        results = _parse_results_zip(
            _zip(
                {
                    "output/Generator/generation_sol_s0_p1.csv": _SHARD_P1,
                    "output/Generator/generation_sol_s0_p0.csv": _SHARD_P0,
                }
            )
        )
        assert list(results["outputs"]) == ["Generator/generation_sol"]
        _assert_expected(results["outputs"]["Generator/generation_sol"])

    def test_compressed_csv_shard(self):
        results = _parse_results_zip(
            _zip(
                {
                    "Generator/generation_sol_s0_p0.csv.gz": gzip.compress(_SHARD_P0.encode()),
                    "Generator/generation_sol_s0_p1.csv.gz": gzip.compress(_SHARD_P1.encode()),
                }
            )
        )
        _assert_expected(results["outputs"]["Generator/generation_sol"])

    def test_hive_partitioned_parquet(self):
        p0 = pd.read_csv(io.StringIO(_SHARD_P0))
        p1 = pd.read_csv(io.StringIO(_SHARD_P1))
        stem = "output/Generator/generation_sol.parquet"
        results = _parse_results_zip(
            _zip(
                {
                    f"{stem}/scene=0/phase=0/part.parquet": _parquet(p0),
                    f"{stem}/scene=0/phase=1/part.parquet": _parquet(p1),
                }
            )
        )
        assert list(results["outputs"]) == ["Generator/generation_sol"]
        _assert_expected(results["outputs"]["Generator/generation_sol"])

    def test_single_long_parquet_file_with_partition_columns(self):
        df = pd.read_csv(io.StringIO(_SHARD_P0 + _SHARD_P1.split("\n", 1)[1]))
        df["scene"] = 0
        df["phase"] = [0, 0, 0, 1, 1, 1]
        results = _parse_results_zip(_zip({"Generator/generation_sol.parquet": _parquet(df)}))
        _assert_expected(results["outputs"]["Generator/generation_sol"])

    def test_explicit_null_value_stays_null(self):
        shard = '"scenario","stage","block","uid","value"\n1,1,1,1,\n1,1,1,2,3\n'
        results = _parse_results_zip(_zip({"Demand/fail_sol_s0_p0.csv": shard}))
        out = results["outputs"]["Demand/fail_sol"]
        assert out["columns"] == ["scenario", "stage", "block", "uid:1", "uid:2"]
        assert out["data"] == [[1, 1, 1, None, 3]]

    def test_upload_route_and_summary_lookup(self, client):
        payload = _zip(
            {
                "output/solution.csv": "obj_value,1.0\nstatus,0\n",
                "output/Generator/generation_sol_s0_p0.csv": _SHARD_P0,
                "output/Generator/generation_sol_s0_p1.csv": _SHARD_P1,
            }
        )
        resp = client.post(
            "/api/results/upload",
            data={"file": (io.BytesIO(payload), "results.zip")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["solution"]["obj_value"] == "1.0"
        _assert_expected(data["outputs"]["Generator/generation_sol"])


class TestLegacyWideLayout:
    def test_single_wide_csv_unchanged(self):
        csv_text = '"scenario","stage","block","uid:1","uid:2"\n1,1,1,10,\n1,1,2,11,4\n'
        results = _parse_results_zip(_zip({"Generator/generation_sol.csv": csv_text}))
        out = results["outputs"]["Generator/generation_sol"]
        assert out["columns"] == ["scenario", "stage", "block", "uid:1", "uid:2"]
        # Legacy CSV rows are passed through verbatim (strings).
        assert out["data"] == [["1", "1", "1", "10", ""], ["1", "1", "2", "11", "4"]]

    def test_single_wide_parquet_unchanged(self):
        df = pd.DataFrame(
            {"scenario": [1, 1], "stage": [1, 1], "block": [1, 2], "uid:1": [1.5, 2.5]}
        )
        results = _parse_results_zip(_zip({"Generator/generation_sol.parquet": _parquet(df)}))
        out = results["outputs"]["Generator/generation_sol"]
        assert out["columns"] == ["scenario", "stage", "block", "uid:1"]
        assert out["data"] == [[1, 1, 1, 1.5], [1, 1, 2, 2.5]]

    def test_wide_shards_are_concatenated(self):
        header = '"scenario","stage","block","uid:1"\n'
        results = _parse_results_zip(
            _zip(
                {
                    "Bus/balance_dual_s0_p0.csv": header + "1,1,1,50\n",
                    "Bus/balance_dual_s0_p1.csv": header + "1,2,2,60\n",
                }
            )
        )
        out = results["outputs"]["Bus/balance_dual"]
        assert out["columns"] == ["scenario", "stage", "block", "uid:1"]
        assert out["data"] == [["1", "1", "1", "50"], ["1", "2", "2", "60"]]
