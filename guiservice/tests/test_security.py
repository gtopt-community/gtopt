"""Security tests for the guiservice: SSRF guard, shutdown auth, bind host."""

# pylint: disable=protected-access

import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
            "https://localhost",
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
        resp = client.post("/api/shutdown")
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
        mock_app.app.run.assert_not_called()

    def test_guisrv_default_host_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_HOST", "::1")
        mock_app = MagicMock()
        with patch("sys.argv", ["gtopt_guisrv"]):
            with patch("guiservice.gtopt_guisrv.get_guiservice_dir", return_value=tmp_path):
                with patch("guiservice.gtopt_guisrv.check_dependencies", return_value=True):
                    with patch.dict("sys.modules", {"app": mock_app}):
                        guisrv.main()
        mock_app.app.run.assert_called_once_with(host="::1", port=5001, debug=False)

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
