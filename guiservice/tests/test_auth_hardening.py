"""Tests for GUI authentication, security headers, upload limits and logs."""

# pylint: disable=protected-access

import gzip
import io
import logging
import os
import sys
import zipfile
from unittest.mock import MagicMock, patch

import pytest

import guiservice._security as security
import guiservice._zip_helpers as zh
import guiservice.app as gui_app
from guiservice.app import AUTH_TOKEN_CONFIG, app

TOKEN = "s3cr3t-token-value"


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def authed(monkeypatch):
    """Authentication required with :data:`TOKEN`."""
    monkeypatch.setitem(app.config, AUTH_TOKEN_CONFIG, TOKEN)


# ---------------------------------------------------------------------------
# _security helpers
# ---------------------------------------------------------------------------


class TestResolveToken:
    def test_loopback_auto_is_open(self):
        assert security.resolve_auth_token("127.0.0.1", {}) == (None, False)

    def test_public_auto_generates(self):
        token, generated = security.resolve_auth_token("0.0.0.0", {})  # noqa: S104
        assert generated and token and len(token) >= 32

    def test_env_token_used(self):
        env = {security.AUTH_TOKEN_ENV: " abc "}
        assert security.resolve_auth_token("127.0.0.1", env) == ("abc", False)

    def test_on_generates_on_loopback(self):
        token, generated = security.resolve_auth_token("::1", {security.AUTH_MODE_ENV: "on"})
        assert generated and token

    def test_off_disables(self):
        env = {security.AUTH_MODE_ENV: "off", security.AUTH_TOKEN_ENV: "x"}
        assert security.resolve_auth_token("0.0.0.0", env) == (None, False)  # noqa: S104

    def test_invalid_mode(self):
        with pytest.raises(ValueError, match="invalid"):
            security.auth_mode({security.AUTH_MODE_ENV: "maybe"})

    def test_empty_mode_is_auto(self):
        assert security.auth_mode({security.AUTH_MODE_ENV: " "}) == "auto"


class TestHelpers:
    def test_token_matches(self):
        assert security.token_matches("a", "a")
        assert not security.token_matches("a", "b")
        assert not security.token_matches(None, "a")
        assert not security.token_matches("", "a")

    def test_presented_bearer(self):
        assert security.presented_bearer({security.AUTH_HEADER: " t "}) == "t"
        assert security.presented_bearer({"Authorization": "Bearer t2"}) == "t2"
        assert security.presented_bearer({"Authorization": "Basic x"}) == ""
        assert security.presented_bearer({}) == ""

    @pytest.mark.parametrize(
        ("host", "prefix"),
        [
            ("0.0.0.0", "http://localhost:5001/"),  # noqa: S104
            ("::", "http://localhost:5001/"),
            ("10.0.0.2", "http://10.0.0.2:5001/"),
            ("fe80::1", "http://[fe80::1]:5001/"),
            ("[::1]", "http://[::1]:5001/"),
        ],
    )
    def test_login_url(self, host, prefix):
        url = security.login_url(host, 5001, "a b")
        assert url.startswith(prefix) and url.endswith("?token=a+b")

    def test_without_token_query(self):
        assert security.without_token_query("/", [("token", "x")]) == "/"
        assert security.without_token_query("/p", [("token", "x"), ("a", "1")]) == "/p?a=1"

    def test_cookie_secure_and_secret_key(self):
        assert security.cookie_secure({security.COOKIE_SECURE_ENV: "1"})
        assert not security.cookie_secure({})
        assert security.secret_key({security.SECRET_KEY_ENV: "k"}) == b"k"
        assert len(security.secret_key({})) == 32
        assert security.cookie_secure() in (True, False)

    def test_digest_is_not_token(self):
        assert TOKEN not in security.token_digest(TOKEN)

    def test_redact(self):
        line = (
            "GET /x?token=abc123&user_key=KEY9 Authorization: Bearer zz.yy "
            "password=hunter2 literal-secret-value"
        )
        out = security.redact(line, ["literal-secret-value", "", "ab"])
        for leaked in ("abc123", "KEY9", "zz.yy", "hunter2", "literal-secret-value"):
            assert leaked not in out
        assert "token=***" in out

    def test_redact_job_token_log_format(self):
        out = security.redact("Webservice timeout on status token=deadbeef")
        assert "deadbeef" not in out


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


