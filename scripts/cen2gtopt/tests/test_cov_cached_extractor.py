# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Extra coverage for :mod:`cen2gtopt._cached_extractor` (fake client)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from cen2gtopt import _cached_extractor as ce
from cen2gtopt._cen_client import CenNotFoundError, CenServerError


class _ScriptedClient:
    """Fake ``CenApiClient``: returns / raises scripted items per call."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def get(self, service: str, endpoint: str, *, params: dict) -> Any:
        self.calls.append({"service": service, "endpoint": endpoint, **params})
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _fetch(client: Any, tmp_path: Path, **kw: Any) -> pd.DataFrame:
    return ce.cached_paginated_fetch(
        client,
        service="sip",
        endpoint="/x/v1/find",
        params={"startDate": "2025-01-01"},
        cache_root=tmp_path,
        sleep_on_fail_seconds=0.0,
        **kw,
    )


def test_cache_root_default_and_all_segment(tmp_path: Path) -> None:
    assert ce.cache_root_default() == ce._DEFAULT_CACHE_ROOT
    p = ce.cache_path(service="sip", endpoint="/a/b", params=None, cache_root=tmp_path)
    assert p.name.startswith("all_")
    assert p.parent == tmp_path / "sip" / "a__b"


def test_404_on_first_page_returns_empty(tmp_path: Path) -> None:
    client = _ScriptedClient([CenNotFoundError("nope")])
    df = _fetch(client, tmp_path)
    assert df.empty
    assert not list(tmp_path.rglob("*.parquet"))


def test_api_error_retry_then_success_and_json_coercion(tmp_path: Path) -> None:
    rows = [{"a": 1, "obj": {"k": 1}}, {"a": 2, "obj": [1, 2]}]
    client = _ScriptedClient(
        [
            CenServerError("500"),
            RuntimeError("socket"),
            {"content": rows},
            {"data": rows},
            {"data": [{"a": 3, "obj": None, "s": "x"}]},
        ]
    )
    df = _fetch(client, tmp_path, page_size=2)
    assert list(df["a"]) == [1, 2, 1, 2, 3]
    assert json.loads(df["obj"].iloc[0]) == {"k": 1}
    assert df["obj"].iloc[1] == "[1, 2]"
    # Retries keep the same page number.
    assert [c["page"] for c in client.calls] == [1, 1, 1, 2, 3]
    # Second call is a cache hit (no client calls needed).
    again = _fetch(_ScriptedClient([]), tmp_path)
    assert len(again) == 5


def test_consecutive_api_errors_give_up(tmp_path: Path) -> None:
    client = _ScriptedClient([CenServerError("a")] * 3)
    assert _fetch(client, tmp_path, max_retries=3).empty
    assert len(client.calls) == 3


def test_consecutive_generic_errors_give_up(tmp_path: Path) -> None:
    client = _ScriptedClient([ValueError("a"), ValueError("b")])
    assert _fetch(client, tmp_path, max_retries=2).empty


def test_empty_chunk_and_max_pages(tmp_path: Path) -> None:
    client = _ScriptedClient([{"data": []}])
    assert _fetch(client, tmp_path).empty
    client2 = _ScriptedClient([{"data": [{"a": 1}]}])
    df = _fetch(client2, tmp_path / "b", page_size=1, max_pages=1)
    assert len(df) == 1


def test_clear_cache_variants(tmp_path: Path) -> None:
    assert ce.clear_cache(cache_root=tmp_path / "missing") == 0
    d = tmp_path / "sip" / "x__y"
    d.mkdir(parents=True)
    (d / "a.parquet").write_bytes(b"")
    (tmp_path / "op").mkdir()
    (tmp_path / "op" / "b.parquet").write_bytes(b"")
    assert ce.clear_cache(service="sip", endpoint="/x/y", cache_root=tmp_path) == 1
    assert ce.clear_cache(cache_root=tmp_path) == 1


def test_fetch_by_name_extra_params_and_prefetch(tmp_path: Path) -> None:
    client: Any = _ScriptedClient([{"data": []}])
    ce.fetch_by_name(
        client,
        "cmg_real",
        start="2025-01-01",
        cache_root=tmp_path,
        extra_params={"bar": "B1"},
    )
    assert client.calls[0]["bar"] == "B1"
    assert client.calls[0]["endDate"] == "2025-01-01"

    client2: Any = _ScriptedClient([{"data": [{"a": 1}]}, KeyError("boom")])
    out = ce.prefetch_date(
        client2,
        date="2025-01-02",
        names=["cmg_real", "nonexistent_endpoint"],
        cache_root=tmp_path,
    )
    assert out == {"cmg_real": 1, "nonexistent_endpoint": -1}
