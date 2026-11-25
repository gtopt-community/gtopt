# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Ctrl-C / SIGTERM escalation for a running gtopt.

gtopt runs in its own session, so a terminal Ctrl-C reaches only
run_gtopt.  gtopt installs no SIGINT handler of its own; its only
graceful-stop channel is the ``sddp_stop_request.json`` file it polls
between SDDP iterations (``SDDPMethod::check_api_stop_request``).

* 1st Ctrl-C — write the stop request: SDDP / cascade finish the current
  iteration, save cuts and exit 0 (a monolithic solve cannot stop early;
  the request is simply ignored there).
* 2nd Ctrl-C — SIGTERM.
* 3rd Ctrl-C, or gtopt still alive :data:`TERM_GRACE_S` seconds after a
  SIGTERM — SIGKILL.  (This bounds *shutdown* only; a solve itself is
  never timed out.)

A SIGTERM sent to run_gtopt (CI cancel, systemd stop) is forwarded to
gtopt with the same SIGKILL fallback.
"""

from __future__ import annotations

import contextlib
import signal
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

#: Seconds gtopt gets to exit after SIGTERM before it is SIGKILLed.
TERM_GRACE_S = 10.0


class InterruptController:
    """State machine behind the Ctrl-C escalation (see module docstring).

    *proc* needs ``send_signal(sig)`` and ``poll()`` (a
    :class:`~run_gtopt._process.SolverProcess`).
    """

    def __init__(
        self,
        proc: Any,
        request_stop: Callable[[], bool],
        notify: Callable[[str], None],
        *,
        grace_s: float = TERM_GRACE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._proc = proc
        self._request_stop = request_stop
        self._notify = notify
        self._grace_s = grace_s
        self._clock = clock
        self.interrupts = 0
        self.stop_requested = False
        #: ``"terminated"`` / ``"killed"`` once run_gtopt signalled gtopt.
        self.forced: str | None = None
        self._term_deadline: float | None = None

    def on_interrupt(self) -> None:
        """Handle one Ctrl-C."""
        self.interrupts += 1
        if self.interrupts == 1:
            self.stop_requested = self._request_stop()
            if self.stop_requested:
                self._notify(
                    "Ctrl-C: graceful stop requested — SDDP finishes the "
                    "current iteration and saves cuts (a monolithic solve "
                    "cannot stop early).  Ctrl-C again to terminate."
                )
            else:
                self._notify(
                    "Ctrl-C: could not write the stop request.  "
                    "Ctrl-C again to terminate gtopt."
                )
        elif self.interrupts == 2:
            self.terminate("second Ctrl-C")
        else:
            self.kill("third Ctrl-C")

    def terminate(self, reason: str) -> None:
        """Send SIGTERM and arm the SIGKILL fallback."""
        if self.forced is not None:
            return
        self._proc.send_signal(signal.SIGTERM)
        self.forced = "terminated"
        self._term_deadline = self._clock() + self._grace_s
        self._notify(
            f"{reason}: stopping… sent SIGTERM to gtopt "
            f"(SIGKILL in {self._grace_s:.0f} s, or press Ctrl-C again)"
        )

    def kill(self, reason: str) -> None:
        """Send SIGKILL."""
        self._proc.send_signal(signal.SIGKILL)
        self.forced = "killed"
        self._term_deadline = None
        self._notify(f"{reason}: sent SIGKILL to gtopt")

    def tick(self) -> None:
        """Escalate to SIGKILL when the SIGTERM grace period ran out."""
        if (
            self._term_deadline is not None
            and self._clock() >= self._term_deadline
            and self._proc.poll() is None
        ):
            self.kill(f"gtopt still running {self._grace_s:.0f} s after SIGTERM")

    @contextlib.contextmanager
    def installed(self) -> Iterator[InterruptController]:
        """Route SIGINT / SIGTERM to this controller while active.

        Signal handlers can only be installed from the main thread; from
        any other thread this is a no-op (the previous handlers stay).
        """
        if threading.current_thread() is not threading.main_thread():
            yield self
            return
        old_int = signal.signal(signal.SIGINT, lambda _s, _f: self.on_interrupt())
        old_term = signal.signal(
            signal.SIGTERM, lambda _s, _f: self.terminate("run_gtopt got SIGTERM")
        )
        try:
            yield self
        finally:
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, old_term)
