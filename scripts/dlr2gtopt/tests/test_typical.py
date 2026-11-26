# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Typical meteorological years of critical-span weather (synthetic data)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dlr2gtopt import _typical as ty
from dlr2gtopt._emit import span_rating_mw
from dlr2gtopt._weather import FIELDS, Weather
from dlr2gtopt.tests._typical_synth import PARAMS, history, spans3


@pytest.fixture(scope="module")
def hist() -> ty.History:
    """Four years (2004 is a leap year), Chilean local time."""
    return ty.History.build(history(), PARAMS, utc_offset=-4.0)


@pytest.fixture(scope="module")
def tmy_hist() -> ty.History:
    """2002 is the central year by construction (wind shifts -1.5/0/+1.5)."""
    w = history((2001, 2002, 2003), {2001: -1.5, 2002: 0.0, 2003: 1.5})
    return ty.History.build(w, PARAMS)


# ---------------------------------------------------------------------------
# cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "label", "q"),
    [
        ("mean", "mean", None),
        ("MEAN", "mean", None),
        ("p90", "p90", 0.1),
        ("p50", "p50", 0.5),
        ("p97.5", "p97.5", 0.025),
        ("quantile:0.25", "q0.25", 0.25),
        ("q0.25", "q0.25", 0.25),
        ("min", "min", 0.0),
    ],
)
def test_parse_case(text: str, label: str, q: float | None) -> None:
    c = ty.parse_case(text)
    assert c.label == label
    if q is None:
        assert c.is_mean and c.q is None
        assert "mean" in c.describe()
    else:
        assert c.q == pytest.approx(q)
        assert "exceeded" in c.describe()


@pytest.mark.parametrize("bad", ["p0", "p100", "quantile:1.5", "median", "q10"])
def test_parse_case_errors(bad: str) -> None:
    with pytest.raises(ValueError, match="case"):
        ty.parse_case(bad)


def test_case_stat() -> None:
    v = np.arange(101, dtype=float)
    assert ty.parse_case("mean").stat(v) == pytest.approx(50.0)
    assert ty.parse_case("p90").stat(v) == pytest.approx(10.0)
    assert ty.parse_case("min").stat(v) == pytest.approx(0.0)
    assert np.isnan(ty.parse_case("p90").stat(np.array([])))


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------


def test_typical_calendar_8760_no_leap_day() -> None:
    tc = ty.typical_calendar()
    assert tc.size == ty.HOURS_PER_YEAR
    assert not ((tc.month == 2) & (tc.day == 29)).any()
    assert tc.hour.min() == 0 and tc.hour.max() == 23


def test_hour_of_year() -> None:
    assert ty.hour_of_year(1, 1, 0) == 0
    assert ty.hour_of_year(12, 31, 23) == 8759
    assert ty.hour_of_year(2, 29, 5) == ty.hour_of_year(2, 28, 5)
    tc = ty.typical_calendar()
    assert (ty.hour_of_year(tc.month, tc.day, tc.hour) == np.arange(8760)).all()


def test_local_calendar_offset() -> None:
    c = ty.local_calendar([datetime(2010, 1, 1, 2)], -4.0)
    assert (int(c.year[0]), int(c.month[0]), int(c.day[0]), int(c.hour[0])) == (
        2009,
        12,
        31,
        22,
    )


# ---------------------------------------------------------------------------
# statistics after the min over spans
# ---------------------------------------------------------------------------


def test_month_hour_stats_after_min(hist: ty.History) -> None:
    stats = ty.month_hour_stats(hist)
    assert len(stats) == 288
    row = stats[5 * 24 + 14]
    assert (row["month"], row["hour"]) == (6, 14)
    sel = hist.valid & (hist.cal.month == 6) & (hist.cal.hour == 14)
    line = hist.span_mw[sel].min(axis=1)
    assert row["p10"] == pytest.approx(np.quantile(line, 0.10))
    assert row["mean"] == pytest.approx(line.mean())
    # min of the per-span p10 is NOT the line p10 (it is optimistic or equal)
    per_span = np.quantile(hist.span_mw[sel], 0.10, axis=0).min()
    assert per_span >= row["p10"] - 1e-9


@settings(max_examples=25, deadline=None)
@given(
    st.lists(
        st.lists(st.floats(100, 1000), min_size=3, max_size=3),
        min_size=4,
        max_size=40,
    ),
    st.floats(0.01, 0.99),
)
def test_quantile_after_min_property(rows: list[list[float]], q: float) -> None:
    """The line statistic is the quantile of the per-hour min, which is
    never above the min over spans of the per-span quantiles."""
    m = np.array(rows)
    line_q = np.quantile(m.min(axis=1), q)
    assert line_q <= np.quantile(m, q, axis=0).min() + 1e-9


def test_month_hour_stats_empty_bin() -> None:
    w = history((2001,))
    h = ty.History.build(w, PARAMS)
    h.valid[h.cal.month == 3] = False
    stats = ty.month_hour_stats(h)
    assert stats[2 * 24]["n"] == 0 and "p10" not in stats[2 * 24]


