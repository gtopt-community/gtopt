# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Golden frames of the dashboard: recorded RunStates rendered at 80 and
120 columns, in colour and under NO_COLOR.

Refresh after an intended layout change with
``PYTEST_UPDATE_GOLDEN=1 python -m pytest run_gtopt/tests/test_tui_snapshots.py``
and review the diff of ``tests/data/tui_frames/``.
"""

from __future__ import annotations

import dataclasses
import re
from importlib.metadata import version
from pathlib import Path

import pytest

from gtopt_shared.testing import assert_text_snapshot
from run_gtopt.tui.frame import render_frame
from run_gtopt.tui.model import OVERLAY_HELP, layout_for

from ._tui_states import SCENARIOS, VIEWS, render_text

GOLDEN_DIR = Path(__file__).parent / "data" / "tui_frames"
_REFRESH = "run_gtopt/tests/test_tui_snapshots.py"

# The goldens hold Rich's exact output; another Rich major may change
# spacing or escape codes without any change on our side.
_RICH_MAJOR = version("rich").split(".", maxsplit=1)[0]
_GOLDEN_RICH_MAJOR = (GOLDEN_DIR / "rich_major.txt").read_text("utf-8").strip()
pytestmark = pytest.mark.skipif(
    _RICH_MAJOR != _GOLDEN_RICH_MAJOR,
    reason=f"goldens rendered with rich {_GOLDEN_RICH_MAJOR}.x, have {_RICH_MAJOR}.x",
)

_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")


def _colour_codes(text: str) -> set[str]:
    """SGR parameters that set a foreground / background colour."""
    codes: set[str] = set()
    for params in _SGR_RE.findall(text):
        for p in params.split(";"):
            if p and (30 <= int(p) <= 49 or 90 <= int(p) <= 107):
                codes.add(p)
    return codes


@pytest.mark.parametrize("width", [80, 120])
@pytest.mark.parametrize("color", [True, False], ids=["color", "nocolor"])
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_frame_golden(name: str, width: int, color: bool) -> None:
    text = render_text(SCENARIOS[name](), width, color=color)
    tag = "color" if color else "nocolor"
    assert_text_snapshot(
        f"{name}_{width}_{tag}", text, GOLDEN_DIR, refresh_target=_REFRESH
    )


@pytest.mark.parametrize("name", sorted(VIEWS))
def test_view_golden(name: str) -> None:
    text = render_text(VIEWS[name](), 120)
    assert_text_snapshot(f"{name}_120_color", text, GOLDEN_DIR, refresh_target=_REFRESH)


def test_help_overlay_golden() -> None:
    state = dataclasses.replace(SCENARIOS["no_data"](), overlay=OVERLAY_HELP)
    text = render_text(state, 80)
    assert_text_snapshot(
        "help_overlay_80_color", text, GOLDEN_DIR, refresh_target=_REFRESH
    )


# -- behaviour checks on the same frames (not just "matches the golden") ----


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_no_color_has_no_colours(name: str) -> None:
    state = SCENARIOS[name]()
    assert _colour_codes(render_text(state, 120, color=True))
    assert not _colour_codes(render_text(state, 120, color=False))


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("width", [80, 120])
def test_frame_fits_width(name: str, width: int) -> None:
    plain = _SGR_RE.sub("", render_text(SCENARIOS[name](), width, color=False))
    assert max(len(line) for line in plain.splitlines()) <= width


def test_render_is_pure() -> None:
    state = SCENARIOS["sddp_running"]()
    first = render_text(state, 120)
    assert render_text(state, 120) == first
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.mode = 2  # type: ignore[misc]


def test_frame_content_follows_state() -> None:
    def plain(name: str, width: int = 120) -> str:
        return _SGR_RE.sub("", render_text(SCENARIOS[name](), width, color=False))

    assert "Waiting for solver output" in plain("no_data")
    assert "scenes 1/4" in plain("monolithic_running")
    running = plain("sddp_running")
    assert "Iteration 3/50" in running and "phase 3/4" in running
    assert "Iteration History" in running and "Solver Statistics" in running
    converged = plain("sddp_converged")
    assert "[converged]" in converged and "1:02:05" in converged
    assert "2 infeasible solve(s)" in plain("failed")
    # 80 columns -> compact layout: no statistics / resource panels.
    assert "Solver Statistics" not in plain("sddp_running", 80)
    assert "CPU" not in plain("sddp_running", 80)


def test_render_frame_layouts_differ() -> None:
    state = SCENARIOS["sddp_running"]()
    compact = render_frame(state, layout_for(80, 30))
    full = render_frame(state, layout_for(120, 60))
    assert len(compact.renderables) < len(full.renderables)
