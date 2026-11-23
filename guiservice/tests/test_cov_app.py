"""Coverage tests for guiservice.app routes and helpers not hit elsewhere."""

import importlib.util
import json
import logging
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

import guiservice.app as gui_app
from guiservice.app import app

GUI_DIR = Path(gui_app.__file__).resolve().parent


@pytest.fixture(name="client")
def fixture_client():
    """Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Script-mode import (cwd=guiservice/, as the gtopt_gui launcher runs it)
# ---------------------------------------------------------------------------


def test_script_mode_import_uses_bare_sibling_modules(monkeypatch, tmp_path):
    """Without the ``guiservice`` package the bare sibling modules are used,
    and a missing gtopt_diagram disables the topology endpoint."""
    monkeypatch.setenv("GTOPT_GUI_LOG_FILE", str(tmp_path / "gui.log"))
    for mod in ("guiservice._schemas", "guiservice._zip_helpers", "gtopt_diagram"):
        monkeypatch.setitem(sys.modules, mod, None)
    for mod in ("_schemas", "_zip_helpers"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.syspath_prepend(str(GUI_DIR))

    spec = importlib.util.spec_from_file_location("gui_app_script_mode", GUI_DIR / "app.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        assert module._DIAGRAM_AVAILABLE is False  # pylint: disable=protected-access
        assert module._TopologyBuilder is None  # pylint: disable=protected-access
        assert sys.modules["_zip_helpers"].ELEMENT_TO_ARRAY_KEY is (
            sys.modules["_schemas"].ELEMENT_TO_ARRAY_KEY
        )
        assert module.OPTIONS_SCHEMA is sys.modules["_schemas"].OPTIONS_SCHEMA
    finally:
        for handler in list(module.app.logger.handlers):
            handler.close()
            module.app.logger.removeHandler(handler)
        sys.modules.pop("_schemas", None)
        sys.modules.pop("_zip_helpers", None)


def test_configure_logging_is_idempotent(monkeypatch, tmp_path):
    """A second call does not stack duplicate handlers."""
    monkeypatch.setenv("GTOPT_GUI_LOG_FILE", str(tmp_path / "again.log"))
    before = list(app.logger.handlers)
    gui_app._configure_logging()  # pylint: disable=protected-access
    assert app.logger.handlers == before


def test_allowed_hosts_ignores_unparseable_env_url(monkeypatch):
    """A malformed GTOPT_WEBSERVICE_URL is ignored, hostless URLs add nothing."""
    monkeypatch.delenv("GTOPT_GUI_ALLOWED_WEBSERVICE_HOSTS", raising=False)
    monkeypatch.setenv("GTOPT_WEBSERVICE_URL", "http://[::1")
    base = gui_app._allowed_webservice_hosts()  # pylint: disable=protected-access
    monkeypatch.setenv("GTOPT_WEBSERVICE_URL", "file:///x")
    assert gui_app._allowed_webservice_hosts() == base  # pylint: disable=protected-access


# ---------------------------------------------------------------------------
# Simple routes
# ---------------------------------------------------------------------------


def test_options_schema_route(client):
    """/api/options_schema serves OPTIONS_SCHEMA."""
    resp = client.get("/api/options_schema")
    assert resp.status_code == 200
    assert resp.get_json() == json.loads(json.dumps(gui_app.OPTIONS_SCHEMA))


@pytest.mark.parametrize("route", ["/api/case/download", "/api/case/preview", "/api/solve/submit"])
def test_empty_case_payload_rejected(client, route):
    """An empty JSON body is a 400 on every case-consuming route."""
    resp = client.post(route, json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "No case data provided"


@patch("guiservice.app.http_requests.post")
def test_submit_forwards_uploaded_zip_verbatim(mock_post, client):
    """A previously uploaded ZIP is forwarded unchanged with its system file."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"token": "t1"}
    mock_post.return_value = mock_resp
    case = {
        "case_name": "c",
        "_uploaded_zip_b64": "UEsFBgAAAAAAAAAAAAAAAAAAAAAAAA==",
        "_system_file": "sys.json",
    }
    resp = client.post("/api/solve/submit", json=case)
    assert resp.get_json() == {"token": "t1"}
    kwargs = mock_post.call_args.kwargs
    assert kwargs["data"] == {"systemFile": "sys.json"}
    assert kwargs["files"]["file"][1].getvalue().startswith(b"PK")


@patch("guiservice.app.http_requests.post")
def test_submit_http_error_body_falls_back_to_text(mock_post, client):
    """A non-JSON error body is reported verbatim."""
    err_resp = MagicMock()
    err_resp.status_code = 503
    err_resp.text = "busy"
    err_resp.json.side_effect = ValueError("not json")
    mock_post.side_effect = requests.HTTPError(response=err_resp)
    resp = client.post("/api/solve/submit", json={"case_name": "c"})
    assert resp.status_code == 502
    assert resp.get_json()["error"] == "Webservice error (503): busy"