# ---------------------------------------------------------------------------
# TMY (mean case)
# ---------------------------------------------------------------------------


def test_fs_statistic() -> None:
    a = np.linspace(0, 1, 50)
    assert ty.fs_statistic(a, a) == pytest.approx(0.0)
    assert ty.fs_statistic(a + 10, a) == pytest.approx(49 / 100)  # (n-1)/(2n)
    assert np.isnan(ty.fs_statistic(np.array([]), a))


def test_parse_weights() -> None:
    assert ty.parse_weights(None) == ty.DEFAULT_FS_WEIGHTS
    w = ty.parse_weights("rating_mean=1,ghi=3")
    assert w == {"rating_mean": 0.25, "ghi": 0.75}
    with pytest.raises(ValueError, match="FS weight"):
        ty.parse_weights("foo=1")
    with pytest.raises(ValueError, match="sum"):
        ty.parse_weights("ghi=0")


@pytest.mark.parametrize("candidates", [1, 5])
def test_tmy_picks_central_year(tmy_hist: ty.History, candidates: int) -> None:
    picks = ty.select_tmy_months(tmy_hist, None, candidates)
    assert [p.month for p in picks] == list(range(1, 13))
    assert all(p.year == 2002 for p in picks)
    assert set(picks[0].scores) == {2001, 2002, 2003}


@pytest.mark.parametrize(
    "weights",
    ["rating_mean=1", "rating_min=1", "rating_max=1", "wind_speed=1"],
)
def test_tmy_each_index(tmy_hist: ty.History, weights: str) -> None:
    picks = ty.select_tmy_months(tmy_hist, ty.parse_weights(weights), 1)
    assert all(p.year == 2002 for p in picks)


def test_tmy_other_indices_run(tmy_hist: ty.History) -> None:
    for w in ("temperature=1", "ghi=1"):
        assert len(ty.select_tmy_months(tmy_hist, ty.parse_weights(w), 1)) == 12


def test_quantile_month_selection(tmy_hist: ty.History) -> None:
    low = ty.select_quantile_months(tmy_hist, ty.parse_case("p90"))
    assert all(p.year == 2001 for p in low)  # the calm (low-rating) year
    mid = ty.select_quantile_months(tmy_hist, ty.parse_case("mean"))
    assert all(p.year == 2002 for p in mid)


def test_month_hours_incomplete(hist: ty.History) -> None:
    # local time UTC-4: December of the last year misses its last 4 hours
    assert hist.month_hours(2004, 12) is None
    assert hist.month_hours(2001, 1) is not None
    assert hist.month_hours(2004, 2) is not None  # leap Feb: 29th dropped
    assert hist.month_hours(2004, 2).size == 28 * 24  # type: ignore[union-attr]
    assert 2004 not in hist.complete_years(12)


def test_no_complete_month_errors() -> None:
    w = history((2001,))
    h = ty.History.build(w, PARAMS, utc_offset=-4.0)
    with pytest.raises(ValueError, match="month 12"):
        ty.select_tmy_months(h)
    with pytest.raises(ValueError, match="month 12"):
        ty.select_quantile_months(h, ty.parse_case("p90"))


# ---------------------------------------------------------------------------
# assembling a typical year
# ---------------------------------------------------------------------------


def _picks(year_of: dict[int, int]) -> list[ty.MonthPick]:
    return [ty.MonthPick(m, year_of.get(m, 2002)) for m in range(1, 13)]


def test_assemble_8760_leap_feb_span_consistent(hist: ty.History) -> None:
    picks = _picks({2: 2004, 3: 2003})
    data, src, blend, _ = ty.assemble_months(hist, picks, blend_hours=0)
    assert src.size == 8760 and (blend == 0).all()
    t = [hist.weather.times[i] for i in src]
    loc = ty.local_calendar(t, -4.0)
    assert not ((loc.month == 2) & (loc.day == 29)).any()
    tc = ty.typical_calendar()
    assert (loc.month == tc.month).all() and (loc.hour == tc.hour).all()
    for f in FIELDS:  # every span from the same source hour
        assert np.array_equal(data[f], hist.weather.data[f][src])


def test_assemble_blend(hist: ty.History) -> None:
    picks = _picks({2: 2003})  # Jan 2002 -> Feb 2003 -> Mar 2002
    raw, src0, _, _ = ty.assemble_months(hist, picks, blend_hours=0)
    data, _, blend, sec = ty.assemble_months(hist, picks, blend_hours=6)
    p = 31 * 24  # first hour of February
    assert (blend[p - 6 : p + 6] > 0).all()
    assert blend[p - 7] == 0 and blend[p + 6] == 0
    assert blend.max() <= 0.5
    # inside a blend window temperatures are convex combinations
    a = hist.weather.data["ambient_temperature"][src0[p - 1] + 1]
    b = hist.weather.data["ambient_temperature"][src0[p]]
    v = data["ambient_temperature"][p]
    assert ((v >= np.minimum(a, b) - 1e-9) & (v <= np.maximum(a, b) + 1e-9)).all()
    # outside the windows, untouched
    assert np.array_equal(data["wind_speed"][:100], raw["wind_speed"][:100])
    # secondary source: the other side of the seam, -1 outside windows
    assert (sec[p - 6 : p + 6] >= 0).all() and sec[p - 7] == -1
    assert (sec[p - 6 : p + 6] != src0[p - 6 : p + 6]).any()
    # the blend partner + weight reproduce the blended value
    k = p - 2
    w2 = blend[k]
    prim = hist.weather.data["ambient_temperature"]
    _, pr, _, _ = ty.assemble_months(hist, picks, blend_hours=6)
    want = (1 - w2) * prim[pr[k]] + w2 * prim[sec[k]]
    assert np.allclose(data["ambient_temperature"][k], want)


