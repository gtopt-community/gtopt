"""Coverage tests for the gtopt_gui launcher.

Every external effect (sockets, subprocesses, browsers, HTTP, signals,
atexit) is replaced by an in-process fake: no real server is started and
no browser is opened.
"""

import io
import json
import signal
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import gtopt_gui as gg  # pylint: disable=wrong-import-position,import-error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal context-manager stand-in for ``urlopen`` responses."""

    def __init__(self, status=200, payload=b"{}"):
        self.status = status
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeProc:
    """Fake ``Popen`` object with scripted ``poll`` results."""

    def __init__(self, polls=None, pid=4242, wait_raises=False):
        self._polls = list(polls or [])
        self.pid = pid
        self.wait_raises = wait_raises
        self.terminated = False
        self.killed = False
        self.waits = []

    def poll(self):
        if self._polls:
            return self._polls.pop(0)
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.wait_raises and len(self.waits) == 1:
            raise subprocess.TimeoutExpired("cmd", timeout)
        return 0


def _install_prefix(tmp_path):
    """Create ``<prefix>/share/gtopt/guiservice/gtopt_gui.py`` and return it."""
    gui_dir = tmp_path / "prefix" / "share" / "gtopt" / "guiservice"
    gui_dir.mkdir(parents=True)
    fake_file = gui_dir / "gtopt_gui.py"
    fake_file.write_text("# fake\n")
    return tmp_path / "prefix", fake_file


@pytest.fixture(name="isolated_paths")
def fixture_isolated_paths(tmp_path, monkeypatch):
    """Point __file__, sys.prefix and HOME at empty temporary locations."""
    prefix, fake_file = _install_prefix(tmp_path)
    monkeypatch.setattr(gg, "__file__", str(fake_file))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "sysprefix"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return prefix


# ---------------------------------------------------------------------------
# Directory / binary discovery
# ---------------------------------------------------------------------------


def test_get_guiservice_dir_falls_back_to_sys_prefix(isolated_paths, tmp_path):
    """Without a sibling app.py, the sys.prefix share dir is used."""
    del isolated_paths
    share = tmp_path / "sysprefix" / "share" / "gtopt" / "guiservice"
    share.mkdir(parents=True)
    (share / "app.py").write_text("")
    assert gg.get_guiservice_dir() == share


def test_get_guiservice_dir_exits_when_missing(isolated_paths, capsys):
    """No app.py anywhere -> error message and exit code 1."""
    del isolated_paths
    for loc in ("/usr/local/share/gtopt/guiservice", "/opt/gtopt/guiservice"):
        if (Path(loc) / "app.py").exists():
            pytest.skip(f"system install present at {loc}")
    with pytest.raises(SystemExit) as exc:
        gg.get_guiservice_dir()
    assert exc.value.code == 1
    assert "Cannot find guiservice app.py" in capsys.readouterr().err


def test_get_webservice_dir_found_in_install_prefix(isolated_paths):
    """A gtopt_websrv launcher plus share/gtopt/webservice is detected."""
    prefix = isolated_paths
    (prefix / "bin").mkdir()
    (prefix / "bin" / "gtopt_websrv").write_text("")
    web = prefix / "share" / "gtopt" / "webservice"
    web.mkdir(parents=True)
    (web / "package.json").write_text("{}")
    assert gg.get_webservice_dir() == web


def test_get_webservice_dir_launcher_without_package(isolated_paths, monkeypatch):
    """A launcher without any webservice package.json yields None."""
    prefix = isolated_paths
    (prefix / "bin").mkdir()
    (prefix / "bin" / "gtopt_websrv").write_text("")
    real_exists = Path.exists

    def _exists(self):
        if str(self).startswith(("/usr/", "/opt/")):
            return False
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _exists)
    assert gg.get_webservice_dir() is None


def test_find_websrv_script_in_install_prefix(isolated_paths):
    """The launcher next to the install prefix is returned."""
    prefix = isolated_paths
    (prefix / "bin").mkdir()
    script = prefix / "bin" / "gtopt_websrv"
    script.write_text("")
    assert gg.find_websrv_script() == script