def test_shutdown_uses_werkzeug_hook(client, monkeypatch):
    """The legacy werkzeug shutdown hook is used when present."""
    monkeypatch.delenv("GTOPT_GUI_SHUTDOWN_TOKEN", raising=False)
    hook = MagicMock()
    with patch("guiservice.app.os.kill") as mock_kill:
        resp = client.post(
            "/api/shutdown",
            headers={"X-GTOpt-GUI": "1"},
            environ_overrides={"werkzeug.server.shutdown": hook},
        )
    assert resp.get_json() == {"status": "shutting_down"}
    hook.assert_called_once_with()
    mock_kill.assert_not_called()


# ---------------------------------------------------------------------------
# /api/diagram/topology
# ---------------------------------------------------------------------------


def test_topology_unavailable(client, monkeypatch):
    """Without gtopt_diagram the endpoint is a 503."""
    monkeypatch.setattr(gui_app, "_DIAGRAM_AVAILABLE", False)
    resp = client.post("/api/diagram/topology", json={"caseData": {}})
    assert resp.status_code == 503


def test_topology_without_auto_info(client, monkeypatch):
    """A builder without auto_info leaves n_total/auto_mode out of meta."""

    class _Builder:  # pylint: disable=too-few-public-methods
        eff_agg = "none"
        eff_vthresh = 0.0
        auto_info = None

        def __init__(self, planning, subsystem, opts):
            del planning, subsystem, opts

        def build(self):
            """Return a dummy model."""
            return "model"

    monkeypatch.setattr(gui_app, "_DIAGRAM_AVAILABLE", True)
    monkeypatch.setattr(gui_app, "_FilterOptions", lambda **kw: kw)
    monkeypatch.setattr(gui_app, "_TopologyBuilder", _Builder)
    monkeypatch.setattr(gui_app, "_model_to_visjs", lambda _m: {"nodes": [1], "edges": []})
    monkeypatch.setattr(gui_app, "_model_to_reactflow", lambda _m: {"nodes": [], "edges": []})
    resp = client.post("/api/diagram/topology", json={"caseData": {"case_name": "x"}})
    assert resp.status_code == 200
    meta = resp.get_json()["meta"]
    assert meta["n_nodes"] == 1
    assert meta["format"] == "visjs"
    assert "auto_mode" not in meta
    assert "n_total" not in meta


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def test_templates_missing_and_broken_files(client, monkeypatch, tmp_path):
    """Missing template files are hidden/404; invalid JSON is a 500."""
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    templates = [
        {
            "slug": "gone",
            "name": "Gone",
            "description": "",
            "category": "x",
            "path": str(tmp_path / "missing.json"),
        },
        {"slug": "nopath", "name": "No path", "description": "", "category": "x"},
        {
            "slug": "broken",
            "name": "Broken",
            "description": "",
            "category": "x",
            "path": str(broken),
        },
    ]
    monkeypatch.setattr(gui_app, "_CASE_TEMPLATES", templates)
    listed = [t["slug"] for t in client.get("/api/templates").get_json()["templates"]]
    assert listed == ["broken"]
    assert client.get("/api/templates/gone").status_code == 404
    assert client.get("/api/templates/nopath").status_code == 404
    resp = client.get("/api/templates/broken")
    assert resp.status_code == 500
    assert resp.get_json() == {"error": "Failed to load template"}


# ---------------------------------------------------------------------------
# Case validation
# ---------------------------------------------------------------------------


def test_validate_case_structural_errors(client):
    """Odd shapes are skipped; missing/duplicate names and refs reported."""
    case = {
        "system": {
            "bus": [
                {"uid": 1, "name": "b1"},
                {"uid": 2, "name": "b1"},
                {"name": ""},
                "not-a-dict",
            ],
            "generator": [
                {"uid": 1, "name": "g1", "bus": 1},
                {"uid": 2, "name": "g2", "bus": 99},
                {"uid": 3, "name": "g3"},
                7,
            ],
            "turbine": [{"uid": 1, "name": "t1"}],
            "mystery": [{"uid": 1, "name": "m"}],
            "notes": "not a list",
        },
        "simulation": {"block_array": [{"uid": 1}]},
    }
    report = client.post("/api/case/validate", json=case).get_json()
    types_ = [e["type"] for e in report["errors"]]
    assert "duplicate_name" in types_
    assert "missing_name" in types_
    assert "duplicate_uid" not in types_
    assert any(e["type"] == "dangling_ref" and e["value"] == 99 for e in report["errors"])
    assert any(
        e["type"] == "missing_required_ref" and e["field"] == "bus" for e in report["errors"]
    )
    # turbine.waterway is optional -> no error for the missing value
    assert not any(e.get("field") == "waterway" for e in report["errors"])
    warn_types = {w["type"] for w in report["warnings"]}
    assert "empty_simulation_array" in warn_types
    assert not any(w["type"] == "isolated_bus" and w["name"] == "1" for w in report["warnings"])