def test_assemble_errors(hist: ty.History) -> None:
    with pytest.raises(ValueError, match="incomplete"):
        ty.assemble_months(hist, _picks({12: 2004}), 0)


def test_blend_skips_history_edge() -> None:
    w = history((2001, 2002))
    h = ty.History.build(w, PARAMS)
    # Dec 2001 -> the edge of 2002 history only matters for Dec/Jan (no wrap)
    data, _, blend, _ = ty.assemble_months(
        h, _picks({m: 2001 for m in range(1, 13)}), 6
    )
    assert (blend == 0).all()  # one year: always contiguous
    # break contiguity: a hole right after January 2001
    h.weather.times[31 * 24 + 2] = datetime(1999, 1, 1)
    _, _, blend2, _ = ty.assemble_months(h, _picks({1: 2001, 2: 2002}), 6)
    assert blend2[31 * 24] == 0
    assert data["wind_speed"].shape == (8760, 3)


# ---------------------------------------------------------------------------
# analogue (P90) year
# ---------------------------------------------------------------------------


def test_analogue_p90_exceedance(hist: ty.History) -> None:
    case = ty.parse_case("p90")
    typ = ty.build_typical(hist, case, ty.TypicalOptions())
    assert typ.method == "analogue"
    assert typ.line_mw is not None and typ.line_mw.size == 8760
    rep = ty.verify(typ, hist)
    assert rep["exceedance"] == pytest.approx(0.90, abs=0.02)
    assert rep["expected_exceedance"] == pytest.approx(0.90)
    assert rep["target_error_mw"]["mean_rel"] < 0.02
    # the recomputed rating is the rating of the source hour (same spans)
    assert np.allclose(typ.line_mw, hist.line_mw[typ.source])
    # span consistency: each hour's weather is ONE historical hour
    for f in FIELDS:
        assert np.array_equal(typ.data[f], hist.weather.data[f][typ.source])


@settings(max_examples=10, deadline=None)
@given(st.floats(0.05, 0.95))
def test_analogue_tracks_quantile(q: float) -> None:
    h = ty.History.build(history((2001, 2002), seed=3), PARAMS)
    typ = ty.analogue_year(h, ty.Case("q", q), selection="nearest")
    typ.rate(PARAMS)
    assert typ.line_mw is not None
    rel = np.abs(typ.line_mw - typ.target) / typ.target
    assert float(rel.mean()) < 0.03


def test_analogue_rotate_distinct(hist: ty.History) -> None:
    typ = ty.analogue_year(hist, ty.parse_case("p90"), selection="rotate", analogues=5)
    jan_14 = np.flatnonzero(
        (ty.typical_calendar().month == 1) & (ty.typical_calendar().hour == 14)
    )
    assert len(set(typ.source[jan_14].tolist())) == 5


def test_analogue_knn_mean_and_fallback(hist: ty.History) -> None:
    loose = ty.analogue_year(
        hist, ty.parse_case("mean"), selection="knn-mean", analogues=3, tolerance=0.5
    )
    assert loose.meta["knn_fallbacks"] == 0
    strict = ty.analogue_year(
        hist, ty.parse_case("mean"), selection="knn-mean", analogues=3, tolerance=0.0
    )
    assert strict.meta["knn_fallbacks"] > 0
    near = ty.analogue_year(hist, ty.parse_case("mean"), selection="nearest")
    strict.rate(PARAMS)
    near.rate(PARAMS)
    assert strict.line_mw is not None and near.line_mw is not None
    with pytest.raises(ValueError, match="selection"):
        ty.analogue_year(hist, ty.parse_case("mean"), selection="best")


def test_analogue_empty_bin() -> None:
    h = ty.History.build(history((2001,)), PARAMS)
    h.valid[h.cal.month == 4] = False
    with pytest.raises(ValueError, match="month 4"):
        ty.analogue_year(h, ty.parse_case("p90"))


