# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage tests for the ``cen2gtopt.cache`` CLI (no network)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cen2gtopt import cache
from cen2gtopt.daily_update import EndpointResult, RunReport


def test_make_client_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "CEN_USER_KEY",
        "CEN_API_KEY",
        "CEN_OPERACION_KEY",
        "CEN_MERCADOS_KEY",
        "CEN_MEDIDAS_KEY",
        "CEN_INFOTECNICA_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CEN_PLANIFICACION_KEY", "p")
    seen: list[Any] = []
    monkeypatch.setattr(cache, "CenApiClient", seen.append)
    cache._make_client()
    cfg = seen[0]
    assert cfg.user_keys == {"planificacion": "p"}  # no built-in SIP key
    monkeypatch.setenv("CEN_USER_KEY", "mine")
    cache._make_client()
    assert seen[1].user_keys["sip"] == "mine"


def test_fetch_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cache, "_make_client", lambda: "client")
    got: dict[str, Any] = {}

    def _prefetch(client: Any, **kw: Any) -> dict[str, int]:
        got.update(kw, client=client)
        return {"a": 3, "b": 0, "c": -1}

    monkeypatch.setattr(cache, "prefetch_date", _prefetch)
    assert cache.main(["fetch", "--date", "2025-01-01", "--no-cache"]) == 0
    assert got["bypass_cache"] is True
    assert len(got["names"]) == len(cache.ENDPOINTS)
    out = capsys.readouterr().out
    assert "✓" in out and "∅" in out and "✗" in out
    assert cache.main(["fetch", "--date", "2025-01-01", "--endpoints", "cmg_real"]) == 0
    assert got["names"] == ["cmg_real"]


def test_list_and_clear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "root"
    monkeypatch.setattr(cache, "cache_root_default", lambda: root)
    assert cache.main(["list"]) == 0
    assert "cache empty" in capsys.readouterr().out
    (root / "sip").mkdir(parents=True)
    (root / "sip" / "x.parquet").write_bytes(b"12345")
    assert cache.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "1 cached frame(s)" in out
    assert "5b" in out

    got: dict[str, Any] = {}
    monkeypatch.setattr(cache, "clear_cache", lambda **kw: got.update(kw) or 4)
    assert cache.main(["clear", "--service", "sip", "--endpoint", "e"]) == 0
    assert got == {"service": "sip", "endpoint": "e"}
    assert "removed 4" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "status", "rc", "manifest"),
    [
        (["--today", "2025-03-10"], "fetched", 0, True),
        (["--dry-run"], "would_fetch", 0, False),
        ([], "error", 1, True),
    ],
)
def test_daily_update_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    status: str,
    rc: int,
    manifest: bool,
) -> None:
    monkeypatch.setattr(cache, "_make_client", lambda: "client")
    monkeypatch.setattr(cache, "cache_root_default", lambda: tmp_path)
    got: dict[str, Any] = {}

    def _du(client: Any, **kw: Any) -> RunReport:
        got.update(kw)
        rep = RunReport(started_at="2025-03-10T00:00:00", tier=kw["tier"])
        rep.endpoints.append(EndpointResult("cmg_real", "2025-03-09", status))
        return rep

    monkeypatch.setattr(cache, "daily_update", _du)
    full = ["daily-update", "--skip-weekly", "--no-include-pid", "--pid-period", "5"]
    assert cache.main(full + argv) == rc
    assert got["refresh_weekly"] is False
    assert got["include_pid"] is False
    assert got["pid_period_filter"] == 5
    out = capsys.readouterr().out
    assert ("manifest:" in out) is manifest
    if argv and argv[0] == "--today":
        assert str(got["today"]) == "2025-03-10"
    else:
        assert got["today"] is None