def test_find_websrv_script_none(isolated_paths, monkeypatch):
    """No launcher anywhere -> None."""
    del isolated_paths
    real_exists = Path.exists

    def _exists(self):
        if str(self).startswith(("/usr/", "/opt/")):
            return False
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _exists)
    assert gg.find_websrv_script() is None


def test_find_gtopt_binary_from_path(monkeypatch):
    """shutil.which hit is returned directly."""
    monkeypatch.setattr(gg.shutil, "which", lambda _name: "/x/bin/gtopt")
    assert gg.find_gtopt_binary() == Path("/x/bin/gtopt")


def test_find_gtopt_binary_install_prefix(isolated_paths, monkeypatch):
    """Without PATH hit, the install-prefix binary is found."""
    prefix = isolated_paths
    monkeypatch.setattr(gg.shutil, "which", lambda _name: None)
    (prefix / "bin").mkdir()
    binary = prefix / "bin" / "gtopt"
    binary.write_text("")
    assert gg.find_gtopt_binary() == binary


def test_find_gtopt_binary_none(isolated_paths, monkeypatch):
    """No binary anywhere -> None (directories named gtopt are skipped)."""
    prefix = isolated_paths
    monkeypatch.setattr(gg.shutil, "which", lambda _name: None)
    (prefix / "bin" / "gtopt").mkdir(parents=True)  # a dir, not a file
    real_exists = Path.exists

    def _exists(self):
        if str(self).startswith(("/usr/", "/opt/")):
            return False
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _exists)
    assert gg.find_gtopt_binary() is None


# ---------------------------------------------------------------------------
# HTTP probes
# ---------------------------------------------------------------------------


def test_wait_for_service_ready(monkeypatch):
    """A 200 response from /api/schemas means ready."""
    monkeypatch.setattr(gg.urllib.request, "urlopen", lambda *_a, **_k: _FakeResponse())
    assert gg.wait_for_service(1234, timeout=5) is True


