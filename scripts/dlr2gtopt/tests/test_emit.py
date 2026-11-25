# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Block aggregation and the gtopt Line.dlr output."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from dlr2gtopt import _emit as em
from dlr2gtopt._ieee738 import CONDUCTORS, ampacity_to_mw
from dlr2gtopt.tests._synthetic import make_spans, make_weather

PARAMS = em.RatingParams(
    conductor=CONDUCTORS["flint"], tmax_conductor=55.0, voltage_kv=220.0
)


def test_blocks(tmp_path: Path) -> None:
    b = em.uniform_blocks(10, 4)
    assert [(x.block, x.start, x.hours) for x in b] == [(1, 0, 4), (2, 4, 4), (3, 8, 2)]
    with pytest.raises(ValueError):
        em.uniform_blocks(10, 0)
    (tmp_path / "b.csv").write_text(
        "stage,block,hours\n1,1,2\n2,1,3\n", encoding="utf-8"
    )
    rb = em.read_blocks(tmp_path / "b.csv")
    assert [(x.stage, x.block, x.start, x.hours) for x in rb] == [
        (1, 1, 0, 2),
        (2, 1, 2, 3),
    ]


def test_rating_matches_mw_conversion() -> None:
    w = make_weather(make_spans(4), hours=24)
    amps = em.span_ampacity(w, PARAMS)
    mw = em.span_rating_mw(w, PARAMS)
    assert mw == pytest.approx(ampacity_to_mw(amps, 220.0))
    assert amps.shape == (24, 3)


def test_aggregation() -> None:
    w = make_weather(make_spans(4), hours=48)
    mw = em.span_rating_mw(w, PARAMS)
    blocks = em.uniform_blocks(48, 6)
    rmin = em.aggregate_rating(mw, blocks, "min")
    rmean = em.aggregate_rating(mw, blocks, "mean")
    assert np.all(rmin <= rmean + 1e-9)
    with pytest.raises(ValueError):
        em.aggregate_rating(mw, blocks, "max")
    # weather of the min-rating hour reproduces the block-min rating
    agg = em.aggregate_weather(w, mw, blocks, "min-rating")
    wb = em.Weather([w.times[b.start] for b in blocks], w.spans, agg)
    assert em.span_rating_mw(wb, PARAMS) == pytest.approx(rmin)
    mean = em.aggregate_weather(w, mw, blocks, "mean")
    assert mean["ambient_temperature"][0] == pytest.approx(
        w.data["ambient_temperature"][:6].mean(axis=0)
    )
    assert np.all((mean["wind_direction"] >= 0) & (mean["wind_direction"] < 360))
    with pytest.raises(ValueError):
        em.aggregate_weather(w, mw, blocks, "max")


def _read_long(p: Path) -> list[dict[str, str]]:
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_emit_weather_mode(tmp_path: Path) -> None:
    w = make_weather(make_spans(4), hours=24)
    params = em.RatingParams(
        conductor=CONDUCTORS["flint"],
        tmax_conductor=55.0,
        voltage_kv=220.0,
        safety_factor=0.95,
        solar_mode="ghi",
        low_wind_speed=0.3,
    )
    dlr = em.emit(tmp_path, w, params, em.uniform_blocks(24, 4), line_uid=7, fmt="csv")
    assert dlr["conductor"]["r20"] == pytest.approx(0.0889e-3)
    assert dlr["safety_factor"] == pytest.approx(0.95)
    assert dlr["low_wind_speed"] == pytest.approx(0.3)
    assert "v_pu" not in dlr
    spans = dlr["critical_spans"]
    assert [s["uid"] for s in spans] == [7001, 7002, 7003]
    assert spans[0]["wind_speed"] == "dlr_wind_speed"
    rows = _read_long(tmp_path / "Line" / "dlr_wind_direction.csv")
    assert len(rows) == 6 * 3
    assert rows[0].keys() == {"scenario", "stage", "block", "uid", "value"}
    js = json.loads((tmp_path / "dlr_line7.json").read_text(encoding="utf-8"))
    assert js["dlr"]["voltage"] == pytest.approx(220.0)


def test_emit_rating_mode_parquet(tmp_path: Path) -> None:
    import pandas as pd

    w = make_weather(make_spans(3), hours=8)
    dlr = em.emit(
        tmp_path,
        w,
        PARAMS,
        em.uniform_blocks(8, 8),
        mode="rating",
        uid_base=500,
        prefix="x",
        fmt="parquet",
    )
    assert [s["rating"] for s in dlr["critical_spans"]] == ["x_rating", "x_rating"]
    assert "conductor" not in dlr
    df = pd.read_parquet(tmp_path / "Line" / "x_rating.parquet")
    assert df["uid"].tolist() == [501, 502]
    mw = em.span_rating_mw(w, PARAMS).min(axis=0)
    assert df["value"].to_numpy() == pytest.approx(mw)


def test_emit_errors_and_no_solar(tmp_path: Path) -> None:
    w = make_weather(make_spans(3), hours=4)
    with pytest.raises(ValueError, match="mode"):
        em.emit(tmp_path, w, PARAMS, em.uniform_blocks(4, 1), mode="x")
    with pytest.raises(ValueError, match="more hours"):
        em.emit(tmp_path, w, PARAMS, em.uniform_blocks(8, 1))
    del w.data["solar_irradiance"]
    p2 = em.RatingParams(conductor=CONDUCTORS["drake"], solar_mode="none", v_pu=0.98)
    dlr = em.emit(tmp_path, w, p2, em.uniform_blocks(4, 2), fmt="csv")
    assert dlr["solar_mode"] == "none" and dlr["v_pu"] == pytest.approx(0.98)
    assert "solar_irradiance" not in dlr["critical_spans"][0]
