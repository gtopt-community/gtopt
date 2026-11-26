# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Weather sources (recorded/synthetic HTTP fixtures, no network) and the
CLI pipeline end to end."""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dlr2gtopt import _typical as dty
from dlr2gtopt._towers import PoliteCache
from dlr2gtopt.tests._typical_synth import PARAMS, history
from re2gtopt import _plants as pl
from re2gtopt import _sources as src
from re2gtopt.__main__ import main

YEAR = 2001


def _utc_year() -> list[datetime]:
    t0 = datetime(YEAR, 1, 1)
    return [t0 + timedelta(hours=k) for k in range(8760)]


def fake_get(url: str) -> bytes:
    """Synthetic Explorador / Open-Meteo replies (deterministic)."""
    if "python-router" in url:
        q = json.loads(urllib.parse.unquote(url.split("/python-router/", 1)[1]))
        d = q["datos"]
        name = f"{d['variable']}_{'-'.join(str(h) for h in d['alturas'])}.csv"
        return json.dumps({"_serverData": name}).encode()
    if "/temp/" in url:
        name = url.rsplit("/", 1)[1]
        var, hs = name[:-4].split("_")
        heights = [float(h) for h in hs.split("-")]
        rows = [",".join(f"{h:g}" for h in heights)]
        rows[0] = "," + rows[0]
        for k, t in enumerate(_utc_year()):
            loc = t - timedelta(hours=4)
            vals = []
            for h in heights:
                if var == "vel":
                    vals.append(f"{(5 + 2 * np.sin(k / 7)) * (h / 10) ** 0.2:.3f}")
                else:
                    vals.append(f"{(200 + k) % 360}")
            rows.append(loc.strftime("%Y-%m-%d %H:%M:%S") + "," + ",".join(vals))
        return ("\n".join(rows) + "\n").encode()
    if "archive" in url:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        times = [t.strftime("%Y-%m-%dT%H:%M") for t in _utc_year()]
        hod = np.array([t.hour for t in _utc_year()])
        sun = np.clip(1000 * np.sin(np.pi * (hod - 10) / 12), 0, None)
        hourly: dict[str, list[float | None]] = {"time": times}  # type: ignore[dict-item]
        for v in q["hourly"][0].split(","):
            if v == "temperature_2m":
                hourly[v] = [20.0] * 8760
            elif v == "surface_pressure":
                hourly[v] = [950.0] * 8760
            elif v == "shortwave_radiation":
                hourly[v] = sun.tolist()
            elif v == "direct_normal_irradiance":
                hourly[v] = (0.8 * sun).tolist()
            elif v == "diffuse_radiation":
                hourly[v] = (0.1 * sun).tolist()
            elif v.startswith("wind_speed"):
                hourly[v] = [6.0 if v.endswith("10m") else 8.0] * 8760
            else:
                hourly[v] = [210.0] * 8760
        hourly["temperature_2m"][5] = None  # a gap to fill
        return json.dumps({"elevation": 512.0, "hourly": hourly}).encode()
    raise AssertionError(url)


def _plants(tmp_path: Path) -> Path:
    f = tmp_path / "plants.csv"
    f.write_text(
        "name,type,lat,lon,pmax_mw,hub_height,turbine\n"
        "W1,wind,-31.505,-71.505,90,100,iec2\n"
        "S1,pv,-31.6,-71.2,100,,\n"
    )
    return f


@pytest.mark.parametrize(
    ("wind_source", "shear"), [("explorador", "fitted"), ("era5", "power")]
)
def test_plant_history_fixtures(tmp_path: Path, wind_source: str, shear: str) -> None:
    plants = pl.read_inventory(_plants(tmp_path))
    cache = PoliteCache(tmp_path / "cache", get=fake_get, pause=0.0)
    w = src.plant_history(
        plants, {"W1": 100.0}, [YEAR], cache=cache, wind_source=wind_source, shear=shear
    )
    assert len(w.times) == 8760 and w.plants == ["W1", "S1"]
    hub = w.column("wind_speed_hub", "W1")
    assert hub is not None and np.isfinite(hub).all() and hub.mean() > 6
    assert np.isnan(w.data["wind_speed_hub"][:, 1]).all()
    assert w.elevation["W1"] == 512.0
    assert w.column("pressure", "S1")[0] == pytest.approx(95000.0)  # type: ignore[index]
    # radiation moved one hour earlier (hour-start convention)
    ghi = w.column("ghi", "S1")
    assert ghi is not None and ghi[10] == pytest.approx(
        max(0.0, 1000 * np.sin(np.pi * (11 - 10) / 12))
    )
    p = tmp_path / "w.parquet"
    w.write(p)
    back = src.read_plant_weather(p)
    assert back.plants == w.plants and back.elevation["S1"] == 512.0
    assert w.column("nope", "W1") is None
    csvp = tmp_path / "w.csv"
    w.write(csvp)
    assert src.read_plant_weather(csvp).times[0] == w.times[0]


def test_shear_ratio_and_errors(tmp_path: Path) -> None:
    plants = pl.read_inventory(_plants(tmp_path))
    cache = PoliteCache(tmp_path / "cache", get=fake_get, pause=0.0)
    from dlr2gtopt._weather import Explorador

    r = src.fit_ratio_from_wrf(Explorador(cache, "wrf2010"), plants[0], 20.0, 100.0)
    assert r.shape == (12, 24) and r.mean() == pytest.approx(5**0.2, rel=1e-3)
    w = src.plant_history(
        plants,
        {"W1": 100.0},
        [YEAR],
        cache=cache,
        shear="ratio",
        shear_ratio={"W1": r},
    )
    assert np.nanmean(w.data["wind_speed_hub"][:, 0]) > 0
    with pytest.raises(ValueError):
        src.plant_history(plants, {}, [YEAR], cache=cache, shear="ratio")
    with pytest.raises(ValueError):
        src.plant_history(plants, {}, [YEAR], cache=cache, wind_source="x")
    u = src.era5_re_url(-30, -70, 2001, api_key="k")
    assert "customer" in u and "apikey=k" in u


def _weather_file(tmp_path: Path) -> Path:
    """Synthetic plant weather on the DLR synthetic history's hours."""
    w = history(years=(2001, 2002, 2003, 2004), year_shift={2002: 0.4})
    n = len(w.times)
    hod = np.array([t.hour for t in w.times])
    sun = np.clip(1000 * np.sin(np.pi * (hod - 10) / 12), 0, None)
    data = {
        "wind_speed_hub": np.column_stack(
            [w.data["wind_speed"][:, 0] * 2.5, np.full(n, np.nan)]
        ),
        "temperature": np.full((n, 2), 15.0),
        "pressure": np.full((n, 2), np.nan),
        "ghi": np.column_stack([sun, sun]),
        "wind_speed_10m": np.full((n, 2), 3.0),
    }
    pw = src.PlantWeather(list(w.times), ["W1", "S1"], data, {"W1": 100.0})
    p = tmp_path / "weather.parquet"
    pw.write(p)
    return p


