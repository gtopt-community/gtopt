# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``history --weather-source`` (the shared layer) on synthetic HTTP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from dlr2gtopt import __main__ as cli
from dlr2gtopt import _towers as tw
from dlr2gtopt._spans import write_towers
from dlr2gtopt.tests._synthetic import make_towers
from gtopt_shared.tests._solar_fake import solar_get
from gtopt_shared.tests.test_weather_source import explorador_get, open_meteo_get


def run(*argv: Any) -> int:
    return cli.main([str(a) for a in argv])


def fake_get(url: str) -> bytes:
    if "solar.minenergia.cl" in url:
        return solar_get(url)
    if "eolico.minenergia.cl" in url:
        return explorador_get(url)
    return open_meteo_get(url)


@pytest.fixture(name="case")
def fixture_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    write_towers(tmp_path / "towers.csv", make_towers(4))
    assert run("spans", tmp_path / "towers.csv", "-o", tmp_path / "spans.csv") == 0
    monkeypatch.setattr(tw, "urllib_get", fake_get)
    return tmp_path


def _history(case: Path, out: str, *extra: Any) -> int:
    return run(
        "history",
        "--spans",
        case / "spans.csv",
        "--cache-dir",
        case / "cache",
        "--pause",
        0,
        "--interp",
        "nearest",
        "-o",
        case / out,
        *extra,
    )


def test_history_explorador_calendar(case: Path) -> None:
    rc = _history(
        case,
        "h.parquet",
        "--years",
        "2021",
        "--weather-source",
        "explorador",
        "--climatology",
        "calendar",
        "--height",
        15,
    )
    assert rc == 0
    df = pd.read_parquet(case / "h.parquet")
    assert df["time"].min() == pd.Timestamp("2021-01-01")
    assert len(df) == 8760 * 3
    assert {"ambient_temperature", "wind_speed", "solar_irradiance"} <= set(df)
    prov = json.loads((case / "h.parquet.provenance.json").read_text())
    assert prov["weather_source"] == "explorador"
    assert prov["model"] == "wrf2010"
    assert prov["climatology"] == "calendar"
    assert prov["requested_years"] == [2021]
    assert prov["tool"] == "dlr2gtopt history"
    assert "no licence stated" in prov["licence"]


def test_history_open_meteo(case: Path) -> None:
    rc = _history(
        case,
        "h.csv",
        "--years",
        "2011",
        "--weather-source",
        "open-meteo",
        "--era5-snap",
        0,
        "--api-key",
        "K",
    )
    assert rc == 0
    prov = json.loads((case / "h.csv.provenance.json").read_text())
    assert prov["model"] == "era5" and prov["endpoint"] == "customer"
    assert "NON-COMMERCIAL" not in prov["licence"]  # stated in the help
    assert "non-commercial" in prov["licence"]


def test_history_weather_source_errors(case: Path) -> None:
    with pytest.raises(SystemExit, match="--climatology calendar"):
        _history(case, "x.csv", "--years", "2021", "--weather-source", "explorador")
    with pytest.raises(SystemExit, match="nothing to calibrate"):
        _history(
            case,
            "x.csv",
            "--years",
            "2010",
            "--weather-source",
            "explorador",
            "--calibrate",
            "wrf",
        )
    with pytest.raises(SystemExit, match="needs --calib-weather"):
        _history(
            case,
            "x.csv",
            "--years",
            "2011",
            "--weather-source",
            "open-meteo",
            "--calibrate",
            "wrf",
        )


def test_history_open_meteo_calibrated(case: Path) -> None:
    assert (
        _history(
            case,
            "wrf.parquet",
            "--years",
            "2010",
            "--weather-source",
            "explorador",
        )
        == 0
    )
    rc = _history(
        case,
        "cal.parquet",
        "--years",
        "2010",
        "--weather-source",
        "open-meteo",
        "--calibrate",
        "wrf",
        "--calib-weather",
        case / "wrf.parquet",
    )
    assert rc == 0
    prov = json.loads((case / "cal.parquet.provenance.json").read_text())
    assert prov["calibrate"] == "wrf"


def test_weather_command_accepts_weather_source_alias(case: Path) -> None:
    rc = run(
        "weather",
        "--spans",
        case / "spans.csv",
        "--weather-source",
        "explorador",
        "--interp",
        "nearest",
        "--cache-dir",
        case / "cache",
        "--pause",
        0,
        "-o",
        case / "w.csv",
    )
    assert rc == 0
    assert len(pd.read_csv(case / "w.csv")) > 0


@pytest.mark.parametrize(
    ("extra", "calendar"),
    [
        ((), "UTC-4 (fixed)"),
        (("--dst", "on"), "America/Santiago (civil time, DST)"),
        (("--dst", "on", "--climatology-utc-offset", 0), "UTC+0 (fixed)"),
    ],
)
def test_history_calendar_follows_time_basis(
    case: Path, extra: tuple[Any, ...], calendar: str
) -> None:
    rc = _history(
        case,
        "h.csv",
        "--years",
        "2021",
        "--weather-source",
        "explorador",
        "--climatology",
        "calendar",
        *extra,
    )
    assert rc == 0
    prov = json.loads((case / "h.csv.provenance.json").read_text())
    assert prov["local_calendar"] == calendar


