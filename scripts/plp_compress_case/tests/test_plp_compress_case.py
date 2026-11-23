# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for plp_compress_case (compress / decompress / split / CLI)."""

from __future__ import annotations

import configparser
import random
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from plp_compress_case import main as pcc
from plp_compress_case._config import (
    default_config_path,
    load_config,
    save_config,
)

SAMPLE_DAT = """\
# plpblo.dat - block definitions
  1     1.0
  2     2.0
  3     3.0
"""

SAMPLE_CSV = """\
year,month,day,hour,block
2026,1,1,1,1
2026,1,1,2,1
2026,1,1,3,2
"""


@pytest.fixture(name="case_dir")
def fixture_case_dir(tmp_path: Path) -> Path:
    d = tmp_path / "test_case"
    d.mkdir()
    return d


@pytest.fixture(name="cfg_path")
def fixture_cfg_path(tmp_path: Path) -> Path:
    """An empty, isolated config file (never read ~/.gtopt.conf)."""
    p = tmp_path / "gtopt.conf"
    p.write_text("")
    return p


def _write_files(d: Path) -> dict[str, str]:
    files = {
        "plpblo.dat": SAMPLE_DAT,
        "plpdem.dat": SAMPLE_DAT * 10,
        "indhor.csv": SAMPLE_CSV,
    }
    for name, content in files.items():
        (d / name).write_text(content)
    return files


def _run_main(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        pcc.main(argv)
    code = exc.value.code
    return 0 if code is None else int(code)


# ── config ──────────────────────────────────────────────────────────────


def test_default_config_path_is_gtopt_conf() -> None:
    assert default_config_path().name == ".gtopt.conf"


def test_load_config_defaults(cfg_path: Path) -> None:
    cfg = load_config(cfg_path)
    assert cfg["codec"] == "xz"
    assert cfg["codec_args"] == "-T0"
    assert cfg["split_mb"] == "10"
    assert "color" in cfg


def test_save_config_roundtrip(cfg_path: Path) -> None:
    save_config(cfg_path, {"color": "never", "codec": "gzip", "split_mb": "3"})
    text = cfg_path.read_text()
    assert "[global]" in text
    assert "[plp_compress_case]" in text
    cfg = load_config(cfg_path)
    assert cfg["codec"] == "gzip"
    assert cfg["split_mb"] == "3"
    assert cfg["color"] == "never"


def test_save_config_only_tool_keys(cfg_path: Path) -> None:
    save_config(cfg_path, {"codec": "zstd"})
    parser = configparser.ConfigParser()
    parser.read(cfg_path)
    assert parser.get("plp_compress_case", "codec") == "zstd"
    assert not parser.has_option("global", "codec")
    assert load_config(cfg_path)["codec"] == "zstd"


def test_save_config_empty_writes_nothing(cfg_path: Path) -> None:
    save_config(cfg_path, {})
    assert cfg_path.read_text() == ""


# ── helpers ─────────────────────────────────────────────────────────────


def test_find_data_files_filters(case_dir: Path) -> None:
    (case_dir / "sub.dat").mkdir()  # directory, skipped
    (case_dir / "a.dat").write_text("x")
    (case_dir / "b.CSV").write_text("x")
    (case_dir / "c.prn").write_text("x")
    (case_dir / "d.png").write_bytes(b"x")
    (case_dir / "e.txt").write_text("x")  # not a data ext
    (case_dir / "f.dat.xz").write_bytes(b"x")  # compressed
    names = [f.name for f in pcc._find_data_files(case_dir)]
    assert names == ["a.dat", "b.CSV", "c.prn", "d.png"]


def test_find_compressed_files(case_dir: Path) -> None:
    (case_dir / "a.dat.xz").write_bytes(b"x")
    (case_dir / "b.dat.gz").write_bytes(b"x")
    (case_dir / "dir.xz").mkdir()
    assert [f.name for f in pcc._find_compressed_files(case_dir, ".xz")] == ["a.dat.xz"]


def test_codec_ext_known_and_unknown() -> None:
    assert pcc._codec_ext("xz") == ".xz"
    assert pcc._codec_ext("zstd") == ".zst"
    assert pcc._codec_ext("brotli") == ".brotli"


def test_decompress_file_unknown_extension(tmp_path: Path) -> None:
    src = tmp_path / "a.dat.rar"
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="Unknown compressed extension"):
        pcc._decompress_file(src, tmp_path / "a.dat")