class TestAuthGuard:
    def test_open_by_default_on_loopback(self, client):
        assert client.get("/api/solve/config").status_code == 200

    def test_api_requires_token(self, client, authed):
        resp = client.get("/api/solve/config")
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "authentication required"

    def test_page_requires_token(self, client, authed):
        resp = client.get("/")
        assert resp.status_code == 401
        assert b"login link" in resp.data

    def test_static_exempt(self, client, authed):
        assert client.get("/static/js/app.js").status_code == 200

    def test_bearer_header(self, client, authed):
        resp = client.get("/api/solve/config", headers={"Authorization": f"Bearer {TOKEN}"})
        assert resp.status_code == 200
        resp = client.get("/api/solve/config", headers={security.AUTH_HEADER: TOKEN})
        assert resp.status_code == 200

    def test_wrong_token(self, client, authed):
        resp = client.get("/api/solve/config", headers={"Authorization": "Bearer nope"})
        assert resp.status_code == 401
        assert client.get("/?token=nope").status_code == 401

    def test_login_link_sets_cookie_and_redirects(self, client, authed):
        resp = client.get(f"/?token={TOKEN}&tab=x")
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/?tab=x"
        cookie = resp.headers["Set-Cookie"]
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
        assert TOKEN not in cookie
        # Session now authenticated.
        assert client.get("/api/solve/config").status_code == 200

    def test_login_only_via_get(self, client, authed):
        resp = client.post(f"/api/solve/config?token={TOKEN}", json={})
        assert resp.status_code == 401

    def test_shutdown_token_still_works(self, client, authed, monkeypatch):
        monkeypatch.setenv("GTOPT_GUI_SHUTDOWN_TOKEN", "sd-token")
        with patch.object(gui_app, "_schedule_shutdown", create=True):
            with patch("os.kill"):
                resp = client.post("/api/shutdown", headers={"X-GTOpt-Shutdown-Token": "sd-token"})
        assert resp.status_code != 401

    def test_shutdown_without_any_token(self, client, authed, monkeypatch):
        monkeypatch.delenv("GTOPT_GUI_SHUTDOWN_TOKEN", raising=False)
        assert client.post("/api/shutdown").status_code == 401


class TestCurrentAuthToken:
    """Token resolution under a WSGI server (``main()`` not called)."""

    def test_open(self, monkeypatch):
        assert gui_app.current_auth_token() is None
        monkeypatch.setenv(security.AUTH_MODE_ENV, "off")
        monkeypatch.setenv(security.AUTH_TOKEN_ENV, "t")
        assert gui_app.current_auth_token() is None

    def test_env_token(self, monkeypatch):
        monkeypatch.setenv(security.AUTH_TOKEN_ENV, "env-tok")
        assert gui_app.current_auth_token() == "env-tok"

    def test_on_generates_once(self, monkeypatch, capsys):
        monkeypatch.setenv(security.AUTH_MODE_ENV, "on")
        monkeypatch.setattr(gui_app, "_generated_token", [])
        first = gui_app.current_auth_token()
        assert first and gui_app.current_auth_token() == first
        assert first in capsys.readouterr().err


# ---------------------------------------------------------------------------
# run_server / _serve
# ---------------------------------------------------------------------------


class TestRunServer:
    def test_public_host_prints_login(self, monkeypatch, capsys):
        with patch.object(app, "run") as run:
            gui_app.run_server("0.0.0.0", 5001)  # noqa: S104
        run.assert_called_once()
        err = capsys.readouterr().err
        assert "Access token required (generated)" in err
        assert "?token=" in err
        assert app.config[AUTH_TOKEN_CONFIG]

    def test_env_token_message(self, monkeypatch, capsys):
        monkeypatch.setenv(security.AUTH_TOKEN_ENV, "abcd")
        with patch.object(app, "run"):
            gui_app.run_server("127.0.0.1", 5001)
        assert f"from ${security.AUTH_TOKEN_ENV}" in capsys.readouterr().err

    def test_auth_off_public_warns(self, monkeypatch, capsys):
        monkeypatch.setenv(security.AUTH_MODE_ENV, "off")
        with patch.object(app, "run"):
            gui_app.run_server("10.1.1.1", 5001)
        assert "authentication disabled" in capsys.readouterr().err
        assert app.config[AUTH_TOKEN_CONFIG] is None

    def test_invalid_auth_mode_exits(self, monkeypatch):
        monkeypatch.setenv(security.AUTH_MODE_ENV, "bogus")
        with pytest.raises(SystemExit) as exc:
            gui_app.run_server("127.0.0.1", 5001)
        assert exc.value.code == 2

    def test_waitress_used_when_available(self, monkeypatch):
        monkeypatch.setenv(gui_app.SERVER_ENV, "auto")
        fake = MagicMock()
        with patch.dict(sys.modules, {"waitress": fake}):
            with patch.object(app, "run") as run:
                gui_app._serve("127.0.0.1", 5001, False)
        fake.serve.assert_called_once()
        run.assert_not_called()

    def test_debug_uses_werkzeug(self, monkeypatch):
        monkeypatch.setenv(gui_app.SERVER_ENV, "auto")
        fake = MagicMock()
        with patch.dict(sys.modules, {"waitress": fake}):
            with patch.object(app, "run") as run:
                gui_app._serve("127.0.0.1", 5001, True)
        run.assert_called_once()
        fake.serve.assert_not_called()

    def test_auto_falls_back_without_waitress(self, monkeypatch, caplog):
        monkeypatch.setenv(gui_app.SERVER_ENV, "auto")
        with patch.dict(sys.modules, {"waitress": None}):
            with patch.object(app, "run") as run:
                with caplog.at_level(logging.WARNING):
                    gui_app._serve("10.0.0.1", 5001, False)
        run.assert_called_once()
        assert "development" in caplog.text

    def test_waitress_required_but_missing(self, monkeypatch):
        monkeypatch.setenv(gui_app.SERVER_ENV, "waitress")
        with patch.dict(sys.modules, {"waitress": None}):
            with pytest.raises(SystemExit):
                gui_app._serve("127.0.0.1", 5001, False)

    def test_invalid_server_choice(self, monkeypatch):
        monkeypatch.setenv(gui_app.SERVER_ENV, "nginx")
        with pytest.raises(SystemExit):
            gui_app._serve("127.0.0.1", 5001, False)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    def test_headers_on_page(self, client):
        resp = client.get("/")
        csp = resp.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp and "connect-src 'self'" in csp
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Referrer-Policy"] == "no-referrer"

    def test_api_no_store(self, client):
        resp = client.get("/api/solve/config")
        assert resp.headers["Cache-Control"] == "no-store"

    def test_page_scripts_local_or_sri(self, client):
        html = client.get("/").get_data(as_text=True)
        assert "cdn.jsdelivr.net/npm/chart.js" not in html
        assert 'integrity="sha384-' in html