def test_history_explorador_source_year_has_every_hour(case: Path) -> None:
    rc = _history(case, "h.csv", "--years", "2010", "--weather-source", "explorador")
    assert rc == 0
    df = pd.read_csv(case / "h.csv")
    assert len(df) == 8760 * 3
    prov = json.loads((case / "h.csv.provenance.json").read_text())
    assert prov["filled_hours"] == 4


@pytest.mark.parametrize(
    ("source", "years", "climatology"),
    [
        ("open-meteo", "2011", "off"),
        ("open-meteo", "1930", "calendar"),
        ("open-meteo", "1930", "month-hour"),
        ("explorador", "2010", "off"),
        ("explorador", "2021", "calendar"),
        ("explorador", "2021", "month-hour"),
    ],
)
def test_history_per_span_heights(
    case: Path, source: str, years: str, climatology: str
) -> None:
    """``--height-source spans`` with every source and climatology mode:
    a span with its own (taller) height gets more wind, the others keep
    the scalar ``--height``."""
    import dataclasses

    from dlr2gtopt._spans import read_spans, write_spans

    spans = read_spans(case / "spans.csv")
    spans[0] = dataclasses.replace(spans[0], height=60.0)
    write_spans(case / "spans_h.csv", spans)
    extra = ["--climatology", climatology, "--climatology-years", "2011"]
    if source == "explorador":
        extra = extra[:2]
    out = {}
    for hsrc in ("scalar", "spans"):
        rc = run(
            "history",
            "--spans",
            case / "spans_h.csv",
            "--cache-dir",
            case / "cache",
            "--pause",
            0,
            "--interp",
            "nearest",
            "--years",
            years,
            "--weather-source",
            source,
            "--height",
            15,
            "--height-source",
            hsrc,
            *extra,
            "-o",
            case / f"{hsrc}.csv",
        )
        assert rc == 0
        df = pd.read_csv(case / f"{hsrc}.csv")
        out[hsrc] = df.groupby("span")["wind_speed"].mean()
    first, second = sorted(out["spans"].index)[:2]
    assert out["spans"][first] > out["scalar"][first] + 0.1
    assert out["spans"][second] == pytest.approx(out["scalar"][second])
    prov = json.loads((case / "spans.csv.provenance.json").read_text())
    assert prov["height_range_m"] == [15.0, 60.0]
    assert prov["climatology"] == ("off" if climatology == "off" else climatology)


def test_history_span_heights_need_weather_source(case: Path) -> None:
    with pytest.raises(SystemExit, match="needs --weather-source"):
        _history(case, "x.csv", "--years", "2010", "--height-source", "spans")


ACADEMIA = "Academia o Investigación"


def test_history_explorador_solar_needs_profile_and_angle(case: Path) -> None:
    base = ("--years", "2004", "--weather-source", "explorador-solar")
    with pytest.raises(SystemExit, match="no wind direction.*45"):
        _history(case, "x.csv", *base, "--explorador-solar-profile", ACADEMIA)
    with pytest.raises(SystemExit, match="Academia o Investigación"):
        _history(case, "x.csv", *base, "--wind-angle-deg", 45)
    with pytest.raises(SystemExit, match="exclude each other"):
        _history(case, "x.csv", *base, "--perpendicular-wind", "--wind-angle-deg", 10)
    with pytest.raises(SystemExit, match=r"\[0, 90\]"):
        _history(
            case,
            "x.csv",
            *base,
            "--explorador-solar-profile",
            ACADEMIA,
            "--wind-angle-deg",
            120,
        )


