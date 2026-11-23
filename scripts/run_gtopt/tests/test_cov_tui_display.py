# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch tests for run_gtopt._tui: trackers, panels, keys and render loop.

The terminal is never touched: termios/tty/select and Rich ``Live`` input
are faked, and panels are rendered into a recording ``Console``.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from run_gtopt import _tui
from run_gtopt._tui import (
    _GRID_ELASTIC,
    _GRID_FORWARD,
    _GRID_INFEASIBLE,
    _MODE_CONVERGENCE,
    _MODE_DASHBOARD,
    _MODE_GRID,
    SDDPGridTracker,
    SolverDisplay,
    SolverPhaseTracker,
    _build_async_panel,
    _build_convergence,
    _build_header,
    _build_plan_panel,
    _build_progress,
    _build_sddp_grid,
    _build_stats_overlay,
    _build_system,
    _enter_cbreak,
    _exit_cbreak,
    _find_planning_json,
    _find_status_file,
    _format_mb,
    _format_number,
    _has_resource_telemetry,
    _heat_style,
    _load_status,
    _poll_key,
)


def _render(panel: Any) -> str:
    from rich.console import Console  # pylint: disable=import-outside-toplevel

    console = Console(width=160, file=io.StringIO(), record=True)
    console.print(panel)
    return console.export_text()


# ---------------------------------------------------------------------------
# SDDPGridTracker
# ---------------------------------------------------------------------------


def test_grid_tracker_tags_and_kappa() -> None:
    tr = SDDPGridTracker()
    tr.process_line("SDDP Elastic [i1 s0 p2] solve")
    tr.process_line("SDDP Forward [i1 s0 p3] infeasible LP")
    tr.process_line("SDDP Forward [i1 s0 p3] ok")  # lower priority kept out
    tr.process_line("SDDP Kappa [i1 s0 p1] high kappa -")  # ValueError
    tr.process_line("SDDP Kappa [i1 s0 p1] high kappa 3e9")
    tr.process_line("SDDP Kappa [i1 s0 p1] no value")
    assert tr.get_cell(0, 1, 2) == _GRID_ELASTIC
    assert tr.get_cell(0, 1, 3) == _GRID_INFEASIBLE
    assert tr.kappa_warnings == 3
    assert tr.max_kappa == pytest.approx(3e9)
    assert tr.elastic_count == 1
    assert tr.infeasible_count == 1


def test_grid_tracker_load_from_status_skips_and_priority() -> None:
    tr = SDDPGridTracker()
    tr.load_from_status({})
    tr.load_from_status(
        {
            "phase_grid": {
                "rows": [
                    {"i": 0, "s": 0, "cells": ""},
                    {"i": 0, "s": 0, "cells": {"1": "B", "2": "."}},
                    {"i": 0, "s": 0, "cells": "F."},  # F < B: not overwritten
                ]
            }
        }
    )
    assert tr.get_cell(0, 0, 0) == _tui._GRID_BACKWARD  # pylint: disable=protected-access
    assert tr.scenes == [0]


# ---------------------------------------------------------------------------
# SolverPhaseTracker
# ---------------------------------------------------------------------------


def test_phase_tracker_done_twice_and_detail_variants() -> None:
    tr = SolverPhaseTracker()
    tr.process_line("Parse all input files time 1s")
    tr.process_line("Parse all input files time 1s")  # already done: no-op
    assert tr.states["parse"].status == "done"
    # detail while not active is ignored
    tr.process_line("SDDP Iter [i1]: === starting (1 of 5) ===")
    assert tr.states["optimize"].detail == ""
    tr.process_line("=== System optimization ===")
    tr.process_line("MonolithicMethod: scene 1 done in 2s (1/4)")
    assert tr.states["optimize"].detail == "scene 1/4"
    assert tr.states["sim_pass"].status == "skipped"
    tr.process_line("=== Output writing ===")  # finishes optimize
    assert tr.states["optimize"].status == "done"
    tr.process_line("Write output time 1s")
    assert tr.states["write"].status == "done"
    tr.finish_all()
    text = _render(_build_plan_panel(tr))
    assert "Simulation pass" not in text
    assert "Writing output files" in text


def test_phase_state_elapsed_pending() -> None:
    tr = SolverPhaseTracker()
    assert tr.states["parse"].elapsed == 0.0


