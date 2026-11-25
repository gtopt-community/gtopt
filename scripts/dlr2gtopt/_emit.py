# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Emit gtopt ``Line.dlr`` input: the JSON object and the per-block schedules.

Two modes (``--mode``):

``weather``
    each critical span carries its four weather schedules and gtopt runs
    IEEE 738 per block (``ambient_temperature``, ``wind_speed``,
    ``wind_direction``, ``solar_irradiance``);
``rating``
    each critical span carries a precomputed MW ``rating`` schedule (IEEE
    738 done here; gtopt uses it verbatim).

Hours are aggregated into blocks.  ``rating`` aggregation: ``min``
(default: the binding hour of the block), ``mean``.  ``weather``
aggregation: ``min-rating`` (default: the weather of the hour whose rating
is lowest in the block, so gtopt reproduces the block-min rating) or
``mean`` (mean temperature, speed and GHI; direction through u/v).

Schedules are written in gtopt's long layout ``scenario,stage,block,uid,
value`` (``uid`` = span uid) as ``<out>/Line/<prefix>_<field>.<fmt>``.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from dlr2gtopt._ieee738 import Conductor, ampacity, ampacity_to_mw
from dlr2gtopt._weather import FIELDS, Weather, dir_from_uv, uv_from_dir

FloatArray = npt.NDArray[np.float64]
MODES = ("weather", "rating")
RATING_AGG = ("min", "mean")
WEATHER_AGG = ("min-rating", "mean")


@dataclass(frozen=True)
class Block:
    """A block: consecutive hours ``[start, start + hours)`` of one stage."""

    stage: int
    block: int
    start: int
    hours: int


def uniform_blocks(n_hours: int, block_hours: int, stage: int = 1) -> list[Block]:
    """Consecutive blocks of ``block_hours`` (last one may be shorter)."""
    if block_hours < 1:
        raise ValueError("block_hours must be >= 1")
    return [
        Block(stage, k + 1, s, min(block_hours, n_hours - s))
        for k, s in enumerate(range(0, n_hours, block_hours))
    ]


def read_blocks(path: Path) -> list[Block]:
    """Blocks CSV ``[stage,]block,hours`` (hours consecutive, in file order)."""
    out: list[Block] = []
    start = 0
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            h = int(float(row["hours"]))
            out.append(Block(int(row.get("stage") or 1), int(row["block"]), start, h))
            start += h
    return out


@dataclass(frozen=True)
class RatingParams:
    """IEEE 738 settings shared with gtopt's evaluator."""

    conductor: Conductor
    tmax_conductor: float = 75.0
    voltage_kv: float = 220.0
    v_pu: float = 1.0
    power_factor: float = 1.0
    safety_factor: float = 1.0
    solar_mode: str = "ghi"
    low_wind_speed: float = 0.5
    low_wind_angle: float = 45.0


def span_ampacity(weather: Weather, p: RatingParams) -> FloatArray:
    """Hourly ampacity [A] per span (per sub-conductor), shape (hours, spans)."""
    az = np.array([s.azimuth for s in weather.spans])
    el = np.array([s.elevation or 0.0 for s in weather.spans])
    d = weather.data
    ghi = d.get("solar_irradiance")
    return ampacity(
        p.conductor,
        p.tmax_conductor,
        d["ambient_temperature"],
        d["wind_speed"],
        d["wind_direction"],
        az[None, :],
        0.0 if ghi is None else ghi,
        el[None, :],
        solar_mode=p.solar_mode,
        low_wind_speed=p.low_wind_speed,
        low_wind_angle=p.low_wind_angle,
    )


def span_rating_mw(weather: Weather, p: RatingParams) -> FloatArray:
    """Hourly MW rating per span (gtopt's A -> MW conversion)."""
    return ampacity_to_mw(
        span_ampacity(weather, p),
        p.voltage_kv,
        v_pu=p.v_pu,
        power_factor=p.power_factor,
        subconductors=p.conductor.subconductors,
        safety_factor=p.safety_factor,
    )


def aggregate_rating(
    mw: FloatArray, blocks: Sequence[Block], how: str = "min"
) -> FloatArray:
    """(hours, spans) -> (blocks, spans)."""
    if how not in RATING_AGG:
        raise ValueError(f"rating aggregation must be one of {RATING_AGG}")
    fn = np.min if how == "min" else np.mean
    return np.array([fn(mw[b.start : b.start + b.hours], axis=0) for b in blocks])


