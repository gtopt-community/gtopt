# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage tests for :mod:`cen2gtopt.prepopulate_cache` (no network)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from cen2gtopt import prepopulate_cache as pc

#: populate_one only forwards the client to the (faked) fetcher.
NO_CLIENT: Any = None
_NS = "http://tempuri.org/MasterDataSet.xsd"


def _write_xml(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    objs = [
        (22, "Crucero220"),
        (22, "AJahuel500"),
        (22, "NoVoltage"),
        (22, "AVeryLongSubstationName220"),
        (22, ""),
        (2, "Generator1"),
        (None, "Orphan"),
    ]
    body = []
    for cid, name in objs:
        cid_xml = f"<class_id>{cid}</class_id>" if cid is not None else ""
        body.append(f"<t_object>{cid_xml}<name>{name}</name></t_object>")
    path.write_text(f'<MasterDataSet xmlns="{_NS}">{"".join(body)}</MasterDataSet>')
    return path


def test_heuristic_and_candidates(tmp_path: Path) -> None:
    assert pc._heuristic_bar_transf("Crucero220") == "CRUCERO_______220"
    assert pc._heuristic_bar_transf("NoVoltage") is None
    assert pc._heuristic_bar_transf("AVeryLongSubstationName220") is None
    xml = _write_xml(tmp_path / "m.xml")
    names = pc.extract_plexos_node_names(xml)
    assert names == [
        "AJahuel500",
        "AVeryLongSubstationName220",
        "Crucero220",
        "NoVoltage",
    ]
    assert pc.candidate_bar_transfs(names) == [
        "A.JAHUEL______500",
        "CRUCERO_______220",
    ]


def test_populate_one(monkeypatch: pytest.MonkeyPatch) -> None:
    got: dict[str, Any] = {}

    def _ok(_c: Any, name: str, **kw: Any) -> pd.DataFrame:
        got.update(kw, name=name)
        return pd.DataFrame({"a": [1, 2]})

    monkeypatch.setattr(pc, "fetch_by_name", _ok)
    assert pc.populate_one(NO_CLIENT, date="2025-01-01", bar_transf="X_220") == 2
    assert got["extra_params"] == {"bar_transf": "X_220"}

    def _bad(*_a: Any, **_k: Any) -> pd.DataFrame:
        raise OSError("net")

    monkeypatch.setattr(pc, "fetch_by_name", _bad)
    assert pc.populate_one(NO_CLIENT, date="2025-01-01", bar_transf="X_220") == 0


def test_find_plexos_xml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert pc.find_plexos_xml(tmp_path / "missing") is None
    root = tmp_path / "unp"
    root.mkdir()
    assert pc.find_plexos_xml(root) is None
    _write_xml(root / "PCP_20250101" / "DBSEN_PRGDIARIO_1.xml")
    newest = _write_xml(root / "PID_20250102" / "Modelos" / "DBSEN_PRGDIARIO_2.xml")
    assert pc.find_plexos_xml(root) == newest
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert pc.find_plexos_xml() is None


class _FakeClient:
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"saved": None, "cached": set()}
    monkeypatch.setattr(pc, "CenApiClient", _FakeClient)
    monkeypatch.setattr(pc.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        pc,
        "discover_buses_from_cache",
        lambda: pd.DataFrame({"bar_transf": sorted(state["cached"])}),
    )
    monkeypatch.setattr(pc, "load_catalogue", pd.DataFrame)
    monkeypatch.setattr(pc, "merge_catalogues", lambda a, b: b)

    def _save(df: pd.DataFrame, path: Path) -> Path:
        state["saved"] = df
        return path

    monkeypatch.setattr(pc, "save_catalogue", _save)
    monkeypatch.setattr(pc, "DEFAULT_CATALOGUE_PATH", tmp_path / "cat.parquet")
    return state


def test_main_no_xml(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(pc, "find_plexos_xml", lambda: None)
    assert pc.main(["--date", "2025-01-01"]) == 2
    assert pc.main(["--date", "2025-01-01", "--xml", str(tmp_path / "nope.xml")]) == 2


def test_main_nothing_to_do(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    env["cached"] = {"A_220"}
    assert pc.main(["--date", "2025-01-01", "--bar-transfs", "A_220, ,"]) == 0
    assert "nothing to do" in capsys.readouterr().out


def test_main_from_xml_with_limit(
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    xml = _write_xml(tmp_path / "m.xml")
    fetched: list[str] = []

    def _pop(_c: Any, *, date: str, bar_transf: str) -> int:
        fetched.append(bar_transf)
        env["cached"].add(bar_transf)
        return 10

    monkeypatch.setattr(pc, "populate_one", _pop)
    rc = pc.main(["--date", "2025-01-01", "--xml", str(xml), "--limit", "1", "-v"])
    assert rc == 0
    assert fetched == ["A.JAHUEL______500"]
    out = capsys.readouterr().out
    assert "capped to first 1" in out
    assert "catalogue updated: 0 → 1" in out


def test_main_adaptive_throttle_and_retry(
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Script: first two raise (back-off), then 6 successes (decay),
    # then one empty and one more failure; the retry pass sees one
    # success, one repeated failure and one empty answer.
    script: dict[str, list[Any]] = {
        "B0": [RuntimeError("429"), 5],
        "B1": [RuntimeError("503"), RuntimeError("still down")],
        "B2": [3],
        "B3": [3],
        "B4": [3],
        "B5": [3],
        "B6": [3],
        "B7": [3],
        "B8": [0],
        "B9": [RuntimeError("timeout"), 0],
    }
    sleeps: list[float] = []
    monkeypatch.setattr(pc.time, "sleep", sleeps.append)

    def _pop(_c: Any, *, date: str, bar_transf: str) -> int:
        item = script[bar_transf].pop(0)
        if isinstance(item, Exception):
            raise item
        return int(item)

    monkeypatch.setattr(pc, "populate_one", _pop)
    todo = ",".join(f"B{i}" for i in range(10))
    assert (
        pc.main(["--date", "2025-01-01", "--bar-transfs", todo, "--delay-ms", "100"])
        == 0
    )
    out = capsys.readouterr().out
    assert "retry pass: 3 bus(es)" in out
    assert "[10/10]  ok=6 empty=1 failed=3" in out
    # Back-off doubled twice from 100 ms, then decayed after 5 successes.
    assert sleeps[0] == pytest.approx(0.2)
    assert sleeps[1] == pytest.approx(0.4)
    assert sleeps[6] < 0.4
    # Retry pass sleeps at the ceiling delay (max(16*base, 4 s)).
    assert sleeps[-1] == pytest.approx(4.0)
