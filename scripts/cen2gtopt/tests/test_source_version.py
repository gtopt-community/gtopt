# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""SIP API version detection and ``--source-version`` for ``cen2gtopt``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from cen2gtopt import _cen_client, _sip_client
from cen2gtopt import main as m
from cen2gtopt._cached_extractor import cache_path
from cen2gtopt.source_version import SOURCE_FORMAT, detect_version
from gtopt_shared.source_versions import format_source_versions


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CEN_USER_KEY", raising=False)
    monkeypatch.delenv("CEN_API_KEY", raising=False)


def _cache(root: Path, endpoint: str, service: str = "sip") -> None:
    path = cache_path(service=service, endpoint=endpoint, params={}, cache_root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_detect_from_cache(tmp_path: Path) -> None:
    assert detect_version(None) is None
    assert detect_version(tmp_path) is None  # no sip/ dir
    (tmp_path / "sip").mkdir()
    (tmp_path / "sip" / "stray.txt").write_text("x")
    (tmp_path / "sip" / "no_version_dir").mkdir()
    _cache(tmp_path, "/unknown-endpoint/v9/findByDate")  # not in the table
    assert detect_version(tmp_path) is None
    # Current table: v4 for cmg, v3 for generacion-real -> "4".
    _cache(tmp_path, "/costo-marginal-real/v4/findByDate")
    _cache(tmp_path, "/generacion-real/v3/findByDate")
    assert detect_version(tmp_path) == "4"
    # An endpoint cached at another version than the table names it.
    _cache(tmp_path, "/demanda-neta/v5/findByDate")
    assert detect_version(tmp_path) == "5"


def test_help_lists_versions(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        m.cli(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert format_source_versions(SOURCE_FORMAT) in out
    assert "Supported: 4 (tested: 4; default 4)" in out


def _base(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--start",
        "2025-01-01",
        "--end",
        "2025-01-02",
        "--out",
        str(tmp_path / "out"),
        *extra,
    ]


def test_manifest_only_records_version(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    _cache(cache, "/demanda-neta/v5/findByDate")
    rc = m.cli(_base(tmp_path, "--manifest-only", "--cache", str(cache)))
    assert rc == m.EXIT_OK
    man = json.loads((tmp_path / "out" / "manifest.json").read_text())
    record = man["source_format"]
    assert record["detected"] == "5"
    assert record["version"] == "4"
    assert "unknown version '5'" in record["warnings"][0]


class _FakeClient:
    def __init__(self, config: Any) -> None:
        self.config = config

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_sip_manifest_records_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_cen_client, "CenApiClient", _FakeClient)
    monkeypatch.setattr(
        _sip_client, "fetch_centrales", lambda **_kw: pd.DataFrame({"a": [1]})
    )
    args = _base(tmp_path, "--api-key", "k", "--include", "flows")
    assert m.cli([*args, "--source-version", "v4"]) == m.EXIT_OK
    man = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert man["source_format"]["version"] == "4"
    assert man["source_format"]["override"] == "v4"
    assert man["source_format"]["warnings"] == []
