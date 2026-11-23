# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Offline edge-case tests for the CEN HTTP clients and endpoint catalogues.

Covers :mod:`cen2gtopt._cen_client`, :mod:`cen2gtopt._sip_client`,
:mod:`cen2gtopt._sip_endpoints` and :mod:`cen2gtopt._operacion_client`.
All HTTP traffic is faked.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest
import requests

from cen2gtopt import (
    _cen_client as cc,
    _operacion_client as oc,
    _sip_client as sc,
    _sip_endpoints as se,
)


class _Resp:
    def __init__(
        self, status: int = 200, payload: Any = None, *, bad_json: bool = False
    ) -> None:
        self.status_code = status
        self._payload = payload
        self._bad_json = bad_json
        self.text = "body"
        self.headers = {"content-type": "text/html"}

    def json(self) -> Any:
        if self._bad_json:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def _client(**kw: Any) -> cc.CenApiClient:
    cfg = cc.CenApiConfig(
        user_keys={"sip": "k", "operacion": "k"}, backoff_base=0.0, **kw
    )
    return cc.CenApiClient(cfg)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cc.time, "sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# CenApiClient
# ---------------------------------------------------------------------------


def test_client_tls_default_and_context() -> None:
    with _client() as client:
        assert client.config.verify_tls
        assert client._session.headers["Accept"] == "application/json"


def test_get_retries_request_errors_then_succeeds() -> None:
    client = _client()
    side = [requests.ConnectionError("reset"), _Resp(200, {"ok": 1})]
    with patch.object(client._session, "get", side_effect=side) as get:
        assert client.get("sip", "/x", params={"a": 1}) == {"ok": 1}
    assert get.call_count == 2
    assert "user_key=k" in get.call_args.args[0]


def test_get_exhausts_retries() -> None:
    client = _client(max_retries=1)
    with patch.object(
        client._session, "get", side_effect=requests.Timeout("slow")
    ) as get:
        with pytest.raises(cc.CenServerError, match="2 attempts exhausted.*slow"):
            client.get("sip", "/x")
    assert get.call_count == 2


def test_get_bad_json_and_unexpected_status() -> None:
    client = _client()
    with patch.object(client._session, "get", return_value=_Resp(bad_json=True)):
        with pytest.raises(cc.CenApiError, match="expected JSON body"):
            client.get("sip", "/x")
    with patch.object(client._session, "get", return_value=_Resp(418)):
        with pytest.raises(cc.CenApiError, match="unexpected HTTP 418"):
            client.get("sip", "/x")


def test_get_typed_errors_and_config() -> None:
    client = _client()
    with pytest.raises(cc.CenApiError, match="unknown service"):
        client.get("nope", "/x")
    with pytest.raises(cc.CenAuthError, match="no user_key"):
        cc.CenApiClient(cc.CenApiConfig(user_keys={})).get("sip", "/x")
    with patch.object(client._session, "get", return_value=_Resp(403)):
        with pytest.raises(cc.CenAuthError):
            client.get("sip", "/x")
    with patch.object(client._session, "get", return_value=_Resp(404)):
        with pytest.raises(cc.CenNotFoundError):
            client.get("sip", "/x")
    side = [_Resp(503), _Resp(200, {"ok": 2})]
    with patch.object(client._session, "get", side_effect=side):
        assert client.get("sip", "/x") == {"ok": 2}


def test_paginate_stops_at_max_pages() -> None:
    client = _client()
    pages: list[int] = []

    def _get(_svc: str, _path: str, params: dict[str, Any]) -> dict[str, Any]:
        pages.append(params["page"])
        return {"data": [1]}

    with patch.object(client, "get", side_effect=_get):
        assert len(list(client.paginate("sip", "/x", max_pages=3))) == 3
    assert pages == [0, 1, 2]


# ---------------------------------------------------------------------------
# SIP typed wrappers
# ---------------------------------------------------------------------------


class _Pager:
    """Minimal client exposing ``paginate`` over a fixed list of bodies."""

    def __init__(self, bodies: list[dict[str, Any]]) -> None:
        self.bodies = bodies
        self.params: list[dict[str, Any] | None] = []

    def paginate(self, _service: str, _path: str, **kw: Any) -> Iterator[dict]:
        self.params.append(kw.get("params"))
        yield from self.bodies


