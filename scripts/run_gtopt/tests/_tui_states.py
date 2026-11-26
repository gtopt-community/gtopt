# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Recorded dashboard states for the snapshot / replay tests.

Every builder returns a deterministic :class:`RunState` (fixed clock,
fixed data), so :func:`render_text` gives the same frame on every run.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from run_gtopt.tui.frame import render_frame
from run_gtopt.tui.model import MODE_CONVERGENCE, MODE_GRID, RunState, layout_for
from run_gtopt.tui.parse import PlanSnapshot, SDDPGridTracker, SolverPhaseTracker

#: A recorded run of ``cases/sddp_hydro_3phase`` (HiGHS), laid out as
#: gtopt wrote it: ``<case>.json``, ``results/solver_status.json``,
#: ``results/logs/gtopt_1.log.txt`` (plugin paths scrubbed; ``output/``
#: and ``*.log`` are git-ignored, hence ``results/`` and ``.txt``).
REPLAY_DIR = Path(__file__).parent / "data" / "tui_replay" / "sddp_hydro_3phase"
FRAME_HEIGHT = 50


class FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def render_text(
    state: RunState, width: int, *, color: bool = True, height: int = FRAME_HEIGHT
) -> str:
    """The frame of *state* as a terminal of *width* x *height* shows it.

    ``color=False`` is what ``NO_COLOR`` gives (bold / dim kept, no
    colours).  The environment is ignored; trailing blanks are stripped
    so the goldens survive whitespace hooks.
    """
    from rich.console import Console  # noqa: PLC0415

    out = io.StringIO()
    console = Console(
        file=out,
        width=width,
        height=height,
        force_terminal=True,
        color_system="standard",
        no_color=not color,
        legacy_windows=False,
        _environ={},
    )
    console.print(render_frame(state, layout_for(width, height)))
    text = out.getvalue()
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def plan_from_log(lines: list[str], step: float = 1.5) -> PlanSnapshot:
    """The plan checklist after *lines*, each taking *step* seconds."""
    clock = FakeClock()
    tracker = SolverPhaseTracker(clock=clock)
    for line in lines:
        clock.advance(step)
        tracker.process_line(line)
    clock.advance(step)
    tracker.tick()
    return tracker.snapshot()


_BOOT_LOG = [
    "[info]   Parsing input file case.json",
    "[info]   Parse all input files time 0.010s",
    "[info] Planning validation passed",
    "[info] === Building LP model ===",
    "[info]   Build lp time 0.017s",
    "[info] === System optimization ===",
]


def _realtime() -> dict[str, Any]:
    return {
        "cpu_loads": [20.0, 40.0, 60.0, 80.0, 75.0],
        "active_workers": [4, 4, 6, 8, 8],
        "memory_percent": [10.0, 11.0, 12.0, 12.0, 13.0],
        "process_rss_mb": [500, 700, 900, 1100, 1200],
        "available_memory_mb": [200000, 199000, 198000, 197500, 197000],
    }


def _history(n: int) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        ub = 1.0e6 * (1.0 + 0.5 / (i + 1))
        lb = 1.0e6 * (1.0 - 0.4 / (i + 1))
        out.append(
            {
                "iteration": i + 1,
                "lower_bound": lb,
                "upper_bound": ub,
                "gap": (ub - lb) / ub,
                "cuts_added": 6,
                "forward_pass_s": 1.25 + 0.1 * i,
                "backward_pass_s": 2.5 + 0.1 * i,
                "iteration_s": 3.75 + 0.2 * i,
                "scene_upper_bounds": [ub * f for f in (0.9, 1.0, 1.1)],
            }
        )
    return out


def sddp_grid(iterations: int = 3, scenes: int = 3, phases: int = 4) -> SDDPGridTracker:
    """A grid tracker fed with forward/backward lines (+ one elastic)."""
    grid = SDDPGridTracker()
    for it in range(1, iterations + 1):
        for sc in range(scenes):
            for ph in range(1, phases + 1):
                grid.process_line(f"SDDP Forward [i{it} s{sc} p{ph}/{phases}]: ok")
            for ph in range(phases, 1, -1):
                grid.process_line(f"SDDP Backward [i{it} s{sc} p{ph}]: cut")
    grid.process_line(f"SDDP Forward [i{iterations} s1 p2]: elastic solve")
    grid.process_line(f"SDDP Forward [i{iterations} s2 p3]: ok")
    return grid


def no_data() -> RunState:
    """gtopt started, nothing written yet."""
    return RunState(case_name="ieee_9b", plan=plan_from_log([]))


def monolithic_running() -> RunState:
    """Monolithic solve, 1 of 4 scenes done."""
    return RunState(
        case_name="ieee_9b",
        elapsed=12.0,
        status={
            "status": "running",
            "total_scenes": 4,
            "scenes_done": 1,
            "method": "monolithic",
            "realtime": _realtime(),
        },
        system_stats={"method": "monolithic", "solver": "highs/1.16.0"},
        log_lines=(
            "[info] MonolithicMethod: scene 0 done (1/4)",
            "[info]   Solver: highs/1.16.0",
        ),
        plan=plan_from_log(
            [*_BOOT_LOG, "[info] MonolithicMethod: scene 0 done in 2.1s (1/4)"]
        ),
    )