def test_wait_for_service_non_200_then_timeout(monkeypatch):
    """Non-200 and connection errors keep polling until timeout."""
    clock = iter([0.0, 0.0, 0.1, 0.2, 100.0])
    monkeypatch.setattr(gg.time, "time", lambda: next(clock))
    sleeps = []
    monkeypatch.setattr(gg.time, "sleep", sleeps.append)
    calls = []

    def _urlopen(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            return _FakeResponse(status=503)
        raise urllib.error.URLError("down")

    monkeypatch.setattr(gg.urllib.request, "urlopen", _urlopen)
    assert gg.wait_for_service(1234, timeout=1) is False
    assert sleeps == [0.5, 0.5]


def test_query_ping_non_200_returns_none(monkeypatch):
    """A non-200 ping response yields None."""
    monkeypatch.setattr(gg.urllib.request, "urlopen", lambda *_a, **_k: _FakeResponse(status=500))
    assert gg.query_webservice_ping("http://x") is None


def test_check_webservice_api_non_200_falls_back(monkeypatch):
    """/api non-200 falls back to ping; a ping with wrong status is False."""

    def _urlopen(url, **_k):
        if url.endswith("/api"):
            return _FakeResponse(status=500)
        return _FakeResponse(payload=json.dumps({"status": "bad"}).encode())

    monkeypatch.setattr(gg.urllib.request, "urlopen", _urlopen)
    assert gg.check_webservice_api("http://x") is False


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------


def test_open_browser_darwin_app_mode(monkeypatch):
    """macOS app mode launches Chrome through ``open``."""
    launched = []
    monkeypatch.setattr(gg.sys, "platform", "darwin")
    monkeypatch.setattr(gg.subprocess, "Popen", lambda cmd, **_k: launched.append(cmd))
    monkeypatch.setattr(gg.webbrowser, "open", lambda *_a, **_k: pytest.fail("fallback"))
    gg.open_browser("http://u/", app_mode=True)
    assert launched[0][0] == "open"
    assert "--app=http://u/" in launched[0]


def test_open_browser_darwin_falls_back(monkeypatch):
    """macOS without ``open`` falls back to the default browser."""
    opened = []

    def _popen(*_a, **_k):
        raise FileNotFoundError

    monkeypatch.setattr(gg.sys, "platform", "darwin")
    monkeypatch.setattr(gg.subprocess, "Popen", _popen)
    monkeypatch.setattr(gg.webbrowser, "open", lambda url, new: opened.append((url, new)))
    gg.open_browser("http://u/", app_mode=True)
    assert opened == [("http://u/", 1)]


def test_open_browser_linux_second_browser(monkeypatch):
    """Linux app mode tries browsers in order until one launches."""
    tried = []

    def _popen(cmd, **_k):
        tried.append(cmd[0])
        if cmd[0] == "google-chrome":
            raise FileNotFoundError

    monkeypatch.setattr(gg.sys, "platform", "linux")
    monkeypatch.setattr(gg.subprocess, "Popen", _popen)
    monkeypatch.setattr(gg.webbrowser, "open", lambda *_a, **_k: pytest.fail("fallback"))
    gg.open_browser("http://u/", app_mode=True)
    assert tried == ["google-chrome", "chromium-browser"]


def test_open_browser_other_platform_app_mode(monkeypatch):
    """Other platforms ignore app mode and use the default browser."""
    opened = []
    monkeypatch.setattr(gg.sys, "platform", "win32")
    monkeypatch.setattr(gg.webbrowser, "open", lambda url, new: opened.append(url))
    gg.open_browser("http://u/", app_mode=True)
    assert opened == ["http://u/"]


# ---------------------------------------------------------------------------
# verify_webservice_api
# ---------------------------------------------------------------------------


def test_verify_webservice_api_logs_ping_details(monkeypatch, tmp_path, capsys):
    """Ping details are printed; an unwritable log file is ignored."""
    monkeypatch.setattr(gg, "check_webservice_api", lambda *_a, **_k: False)
    monkeypatch.setattr(
        gg,
        "query_webservice_ping",
        lambda *_a, **_k: {
            "status": "ok",
            "service": "gtopt-webservice",
            "gtopt_version": "9.9",
            "gtopt_bin": "/b/gtopt",
        },
    )
    assert gg.verify_webservice_api("http://x", log_file=str(tmp_path)) is True
    out = capsys.readouterr().out
    assert "gtopt version: 9.9" in out
    assert "gtopt binary: /b/gtopt" in out
    assert "FAILED" in out


def test_verify_webservice_api_ping_without_details(monkeypatch, capsys):
    """A minimal ok ping without version/bin prints no detail lines."""
    monkeypatch.setattr(gg, "check_webservice_api", lambda *_a, **_k: True)
    monkeypatch.setattr(gg, "query_webservice_ping", lambda *_a, **_k: {"status": "ok"})
    assert gg.verify_webservice_api("http://x") is True
    out = capsys.readouterr().out
    assert "gtopt version" not in out
    assert "gtopt binary" not in out


# ---------------------------------------------------------------------------
# start_webservice
# ---------------------------------------------------------------------------


def test_start_webservice_not_built(tmp_path, capsys):
    """Missing .next directory -> (None, None) with a warning."""
    assert gg.start_webservice(tmp_path, 3000) == (None, None)
    assert "Webservice not built" in capsys.readouterr().err


def test_start_webservice_node_launcher_with_options(tmp_path, monkeypatch):
    """gtopt_websrv.js is preferred and receives all options."""
    (tmp_path / ".next").mkdir()
    (tmp_path / "gtopt_websrv.js").write_text("")
    captured = {}

    def _popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return "proc"

    monkeypatch.setattr(gg.subprocess, "Popen", _popen)
    log = tmp_path / "logs" / "web.log"
    log.parent.mkdir()
    proc, handle = gg.start_webservice(
        tmp_path, 3100, gtopt_bin="/b/gtopt", data_dir="/d", log_file=str(log)
    )
    try:
        assert proc == "proc"
        assert handle is not None
        cmd = captured["cmd"]
        assert cmd[:2] == ["node", str(tmp_path / "gtopt_websrv.js")]
        assert ["--gtopt-bin", "/b/gtopt"] == cmd[4:6]
        assert ["--data-dir", "/d"] == cmd[6:8]
        assert ["--log-dir", str(log.parent)] == cmd[8:10]
        env = captured["env"]
        assert env["PORT"] == "3100"
        assert env["GTOPT_BIN"] == "/b/gtopt"
        assert env["GTOPT_DATA_DIR"] == "/d"
        assert env["GTOPT_LOG_DIR"] == str(log.parent)
        assert captured["stdout"] is handle
    finally:
        handle.close()


def test_start_webservice_node_launcher_minimal(tmp_path, monkeypatch):
    """Without optional args only the port is passed to the node launcher."""
    (tmp_path / ".next").mkdir()
    (tmp_path / "gtopt_websrv.js").write_text("")
    captured = {}

    def _popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return "proc"

    monkeypatch.setattr(gg.subprocess, "Popen", _popen)
    proc, handle = gg.start_webservice(tmp_path, 3100)
    assert (proc, handle) == ("proc", None)
    assert captured["cmd"] == ["node", str(tmp_path / "gtopt_websrv.js"), "--port", "3100"]
    assert captured["stdout"] is subprocess.PIPE


def test_start_webservice_npm_not_found_closes_log(tmp_path, monkeypatch, capsys):
    """npm missing -> error, log handle closed, (None, None)."""
    (tmp_path / ".next").mkdir()
    handles = []
    real_open = open

    def _open(*args, **kwargs):
        fh = real_open(*args, **kwargs)  # pylint: disable=consider-using-with
        handles.append(fh)
        return fh

    def _popen(cmd, **_k):
        assert cmd == ["npm", "start"]
        raise FileNotFoundError

    monkeypatch.setattr("builtins.open", _open)
    monkeypatch.setattr(gg.subprocess, "Popen", _popen)
    result = gg.start_webservice(tmp_path, 3000, log_file=str(tmp_path / "w.log"))
    assert result == (None, None)
    assert handles and handles[0].closed
    assert "node/npm not found" in capsys.readouterr().err


def test_start_webservice_npm_not_found_without_log(tmp_path, monkeypatch):
    """npm missing without a log file still returns (None, None)."""
    (tmp_path / ".next").mkdir()

    def _popen(*_a, **_k):
        raise FileNotFoundError

    monkeypatch.setattr(gg.subprocess, "Popen", _popen)
    assert gg.start_webservice(tmp_path, 3000) == (None, None)


# ---------------------------------------------------------------------------
# launch_ui_plus
# ---------------------------------------------------------------------------


@pytest.fixture(name="ui_plus_dir")
def fixture_ui_plus_dir(tmp_path, monkeypatch):
    """Create <tmp>/repo/guiservice-plus with package.json; patch __file__."""
    repo = tmp_path / "repo"
    gui = repo / "guiservice"
    gui.mkdir(parents=True)
    fake_file = gui / "gtopt_gui.py"
    fake_file.write_text("")
    ui_dir = repo / "guiservice-plus"
    ui_dir.mkdir()
    (ui_dir / "package.json").write_text("{}")
    monkeypatch.setattr(gg, "__file__", str(fake_file))
    monkeypatch.setattr(gg.shutil, "which", lambda _n: "/usr/bin/npm")
    return ui_dir


def test_launch_ui_plus_missing_dir(tmp_path, monkeypatch, capsys):
    """No guiservice-plus directory -> disabled."""
    fake = tmp_path / "a" / "b" / "c" / "gtopt_gui.py"
    fake.parent.mkdir(parents=True)
    monkeypatch.setattr(gg, "__file__", str(fake))
    assert gg.launch_ui_plus(5002, "http://g", False, tmp_path) == (None, None, None)
    assert "guiservice-plus directory not found" in capsys.readouterr().err


def test_launch_ui_plus_no_npm(ui_plus_dir, monkeypatch, tmp_path, capsys):
    """npm missing -> disabled."""
    del ui_plus_dir
    monkeypatch.setattr(gg.shutil, "which", lambda _n: None)
    assert gg.launch_ui_plus(5002, "http://g", False, tmp_path) == (None, None, None)
    assert "`npm` not found" in capsys.readouterr().err


def test_launch_ui_plus_npm_install_fails(ui_plus_dir, monkeypatch, tmp_path, capsys):
    """Failing ``npm install`` disables GUI Plus."""
    del ui_plus_dir
    monkeypatch.setattr(gg.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=1))
    assert gg.launch_ui_plus(5002, "http://g", False, tmp_path) == (None, None, None)
    assert "`npm install` failed" in capsys.readouterr().err


