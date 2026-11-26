# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""RunState and the tracker snapshots it is built from."""

from __future__ import annotations

from run_gtopt.tui.model import RunState
from run_gtopt.tui.parse import SDDPGridTracker, SolverPhaseTracker

from ._tui_states import FakeClock


def test_grid_snapshot_is_independent_and_windowed() -> None:
    grid = SDDPGridTracker()
    for it in (1, 2, 3):
        grid.process_line(f"SDDP Forward [i{it} s0 p1/2]: ok")
    snap = grid.snapshot(min_iter=2)
    grid.process_line("SDDP Forward [i4 s1 p2]: ok")
    # The copy does not see later lines ...
    assert snap.scenes == [0] and snap.max_iter == 3
    assert grid.scenes == [0, 1] and grid.max_iter == 4
    # ... and keeps only the iterations it was asked for.
    assert snap.get_cell(0, 1, 1) == 0
    assert snap.get_cell(0, 2, 1) == grid.get_cell(0, 2, 1) != 0
    assert snap.max_phase == 2


def test_phase_tracker_uses_injected_clock() -> None:
    clock = FakeClock(10.0)
    tracker = SolverPhaseTracker(clock=clock)
    tracker.process_line("=== Building LP model ===")
    clock.advance(4.0)
    snap = tracker.snapshot()
    build = next(st for st in snap.steps if st.label == "Building LP model")
    assert build.status == "active" and build.elapsed == 4.0
    clock.advance(1.0)
    tracker.process_line("Build lp time 5s")
    clock.advance(100.0)
    assert tracker.states["build_lp"].elapsed == 5.0
    # A snapshot is frozen: later progress does not change it.
    assert build.elapsed == 4.0


def test_run_state_fields() -> None:
    state = RunState(case_name="c", system_stats={"convergence_tol": "tight"})
    assert state.convergence_tol is None
    assert state.phase is None
    assert not state.has_sddp and not state.has_monolithic
    assert not state.history
    state = RunState(
        case_name="c",
        status={"max_iterations": 5, "history": [{"gap": 0.1}]},
        system_stats={"convergence_tol": 0.01},
        grid_active_cell=(0, 2, 3),
        grid_max_phase=7,
    )
    assert state.has_sddp and state.convergence_tol == 0.01
    assert state.phase == (3, 7)
    assert state.history == [{"gap": 0.1}]


def test_run_state_notice_priority() -> None:
    base = {"case_name": "c", "infeasible_count": 2, "keys_enabled": False}
    assert (
        RunState(**base, quit_pending=True, shutdown_msg="x")
        .notice[0]
        .startswith("Terminate gtopt now?")
    )
    assert RunState(**base, shutdown_msg="stopping…").notice == (
        "stopping…",
        "bold yellow",
    )
    assert "2 infeasible" in (RunState(**base).notice[0] or "")
    assert RunState(case_name="c", keys_enabled=False).notice[1] == "dim"
    assert RunState(case_name="c").notice == (None, "bold yellow")