def test_plan_panel_active_detail() -> None:
    tr = SolverPhaseTracker()
    tr.process_line("=== System optimization ===")
    tr.process_line("SDDP Iter [i1]: === starting (2 of 9) ===")
    assert "(iter 2/9)" in _render(_build_plan_panel(tr))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_format_helpers() -> None:
    assert _format_number(1234.5) == "1,234.5000"
    assert _format_mb(2048.0) == "2.0 GB"
    assert _format_mb(10.0) == "10 MB"
    assert _heat_style(5.0, 5.0, 5.0)[0] == _tui._HEAT_BLOCKS[0]  # pylint: disable=protected-access
    assert _heat_style(10.0, 0.0, 10.0)[0] == _tui._HEAT_BLOCKS[7]  # pylint: disable=protected-access


def test_load_status_non_dict_and_stat_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lst = tmp_path / "list.json"
    lst.write_text("[1, 2]", encoding="utf-8")
    assert _load_status(lst) == [1, 2]

    good = tmp_path / "s.json"
    good.write_text('{"a": 1}', encoding="utf-8")
    real_stat = Path.stat
    real_is_file = Path.is_file

    def _stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == good:
            raise OSError("gone")
        return real_stat(self, *args, **kwargs)

    def _is_file(self: Path) -> bool:
        return True if self == good else real_is_file(self)

    monkeypatch.setattr(Path, "stat", _stat)
    monkeypatch.setattr(Path, "is_file", _is_file)
    assert _load_status(good) == {"a": 1}


def test_find_status_file_bad_json(tmp_path: Path) -> None:
    case = tmp_path / "c"
    case.mkdir()
    (case / "c.json").write_text("{bad", encoding="utf-8")
    assert _find_status_file(case) == case / "output" / "solver_status.json"


def test_find_planning_json_glob_skips_dirs(tmp_path: Path) -> None:
    (tmp_path / "x_sanitized.json").mkdir()
    (tmp_path / "plan.json").write_text("{}", encoding="utf-8")
    assert _find_planning_json(tmp_path) == tmp_path / "plan.json"


def test_has_resource_telemetry() -> None:
    assert _has_resource_telemetry({"realtime": {"active_workers": [1]}})
    assert _has_resource_telemetry({"pool_memory_percent": 3.0})
    assert not _has_resource_telemetry({})


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------


def test_enter_and_exit_cbreak(monkeypatch: pytest.MonkeyPatch) -> None:
    import termios  # pylint: disable=import-outside-toplevel
    import tty  # pylint: disable=import-outside-toplevel

    calls: list[Any] = []

    class _Stdin:
        @staticmethod
        def fileno() -> int:
            return 0

    monkeypatch.setattr(sys, "stdin", _Stdin())
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: ["old", fd])
    monkeypatch.setattr(tty, "setcbreak", lambda fd: calls.append(("cbreak", fd)))
    monkeypatch.setattr(
        termios, "tcsetattr", lambda fd, when, s: calls.append(("set", fd, s))
    )
    old = _enter_cbreak()
    assert old == ["old", 0]
    _exit_cbreak(old)
    assert calls == [("cbreak", 0), ("set", 0, ["old", 0])]

    def _raise(*_a: Any) -> None:
        raise termios.error("no tty")

    monkeypatch.setattr(termios, "tcsetattr", _raise)
    _exit_cbreak(old)  # swallowed


def test_poll_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import select  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(sys, "stdin", io.StringIO("q"))
    monkeypatch.setattr(select, "select", lambda r, _w, _x, _t: (r, [], []))
    assert _poll_key(True) == "q"
    monkeypatch.setattr(select, "select", lambda _r, _w, _x, _t: ([], [], []))
    assert _poll_key(True) is None

    def _raise(*_a: Any) -> Any:
        raise ValueError("closed")

    monkeypatch.setattr(select, "select", _raise)
    assert _poll_key(True) is None
    assert _poll_key(False) is None


# ---------------------------------------------------------------------------
# Panel builders
# ---------------------------------------------------------------------------