def test_cli_pipeline(tmp_path: Path) -> None:
    pytest.importorskip("pvlib")
    plants = _plants(tmp_path)
    weather = _weather_file(tmp_path)
    run = tmp_path / "run"
    assert (
        main(
            [
                "model",
                "--plants",
                str(plants),
                "--weather",
                str(weather),
                "-o",
                str(run),
            ]
        )
        == 0
    )
    # fixed DC/AC instead of the commissioning-year table
    run2 = tmp_path / "run_fixed"
    assert (
        main(
            [
                "model",
                "--plants",
                str(plants),
                "--weather",
                str(weather),
                "--dc-ac-ratio",
                "1.1",
                "--dc-ac-table",
                "2015:1.05,9999:1.2",
                "-o",
                str(run2),
            ]
        )
        == 0
    )
    fixed = pd.read_csv(run2 / "sizing.csv").set_index("plant")
    assert fixed.loc["S1", "dc_ac_ratio"] == pytest.approx(1.1)
    with pytest.raises(SystemExit):
        main(
            [
                "model",
                "--plants",
                str(plants),
                "--weather",
                str(weather),
                "--dc-ac-ratio",
                "lots",
                "-o",
                str(run2),
            ]
        )
    sizing = pd.read_csv(run / "sizing.csv")
    assert set(sizing["plant"]) == {"W1", "S1"}
    assert {"modelled_peak_pu", "peak_ok", "n_turbines", "dc_ac_ratio"} <= set(
        sizing.columns
    )
    cf = pd.read_parquet(run / "cf_model.parquet")
    # observed = 0.9 x model with midday PV caps (curtailment), local time
    obs = cf.copy()
    obs["cf"] = obs["cf"] * 0.9
    hod = pd.to_datetime(obs["time"]).dt.hour
    cap = (
        (obs["plant"] == "S1")
        & hod.between(15, 18)
        & (pd.to_datetime(obs["time"]).dt.day % 2 == 0)
    )
    obs.loc[cap, "cf"] = np.minimum(obs.loc[cap, "cf"], 0.3)
    obs = obs[pd.to_datetime(obs["time"]).dt.year >= 2002]
    obs["time"] = pd.to_datetime(obs["time"]) - pd.Timedelta(hours=4)  # local
    obs[["time", "plant", "cf"]].to_parquet(tmp_path / "obs.parquet")
    cal = tmp_path / "cal"
    assert (
        main(
            [
                "calibrate",
                "--plants",
                str(plants),
                "--model",
                str(run),
                "--observed",
                str(tmp_path / "obs.parquet"),
                "--obs-utc-offset",
                "-4",
                "-o",
                str(cal),
            ]
        )
        == 0
    )
    table = pd.read_csv(cal / "calibration.csv")
    s1 = table[table["plant"] == "S1"].iloc[0]
    assert s1["flat_curtailment"] > 0
    assert s1["k"] == pytest.approx(0.9, abs=0.05)
    assert (cal / "cross_validation.json").exists()
    assert (
        main(
            [
                "calibrate",
                "--plants",
                str(plants),
                "--model",
                str(run),
                "--observed",
                str(tmp_path / "obs.parquet"),
                "--censoring",
                "envelope",
                "--calibration",
                "scalar",
                "--cv",
                "off",
                "--obs-utc-offset",
                "-4",
                "-o",
                str(tmp_path / "cal2"),
            ]
        )
        == 0
    )
    # DLR typical year of the same history
    w = history(years=(2001, 2002, 2003, 2004), year_shift={2002: 0.4})
    h = dty.History.build(w, PARAMS, utc_offset=-4.0)
    ty = dty.build_typical(h, dty.parse_case("mean"), dty.TypicalOptions())
    ty.rate(PARAMS)
    dlr_file = tmp_path / "typical_mean.parquet"
    dty.write_frame(dty.typical_frame(ty), dlr_file)
    typ = tmp_path / "typ"
    for case in ("mean", "p90"):
        assert (
            main(
                [
                    "typical",
                    "--cf",
                    str(cal / "cf_calibrated.parquet"),
                    "--dlr-typical",
                    str(dlr_file),
                    "--case",
                    case,
                    "--plants",
                    str(plants),
                    "-o",
                    str(typ),
                ]
            )
            == 0
        )
    rep = json.loads((typ / "re_typical_p90_report.json").read_text())
    assert rep["plants"]["W1"]["final_cf"] < rep["plants"]["W1"]["mean_cf"]
    assert (
        main(
            [
                "typical",
                "--cf",
                str(cal / "cf_calibrated.parquet"),
                "--dlr-typical",
                str(dlr_file),
                "--energy-preserve",
                "off",
                "--format",
                "csv",
                "--timezone",
                "America/Santiago",
                "-o",
                str(typ),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "typical",
                "--cf",
                str(cal / "cf_calibrated.parquet"),
                "--dlr-typical",
                str(dlr_file),
                "--case",
                "p90",
                "--energy-preserve",
                "off",
                "--utc-offset",
                "-4",
                "-o",
                str(typ),
            ]
        )
        == 2
    )
    # no plants, no --timezone: auto has no coordinates -> error
    assert (
        main(
            [
                "typical",
                "--cf",
                str(cal / "cf_calibrated.parquet"),
                "--dlr-typical",
                str(dlr_file),
                "-o",
                str(typ),
            ]
        )
        == 2
    )
    assert (
        main(
            [
                "typical",
                "--cf",
                str(cal / "cf_calibrated.parquet"),
                "--dlr-typical",
                str(dlr_file),
                "--energy-target",
                "observed",
                "--observed",
                str(tmp_path / "obs.parquet"),
                "--plants",
                str(plants),
                "--obs-utc-offset",
                "-4",
                "-o",
                str(tmp_path / "t2"),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "typical",
                "--cf",
                str(cal / "cf_calibrated.parquet"),
                "--dlr-typical",
                str(dlr_file),
                "--energy-target",
                "observed",
                "-o",
                str(tmp_path / "t3"),
            ]
        )
        == 2
    )
    tpl = tmp_path / "tpl"
    assert (
        main(
            [
                "to-blocks",
                str(typ / "re_typical_mean.parquet"),
                "--plants",
                str(plants),
                "--hour-bands",
                "1-7,8-18,19-24",
                "-o",
                str(tpl),
            ]
        )
        == 0
    )
    # dlr-hour on the DLR blocks' exact historical hours (--cf-history)
    hist_t = pd.read_parquet(cal / "cf_calibrated.parquet")["time"].iloc[30]
    dlr_b = tmp_path / "dlr_blocks.csv"
    specs = dty.blocks_from_bands(
        dty.stage_months(1, 12), dty.parse_bands("1-7,8-18,19-24")
    )
    pd.DataFrame(
        {
            "stage": [sp.stage for sp in specs],
            "block": [sp.block for sp in specs],
            "source_hour": [int(sp.hours[0]) + 1 for sp in specs],
            "source_time": [hist_t] * len(specs),
        }
    ).to_csv(dlr_b, index=False)
    assert (
        main(
            [
                "to-blocks",
                str(typ / "re_typical_mean.parquet"),
                "--hour-bands",
                "1-7,8-18,19-24",
                "--block-stat",
                "dlr-hour",
                "--dlr-blocks",
                str(dlr_b),
                "--cf-history",
                str(cal / "cf_calibrated.parquet"),
                "-o",
                str(tmp_path / "exact"),
            ]
        )
        == 0
    )
    ex = pd.read_parquet(tmp_path / "exact" / "re_profile_blocks.parquet")
    assert ex.groupby("plant")["cf"].nunique().max() == 1  # one hour everywhere
    case = tmp_path / "case"
    assert (
        main(
            [
                "repeat",
                str(tpl / "re_profile_blocks.parquet"),
                "--plants",
                str(plants),
                "--stages",
                "240",
                "--scenarios",
                "1,2",
                "-o",
                str(case),
            ]
        )
        == 0
    )
    gp = pd.read_parquet(case / "GeneratorProfile" / "re_profile.parquet")
    assert len(gp) == 2 * 240 * 3 * 2
    snip = json.loads((case / "re_profile_generator_profiles.json").read_text())
    assert [g["generator"] for g in snip["generator_profile_array"]] == ["W1", "S1"]
    assert (
        main(
            [
                "to-blocks",
                str(typ / "re_typical_mean.parquet"),
                "--block-hours",
                "24",
                "--stages",
                "12",
                "-o",
                str(tmp_path / "chrono"),
            ]
        )
        == 0
    )
    bmap = tmp_path / "bmap.csv"
    tc = dty.typical_calendar()
    pd.DataFrame(
        {
            "month": tc.month,
            "day": tc.day,
            "hour": tc.hour + 1,
            "block": (tc.month - 1) * 2 + (tc.hour >= 12) + 1,
            "stage": tc.month,
        }
    ).to_csv(bmap, index=False)
    assert (
        main(
            [
                "to-blocks",
                str(typ / "re_typical_mean.parquet"),
                "--block-map",
                str(bmap),
                "-o",
                str(tmp_path / "bm"),
            ]
        )
        == 0
    )
    planning = tmp_path / "planning.json"
    planning.write_text(
        json.dumps(
            {
                "simulation": {
                    "block_array": [{"uid": k + 1} for k in range(24)],
                    "stage_array": [
                        {"uid": m + 1, "first_block": 2 * m, "count_block": 2}
                        for m in range(12)
                    ],
                }
            }
        )
    )
    pd.read_csv(bmap).drop(columns="stage").to_csv(bmap, index=False)
    assert (
        main(
            [
                "to-blocks",
                str(typ / "re_typical_mean.parquet"),
                "--block-map",
                str(bmap),
                "--planning",
                str(planning),
                "--block-stat",
                "p90",
                "-o",
                str(tmp_path / "bm2"),
            ]
        )
        == 0
    )
    # validation + DLR cross-correlation (rating of the same history)
    rating = tmp_path / "rating.parquet"
    pd.DataFrame({"time": h.weather.times, "line_rating_mw": h.line_mw}).to_parquet(
        rating
    )
    out = tmp_path / "val.json"
    assert (
        main(
            [
                "validate",
                "--plants",
                str(plants),
                "--cf",
                str(cal / "cf_calibrated.parquet"),
                "--compare",
                f"uncalibrated={run / 'cf_model.parquet'}",
                "--observed",
                str(tmp_path / "obs.parquet"),
                "--obs-utc-offset",
                "-4",
                "--rating",
                str(rating),
                "-o",
                str(out),
            ]
        )
        == 0
    )
    val = json.loads(out.read_text())
    w1 = val["validation"]["plants"]["W1"]
    assert w1["model"]["corr"] > 0.99 and "uncalibrated" in w1
    xc = val["cross_correlation"]["W1"]["0"]
    assert xc["model"] > 0.3  # wind cools the line and makes power
    assert xc["observed"] == pytest.approx(xc["model_on_obs_hours"], abs=0.05)


