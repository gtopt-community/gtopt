# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Alignment with the dlr2gtopt typical year, energy cases, blocks, schema."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dlr2gtopt import _typical as dty
from dlr2gtopt.tests._typical_synth import PARAMS, history
from re2gtopt import _blocks as bl
from re2gtopt import _typical as rt


def _dlr_typical(tmp_path: Path, case: str = "mean", blend: int = 6) -> Path:
    """A real dlr2gtopt typical year written to disk."""
    w = history(years=(2001, 2002, 2003, 2004), year_shift={2002: 0.4, 2004: -0.3})
    h = dty.History.build(w, PARAMS, utc_offset=-4.0)
    ty = dty.build_typical(
        h, dty.parse_case(case), dty.TypicalOptions(blend_hours=blend)
    )
    ty.rate(PARAMS)
    path = tmp_path / f"typical_{case}.parquet"
    dty.write_frame(dty.typical_frame(ty), path)
    return path


def _cf_from_weather(span: int = 0) -> rt.CFHistory:
    """A CF history that is a linear function of span wind speed, so the
    aligned CF can be checked against the DLR file's own weather."""
    w = history(years=(2001, 2002, 2003, 2004), year_shift={2002: 0.4, 2004: -0.3})
    cf = np.column_stack(
        [w.data["wind_speed"][:, span] / 20.0, w.data["wind_speed"][:, 1] / 25.0]
    )
    return rt.CFHistory(list(w.times), ["A", "B"], [11, 12], cf)


def test_alignment_reproduces_dlr_source_hours(tmp_path: Path) -> None:
    path = _dlr_typical(tmp_path)
    src = rt.read_source_hours(path)
    assert src.blend.max() > 0  # TMY months from different years blend
    hist = _cf_from_weather()
    aligned = rt.align(hist, src)
    df = dty.read_frame(path)
    first = df[df["span"] == df["span"].iloc[0]].sort_values("hour_of_year")
    want = first["wind_speed"].to_numpy() / 20.0
    assert aligned[:, 0] == pytest.approx(want, abs=1e-9)  # incl. blended hours
    # dlr2gtopt writes source_time_2: read, not rebuilt; the legacy rule agrees
    assert src.meta["provenance"] == "source_time_2"
    sec = rt.reconstruct_secondary(src.primary, src.blend)
    assert sec == src.secondary
    tab = pd.DataFrame(
        {
            "hour_of_year": np.arange(1, 8761),
            "source_time": src.primary,
            "source_time_2": [pd.NaT if s is None else s for s in sec],
            "blend": src.blend,
        }
    )
    p2 = tmp_path / "src.csv"
    tab.to_csv(p2, index=False)
    src2 = rt.read_source_hours(p2)
    assert rt.align(hist, src2) == pytest.approx(aligned)


def test_alignment_errors(tmp_path: Path) -> None:
    path = _dlr_typical(tmp_path)
    src = rt.read_source_hours(path)
    hist = _cf_from_weather()
    short = rt.CFHistory(hist.times[:100], hist.plants, hist.uids, hist.cf[:100])
    with pytest.raises(ValueError, match="misses"):
        rt.align(short, src)
    bad = rt.CFHistory(hist.times, hist.plants, hist.uids, hist.cf * np.nan)
    with pytest.raises(ValueError, match="NaN"):
        rt.align(bad, src)
    p = tmp_path / "x.csv"
    pd.DataFrame({"a": [1]}).to_csv(p, index=False)
    with pytest.raises(ValueError):
        rt.read_source_hours(p)
    pd.DataFrame({"hour_of_year": [1], "source_time": ["2001-01-01"]}).to_csv(
        p, index=False
    )
    with pytest.raises(ValueError):
        rt.read_source_hours(p)


def test_analogue_p90_alignment(tmp_path: Path) -> None:
    path = _dlr_typical(tmp_path, "p90")
    src = rt.read_source_hours(path)
    assert src.blend.max() == 0
    aligned = rt.align(_cf_from_weather(), src)
    assert aligned.shape == (8760, 2)


