# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage tests for the ``cen2gtopt.buses`` CLI (no network)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
import pytest

from cen2gtopt import buses


def _catalogue() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "bus_id": [1, 2, 3],
            "bar_transf": [
                "CRUCERO_______220",
                "CHARRUA_______500",
                "POLPAICO______220",
            ],
            "barra_info": ["Crucero 220", "Charrua 500", "Polpaico 220"],
            "voltage_kv": [220, 500, 220],
            "region": ["north", "south-centre", "centre"],
            "last_seen": ["2025-01-01"] * 3,
        }
    )


@pytest.fixture
def cat_path(tmp_path: Path) -> Path:
    p = tmp_path / "cat.parquet"
    _catalogue().to_parquet(p)
    return p


def test_make_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CEN_USER_KEY", "abc")
    seen: list[Any] = []
    monkeypatch.setattr(buses, "CenApiClient", seen.append)
    buses._make_client()
    assert seen[0].user_keys["sip"] == "abc"


def test_list_show_categorize(
    cat_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    c = ["--catalogue", str(cat_path)]
    assert buses.main([*c, "list", "--min-voltage", "300"]) == 0
    out = capsys.readouterr().out
    assert "Charrua 500" in out and "1 buses" in out

    assert buses.main([*c, "-v", "show", "2"]) == 0
    assert "CHARRUA" in capsys.readouterr().out
    assert buses.main([*c, "-q", "show", "crucero_______220"]) == 0
    assert "Crucero 220" in capsys.readouterr().out
    assert buses.main([*c, "show", "_220"]) == 0
    assert "2 matches for '_220'" in capsys.readouterr().out
    assert buses.main([*c, "show", "nothere"]) == 1
    assert "no bus matched" in capsys.readouterr().out

    assert buses.main([*c, "categorize"]) == 0
    out = capsys.readouterr().out
    assert "TOTAL" in out and "3 buses total" in out

    empty = ["--catalogue", str(tmp_path / "missing.parquet")]
    assert buses.main([*empty, "list"]) == 0
    assert "catalogue empty" in capsys.readouterr().out
    assert buses.main([*empty, "show", "1"]) == 1
    assert buses.main([*empty, "categorize"]) == 1


def test_resolve_bus_ref_empty() -> None:
    assert buses._resolve_bus_ref(pd.DataFrame(), "x").empty


def test_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "c.parquet"
    c = ["--catalogue", str(path)]
    assert buses.main([*c, "refresh", "--from-api"]) == 2

    monkeypatch.setattr(buses, "discover_buses_from_cache", _catalogue)
    assert buses.main([*c, "refresh", "--from-cache"]) == 0
    assert "0 → 3 buses (+3)" in capsys.readouterr().out
    assert path.exists()

    class _Client:
        def __enter__(self) -> "_Client":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    got: dict[str, Any] = {}

    def _api(client: Any, *, date: str) -> pd.DataFrame:
        got["date"] = date
        return _catalogue().iloc[[0]].assign(bus_id=9, last_seen=date)

    monkeypatch.setattr(buses, "_make_client", _Client)
    monkeypatch.setattr(buses, "discover_buses_from_api", _api)
    assert buses.main([*c, "refresh", "--from-api", "--date", "2025-02-01"]) == 0
    assert got["date"] == "2025-02-01"
    assert "3 → 4 buses (+1)" in capsys.readouterr().out


def _island_frame(legacy: bool) -> pd.DataFrame:
    rows = []
    for d in ("2025-01-01", "2025-01-02"):
        for q in (0, 1, 2, 72):
            for i in range(8):
                if q == 72:
                    isl = "A" if i < 7 else "B"
                else:
                    isl = "A" if (i >> (q % 3)) & 1 else "B"
                rows.append(
                    {
                        "date_utc": d,
                        "quarter": q,
                        "hour": q // 4,
                        "minute": (q % 4) * 15,
                        "bus_uid" if legacy else "bus_id": i,
                        "barra_info" if legacy else "bus_label": f"Bus{i}",
                        "island_id": isl,
                        "lambda_cen": 10.0 * i + q,
                        "marginal_central": "U1" if i % 2 else "U2",
                        "marginal_fuel": "gas",
                    }
                )
    return pd.DataFrame(rows)


def _write_dataset(df: pd.DataFrame, root: Path) -> Path:
    pads.write_dataset(
        pa.Table.from_pandas(df, preserve_index=False),
        str(root),
        format="parquet",
        partitioning=pads.partitioning(
            pa.schema([("date_utc", pa.string())]), flavor="hive"
        ),
    )
    return root


@pytest.mark.parametrize("legacy", [False, True])
def test_islands_views(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], legacy: bool
) -> None:
    ds = _write_dataset(_island_frame(legacy), tmp_path / "ds")
    base = ["islands", "--dataset", str(ds)]
    assert buses.main(base) == 0
    assert "2025-01-01: 2 distinct islands" in capsys.readouterr().out
    assert buses.main([*base, "--date", "2025-01-01"]) == 0
    out = capsys.readouterr().out
    assert "quarter=72 (18:00)" in out
    assert "(+1 more)" in out
    assert buses.main([*base, "--date", "2025-01-01", "--quarter", "1"]) == 0
    assert buses.main([*base, "--date", "2025-01-09", "--quarter", "1"]) == 1
    assert "no rows" in capsys.readouterr().out
    assert buses.main([*base, "--aggregate"]) == 0
    out = capsys.readouterr().out
    assert "Co-island fraction" in out
    assert "single-island quarters: 0 / 8" in out


def test_islands_missing_dataset(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="dataset not found"):
        buses.main(["islands", "--dataset", str(tmp_path / "nope")])


def test_snapshot_and_aggregate_with_uid_column(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Direct calls with the legacy ``bus_uid`` column and a single bus
    # (no co-island matrix) exercise the fallback column selection.
    df = _island_frame(legacy=True)
    df = df[df["bus_uid"] == 0]
    assert buses._cmd_islands_snapshot(df, date_iso="2025-01-01", quarter=0) == 0
    assert buses._cmd_islands_aggregate(df) == 0
    assert "Co-island" not in capsys.readouterr().out


def test_aggregate_few_pairs_lists_all(capsys: pytest.CaptureFixture[str]) -> None:
    df = _island_frame(legacy=False)
    df = df[df["bus_id"].isin([0, 1, 2])]
    assert buses._cmd_islands_aggregate(df) == 0
    out = capsys.readouterr().out
    table = out.split("freq\n", 1)[1]
    pair_lines = [ln for ln in table.splitlines() if ln.startswith("Bus")]
    assert len(pair_lines) == 3


def test_aggregate_keeps_always_together_pairs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Regression: the pair table used to drop pairs with frequency 1.0
    # (mistaken for the diagonal) and list zeroed self-pairs instead.
    df = _island_frame(legacy=False)
    df = df[df["bus_id"].isin([0, 1])].copy()
    df["island_id"] = "A"
    assert buses._cmd_islands_aggregate(df) == 0
    table = capsys.readouterr().out.split("freq\n", 1)[1]
    pair_lines = [ln for ln in table.splitlines() if ln.startswith("Bus")]
    assert len(pair_lines) == 1
    assert pair_lines[0].split()[:3] == ["Bus0", "Bus1", "100.00%"]
