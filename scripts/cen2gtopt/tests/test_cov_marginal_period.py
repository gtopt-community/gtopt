# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage tests for :mod:`cen2gtopt.marginal_period` (no network).

The CEN client, the per-day compute and the process pool are replaced
by in-process fakes.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

import pandas as pd
import pytest

from cen2gtopt import marginal_period as mp_mod
from cen2gtopt.marginal_units import REF_BUSES


def _ns(**kw: Any) -> argparse.Namespace:
    base = {
        "start_date": None,
        "end_date": None,
        "year": None,
        "month": None,
        "last_n_days": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def test_parse_period_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    assert mp_mod.parse_period(_ns(start_date="2025-01-02", end_date="2025-01-03")) == (
        date(2025, 1, 2),
        date(2025, 1, 3),
    )
    assert mp_mod.parse_period(_ns(year=2024)) == (date(2024, 1, 1), date(2024, 12, 31))
    assert mp_mod.parse_period(_ns(month="2024-02")) == (
        date(2024, 2, 1),
        date(2024, 2, 29),
    )
    assert mp_mod.parse_period(_ns(month="2024-12"))[1] == date(2024, 12, 31)
    monkeypatch.setattr(mp_mod, "_today_clt", lambda: date(2025, 1, 10))
    assert mp_mod.parse_period(_ns(last_n_days=3)) == (
        date(2025, 1, 7),
        date(2025, 1, 9),
    )
    with pytest.raises(SystemExit, match="Must specify a period"):
        mp_mod.parse_period(_ns())


def test_today_and_layout(tmp_path: Path) -> None:
    today = mp_mod._today_clt()
    assert abs((today - date.today()).days) <= 1
    d = date(2025, 3, 4)
    p = mp_mod.day_parquet_path(tmp_path, d)
    assert p == tmp_path / "year=2025" / "month=03" / "day=04" / "data.parquet"
    assert not mp_mod.already_done(tmp_path, d)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"")
    assert not mp_mod.already_done(tmp_path, d)
    p.write_bytes(b"x")
    assert mp_mod.already_done(tmp_path, d)
    assert not mp_mod.date_range(d, d - timedelta(days=1))


def test_parse_bus_ids(tmp_path: Path) -> None:
    assert mp_mod._parse_bus_ids("1, 2,,3") == {1, 2, 3}
    with pytest.raises(SystemExit, match="cannot parse"):
        mp_mod._parse_bus_ids("1,x")
    with pytest.raises(SystemExit, match="file not found"):
        mp_mod._parse_bus_ids(f"@{tmp_path / 'nope.txt'}")
    f = tmp_path / "ids.txt"
    f.write_text("# header\n\n10 crucero\nbad\n12\n")
    assert mp_mod._parse_bus_ids(f"@{f}") == {10, 12}


def _catalogue() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "bus_id": [100, 200],
            "bar_transf": ["X_____________220", "Y_____________500"],
            "barra_info": ["Xbus", "Ybus"],
            "voltage_kv": [220, 500],
            "region": ["north", "south"],
            "last_seen": ["2025-01-01", "2025-01-01"],
        }
    )


def test_select_ref_buses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kw: dict[str, Any] = {
        "name_regex": None,
        "region": None,
        "min_voltage": None,
    }
    assert mp_mod.select_ref_buses(
        buses_spec="default10", catalogue_path=None, **kw
    ) == list(REF_BUSES)
    missing = tmp_path / "none.parquet"
    assert mp_mod.select_ref_buses(
        buses_spec="all", catalogue_path=missing, **kw
    ) == list(REF_BUSES)
    assert "catalogue empty" in capsys.readouterr().out
    assert mp_mod.select_ref_buses(buses_spec="10,2", catalogue_path=missing, **kw) == [
        b for b in REF_BUSES if b[1] in {10, 2}
    ]
    assert mp_mod.select_ref_buses(
        buses_spec="9999", catalogue_path=missing, **kw
    ) == list(REF_BUSES)

    cat = tmp_path / "cat.parquet"
    _catalogue().to_parquet(cat)
    assert [
        b[1]
        for b in mp_mod.select_ref_buses(buses_spec="all", catalogue_path=cat, **kw)
    ] == [100, 200]
    assert mp_mod.select_ref_buses(buses_spec="200", catalogue_path=cat, **kw) == [
        ("Y_____________500", 200, "Ybus")
    ]


class _Client:
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg

    def __enter__(self) -> "_Client":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_maybe_refresh_catalogue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cat = tmp_path / "cat.parquet"
    mp_mod._maybe_refresh_catalogue(refresh=None, date=None, catalogue_path=cat)
    assert not cat.exists()

    monkeypatch.setattr(mp_mod, "discover_buses_from_cache", _catalogue)
    mp_mod._maybe_refresh_catalogue(refresh="cache", date=None, catalogue_path=cat)
    assert "0 → 2 buses" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="requires --refresh-date"):
        mp_mod._maybe_refresh_catalogue(refresh="api", date=None, catalogue_path=cat)

    monkeypatch.setattr(mp_mod, "CenApiClient", _Client)
    monkeypatch.setattr(
        mp_mod,
        "discover_buses_from_api",
        lambda client, date: _catalogue().iloc[[0]].assign(bus_id=300, last_seen=date),
    )
    mp_mod._maybe_refresh_catalogue(
        refresh="api", date="2025-02-01", catalogue_path=cat
    )
    assert "2 → 3 buses" in capsys.readouterr().out