@pytest.mark.parametrize(
    ("case", "mean_method", "p90_method", "method"),
    [
        ("mean", "tmy", "analogue", "tmy"),
        ("mean", "analogue", "analogue", "analogue"),
        ("p90", "tmy", "month", "month"),
        ("quantile:0.25", "tmy", "analogue", "analogue"),
    ],
)
def test_build_typical_methods(
    hist: ty.History, case: str, mean_method: str, p90_method: str, method: str
) -> None:
    opt = ty.TypicalOptions(mean_method=mean_method, quantile_method=p90_method)
    typ = ty.build_typical(hist, ty.parse_case(case), opt)
    assert typ.method == method
    assert typ.line_mw is not None
    rep = ty.verify(typ, hist)
    assert len(rep["months"]) == 12 and "p50" in rep["duration_curve"]
    if method == "tmy":
        assert rep["typical_mean_mw"] == pytest.approx(rep["history_mean_mw"], rel=0.05)
        assert "target_error_mw" not in rep
    if case == "p90" and method == "month":
        assert rep["typical_mean_mw"] < rep["history_mean_mw"]


def test_build_typical_bad_options(hist: ty.History) -> None:
    with pytest.raises(ValueError, match="mean method"):
        ty.build_typical(
            hist, ty.parse_case("mean"), ty.TypicalOptions(mean_method="x")
        )
    with pytest.raises(ValueError, match="quantile method"):
        ty.build_typical(
            hist, ty.parse_case("p90"), ty.TypicalOptions(quantile_method="x")
        )


def test_history_years_filter() -> None:
    h = ty.History.build(history((2001, 2002)), PARAMS, years=[2002])
    assert not h.valid[h.cal.year == 2001].any()
    assert h.valid[h.cal.year == 2002].all()


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_holdout_errors(hist: ty.History) -> None:
    typ_line = np.full(8760, 1e6)
    out = ty.holdout_errors(typ_line, hist, [2003], static_mw=0.0)
    assert out["test_hours"] == 8760
    assert out["p_over"] == 1.0 and out["mean_under_mw"] == 0.0
    assert out["static"]["p_over"] == 0.0 and out["static"]["mean_over_mw"] == 0.0
    out2 = ty.holdout_errors(np.zeros(8760), hist, [2004])
    # leap year (29 Feb -> 28 Feb) minus the 4 local hours after the record
    assert out2["test_hours"] == 8784 - 4
    assert out2["p_over"] == 0.0 and "static" not in out2


def test_holdout_p90_vs_mean(hist: ty.History) -> None:
    fit = ty.History.build(hist.weather, PARAMS, -4.0, years=[2001, 2002, 2003])
    res = {}
    for c in ("mean", "p90"):
        typ = ty.build_typical(
            fit, ty.parse_case(c), ty.TypicalOptions(mean_method="analogue")
        )
        assert typ.line_mw is not None
        res[c] = ty.holdout_errors(typ.line_mw, hist, [2004])
    assert res["p90"]["p_over"] < res["mean"]["p_over"]
    assert res["p90"]["mean_over_mw"] < res["mean"]["mean_over_mw"]


# ---------------------------------------------------------------------------
# typical-year file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", [".parquet", ".csv"])
def test_typical_roundtrip(hist: ty.History, tmp_path: Path, suffix: str) -> None:
    typ = ty.build_typical(hist, ty.parse_case("p90"), ty.TypicalOptions())
    df = ty.typical_frame(typ)
    assert list(df.columns) == [*ty.TYPICAL_COLUMNS, "source_time_2"]
    assert len(df) == 8760 * 3
    assert df["hour"].min() == 1 and df["hour"].max() == 24
    path = tmp_path / f"typical_p90{suffix}"
    ty.write_frame(df, path)
    tt = ty.read_typical(path)
    assert tt.keys == [("L", 1), ("L", 2), ("L", 3)]
    assert tt.line_mw.shape == (8760,)
    assert np.allclose(tt.data["wind_speed"], typ.data["wind_speed"], atol=1e-3)
    # one source hour per typical hour for every span
    assert df.groupby("hour_of_year")["source_time"].nunique().max() == 1
    bad = tmp_path / "bad.csv"
    df.iloc[:-1].to_csv(bad, index=False)
    with pytest.raises(ValueError, match="8760"):
        ty.read_typical(bad)


# ---------------------------------------------------------------------------
# blocks
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def table(
    hist: ty.History, tmp_path_factory: pytest.TempPathFactory
) -> ty.TypicalTable:
    typ = ty.build_typical(hist, ty.parse_case("p90"), ty.TypicalOptions())
    path = tmp_path_factory.mktemp("t") / "typical_p90.parquet"
    ty.write_frame(ty.typical_frame(typ), path)
    return ty.read_typical(path)


def _block_map(hour_col: int = 1, stage: bool = True) -> pd.DataFrame:
    """Two monthly stages (Jan, Feb), load blocks by hour band: block 1 =
    peak 19-23, block 2 = the rest of the day (non-chronological)."""
    rows = []
    for y, m, nd, b0 in ((2030, 1, 31, 1), (2030, 2, 28, 3), (2032, 2, 29, 3)):
        for d in range(1, nd + 1):
            for hr in range(1, 25):
                blk = b0 if 19 <= hr <= 23 else b0 + 1
                rows.append((y, m, d, hr - 1 + hour_col, blk, 1 if m == 1 else 2))
    df = pd.DataFrame(rows, columns=["year", "month", "day", "hour", "block", "stage"])
    return df if stage else df.drop(columns="stage")


