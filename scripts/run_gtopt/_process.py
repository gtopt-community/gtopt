# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""The gtopt child process: launch, capture of its stdout/stderr, errors.

gtopt sends its spdlog output to ``<output>/logs/gtopt_N.log`` when its
stdout is not a TTY, but early failures (bad path, JSON parse error,
plugin load failure, abort) only reach stderr.  :class:`SolverProcess`
merges the child's stdout and stderr into one pipe, keeps the last lines
in a ring buffer, tees them to ``<output>/logs/run_gtopt_stderr_<ts>.log``
(created on the first line only) and optionally echoes them, so the
failure message can always show *why* gtopt stopped.
"""

from __future__ import annotations

import contextlib
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._outcome import Outcome

#: Lines of captured solver stdout/stderr kept for the failure message.
TAIL_LINES = 40
#: Distinct ``[critical]`` / ``[error]`` lines kept for the failure message.
MAX_ERROR_LINES = 8

_LEVEL_RE = re.compile(r"\[(critical|error|err)\]", re.IGNORECASE)
# "[2026-11-25 02:36:16.304] " spdlog timestamp / "gtopt: " stderr prefix.
_PREFIX_RE = re.compile(r"^(?:\[\d{4}-\d\d-\d\d [\d:.]+\]\s*|gtopt:\s*)+")

#: gtopt's documented exit codes (include/gtopt/gtopt_cli.hpp).
GTOPT_EXIT_CODES = {
    0: "success",
    1: "non-optimal solution or CLI error",
    2: "input error",
    3: "internal error",
}


def is_error_line(line: str) -> bool:
    """True for a gtopt log line at ``critical`` / ``error`` level."""
    return _LEVEL_RE.search(line) is not None


def describe_exit(rc: int | None) -> str:
    """Human description of a child exit status.

    Negative codes (``Popen`` convention) and shell-style ``128+N`` codes
    are named after their signal (``SIGKILL`` usually means the kernel's
    OOM killer).
    """
    if rc is None:
        return "still running"
    sig = -rc if rc < 0 else rc - 128 if rc > 128 else 0
    if sig > 0:
        try:
            name = signal.Signals(sig).name
        except ValueError:
            name = f"signal {sig}"
        text = f"killed by {name}"
        if sig == signal.SIGKILL:
            text += " (out of memory, or a forced kill)"
        elif sig == signal.SIGABRT:
            text += " (abort / failed assertion; check for a core file)"
        elif sig == signal.SIGSEGV:
            text += " (crash; check for a core file)"
        return text
    meaning = GTOPT_EXIT_CODES.get(rc)
    return f"exit code {rc}" + (f" ({meaning})" if meaning else "")


def shell_exit_code(rc: int | None) -> int:
    """Map a ``Popen`` return code to a shell exit status (``128+N``)."""
    if rc is None:
        return 1
    return 128 - rc if rc < 0 else rc


@dataclass
class RunResult:
    """Everything the final banner needs to know about a finished run."""

    returncode: int | None
    elapsed: float = 0.0
    errors: list[str] = field(default_factory=list)
    tail: list[str] = field(default_factory=list)
    capture_path: Path | None = None
    log_path: Path | None = None
    output_dir: Path | None = None
    stop_requested: bool = False
    #: ``"terminated"`` / ``"killed"`` when run_gtopt had to signal gtopt.
    forced: str | None = None
    #: Final ``solver_status.json`` contents (empty when not written).
    status: dict[str, Any] = field(default_factory=dict)
    #: :func:`run_gtopt._runner.check_solution` of the output directory.
    solution: dict[str, Any] = field(default_factory=dict)
    #: ``--attach`` observer left while gtopt was still running.
    detached: bool = False

    def outcome(self, stop_requested: bool = False) -> Outcome:
        """Classify this run (see :mod:`run_gtopt._outcome`)."""
        from ._outcome import classify  # noqa: PLC0415

        return classify(
            self.returncode,
            self.status,
            self.solution,
            stop_requested=stop_requested or self.stop_requested,
            forced=self.forced,
            detached=self.detached,
        )


class ErrorCollector:
    """Thread-safe set of distinct error lines, in arrival order."""

    def __init__(self, limit: int = MAX_ERROR_LINES) -> None:
        self._lines: list[str] = []
        self._keys: set[str] = set()
        self._limit = limit
        self._lock = threading.Lock()

    def feed(self, line: str) -> None:
        """Keep *line* when it is a new critical/error line.

        The same message arrives twice — on stderr (``gtopt: [critical]
        …``) and in ``gtopt_N.log`` (``[<timestamp>] [critical] …``) — so
        lines are compared without those prefixes.
        """
        if not is_error_line(line):
            return
        text = line.rstrip()
        key = _PREFIX_RE.sub("", text)
        with self._lock:
            if key not in self._keys and len(self._lines) < self._limit:
                self._keys.add(key)
                self._lines.append(text)

    @property
    def lines(self) -> list[str]:
        with self._lock:
            return list(self._lines)


def _set_parent_death_signal() -> None:
    """Child-side: ask Linux to SIGTERM gtopt if run_gtopt dies."""
    with contextlib.suppress(Exception):
        import ctypes  # noqa: PLC0415

        libc = ctypes.CDLL(None, use_errno=True)
        pr_set_pdeathsig = 1
        libc.prctl(pr_set_pdeathsig, int(signal.SIGTERM))


class SolverProcess:
    """Run gtopt with its stdout+stderr captured (see module docstring).

    The child gets its own session (``start_new_session``) so a terminal
    Ctrl-C reaches only run_gtopt, which then decides how to stop gtopt
    (graceful stop request first; see ``_interrupt``).  On Linux the
    child is also armed with ``PR_SET_PDEATHSIG`` so it never outlives a
    killed run_gtopt.
    """

    def __init__(
        self,
        cmd: list[str],
        env: dict[str, str],
        *,
        capture_dir: Path | None = None,
        echo: IO[str] | None = None,
        on_line: Callable[[str], None] | None = None,
        errors: ErrorCollector | None = None,
    ) -> None:
        self.cmd = cmd
        self.env = env
        self.capture_dir = capture_dir
        self.capture_path: Path | None = None
        self.echo = echo
        self.on_line = on_line
        self.errors = errors or ErrorCollector()
        self.tail: deque[str] = deque(maxlen=TAIL_LINES)
        self.proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._capture_fh: IO[str] | None = None
        self.started_at = time.monotonic()
        #: Wall-clock launch time; output files older than this are stale.
        self.started_wall = time.time()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Launch gtopt and the capture thread."""
        kwargs: dict[str, Any] = {
            "env": self.env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "errors": "replace",
            "bufsize": 1,
            "start_new_session": True,
        }
        if sys.platform.startswith("linux"):
            kwargs["preexec_fn"] = _set_parent_death_signal
        self.started_at = time.monotonic()
        self.started_wall = time.time()
        self.proc = subprocess.Popen(self.cmd, **kwargs)  # noqa: S603  # pylint: disable=consider-using-with,subprocess-popen-preexec-fn
        self._reader = threading.Thread(
            target=self._read_loop, daemon=True, name="gtopt-capture"
        )
        self._reader.start()

    def _open_capture(self) -> IO[str] | None:
        if self.capture_dir is None:
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.capture_dir / f"run_gtopt_stderr_{stamp}.log"
        try:
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            fh = open(path, "a", encoding="utf-8")  # pylint: disable=consider-using-with
        except OSError:
            self.capture_dir = None
            return None
        self.capture_path = path
        return fh

    def _read_loop(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        for raw in proc.stdout:
            self._consume(raw)
        if self._capture_fh is not None:
            self._capture_fh.close()

    def _consume(self, raw: str) -> None:
        line = raw.rstrip("\n")
        self.tail.append(line)
        self.errors.feed(line)
        if self._capture_fh is None:
            self._capture_fh = self._open_capture()
        if self._capture_fh is not None:
            self._capture_fh.write(line + "\n")
            self._capture_fh.flush()
        if self.echo is not None:
            self.echo.write(line + "\n")
            self.echo.flush()
        if self.on_line is not None:
            self.on_line(line)

    def poll(self) -> int | None:
        assert self.proc is not None
        return self.proc.poll()

    def wait(self) -> int:
        """Wait for gtopt and for the capture thread to drain the pipe."""
        assert self.proc is not None
        rc = self.proc.wait()
        assert self._reader is not None
        self._reader.join(timeout=5.0)
        return rc

    def send_signal(self, sig: int) -> None:
        """Signal gtopt; a no-op once it has exited."""
        if self.proc is not None and self.proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.send_signal(sig)

    @property
    def returncode(self) -> int | None:
        return None if self.proc is None else self.proc.returncode

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def result(self, log_path: Path | None = None, **kwargs: Any) -> RunResult:
        """Snapshot of the finished run for :class:`RunResult` consumers."""
        return RunResult(
            returncode=self.returncode,
            elapsed=self.elapsed,
            errors=self.errors.lines,
            tail=list(self.tail),
            capture_path=self.capture_path,
            log_path=log_path,
            **kwargs,
        )


#: Lines of captured output shown in the failure message.
FAILURE_TAIL_LINES = 12


def failure_details(result: RunResult, include_tail: bool = True) -> list[str]:
    """The "why did it fail" lines shared by the TUI and batch banners.

    Order: distinct gtopt ``[critical]``/``[error]`` lines, then the last
    lines gtopt printed on stdout/stderr (those not already listed), then
    where the full capture and the gtopt log live.
    """
    lines: list[str] = []
    if result.errors:
        lines.append("Errors reported by gtopt:")
        lines.extend(f"  {e}" for e in result.errors)
    tail = [
        t
        for t in (result.tail if include_tail else [])
        if t.strip() and t.rstrip() not in result.errors
    ]
    if tail:
        lines.append("Last solver output:")
        lines.extend(f"  {t}" for t in tail[-FAILURE_TAIL_LINES:])
    if not result.errors and not result.tail:
        lines.append("gtopt printed nothing on stdout/stderr.")
    return lines


def run_paths(result: RunResult) -> list[tuple[str, Path]]:
    """Where this run's files are: output dir, gtopt log, status file,
    captured solver stdout/stderr, error LPs (shown in every banner)."""
    paths: list[tuple[str, Path]] = []
    if result.output_dir is not None:
        paths.append(("Output", result.output_dir))
    if result.log_path is not None:
        paths.append(("gtopt log", result.log_path))
    if result.output_dir is not None:
        status = result.output_dir / "solver_status.json"
        if status.is_file():
            paths.append(("Status", status))
    if result.capture_path is not None:
        paths.append(("Solver stdout/stderr", result.capture_path))
    return paths


def error_lp_hint(output_dir: Path | None, limit: int = 3) -> list[str]:
    """Lines pointing at the error LPs gtopt dumped for infeasible cells."""
    if output_dir is None:
        return []
    logs = output_dir / "logs"
    try:
        files = sorted(logs.glob("error*.lp*"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return []
    if not files:
        return []
    lines = [f"{len(files)} error LP file(s) (infeasible / failed solves):"]
    lines.extend(f"  {f}" for f in files[:limit])
    if len(files) > limit:
        lines.append(f"  … and {len(files) - limit} more in {logs}")
    lines.append(f"Diagnose: gtopt_check_lp {files[0]} --solver all")
    return lines
