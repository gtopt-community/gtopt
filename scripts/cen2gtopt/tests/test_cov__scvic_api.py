# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Offline tests for :mod:`cen2gtopt._scvic_api` (HTTP mocked)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from cen2gtopt import _scvic_api as sa

BODY = {
    "header": [
        {"field": "coordinatedName", "header": "Empresa"},
        {"field": "2026-04-01", "header": "01-04-2026"},
        {"field": "2026-04-02", "header": "02-04-2026"},
        {"header": "no field"},
    ],
    "data": [
        {
            "coordinatedName": "ACME",
            "powerStationName": "CENTRAL A",
            "powerPlantName": "A-U1",
            "configurationName": "A-U1 GNL",
            "fuelsTypesName": "GNL",
            "2026-04-01": "1.234,5",
            "2026-04-02": "",
        },
        {"powerStationName": "CENTRAL B", "2026-04-01": 80, "2026-04-02": "x"},
    ],
}


class _Resp:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


@pytest.fixture(name="http")
def _http(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"calls": [], "envelope": {"payload": json.dumps(BODY)}}

    def _get(url: str, **kw: Any) -> _Resp:
        state["calls"].append((url, kw))
        return _Resp(state["envelope"])

    monkeypatch.setattr(sa.requests, "get", _get)
    return state


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, math.nan),
        (float("nan"), math.nan),
        (5, 5.0),
        (2.5, 2.5),
        ("  ", math.nan),
        ("1.234,5", 1234.5),
        ("50,343", 50.343),
        ("12.5", 12.5),
        ("abc", math.nan),
    ],
)
def test_to_float_es(raw: object, expected: float) -> None:
    got = sa._to_float_es(raw)
    if math.isnan(expected):
        assert math.isnan(got)
    else:
        assert got == pytest.approx(expected)


def test_fetch_cv_rpt_unpivots_and_caches(http: dict[str, Any], tmp_path: Path) -> None:
    df = sa.fetch_cv_rpt(2026, 4, cache_root=tmp_path)
    url, kw = http["calls"][0]
    assert url == f"{sa.CV_RPT_BASE}/cvrpt/show_cv_rpt"
    assert kw["params"] == {"yyyy": 2026, "mm": "04"}
    assert len(df) == 2  # empty + non-numeric cells are dropped
    a = df[df["central_name"] == "CENTRAL A"].iloc[0]
    assert a["costo_variable_total"] == pytest.approx(1234.5)
    assert a["tipo_combustible"] == "GNL" and a["empresa"] == "ACME"
    b = df[df["central_name"] == "CENTRAL B"].iloc[0]
    assert b["empresa"] == "" and b["costo_variable_total"] == pytest.approx(80.0)
    assert (tmp_path / "cv_rpt_2026_04.parquet").exists()

    # Cache hit → no new request; bypass_cache → refetch.
    assert len(sa.fetch_cv_rpt(2026, 4, cache_root=tmp_path)) == 2
    assert len(http["calls"]) == 1
    sa.fetch_cv_rpt(2026, 4, cache_root=tmp_path, bypass_cache=True)
    assert len(http["calls"]) == 2


def test_fetch_cv_rpt_dict_payload(http: dict[str, Any], tmp_path: Path) -> None:
    http["envelope"] = {"payload": BODY}
    assert len(sa.fetch_cv_rpt(2026, 4, cache_root=tmp_path)) == 2


def test_fetch_cv_rpt_empty(http: dict[str, Any], tmp_path: Path) -> None:
    http["envelope"] = {}
    assert sa.fetch_cv_rpt(2026, 5, cache_root=tmp_path).empty
    # Rows present but no date columns → empty frame, nothing cached.
    http["envelope"] = {"payload": {"header": [], "data": [{"x": 1}]}}
    assert sa.fetch_cv_rpt(2026, 5, cache_root=tmp_path).empty
    assert not list(tmp_path.glob("*.parquet"))


def test_fetch_cv_rpt_default_cache_root(
    http: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sa.Path, "home", lambda: tmp_path)
    sa.fetch_cv_rpt(2026, 4)
    assert (tmp_path / ".cache/gtopt/cen2gtopt/scvic/cv_rpt_2026_04.parquet").exists()
    assert http["calls"]


def test_cv_rpt_for_date(http: dict[str, Any], tmp_path: Path) -> None:
    day = sa.cv_rpt_for_date("2026-04-01", cache_root=tmp_path)
    assert list(day.index) == [0, 1]
    assert set(day["date_utc"]) == {"2026-04-01"}
    http["envelope"] = {}
    assert sa.cv_rpt_for_date("2026-06-01", cache_root=tmp_path).empty