def _capture_popen(monkeypatch):
    captured = {}

    def _popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return "uiproc"

    monkeypatch.setattr(gg.subprocess, "Popen", _popen)
    return captured


def test_launch_ui_plus_install_then_dev_fallback(ui_plus_dir, monkeypatch, tmp_path):
    """After a successful install, missing .next falls back to ``next dev``."""
    runs = []
    monkeypatch.setattr(
        gg.subprocess,
        "run",
        lambda cmd, **_k: runs.append(cmd) or SimpleNamespace(returncode=0),
    )
    captured = _capture_popen(monkeypatch)
    url, proc, handle = gg.launch_ui_plus(5002, "http://g", False, tmp_path)
    handle.close()
    assert runs and runs[0][1] == "install"
    assert (url, proc) == ("http://localhost:5002", "uiproc")
    assert captured["cmd"][2] == "dev"
    assert captured["cwd"] == str(ui_plus_dir)
    assert captured["env"]["GTOPT_GUISERVICE_URL"] == "http://g"
    assert captured["env"]["PORT"] == "5002"


def test_launch_ui_plus_dev_mode(ui_plus_dir, monkeypatch, tmp_path):
    """dev_mode always uses ``next dev``."""
    (ui_plus_dir / "node_modules").mkdir()
    (ui_plus_dir / ".next").mkdir()
    captured = _capture_popen(monkeypatch)
    _url, _proc, handle = gg.launch_ui_plus(5002, "http://g", True, tmp_path)
    handle.close()
    assert captured["cmd"][2] == "dev"