def test_cli_errors(tmp_path: Path) -> None:
    plants = _plants(tmp_path)
    assert (
        main(
            [
                "model",
                "--plants",
                str(plants),
                "--weather",
                str(tmp_path / "missing.parquet"),
                "-o",
                str(tmp_path),
            ]
        )
        == 2
    )
    with pytest.raises(SystemExit):
        main(
            [
                "model",
                "--plants",
                str(plants),
                "--weather",
                "x",
                "--wind-cap",
                "2",
                "-o",
                "y",
                "--backtrack",
                "maybe",
            ]
        )


def test_cli_history_and_calibrate_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("pvlib")
    import dlr2gtopt._towers as tw

    monkeypatch.setattr(tw, "urllib_get", fake_get)
    plants = _plants(tmp_path)
    cache = tmp_path / "cache"
    # PoliteCache in the CLI uses the default getter: seed it via the fake
    from re2gtopt import __main__ as cli

    real = cli.PoliteCache
    monkeypatch.setattr(
        cli, "PoliteCache", lambda d, pause=3.0: real(d, get=fake_get, pause=0.0)
    )
    out = tmp_path / "hist.parquet"
    assert (
        main(
            [
                "history",
                "--plants",
                str(plants),
                "--years",
                str(YEAR),
                "--cache-dir",
                str(cache),
                "-o",
                str(out),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "history",
                "--plants",
                str(plants),
                "--years",
                str(YEAR),
                "--cache-dir",
                str(cache),
                "--levels",
                "10,20",
                "--shear",
                "ratio",
                "--equipment-source",
                "generic",
                "-o",
                str(tmp_path / "h2.parquet"),
            ]
        )
        == 0
    )
    w = src.read_plant_weather(out)
    assert np.nanmean(w.column("wind_speed_hub", "W1")) > 5  # type: ignore[arg-type]
    run = tmp_path / "run"
    assert (
        main(["model", "--plants", str(plants), "--weather", str(out), "-o", str(run)])
        == 0
    )
    cf = pd.read_parquet(run / "cf_model.parquet")
    obs = cf.copy()
    obs["mwh"] = np.where(obs["plant"] == "W1", 90.0, 100.0) * obs["cf"] * 0.8
    obs["curtailed_mwh"] = 0.0
    obs.loc[obs.index[::50], "curtailed_mwh"] = 1.0
    obs = obs.drop(columns="cf")
    obs.to_parquet(tmp_path / "obs.parquet")
    price = cf[["time", "plant"]].copy()
    price["price"] = 50.0
    price.to_parquet(tmp_path / "price.parquet")
    base = [
        "calibrate",
        "--plants",
        str(plants),
        "--model",
        str(run),
        "--observed",
        str(tmp_path / "obs.parquet"),
        "--obs-utc-offset",
        "0",
        "--cv",
        "off",
        "--min-hours",
        "100",
    ]
    assert (
        main(
            [
                *base,
                "--censoring",
                "mask",
                "--prices",
                str(tmp_path / "price.parquet"),
                "-o",
                str(tmp_path / "c1"),
            ]
        )
        == 0
    )
    assert (
        main([*base, "--calibration", "quantile-map", "-o", str(tmp_path / "c2")]) == 0
    )
    # one plant without enough hours falls back to its nearest neighbour's k
    only_w = pd.read_parquet(tmp_path / "obs.parquet")
    only_w = only_w[(only_w["plant"] == "W1") | (only_w.index % 1000 == 0)]
    only_w.to_parquet(tmp_path / "obs_w.parquet")
    two = tmp_path / "two.csv"
    two.write_text(
        "name,type,lat,lon,pmax_mw,hub_height\n"
        "W1,wind,-31.505,-71.505,90,100\nS1,wind,-31.6,-71.2,100,100\n"
    )
    hist2 = tmp_path / "hist2.parquet"
    pw = src.read_plant_weather(out)
    pw.data["wind_speed_hub"][:, 1] = pw.data["wind_speed_hub"][:, 0]
    pw.write(hist2)
    assert (
        main(
            [
                "model",
                "--plants",
                str(two),
                "--weather",
                str(hist2),
                "-o",
                str(tmp_path / "run2"),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "calibrate",
                "--plants",
                str(two),
                "--model",
                str(tmp_path / "run2"),
                "--observed",
                str(tmp_path / "obs_w.parquet"),
                "--obs-utc-offset",
                "0",
                "--cv",
                "off",
                "--min-hours",
                "100",
                "-o",
                str(tmp_path / "c3"),
            ]
        )
        == 0
    )
    t3 = pd.read_csv(tmp_path / "c3" / "calibration.csv")
    assert t3.set_index("plant").loc["S1", "fallback_from"] == "W1"
    bad = tmp_path / "bad.csv"
    bad.write_text("time,plant\n2001-01-01,W1\n")
    assert main([*base[:5], "--observed", str(bad), "-o", str(tmp_path / "c4")]) == 2
    bad.write_text("x,y\n1,2\n")
    assert main([*base[:5], "--observed", str(bad), "-o", str(tmp_path / "c4")]) == 2


