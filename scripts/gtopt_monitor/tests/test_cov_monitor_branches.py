# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Extra branch tests for gtopt_monitor (GUI loop with mocked matplotlib)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtopt_monitor.gtopt_monitor import find_planning_json, main, run_gui
from gtopt_monitor.tests.test_gtopt_monitor import _make_mock_plt, _patch_matplotlib


def test_find_planning_json_skips_json_named_dirs(tmp_path: Path) -> None:
    (tmp_path / "a.json").mkdir()
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    assert find_planning_json(tmp_path) == tmp_path / "b.json"
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "x.json").mkdir()
    assert find_planning_json(empty) is None


def test_gui_memory_series_and_history_without_scenes(tmp_path: Path) -> None:
    fpath = tmp_path / "solver_status.json"
    fpath.write_text(
        json.dumps(
            {
                "status": "running",
                "realtime": {
                    "timestamps": [0.0, 1.0],
                    "cpu_loads": [10.0, 20.0],
                    "active_workers": [1, 1],
                    "memory_percent": [30.0, 31.0],
                    "process_rss_mb": [100.0, 110.0],
                },
                "history": [
                    {"iteration": 1, "gap": 0.5, "scene_upper_bounds": []},
                    {"iteration": 2, "gap": 0.4},
                ],
            }
        ),
        encoding="utf-8",
    )
    mock_plt, mock_ticker, _f1, _f2 = _make_mock_plt()
    mock_plt.fignum_exists.side_effect = [True, True, False]
    with _patch_matplotlib(mock_plt, mock_ticker):
        run_gui(fpath, poll_interval=0.0)
    mock_plt.ioff.assert_called_once()


def test_get_without_planning_json_exits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--get", "method", "--case-dir", str(tmp_path)])
    assert exc.value.code == 1
    assert "no planning JSON found" in capsys.readouterr().err
