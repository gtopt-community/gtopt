# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for opendss2gtopt.feeders (download helper, no network)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from opendss2gtopt import feeders


def test_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GTOPT_OPENDSS_FEEDERS", str(tmp_path))
    assert feeders.cache_dir() == tmp_path
    monkeypatch.delenv("GTOPT_OPENDSS_FEEDERS")
    assert feeders.cache_dir().name == "opendss-feeders"


def test_unknown_feeder() -> None:
    with pytest.raises(KeyError):
        feeders.feeder_path("ieee9999")


def test_offline_and_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GTOPT_OPENDSS_FEEDERS", str(tmp_path))
    monkeypatch.setenv("GTOPT_OFFLINE", "1")
    assert feeders.feeder_path("ieee4") is None
    assert feeders.feeder_path("ieee4", download=False) is None
    monkeypatch.delenv("GTOPT_OFFLINE")
    urls: list[str] = []

    def fake_urlopen(url: str, timeout: float = 0):
        del timeout
        urls.append(url)
        return io.BytesIO(b"! stub\n")

    monkeypatch.setattr(feeders.urllib.request, "urlopen", fake_urlopen)
    path = feeders.feeder_path("ieee4")
    assert path is not None and path.is_file()
    assert any(u.endswith("4Bus-YY-Bal/4Bus-YY-Bal.DSS") for u in urls)
    # cached now: no further downloads
    urls.clear()
    assert feeders.feeder_path("ieee4") == path and not urls


def test_download_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GTOPT_OPENDSS_FEEDERS", str(tmp_path))
    monkeypatch.delenv("GTOPT_OFFLINE", raising=False)

    def boom(url: str, timeout: float = 0):
        raise OSError("no network")

    monkeypatch.setattr(feeders.urllib.request, "urlopen", boom)
    assert feeders.feeder_path("ieee13") is None
