"""Conftest for the guiservice test suite: HOME sandbox.

Tests must write only under ``$TMPDIR`` / ``tmp_path``.  pytest imports
this conftest before any test module, so pointing ``HOME`` and ``XDG_*``
at a throw-away per-process directory here makes every
``Path.home()`` / ``expanduser`` lookup in the app (and every subprocess
a test spawns, e.g. the ``gtopt_guisrv`` launcher) resolve inside
``$TMPDIR``.

Guard: the xdist controller (or the single process without xdist)
snapshots the real ``~/.gtopt*``, ``~/.config/gtopt*`` and
``~/.cache/gtopt`` (minus the C++ solver's live ``runs/`` registry) at
session start and fails the run if anything there changed at session
end.  Set ``GTOPT_TEST_HOME_GUARD=0`` to skip the guard.

Mirror of the sandbox in ``scripts/conftest.py`` (guiservice tests do
not import the scripts packages); keep both in sync.
"""

from __future__ import annotations

import atexit
import os
import shutil
import site
import tempfile
from pathlib import Path

import pytest

_REAL_HOME_ENV = "GTOPT_TEST_REAL_HOME"
_GUARD_ENV = "GTOPT_TEST_HOME_GUARD"
_XDG_DIRS = {
    "XDG_CACHE_HOME": ".cache",
    "XDG_CONFIG_HOME": ".config",
    "XDG_DATA_HOME": ".local/share",
    "XDG_STATE_HOME": ".local/state",
}


def _install_fake_home() -> tuple[Path, Path]:
    """Point HOME / XDG_* at a fresh temp dir; return (real, fake) homes."""
    # Inherited by xdist workers and nested runs: always the user's home.
    real = Path(os.environ.setdefault(_REAL_HOME_ENV, str(Path.home())))
    # Keep the real user site-packages importable in subprocesses
    # (``python -m <tool>``): its location is derived from HOME.
    os.environ.setdefault("PYTHONUSERBASE", site.getuserbase())
    fake = Path(tempfile.mkdtemp(prefix="gtopt-test-home-"))
    os.environ["HOME"] = str(fake)
    for var, rel in _XDG_DIRS.items():
        os.environ[var] = str(fake / rel)
    atexit.register(shutil.rmtree, fake, ignore_errors=True)
    return real, fake


def _guarded_roots(home: Path) -> list[Path]:
    """Per-user gtopt state that tests must never touch."""
    return [
        *home.glob(".gtopt*"),
        *(home / ".config").glob("gtopt*"),
        home / ".cache" / "gtopt",
    ]


def _snapshot(home: Path) -> dict[str, tuple[int, int]]:
    """Map every guarded file to ``(mtime_ns, size)`` (dirs to ``(0, 0)``)."""
    skip = home / ".cache" / "gtopt" / "runs"
    snap: dict[str, tuple[int, int]] = {}
    for root in _guarded_roots(home):
        if root.is_file():
            st = root.stat()
            snap[str(root)] = (st.st_mtime_ns, st.st_size)
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if Path(dirpath, d) != skip]
            snap[dirpath] = (0, 0)
            for name in filenames:
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                snap[path] = (st.st_mtime_ns, st.st_size)
    return snap


_REAL_HOME, _FAKE_HOME = _install_fake_home()
_GUARD_STASH = pytest.StashKey[dict[str, tuple[int, int]]]()


def _guard_active(session: pytest.Session) -> bool:
    is_xdist_worker = hasattr(session.config, "workerinput")
    return not is_xdist_worker and os.environ.get(_GUARD_ENV, "1") != "0"


def pytest_sessionstart(session: pytest.Session) -> None:
    """Snapshot the real per-user gtopt state."""
    if _guard_active(session):
        session.stash[_GUARD_STASH] = _snapshot(_REAL_HOME)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the run if any test wrote to the real per-user gtopt state."""
    del exitstatus
    before = session.stash.get(_GUARD_STASH, None)
    if before is None:
        return
    after = _snapshot(_REAL_HOME)
    changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
    if not changed:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    lines = [
        f"HOME sandbox guard: tests modified the real home {_REAL_HOME} "
        f"(tests must write only under $TMPDIR / tmp_path; "
        f"set {_GUARD_ENV}=0 to skip):",
        *(f"  {p}" for p in changed[:50]),
    ]
    if reporter is not None:
        reporter.write_line("")
        for line in lines:
            reporter.write_line(line, red=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(name="fake_home")
def fixture_fake_home() -> Path:
    """The per-process sandbox directory that ``HOME`` points at."""
    return _FAKE_HOME


@pytest.fixture(name="real_home")
def fixture_real_home() -> Path:
    """The user's real home directory (read-only use in tests!)."""
    return _REAL_HOME


@pytest.fixture(autouse=True)
def _gui_request_header(monkeypatch):
    """Make Flask test clients send ``X-GTOpt-GUI`` like the bundled UI.

    The service rejects state-changing requests without that header (CSRF
    guard).  Tests of the guard itself build a plain ``FlaskClient``.
    """
    # pylint: disable=import-outside-toplevel
    try:
        from flask.testing import FlaskClient

        from guiservice.app import GUI_REQUEST_HEADER, app
    except ImportError:  # flask / app deps missing: nothing to patch
        yield
        return

    environ_key = "HTTP_" + GUI_REQUEST_HEADER.upper().replace("-", "_")

    class _GuiTestClient(FlaskClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.environ_base[environ_key] = "1"

    monkeypatch.setattr(app, "test_client_class", _GuiTestClient)
    yield


@pytest.fixture(autouse=True)
def _isolated_server_env(monkeypatch):
    """Isolate the bind / auth / server environment between tests.

    ``gtopt_guisrv.main()`` exports ``GTOPT_GUI_HOST`` / ``GTOPT_GUI_PORT``
    / ``FLASK_DEBUG`` and ``app.run_server()`` stores the resolved access
    token; both are undone after every test.  Tests serve with the
    (mocked) Werkzeug ``app.run`` unless they opt into waitress.
    """
    for name in (
        "GTOPT_GUI_HOST",
        "GTOPT_GUI_PORT",
        "FLASK_DEBUG",
        "GTOPT_GUI_AUTH",
        "GTOPT_GUI_AUTH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GTOPT_GUI_SERVER", "werkzeug")
    try:
        from guiservice.app import (  # pylint: disable=import-outside-toplevel
            AUTH_TOKEN_CONFIG,
            app,
        )
    except ImportError:
        yield
        return
    app.config.pop(AUTH_TOKEN_CONFIG, None)
    yield
    app.config.pop(AUTH_TOKEN_CONFIG, None)
