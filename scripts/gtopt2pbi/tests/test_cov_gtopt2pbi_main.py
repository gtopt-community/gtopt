"""Extra coverage for gtopt2pbi: codec selection, CSV relayout, CLI."""

import importlib
import runpy
import sys
import types

import pandas as pd
import pyarrow.parquet as pq
import pytest

from gtopt2pbi.main import _codec_kwargs, main, relayout_tree

pbi = importlib.import_module("gtopt2pbi.main")


@pytest.mark.parametrize("codec", ["none", "", "UNCOMPRESSED", None])
def test_codec_kwargs_uncompressed(codec):
    assert not _codec_kwargs(codec, 5)


def test_codec_kwargs_level_and_fallback(monkeypatch, capsys):
    assert _codec_kwargs("zstd", 3) == {"compression": "zstd", "compression_level": 3}
    assert _codec_kwargs("zstd", None) == {"compression": "zstd"}

    fake_codec = types.SimpleNamespace(is_available=lambda codec: False)
    monkeypatch.setattr(pbi, "pa", types.SimpleNamespace(Codec=fake_codec))
    assert _codec_kwargs("lz4", None) == {"compression": "gzip"}
    assert "falling back to 'gzip'" in capsys.readouterr().err


def test_relayout_skips_unreadable_parquet(tmp_path, capsys):
    bad = tmp_path / "broken.parquet"
    bad.write_bytes(b"not a parquet file")
    assert relayout_tree(tmp_path) == 0
    assert "skip (unreadable parquet)" in capsys.readouterr().err
    assert bad.read_bytes() == b"not a parquet file"


def test_relayout_ignores_directories_named_parquet(tmp_path):
    """A directory matching *.parquet is not a file and is skipped."""
    (tmp_path / "dir.parquet").mkdir()
    (tmp_path / "dir.csv").mkdir()
    assert relayout_tree(tmp_path) == 0


def test_relayout_csv_wide_dry_run_and_write(tmp_path, capsys):
    wide = tmp_path / "Demand" / "lmax.csv"
    wide.parent.mkdir()
    wide.write_text("block,uid:1,uid:2\n1,10.0,20.0\n2,11.0,21.0\n")
    structural = tmp_path / "block.csv"
    structural.write_text("block,duration\n1,1.0\n")
    unreadable = tmp_path / "empty.csv"
    unreadable.write_text("")  # pandas raises EmptyDataError (a ValueError)
    hive = tmp_path / "out.parquet" / "scene=1" / "x.csv"
    hive.parent.mkdir(parents=True)
    hive.write_text("block,uid:1\n1,5.0\n")
    hive_before = hive.read_text()

    before = wide.read_text()
    assert relayout_tree(tmp_path, dry_run=True) == 1
    assert "[dry-run] reshape (csv)" in capsys.readouterr().out
    assert wide.read_text() == before

    assert relayout_tree(tmp_path) == 1
    out = pd.read_csv(wide)
    assert set(out.columns) == {"block", "uid", "value"}
    assert len(out) == 4
    assert structural.read_text() == "block,duration\n1,1.0\n"
    assert hive.read_text() == hive_before


def test_main_dry_run_and_level(tmp_path, capsys):
    f = tmp_path / "Gen" / "pmax.parquet"
    f.parent.mkdir()
    pd.DataFrame({"block": [1], "uid:3": [1.0]}).to_parquet(
        f, index=False, compression="snappy"
    )
    assert main(["-n", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "[dry-run] gtopt2pbi:" in out
    assert "would convert 1" in out
    assert pq.ParquetFile(f).metadata.row_group(0).column(0).compression == "SNAPPY"

    assert main(["--compression-level", "5", str(tmp_path)]) == 0
    assert pq.ParquetFile(f).metadata.row_group(0).column(0).compression == "ZSTD"


def test_module_entry_point(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gtopt2pbi", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("gtopt2pbi", run_name="__main__")
    assert exc.value.code == 0


def test_codec_kwargs_unknown_codec_falls_back(capsys):
    """Regression: an unknown codec name used to raise ValueError from Arrow."""
    assert _codec_kwargs("bogus", 4) == {"compression": "gzip", "compression_level": 4}
    assert "codec 'bogus' unavailable" in capsys.readouterr().err
