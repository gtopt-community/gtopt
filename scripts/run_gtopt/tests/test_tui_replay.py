# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Replay a recorded gtopt run through the dashboard controller.

The recording (``data/tui_replay/sddp_hydro_3phase``) is a real SDDP
run of ``cases/sddp_hydro_3phase``: its ``gtopt_1.log`` and final
``solver_status.json``.  The log is fed line by line into
:class:`SolverDisplay` with a fake clock; at each "─── iter N ───" line
the status of iteration N (the final status cut to N history rows) is
adopted, as the live poller would.  No thread, no terminal.
"""

from __future__ import annotations

import copy
import io
import logging
from pathlib import Path
from typing import Any

import pytest

from gtopt_shared.status import find_status_file
from gtopt_shared.testing import assert_text_snapshot
from run_gtopt._process import RunResult
from run_gtopt._tui import SolverDisplay
from run_gtopt.tui.model import MODE_GRID, RunState

from ._tui_states import FakeClock, REPLAY_DIR, render_text, replay_log, replay_status

GOLDEN_DIR = Path(__file__).parent / "data" / "tui_frames"
_REFRESH = "run_gtopt/tests/test_tui_replay.py"


def _status_at(final: dict[str, Any], iteration: int) -> dict[str, Any]:
    st = copy.deepcopy(final)
    st["history"] = st["history"][:iteration]
    last = st["history"][-1]
    st.update(
        iteration=iteration,
        lower_bound=last["lower_bound"],
        upper_bound=last["upper_bound"],
        gap=last["gap"],
        converged=bool(last["converged"]),
        status="converged" if last["converged"] else "running",
        current_pass=0 if last["converged"] else 1,
    )
    return st


def _console(width: int = 120, height: int = 50) -> Any:
    from rich.console import Console  # noqa: PLC0415

    return Console(
        file=io.StringIO(),
        width=width,
        height=height,
        force_terminal=True,
        color_system="standard",
        legacy_windows=False,
        _environ={},
    )


def _replay() -> tuple[SolverDisplay, FakeClock, list[RunState]]:
    """Feed the recording; return the display and the states captured
    after the build, after iteration 1 and at the end."""
    clock = FakeClock()
    disp = SolverDisplay(
        case_name="sddp_hydro_3phase",
        case_dir=REPLAY_DIR,
        clock=clock,
        console=_console(),
    )
    disp.load_planning_stats()
    final = replay_status()
    states: list[RunState] = []
    for line in replay_log():
        clock.advance(0.25)
        disp.add_log_line(line + "\n")
        if "Build lp time" in line:
            states.append(disp.snapshot())
        if line.split("] ", 2)[-1].startswith("─── iter "):
            iteration = int(line.split("─── iter ")[1].split()[0])
            disp.update_status(_status_at(final, iteration))
            if iteration == 1:
                states.append(disp.snapshot())
    disp.update_status(final)
    disp._phase_tracker.finish_all()  # pylint: disable=protected-access
    states.append(disp.snapshot())
    return disp, clock, states


def test_replay_state_progression() -> None:
    disp, _clock, (built, iter1, done) = _replay()

    def step(state: RunState, label: str) -> str:
        return next(st.status for st in state.plan.steps if st.label == label)

    # After "Build lp time": parsing + build done, nothing solving yet.
    assert step(built, "Building LP model") == "done"
    assert step(built, "Solving") == "pending"
    assert not built.has_sddp
    # Iteration 1: SDDP status adopted, forward pass grid seen.
    assert iter1.has_sddp and iter1.status["iteration"] == 1
    assert step(iter1, "Solving") == "active"
    assert iter1.grid_max_phase == 3
    # End: converged, every step done, durations from the fake clock.
    assert done.status["status"] == "converged"
    assert all(st.status == "done" for st in done.plan.steps)
    assert done.plan.steps[0].elapsed == pytest.approx(0.25)
    assert done.system_stats["method"] == "sddp"
    assert done.system_stats["solver"].startswith("highs")
    assert disp.warning_count == 0 and disp.error_count == 0


@pytest.mark.parametrize("index", [0, 1, 2], ids=["built", "iter1", "done"])
def test_replay_frames_golden(index: int) -> None:
    _disp, _clock, states = _replay()
    name = ("built", "iter1", "done")[index]
    assert_text_snapshot(
        f"replay_{name}_120_color",
        render_text(states[index], 120),
        GOLDEN_DIR,
        refresh_target=_REFRESH,
    )


def test_replay_grid_view_and_final_banner() -> None:
    disp, _clock, _states = _replay()
    disp._handle_key("2")  # pylint: disable=protected-access
    state = disp.snapshot()
    assert state.mode == MODE_GRID and state.grid is not None
    assert state.grid.scenes == [0]
    frame = render_text(state, 120, color=False)
    assert "SDDP Phase Grid" in frame

    disp.print_final(
        RunResult(returncode=0, output_dir=REPLAY_DIR / "results", status={})
    )
    out = disp.console.file.getvalue()
    assert "Last iterations" in out
    assert "Steps: Parsing input files" in out
    # The status of the replay (status={} falls back to the display's).
    assert "converged" in out.lower()


def test_replay_status_file_is_found() -> None:
    assert find_status_file(REPLAY_DIR) == REPLAY_DIR / "results" / "solver_status.json"


# -- dashboard failure -> plain fallback ------------------------------------


def test_render_failure_falls_back_to_plain(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    disp = SolverDisplay(
        case_name="c", case_dir=REPLAY_DIR, clock=FakeClock(), console=_console()
    )
    disp.add_log_line("[info] before the crash\n")

    def _boom() -> Any:
        raise RuntimeError("panel bug")

    monkeypatch.setattr(disp, "_build_display", _boom)
    with caplog.at_level(logging.WARNING, logger="run_gtopt._tui"):
        disp._run_loop()  # pylint: disable=protected-access
    assert disp.plain_fallback
    assert any(
        "live dashboard stopped (RuntimeError: panel bug)" in r.getMessage()
        for r in caplog.records
    )
    # The lines seen so far are printed, then every new one.
    disp.add_log_line("[info] after the crash\n")
    out = capsys.readouterr().out
    assert "before the crash" in out and "after the crash" in out


def test_render_failure_after_stop_prints_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    disp = SolverDisplay(
        case_name="c", case_dir=REPLAY_DIR, clock=FakeClock(), console=_console()
    )
    disp.add_log_line("[info] line\n")
    disp._stop.set()  # pylint: disable=protected-access

    def _boom() -> Any:
        raise RuntimeError("late")

    monkeypatch.setattr(disp, "_build_display", _boom)
    disp._run_loop()  # pylint: disable=protected-access
    assert disp.plain_fallback
    assert "line" not in capsys.readouterr().out
