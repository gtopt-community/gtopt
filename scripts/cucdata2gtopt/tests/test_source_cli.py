# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests of the download cache and the ``cucdata2gtopt`` CLI (no network)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from cucdata2gtopt import _source
from cucdata2gtopt.__main__ import main, make_parser

from ._synthetic import make_workbook

NAME = "README.md"


@pytest.fixture
def fake_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> bytes:
    """Point the cache at *tmp_path* and make README.md hash to b'data'."""
    payload = b"data"
    monkeypatch.setenv("GTOPT_CUCDATA_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GTOPT_OFFLINE", raising=False)
    monkeypatch.setitem(_source.MANIFEST, NAME, hashlib.sha256(payload).hexdigest())
    return payload


def test_cache_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GTOPT_CUCDATA_DIR", str(tmp_path))
    assert _source.cache_dir() == tmp_path
    monkeypatch.delenv("GTOPT_CUCDATA_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert _source.cache_dir() == tmp_path / "xdg" / "gtopt" / "cucdata"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert _source.cache_dir() == Path.home() / ".cache" / "gtopt" / "cucdata"


def test_offline_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GTOPT_OFFLINE", "0")
    assert not _source.offline()
    monkeypatch.setenv("GTOPT_OFFLINE", "1")
    assert _source.offline()


def test_fetch_downloads_verifies_and_caches(
    fake_manifest: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls: list[str] = []

    def fake_download(url: str) -> bytes:
        urls.append(url)
        return fake_manifest

    monkeypatch.setattr(_source, "_download", fake_download)
    path = _source.fetch_file(NAME)
    assert path.read_bytes() == fake_manifest
    assert urls == [
        f"https://raw.githubusercontent.com/{_source.UPSTREAM_REPO}/"
        f"{_source.UPSTREAM_COMMIT}/{NAME}"
    ]
    # cached: no second download
    assert _source.fetch_file(NAME) == path
    assert len(urls) == 1
    # force re-downloads
    _source.fetch_file(NAME, force=True)
    assert len(urls) == 2


def test_fetch_rejects_bad_hash(
    fake_manifest: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_source, "_download", lambda url: b"other")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _source.fetch_file(NAME)
    dest = _source.cached_path(NAME)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="cached"):
        _source.fetch_file(NAME)


def test_fetch_offline_and_network_errors(
    fake_manifest: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake_manifest
    monkeypatch.setenv("GTOPT_OFFLINE", "1")
    with pytest.raises(_source.DataUnavailableError, match="GTOPT_OFFLINE"):
        _source.fetch_file(NAME)
    monkeypatch.delenv("GTOPT_OFFLINE")

    def boom(url: str) -> bytes:
        raise OSError("no network")

    monkeypatch.setattr(_source, "_download", boom)
    with pytest.raises(_source.DataUnavailableError, match="no network"):
        _source.fetch_file(NAME)


def test_unknown_names() -> None:
    with pytest.raises(KeyError):
        _source.cached_path("nope.xlsx")
    with pytest.raises(KeyError):
        _source.system_workbook(57)


def test_system_workbook_and_fetch_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[str] = []

    def fake_fetch(name: str, directory: Path | None = None, *, force: bool = False):
        del force
        seen.append(name)
        return (directory or tmp_path) / name

    monkeypatch.setattr(_source, "fetch_file", fake_fetch)
    assert _source.system_workbook(118, tmp_path).name == "CUC_IEEE118-busSystem.xlsx"
    assert len(_source.fetch_all(tmp_path)) == len(_source.MANIFEST)
    assert seen[0] == "CUC_IEEE118-busSystem.xlsx"


def test_download_uses_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resp:
        def __enter__(self) -> "Any":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b"xyz"

    monkeypatch.setattr(_source.urllib.request, "urlopen", lambda url, timeout: Resp())
    assert _source._download("http://example.invalid") == b"xyz"  # noqa: SLF001


def test_parser_defaults() -> None:
    args = make_parser().parse_args([])
    assert args.system == 39
    assert args.reserve is None and args.scale is None
    assert args.formulation == "paper"
    assert args.mode == "chronological"


def test_cli_converts_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wb = make_workbook(tmp_path / "wb.xlsx")
    out = tmp_path / "out"
    rc = main(["--input", str(wb), "--reserve", "5", "-o", str(out), "-v"])
    assert rc == 0
    path = out / "cuc39_r5_s2.json"
    assert capsys.readouterr().out.strip() == str(path)
    case = json.loads(path.read_text())
    assert case["system"]["reserve_zone_array"][0]["urreq"][0][0] == pytest.approx(5.0)


def test_cli_offline_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GTOPT_OFFLINE", "1")
    rc = main(["--cache-dir", str(tmp_path / "empty"), "-o", str(tmp_path)])
    assert rc == 2


def test_cli_fetch_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import cucdata2gtopt.__main__ as cli  # noqa: PLC0415

    monkeypatch.setattr(cli, "fetch_all", lambda directory: [tmp_path / "a"])
    assert main(["--fetch-only", "--cache-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "a")