def aggregate_weather(
    weather: Weather,
    mw: FloatArray,
    blocks: Sequence[Block],
    how: str = "min-rating",
) -> dict[str, FloatArray]:
    """Per-block weather (blocks, spans) for each field."""
    if how not in WEATHER_AGG:
        raise ValueError(f"weather aggregation must be one of {WEATHER_AGG}")
    n_s = len(weather.spans)
    out = {k: np.zeros((len(blocks), n_s)) for k in weather.data}
    cols = np.arange(n_s)
    for i, b in enumerate(blocks):
        sl = slice(b.start, b.start + b.hours)
        if how == "min-rating":
            hrs = b.start + np.argmin(mw[sl], axis=0)
            for k, v in weather.data.items():
                out[k][i] = v[hrs, cols]
            continue
        for k, v in weather.data.items():
            if k != "wind_direction":
                out[k][i] = v[sl].mean(axis=0)
        u, vv = uv_from_dir(
            weather.data["wind_speed"][sl], weather.data["wind_direction"][sl]
        )
        out["wind_direction"][i] = dir_from_uv(u.mean(axis=0), vv.mean(axis=0))
    return out


def span_uids(line_uid: int, n: int, base: int | None = None) -> list[int]:
    """gtopt's default span uid ``1000 * line_uid + position`` (1-based)."""
    b = 1000 * line_uid if base is None else base
    return [b + k for k in range(1, n + 1)]


def write_long(
    path: Path,
    blocks: Sequence[Block],
    uids: Sequence[int],
    values: FloatArray,
    scenario: int = 1,
) -> None:
    """One long-layout schedule ``scenario,stage,block,uid,value``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        (scenario, b.stage, b.block, u, float(values[i, j]))
        for i, b in enumerate(blocks)
        for j, u in enumerate(uids)
    ]
    if path.suffix == ".parquet":
        import pandas as pd

        pd.DataFrame(
            rows, columns=["scenario", "stage", "block", "uid", "value"]
        ).to_parquet(path, index=False)
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "stage", "block", "uid", "value"])
        for r in rows:
            w.writerow([*r[:4], f"{r[4]:.4f}"])


def emit(
    out_dir: Path,
    weather: Weather,
    params: RatingParams,
    blocks: Sequence[Block],
    *,
    line_uid: int = 1,
    mode: str = "weather",
    rating_agg: str = "min",
    weather_agg: str = "min-rating",
    prefix: str = "dlr",
    fmt: str = "csv",
    uid_base: int | None = None,
    scenario: int = 1,
) -> dict[str, Any]:
    """Write the schedules under ``out_dir/Line`` and return the ``dlr``
    JSON object for the line."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    n_h = len(weather.times)
    if blocks and blocks[-1].start + blocks[-1].hours > n_h:
        raise ValueError(f"blocks cover more hours than the weather ({n_h})")
    mw = span_rating_mw(weather, params)
    uids = span_uids(line_uid, len(weather.spans), uid_base)
    spans_json: list[dict[str, Any]] = []
    for k, s in enumerate(weather.spans):
        js: dict[str, Any] = {"uid": uids[k], "name": f"{s.line}_span{s.span}"}
        if mode == "weather":
            js["azimuth"] = round(s.azimuth, 2)
            js["elevation"] = round(s.elevation or 0.0, 1)
        spans_json.append(js)
    if mode == "rating":
        name = f"{prefix}_rating"
        write_long(
            out_dir / "Line" / f"{name}.{fmt}",
            blocks,
            uids,
            aggregate_rating(mw, blocks, rating_agg),
            scenario,
        )
        for js in spans_json:
            js["rating"] = name
    else:
        agg = aggregate_weather(weather, mw, blocks, weather_agg)
        for f in FIELDS:
            if f not in agg:
                continue
            name = f"{prefix}_{f}"
            write_long(
                out_dir / "Line" / f"{name}.{fmt}", blocks, uids, agg[f], scenario
            )
            for js in spans_json:
                js[f] = name
    dlr: dict[str, Any] = {}
    if mode == "weather":
        dlr["conductor"] = params.conductor.to_json()
        dlr["tmax_conductor"] = params.tmax_conductor
        dlr["voltage"] = params.voltage_kv
        for key, val, default in (
            ("v_pu", params.v_pu, 1.0),
            ("power_factor", params.power_factor, 1.0),
            ("safety_factor", params.safety_factor, 1.0),
            ("low_wind_speed", params.low_wind_speed, 0.5),
            ("low_wind_angle", params.low_wind_angle, 45.0),
        ):
            if val != default:
                dlr[key] = val
        if params.solar_mode != "ghi":
            dlr["solar_mode"] = params.solar_mode
        if "solar_irradiance" not in weather.data:
            dlr["solar_mode"] = "none"
    dlr["critical_spans"] = spans_json
    (out_dir / f"{prefix}_line{line_uid}.json").write_text(
        json.dumps({"dlr": dlr}, indent=2) + "\n", encoding="utf-8"
    )
    return dlr
