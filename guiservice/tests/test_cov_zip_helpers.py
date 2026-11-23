"""Coverage tests for guiservice._zip_helpers edge cases."""

import io
import json
import logging
import sys
import zipfile

import pandas as pd
import pytest

from guiservice import _zip_helpers as zh


def _zip(members: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return buf.getvalue()


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _build_case_json / _data_file_key
# ---------------------------------------------------------------------------


def test_build_case_json_skips_empty_extra_arrays():
    """Empty passthrough arrays are dropped, non-empty ones kept."""
    case = {
        "case_name": "c",
        "system_extra": {"foo_array": [], "bar_array": [{"uid": 1}]},
    }
    out = zh._build_case_json(case)  # pylint: disable=protected-access
    assert "foo_array" not in out["system"]
    assert out["system"]["bar_array"] == [{"uid": 1}]


@pytest.mark.parametrize(
    ("name", "input_dir", "ext", "expected"),
    [
        ("in/demand.csv", "in", ".csv", "demand"),
        ("other/demand.csv", "in", ".csv", "other/demand"),
        ("Gen/pmax.parquet", "", ".parquet", "Gen/pmax"),
    ],
)
def test_data_file_key(name, input_dir, ext, expected):
    """The input_directory prefix is stripped only when it matches."""
    assert zh._data_file_key(name, input_dir, ext) == expected  # pylint: disable=protected-access


# ---------------------------------------------------------------------------
# _parse_uploaded_zip
# ---------------------------------------------------------------------------


def test_parse_uploaded_zip_data_file_edge_cases(caplog):
    """Empty CSV, undecodable CSV and corrupt Parquet are skipped."""
    planning = {"options": {}, "system": {}}
    good = _parquet_bytes(pd.DataFrame({"uid:1": [1.0]}))
    payload = _zip(
        {
            "case.json": json.dumps(planning),
            "empty.csv": "",
            "bad.csv": b"\xff\xfe\xfa",
            "ok.csv": "a,b\n1,2\n",
            "broken.parquet": b"not parquet",
            "Gen/pmax.parquet": good,
        }
    )
    with caplog.at_level(logging.WARNING):
        case = zh._parse_uploaded_zip(payload)  # pylint: disable=protected-access
    files = case["data_files"]
    assert "empty" not in files
    assert "bad" not in files
    assert "broken" not in files
    assert files["ok"] == {"columns": ["a", "b"], "data": [["1", "2"]]}
    assert files["Gen/pmax"]["columns"] == ["uid:1"]
    assert "Failed to parse CSV file bad.csv" in caplog.text
    assert "Failed to parse Parquet file broken.parquet" in caplog.text


# ---------------------------------------------------------------------------
# Solution / output-key helpers
# ---------------------------------------------------------------------------


def test_parse_solution_rows_edge_cases():
    """Blank input and a header-only columnar file both yield {}."""
    parse = zh._parse_solution_rows  # pylint: disable=protected-access
    assert parse([]) == {}
    assert parse([[" ", ""]]) == {}
    assert parse([["scene", "phase", "obj_value"]]) == {}
    assert parse([["obj_value", "5"], ["status", "0"]]) == {"obj_value": "5", "status": "0"}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("", None),
        ("///", None),
        ("Generator/readme.txt", None),
        ("gen.parquet/scene=1/part.parquet", "gen"),
        ("Generator/gen.parquet/scene=1/phase=0/part.parquet", "Generator/gen"),
        ("gen_s1_p0.csv.gz", "gen"),
    ],
)
def test_output_key(name, expected):
    """Top-level and hive layouts map to the expected keys."""
    assert zh._output_key(name) == expected  # pylint: disable=protected-access


def test_decode_csv_member_zstd_roundtrip():
    """.csv.zst members are decompressed when zstandard is available."""
    zstandard = pytest.importorskip("zstandard")
    raw = zstandard.ZstdCompressor().compress(b"a,b\n1,2\n")
    rows = zh._decode_csv_member("x.csv.zst", raw)  # pylint: disable=protected-access
    assert rows == [["a", "b"], ["1", "2"]]


