# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage of the user-constraint helper functions in parsers.py."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

from plexos2gtopt import parsers as P
from plexos2gtopt.entities import UserConstraintSpec
from plexos2gtopt.plexos_xml import PlexosDataRow
from plexos2gtopt.tests.cov_parsers_kit import DbKit


def test_expand_timeslice_empty_and_weekday_only() -> None:
    """An empty tag raises; a weekday-only tag covers every hour."""
    with pytest.raises(ValueError, match="empty timeslice"):
        P._expand_timeslice(" , ", datetime(2026, 4, 19), 24)
    # 2026-04-19 is a Sunday (W1); W1 -> the whole first day active.
    mask = P._expand_timeslice("W1", datetime(2026, 4, 19), 48)
    assert all(mask[:24])
    assert not any(mask[24:])


def test_hydro_min_mode_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown GTOPT_HYDRO_MIN_MODE falls back to soft with a warning."""
    monkeypatch.setenv("GTOPT_HYDRO_MIN_MODE", "bogus")
    seen: list[str] = []
    monkeypatch.setattr(P.logger, "warning", lambda msg, *a: seen.append(msg % a))
    P._hydro_min_mode.cache_clear()
    try:
        assert P._hydro_min_mode() == "soft"
    finally:
        P._hydro_min_mode.cache_clear()
    assert "not recognised" in seen[0]


@pytest.mark.parametrize("mode", ["soft", "hard"])
def test_load_plexos_hard_uc_list_without_hydro_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    """A list without hydro min/max names logs nothing in either mode."""
    monkeypatch.setenv("GTOPT_HYDRO_MIN_MODE", mode)
    path = tmp_path / "hard.txt"
    path.write_text("UC_A\nUC_B  # note\n")
    import gtopt_shared.resources as res  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(res, "package_data_file", lambda *_a, **_k: path)
    P._load_plexos_hard_uc_list.cache_clear()
    P._hydro_min_mode.cache_clear()
    try:
        assert P._load_plexos_hard_uc_list() == frozenset({"UC_A", "UC_B"})
    finally:
        P._load_plexos_hard_uc_list.cache_clear()
        P._hydro_min_mode.cache_clear()


def _row(did: int, value: float, **kw: object) -> PlexosDataRow:
    return PlexosDataRow(data_id=did, membership_id=1, property_id=1, value=value, **kw)  # type: ignore[arg-type]


def test_horizon_value_dated_rows() -> None:
    """Active dated rows win; expired-only rows fall back; sentinels drop."""
    start = datetime(2026, 4, 22)
    active = _row(1, 5.0, date_from=datetime(2026, 4, 1), date_to=datetime(2026, 5, 1))
    open_end = _row(2, 7.0, date_from=datetime(2026, 4, 1))
    expired = _row(3, 9.0, date_from=datetime(2024, 1, 1), date_to=datetime(2024, 1, 2))
    future = _row(4, 11.0, date_from=datetime(2027, 1, 1))
    assert P._horizon_value([active, open_end, expired], start) == pytest.approx(5.0)
    assert P._horizon_value([expired, future], start) == pytest.approx(9.0)
    assert P._horizon_value([_row(5, 1e30)], start) is None


def test_index_coefficient_rows_skips_dangling_and_zero() -> None:
    """Unknown parents and zero coefficients are dropped."""
    kit = DbKit()
    con = kit.obj("Constraint", "C")
    g1 = kit.obj("Generator", "G1")
    g2 = kit.obj("Generator", "G2")
    cid = kit.coll("Generator", "Constraint", "Constraints")
    pid = kit.prop(cid, "Generation Coefficient")
    kit.data(
        kit.mem("Generator", "Constraint", g1, con, coll_name="Constraints"), pid, 2.0
    )
    kit.data(
        kit.mem("Generator", "Constraint", g2, con, coll_name="Constraints"), pid, 0.0
    )
    kit.data(
        kit.mem("Generator", "Constraint", 999, con, coll_name="Constraints"), pid, 3.0
    )
    out = P._index_coefficient_rows(kit.db, cid, pid, kit.db.object_by_id())
    assert out == {con: [("G1", 2.0)]}


def test_shift_helpers_grow_buffer() -> None:
    """``_set_shift`` / ``_shift_at`` pad the buffer with zeros."""
    buf: list[float] = []
    P._set_shift(buf, 2, 4.0)
    assert buf == [0.0, 0.0, 4.0]
    assert P._shift_at(buf, 4) == 0.0
    assert len(buf) == 5


def test_describe_fuel_offtake_from_rhs_custom() -> None:
    """The RHS-Custom daily gas cap tail is appended."""
    desc = P._describe_user_constraint(
        "Gas_X",
        '2 * generator("G").generation <= 5',
        "<=",
        5.0,
        source_file="x.xml",
        fuel_offtake=True,
        from_rhs_custom=True,
    )
    assert "RHS Custom" in desc


def test_is_tautology_single_term_ops() -> None:
    """Equality and unknown operators on a single commitment term."""
    term = ['0 * commitment("uc_G").status']
    assert P._is_tautology_single_term(term, "=", 0.0)
    assert not P._is_tautology_single_term(['1 * commitment("uc_G").status'], "=", 0.0)
    assert not P._is_tautology_single_term(term, "<>", 0.0)


def test_parse_hydro_maxrampday_csv_bad_rows(tmp_path: Path) -> None:
    """Unparseable rows are skipped."""
    path = tmp_path / "r.csv"
    path.write_text(
        "NAME,YEAR,MONTH,DAY,PERIOD,VALUE\nA,2026,1,1,1,3\nA,2026,1,2,1,x\nB,2026\n"
    )
    out = P._parse_hydro_maxrampday_csv(path)
    assert out["A"] == [3.0]
    assert not out.get("B")


def test_build_period_sum_expression_edges() -> None:
    """Unparseable / empty terms -> None; status + startup -> mixed."""
    assert P.build_period_sum_expression(["garbage"], "<=", 1.0) is None
    assert P.build_period_sum_expression([], "<=", 1.0) is None
    out = P.build_period_sum_expression(
        ['1 * commitment("uc_G").status', ' + 1 * commitment("uc_G").startup'],
        "<=",
        3.0,
    )
    assert out is not None and out[1] == "mixed"


def test_nolimit_sentinel_needs_mw_term() -> None:
    """An expression with no MW-kind term is never a sentinel."""
    assert not P._is_nolimit_line_sentinel('waterway("W").flow <= 100000', 100000.0)


def _uc(name: str, expression: str, **kw: object) -> UserConstraintSpec:
    return UserConstraintSpec(name=name, expression=expression, **kw)  # type: ignore[arg-type]


def test_consolidate_gas_maxopday_with_block_layout() -> None:
    """The PLEXOS block layout maps blocks to calendar days."""
    specs = [
        _uc("Gas_MaxOpDay1_GRP", '1 * generator("G").generation <= 2'),
        _uc("Gas_MaxOpDay2_GRP", '1 * generator("G").generation <= 3'),
        _uc("Gas_MaxOpDay3_GRP", "malformed"),
        _uc("OTHER", "x <= 1"),
    ]
    layout = ((1, 2), (), tuple(range(25, 49)))
    out = P._consolidate_gas_maxopday_groups(
        specs, block_layout=layout, horizon_hours=48.0, soft_penalty=10.0
    )
    names = [s.name for s in out]
    assert "OTHER" in names
    merged = next(s for s in out if s.name != "OTHER")
    assert merged.daily_sum
    assert len(merged.rhs_profile) == 3


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (
            '1 * generator("G").generation - 100000 * decision_variable("D").value <= 0',
            '1 * generator("G").generation - 50 * decision_variable("D").value <= 0',
        ),
        (
            '1 * battery("B").discharge + 1 * battery("B").charge '
            '+ 5000 * decision_variable("D").value <= 0',
            '1 * battery("B").discharge + 1 * battery("B").charge '
            '+ 30 * decision_variable("D").value <= 0',
        ),
        (
            '1 * generator("G").generation - 10 * decision_variable("D").value <= 0',
            None,
        ),
        (
            '1 * generator("G").generation - 1e5 * decision_variable("D").value >= 0',
            None,
        ),
        (
            '1 * generator("G").generation - 1e5 * decision_variable("D").value <= 5',
            None,
        ),
        ('1 * generator("G").generation <= 1e.5.5', None),
        ("no terms <= 0", None),
        (
            '1 * generator("Z").generation - 1e5 * decision_variable("D").value <= 0',
            None,
        ),
        ('1 * generator("G").generation - 1e5 * generator("G").commit <= 0', None),
        (
            '1 * generator("G").generation - 1e.5.5 * decision_variable("D").value <= 0',
            None,
        ),
    ],
)
def test_tighten_dv_bigm_expression(expr: str, expected: str | None) -> None:
    """Only oversized DV big-M gates on ``<= 0`` rows are capped."""
    out = P._tighten_dv_bigm_expression(expr, {"G": 50.0}, {"B": 20.0}, {"B": 10.0})
    assert out == expected


def test_tighten_decision_variable_bigm(caplog: pytest.LogCaptureFixture) -> None:
    """Capped UCs are replaced; others pass through."""
    ucs = (
        _uc(
            "GATE",
            '1 * generator("G").generation - 1e5 * decision_variable("D").value <= 0',
        ),
        _uc("PLAIN", '1 * generator("G").generation <= 3'),
    )
    with caplog.at_level(logging.INFO, logger=P.logger.name):
        out = P._tighten_decision_variable_bigm(ucs, {"G": 50.0}, {}, {})
    assert "50 * decision_variable" in out[0].expression
    assert out[1] is ucs[1]
    assert "tightened decision-variable big-M on 1" in caplog.text