def _day_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {"quarter": [0, 1], "bus_id": [10, 10], "lambda_cen": [1.0, 2.0]}
    )


def _fake_compute(client: Any, *, date_iso: str, **_kw: Any) -> pd.DataFrame:
    if date_iso.endswith("-02"):
        return pd.DataFrame()
    if date_iso.endswith("-03"):
        raise RuntimeError("bad day")
    return _day_frame()


def test_process_one_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mp_mod, "CenApiClient", _Client)
    monkeypatch.setattr(mp_mod, "compute_marginal_units_for_day", _fake_compute)
    buses = list(REF_BUSES[:2])
    ok = mp_mod._process_one_day(("2025-01-01", buses, "real", str(tmp_path), "none"))
    assert (ok.status, ok.n_rows, ok.n_buses) == ("ok", 2, 2)
    assert mp_mod.already_done(tmp_path, date(2025, 1, 1))
    empty = mp_mod._process_one_day(
        ("2025-01-02", buses, "real", str(tmp_path), "none")
    )
    assert empty.status == "empty"
    err = mp_mod._process_one_day(("2025-01-03", buses, "real", str(tmp_path), "none"))
    assert err.status == "error"
    assert err.error.startswith("RuntimeError: bad day")


class _FakePool:
    def __init__(self, processes: int) -> None:
        self.processes = processes
        self.terminated = False

    def __enter__(self) -> "_FakePool":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def imap_unordered(
        self, fn: Callable[[Any], Any], jobs: Iterable[Any]
    ) -> Iterator[Any]:
        return map(fn, jobs)

    def terminate(self) -> None:
        self.terminated = True

    def join(self) -> None:
        return None


class _FakeMp:
    @staticmethod
    def get_context(_method: str) -> "_FakeMp":
        return _FakeMp()

    Pool = _FakePool


@pytest.fixture
def fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mp_mod, "mp", _FakeMp)
    monkeypatch.setattr(mp_mod, "CenApiClient", _Client)
    monkeypatch.setattr(mp_mod, "compute_marginal_units_for_day", _fake_compute)
    monkeypatch.setattr(mp_mod.logging, "basicConfig", lambda **_k: None)


def _base(out: Path, *extra: str) -> list[str]:
    return [
        "--start-date",
        "2025-01-01",
        "--end-date",
        "2025-01-04",
        "--buses",
        "default10",
        "--output",
        str(out),
        "--workers",
        "1",
        *extra,
    ]


def test_main_full_run(
    tmp_path: Path, fake_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "ds"
    # Day 4 already done → skipped on resume.
    done = mp_mod.day_parquet_path(out, date(2025, 1, 4))
    done.parent.mkdir(parents=True)
    done.write_bytes(b"x")
    assert mp_mod.main(_base(out, "-v")) == 0
    man = json.loads((out / "_manifest.json").read_text())
    assert man["n_days_ok"] == 1
    assert man["n_days_empty"] == 1
    assert man["n_days_error"] == 1
    assert man["n_days_skipped"] == 1
    assert man["n_rows_total"] == 2
    assert man["ref_bus_uids"] == [b[1] for b in REF_BUSES]
    text = capsys.readouterr().out
    assert "✓" in text and "∅" in text and "✗" in text


def test_main_abort_on_failures(
    tmp_path: Path, fake_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "ds"
    args = _base(out, "-q", "--max-failures", "0")
    args[3] = "2025-01-03"  # only the failing day
    assert mp_mod.main(args) == 1
    assert "ABORT: 1 failures" in capsys.readouterr().out


def test_main_other_status_tag(
    tmp_path: Path,
    fake_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        mp_mod,
        "_process_one_day",
        lambda job: mp_mod.DayResult(date_iso=job[0], status="weird"),
    )
    assert mp_mod.main(_base(tmp_path / "ds")) == 0
    assert "] ? 2025-01-01" in capsys.readouterr().out


def test_main_nothing_to_do_dry_run_list_budget(
    tmp_path: Path,
    fake_env: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "ds"
    assert mp_mod.main(_base(out, "--list-buses")) == 0
    assert f"Resolved bus selection — {len(REF_BUSES)} buses" in capsys.readouterr().out
    assert mp_mod.main(_base(out, "--dry-run")) == 0
    assert "DRY RUN" in capsys.readouterr().out
    # Everything done already → empty job list, still writes a manifest.
    for k in range(1, 5):
        p = mp_mod.day_parquet_path(out, date(2025, 1, k))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    assert mp_mod.main(_base(out)) == 0
    assert json.loads((out / "_manifest.json").read_text())["n_days_skipped"] == 4
    # Budget guard: a year of 10 buses exceeds the threshold.
    big = ["--year", "2024", "--buses", "default10", "--output", str(out), "--force"]
    assert mp_mod.main(big) == 2
    assert "safety threshold" in capsys.readouterr().out


def test_main_no_buses_matched(tmp_path: Path, fake_env: None) -> None:
    cat = tmp_path / "cat.parquet"
    _catalogue().to_parquet(cat)
    args = [
        "--start-date",
        "2025-01-01",
        "--end-date",
        "2025-01-01",
        "--catalogue",
        str(cat),
        "--buses-region",
        "centre",
        "--output",
        str(tmp_path / "o"),
    ]
    with pytest.raises(SystemExit, match="no buses matched"):
        mp_mod.main(args)