# ---------------------------------------------------------------------------
# Results summary / aggregate / Excel export
# ---------------------------------------------------------------------------


def _fake_module(monkeypatch, dotted: str, **attrs):
    module = types.ModuleType(dotted)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, dotted, module)
    return module


def test_summary_not_installed(client, monkeypatch):
    """Missing gtopt_results_summary -> 503."""
    monkeypatch.setitem(sys.modules, "gtopt_results_summary.summary", None)
    resp = client.post("/api/results/summary", json={"results": {}})
    assert resp.status_code == 503


def test_summary_unexpected_error(client, monkeypatch):
    """An unexpected summary failure is a generic 500 (no detail leaked)."""

    def _boom(*_a, **_k):
        raise RuntimeError("secret detail")

    _fake_module(monkeypatch, "gtopt_results_summary")
    _fake_module(monkeypatch, "gtopt_results_summary.summary", summarize_output_dict=_boom)
    resp = client.post("/api/results/summary", json={"results": {}, "scale_objective": 2})
    assert resp.status_code == 500
    assert resp.get_json() == {"error": "Results summary failed"}


def test_aggregate_explicit_group_by_and_failure(client, monkeypatch):
    """An explicit group_by is filtered to existing columns; errors -> 500."""
    table = {"columns": ["stage", "uid:1"], "data": [[1, 2], [1, 3], [2, 4]]}
    resp = client.post(
        "/api/results/aggregate",
        json={"table": table, "group_by": ["stage", "nope"], "aggregation": "max"},
    )
    body = resp.get_json()
    assert body["columns"] == ["stage", "uid:1"]
    assert body["rows"] == [[1, 3], [2, 4]]

    def _boom(*_a, **_k):
        raise ValueError("bad frame")

    monkeypatch.setattr(gui_app.pd, "DataFrame", _boom)
    resp = client.post("/api/results/aggregate", json={"table": table})
    assert resp.status_code == 500
    assert resp.get_json() == {"error": "Aggregation failed"}


def test_export_excel_not_installed(client, monkeypatch):
    """Missing gtopt_timeseries_export -> 503."""
    monkeypatch.setitem(sys.modules, "gtopt_timeseries_export.exporter", None)
    resp = client.post("/api/results/export/excel", json={"results": {}})
    assert resp.status_code == 503


@pytest.mark.parametrize("unlink_fails", [False, True])
def test_export_excel_failure_cleans_up(client, monkeypatch, caplog, unlink_fails):
    """Exporter errors remove the temp file (best effort) and return 500."""
    dests = []

    def _boom(_results, dest, **_k):
        dests.append(dest)
        raise RuntimeError("secret detail")

    _fake_module(monkeypatch, "gtopt_timeseries_export")
    _fake_module(monkeypatch, "gtopt_timeseries_export.exporter", export_from_dict=_boom)
    if unlink_fails:
        real_unlink = gui_app.os.unlink

        def _unlink(path, *args, **kwargs):
            if dests and path == dests[0]:
                raise OSError("busy")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(gui_app.os, "unlink", _unlink)
    with caplog.at_level(logging.ERROR):
        resp = client.post(
            "/api/results/export/excel", json={"results": {}, "scale_objective": "1"}
        )
    assert resp.status_code == 500
    assert resp.get_json() == {"error": "Excel export failed"}
    if unlink_fails:
        Path(dests[0]).unlink()
    else:
        assert not Path(dests[0]).exists()


def test_export_excel_scale_objective_mismatch(client, monkeypatch):
    """A scale_objective mismatch from the exporter is a client error (400)."""

    class ScaleObjectiveMismatchError(ValueError):
        """Stand-in matched by class name, like the real scripts error."""

    def _boom(_results, _dest, **_k):
        raise ScaleObjectiveMismatchError("mismatch")

    _fake_module(monkeypatch, "gtopt_timeseries_export")
    _fake_module(monkeypatch, "gtopt_timeseries_export.exporter", export_from_dict=_boom)
    resp = client.post("/api/results/export/excel", json={"results": {}, "scale_objective": 3})
    assert resp.status_code == 400
    assert "scale_objective" in resp.get_json()["error"]