@pytest.mark.parametrize("scope", ["monthly", "annual"])
def test_energy_preserve_hits_targets(tmp_path: Path, scope: str) -> None:
    src = rt.read_source_hours(_dlr_typical(tmp_path))
    hist = _cf_from_weather()
    aligned = rt.align(hist, src)
    tg, stats = rt.energy_targets(hist, rt.parse_energy_case("mean"))
    ceil = np.nanmax(hist.cf, axis=0)
    final, fac = rt.preserve_energy(aligned, tg, ceil, scope)
    tc = dty.typical_calendar()
    days = np.diff(dty._CUM_DAYS)  # noqa: SLF001
    for j in range(2):
        annual_target = float((tg[:, j] * days).sum() / days.sum())
        assert final[:, j].mean() == pytest.approx(annual_target, rel=1e-4)
        assert final[:, j].max() <= ceil[j] + 1e-12
        if scope == "monthly":
            for m in (1, 7):
                got = final[tc.month == m, j].mean()
                assert got == pytest.approx(tg[m - 1, j], rel=1e-4)
    assert stats["plants"]["A"]["n_years"] == 4  # local 2000 (4 h) dropped
    rep = rt.typical_report(hist, aligned, final, tg, fac, stats)
    assert 0 <= rep["plants"]["A"]["aligned_percentile_among_years"] <= 1
    with pytest.raises(ValueError):
        rt.preserve_energy(aligned, tg, ceil, "weekly")


def test_energy_cases() -> None:
    hist = _cf_from_weather()
    mean, _ = rt.energy_targets(hist, rt.parse_energy_case("mean"))
    _, s50 = rt.energy_targets(hist, rt.parse_energy_case("p50"))
    p90, s90 = rt.energy_targets(hist, rt.parse_energy_case("p90"))
    _, sn = rt.energy_targets(
        hist, rt.parse_energy_case("p90"), p_method="normal", uncertainty=0.05
    )
    a = s90["plants"]["A"]
    assert a["target_cf"] < s50["plants"]["A"]["target_cf"] <= a["mean_cf"] * 1.05
    assert sn["plants"]["A"]["target_cf"] < s50["plants"]["A"]["target_cf"]
    # the p-cases keep the mean monthly shape
    assert (p90[:, 0] / mean[:, 0]) == pytest.approx(
        np.full(12, p90[0, 0] / mean[0, 0])
    )
    assert rt.parse_energy_case("quantile:0.2").q == pytest.approx(0.2)
    for bad in ("min", "bogus"):
        with pytest.raises(ValueError):
            rt.parse_energy_case(bad)
    with pytest.raises(ValueError):
        rt.energy_targets(hist, rt.parse_energy_case("p90"), p_method="x")
    one = rt.CFHistory(hist.times[:9000], hist.plants, hist.uids, hist.cf[:9000])
    with pytest.raises(ValueError):
        rt.energy_targets(one, rt.parse_energy_case("p90"))
    rt.energy_targets(one, rt.parse_energy_case("p90"), p_method="normal")
    yrs, _ = rt.energy_targets(hist, rt.parse_energy_case("mean"), years=[2003])
    assert yrs.shape == (12, 2)


def test_cf_history_roundtrip_and_typical_frame(tmp_path: Path) -> None:
    hist = _cf_from_weather()
    p = tmp_path / "cf.parquet"
    rt.write_cf_history(hist, p)
    back = rt.read_cf_history(p)
    assert back.uids == [11, 12] and back.cf == pytest.approx(hist.cf)
    pd.DataFrame({"time": ["2001-01-01"], "plant": ["A"], "x": [1]}).to_csv(
        tmp_path / "bad.csv", index=False
    )
    with pytest.raises(ValueError):
        rt.read_cf_history(tmp_path / "bad.csv")
    nouid = tmp_path / "nouid.csv"
    pd.DataFrame({"time": ["2001-01-01"], "plant": ["A"], "cf": [0.1]}).to_csv(
        nouid, index=False
    )
    assert rt.read_cf_history(nouid).uids == [1]
    src = rt.read_source_hours(_dlr_typical(tmp_path))
    prof = rt.align(hist, src)
    df = rt.typical_frame(hist.plants, hist.uids, prof, src)
    f = tmp_path / "re_typical_mean.parquet"
    dty.write_frame(df, f)
    tcf = rt.read_typical_cf(f)
    assert tcf.cf.shape == (8760, 2) and tcf.uids == [11, 12]
    dty.write_frame(df.iloc[:10], tmp_path / "short.parquet")
    with pytest.raises(ValueError):
        rt.read_typical_cf(tmp_path / "short.parquet")