def test_cli_calibration_target_forecast(tmp_path: Path) -> None:
    pytest.importorskip("pvlib")
    plants = _plants(tmp_path)
    run = tmp_path / "run"
    assert (
        main(
            [
                "model",
                "--plants",
                str(plants),
                "--weather",
                str(_weather_file(tmp_path)),
                "-o",
                str(run),
            ]
        )
        == 0
    )
    cf = pd.read_parquet(run / "cf_model.parquet")
    pmax = np.where(cf["plant"] == "W1", 90.0, 100.0)
    obs = cf[["time", "plant"]].copy()
    obs["mwh"] = pmax * cf["cf"] * 0.8
    obs["forecast_mwh"] = pmax * cf["cf"] * 0.95  # uncensored, other level
    obs.to_parquet(tmp_path / "obs.parquet")
    base = [
        "calibrate",
        "--plants",
        str(plants),
        "--model",
        str(run),
        "--observed",
        str(tmp_path / "obs.parquet"),
        "--cv",
        "off",
        "--calibration",
        "scalar",
    ]
    assert (
        main([*base, "--calibration-target", "forecast", "-o", str(tmp_path / "f")])
        == 0
    )
    tf = pd.read_csv(tmp_path / "f" / "calibration.csv").set_index("plant")
    assert tf.loc["S1", "k"] == pytest.approx(0.95, abs=0.02)
    assert (tf["censoring"] == "naive").all()
    assert main([*base, "--calibration-target", "both", "-o", str(tmp_path / "b")]) == 0
    tb = pd.read_csv(tmp_path / "b" / "calibration.csv").set_index("plant")
    assert tb.loc["S1", "k"] == pytest.approx(0.8, abs=0.03)
    assert tb.loc["S1", "forecast_k"] == pytest.approx(0.95, abs=0.02)
    obs.drop(columns="forecast_mwh").to_parquet(tmp_path / "o2.parquet")
    assert (
        main(
            [
                *base[:5],
                "--observed",
                str(tmp_path / "o2.parquet"),
                "--calibration-target",
                "forecast",
                "-o",
                str(tmp_path / "x"),
            ]
        )
        == 2
    )