def test_launch_ui_plus_start_mode(ui_plus_dir, monkeypatch, tmp_path):
    """A prebuilt .next directory uses ``next start``."""
    (ui_plus_dir / "node_modules").mkdir()
    (ui_plus_dir / ".next").mkdir()
    captured = _capture_popen(monkeypatch)
    _url, _proc, handle = gg.launch_ui_plus(5002, "http://g", False, tmp_path)
    handle.close()
    assert captured["cmd"][2] == "start"
    assert (tmp_path / "ui_plus.log").exists()


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class _MainHarness:  # pylint: disable=too-many-instance-attributes
    """Patch every side effect of ``gtopt_gui.main``."""

    def __init__(self, monkeypatch, tmp_path):
        self.mp = monkeypatch
        self.tmp = tmp_path
        self.atexit = []
        self.signals = {}
        self.browser = []
        self.popen = []
        self.killpg = []
        self.flask_proc = _FakeProc()
        self.sleep_budget = 1
        self.mkdtemp_count = 0

        gui_dir = tmp_path / "gui"
        gui_dir.mkdir()
        monkeypatch.setattr(gg, "get_guiservice_dir", lambda: gui_dir)
        monkeypatch.setattr(gg.tempfile, "mkdtemp", self._mkdtemp)
        monkeypatch.setattr(gg.atexit, "register", self.atexit.append)
        monkeypatch.setattr(gg.signal, "signal", self._signal)
        monkeypatch.setattr(gg.subprocess, "Popen", self._popen)
        monkeypatch.setattr(gg.time, "sleep", self._sleep)
        monkeypatch.setattr(gg, "wait_for_service", lambda _port: True)
        monkeypatch.setattr(gg, "find_free_port", lambda: 5999)
        monkeypatch.setattr(
            gg, "open_browser", lambda url, app_mode: self.browser.append((url, app_mode))
        )
        monkeypatch.setattr(gg, "is_port_open", lambda *_a, **_k: False)
        monkeypatch.setattr(gg, "get_webservice_dir", lambda: None)
        monkeypatch.setattr(gg, "verify_webservice_api", lambda *_a, **_k: True)
        monkeypatch.setattr(gg, "query_webservice_ping", lambda *_a, **_k: None)
        monkeypatch.setattr(gg.os, "getpgid", lambda pid: pid)
        monkeypatch.setattr(gg.os, "killpg", lambda pgid, sig: self.killpg.append((pgid, sig)))

    def _mkdtemp(self, prefix=""):
        self.mkdtemp_count += 1
        path = self.tmp / f"{prefix}{self.mkdtemp_count}"
        path.mkdir()
        return str(path)

    def _signal(self, sig, handler):
        self.signals[sig] = handler

    def _popen(self, cmd, **kwargs):
        self.popen.append((cmd, kwargs))
        return self.flask_proc

    def _sleep(self, _secs):
        self.sleep_budget -= 1
        if self.sleep_budget < 0:
            raise KeyboardInterrupt

    def run(self, argv):
        """Run main and close the Flask log handle afterwards."""
        try:
            gg.main(argv)
        finally:
            for _cmd, kwargs in self.popen:
                handle = kwargs.get("stdout")
                if isinstance(handle, io.IOBase):
                    handle.close()