def _cf8760() -> np.ndarray:
    tc = dty.typical_calendar()
    a = np.clip(np.sin(np.pi * (tc.hour - 6) / 12), 0, None)
    b = 0.3 + 0.2 * np.cos(2 * np.pi * tc.hour / 24)
    return np.column_stack([a, b])


def test_block_values_and_repeat() -> None:
    cf = _cf8760()
    specs = dty.blocks_from_bands(
        dty.stage_months(1, 12), dty.parse_bands("1-7,8-18,19-24")
    )
    vals, info = bl.block_values(cf, specs, "mean")
    assert vals.shape == (36, 2) and info["blocks"] == 36
    # energy: hour-weighted block means reproduce the annual mean
    hrs = np.array([s.hours.size for s in specs])
    assert (vals * hrs[:, None]).sum(0) / hrs.sum() == pytest.approx(cf.mean(0))
    q, _ = bl.block_values(cf, specs, "p90")
    assert np.all(q <= vals + 1e-9 + 1)  # shape only
    mn, _ = bl.block_values(cf, specs, "min")
    assert np.all(mn <= vals + 1e-12)
    src_h = [int(s.hours[0]) + 1 for s in specs]
    src_h[0] = 0  # a knn-mean block
    dh, inf2 = bl.block_values(cf, specs, "dlr-hour", src_h)
    assert inf2["dlr_hour_fallbacks"] == 1
    assert dh[1] == pytest.approx(cf[specs[1].hours[0]])
    with pytest.raises(ValueError):
        bl.block_values(cf, specs, "dlr-hour", None)
    empty = [dty.BlockSpec(1, 1, np.zeros(0, dtype=np.int64), 1)]
    with pytest.raises(ValueError):
        bl.block_values(cf, empty)
    specs2, vals2 = bl.repeat(specs, vals, dty.stage_months(1, 240))
    assert len(specs2) == 720 and vals2.shape == (720, 2)
    assert [s.block for s in specs2[:4]] == [1, 2, 3, 4]
    assert vals2[36] == pytest.approx(vals[0])  # month 1 of year 2


def test_same_hour_membership_as_dlr() -> None:
    """RE blocks and DLR blocks share the block -> hours assignment."""
    stages = dty.stage_months(4, 24)
    a = dty.blocks_chronological(stages, 6)
    vals, _ = bl.block_values(_cf8760(), a, "mean")
    b = dty.blocks_chronological(stages, 6)
    assert [s.hours.tolist() for s in a] == [s.hours.tolist() for s in b]
    assert vals.shape[0] == len(b)


def test_generator_profile_schema(tmp_path: Path) -> None:
    cf = _cf8760()
    specs = dty.blocks_from_bands(dty.stage_months(1, 2), dty.parse_bands("1-12,13-24"))
    vals, _ = bl.block_values(cf, specs)
    snip = bl.write_generator_profiles(
        tmp_path,
        specs,
        vals,
        ["A", "B"],
        [11, 12],
        ["genA", "7"],
        prefix="re",
        scenarios=[1, 2],
    )
    df = pd.read_parquet(tmp_path / "GeneratorProfile" / "re.parquet")
    assert list(df.columns) == ["scenario", "stage", "block", "uid", "value"]
    assert len(df) == 2 * 4 * 2
    assert str(df["uid"].dtype) == "int32" and str(df["value"].dtype) == "float64"
    assert df["value"].between(0, 1).all()
    arr = snip["generator_profile_array"]
    assert arr[0] == {
        "uid": 11,
        "name": "A_profile",
        "generator": "genA",
        "profile": "re",
    }
    assert arr[1]["generator"] == 7
    js = json.loads((tmp_path / "re_generator_profiles.json").read_text())
    assert js == snip
    with pytest.raises(ValueError):
        bl.write_generator_profiles(tmp_path, specs, vals + 2, ["A", "B"], [1, 2])
    fr = bl.block_frame(specs, vals, ["A", "B"], [11, 12])
    p = tmp_path / "blocks.csv"
    dty.write_frame(fr, p)
    s2, v2, pl2, u2 = bl.read_block_frame(p)
    assert v2 == pytest.approx(vals, abs=1e-4) and pl2 == ["A", "B"] and u2 == [11, 12]
    assert [s.hours.size for s in s2] == [s.hours.size for s in specs]