def _multi_get(url: str) -> bytes:
    """``fake_get`` with one archive object per requested point."""
    body = fake_get(url)
    if "archive" in url:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        n = len(q["latitude"][0].split(","))
        if n > 1:
            return json.dumps([json.loads(body)] * n).encode()
    return body


@pytest.mark.parametrize("source", ["explorador", "open-meteo"])
def test_cli_history_weather_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    pytest.importorskip("pvlib")
    from re2gtopt import __main__ as cli

    real = cli.PoliteCache
    monkeypatch.setattr(
        cli, "PoliteCache", lambda d, pause=3.0: real(d, get=_multi_get, pause=0.0)
    )
    plants = _plants(tmp_path)
    out = tmp_path / "hist.parquet"
    argv = [
        "history",
        "--plants",
        str(plants),
        "--years",
        str(YEAR),
        "--cache-dir",
        str(tmp_path / "cache"),
        "--weather-source",
        source,
        "-o",
        str(out),
    ]
    if source == "explorador":
        with pytest.raises(SystemExit, match="--climatology calendar"):
            main(argv)
        argv += ["--climatology", "calendar"]
    assert main(argv) == 0
    w = src.read_plant_weather(out)
    prov = json.loads((tmp_path / "hist.parquet.provenance.json").read_text())
    assert prov["weather_source"] == source
    assert prov["tool"] == "re2gtopt history"
    assert prov["local_calendar"] == "UTC-4 (fixed)"
    if source == "explorador":
        assert prov["climatology"] == "calendar"
        assert "dni" not in w.data and "pressure" not in w.data
        assert "recon" in prov["wind"]
    else:
        assert np.allclose(w.data["pressure"], 95000.0)
        assert "10/100 m" in prov["wind"]
    assert np.isfinite(w.column("wind_speed_hub", "W1")).all()  # type: ignore[arg-type]
    run = tmp_path / "run"
    assert (
        main(["model", "--plants", str(plants), "--weather", str(out), "-o", str(run)])
        == 0
    )
    cf = pd.read_parquet(run / "cf_model.parquet")
    assert cf["cf"].between(0, 1).all() and cf["cf"].max() > 0