@pytest.fixture(name="harness")
def fixture_harness(monkeypatch, tmp_path):
    """Provide a fully patched main() harness."""
    return _MainHarness(monkeypatch, tmp_path)


def test_main_missing_config_exits(harness, capsys):
    """A non-existent config file aborts with exit code 1."""
    with pytest.raises(SystemExit) as exc:
        harness.run([str(harness.tmp / "nope.json")])
    assert exc.value.code == 1
    assert "Configuration file not found" in capsys.readouterr().err


def test_main_no_webservice_and_browser(harness, capsys):
    """Happy path with an invalid-JSON config and default browser mode."""
    cfg = harness.tmp / "case.json"
    cfg.write_text("{not json")
    harness.run([str(cfg), "--no-webservice", "--port", "5123", "--debug"])
    captured = capsys.readouterr()
    assert "may not be valid JSON" in captured.err
    assert "Upload Case" in captured.out
    assert "Webservice not available" in captured.out
    assert harness.browser == [("http://localhost:5123/", False)]
    cmd, kwargs = harness.popen[0]
    assert cmd[1:] == ["-u", "app.py"]
    assert kwargs["env"]["FLASK_DEBUG"] == "1"
    assert kwargs["env"]["GTOPT_GUI_PORT"] == "5123"
    assert "GTOPT_WEBSERVICE_URL" not in kwargs["env"]
    assert set(harness.signals) == {signal.SIGINT, signal.SIGTERM}


def test_main_valid_config_app_mode(harness):
    """A valid config and --app-mode opens the browser in app mode."""
    cfg = harness.tmp / "case.json"
    cfg.write_text("{}")
    harness.run([str(cfg), "--no-webservice", "--app-mode"])
    assert harness.browser == [("http://localhost:5999/", True)]


def test_main_reuses_existing_webservice(harness, monkeypatch, capsys):
    """An existing gtopt webservice on the port is reused and pinged."""
    monkeypatch.setattr(gg, "is_port_open", lambda *_a, **_k: True)
    monkeypatch.setattr(gg, "is_gtopt_webservice", lambda _u: True)
    monkeypatch.setattr(
        gg,
        "query_webservice_ping",
        lambda *_a, **_k: {
            "gtopt_version": "1.2",
            "gtopt_bin": "/b/gtopt",
            "log_file": "/l/web.log",
        },
    )
    harness.run(["--no-browser", "--webservice-port", "3456"])
    out = capsys.readouterr().out
    assert "Detected existing gtopt webservice on port 3456" in out
    assert "Webservice API check passed" in out
    assert "gtopt version: 1.2" in out
    assert "gtopt binary: /b/gtopt" in out
    assert "Webservice log file: /l/web.log" in out
    assert "GUI service is running at: http://localhost:5999/" in out
    assert harness.popen[0][1]["env"]["GTOPT_WEBSERVICE_URL"] == "http://localhost:3456"
    assert not harness.browser


def test_main_ping_without_details(harness, monkeypatch, capsys):
    """An empty ping response prints no detail lines."""
    monkeypatch.setattr(gg, "query_webservice_ping", lambda *_a, **_k: {"status": "ok"})
    monkeypatch.setattr(gg, "verify_webservice_api", lambda *_a, **_k: False)
    harness.run(["--no-browser", "--webservice-url", "http://ext:1"])
    captured = capsys.readouterr()
    assert "Webservice API check failed" in captured.err
    assert "gtopt version" not in captured.out
    assert "Webservice available at: http://ext:1" in captured.out


