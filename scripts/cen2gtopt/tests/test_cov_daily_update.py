# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage tests for :mod:`cen2gtopt.daily_update` (no network)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from cen2gtopt import daily_update as du
from cen2gtopt import pcp_archive
from cen2gtopt._cached_extractor import ENDPOINTS, cache_path

TODAY = date(2025, 3, 10)
#: The fetch layer is faked, so no real client is needed.
NO_CLIENT: Any = None


def _touch_cache(root: Path, name: str, day: str, age_s: float = 0.0) -> Path:
    p = cache_path(
        service=ENDPOINTS[name]["service"],
        endpoint=ENDPOINTS[name]["path"],
        params={"startDate": day, "endDate": day},
        cache_root=root,
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    if age_s:
        t = time.time() - age_s
        os.utime(p, (t, t))
    return p


@pytest.fixture
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, bool]]:
    calls: list[tuple[str, str, bool]] = []

    def _fake(
        _client: Any, name: str, *, start: str, cache_root: Path, bypass_cache: bool
    ) -> pd.DataFrame:
        calls.append((name, start, bypass_cache))
        if name == "instrucciones_sscc":
            raise RuntimeError("upstream down")
        return pd.DataFrame({"a": [1, 2, 3]})

    monkeypatch.setattr(du, "fetch_by_name", _fake)
    return calls


def test_date_helpers() -> None:
    assert du._settled_window(TODAY, 2) == ["2025-03-08", "2025-03-09"]
    assert du._day_ahead_window(TODAY) == ["2025-03-09", "2025-03-10"]
    assert not du._date_range(TODAY, date(2025, 3, 9))
    assert isinstance(du._today(), date)


def test_is_fresh(tmp_path: Path) -> None:
    assert not du._is_fresh("cmg_real", "2025-03-09", tmp_path)
    _touch_cache(tmp_path, "cmg_real", "2025-03-09")
    assert du._is_fresh("cmg_real", "2025-03-09", tmp_path)
    _touch_cache(tmp_path, "cmg_online", "2025-03-09", age_s=2 * 86400)
    assert not du._is_fresh("cmg_online", "2025-03-09", tmp_path)


def test_fetch_one_paths(tmp_path: Path, fake_fetch: list) -> None:
    _touch_cache(tmp_path, "cmg_real", "2025-03-09")
    r = du._fetch_one(
        NO_CLIENT, "cmg_real", "2025-03-09", tmp_path, force=False, dry_run=False
    )
    assert r.status == "skipped_fresh"
    r = du._fetch_one(
        NO_CLIENT, "cmg_real", "2025-03-08", tmp_path, force=False, dry_run=True
    )
    assert r.status == "would_fetch"
    r = du._fetch_one(
        NO_CLIENT, "cmg_real", "2025-03-09", tmp_path, force=True, dry_run=False
    )
    assert (r.status, r.rows) == ("fetched", 3)
    assert fake_fetch == [("cmg_real", "2025-03-09", True)]
    r = du._fetch_one(
        NO_CLIENT,
        "instrucciones_sscc",
        "2025-03-09",
        tmp_path,
        force=False,
        dry_run=False,
    )
    assert r.status == "error"
    assert "RuntimeError: upstream down" in r.error


def test_daily_update_unknown_tier() -> None:
    with pytest.raises(ValueError, match="unknown tier"):
        du.daily_update(NO_CLIENT, tier="bogus")