def test_plant_history_met_without_100m_wind(tmp_path: Path) -> None:
    from gtopt_shared.weather_source import PointWeather

    plants = pl.read_inventory(_plants(tmp_path))
    times = _utc_year()
    met = PointWeather(
        times,
        [(p.lat, p.lon) for p in plants],
        {
            "temperature": np.full((8760, 2), 20.0),
            "ghi": np.zeros((8760, 2)),
        },
    )
    with pytest.raises(ValueError, match="no 100 m wind"):
        src.plant_history(
            plants,
            {"W1": 100.0},
            [YEAR],
            cache=PoliteCache(tmp_path, get=fake_get, pause=0.0),
            wind_source="era5",
            met=met,
        )


def test_cli_history_explorador_solar_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("pvlib")
    from gtopt_shared.tests._solar_fake import solar_get
    from re2gtopt import __main__ as cli

    def get(url: str) -> bytes:
        return solar_get(url) if "solar.minenergia.cl" in url else fake_get(url)

    real = cli.PoliteCache
    monkeypatch.setattr(
        cli, "PoliteCache", lambda d, pause=3.0: real(d, get=get, pause=0.0)
    )
    plants = _plants(tmp_path)
    out = tmp_path / "hist.parquet"
    argv = [
        "history",
        "--plants",
        str(plants),
        "--years",
        str(YEAR),
        "--cache-dir",
        str(tmp_path / "cache"),
        "--weather-source",
        "explorador-solar",
        "--explorador-solar-mode",
        "aggregate",
        "--climatology",
        "month-hour",
        "-o",
        str(out),
    ]
    assert main(argv) == 0
    w = src.read_plant_weather(out)
    assert {"dni", "dhi", "ghi", "temperature"} <= set(w.data)
    assert np.isfinite(w.column("wind_speed_hub", "W1")).all()  # type: ignore[arg-type]
    prov = json.loads((tmp_path / "hist.parquet.provenance.json").read_text())
    assert prov["weather_source"] == "explorador-solar"
    assert "recon" in prov["wind"]
    run = tmp_path / "run"
    assert (
        main(["model", "--plants", str(plants), "--weather", str(out), "-o", str(run)])
        == 0
    )
    hourly = [*argv[:-6], "-o", str(tmp_path / "h2.parquet")]
    hourly[hourly.index(str(YEAR))] = "2004"
    with pytest.raises(SystemExit, match="--explorador-solar-profile"):
        main(hourly)
