"""Security tests for the guiservice: SSRF guard, shutdown auth, bind host."""

# pylint: disable=protected-access

import io
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask.testing import FlaskClient

import guiservice.app as gui_app
import guiservice.gtopt_guisrv as guisrv
from guiservice.app import app

_GUISERVICE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.delenv("GTOPT_GUI_ALLOWED_WEBSERVICE_HOSTS", raising=False)
    monkeypatch.delenv("GTOPT_WEBSERVICE_URL", raising=False)
    monkeypatch.delenv("GTOPT_GUI_SHUTDOWN_TOKEN", raising=False)
    monkeypatch.delenv("GTOPT_WEBSERVICE_API_TOKEN", raising=False)
    saved = gui_app._webservice_url
    with app.test_client() as c:
        yield c
    gui_app._webservice_url = saved


def _plain_client():
    """A test client that does NOT add the X-GTOpt-GUI header."""
    return FlaskClient(app, app.response_class, use_cookies=True)


def _set_url(client, url):
    return client.post("/api/solve/config", json={"webservice_url": url})


# ---------------------------------------------------------------------------
# SSRF: POST /api/solve/config
# ---------------------------------------------------------------------------


class TestWebserviceUrlSsrf:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://127.0.0.2:3000/",
            "http://[::1]:3000",
        ],
    )
    def test_loopback_urls_accepted(self, client, url):
        resp = _set_url(client, url)
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["webservice_url"] == url.rstrip("/")

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data",  # cloud metadata
            "http://10.0.0.5:3000",
            "http://192.168.1.10",
            "http://example.com:3000",
            "http://0.0.0.0:3000",
            "http://localhost.evil.com",
            "http://127.0.0.1.nip.io",
        ],
    )
    def test_non_allowlisted_hosts_rejected(self, client, url):
        before = gui_app._webservice_url
        resp = _set_url(client, url)
        assert resp.status_code == 400
        assert "not allowed" in resp.get_json()["error"]
        assert gui_app._webservice_url == before

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "gopher://localhost:70/",
            "ftp://localhost/",
            "localhost:3000",
            "//localhost:3000",
            "http://user:pass@localhost:3000",
            "http://localhost:3000/?x=1",
            "http://localhost:3000/#frag",
            "http://localhost:99999",
            "http://",
        ],
    )
    def test_bad_schemes_and_shapes_rejected(self, client, url):
        resp = _set_url(client, url)
        assert resp.status_code == 400

    def test_non_string_rejected(self, client):
        resp = client.post("/api/solve/config", json={"webservice_url": 42})
        assert resp.status_code == 400

    def test_allowlist_env_host(self, client, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_ALLOWED_WEBSERVICE_HOSTS", "solver.internal, other:8080")
        assert _set_url(client, "https://solver.internal:3000").status_code == 200
        assert _set_url(client, "http://other:8080").status_code == 200
        # host:port entries only allow that port
        assert _set_url(client, "http://other:9090").status_code == 400

    def test_configured_env_url_host_allowed(self, client, monkeypatch):
        monkeypatch.setenv("GTOPT_WEBSERVICE_URL", "http://ws.example.org:3000")
        assert _set_url(client, "http://ws.example.org:3000").status_code == 200

    def test_validate_webservice_url_normalizes(self):
        url, err = gui_app.validate_webservice_url("  http://LOCALHOST:3000/api/ ")
        assert err is None
        assert url == "http://LOCALHOST:3000/api"


# ---------------------------------------------------------------------------
# Job-token path injection on proxied routes
# ---------------------------------------------------------------------------


class TestJobTokenValidation:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/solve/status/..",
            "/api/solve/results/a%3Fb",
            "/api/solve/job_logs/a.b",
            "/api/solve/monitor/a%23b",
        ],
    )
    @patch("guiservice.app.http_requests.get")
    def test_bad_tokens_rejected(self, mock_get, client, path):
        resp = client.get(path)
        assert resp.status_code in (400, 404)
        mock_get.assert_not_called()

    @patch("guiservice.app.http_requests.post")
    def test_bad_stop_token_rejected(self, mock_post, client):
        resp = client.post("/api/solve/stop/a.b")
        assert resp.status_code == 400
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Webservice API token forwarding
# ---------------------------------------------------------------------------