def test_read_dlr_source_hours(tmp_path: Path) -> None:
    p = tmp_path / "dlr_blocks.csv"
    pd.DataFrame(
        {"stage": [1, 1, 1, 1], "block": [1, 1, 2, 2], "source_hour": [5, 5, 0, 0]}
    ).to_csv(p, index=False)
    assert bl.read_dlr_source_hours(p) == [5, 0]
    q = tmp_path / "no.csv"
    pd.DataFrame({"stage": [1]}).to_csv(q, index=False)
    with pytest.raises(ValueError):
        bl.read_dlr_source_hours(q)


def test_reconstruct_secondary_rule() -> None:
    t0 = datetime(2003, 1, 1)
    prim = [t0 + timedelta(hours=k) for k in range(8760)]
    blend = np.zeros(8760)
    seam = 31 * 24
    blend[seam - 2 : seam + 2] = 0.2
    prim[seam] = datetime(2010, 2, 1)
    prim[seam + 1] = datetime(2010, 2, 1, 1)
    sec = rt.reconstruct_secondary(prim, blend)
    assert sec[seam - 1] == datetime(2010, 1, 31, 23)
    assert sec[seam] == prim[seam - 1] + timedelta(hours=1)
    assert sec[0] is None


def test_knn_members_alignment(tmp_path: Path) -> None:
    w = history(years=(2001, 2002, 2003, 2004), year_shift={2002: 0.4, 2004: -0.3})
    h = dty.History.build(w, PARAMS, utc_offset=-4.0)
    ty = dty.build_typical(
        h,
        dty.parse_case("p90"),
        dty.TypicalOptions(selection="knn-mean", analogues=3, tolerance=1.0),
    )
    ty.rate(PARAMS)
    path = tmp_path / "typical_p90.parquet"
    dty.write_frame(dty.typical_frame(ty), path)
    src = rt.read_source_hours(path)
    assert src.members is not None and src.meta["knn_hours"] == 8760
    hist = _cf_from_weather()
    aligned = rt.align(hist, src)
    idx = hist.index()
    for k in (0, 4000, 8759):
        want = hist.cf[[idx[t] for t in src.members[k]]].mean(axis=0)
        assert aligned[k] == pytest.approx(want)
    # a member outside the CF history is reported
    short = rt.CFHistory(hist.times[:-200], hist.plants, hist.uids, hist.cf[:-200])
    src.members[0] = [hist.times[-1], hist.times[-2]]
    src.primary[0] = hist.times[0]
    with pytest.raises(ValueError, match="misses"):
        rt.align(short, src)


def test_dlr_exact_source_time(tmp_path: Path) -> None:
    cf = _cf8760()
    specs = dty.blocks_from_bands(dty.stage_months(1, 2), dty.parse_bands("1-12,13-24"))
    hist = _cf_from_weather()
    times = [hist.times[100], None, datetime(1990, 1, 1), hist.times[7]]
    dh, info = bl.block_values(
        cf,
        specs,
        "dlr-hour",
        [1, 2, 3, 4],
        dlr_source_time=times,
        cf_history=hist,
    )
    assert info["dlr_exact"] == 2 and info["dlr_hour_fallbacks"] == 0
    assert dh[0] == pytest.approx(hist.cf[100]) and dh[3] == pytest.approx(hist.cf[7])
    assert dh[1] == pytest.approx(cf[1])  # no source_time: typical source_hour
    with pytest.raises(ValueError, match="same blocks"):
        bl.block_values(cf, specs, "dlr-hour", [1, 2, 3, 4], dlr_source_time=[None])
    p = tmp_path / "dlr_blocks.csv"
    pd.DataFrame(
        {
            "stage": [1, 1, 1],
            "block": [1, 1, 2],
            "source_hour": [5, 5, 3],
            "source_time": ["2003-02-01 04:00:00", "2003-02-01 04:00:00", None],
        }
    ).to_csv(p, index=False)
    assert bl.read_dlr_source_times(p) == [datetime(2003, 2, 1, 4), None]
    q = tmp_path / "old.csv"
    pd.DataFrame({"stage": [1], "block": [1], "source_hour": [5]}).to_csv(
        q, index=False
    )
    assert bl.read_dlr_source_times(q) is None