def test_header_variants() -> None:
    text = _render(
        _build_header(
            "c",
            {
                "status": "running",
                "lower_bound": 1.0,
                "upper_bound": 2.0,
                "iteration": 3,
                "max_iterations": 10,
                "history": [{"gap": 0.5}, {"gap": 0.1}],
            },
            1.0,
            {"method": "sddp", "solver": "highs"},
        )
    )
    assert "Solver: highs" in text
    assert "Gap" not in text.split("ΔGap", maxsplit=1)[0]  # gap None -> no Gap field
    assert "-40.00%" in text
    assert "iter 3/10" in text


def test_progress_scenes_done() -> None:
    text = _render(
        _build_progress({"iteration": 1, "max_iterations": 4, "scenes_done": 2})
    )
    assert "scenes: 2" in text
    assert "Solving" in _render(_build_progress({}))


def test_async_panel() -> None:
    assert _build_async_panel({}) is None
    text = _render(
        _build_async_panel(
            {
                "async": {
                    "spread": 2,
                    "max_async_spread": 2,
                    "converged_scenes": 1,
                    "pool_cpu_load": 55.0,
                    "scene_iterations": [3, 4, 5],
                    "scene_states": ["done", "training"],
                }
            }
        )
    )
    assert "2/2" in text
    assert "CPU load" in text
    assert "s0:3" in text
    assert "s2:5" in text
    text = _render(_build_async_panel({"async": {"spread": 0, "max_async_spread": 1}}))
    assert "CPU load" not in text


def test_system_panel_pool_fallbacks() -> None:
    text = _render(
        _build_system(
            {
                "pool_process_rss_mb": 2048.0,
                "pool_memory_percent": 0.0,
                "async": {"pool_tasks_active": 2},
                "lp_task_stats": {"dispatched": 5, "avg_cpu_pct": 10.0},
                "realtime": {"memory_percent": [10.0, 20.0]},
            }
        )
    )
    assert "Pool: 2 active  0 pending" in text
    assert "gtopt: 2.0 GB" in text
    assert "Free" not in text
    assert "5 tasks" in text
    assert "Mem" in text
    text = _render(
        _build_system(
            {"async": {"pool_tasks_pending": 4}, "pool_available_memory_mb": 9}
        )
    )
    assert "Pool: 0 active  4 pending" in text
    assert "Free: 9 MB" in text


def test_stats_overlay_variants() -> None:
    text = _render(
        _build_stats_overlay(
            {
                "elements": {"Bus": 1, "Generator": 2, "Demand": 3},
                "solver": "clp",
                "scale_objective": 1000,
                "use_kirchhoff": True,
                "use_single_bus": False,
            }
        )
    )
    assert "Solver" in text
    assert "Demand" in text
    assert "Single bus" in text
    text = _render(_build_stats_overlay({"elements": {}, "use_kirchhoff": True}))
    assert "Kirchhoff" in text
    assert "Solver" not in text


def test_sddp_grid_indicators_and_empty() -> None:
    tr = SDDPGridTracker()
    assert "all  (" not in _render(_build_sddp_grid(tr))
    tr.process_line("SDDP Kappa [i0 s0 p0] warning")  # no value
    tr.process_line("SDDP Forward [i0 s0 p0] elastic")
    tr.process_line("SDDP Forward [i0 s1 p1] infeasible")
    text = _render(_build_sddp_grid(tr, max_visible_scenes=1, active_scene_hint=None))
    assert "kappa:1" in text
    assert "elastic:1" in text
    assert "infeas:1" in text
    assert "active:" not in text
    tr.process_line("SDDP Kappa [i0 s0 p0] high kappa 1e8")
    assert "max 1.0e+08" in _render(_build_sddp_grid(tr))


def test_convergence_panel() -> None:
    assert "Waiting for iteration data" in _render(_build_convergence({}))
    text = _render(
        _build_convergence(
            {"history": [{"lower_bound": 1, "upper_bound": 2, "gap": 0.05}]}
        )
    )
    assert "Per-scene data not yet available" in text
    hist = [
        {
            "lower_bound": i,
            "upper_bound": 10 - i,
            "gap": g,
            "scene_upper_bounds": [float(i), float(i + 1)],
        }
        for i, g in enumerate([0.5, 0.005])
    ]
    hist.insert(1, {"lower_bound": 0, "upper_bound": 9, "gap": 0.2})
    text = _render(_build_convergence({"history": hist}))
    assert "Scene Costs" in text
    assert "Per-Scene Trends" in text
    many = [
        {
            "lower_bound": 0,
            "upper_bound": 1,
            "gap": 0.2,
            "scene_upper_bounds": [1.0] * 25,
        }
    ]
    text = _render(_build_convergence({"history": many}))
    assert "s20" in text
    assert "s21" not in text
    assert "Per-Scene Trends" not in text


