# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Internal errors must not leak exception text or tracebacks to clients.

Covers the CodeQL ``py/stack-trace-exposure`` findings: every handler that
catches an unexpected exception returns a generic message with an
``error_id``; the details (exception text + traceback) only go to the
server-side log.
"""

# pylint: disable=protected-access

import logging
import sys
import types
from unittest.mock import patch

import pytest
import requests

import guiservice.app as gui_app
from guiservice.app import app

SECRET = "s3cr3t-/srv/internal/path-db_password=hunter2"


@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.delenv("GTOPT_WEBSERVICE_API_TOKEN", raising=False)
    saved = gui_app._webservice_url
    with app.test_client() as c:
        yield c
    gui_app._webservice_url = saved


def _assert_no_leak(resp):
    text = resp.get_data(as_text=True)
    assert SECRET not in text
    assert "Traceback" not in text
    assert "RuntimeError" not in text


def _assert_generic_error(resp, status):
    assert resp.status_code == status
    _assert_no_leak(resp)
    body = resp.get_json()
    error_id = body["error_id"]
    assert len(error_id) == 12
    assert error_id in body["error"]
    return error_id


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/solve/status/tok123"),
        ("get", "/api/solve/results/tok123"),
        ("get", "/api/solve/jobs"),
        ("get", "/api/solve/ping"),
        ("get", "/api/solve/logs"),
        ("get", "/api/solve/job_logs/tok123"),
        ("get", "/api/solve/monitor/tok123"),
        ("post", "/api/solve/stop/tok123"),
    ],
)
def test_webservice_proxy_internal_error_is_generic(client, caplog, method, path):
    with patch(f"guiservice.app.http_requests.{method}", side_effect=RuntimeError(SECRET)):
        with caplog.at_level(logging.ERROR, logger=app.logger.name):
            resp = getattr(client, method)(path)
    error_id = _assert_generic_error(resp, 500)
    # The details are logged server-side, correlated by the error id.
    records = [r for r in caplog.records if error_id in r.getMessage()]
    assert records
    assert records[0].exc_info is not None
    assert SECRET in str(records[0].exc_info[1])


def test_submit_internal_error_is_generic(client):
    case = {
        "case_name": "c",
        "options": {},
        "simulation": {},
        "system": {"bus": [{"uid": 1, "name": "b1"}]},
    }
    with patch("guiservice.app.http_requests.post", side_effect=RuntimeError(SECRET)):
        resp = client.post("/api/solve/submit", json=case)
    _assert_generic_error(resp, 500)


def test_check_server_does_not_leak_exception_text(client):
    with patch(
        "guiservice.app.http_requests.get",
        side_effect=requests.ConnectionError(SECRET),
    ):
        resp = client.get("/api/check_server")
    assert resp.status_code == 200
    _assert_no_leak(resp)
    data = resp.get_json()
    for key in ("ping", "logs", "jobs"):
        assert data[key]["status"] == "error"
        assert data[key]["error_id"] in data[key]["error"]


def test_topology_build_error_is_generic(client):
    if not gui_app._DIAGRAM_AVAILABLE:
        pytest.skip("gtopt_diagram not available")
    with patch("guiservice.app._build_case_json", side_effect=RuntimeError(SECRET)):
        resp = client.post("/api/diagram/topology", json={"caseData": {}})
    _assert_generic_error(resp, 400)


def test_topology_builder_error_is_generic(client):
    if not gui_app._DIAGRAM_AVAILABLE:
        pytest.skip("gtopt_diagram not available")
    with patch("guiservice.app._TopologyBuilder", side_effect=RuntimeError(SECRET)):
        resp = client.post("/api/diagram/topology", json={"caseData": {}})
    _assert_generic_error(resp, 500)


def test_scale_objective_mismatch_uses_fixed_message(client, monkeypatch):
    class ScaleObjectiveMismatchError(ValueError):
        """Stand-in matched by class name in guiservice.app."""

    def _raise(*_args, **_kwargs):
        raise ScaleObjectiveMismatchError(SECRET)

    pkg = types.ModuleType("gtopt_results_summary")
    mod = types.ModuleType("gtopt_results_summary.summary")
    mod.summarize_output_dict = _raise  # type: ignore[attr-defined]
    pkg.summary = mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gtopt_results_summary", pkg)
    monkeypatch.setitem(sys.modules, "gtopt_results_summary.summary", mod)

    resp = client.post("/api/results/summary", json={"results": {}})
    assert resp.status_code == 400
    assert SECRET not in resp.get_data(as_text=True)
    assert "scale_objective" in resp.get_json()["error"]


def test_gui_log_viewer_omits_tracebacks(client):
    with patch("guiservice.app.http_requests.get", side_effect=RuntimeError(SECRET)):
        error_id = _assert_generic_error(client.get("/api/solve/jobs"), 500)
    logs = client.get("/api/logs?lines=500").get_json()["logs"]
    matching = [line for line in logs if error_id in line]
    assert matching
    joined = "\n".join(logs)
    assert SECRET not in joined
    assert "Traceback" not in joined