def test_main_port_busy_with_foreign_service(harness, monkeypatch, capsys):
    """A non-gtopt service on the port is reported and not used."""
    monkeypatch.setattr(gg, "is_port_open", lambda *_a, **_k: True)
    monkeypatch.setattr(gg, "is_gtopt_webservice", lambda _u: False)
    harness.run(["--no-browser"])
    err = capsys.readouterr().err
    assert "does not appear to be a gtopt webservice" in err
    assert "GTOPT_WEBSERVICE_URL" not in harness.popen[0][1]["env"]


def test_main_webservice_not_installed(harness, capsys):
    """No webservice installation -> warning, GUI still starts."""
    harness.run(["--no-browser"])
    assert "Webservice not found" in capsys.readouterr().out


def test_main_starts_webservice_child_reused_port(harness, monkeypatch, tmp_path, capsys):
    """Child webservice exits but port is open -> treat as external instance."""
    web_dir = tmp_path / "web"
    monkeypatch.setattr(gg, "get_webservice_dir", lambda: web_dir)
    monkeypatch.setattr(gg, "find_gtopt_binary", lambda: None)
    ws_proc = _FakeProc(polls=[0])
    started = {}

    def _start(wdir, port, **kwargs):
        started.update(kwargs, wdir=wdir, port=port)
        return ws_proc, None

    port_checks = iter([False, True])
    monkeypatch.setattr(gg, "is_port_open", lambda *_a, **_k: next(port_checks))
    monkeypatch.setattr(gg, "start_webservice", _start)
    monkeypatch.setattr(gg, "wait_for_webservice", lambda *_a, **_k: False)
    harness.run(["--no-browser"])
    captured = capsys.readouterr()
    assert "gtopt binary not found" in captured.out
    assert "did not become ready" in captured.err
    assert "Detected existing process on port 3000" in captured.out
    assert started["wdir"] == web_dir
    assert started["gtopt_bin"] is None
    # webservice_process was dropped -> cleanup only kills flask
    cleanup = harness.atexit[0]
    harness.flask_proc = _FakeProc(polls=[0])
    cleanup()


def test_main_starts_webservice_and_monitors_it(harness, monkeypatch, tmp_path, capsys):
    """A started webservice that later dies is reported, GUI keeps running."""
    monkeypatch.setattr(gg, "get_webservice_dir", lambda: tmp_path / "web")
    monkeypatch.setattr(gg, "find_gtopt_binary", lambda: Path("/b/gtopt"))
    # poll(): after-wait check -> None (alive); main loop -> 1 (died)
    ws_proc = _FakeProc(polls=[None, 1, 1, 1])
    log_handle = open(tmp_path / "ws.log", "a", encoding="utf-8")  # pylint: disable=consider-using-with
    monkeypatch.setattr(gg, "start_webservice", lambda *_a, **_k: (ws_proc, log_handle))
    monkeypatch.setattr(gg, "wait_for_webservice", lambda *_a, **_k: True)
    harness.run(["--no-browser"])
    captured = capsys.readouterr()
    assert "Found gtopt binary at: /b/gtopt" in captured.out
    assert "Webservice is ready." in captured.out
    assert "Webservice terminated unexpectedly" in captured.err

    # Run the registered cleanup: both children already exited.
    harness.flask_proc._polls = [0]  # pylint: disable=protected-access
    ws_proc._polls = [0]  # pylint: disable=protected-access
    harness.atexit[0]()
    assert log_handle.closed


def test_main_webservice_start_fails(harness, monkeypatch, tmp_path, capsys):
    """start_webservice returning None prints a warning."""
    monkeypatch.setattr(gg, "get_webservice_dir", lambda: tmp_path / "web")
    monkeypatch.setattr(gg, "find_gtopt_binary", lambda: None)
    monkeypatch.setattr(gg, "start_webservice", lambda *_a, **_k: (None, None))
    harness.run(["--no-browser"])
    assert "Failed to start webservice" in capsys.readouterr().out