# ---------------------------------------------------------------------------
# SolverDisplay
# ---------------------------------------------------------------------------


def _display(tmp_path: Path, **kwargs: Any) -> SolverDisplay:
    case = tmp_path / "case"
    case.mkdir(exist_ok=True)
    return SolverDisplay(case_name="case", case_dir=case, **kwargs)


def test_add_log_line_detection(tmp_path: Path) -> None:
    disp = _display(tmp_path)
    disp.add_log_line("no solver here\n")
    disp.add_log_line("  Solver: cplex/22\n")
    disp.add_log_line("MonolithicSolver: start\n")
    assert disp._system_stats == {  # pylint: disable=protected-access
        "solver": "cplex/22",
        "method": "monolithic",
    }
    disp.add_log_line("  Solver: other\n")
    assert disp._system_stats["solver"] == "cplex/22"  # pylint: disable=protected-access


def test_start_with_hint_and_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disp = _display(tmp_path, solver_hint="highs")
    monkeypatch.setattr(disp, "_run_loop", lambda: None)
    disp.stop()  # no thread yet
    disp.start()
    disp.stop()
    assert disp._system_stats["solver"] == "highs"  # pylint: disable=protected-access


def test_sync_stats_from_status(tmp_path: Path) -> None:
    disp = _display(tmp_path)
    disp._sync_stats_from_status()  # pylint: disable=protected-access
    disp._status = {  # pylint: disable=protected-access
        "solver": "",
        "method": "",
        "phase_grid": {"rows": [{"i": 0, "s": 1, "cells": "F"}]},
    }
    disp._sync_stats_from_status()  # pylint: disable=protected-access
    assert disp._grid_tracker.scenes == [1]  # pylint: disable=protected-access
    disp._status = {"solver": "cbc", "method": "sddp"}  # pylint: disable=protected-access
    disp._sync_stats_from_status()  # pylint: disable=protected-access
    assert disp._system_stats == {"solver": "cbc", "method": "sddp"}  # pylint: disable=protected-access