def test_daily_update_full_run(
    tmp_path: Path, fake_fetch: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(du, "refresh_scvic_if_month_rolled", lambda t, force: "ok")
    seen: dict[str, Any] = {}

    def _pcp(today: date, **kw: Any) -> dict[str, int]:
        seen.update(kw, today=today)
        return {"downloaded": 1, "skipped": 2, "would_download": 0, "error": 0}

    monkeypatch.setattr(du, "refresh_pcp_archive", _pcp)
    rep = du.daily_update(
        NO_CLIENT,
        tier="full",
        lookback_days=1,
        today=TODAY,
        cache_root=tmp_path,
        include_pid=True,
        pid_period_filter=20,
    )
    by = {(r.endpoint, r.target_date): r.status for r in rep.endpoints}
    assert by[("cmg_online", "2025-03-10")] == "fetched"
    assert by[("cmg_real", "2025-03-09")] == "fetched"
    assert by[("instrucciones_sscc", "2025-03-09")] == "error"
    assert by[("centrales", "2025-03-10")] == "fetched"
    assert rep.weekly_catalog_status == "checked"
    assert rep.scvic_status == "ok"
    assert rep.pcp_archive_status.startswith("downloaded=1 skipped=2")
    assert seen["pid_period_filter"] == 20
    text = du.summarise(rep)
    assert "errors=1" in text
    assert "cmg_online" in text
    out = rep.write(tmp_path / "man")
    assert json.loads(out.read_text())["tier"] == "full"


def test_daily_update_dry_run_and_skips(tmp_path: Path, fake_fetch: list) -> None:
    _touch_cache(tmp_path, "cmg_real", "2025-03-09")
    rep = du.daily_update(
        NO_CLIENT,
        lookback_days=1,
        today=TODAY,
        cache_root=tmp_path,
        dry_run=True,
        refresh_weekly=False,
    )
    assert not fake_fetch
    assert rep.weekly_catalog_status == "skipped"
    assert rep.scvic_status == "dry_run"
    assert rep.pcp_archive_status == "skipped"
    statuses = {r.status for r in rep.endpoints}
    assert statuses == {"would_fetch", "skipped_fresh"}
    text = du.summarise(rep)
    assert "would_fetch=" in text

    rep2 = du.daily_update(
        NO_CLIENT,
        lookback_days=0,
        today=TODAY,
        cache_root=tmp_path,
        dry_run=True,
        refresh_scvic=False,
    )
    assert rep2.scvic_status == "skipped"
    # Unknown status values are still counted in the summary.
    rep2.endpoints.append(du.EndpointResult("x", "d", "weird"))
    assert "x " in du.summarise(rep2)


def test_refresh_scvic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(du, "cv_rpt_for_date", lambda d: pd.DataFrame({"a": [1]}))
    assert du.refresh_scvic_if_month_rolled(TODAY).startswith("refreshed (1 rows")
    f = tmp_path / ".cache" / "gtopt" / "cen2gtopt" / "scvic" / "cv_rpt_2025_03.parquet"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    assert du.refresh_scvic_if_month_rolled(TODAY) == "already_cached"

    def _boom(_d: str) -> pd.DataFrame:
        raise OSError("net")

    monkeypatch.setattr(du, "cv_rpt_for_date", _boom)
    assert du.refresh_scvic_if_month_rolled(TODAY, force=True) == "error: net"


@dataclass
class _F:
    nombre: str
    categoria: str = "PID"


def test_refresh_pcp_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = [
        _F("PLEXOS20250309.zip", "PCP"),
        _F("PID_20250309_20.zip"),
        _F("PID_20250309_21.zip"),
        _F("PID_20250308_01.zip"),
        _F("PID_20250101_01.zip"),
        _F("OTHER.zip"),
    ]
    downloaded: list[str] = []

    def _dl(f: _F, *, output_dir: Path, skip_existing: bool) -> None:
        if f.nombre.endswith("_01.zip"):
            raise OSError("boom")
        downloaded.append(f.nombre)

    monkeypatch.setattr(pcp_archive, "fetch_index", lambda: files)
    monkeypatch.setattr(pcp_archive, "download_one", _dl)
    monkeypatch.setattr(pcp_archive, "DEFAULT_DOWNLOAD_ROOT", tmp_path)

    none = du.refresh_pcp_archive(TODAY, include_pid=False)
    assert none == {"would_download": 0, "downloaded": 0, "skipped": 0, "error": 0}

    (tmp_path / "PID").mkdir()
    (tmp_path / "PID" / "PID_20250309_21.zip").write_bytes(b"x")
    c = du.refresh_pcp_archive(TODAY, lookback_days=2, include_pcp=True)
    assert c == {"would_download": 0, "downloaded": 2, "skipped": 1, "error": 1}
    assert downloaded == ["PLEXOS20250309.zip", "PID_20250309_20.zip"]

    c = du.refresh_pcp_archive(
        TODAY, lookback_days=2, pid_period_filter=20, dry_run=True
    )
    assert c["would_download"] == 1

    c = du.refresh_pcp_archive(
        TODAY, lookback_days=1, pid_period_filter=21, force=True, dry_run=True
    )
    assert c["would_download"] == 1

    c = du.refresh_pcp_archive(
        TODAY, lookback_days=1, include_pcp=True, include_pid=False, dry_run=True
    )
    assert c["would_download"] == 1
