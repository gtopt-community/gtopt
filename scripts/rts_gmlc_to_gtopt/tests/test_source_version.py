# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Release detection and ``--source-version`` for ``rts_gmlc_to_gtopt``."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

import rts_gmlc_to_gtopt.__main__ as cli
from gtopt_shared.source_versions import format_source_versions
from rts_gmlc_to_gtopt.source_version import (
    MARKER,
    SOURCE_FORMAT,
    detect_version,
    release_tag,
    write_marker,
)


class _FakeResp(io.BytesIO):
    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def test_detect_and_marker(tmp_path: Path) -> None:
    assert detect_version(tmp_path) is None
    assert detect_version(tmp_path / "missing") is None
    (tmp_path / MARKER).write_text("\n")
    assert detect_version(tmp_path) is None
    write_marker(tmp_path, "0.2.3")
    assert detect_version(tmp_path) == "0.2.3"
    assert release_tag("0.2.3") == "v0.2.3"


def test_download_urls_pinned_to_release() -> None:
    assert "/GridMod/RTS-GMLC/v0.2.3/RTS_Data" in cli.GITHUB_RAW_BASE
    src, ts = cli._bases("0.2.3")  # pylint: disable=protected-access
    assert (src, ts) == (cli.GITHUB_RAW_BASE, cli.TIMESERIES_BASE)


def test_populate_fresh_cache_writes_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls: list[str] = []

    def fake_urlopen(url: str) -> _FakeResp:
        urls.append(url)
        return _FakeResp(b"x")

    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    fresh = tmp_path / "fresh"
    cli.populate_cache(fresh, version="0.2.3")
    assert detect_version(fresh) == "0.2.3"
    assert all("/v0.2.3/" in u for u in urls)
    # A pre-filled cache is never stamped (its release is unknown).
    old = tmp_path / "old"
    old.mkdir()
    (old / "gen.csv").write_text("x")
    cli.populate_cache(old)
    assert detect_version(old) is None


def test_help_lists_versions(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", ["rts_gmlc_to_gtopt"])
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert format_source_versions(SOURCE_FORMAT) in out
    assert "Supported: 0.2.3 (tested: 0.2.3; default 0.2.3)" in out


def test_main_override_passed_to_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[str | None] = []

    def fake_populate(_d: Path, timeseries: bool = False, version: str | None = None):
        del timeseries
        seen.append(version)
        raise SystemExit(0)

    monkeypatch.setattr(cli, "populate_cache", fake_populate)
    write_marker(tmp_path, "0.2.2")
    with caplog.at_level(logging.INFO, logger="rts_gmlc_to_gtopt"):
        with pytest.raises(SystemExit):
            cli.main(["--cache-dir", str(tmp_path), "--source-version", "0.2.3"])
    assert seen == ["0.2.3"]
    assert "overrides the version 0.2.2 detected" in caplog.text