def test_print_final_variants(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    disp = _display(tmp_path)
    disp._status = {"converged": True, "iteration": 4, "gap": 0.001}  # pylint: disable=protected-access
    disp.print_final(0)
    out = capsys.readouterr().out
    assert "Converged at iteration 4" in out
    assert "Final gap" in out
    disp._status = {}  # pylint: disable=protected-access
    disp.print_final(0)
    out = capsys.readouterr().out
    assert "Converged" not in out
    disp._stop_sent = True  # pylint: disable=protected-access
    disp.print_final(1)
    assert "Stopped" in capsys.readouterr().out


def test_handle_keys_modes_and_stats_merge(tmp_path: Path) -> None:
    disp = _display(tmp_path)
    (tmp_path / "case" / "case.json").write_text(
        json.dumps({"options": {"method": "sddp"}, "system": {"bus_array": [{}]}}),
        encoding="utf-8",
    )
    disp._system_stats = {"method": "cascade", "solver": ""}  # pylint: disable=protected-access
    disp._handle_key("i")  # pylint: disable=protected-access
    stats = disp._system_stats  # pylint: disable=protected-access
    assert stats["method"] == "cascade"  # log-detected value wins
    assert stats["elements"] == {"Bus": 1}
    for key, mode in (("\t", 1), ("1", 0), ("2", 1), ("3", 2)):
        disp._handle_key(key)  # pylint: disable=protected-access
        assert disp._display_mode == mode  # pylint: disable=protected-access
    disp._handle_key("x")  # pylint: disable=protected-access
    disp._handle_key("j")  # not grid mode -> ignored  # pylint: disable=protected-access
    assert disp._grid_scroll_offset == 0  # pylint: disable=protected-access


def test_scroll_grid_keys(tmp_path: Path) -> None:
    disp = _display(tmp_path)
    disp._display_mode = _MODE_GRID  # pylint: disable=protected-access
    disp._handle_key("j")  # no scenes -> no-op  # pylint: disable=protected-access
    assert disp._grid_user_scrolled is False  # pylint: disable=protected-access
    for sc in range(10):
        disp.add_log_line(f"SDDP Forward [i0 s{sc} p0] ok")
    disp._handle_key("J")  # pylint: disable=protected-access
    assert disp._grid_scroll_offset == 5  # pylint: disable=protected-access
    disp._handle_key("K")  # pylint: disable=protected-access
    assert disp._grid_scroll_offset == 0  # pylint: disable=protected-access
    disp._handle_key("G")  # pylint: disable=protected-access
    assert disp._grid_scroll_offset == 9  # pylint: disable=protected-access
    disp._handle_key("Q")  # pylint: disable=protected-access
    assert disp.quit_requested.is_set()


def test_grid_visible_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import rich.console  # pylint: disable=import-outside-toplevel

    disp = _display(tmp_path)
    assert disp._grid_visible_window() == (None, None)  # pylint: disable=protected-access

    class _BrokenConsole:
        @property
        def size(self) -> Any:
            raise RuntimeError("no terminal")

    monkeypatch.setattr(rich.console, "Console", _BrokenConsole)
    for sc in range(6):
        disp.add_log_line(f"SDDP Forward [i0 s{sc} p0] ok")
    # height 40 -> budget = (40-22)//14 = 1
    disp._grid_scroll_offset = 99  # pylint: disable=protected-access
    budget, active = disp._grid_visible_window()  # pylint: disable=protected-access
    assert budget == 1
    assert active == 5
    assert disp._grid_scroll_offset == 5  # follows active scene  # pylint: disable=protected-access
    disp.add_log_line("SDDP Forward [i0 s0 p0] ok")
    disp._grid_visible_window()  # pylint: disable=protected-access
    assert disp._grid_scroll_offset == 0  # pylint: disable=protected-access
    disp.add_log_line("SDDP Forward [i0 s2 p0] ok")
    disp._grid_scroll_offset = 2  # pylint: disable=protected-access
    disp._grid_visible_window()  # active inside window  # pylint: disable=protected-access
    assert disp._grid_scroll_offset == 2  # pylint: disable=protected-access
    disp._grid_user_scrolled = True  # pylint: disable=protected-access
    disp._grid_visible_window()  # pylint: disable=protected-access


def test_cmd_stop_uses_known_status_file_and_handles_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disp = _display(tmp_path)
    out = tmp_path / "out"
    disp._status_file = out / "solver_status.json"  # pylint: disable=protected-access
    disp._cmd_stop()  # pylint: disable=protected-access
    assert (out / "sddp_stop_request.json").is_file()
    disp._cmd_stop()  # already sent -> no-op  # pylint: disable=protected-access

    disp2 = _display(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")
    disp2._status_file = blocker / "x" / "solver_status.json"  # pylint: disable=protected-access
    disp2._cmd_stop()  # mkdir fails -> logged  # pylint: disable=protected-access
    assert disp2._stop_sent is False  # pylint: disable=protected-access


_SDDP_STATUS: dict[str, Any] = {
    "status": "running",
    "iteration": 2,
    "max_iterations": 10,
    "lower_bound": 1.0,
    "upper_bound": 2.0,
    "gap": 0.5,
    "async": {"spread": 1, "max_async_spread": 2},
    "history": [{"iteration": 1, "lower_bound": 1.0, "upper_bound": 2.0, "gap": 0.5}],
    "realtime": {"cpu_loads": [10.0]},
}


@pytest.mark.parametrize(
    ("status", "mode", "help_", "stats", "expect"),
    [
        (_SDDP_STATUS, _MODE_DASHBOARD, True, False, "Async Scenes"),
        (
            {"total_scenes": 3, "scenes_done": 1, "realtime": {"cpu_loads": [5.0]}},
            _MODE_DASHBOARD,
            False,
            True,
            "CPU",
        ),
        ({"total_scenes": 3}, _MODE_DASHBOARD, False, False, "Solving"),
        (
            {**_SDDP_STATUS, "async": None, "history": [], "realtime": {}},
            0,
            0,
            0,
            "Iteration",
        ),
        (_SDDP_STATUS, _MODE_GRID, False, False, "SDDP Phase Grid"),
        ({}, _MODE_GRID, False, False, "case"),
        (_SDDP_STATUS, _MODE_CONVERGENCE, False, False, "Convergence"),
        ({}, _MODE_CONVERGENCE, False, False, "Waiting for iteration data"),
    ],
)
def test_build_display_modes(
    tmp_path: Path,
    status: dict[str, Any],
    mode: int,
    help_: bool,
    stats: bool,
    expect: str,
) -> None:
    disp = _display(tmp_path)
    disp.add_log_line("=== System optimization ===")
    if mode == _MODE_GRID and status:
        disp.add_log_line("SDDP Forward [i0 s0 p0] ok")
    disp._status = dict(status)  # pylint: disable=protected-access
    disp._display_mode = mode  # pylint: disable=protected-access
    disp._show_help = bool(help_)  # pylint: disable=protected-access
    disp._show_stats = bool(stats)  # pylint: disable=protected-access
    text = _render(disp._build_display())  # pylint: disable=protected-access
    assert expect in text
    if status.get("max_iterations"):
        assert disp._phase_tracker.states["optimize"].detail == "iter 2/10"  # pylint: disable=protected-access
    elif status.get("total_scenes") is not None:
        assert disp._phase_tracker.states["optimize"].detail == "scene 1/3" or (  # pylint: disable=protected-access
            "scenes_done" not in status
        )


def test_run_loop_handles_keys_and_final_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disp = _display(tmp_path, poll_interval=0.0)
    out = tmp_path / "case" / "output"
    out.mkdir()
    (out / "solver_status.json").write_text(
        json.dumps({"status": "running", "solver": "highs"}), encoding="utf-8"
    )
    keys = ["2", None, "h"]
    exited: list[Any] = []

    def _poll(active: bool) -> str | None:
        assert active is True
        if keys:
            return keys.pop(0)
        disp._stop.set()  # pylint: disable=protected-access
        return None

    monkeypatch.setattr(_tui, "_enter_cbreak", lambda: "OLD")
    monkeypatch.setattr(_tui, "_exit_cbreak", exited.append)
    monkeypatch.setattr(_tui, "_poll_key", _poll)
    disp._run_loop()  # pylint: disable=protected-access
    assert exited == ["OLD"]
    assert disp._display_mode == _MODE_GRID  # pylint: disable=protected-access
    assert disp._show_help is True  # pylint: disable=protected-access
    assert disp._system_stats["solver"] == "highs"  # pylint: disable=protected-access


def test_run_loop_swallows_render_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disp = _display(tmp_path)
    exited: list[Any] = []
    monkeypatch.setattr(_tui, "_enter_cbreak", lambda: None)
    monkeypatch.setattr(_tui, "_exit_cbreak", exited.append)

    def _boom() -> Any:
        raise RuntimeError("render failed")

    monkeypatch.setattr(disp, "_build_display", _boom)
    disp._run_loop()  # pylint: disable=protected-access
    assert exited == [None]


def test_grid_forward_constant_used() -> None:
    tr = SDDPGridTracker()
    tr.process_line("SDDP Forward [i0 s0 p0] ok")
    assert tr.get_cell(0, 0, 0) == _GRID_FORWARD


def test_header_bounds_without_iteration() -> None:
    text = _render(_build_header("c", {"lower_bound": 1.0}, 0.0))
    assert "iter " not in text
    assert "—" in text


def test_sync_keeps_log_detected_method(tmp_path: Path) -> None:
    disp = _display(tmp_path)
    disp._system_stats = {"solver": "x", "method": "cascade"}  # pylint: disable=protected-access
    disp._status = {"method": "sddp"}  # pylint: disable=protected-access
    disp._sync_stats_from_status()  # pylint: disable=protected-access
    assert disp._system_stats["method"] == "cascade"  # pylint: disable=protected-access


def test_build_display_active_without_progress_keys(tmp_path: Path) -> None:
    disp = _display(tmp_path)
    disp.add_log_line("=== System optimization ===")
    disp._status = {"status": "running"}  # pylint: disable=protected-access
    _render(disp._build_display())  # pylint: disable=protected-access
    assert disp._phase_tracker.states["optimize"].detail == ""  # pylint: disable=protected-access
