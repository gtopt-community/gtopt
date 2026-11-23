# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage of line extraction and the loss-segment layout helpers."""

# pylint: disable=unbalanced-tuple-unpacking

from __future__ import annotations

from pathlib import Path

import pytest

from gtopt_shared.cli_flags import DEFAULT_LIFT_LINE_CAPS_PLEXOS
from plexos2gtopt import parsers as P
from plexos2gtopt.entities import LineSpec
from plexos2gtopt.tests.cov_parsers_kit import DbKit, bundle


def _line_db(name: str = "L1", *, endpoints: bool = True) -> tuple[DbKit, int]:
    kit = DbKit()
    a = kit.obj("Node", "a")
    b = kit.obj("Node", "b")
    line = kit.obj("Line", name)
    if endpoints:
        kit.mem("Line", "Node", line, a, coll_name="Node From")
        kit.mem("Line", "Node", line, b, coll_name="Node To")
    kit.sysprop("Line", line, "Max Flow", 100.0)
    return kit, line


def test_line_without_endpoints_is_skipped(tmp_path: Path) -> None:
    """A line lacking Node From / Node To is dropped."""
    kit, _ = _line_db(endpoints=False)
    assert not P.extract_lines(kit.db, bundle(tmp_path))


def test_all_off_line_recorded_as_shadow(tmp_path: Path) -> None:
    """Lin_Units all zero -> skipped and recorded in the shadow set."""
    kit, _ = _line_db()
    b = bundle(
        tmp_path, {"Lin_Units.csv": "YEAR,MONTH,DAY,PERIOD,BAND,L1\n2026,1,1,1,1,0\n"}
    )
    shadow: set[str] = set()
    assert not P.extract_lines(kit.db, b, shadow_lines_all_off_out=shadow)
    assert shadow == {"L1"}


def test_fractional_units_round_up_to_one(tmp_path: Path) -> None:
    """A sub-unit Lin_Units value still yields one circuit."""
    kit, _ = _line_db()
    b = bundle(
        tmp_path, {"Lin_Units.csv": "YEAR,MONTH,DAY,PERIOD,BAND,L1\n2026,1,1,1,1,0.5\n"}
    )
    (line,) = P.extract_lines(kit.db, b)
    assert line.units == 1


def test_default_lift_list_soft_caps_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A curated default-lift line with EL=1 becomes a lifted soft cap."""
    name = DEFAULT_LIFT_LINE_CAPS_PLEXOS.split(",", maxsplit=1)[0].strip()
    monkeypatch.setenv("GTOPT_LIFT_LINE_CAPS", DEFAULT_LIFT_LINE_CAPS_PLEXOS)
    kit, line_id = _line_db(name)
    kit.sysprop("Line", line_id, "Enforce Limits", 1)
    (line,) = P.extract_lines(kit.db, bundle(tmp_path))
    assert line.soft_cap and line.soft_cap_lifted
    assert line.enforce_limits == 1


def test_adaptive_env_parse_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unparseable env knobs fall back to the defaults."""
    monkeypatch.setenv("GTOPT_LOSS_ERROR_PCT", "bad")
    monkeypatch.setenv("GTOPT_NSEG_LOSSES", "bad")
    monkeypatch.delenv("GTOPT_LOSS_PWL_LAYOUT", raising=False)
    ln = LineSpec(
        object_id=1, name="L", bus_from="a", bus_to="b", resistance=1.0, tmax_ab=100.0
    )
    (out,) = P._apply_adaptive_loss_segments((ln,))
    assert 2 <= out.loss_segments <= 6


def _dyn(
    monkeypatch: pytest.MonkeyPatch, ls: list[float], err: float, nseg: str | None
) -> tuple[LineSpec, ...]:
    if nseg is None:
        monkeypatch.delenv("GTOPT_NSEG_LOSSES", raising=False)
    else:
        monkeypatch.setenv("GTOPT_NSEG_LOSSES", nseg)
    lines = tuple(
        LineSpec(object_id=i, name=f"l{i}", bus_from="a", bus_to="b")
        for i in range(len(ls))
    )
    lossy = [(i, lines[i], float(v)) for i, v in enumerate(ls)]
    return P._apply_dynamic_loss_layout(lines, err, lossy)


@pytest.mark.parametrize(
    ("ls", "err", "nseg"),
    [
        ([10, 100], 0.001, "12"),
        ([5, 2], 0.0074, "8"),
        ([1, 10, 1000], 0.003, "3"),
        ([50, 1000], 0.003, "4"),
        ([5, 1, 5], 0.0074, "12"),
        ([1, 100], 0.001, None),
        ([0, 100], 0.001, "bad"),
    ],
)
def test_dynamic_layout_budget_invariants(
    monkeypatch: pytest.MonkeyPatch, ls: list[float], err: float, nseg: str | None
) -> None:
    """Every lossy line gets 2 <= K <= ceiling and a valid layout."""
    out = _dyn(monkeypatch, ls, err, nseg)
    for ln, big_l in zip(out, ls, strict=True):
        if big_l <= 0:
            # Lossless entries are left untouched.
            blank = LineSpec(object_id=0, name="x", bus_from="a", bus_to="b")
            assert ln.loss_segments == blank.loss_segments
            assert ln.loss_pwl_layout == blank.loss_pwl_layout
            continue
        assert ln.loss_segments >= 2
        assert ln.loss_pwl_layout in ("uniform", "midpoint")
    ceiling = int(nseg) if nseg and nseg.isdigit() else 6
    assert all(ln.loss_segments <= max(2, ceiling) for ln in out)


def test_sos2_heavy_mode_without_lossy_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """``heavy`` auto mode on lossless lines is a no-op."""
    monkeypatch.setenv("GTOPT_LOSS_SOS2_AUTO", "heavy")
    monkeypatch.delenv("GTOPT_LOSS_SOS2_LINES", raising=False)
    ln = LineSpec(object_id=1, name="L", bus_from="a", bus_to="b")
    assert P._apply_loss_sos2_policy((ln,)) == (ln,)