def test_main_gui_service_timeout(harness, monkeypatch, capsys):
    """GUI service never ready -> cleanup kills it and exit 1."""
    monkeypatch.setattr(gg, "wait_for_service", lambda _port: False)
    with pytest.raises(SystemExit) as exc:
        harness.run(["--no-webservice"])
    assert exc.value.code == 1
    assert "failed to start within timeout" in capsys.readouterr().err
    assert harness.killpg == [(4242, signal.SIGTERM)]
    assert harness.flask_proc.waits == [5]


def test_main_flask_dies(harness, capsys):
    """Flask terminating unexpectedly exits with code 1."""
    harness.flask_proc = _FakeProc(polls=[1])
    with pytest.raises(SystemExit) as exc:
        harness.run(["--no-webservice", "--no-browser"])
    assert exc.value.code == 1
    assert "Flask process terminated unexpectedly" in capsys.readouterr().err


def test_main_signal_handler_exits(harness):
    """The registered SIGINT handler exits cleanly with code 0."""
    harness.run(["--no-webservice", "--no-browser"])
    with pytest.raises(SystemExit) as exc:
        harness.signals[signal.SIGINT](signal.SIGINT, None)
    assert exc.value.code == 0


def test_main_ui_plus_launched(harness, monkeypatch, capsys):
    """--ui-plus opens the GUI Plus URL and registers its cleanup."""
    ui_proc = _FakeProc()
    seen = {}

    def _launch(**kwargs):
        seen.update(kwargs)
        return "http://localhost:5002", ui_proc, None

    monkeypatch.setattr(gg, "launch_ui_plus", _launch)
    harness.run(["--no-webservice", "--ui-plus", "--ui-plus-dev"])
    assert harness.browser == [("http://localhost:5002", False)]
    assert seen["dev_mode"] is True
    assert seen["guiservice_url"] == "http://localhost:5999"
    assert len(harness.atexit) == 2
    harness.atexit[1]()  # kill GUI Plus (still running)
    assert harness.killpg == [(4242, signal.SIGTERM)]
    del capsys


def test_main_ui_plus_no_browser_and_failed(harness, monkeypatch, capsys):
    """--no-browser prints the GUI Plus URL; a failed launch registers nothing."""
    monkeypatch.setattr(gg, "launch_ui_plus", lambda **_k: ("http://localhost:5002", None, None))
    harness.run(["--no-webservice", "--ui-plus", "--no-browser"])
    assert "GUI Plus is running at: http://localhost:5002" in capsys.readouterr().out
    assert len(harness.atexit) == 1


# ---------------------------------------------------------------------------
# _kill_process_group (reached through the registered cleanup)
# ---------------------------------------------------------------------------


def _cleanup_for(harness):
    harness.run(["--no-webservice", "--no-browser"])
    return harness.atexit[0]


def test_cleanup_getpgid_error_terminates(harness, monkeypatch):
    """getpgid failure falls back to proc.terminate()."""
    cleanup = _cleanup_for(harness)

    def _boom(_pid):
        raise ProcessLookupError

    monkeypatch.setattr(gg.os, "getpgid", _boom)
    cleanup()
    assert harness.flask_proc.terminated


def test_cleanup_timeout_escalates_to_sigkill(harness):
    """A wait timeout escalates to SIGKILL on the process group."""
    harness.flask_proc = _FakeProc(wait_raises=True)
    cleanup = _cleanup_for(harness)
    cleanup()
    assert harness.killpg == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    assert harness.flask_proc.waits == [5, 3]


def test_cleanup_timeout_and_killpg_error_kills(harness, monkeypatch):
    """SIGKILL failure falls back to proc.kill()."""
    harness.flask_proc = _FakeProc(wait_raises=True)
    cleanup = _cleanup_for(harness)
    calls = []

    def _killpg(_pgid, sig):
        calls.append(sig)
        if sig == signal.SIGKILL:
            raise OSError

    monkeypatch.setattr(gg.os, "killpg", _killpg)
    cleanup()
    assert calls == [signal.SIGTERM, signal.SIGKILL]
    assert harness.flask_proc.killed
