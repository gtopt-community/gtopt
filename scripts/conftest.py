"""Top-level conftest for the gtopt scripts test tree.

Thread caps
-----------
Caps native thread pools in this process and (via inherited environment)
in every subprocess started by tests.  Pytest-xdist already runs ~20
worker processes in parallel; many tests then spawn another Python via
``subprocess.run([sys.executable, "-m", ...])`` which can in turn open
its own ThreadPoolExecutor / numpy BLAS pool sized to ``os.cpu_count()``.
On a 20-core box that compounds to 20 workers x 20 BLAS threads = 400
threads competing for 20 cores.

Forcing OMP/MKL to 1 keeps each Python interpreter single-threaded for
linear-algebra work; xdist still parallelises across tests, and
ThreadPoolExecutor instances we explicitly construct are unaffected.
``setdefault`` so a developer can still override via shell.

HOME sandbox
------------
Tests must write only under ``$TMPDIR`` / ``tmp_path``.  Several tools
keep per-user state under the home directory and some resolve it at
*import* time (``plexos2gtopt.PLEXOS_RUN_REGISTRY``,
``gtopt_config.DEFAULT_CONFIG_PATH``, the ``cen2gtopt`` cache roots,
``nrel118_to_gtopt`` data cache, ...).  pytest imports this conftest
before any test module, so pointing ``HOME`` and ``XDG_*`` at a
throw-away per-process directory here makes every such constant, every
runtime ``Path.home()`` / ``expanduser`` lookup and every subprocess a
test spawns resolve inside ``$TMPDIR``.  Read-only download caches
(``gtopt/sienna``: test data fetched from upstream on first use) are
*copied* into the sandbox so offline reruns keep working without ever
writing to the real cache.

Guard: the xdist controller (or the single process without xdist)
snapshots the real ``~/.gtopt*``, ``~/.config/gtopt*`` and
``~/.cache/gtopt`` at session start and fails the run if anything there
changed at session end.  ``~/.cache/gtopt/runs`` is excluded: the C++
solver registers every live run there, including the user's own runs
that may happen concurrently.  Set ``GTOPT_TEST_HOME_GUARD=0`` to skip
the guard (e.g. while a converter is legitimately refreshing the real
cache on the same box).

Keep in sync with ``guiservice/tests/conftest.py``.
"""

from __future__ import annotations

import atexit
import os
import shutil
import site
import tempfile
from pathlib import Path

import pytest

for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

_REAL_HOME_ENV = "GTOPT_TEST_REAL_HOME"
_GUARD_ENV = "GTOPT_TEST_HOME_GUARD"
_XDG_DIRS = {
    "XDG_CACHE_HOME": ".cache",
    "XDG_CONFIG_HOME": ".config",
    "XDG_DATA_HOME": ".local/share",
    "XDG_STATE_HOME": ".local/state",
}
# Download caches copied (never linked) into the sandbox at start-up.
_SEEDED_CACHES = ("gtopt/sienna",)


def _install_fake_home() -> tuple[Path, Path]:
    """Point HOME / XDG_* at a fresh temp dir; return (real, fake) homes."""
    # Inherited by xdist workers and nested runs: always the user's home.
    real = Path(os.environ.setdefault(_REAL_HOME_ENV, str(Path.home())))
    # Keep the real user site-packages importable in subprocesses
    # (``python -m <tool>``): its location is derived from HOME.
    os.environ.setdefault("PYTHONUSERBASE", site.getuserbase())
    real_cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    fake = Path(tempfile.mkdtemp(prefix="gtopt-test-home-"))
    for seed in _SEEDED_CACHES:
        if (real_cache / seed).is_dir():
            shutil.copytree(real_cache / seed, fake / ".cache" / seed)
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
    changed = sorted(
        p for p in before.keys() | after.keys() if before.get(p) != after.get(p)
    )
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