# ---------------------------------------------------------------------------
# Upload limits
# ---------------------------------------------------------------------------


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


class TestUploadLimits:
    def test_env_limit_parsing(self, monkeypatch):
        monkeypatch.setenv(zh.MAX_MEMBERS_ENV, "abc")
        assert zh._env_limit(zh.MAX_MEMBERS_ENV, 7) == 7
        monkeypatch.setenv(zh.MAX_MEMBERS_ENV, "-3")
        assert zh._env_limit(zh.MAX_MEMBERS_ENV, 7) == 7
        monkeypatch.setenv(zh.MAX_MEMBERS_ENV, "9")
        assert zh._env_limit(zh.MAX_MEMBERS_ENV, 7) == 9

    def test_too_many_members(self, monkeypatch):
        monkeypatch.setenv(zh.MAX_MEMBERS_ENV, "2")
        data = _zip_bytes({"a.json": b"{}", "b.csv": b"x", "c.csv": b"y"})
        with pytest.raises(zh.UploadLimitError, match="members"):
            zh._parse_uploaded_zip(data)

    def test_declared_size_too_large(self, monkeypatch):
        monkeypatch.setenv(zh.MAX_UNCOMPRESSED_ENV, "100")
        data = _zip_bytes({"a.json": b"{}" + b" " * 200})
        with pytest.raises(zh.UploadLimitError, match="expands"):
            zh._parse_uploaded_zip(data)

    def test_gzip_member_bomb(self, monkeypatch):
        monkeypatch.setenv(zh.MAX_UNCOMPRESSED_ENV, str(64 * 1024))
        bomb = gzip.compress(b"a," * (512 * 1024))
        data = _zip_bytes({"output/Gen/x_sol.csv.gz": bomb})
        with pytest.raises(zh.UploadLimitError):
            zh._parse_results_zip(data)

    def test_zstd_member_bomb(self, monkeypatch):
        zstandard = pytest.importorskip("zstandard")
        monkeypatch.setenv(zh.MAX_UNCOMPRESSED_ENV, str(64 * 1024))
        bomb = zstandard.ZstdCompressor().compress(b"a," * (512 * 1024))
        data = _zip_bytes({"output/Gen/x_sol.csv.zst": bomb})
        with pytest.raises(zh.UploadLimitError):
            zh._parse_results_zip(data)

    def test_small_gzip_member_ok(self):
        rows = zh._decode_csv_member("x.csv.gz", gzip.compress(b"a,b\n1,2\n"))
        assert rows == [["a", "b"], ["1", "2"]]

    def test_upload_route_413(self, client, monkeypatch):
        monkeypatch.setenv(zh.MAX_MEMBERS_ENV, "1")
        data = _zip_bytes({"a.json": b"{}", "b.csv": b"x"})
        resp = client.post(
            "/api/case/upload",
            data={"file": (io.BytesIO(data), "case.zip")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 413
        assert zh.MAX_MEMBERS_ENV in resp.get_json()["error"]

    def test_results_route_bad_zip(self, client):
        resp = client.post(
            "/api/results/upload",
            data={"file": (io.BytesIO(b"not a zip"), "r.zip")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


class TestLogs:
    def test_default_log_file_not_tmp(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert gui_app.default_log_file() == os.path.join(str(tmp_path), "gtopt", "guiservice.log")
        monkeypatch.delenv("XDG_STATE_HOME")
        assert gui_app.default_log_file().endswith(
            os.path.join(".local", "state", "gtopt", "guiservice.log")
        )

    def test_recent_logs_redacted(self, client, monkeypatch):
        monkeypatch.setenv("GTOPT_WEBSERVICE_API_TOKEN", "api-token-xyz")
        monkeypatch.setitem(app.config, AUTH_TOKEN_CONFIG, None)
        app.logger.warning("forwarding api-token-xyz for token=jobtok123")
        text = "\n".join(client.get("/api/logs").get_json()["logs"])
        assert "forwarding" in text
        assert "api-token-xyz" not in text
        assert "jobtok123" not in text