def test_blocks_from_map(table: ty.TypicalTable) -> None:
    specs = ty.blocks_from_map(_block_map())
    assert [(s.stage, s.block) for s in specs] == [(1, 1), (1, 2), (2, 3), (2, 4)]
    assert specs[0].hours.size == 31 * 5
    assert specs[2].hours.size == (28 + 29) * 5  # leap 29 Feb -> 28 Feb
    bw = ty.to_blocks(table, specs, ty.parse_case("p90"))
    for i, sp in enumerate(specs):
        r = table.line_mw[sp.hours]
        target = np.quantile(r, 0.10)
        assert bw.stat_mw[i] == pytest.approx(target)
        assert abs(bw.line_mw[i] - target) == pytest.approx(np.abs(r - target).min())
        h = bw.source_hour[i] - 1
        for f in FIELDS:  # all spans from the chosen hour
            assert np.array_equal(bw.data[f][i], table.data[f][h])


def test_blocks_from_map_planning_and_errors() -> None:
    planning = {
        "simulation": {
            "block_array": [{"uid": u} for u in (1, 2, 3, 4)],
            "stage_array": [
                {"uid": 1, "first_block": 0, "count_block": 2},
                {"uid": 2, "first_block": 2, "count_block": 2},
            ],
        }
    }
    sob = ty.stage_of_block_from_planning(planning)
    assert sob == {1: 1, 2: 1, 3: 2, 4: 2}
    specs = ty.blocks_from_map(_block_map(stage=False), sob)
    assert [s.stage for s in specs] == [1, 1, 2, 2]
    specs0 = ty.blocks_from_map(_block_map(hour_col=0), None, hour_base=0)
    assert len(specs0) == 4
    with pytest.raises(ValueError, match="stage column"):
        ty.blocks_from_map(_block_map(stage=False))
    with pytest.raises(ValueError, match="stage_array"):
        ty.blocks_from_map(_block_map(stage=False), {1: 1})
    with pytest.raises(ValueError, match="columns"):
        ty.blocks_from_map(pd.DataFrame({"month": [1]}))
    with pytest.raises(ValueError, match="out of range"):
        ty.blocks_from_map(_block_map(hour_col=1), None, hour_base=0)
    with pytest.raises(ValueError, match="stage_array"):
        ty.stage_of_block_from_planning({})


def test_bands_and_stage_months(table: ty.TypicalTable, tmp_path: Path) -> None:
    assert ty.parse_bands("1-7,8-18,19-24") == [(1, 7), (8, 18), (19, 24)]
    assert ty.parse_bands("1-23,24") == [(1, 23), (24, 24)]
    for bad, msg in (("0-24", "outside"), ("1-12,12-24", "exactly once")):
        with pytest.raises(ValueError, match=msg):
            ty.parse_bands(bad)
    sm = ty.stage_months(first_month=4, stages=14, first_stage=1)
    assert sm[0] == (1, 4) and sm[8] == (9, 12) and sm[9] == (10, 1) and len(sm) == 14
    p = tmp_path / "sm.csv"
    pd.DataFrame({"stage": [1, 2], "month": [3, 4]}).to_csv(p, index=False)
    assert ty.read_stage_months(p) == [(1, 3), (2, 4)]
    specs = ty.blocks_from_bands(ty.stage_months(), ty.parse_bands("1-7,8-18,19-24"))
    assert len(specs) == 36 and specs[-1].block == 36
    assert specs[0].hours.size == 31 * 7 and specs[0].month == 1
    bw = ty.to_blocks(table, specs, ty.parse_case("mean"))
    assert bw.data["wind_speed"].shape == (36, 3)


def test_chronological_direct(table: ty.TypicalTable) -> None:
    specs = ty.blocks_chronological(ty.stage_months(), 1)
    assert len(specs) == 8760
    bw = ty.to_blocks(table, specs, ty.parse_case("p90"))
    assert np.array_equal(bw.source_hour, np.arange(1, 8761))  # direct
    assert np.array_equal(bw.data["wind_speed"], table.data["wind_speed"])
    specs6 = ty.blocks_chronological(ty.stage_months(1, 1), 6)
    assert len(specs6) == 31 * 4
    with pytest.raises(ValueError, match="block_hours"):
        ty.blocks_chronological(ty.stage_months(), 0)