def test_history_explorador_solar_hourly(
    case: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from dlr2gtopt._spans import read_spans

    with caplog.at_level("WARNING", logger="dlr2gtopt"):
        rc = _history(
            case,
            "h.csv",
            "--years",
            "2004",
            "--weather-source",
            "explorador-solar",
            "--explorador-solar-profile",
            ACADEMIA,
            "--perpendicular-wind",
        )
    assert rc == 0
    assert "SOLEVAPP" in caplog.text
    df = pd.read_csv(case / "h.csv")
    spans = read_spans(case / "spans.csv")
    first = df[df["span"] == spans[0].span]
    assert first["wind_direction"].iloc[0] == pytest.approx(
        (spans[0].azimuth + 90.0) % 360.0, abs=1e-3
    )
    prov = json.loads((case / "h.csv.provenance.json").read_text())
    assert prov["weather_source"] == "explorador-solar"
    assert prov["download_profile"] == ACADEMIA
    assert "SOLEVAPP" in prov["wind_height_basis"] and prov["wind_height_m"] == 5.5
    assert "fixed attack angle 90" in prov["wind_direction"]
    assert prov["filled_hours"] == 4


def test_history_explorador_solar_aggregate_span_heights(case: Path) -> None:
    import dataclasses

    from dlr2gtopt._spans import read_spans, write_spans

    spans = read_spans(case / "spans.csv")
    spans[0] = dataclasses.replace(spans[0], height=60.0)
    write_spans(case / "spans_h.csv", spans)
    common = [
        "--spans",
        case / "spans_h.csv",
        "--cache-dir",
        case / "cache",
        "--pause",
        0,
        "--years",
        "2021",
        "--weather-source",
        "explorador-solar",
        "--explorador-solar-mode",
        "aggregate",
        "--wind-angle-deg",
        0,
        "--explorador-solar-wind-height",
        8,
        "--height",
        15,
    ]
    with pytest.raises(SystemExit, match="use --climatology month-hour"):
        run("history", *common, "-o", case / "x.csv")
    out = {}
    for hsrc in ("scalar", "spans"):
        assert (
            run(
                "history",
                *common,
                "--climatology",
                "month-hour",
                "--height-source",
                hsrc,
                "-o",
                case / f"{hsrc}.csv",
            )
            == 0
        )
        out[hsrc] = (
            pd.read_csv(case / f"{hsrc}.csv").groupby("span")["wind_speed"].mean()
        )
    first, second = sorted(out["spans"].index)[:2]
    assert out["spans"][first] > out["scalar"][first]
    assert out["spans"][second] == pytest.approx(out["scalar"][second])
    prov = json.loads((case / "spans.csv.provenance.json").read_text())
    assert prov["model"] == "aggregate" and prov["climatology"] == "month-hour"
    assert "log law from 8 m" in prov["wind_extrapolation"]
    assert "given by the user" in prov["wind_height_basis"]


def test_history_explorador_solar_attack_angle_orders_ratings(case: Path) -> None:
    """45 deg (study value for an unknown direction) rates below 90 deg
    (perpendicular) and above 0 deg (parallel, the conservative variant)."""

    mean = {}
    for angle in (0, 45, 90):
        assert (
            _history(
                case,
                f"h{angle}.csv",
                "--years",
                "2004",
                "--weather-source",
                "explorador-solar",
                "--explorador-solar-profile",
                ACADEMIA,
                "--explorador-solar-wind-height",
                10,
                "--wind-angle-deg",
                angle,
            )
            == 0
        )
        amp = case / f"a{angle}.csv"
        assert (
            run(
                "ampacity",
                "--spans",
                case / "spans.csv",
                "--weather",
                case / f"h{angle}.csv",
                "--conductor",
                "flint",
                "-o",
                amp,
            )
            == 0
        )
        _, _, m = cli._read_matrix(amp)
        mean[angle] = m
        prov = json.loads((case / f"h{angle}.csv.provenance.json").read_text())
        assert f"fixed attack angle {angle} deg" in prov["wind_direction"]
    assert (mean[0] <= mean[45] + 1e-9).all() and (mean[45] <= mean[90] + 1e-9).all()
    assert np.nanmean(mean[0]) < np.nanmean(mean[45]) < np.nanmean(mean[90])


def test_history_explorador_solar_angle_from_eolico(case: Path) -> None:
    from dlr2gtopt import _wind_angle as wa
    from dlr2gtopt._spans import read_spans

    base = (
        "--years",
        "2004",
        "--weather-source",
        "explorador-solar",
        "--explorador-solar-profile",
        ACADEMIA,
    )
    with pytest.raises(SystemExit, match="drop --wind-angle-deg"):
        _history(
            case,
            "x.csv",
            *base,
            "--wind-angle-source",
            "explorador-eolico",
            "--wind-angle-deg",
            45,
        )
    with pytest.raises(SystemExit, match="is for --weather-source explorador-solar"):
        _history(
            case,
            "x.csv",
            "--years",
            "2010",
            "--weather-source",
            "explorador",
            "--wind-angle-source",
            "explorador-eolico",
        )
    with pytest.raises(SystemExit, match="model must be one of"):
        _history(
            case,
            "x.csv",
            *base,
            "--wind-angle-source",
            "explorador-eolico",
            "--wind-angle-wrf",
            "wrf1999",
        )
    assert (
        _history(case, "e.csv", *base, "--wind-angle-source", "explorador-eolico") == 0
    )
    spans = read_spans(case / "spans.csv")
    df = pd.read_csv(case / "e.csv")
    prov = json.loads((case / "e.csv.provenance.json").read_text())
    wa_prov = prov["wind_angle"]
    assert wa_prov["wrf_models"] == ["wrf2010", "wrf2015"]
    assert wa_prov["wrf_years"] == [2010, 2015]
    assert "Eolico" in prov["wind_direction"]
    for s in spans:
        # the synthetic Eolico wind blows from 90 deg at every hour
        phi = float(wa.attack_angle(90.0, s.azimuth))
        rec = wa_prov["per_span"][str(s.span)]
        assert np.allclose(np.array(rec["phi_deg"]), round(phi, 2), atol=0.01)
        assert np.allclose(np.array(rec["sector_share"]), 1.0)
        assert np.allclose(np.array(rec["R"]), 1.0)
        got = df[df["span"] == s.span]["wind_direction"].to_numpy()
        assert np.allclose(wa.attack_angle(got, s.azimuth), phi, atol=1e-3)
    # the solar wind speed is kept
    assert "SOLEVAPP" in prov["wind_height_basis"]