# ── CLI: compress / decompress roundtrip ────────────────────────────────


def test_compress_creates_xz_files(case_dir: Path, cfg_path: Path, capsys) -> None:
    _write_files(case_dir)
    assert _run_main([str(case_dir), "--config", str(cfg_path)]) == 0
    assert not (case_dir / "plpblo.dat").exists()
    for name in ("plpblo.dat.xz", "plpdem.dat.xz", "indhor.csv.xz"):
        assert (case_dir / name).exists()
    # no leftover temp dirs
    assert not [p for p in case_dir.iterdir() if p.is_dir()]
    out = capsys.readouterr().out
    assert "Compressing 3 file(s)" in out
    assert "Files are ready to commit" in out


@pytest.mark.parametrize(
    ("codec", "ext"),
    [("xz", ".xz"), ("gzip", ".gz"), ("bzip2", ".bz2"), ("zstd", ".zst")],
)
def test_roundtrip_codecs(case_dir: Path, cfg_path: Path, codec: str, ext: str) -> None:
    if shutil.which(codec) is None:
        pytest.skip(f"{codec} not installed")
    original = _write_files(case_dir)
    argv = [str(case_dir), "--config", str(cfg_path), "--codec", codec]
    argv += ["--codec-args", ""]
    assert _run_main(argv) == 0
    assert sorted(p.suffix for p in case_dir.iterdir()) == [ext] * 3
    assert _run_main(["-d", str(case_dir), "--config", str(cfg_path)]) == 0
    for name, content in original.items():
        assert (case_dir / name).read_text() == content
    assert sorted(p.name for p in case_dir.iterdir()) == sorted(original)


def test_codec_from_config(case_dir: Path, cfg_path: Path) -> None:
    save_config(cfg_path, {"codec": "gzip", "codec_args": "-1", "split_mb": "5"})
    _write_files(case_dir)
    assert _run_main([str(case_dir), "--config", str(cfg_path)]) == 0
    assert (case_dir / "plpblo.dat.gz").exists()


def test_no_files_exits_cleanly(case_dir: Path, cfg_path: Path, capsys) -> None:
    assert _run_main([str(case_dir), "--config", str(cfg_path)]) == 0
    assert "No .dat/.csv/.prn/.png files found" in capsys.readouterr().out


def test_decompress_empty_dir(case_dir: Path, cfg_path: Path, capsys) -> None:
    assert _run_main(["-d", str(case_dir), "--config", str(cfg_path)]) == 0
    assert "No .xz or data files found" in capsys.readouterr().out


def test_already_compressed_detected(case_dir: Path, cfg_path: Path, capsys) -> None:
    _write_files(case_dir)
    _run_main([str(case_dir), "--config", str(cfg_path)])
    capsys.readouterr()
    assert _run_main([str(case_dir), "--config", str(cfg_path)]) == 0
    assert "already compressed (3 .xz file(s))" in capsys.readouterr().out


def test_already_decompressed_detected(case_dir: Path, cfg_path: Path, capsys) -> None:
    _write_files(case_dir)
    assert _run_main(["-d", str(case_dir), "--config", str(cfg_path)]) == 0
    assert "already decompressed (3 data file(s))" in capsys.readouterr().out


def test_invalid_dir_fails(tmp_path: Path, cfg_path: Path, capsys) -> None:
    rc = _run_main([str(tmp_path / "missing"), "--config", str(cfg_path)])
    assert rc == 1
    assert "is not a directory" in capsys.readouterr().err


def test_no_directory_prints_help(cfg_path: Path, capsys) -> None:
    assert _run_main(["--config", str(cfg_path)]) == 0
    assert "usage: plp_compress_case" in capsys.readouterr().out


