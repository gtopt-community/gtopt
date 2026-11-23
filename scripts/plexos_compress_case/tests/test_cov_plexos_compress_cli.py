# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI, config and error-path tests for plexos_compress_case."""

from __future__ import annotations

import configparser
import runpy
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from plexos_compress_case import main as pxc
from plexos_compress_case._config import (
    default_config_path,
    load_config,
    save_config,
)


def _make_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _make_wrapper(directory: Path, date: str = "20260101") -> dict[str, bytes]:
    """Build PLEXOS{date}.zip wrapping DATOS/RES; return inner zip bytes."""
    datos = directory / f"DATOS{date}.zip"
    res = directory / f"RES{date}.zip"
    _make_zip(datos, {"Gen.csv": b"g,1\n" * 50})
    _make_zip(res, {"log.txt": b"optimal\n" * 50})
    inner = {datos.name: datos.read_bytes(), res.name: res.read_bytes()}
    with zipfile.ZipFile(
        directory / f"PLEXOS{date}.zip", "w", compression=zipfile.ZIP_STORED
    ) as zf:
        zf.write(datos, arcname=datos.name)
        zf.write(res, arcname=res.name)
    datos.unlink()
    res.unlink()
    return inner


@pytest.fixture(name="cfg_path")
def fixture_cfg_path(tmp_path: Path) -> Path:
    p = tmp_path / "gtopt.conf"
    p.write_text("")
    return p


@pytest.fixture(name="bundle")
def fixture_bundle(tmp_path: Path) -> Path:
    d = tmp_path / "bundle"
    d.mkdir()
    return d


