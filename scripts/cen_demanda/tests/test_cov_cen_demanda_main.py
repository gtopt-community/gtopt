# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage for the network fetcher and CLI error path of cen_demanda."""

from __future__ import annotations

import importlib
import io
import json
from pathlib import Path
from urllib.error import URLError

import pytest

_mod = importlib.import_module("cen_demanda.main")


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_default_fetcher_decodes_json(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["accept"] = req.get_header("Accept")
        seen["timeout"] = timeout
        return _FakeResponse(json.dumps({"data": [1, 2]}).encode("utf-8"))

    monkeypatch.setattr(_mod, "urlopen", fake_urlopen)
    assert _mod._default_fetcher("https://x.invalid/api", timeout=3.0) == {
        "data": [1, 2]
    }
    assert seen == {
        "url": "https://x.invalid/api",
        "accept": "application/json",
        "timeout": 3.0,
    }


def test_cli_network_error_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_fetcher(url):
        raise URLError("unreachable")

    monkeypatch.setattr(_mod, "_default_fetcher", failing_fetcher)
    monkeypatch.setattr(_mod.RateLimiter, "wait", lambda self: None)
    rc = _mod.main(
        [
            "--start",
            "2024-01-01",
            "--end",
            "2024-01-01",
            "--output-dir",
            str(tmp_path),
            "--user-key",
            "K",
            "-v",
        ]
    )
    assert rc == 2