def test_decode_csv_member_zstd_missing(monkeypatch, caplog):
    """Without zstandard the member is skipped with a warning."""
    monkeypatch.setitem(sys.modules, "zstandard", None)
    with caplog.at_level(logging.WARNING):
        assert zh._decode_csv_member("x.csv.zst", b"") is None  # pylint: disable=protected-access
    assert "zstandard" in caplog.text


def test_uid_label_float_and_str():
    """Integral floats print as ints; other values print verbatim."""
    label = zh._uid_label  # pylint: disable=protected-access
    assert label(3.0) == "uid:3"
    assert label(2.5) == "uid:2.5"
    assert label("a") == "uid:a"


# ---------------------------------------------------------------------------
# Long-format merging
# ---------------------------------------------------------------------------


def test_merge_long_without_index_falls_back_to_concat():
    """Long tables with no index column cannot pivot -> plain concat."""
    merge = zh._merge_output_entries  # pylint: disable=protected-access
    out = merge([(["uid", "value"], [[1, 2.0]]), (["uid", "value"], [[2, 3.0]])])
    assert out["columns"] == ["uid", "value"]
    assert out["data"] == [[1, 2.0], [2, 3.0]]


def test_merge_long_keeps_non_numeric_index_labels():
    """A non-numeric scenario label stays a string after pivoting."""
    merge = zh._merge_output_entries  # pylint: disable=protected-access
    cols = ["scenario", "uid", "value"]
    out = merge([(cols, [["wet", "1", "5"]]), (cols, [["dry", "1", "7"]])])
    assert out["columns"] == ["scenario", "uid:1"]
    assert sorted(out["data"]) == [["dry", 7], ["wet", 5]]


# ---------------------------------------------------------------------------
# _parse_results_zip
# ---------------------------------------------------------------------------


def test_parse_results_zip_skips_and_logs(caplog):
    """Directories, blank logs, unknown files, empty and bad members."""
    payload = _zip(
        {
            "Generator/": "",
            "stdout.log": "   \n",
            "stderr.log": "boom\n",
            "gtopt_terminal.log": "first\n",
            "notes.txt": "ignored",
            "Generator/empty.csv": "",
            "Generator/bad.parquet": b"not parquet",
            "Generator/generation_sol.csv": "scenario,uid:1\n1,2\n",
        }
    )
    with caplog.at_level(logging.WARNING):
        res = zh._parse_results_zip(payload)  # pylint: disable=protected-access
    assert "first" in res["terminal_output"]
    assert "boom" in res["terminal_output"]
    assert "\n" in res["terminal_output"].strip()
    assert list(res["outputs"]) == ["Generator/generation_sol"]
    assert "Failed to parse results file Generator/bad.parquet" in caplog.text


def test_parse_results_zip_unreadable_log_is_ignored(monkeypatch):
    """A log member that cannot be read is skipped silently."""
    payload = _zip({"stdout.log": "x", "Bus/b.csv": "uid:1\n1\n"})
    real_read = zipfile.ZipFile.read

    def _read(self, name, pwd=None):
        if name == "stdout.log":
            raise zipfile.BadZipFile("crc")
        return real_read(self, name, pwd)

    monkeypatch.setattr(zipfile.ZipFile, "read", _read)
    res = zh._parse_results_zip(payload)  # pylint: disable=protected-access
    assert res["terminal_output"] == ""
    assert "Bus/b" in res["outputs"]


def test_parse_results_zip_merge_failure_logged(monkeypatch, caplog):
    """A failure while merging one output key is logged, others survive."""
    payload = _zip({"Bus/b.csv": "uid:1\n1\n"})

    def _boom(_entries):
        raise ValueError("merge")

    monkeypatch.setattr(zh, "_merge_output_entries", _boom)
    with caplog.at_level(logging.WARNING):
        res = zh._parse_results_zip(payload)  # pylint: disable=protected-access
    assert not res["outputs"]
    assert "Failed to merge results for Bus/b" in caplog.text