def test_to_blocks_knn_mean(table: ty.TypicalTable) -> None:
    specs = ty.blocks_from_bands(ty.stage_months(1, 2), ty.parse_bands("1-12,13-24"))
    loose = ty.to_blocks(
        table,
        specs,
        ty.parse_case("mean"),
        selection="knn-mean",
        analogues=3,
        tolerance=1.0,
        spans=spans3(),
        params=PARAMS,
    )
    assert (loose.source_hour == 0).all()  # averaged
    # property: the recomputed rating of the emitted weather is the stored one
    w = Weather(
        [datetime(2001, 1, 1, h) for h in range(len(specs))],
        spans3(),
        dict(loose.data),
    )
    assert np.allclose(span_rating_mw(w, PARAMS).min(axis=1), loose.line_mw)
    assert np.allclose(
        np.abs(loose.line_mw - loose.stat_mw) / loose.stat_mw, 0, atol=1.0
    )
    strict = ty.to_blocks(
        table,
        specs,
        ty.parse_case("mean"),
        selection="knn-mean",
        tolerance=0.0,
        spans=spans3(),
        params=PARAMS,
    )
    assert (strict.source_hour > 0).all()  # fell back to the nearest hour
    rot = ty.to_blocks(table, specs, ty.parse_case("mean"), selection="rotate")
    assert (rot.source_hour > 0).all()
    with pytest.raises(ValueError, match="knn-mean needs"):
        ty.to_blocks(table, specs, ty.parse_case("mean"), selection="knn-mean")
    with pytest.raises(ValueError, match="selection"):
        ty.to_blocks(table, specs, ty.parse_case("mean"), selection="x")
    with pytest.raises(ValueError, match="no hours"):
        ty.to_blocks(
            table,
            [ty.BlockSpec(1, 1, np.zeros(0, dtype=np.int64))],
            ty.parse_case("mean"),
        )


def test_repeat_20_years(table: ty.TypicalTable, tmp_path: Path) -> None:
    specs = ty.blocks_from_bands(ty.stage_months(), ty.parse_bands("1-7,8-18,19-24"))
    tpl = ty.to_blocks(table, specs, ty.parse_case("p90"))
    path = tmp_path / "tpl.csv"
    keys = table.keys
    ty.write_frame(ty.block_frame(tpl, keys), path)
    back, keys2 = ty.read_block_frame(path)
    assert keys2 == keys and len(back.specs) == 36
    horizon = ty.stage_months(first_month=4, stages=240)
    bw = ty.repeat(back, horizon)
    assert len(bw.specs) == 720
    assert [s.block for s in bw.specs] == list(range(1, 721))
    assert bw.specs[0].month == 4 and bw.specs[0].stage == 1
    # stage 13 is April again: same weather as stage 1
    assert np.allclose(bw.data["wind_speed"][36:39], bw.data["wind_speed"][0:3])
    with pytest.raises(ValueError, match="month 5"):
        ty.repeat(ty.repeat(back, [(1, 4)]), [(1, 5)])
    nomonth = ty.BlockWeather(
        [ty.BlockSpec(1, 1, np.zeros(1, dtype=np.int64))],
        {},
        np.zeros((1, 1)),
        np.zeros(1),
        np.zeros(1, dtype=np.int64),
        np.zeros(1),
    )
    with pytest.raises(ValueError, match="month template"):
        ty.repeat(nomonth, [(1, 1)])


# ---------------------------------------------------------------------------
# Line.dlr schedules
# ---------------------------------------------------------------------------

# Fields of gtopt's C++ ``CriticalSpan`` / ``LineDlr`` (include/gtopt/line_dlr.hpp)
CPP_SPAN_FIELDS = {
    "uid",
    "name",
    "azimuth",
    "elevation",
    "ambient_temperature",
    "wind_speed",
    "wind_direction",
    "solar_irradiance",
    "tmax_conductor",
    "tmax_conductor_emergency",
    "rating",
    "rating_emergency",
}
CPP_DLR_FIELDS = {
    "conductor",
    "tmax_conductor",
    "tmax_conductor_emergency",
    "voltage",
    "v_pu",
    "power_factor",
    "safety_factor",
    "span_mode",
    "use_emergency",
    "low_wind_speed",
    "low_wind_angle",
    "solar_mode",
    "critical_spans",
}


@pytest.mark.parametrize("mode", ty.WRITE_MODES)
@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_write_schedules(
    table: ty.TypicalTable, tmp_path: Path, mode: str, fmt: str
) -> None:
    specs = ty.blocks_from_bands(ty.stage_months(1, 2), ty.parse_bands("1-12,13-24"))
    bw = ty.to_blocks(table, specs, ty.parse_case("p90"))
    dlr = ty.write_schedules(
        tmp_path,
        bw,
        spans3(),
        PARAMS,
        mode=mode,
        fmt=fmt,
        scenarios=(1, 2),
        tmax_profile=True,
        line_uid=7,
    )
    assert set(dlr) <= CPP_DLR_FIELDS
    for js in dlr["critical_spans"]:
        assert set(js) <= CPP_SPAN_FIELDS
    assert [js["uid"] for js in dlr["critical_spans"]] == [7001, 7002, 7003]
    files = {p.stem for p in (tmp_path / "Line").iterdir()}
    if mode == "rating":
        assert "rating" in dlr["critical_spans"][0] and "conductor" not in dlr
        assert files == {"dlr_rating", "dlr_tmax"}
    else:
        assert dlr["critical_spans"][0]["wind_speed"] == "dlr_wind_speed"
        assert dlr["tmax_conductor"] == 55.0
        assert {f"dlr_{f}" for f in FIELDS} <= files
    df = ty.read_frame(
        tmp_path
        / "Line"
        / f"dlr_{'rating' if mode == 'rating' else 'wind_speed'}.{fmt}"
    )
    assert list(df.columns) == ["scenario", "stage", "block", "uid", "value"]
    assert len(df) == 2 * 4 * 3 and set(df["scenario"]) == {1, 2}
    tmax = ty.read_frame(tmp_path / "Line" / f"dlr_tmax.{fmt}")
    assert list(tmax.columns) == ["stage", "block", "uid", "value"]
    assert np.allclose(tmax["value"], bw.line_mw, atol=1e-3)
    js = json.loads((tmp_path / "dlr_line7.json").read_text())
    assert js["dlr"] == dlr