def _pager(bodies: list[dict[str, Any]]) -> Any:
    return _Pager(bodies)


def test_fetch_endpoint_envelopes() -> None:
    client = _pager([{"data": [{"a": 1}]}, {"content": [{"a": 2}]}, {"data": "x"}])
    df = sc.fetch_endpoint(client, se.CENTRALES)
    assert list(df["a"]) == [1, 2]
    assert sc.fetch_endpoint(_pager([{"data": []}]), se.CENTRALES).empty


def test_typed_wrappers_empty() -> None:
    empty = _pager([{}])
    assert sc.fetch_costo_marginal_real(empty, start="a", end="b").empty
    assert sc.fetch_costo_marginal_online(empty, start="a", end="b").empty
    assert sc.fetch_centrales(empty).empty
    assert sc.fetch_costo_combustible(empty, start="a", end="b").empty


def test_fetch_generacion_real_technology_param() -> None:
    pager = _Pager([{"data": [{"x": 1}]}])
    client: Any = pager
    sc.fetch_generacion_real(client, start="a", end="b", technology="Solar")
    sc.fetch_generacion_real(client, start="a", end="b")
    assert pager.params[0] == {
        "startDate": "a",
        "endDate": "b",
        "pageSize": 1000,
        "tipoTecnologia": "Solar",
    }
    assert "tipoTecnologia" not in (pager.params[1] or {})


def test_fetch_costo_combustible_both_accent_forms() -> None:
    rows = [
        {
            "fecha": "2024-11-01T00:00:00",
            "nombreCentral": "C1",
            "configuración": "C1_GNL",
            "empresa": "E",
            "tipoCombustible": "GNL",
            "costoCombustible": "7.5",
        }
    ]
    df = sc.fetch_costo_combustible(_pager([{"content": rows}]), start="a", end="b")
    assert df.iloc[0].to_dict() == {
        "date_utc": "2024-11-01",
        "central_name": "C1",
        "configuracion": "C1_GNL",
        "empresa": "E",
        "tipo_combustible": "GNL",
        "costo_combustible": 7.5,
    }
    plain = [{"fecha": "2024-11-02", "configuracion": "X", "costoCombustible": "bad"}]
    df = sc.fetch_costo_combustible(_pager([{"content": plain}]), start="a", end="b")
    assert df["configuracion"].iloc[0] == "X"
    assert pd.isna(df["costo_combustible"].iloc[0])


def test_normalize_cmg_optional_columns() -> None:
    raw = pd.DataFrame(
        {
            "fecha": ["2026-04-07"],
            "hra": [3],
            "barra_transf": ["BA S/E CRUCERO 220KV"],
            "barra_info": ["Crucero 220"],
            "cmg_usd_mwh_": ["55.5"],
        }
    )
    out = sc._normalize_cmg(raw, value_col="lmp")
    assert list(out.columns) == ["date_utc", "hour", "bus_uid", "bus_name", "lmp"]
    assert out["lmp"].iloc[0] == pytest.approx(55.5)


def test_endpoint_catalogues() -> None:
    live = se.list_confirmed_live()
    assert se.COSTO_MARGINAL_REAL in live
    assert se.CMG_PROGRAMADO_PCP not in live
    assert all(e.path.startswith("/") for e in se.ALL_ENDPOINTS.values())


# ---------------------------------------------------------------------------
# operación
# ---------------------------------------------------------------------------


def test_operacion_fuelcons_empty_and_maintenance() -> None:
    client = _client()
    with patch.object(client, "get", return_value={"content": []}):
        assert oc.fetch_topologia_fuelcons(client, 1).empty
    bodies = [
        {"content": [{"id": 1}], "totalPages": 2},
        {"content": [{"id": 2}], "totalPages": 2},
    ]
    with patch.object(client, "get", side_effect=bodies):
        df = oc.fetch_mantenimiento_mayor(client, page_size=10, max_pages=5)
    assert list(df["id"]) == [1, 2]