@patch("guiservice.app.http_requests.get")
def test_webservice_token_forwarded(mock_get, client, monkeypatch):
    monkeypatch.setenv("GTOPT_WEBSERVICE_API_TOKEN", "tok123")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "ok"}
    mock_get.return_value = mock_resp
    client.get("/api/solve/ping")
    assert mock_get.call_args.kwargs["headers"] == {"Authorization": "Bearer tok123"}


@patch("guiservice.app.http_requests.get")
def test_no_webservice_token_no_header(mock_get, client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "ok"}
    mock_get.return_value = mock_resp
    client.get("/api/solve/ping")
    assert mock_get.call_args.kwargs["headers"] == {}


# ---------------------------------------------------------------------------
# POST /api/shutdown
# ---------------------------------------------------------------------------

GUI_HDR = {"X-GTOpt-GUI": "1"}


class TestShutdownAuth:
    @patch("os.kill")
    def test_loopback_with_gui_header_allowed(self, mock_kill, client):
        resp = client.post("/api/shutdown", headers=GUI_HDR)
        assert resp.status_code == 200
        mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)

    @patch("os.kill")
    def test_loopback_without_gui_header_rejected(self, mock_kill, client):
        """A cross-site form POST from the user's browser cannot add the header."""
        del client  # env cleanup only
        resp = _plain_client().post("/api/shutdown")
        assert resp.status_code == 403
        mock_kill.assert_not_called()

    @patch("os.kill")
    def test_remote_client_rejected(self, mock_kill, client):
        resp = client.post(
            "/api/shutdown", headers=GUI_HDR, environ_base={"REMOTE_ADDR": "10.1.2.3"}
        )
        assert resp.status_code == 403
        mock_kill.assert_not_called()

    @patch("os.kill")
    def test_proxied_loopback_rejected(self, mock_kill, client):
        resp = client.post("/api/shutdown", headers={**GUI_HDR, "X-Forwarded-For": "203.0.113.9"})
        assert resp.status_code == 403
        mock_kill.assert_not_called()

    @patch("os.kill")
    def test_remote_with_valid_token_allowed(self, mock_kill, client, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_SHUTDOWN_TOKEN", "s3cret")
        resp = client.post(
            "/api/shutdown",
            headers={"X-GTOpt-Shutdown-Token": "s3cret"},
            environ_base={"REMOTE_ADDR": "10.1.2.3"},
        )
        assert resp.status_code == 200
        mock_kill.assert_called_once()

    @patch("os.kill")
    def test_bearer_token_allowed(self, mock_kill, client, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_SHUTDOWN_TOKEN", "s3cret")
        resp = client.post(
            "/api/shutdown",
            headers={"Authorization": "Bearer s3cret"},
            environ_base={"REMOTE_ADDR": "10.1.2.3"},
        )
        assert resp.status_code == 200
        mock_kill.assert_called_once()

    @patch("os.kill")
    def test_wrong_token_rejected(self, mock_kill, client, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_SHUTDOWN_TOKEN", "s3cret")
        resp = client.post("/api/shutdown", headers={**GUI_HDR, "X-GTOpt-Shutdown-Token": "wrong"})
        assert resp.status_code == 403
        mock_kill.assert_not_called()

    @patch("os.kill")
    def test_token_without_configured_secret_ignored(self, mock_kill, client):
        resp = client.post(
            "/api/shutdown",
            headers={"X-GTOpt-Shutdown-Token": "anything"},
            environ_base={"REMOTE_ADDR": "10.1.2.3"},
        )
        assert resp.status_code == 403
        mock_kill.assert_not_called()

    def test_constant_time_compare_used(self, client, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_SHUTDOWN_TOKEN", "s3cret")
        with patch("guiservice.app.hmac.compare_digest", return_value=False) as cmp:
            resp = client.post("/api/shutdown", headers={"X-GTOpt-Shutdown-Token": "s3cret"})
        assert resp.status_code == 403
        cmp.assert_called_once()


# ---------------------------------------------------------------------------
# Default bind host and debug-mode refusal
# ---------------------------------------------------------------------------


class TestBindHost:
    def test_app_main_defaults_to_loopback(self, monkeypatch):
        monkeypatch.delenv("GTOPT_GUI_HOST", raising=False)
        monkeypatch.delenv("FLASK_DEBUG", raising=False)
        with patch.object(gui_app.app, "run") as run:
            gui_app.main()
        assert run.call_args.kwargs["host"] == "127.0.0.1"
        assert run.call_args.kwargs["debug"] is False

    def test_app_main_honours_host_env(self, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_HOST", "0.0.0.0")
        monkeypatch.delenv("FLASK_DEBUG", raising=False)
        with patch.object(gui_app.app, "run") as run:
            gui_app.main()
        assert run.call_args.kwargs["host"] == "0.0.0.0"

    def test_app_main_refuses_debug_on_public_host(self, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_HOST", "0.0.0.0")
        monkeypatch.setenv("FLASK_DEBUG", "1")
        with patch.object(gui_app.app, "run") as run:
            with pytest.raises(SystemExit) as exc:
                gui_app.main()
        assert exc.value.code == 2
        run.assert_not_called()

    def test_app_main_allows_debug_on_loopback(self, monkeypatch):
        monkeypatch.delenv("GTOPT_GUI_HOST", raising=False)
        monkeypatch.setenv("FLASK_DEBUG", "1")
        with patch.object(gui_app.app, "run") as run:
            gui_app.main()
        assert run.call_args.kwargs == {"host": "127.0.0.1", "port": 5001, "debug": True}

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("127.0.0.1", True),
            ("127.9.9.9", True),
            ("localhost", True),
            ("::1", True),
            ("[::1]", True),
            ("0.0.0.0", False),
            ("::", False),
            ("10.0.0.1", False),
            ("example.com", False),
            ("", False),
            (None, False),
        ],
    )
    def test_is_loopback_host(self, host, expected):
        assert gui_app.is_loopback_host(host) is expected
        assert guisrv.is_loopback_host(host) is expected

    def test_guisrv_refuses_debug_on_public_host(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GTOPT_GUI_HOST", raising=False)
        mock_app = MagicMock()
        with patch("sys.argv", ["gtopt_guisrv", "--debug", "--host", "0.0.0.0"]):
            with patch("guiservice.gtopt_guisrv.get_guiservice_dir", return_value=tmp_path):
                with patch("guiservice.gtopt_guisrv.check_dependencies", return_value=True):
                    with patch.dict("sys.modules", {"app": mock_app}):
                        with pytest.raises(SystemExit) as exc:
                            guisrv.main()
        assert exc.value.code == 2
        mock_app.run_server.assert_not_called()

    def test_guisrv_default_host_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_HOST", "::1")
        mock_app = MagicMock()
        with patch("sys.argv", ["gtopt_guisrv"]):
            with patch("guiservice.gtopt_guisrv.get_guiservice_dir", return_value=tmp_path):
                with patch("guiservice.gtopt_guisrv.check_dependencies", return_value=True):
                    with patch.dict("sys.modules", {"app": mock_app}):
                        guisrv.main()
        mock_app.run_server.assert_called_once_with(host="::1", port=5001, debug=False)

    def test_guisrv_help_mentions_loopback_default(self):
        result = subprocess.run(
            [sys.executable, str(_GUISERVICE_DIR / "gtopt_guisrv.py"), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "127.0.0.1" in result.stdout
        assert "0.0.0.0" not in result.stdout


# ---------------------------------------------------------------------------
# Loopback webservice URLs are pinned to the configured port
# ---------------------------------------------------------------------------


class TestLoopbackWebservicePort:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:4444",
            "http://127.0.0.1:22",
            "http://[::1]:8080",
            "https://localhost",
            "http://localhost",
        ],
    )
    def test_other_loopback_ports_rejected(self, client, url):
        before = gui_app._webservice_url
        resp = _set_url(client, url)
        assert resp.status_code == 400
        assert "port" in resp.get_json()["error"]
        assert gui_app._webservice_url == before

    def test_env_url_port_allowed(self, client, monkeypatch):
        monkeypatch.setenv("GTOPT_WEBSERVICE_URL", "http://127.0.0.1:3456")
        assert _set_url(client, "http://localhost:3456").status_code == 200
        assert _set_url(client, "http://localhost:3000").status_code == 400

    def test_env_url_default_scheme_port(self, client, monkeypatch):
        monkeypatch.setenv("GTOPT_WEBSERVICE_URL", "https://localhost")
        assert _set_url(client, "https://127.0.0.1").status_code == 200
        assert _set_url(client, "https://127.0.0.1:443").status_code == 200
        assert _set_url(client, "http://127.0.0.1").status_code == 400

    def test_unparsable_env_url_falls_back_to_listed_ports(self, client, monkeypatch):
        monkeypatch.setenv("GTOPT_WEBSERVICE_URL", "http://localhost:99999")
        monkeypatch.setenv("GTOPT_GUI_ALLOWED_WEBSERVICE_HOSTS", "localhost:3001")
        assert _set_url(client, "http://localhost:3001").status_code == 200
        assert _set_url(client, "http://localhost:3000").status_code == 400

    def test_allowlisted_loopback_port(self, client, monkeypatch):
        monkeypatch.setenv(
            "GTOPT_GUI_ALLOWED_WEBSERVICE_HOSTS", "example.org, , 127.0.0.1:3001, x:bad"
        )
        assert _set_url(client, "http://127.0.0.1:3001").status_code == 200
        assert _set_url(client, "http://localhost:3001").status_code == 200
        assert _set_url(client, "http://localhost:3002").status_code == 400

    def test_bare_loopback_allowlist_entry_allows_any_port(self, client, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_ALLOWED_WEBSERVICE_HOSTS", "localhost")
        assert _set_url(client, "http://127.0.0.1:4444").status_code == 200


# ---------------------------------------------------------------------------
# Host allowlist (DNS rebinding) and X-GTOpt-GUI on state-changing requests
# ---------------------------------------------------------------------------


class TestHostAllowlist:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("GTOPT_GUI_ALLOWED_HOSTS", raising=False)
        monkeypatch.delenv("GTOPT_GUI_HOST", raising=False)
        monkeypatch.setenv("GTOPT_GUI_PORT", "5001")
        monkeypatch.delitem(app.config, "GTOPT_GUI_PORT", raising=False)

    @pytest.mark.parametrize(
        "host",
        ["localhost", "localhost:5001", "127.0.0.1:5001", "[::1]:5001", "LOCALHOST:5001"],
    )
    def test_loopback_hosts_accepted(self, client, host):
        resp = client.get("/api/solve/config", headers={"Host": host})
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        "host",
        [
            "attacker.example:5001",
            "attacker.example",
            "localhost:5002",
            "127.0.0.1:81",
            "10.0.0.1:5001",
            "localhost:notaport",
            ":5001",
        ],
    )
    def test_foreign_hosts_rejected(self, client, host):
        resp = client.get("/api/solve/config", headers={"Host": host})
        assert resp.status_code == 403
        assert "GTOPT_GUI_ALLOWED_HOSTS" in resp.get_json()["error"]

    def test_rejected_on_post_too(self, client):
        resp = client.post("/api/case/validate", json={}, headers={"Host": "evil.example:5001"})
        assert resp.status_code == 403

    def test_extra_hosts_from_env(self, client, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_ALLOWED_HOSTS", "gui.lan, 10.0.0.7:8000, [fd00::1]")
        assert client.get("/api/solve/config", headers={"Host": "gui.lan:5001"}).status_code == 200
        assert client.get("/api/solve/config", headers={"Host": "10.0.0.7:8000"}).status_code == 200
        assert client.get("/api/solve/config", headers={"Host": "10.0.0.7:9000"}).status_code == 403
        assert (
            client.get("/api/solve/config", headers={"Host": "[fd00::1]:5001"}).status_code == 200
        )

    def test_concrete_bind_host_accepted(self, client, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_HOST", "192.168.0.5")
        assert (
            client.get("/api/solve/config", headers={"Host": "192.168.0.5:5001"}).status_code == 200
        )

    def test_wildcard_bind_host_not_a_free_pass(self, client, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_HOST", "0.0.0.0")
        assert client.get("/api/solve/config", headers={"Host": "0.0.0.0:5001"}).status_code == 403

    def test_app_config_port_wins(self, client, monkeypatch):
        monkeypatch.setitem(app.config, "GTOPT_GUI_PORT", 6000)
        assert client.get("/", headers={"Host": "localhost:6000"}).status_code == 200
        assert client.get("/", headers={"Host": "localhost:5001"}).status_code == 403

    def test_no_configured_port_accepts_any_loopback_port(self, client, monkeypatch):
        monkeypatch.delenv("GTOPT_GUI_PORT", raising=False)
        assert client.get("/", headers={"Host": "127.0.0.1:43210"}).status_code == 200

    def test_bad_configured_port_ignored(self, client, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_PORT", "abc")
        assert client.get("/", headers={"Host": "127.0.0.1:43210"}).status_code == 200

    def test_host_header_allowed_helper(self):
        assert gui_app.host_header_allowed("localhost:5001")
        assert not gui_app.host_header_allowed("")
        assert not gui_app.host_header_allowed(None)


class TestGuiHeaderRequired:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/solve/config",
            "/api/case/validate",
            "/api/case/upload",
            "/api/results/upload",
            "/api/solve/stop/abc",
            "/api/solve/submit",
            "/api/shutdown",
        ],
    )
    def test_post_without_header_rejected(self, client, path):
        del client  # env cleanup only
        resp = _plain_client().post(path, json={"webservice_url": "http://localhost:3000"})
        assert resp.status_code == 403
        assert "X-GTOpt-GUI" in resp.get_json()["error"]

    def test_post_with_header_reaches_route(self, client):
        del client
        resp = _plain_client().post(
            "/api/solve/config",
            json={"webservice_url": "http://localhost:3000"},
            headers={"X-GTOpt-GUI": "1"},
        )
        assert resp.status_code == 200

    def test_get_without_header_allowed(self, client):
        del client
        assert _plain_client().get("/api/solve/config").status_code == 200

    def test_multipart_upload_without_header_rejected_before_parsing(self, client):
        del client
        with patch("guiservice.app._parse_uploaded_zip") as parse:
            resp = _plain_client().post(
                "/api/case/upload",
                data={"file": (io.BytesIO(b"PK"), "case.zip")},
                content_type="multipart/form-data",
            )
        assert resp.status_code == 403
        parse.assert_not_called()

    @patch("os.kill")
    def test_shutdown_token_client_needs_no_gui_header(self, mock_kill, client, monkeypatch):
        del client
        monkeypatch.setenv("GTOPT_GUI_SHUTDOWN_TOKEN", "s3cret")
        resp = _plain_client().post(
            "/api/shutdown",
            headers={"X-GTOpt-Shutdown-Token": "s3cret"},
            environ_base={"REMOTE_ADDR": "10.1.2.3"},
        )
        assert resp.status_code == 200
        mock_kill.assert_called_once()

    def test_frontend_sends_header(self):
        """The bundled UI adds the header to every same-origin fetch."""
        app_js = (_GUISERVICE_DIR / "static" / "js" / "app.js").read_text(encoding="utf-8")
        assert 'const GUI_REQUEST_HEADER = "X-GTOpt-GUI";' in app_js
        assert "window.fetch = function guiFetch(" in app_js
        api_ts = (_GUISERVICE_DIR.parent / "guiservice-plus" / "src" / "lib" / "api.ts").read_text(
            encoding="utf-8"
        )
        assert "'X-GTOpt-GUI': '1'" in api_ts