def test_write_schedules_options(table: ty.TypicalTable, tmp_path: Path) -> None:
    from dataclasses import replace

    specs = ty.blocks_from_bands(ty.stage_months(1, 1), ty.parse_bands("1-24"))
    bw = ty.to_blocks(table, specs, ty.parse_case("mean"))
    p = replace(PARAMS, v_pu=0.95, solar_mode="none")
    dlr = ty.write_schedules(tmp_path, bw, spans3(), p, uid_base=100)
    assert dlr["v_pu"] == 0.95 and dlr["solar_mode"] == "none"
    assert dlr["critical_spans"][0]["uid"] == 101
    del bw.data["solar_irradiance"]
    dlr2 = ty.write_schedules(tmp_path, bw, spans3(), PARAMS)
    assert dlr2["solar_mode"] == "none"
    with pytest.raises(ValueError, match="mode"):
        ty.write_schedules(tmp_path, bw, spans3(), PARAMS, mode="x")


def test_plot_typical(hist: ty.History, tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    years = [
        ty.build_typical(hist, ty.parse_case(c), ty.TypicalOptions())
        for c in ("mean", "p90")
    ]
    paths = ty.plot_typical(tmp_path, hist, years, ty.month_hour_stats(hist), 250.0)
    assert len(paths) == 3 and all(p.stat().st_size > 1000 for p in paths)


def test_leap_feb_march_same_year_no_seam(hist: ty.History) -> None:
    _, _, blend, _ = ty.assemble_months(hist, _picks({2: 2004, 3: 2004}), 6)
    p = (31 + 28) * 24  # first hour of March
    assert blend[p - 3 : p + 3].max() == 0.0


def test_repeat_rejects_multi_stage_template(table: ty.TypicalTable) -> None:
    specs = ty.blocks_from_bands(
        [(1, 1), (2, 1)], ty.parse_bands("1-24")
    )  # two stages for January
    tpl = ty.to_blocks(table, specs, ty.parse_case("p90"))
    with pytest.raises(ValueError, match="several stages"):
        ty.repeat(tpl, [(1, 1)])


def test_history_to_blocks_exact_quantile(hist: ty.History) -> None:
    specs = ty.blocks_from_map(_block_map())
    bw = ty.history_to_blocks(hist, specs, ty.parse_case("p90"))
    hoy = ty.hour_of_year(hist.cal.month, hist.cal.day, hist.cal.hour)
    for i, sp in enumerate(specs):
        member = np.isin(hoy, sp.hours) & hist.valid
        r = hist.line_mw[member]
        assert bw.stat_mw[i] == pytest.approx(np.quantile(r, 0.10))
        assert abs(bw.line_mw[i] - bw.stat_mw[i]) == pytest.approx(
            np.abs(r - bw.stat_mw[i]).min()
        )
        assert np.mean(r >= bw.line_mw[i]) == pytest.approx(0.90, abs=0.02)
    empty = ty.History.build(history((2001,)), PARAMS)
    empty.valid[empty.cal.month == 1] = False
    with pytest.raises(ValueError, match="no history"):
        ty.history_to_blocks(empty, specs, ty.parse_case("p90"))


def test_typical_frame_sources(hist: ty.History) -> None:
    typ = ty.build_typical(hist, ty.parse_case("mean"), ty.TypicalOptions())
    typ.rate(PARAMS)
    df = ty.typical_frame(typ)
    first = df.iloc[:: len(typ.spans)]
    blended = first["blend"] > 0
    assert first.loc[blended, "source_time_2"].notna().all()
    assert first.loc[~blended, "source_time_2"].isna().all()
    assert "knn_sources" not in df.columns
    assert "source_time_2" not in ty.typical_frame(typ, sources=False).columns
    knn = ty.build_typical(
        hist,
        ty.parse_case("p90"),
        ty.TypicalOptions(selection="knn-mean", analogues=3, tolerance=1.0),
    )
    knn.rate(PARAMS)
    kf = ty.typical_frame(knn).iloc[:: len(knn.spans)]
    n_src = kf["knn_sources"].str.split(";").str.len()
    assert (n_src == 3).all()
    first_src = pd.to_datetime(kf["knn_sources"].str.split(";").str[0])
    assert (first_src.to_numpy() == kf["source_time"].to_numpy()).all()


def test_blocks_source_time(hist: ty.History, tmp_path: Path) -> None:
    typ = ty.build_typical(hist, ty.parse_case("mean"), ty.TypicalOptions())
    typ.rate(PARAMS)
    path = tmp_path / "typical_mean.parquet"
    ty.write_frame(ty.typical_frame(typ), path)
    tt = ty.read_typical(path)
    assert tt.source_time is not None and len(tt.source_time) == 8760
    specs = ty.blocks_from_bands(ty.stage_months(), ty.parse_bands("1-12,13-24"), 1)
    bw = ty.to_blocks(tt, specs, ty.parse_case("mean"))
    assert bw.source_time is not None
    for i in range(len(bw.specs)):
        assert bw.source_time[i] == tt.source_time[bw.source_hour[i] - 1]
    hb = ty.history_to_blocks(hist, specs, ty.parse_case("p90"))
    assert hb.source_time is not None
    df = ty.block_frame(hb, tt.keys)
    assert {"source_time", "source_year"} <= set(df.columns)
    years = set(df["source_year"])
    assert years <= {int(y) for y in hist.cal.year} | {
        int(y) + 1 for y in hist.cal.year
    }
    bpath = tmp_path / "blocks.parquet"
    ty.write_frame(df, bpath)
    back, _ = ty.read_block_frame(bpath)
    assert back.source_time == hb.source_time
    rep = ty.repeat(back, [(1, 1), (2, 2)])
    assert rep.source_time is not None and len(rep.source_time) == 4
    assert "source_time" not in ty.block_frame(hb, tt.keys, sources=False).columns
    assert (
        ty.block_frame(
            ty.to_blocks(
                ty.TypicalTable(
                    tt.keys, tt.data, tt.span_mw, tt.line_mw, tt.month, tt.day, tt.hour
                ),
                specs,
                ty.parse_case("mean"),
            ),
            tt.keys,
        )
        .columns.tolist()
        .count("source_time")
        == 0
    )


def test_dst_calendar_and_civil_month() -> None:
    w = history((2018, 2019))
    h = ty.History.build(w, PARAMS, utc_offset="America/Santiago")
    # April 2019: daylight saving ends -> 721 civil hours, kept at 720
    idx = h.month_hours(2019, 4)
    assert idx is not None and idx.size == 30 * 24
    # September 2019: it starts -> 719 civil hours, filled to 720
    sep = h.month_hours(2019, 9)
    assert sep is not None and sep.size == 30 * 24
    fixed = ty.History.build(w, PARAMS, utc_offset=-4.0)
    assert fixed.month_hours(2019, 4).size == 720  # type: ignore[union-attr]
    c = ty.local_calendar([datetime(2019, 1, 1, 3)], "America/Santiago")
    assert int(c.hour[0]) == 0  # UTC-3 in summer


def test_aligned_exact_and_conditional(hist: ty.History, tmp_path: Path) -> None:
    other = ty.build_typical(hist, ty.parse_case("mean"), ty.TypicalOptions())
    path = tmp_path / "typical_re_p50.parquet"
    ty.write_frame(ty.typical_frame(other), path)
    times = ty.read_source_times(path)
    assert times == other.source_times
    exact = ty.build_typical(
        hist,
        ty.parse_case("p90"),
        ty.TypicalOptions(source_times=times, align="exact"),
    )
    assert exact.method == "aligned-exact"
    assert exact.source_times == times  # same historical hours, all spans
    cond = ty.aligned_year(hist, ty.parse_case("p90"), times, condition_k=40)
    cond.rate(PARAMS)
    assert cond.line_mw is not None
    # conservative: on average below the exact (same-hour) rating
    assert cond.line_mw.mean() < exact.line_mw.mean()  # type: ignore[union-attr]
    assert np.isfinite(cond.target).all()
    # wind-cooling correlation with the source year survives
    wind_src = hist.weather.data["wind_speed"].mean(axis=1)[exact.source]
    assert np.corrcoef(wind_src, cond.line_mw)[0, 1] > 0.3
    for f in FIELDS:  # span consistency
        assert np.array_equal(cond.data[f], hist.weather.data[f][cond.source])


def test_aligned_errors(hist: ty.History, tmp_path: Path) -> None:
    t0 = hist.weather.times[100]
    with pytest.raises(ValueError, match="8760"):
        ty.aligned_year(hist, ty.parse_case("p90"), [t0])
    with pytest.raises(ValueError, match="not in the history"):
        ty.aligned_year(hist, ty.parse_case("p90"), [datetime(1900, 1, 1)] * 8760)
    with pytest.raises(ValueError, match="align"):
        ty.aligned_year(hist, ty.parse_case("p90"), [t0] * 8760, align="x")
    bad = tmp_path / "x.csv"
    pd.DataFrame({"hour_of_year": [1]}).to_csv(bad, index=False)
    with pytest.raises(ValueError, match="source_time"):
        ty.read_source_times(bad)
    h1 = ty.History.build(history((2001,)), PARAMS)
    h1.valid[h1.cal.month == 5] = False
    with pytest.raises(ValueError, match="month 5"):
        ty.aligned_year(h1, ty.parse_case("p90"), h1.weather.times[:8760])
