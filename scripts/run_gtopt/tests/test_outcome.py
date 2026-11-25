# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for run_gtopt._outcome (final banner classification)."""

from __future__ import annotations

import signal

import pytest

from run_gtopt._outcome import classify
from run_gtopt._process import RunResult

_SDDP = {"iteration": 8, "max_iterations": 100, "gap": 0.0004}


@pytest.mark.parametrize(
    ("kwargs", "kind", "title_frag"),
    [
        (
            {"returncode": 0, "status": {**_SDDP, "status": "converged"}},
            "converged",
            "Converged",
        ),
        (
            {"returncode": 0, "status": {**_SDDP, "converged": True}},
            "converged",
            "Converged",
        ),
        (
            {
                "returncode": 0,
                "status": {**_SDDP, "status": "max_iterations", "gap": 0.032},
            },
            "not_converged",
            "NOT converged (iteration limit)",
        ),
        (
            {"returncode": 0, "status": {**_SDDP, "status": "stopped"}},
            "stopped",
            "Stopped on request",
        ),
        (
            {
                "returncode": 0,
                "status": {**_SDDP, "status": "stopped", "converged": True},
            },
            "stopped",
            "Stopped",
        ),
        ({"returncode": 0, "stop_requested": True}, "stopped", "Stopped on request"),
        (
            {"returncode": 0, "status": {"status": "done"}, "solution": {"status": 0}},
            "optimal",
            "Optimal",
        ),
        ({"returncode": 0, "solution": {"status": 1}}, "non_optimal", "NOT optimal"),
        ({"returncode": 0, "solution": {"stale": True}}, "unknown", "status unknown"),
        ({"returncode": 0}, "unknown", "status unknown"),
        ({"returncode": None}, "unknown", "outcome unknown"),
        (
            {"returncode": None, "status": {**_SDDP, "status": "converged"}},
            "converged",
            "Converged",
        ),
        (
            {"returncode": None, "detached": True, "status": _SDDP},
            "detached",
            "still running",
        ),
        (
            {"returncode": 1, "solution": {"status": 1}},
            "non_optimal",
            "NOT optimal / infeasible",
        ),
        ({"returncode": 3}, "failed", "Failed"),
        ({"returncode": 2, "solution": {"status": 0}}, "failed", "Failed"),
        ({"returncode": -signal.SIGKILL}, "killed", "killed"),
        ({"returncode": 128 + signal.SIGSEGV}, "killed", "killed"),
        (
            {"returncode": -signal.SIGTERM, "forced": "terminated", "status": _SDDP},
            "killed",
            "Interrupted",
        ),
        (
            {"returncode": -signal.SIGKILL, "forced": "killed"},
            "killed",
            "gtopt was killed",
        ),
    ],
)
def test_classify(kwargs: dict, kind: str, title_frag: str) -> None:
    rc = kwargs.pop("returncode")
    out = classify(rc, **kwargs)
    assert out.kind == kind
    assert title_frag in out.title
    assert out.style in {"green", "yellow", "cyan", "red"}
    assert out.icon and out.ascii_icon
    assert out.ok == (kind in ("optimal", "converged"))


def test_classify_lines_carry_progress_and_solution() -> None:
    out = classify(
        0, {**_SDDP, "status": "max_iterations", "gap": 0.032}, {"status": 0}
    )
    assert out.lines == ["iteration 8/100, gap 3.2%", "solution status: optimal"]
    assert classify(0, {"iteration": 3}).lines[0] == "iteration 3"
    forced = classify(-15, _SDDP, forced="terminated")
    assert "last progress: iteration 8/100, gap 0.04%" in forced.lines
    assert classify(0, {}, {"stale": True}).lines[0].startswith("solution.csv is from")


def test_run_result_outcome_uses_flags() -> None:
    res = RunResult(returncode=0, stop_requested=True)
    assert res.outcome().kind == "stopped"
    assert RunResult(returncode=0).outcome(stop_requested=True).kind == "stopped"