def _sddp_status(n_iter: int, **extra: Any) -> dict[str, Any]:
    history = _history(n_iter)
    last = history[-1]
    status: dict[str, Any] = {
        "status": "running",
        "iteration": n_iter,
        "max_iterations": 50,
        "lower_bound": last["lower_bound"],
        "upper_bound": last["upper_bound"],
        "gap": last["gap"],
        "converged": False,
        "current_pass": 1,
        "method": "sddp",
        "history": history,
        "realtime": _realtime(),
        "lp_task_stats": {
            "dispatched": 120,
            "avg_cpu_pct": 87.0,
            "avg_rss_delta_mb": 3.5,
        },
    }
    status.update(extra)
    return status


def _sddp_log(n_iter: int) -> list[str]:
    return [
        *_BOOT_LOG,
        *(
            f"[info] SDDP Iter [i{i}]: === starting ({i} of 50) ==="
            for i in range(1, n_iter + 1)
        ),
    ]


def sddp_running(mode: int = 0) -> RunState:
    """SDDP at iteration 3 of 50, forward pass in phase 3."""
    grid = sddp_grid()
    return RunState(
        case_name="hydro_3phase",
        elapsed=95.0,
        status=_sddp_status(3),
        system_stats={
            "method": "sddp",
            "solver": "highs/1.16.0",
            "convergence_tol": 0.01,
        },
        log_lines=(
            "[info] SDDP Forward [i3 s1 p2]: elastic solve",
            "[warning] SDDP Kappa [i3 s0 p2]: high kappa 3.2e+09",
            "[info] SDDP Forward [i3 s2 p3]: ok",
        ),
        log_path=Path("gtopt_1.log"),
        plan=plan_from_log(_sddp_log(3)),
        grid=grid.snapshot() if mode == MODE_GRID else None,
        grid_active_cell=grid.active_cell,
        grid_max_phase=grid.max_phase,
        grid_visible_scenes=2 if mode == MODE_GRID else None,
        grid_active_scene=2 if mode == MODE_GRID else None,
        mode=mode,
    )


def sddp_converged(mode: int = 0) -> RunState:
    """SDDP converged after 6 iterations; output written."""
    status = _sddp_status(6, status="converged", converged=True, current_pass=0)
    status["gap"] = 0.004
    return RunState(
        case_name="hydro_3phase",
        elapsed=3725.0,
        status=status,
        system_stats={
            "method": "sddp",
            "solver": "highs/1.16.0",
            "convergence_tol": 0.01,
        },
        log_lines=(
            "[info] SDDP: converged (gap=0.40%)",
            "[info]   Write output time 0.020s",
        ),
        plan=plan_from_log(
            [
                *_sddp_log(6),
                "[info] SDDP Sim [i7]: === simulation pass ===",
                "[info] SDDP Sim [i7]: done in 0.002s",
                "[info] === Solution statistics ===",
                "[info] === Output writing ===",
                "[info]   Write output time 0.020s",
            ]
        ),
        mode=mode,
    )


def failed() -> RunState:
    """SDDP hitting infeasible cells; errors in the log."""
    return RunState(
        case_name="hydro_3phase",
        elapsed=40.0,
        status=_sddp_status(2),
        system_stats={"method": "sddp", "solver": "highs/1.16.0"},
        log_lines=(
            "[error] SDDP Forward [i2 s0 p3]: infeasible (status 2)",
            "[critical] SDDP Forward [i2 s1 p3]: infeasible, error LP written",
        ),
        plan=plan_from_log(_sddp_log(2)),
        grid_active_cell=(1, 2, 3),
        grid_max_phase=3,
        infeasible_count=2,
    )


#: Golden-frame scenarios: name -> builder.
SCENARIOS = {
    "no_data": no_data,
    "monolithic_running": monolithic_running,
    "sddp_running": sddp_running,
    "sddp_converged": sddp_converged,
    "failed": failed,
}

#: Extra views, rendered at 120 columns in colour only.
VIEWS = {
    "sddp_running_grid": lambda: sddp_running(MODE_GRID),
    "sddp_converged_convergence": lambda: sddp_converged(MODE_CONVERGENCE),
}


def replay_status() -> dict[str, Any]:
    """The recorded final ``solver_status.json`` of the replay run."""
    return json.loads(
        (REPLAY_DIR / "results" / "solver_status.json").read_text("utf-8")
    )


def replay_log() -> list[str]:
    """The recorded ``gtopt_1.log`` of the replay run."""
    return (
        (REPLAY_DIR / "results" / "logs" / "gtopt_1.log.txt")
        .read_text("utf-8")
        .splitlines()
    )