def test_show_config(cfg_path: Path, capsys) -> None:
    save_config(cfg_path, {"codec": "zstd"})
    assert _run_main(["--show-config", "--config", str(cfg_path)]) == 0
    out = capsys.readouterr().out
    assert "codec = zstd" in out
    assert "split_mb = 10" in out


def test_main_uses_default_config_path(
    monkeypatch: pytest.MonkeyPatch, cfg_path: Path, capsys
) -> None:
    monkeypatch.setattr(pcc, "default_config_path", lambda: cfg_path)
    assert _run_main(["--show-config"]) == 0
    assert "codec = xz" in capsys.readouterr().out


def test_module_entry_point(
    monkeypatch: pytest.MonkeyPatch, cfg_path: Path, capsys
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["plp_compress_case", "--show-config", "--config", str(cfg_path)]
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("plp_compress_case", run_name="__main__")
    assert exc.value.code == 0
    assert "codec = xz" in capsys.readouterr().out


# ── failure handling: originals stay intact ─────────────────────────────


def test_compress_failure_keeps_originals(case_dir: Path) -> None:
    original = _write_files(case_dir)
    with pytest.raises(subprocess.CalledProcessError):
        pcc.compress_case(case_dir, "false", [], 10)
    assert sorted(p.name for p in case_dir.iterdir()) == sorted(original)


def test_decompress_failure_keeps_compressed(case_dir: Path) -> None:
    (case_dir / "bad.dat.xz").write_bytes(b"not an xz stream")
    with pytest.raises(subprocess.CalledProcessError):
        pcc.decompress_case(case_dir)
    assert [p.name for p in case_dir.iterdir()] == ["bad.dat.xz"]


# ── split ───────────────────────────────────────────────────────────────


def _incompressible(n_bytes: int) -> bytes:
    return random.Random(0).randbytes(n_bytes)


def test_large_file_splits_and_rejoins(case_dir: Path, cfg_path: Path, capsys) -> None:
    # ~1.6 MB of random bytes stays > 1 MB after xz -> split at 1 MB.
    data = _incompressible(1_600_000)
    (case_dir / "big.dat").write_bytes(data)
    (case_dir / "small.dat").write_text(SAMPLE_DAT)
    argv = [str(case_dir), "--config", str(cfg_path), "--split-mb", "1"]
    assert _run_main(argv) == 0
    parts = sorted(p.name for p in case_dir.glob("big.dat.*.xz"))
    assert len(parts) >= 2
    assert all(p.stat().st_size <= 1024 * 1024 for p in case_dir.glob("*.xz"))
    assert (case_dir / "small.dat.xz").exists()
    assert "splitting..." in capsys.readouterr().out

    assert _run_main(["-d", str(case_dir), "--config", str(cfg_path)]) == 0
    assert (case_dir / "big.dat").read_bytes() == data
    assert (case_dir / "small.dat").read_text() == SAMPLE_DAT
    out = capsys.readouterr().out
    assert "reassembling split parts" in out
    assert sorted(p.name for p in case_dir.iterdir()) == ["big.dat", "small.dat"]


def test_decompress_orders_parts_numerically(case_dir: Path) -> None:
    # Ten parts: lexical order (1, 10, 2, ...) would scramble the content.
    chunks = [f"line {i}\n".encode() for i in range(1, 11)]
    for i, chunk in enumerate(chunks, start=1):
        raw = case_dir / f"f.dat.{i}"
        raw.write_bytes(chunk)
        pcc._compress_file(raw, case_dir / f"f.dat.{i}.xz", "xz", [])
        raw.unlink()
    assert pcc.decompress_case(case_dir) == 0
    assert (case_dir / "f.dat").read_bytes() == b"".join(chunks)


def test_split_mb_zero_disables_split(case_dir: Path) -> None:
    (case_dir / "a.dat").write_text(SAMPLE_DAT * 50)
    assert pcc.compress_case(case_dir, "xz", ["-T0"], 0) == 0
    assert [p.name for p in case_dir.iterdir()] == ["a.dat.xz"]