def _run_main(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        pxc.main(argv)
    code = exc.value.code
    return 0 if code is None else int(code)


# ── config ──────────────────────────────────────────────────────────────


def test_default_config_path() -> None:
    assert default_config_path().name == ".gtopt.conf"


def test_load_config_defaults(cfg_path: Path) -> None:
    cfg = load_config(cfg_path)
    assert cfg["codec"] == "xz"
    assert cfg["codec_args"] == "-T0 -9"
    assert cfg["unwrap"] == "true"
    assert "color" in cfg


def test_save_config_splits_global_and_tool(cfg_path: Path) -> None:
    save_config(cfg_path, {"color": "never", "unwrap": "no"})
    parser = configparser.ConfigParser()
    parser.read(cfg_path)
    assert parser.get("global", "color") == "never"
    assert parser.get("plexos_compress_case", "unwrap") == "no"
    assert load_config(cfg_path)["unwrap"] == "no"


def test_save_config_only_global(cfg_path: Path) -> None:
    save_config(cfg_path, {"color": "always"})
    parser = configparser.ConfigParser()
    parser.read(cfg_path)
    assert not parser.has_section("plexos_compress_case")


def test_save_config_empty(cfg_path: Path) -> None:
    save_config(cfg_path, {})
    assert cfg_path.read_text() == ""


# ── helpers ─────────────────────────────────────────────────────────────


def test_find_data_files_filters(bundle: Path) -> None:
    (bundle / "dir.zip").mkdir()
    (bundle / "a.zip").write_bytes(b"x")
    (bundle / "b.ZIP").write_bytes(b"x")
    (bundle / "c.zip.xz").write_bytes(b"x")
    (bundle / "d.csv").write_bytes(b"x")
    assert [f.name for f in pxc._find_data_files(bundle)] == ["a.zip", "b.ZIP"]


def test_codec_ext() -> None:
    assert pxc._codec_ext("gzip") == ".gz"
    assert pxc._codec_ext("brotli") == ".brotli"


def test_decompress_file_unknown_extension(tmp_path: Path) -> None:
    src = tmp_path / "a.zip.rar"
    src.write_bytes(b"x")
    with pytest.raises(ValueError, match="Unknown compressed extension"):
        pxc._decompress_file(src, tmp_path / "a.zip")


def test_wrapper_with_three_inner_zips_is_not_unwrapped(bundle: Path) -> None:
    outer = bundle / "PLEXOS20260101.zip"
    _make_zip(outer, {"A.zip": b"1", "B.zip": b"2", "C.zip": b"3"})
    assert pxc._is_outer_wrapper(outer) is False


# ── compress / decompress via CLI ───────────────────────────────────────


def test_cli_unwrap_roundtrip_with_rewrap(bundle: Path, cfg_path: Path, capsys) -> None:
    inner = _make_wrapper(bundle)
    _make_zip(bundle / "other.zip", {"x.csv": b"1\n"})
    other = (bundle / "other.zip").read_bytes()

    rc = _run_main([str(bundle), "--config", str(cfg_path), "--codec-args", "-0"])
    assert rc == 0
    names = sorted(p.name for p in bundle.iterdir())
    assert names == ["DATOS20260101.zip.xz", "RES20260101.zip.xz", "other.zip.xz"]
    out = capsys.readouterr().out
    assert "unwrap=yes" in out
    assert "unwrapped" in out

    rc = _run_main(["-d", "--rewrap", str(bundle), "--config", str(cfg_path)])
    assert rc == 0
    names = sorted(p.name for p in bundle.iterdir())
    assert names == ["PLEXOS20260101.zip", "other.zip"]
    assert (bundle / "other.zip").read_bytes() == other
    with zipfile.ZipFile(bundle / "PLEXOS20260101.zip") as zf:
        assert sorted(zf.namelist()) == sorted(inner)
        for name, data in inner.items():
            assert zf.read(name) == data
    assert "rewrapped from" in capsys.readouterr().out


def test_cli_decompress_without_rewrap_keeps_inner(
    bundle: Path, cfg_path: Path
) -> None:
    inner = _make_wrapper(bundle)
    assert _run_main([str(bundle), "--config", str(cfg_path), "--unwrap"]) == 0
    assert _run_main(["-d", str(bundle), "--config", str(cfg_path)]) == 0
    for name, data in inner.items():
        assert (bundle / name).read_bytes() == data


def test_cli_rewrap_without_pairs(bundle: Path, cfg_path: Path) -> None:
    _make_zip(bundle / "DATOS20260101.zip", {"a": b"1"})  # no RES partner
    assert _run_main([str(bundle), "--config", str(cfg_path)]) == 0
    assert _run_main(["-d", "--rewrap", str(bundle), "--config", str(cfg_path)]) == 0
    assert [p.name for p in bundle.iterdir()] == ["DATOS20260101.zip"]


@pytest.mark.parametrize("value", ["false", "No", " off ", "0"])
def test_cli_unwrap_disabled_by_config(
    bundle: Path, cfg_path: Path, value: str, capsys
) -> None:
    save_config(cfg_path, {"unwrap": value})
    wrapper = bundle / "PLEXOS20260101.zip"
    _make_wrapper(bundle)
    original = wrapper.read_bytes()
    assert _run_main([str(bundle), "--config", str(cfg_path)]) == 0
    assert [p.name for p in bundle.iterdir()] == ["PLEXOS20260101.zip.xz"]
    assert "unwrap=no" in capsys.readouterr().out
    assert _run_main(["-d", str(bundle), "--config", str(cfg_path)]) == 0
    assert wrapper.read_bytes() == original


def test_cli_codec_and_empty_args(bundle: Path, cfg_path: Path) -> None:
    _make_zip(bundle / "a.zip", {"a": b"1"})
    argv = [str(bundle), "--config", str(cfg_path), "--codec", "gzip"]
    assert _run_main([*argv, "--codec-args", ""]) == 0
    assert [p.name for p in bundle.iterdir()] == ["a.zip.gz"]
    assert _run_main(["-d", str(bundle), "--config", str(cfg_path)]) == 0
    assert [p.name for p in bundle.iterdir()] == ["a.zip"]


def test_cli_already_decompressed(bundle: Path, cfg_path: Path, capsys) -> None:
    _make_zip(bundle / "a.zip", {"a": b"1"})
    assert _run_main(["-d", str(bundle), "--config", str(cfg_path)]) == 0
    assert "already decompressed (1 .zip file(s))" in capsys.readouterr().out


def test_cli_show_config(cfg_path: Path, capsys) -> None:
    assert _run_main(["--show-config", "--config", str(cfg_path)]) == 0
    out = capsys.readouterr().out
    assert "codec = xz" in out
    assert "unwrap = true" in out


def test_cli_default_config_path(
    monkeypatch: pytest.MonkeyPatch, cfg_path: Path, capsys
) -> None:
    monkeypatch.setattr(pxc, "default_config_path", lambda: cfg_path)
    assert _run_main(["--show-config"]) == 0
    assert "codec_args = -T0 -9" in capsys.readouterr().out


def test_cli_no_directory_prints_help(cfg_path: Path, capsys) -> None:
    assert _run_main(["--config", str(cfg_path)]) == 0
    assert "usage: plexos_compress_case" in capsys.readouterr().out


def test_cli_invalid_directory(tmp_path: Path, cfg_path: Path, capsys) -> None:
    assert _run_main([str(tmp_path / "nope"), "--config", str(cfg_path)]) == 1
    assert "is not a directory" in capsys.readouterr().err


def test_cli_unwrap_flags_are_exclusive(cfg_path: Path) -> None:
    assert _run_main(["--unwrap", "--no-unwrap", "--config", str(cfg_path)]) == 2


def test_module_entry_point(
    monkeypatch: pytest.MonkeyPatch, cfg_path: Path, capsys
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["plexos_compress_case", "--show-config", "--config", str(cfg_path)],
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("plexos_compress_case", run_name="__main__")
    assert exc.value.code == 0
    assert "codec = xz" in capsys.readouterr().out


# ── failure cleanup ─────────────────────────────────────────────────────


def test_compress_failure_keeps_originals(bundle: Path) -> None:
    _make_wrapper(bundle)
    before = {p.name: p.read_bytes() for p in bundle.iterdir()}
    with pytest.raises(subprocess.CalledProcessError):
        pxc.compress_case(bundle, "false", [], unwrap=True)
    assert {p.name: p.read_bytes() for p in bundle.iterdir()} == before


def test_decompress_failure_keeps_compressed(bundle: Path) -> None:
    bad = bundle / "a.zip.xz"
    bad.write_bytes(b"not xz")
    with pytest.raises(subprocess.CalledProcessError):
        pxc.decompress_case(bundle, rewrap=False)
    assert [p.name for p in bundle.iterdir()] == ["a.zip.xz"]
