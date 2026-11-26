# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Shared weather-source layer: coverage, climatology alignment and both
backends on synthetic HTTP payloads (no network, no downloaded data)."""

from __future__ import annotations

import argparse
import json
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from gtopt_shared import weather_source as ws

SRC_YEAR = 2010


class FakeCache:
    """In-memory stand-in of ``PoliteCache`` that records the requests."""

    def __init__(self, tmp: Path, get) -> None:  # type: ignore[no-untyped-def]
        self.cache_dir = tmp
        self.get = get
        self.keys: list[str] = []
        self.urls: list[str] = []

    def fetch(self, key: str, url: str) -> bytes:
        self.keys.append(key)
        self.urls.append(url)
        return bytes(self.get(url))


def _hours(year: int) -> list[datetime]:
    return ws.year_hours([year])


def explorador_get(url: str) -> bytes:
    """Minimal Explorador router + CSV shapes (synthetic values)."""
    if "/python-router/" in url:
        q = json.loads(urllib.parse.unquote(url.split("/python-router/", 1)[1]))
        d = q["datos"]
        assert q["tipo"] == "DescargaExploracion"
        name = f"{d['variable']}_{'-'.join(f'{h:g}' for h in d['alturas'])}.csv"
        return json.dumps({"_serverData": name}).encode()
    name = url.rsplit("/temp/", 1)[1]
    var, hs = name[:-4].split("_")
    heights = [float(h) for h in hs.split("-")]
    rows = ["," + ",".join(f"{h:g}" for h in heights)]
    for k, loc in enumerate(_hours(SRC_YEAR)):  # local 2010 (UTC-4)
        vals = {
            "vel": lambda h: 5.0 * (h / 10.0) ** 0.2,
            "vdir": lambda h: 90.0,
            "temp": lambda h, k=k: 10.0 + (k % 24) - 0.01 * h,
            "glb": lambda h, k=k: float(max(0, 12 - abs(k % 24 - 16)) * 80),
        }[var]
        rows.append(
            loc.strftime("%Y-%m-%d %H:%M:%S")
            + ","
            + ",".join(f"{vals(h):.3f}" for h in heights)
        )
    return ("\n".join(rows) + "\n").encode()


def open_meteo_get(url: str) -> bytes:
    """Archive JSON shape for the requested points and year."""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    year = int(q["start_date"][0][:4])
    times = _hours(year)
    n = len(times)
    hod = np.array([t.hour for t in times])
    sun = np.clip(1000 * np.sin(np.pi * (hod - 10) / 12), 0, None)
    lats = q["latitude"][0].split(",")
    items = []
    for i, _ in enumerate(lats):
        h: dict[str, object] = {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in times]}
        for v in q["hourly"][0].split(","):
            if v == "temperature_2m":
                a: list[float | None] = [15.0 + i] * n
                a[3] = None
                h[v] = a
            elif v == "surface_pressure":
                h[v] = [950.0] * n
            elif v == "shortwave_radiation":
                h[v] = sun.tolist()
            elif v == "direct_normal_irradiance":
                h[v] = (0.8 * sun).tolist()
            elif v == "diffuse_radiation":
                h[v] = (0.1 * sun).tolist()
            elif v == "wind_speed_10m":
                h[v] = [5.0] * n
            elif v == "wind_speed_100m":
                h[v] = [8.0] * n
            else:
                h[v] = [180.0] * n
        items.append({"elevation": 100.0, "hourly": h})
    return json.dumps(items if len(items) > 1 else items[0]).encode()


PTS = [(-31.505, -71.505), (-31.6, -71.2)]


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------


def test_coverage_and_fields() -> None:
    c = ws.coverage("explorador")
    assert (c.model, c.first_year, c.last_year) == ("wrf2010", 2010, 2010)
    assert c.missing([2009, 2010, 2011]) == [2009, 2011]
    om = ws.coverage("open-meteo", "ecmwf_ifs")
    assert om.missing([2016, 2017]) == [2016]
    assert om.missing([date.today().year + 1]) == [date.today().year + 1]
    with pytest.raises(ws.WeatherSourceError, match="weather source must be"):
        ws.coverage("nasa")
    with pytest.raises(ws.WeatherSourceError, match="model must be one of"):
        ws.coverage("explorador", "recon")
    ws.require_fields(c, ["temperature", "ghi"])
    with pytest.raises(ws.WeatherSourceError, match="has no dni"):
        ws.require_fields(c, ["ghi", "dni"])


def test_point_weather_shape_and_provenance(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="shape"):
        ws.PointWeather([datetime(2010, 1, 1)], PTS, {"ghi": np.zeros((2, 2))})
    prov = ws.provenance(
        ws.coverage("explorador"), years=[2021], climatology="month-hour", stat="q10"
    )
    assert prov["climatology_stat"] == "q10"
    assert "height_m" not in prov
    pw = ws.PointWeather([], [], provenance=prov)
    out = pw.write_provenance(tmp_path / "sub" / "w.parquet")
    assert out.name == "w.parquet.provenance.json"
    assert json.loads(out.read_text())["weather_source"] == "explorador"


# ---------------------------------------------------------------------------
# climatology
# ---------------------------------------------------------------------------


def test_year_hours_leap() -> None:
    assert len(ws.year_hours([2020, 2021, 2020])) == 8784 + 8760


def test_calendar_index_same_local_hour_and_leap_day() -> None:
    src = [t + timedelta(hours=4) for t in _hours(SRC_YEAR)]  # local 2010
    target = ws.year_hours([2020])
    rows = ws.calendar_index(src, target, -4.0)
    loc_src = [src[r] - timedelta(hours=4) for r in rows]
    loc_tgt = [t - timedelta(hours=4) for t in target]
    for s, t in zip(loc_src, loc_tgt):
        assert (s.hour, s.month) == (t.hour, t.month)
        assert s.day == (28 if (t.month, t.day) == (2, 29) else t.day)


def test_calendar_index_edges_and_errors() -> None:
    # local 1 Jan 00:00 (UTC-4) of a UTC-year source is its UTC 04:00
    src = _hours(SRC_YEAR)
    rows = ws.calendar_index(src, [datetime(2021, 1, 1, 4)], -4.0)
    assert src[int(rows[0])] == datetime(2010, 1, 1, 4)
    # a missing key late in a day takes the nearest earlier hour
    short = [datetime(2010, 3, 1, h) for h in range(5)]
    rows = ws.calendar_index(short, [datetime(2021, 3, 1, 20)], 0.0)
    assert int(rows[0]) == 4
    late = [datetime(2010, 3, 1, h) for h in range(5, 9)]
    rows = ws.calendar_index(late, [datetime(2021, 3, 1, 2)], 0.0)
    assert int(rows[0]) == 0
    with pytest.raises(ws.WeatherSourceError, match="no hours"):
        ws.calendar_index([], [datetime(2021, 1, 1)], 0.0)
    with pytest.raises(ws.WeatherSourceError, match="no source hour"):
        ws.calendar_index(short, [datetime(2021, 4, 1)], 0.0)


def test_parse_stat() -> None:
    assert ws.parse_stat("mean") is None
    assert ws.parse_stat("median") == 0.5
    assert ws.parse_stat("q10") == pytest.approx(0.1)
    assert ws.parse_stat("q2.5") == pytest.approx(0.025)
    with pytest.raises(ws.WeatherSourceError, match="mean, median or qNN"):
        ws.parse_stat("p90")


def test_month_hour_table() -> None:
    times = _hours(SRC_YEAR)
    hod = np.array([t.hour for t in times], dtype=float)
    a = np.column_stack([hod, 2 * hod])
    tab = ws.month_hour_table(times, a, utc_offset=0.0)
    assert tab.shape == (12, 24, 2)
    assert tab[5, 7, 1] == pytest.approx(14.0)
    q = ws.month_hour_table(times, np.column_stack([hod]), utc_offset=0.0, stat="q90")
    assert q[0, 3, 0] == pytest.approx(3.0)
    d = np.column_stack([np.where(hod % 2 == 0, 350.0, 10.0)])
    circ = ws.month_hour_table(times, d, utc_offset=0.0, circular=True)
    assert circ[0, 0, 0] == pytest.approx(350.0)
    assert circ[0, 1, 0] == pytest.approx(10.0)
    with pytest.raises(ws.WeatherSourceError, match="misses some"):
        ws.month_hour_table(times[:100], a[:100], utc_offset=0.0)


def test_apply_climatology_modes() -> None:
    times = _hours(SRC_YEAR)
    hod = np.array([t.hour for t in times], dtype=float)
    pw = ws.PointWeather(
        times,
        [PTS[0]],
        {
            "temperature": hod[:, None] + 0.0,
            "wind_direction": np.full((len(times), 1), 90.0),
        },
        list(times),
        {"weather_source": "x"},
    )
    cal = ws.apply_climatology(pw, [2021, 2022], "calendar", utc_offset=0.0)
    assert len(cal.times) == 2 * 8760
    assert cal.source_times is not None and cal.source_times[0] == times[0]
    assert np.array_equal(cal.data["temperature"][:24, 0], np.arange(24.0))
    assert "calendar_rule" in cal.provenance
    mh = ws.apply_climatology(pw, [2021], "month-hour", stat="median", utc_offset=0.0)
    assert mh.source_times is None
    assert mh.provenance["climatology_stat"] == "median"
    assert np.allclose(mh.data["wind_direction"], 90.0)
    for bad in ("off", "tmy"):
        with pytest.raises(ws.WeatherSourceError, match="calendar or month-hour"):
            ws.apply_climatology(pw, [2021], bad)


# ---------------------------------------------------------------------------
# Open-Meteo backend
# ---------------------------------------------------------------------------


def test_open_meteo_url_and_parse() -> None:
    u = ws.open_meteo_url([-31.5], [-71.5], 2011)
    assert u.startswith(ws.OPEN_METEO_ARCHIVE_URL)
    assert "start_date=2011-01-01" in u and "apikey" not in u
    k = ws.open_meteo_url([-31.5], [-71.5], 2011, api_key="K")
    assert k.startswith(ws.OPEN_METEO_CUSTOMER_ARCHIVE_URL) and "apikey=K" in k
    own = ws.open_meteo_url([0], [0], 2011, base_url="http://x/a", api_key="K")
    assert own.startswith("http://x/a?")
    t, per = ws.parse_open_meteo(
        {"hourly": {"time": ["2011-01-01T00:00", "2011-01-01T01:00"], "x": [1, 2]}}
    )
    assert t[1] == datetime(2011, 1, 1, 1)
    assert np.isnan(per[0]["temperature_2m"]).all()


def test_open_meteo_points(tmp_path: Path) -> None:
    cache = FakeCache(tmp_path, open_meteo_get)
    pts = [PTS[0], PTS[0], PTS[1]]
    pw = ws.open_meteo_points(cache, pts, [2011], height=10.0, api_key="SECRET")
    assert len(cache.urls) == 1 and "SECRET" not in cache.keys[0]
    assert pw.data["wind_speed"].shape == (8760, 3)
    assert np.allclose(pw.data["wind_speed"], 5.0)  # 10 m level
    assert np.allclose(pw.data["wind_direction"], 180.0)
    assert np.allclose(pw.data["pressure"], 95000.0)
    assert pw.data["temperature"][3, 0] == pytest.approx(15.0)  # gap filled
    assert pw.data["temperature"][0, 2] == pytest.approx(16.0)
    sun = np.clip(1000 * np.sin(np.pi * (np.arange(48) % 24 - 10) / 12), 0, None)
    assert np.allclose(pw.data["ghi"][:47, 0], sun[1:48])  # hour-start label
    assert pw.provenance["endpoint"] == "customer"
    hi = ws.open_meteo_points(
        FakeCache(tmp_path, open_meteo_get), [PTS[0]], [2011], height=55.0, snap=0.25
    )
    assert 5.0 < float(hi.data["wind_speed"][0, 0]) < 8.0
    assert hi.provenance["radiation_label"] == "hour start"


def test_open_meteo_points_errors(tmp_path: Path) -> None:
    with pytest.raises(ws.WeatherSourceError, match="starts in 1940"):
        ws.open_meteo_points(FakeCache(tmp_path, open_meteo_get), PTS, [1930])

    def one_point(url: str) -> bytes:
        return json.dumps(json.loads(open_meteo_get(url))[0]).encode()

    with pytest.raises(ws.WeatherSourceError, match="returned 1 points"):
        ws.open_meteo_points(FakeCache(tmp_path, one_point), PTS, [2011])


# ---------------------------------------------------------------------------
# Explorador backend (the dlr2gtopt client, reused)
# ---------------------------------------------------------------------------


def test_explorador_points(tmp_path: Path) -> None:
    cache = FakeCache(tmp_path, explorador_get)
    pw = ws.explorador_points(cache, [PTS[0]], height=10.0, method="nearest")
    assert set(pw.data) == {"temperature", "wind_speed", "wind_direction", "ghi"}
    assert np.allclose(pw.data["wind_speed"], 5.0, atol=1e-3)
    assert np.allclose(pw.data["wind_direction"], 90.0, atol=1e-6)
    assert pw.times[0] == datetime(2010, 1, 1, 4)  # UTC-4 local -> UTC
    assert pw.provenance["model"] == "wrf2010"
    assert any("python-router" in u for u in cache.urls)


# ---------------------------------------------------------------------------
# point_weather: the entry point both tools call
# ---------------------------------------------------------------------------


def test_point_weather_explorador_calendar(tmp_path: Path) -> None:
    cache = FakeCache(tmp_path, explorador_get)
    with pytest.raises(ws.WeatherSourceError, match="--climatology calendar"):
        ws.point_weather("explorador", cache, PTS, [2021], method="nearest")
    pw = ws.point_weather(
        "explorador",
        cache,
        [PTS[0]],
        [2020, 2021],
        climatology="calendar",
        method="nearest",
    )
    assert len(pw.times) == 8784 + 8760
    assert pw.provenance["requested_years"] == [2020, 2021]
    assert pw.provenance["climatology"] == "calendar"
    assert pw.source_times is not None
    assert all(s.year in (2010, 2011) for s in pw.source_times)
    # same local hour of day: the synthetic temperature follows k % 24
    loc = [t - timedelta(hours=4) for t in pw.times[:48]]
    src = [t - timedelta(hours=4) for t in pw.source_times[:48]]
    assert [a.hour for a in loc] == [b.hour for b in src]


def test_point_weather_explorador_in_coverage(tmp_path: Path) -> None:
    pw = ws.point_weather(
        "explorador",
        FakeCache(tmp_path, explorador_get),
        [PTS[0]],
        [2010],
        method="nearest",
        need=("temperature", "ghi"),
    )
    # the first 4 UTC hours (local 31 Dec 20-23 of 2009) are filled from
    # the same local hours of the source year, and recorded as filled
    assert len(pw.times) == 8760
    assert pw.times[0] == datetime(2010, 1, 1, 0)
    assert pw.times[-1] == datetime(2010, 12, 31, 23)
    assert pw.source_times is not None
    assert pw.source_times[:4] == [datetime(2011, 1, 1, h) for h in range(4)]
    assert pw.source_times[4:] == pw.times[4:]
    assert pw.provenance["filled_hours"] == 4
    assert pw.provenance["filled_times"][0] == "2010-01-01T00:00:00"
    assert "calendar" in pw.provenance["fill_rule"]


def test_fill_to_years_without_gaps() -> None:
    times = _hours(SRC_YEAR)
    pw = ws.PointWeather(times, [PTS[0]], {"ghi": np.zeros((8760, 1))})
    out = ws.fill_to_years(pw, [SRC_YEAR])
    assert out.provenance["filled_hours"] == 0
    assert "fill_rule" not in out.provenance


def test_calendar_follows_a_civil_clock() -> None:
    from dlr2gtopt._tz import TimeBasis

    clock = TimeBasis(0.0, "America/Santiago")
    assert ws.describe_basis(clock) == "America/Santiago (civil time, DST)"
    assert ws.describe_basis(-4) == "UTC-4 (fixed)"
    src = _hours(SRC_YEAR)

    class YearClock:
        """UTC-3 in 2021, UTC-4 in 2010 (a DST rule that changed)."""

        def local(self, times: list[datetime]) -> list[datetime]:
            return [t + timedelta(hours=-3 if t.year == 2021 else -4) for t in times]

        def describe(self) -> str:
            return "year clock"

    t = [datetime(2021, 3, 1, 12)]
    assert src[int(ws.calendar_index(src, t, YearClock())[0])] == datetime(
        2010, 3, 1, 13
    )
    assert src[int(ws.calendar_index(src, t, -4.0)[0])] == datetime(2010, 3, 1, 12)
    assert clock.local([datetime(2021, 1, 15, 15)])[0].hour == 12
    pw = ws.PointWeather(
        src, [PTS[0]], {"temperature": np.zeros((8760, 1))}, list(src), {}
    )
    out = ws.apply_climatology(pw, [2021], "month-hour", utc_offset=clock)
    assert len(out.times) == 8760


def test_calendar_basis_helper() -> None:
    a = argparse.Namespace(climatology_utc_offset=None)
    assert ws.calendar_basis(a) == -4.0
    assert ws.calendar_basis(a, -3.0) == -3.0
    a.climatology_utc_offset = 0
    assert ws.calendar_basis(a, -3.0) == 0.0


def test_point_weather_open_meteo_and_errors(tmp_path: Path) -> None:
    cache = FakeCache(tmp_path, open_meteo_get)
    pw = ws.point_weather("open-meteo", cache, PTS, [2011], need=ws.FIELDS)
    assert len(pw.times) == 8760 and "dni" in pw.data
    mh = ws.point_weather(
        "open-meteo",
        cache,
        PTS,
        [1930],
        model="ecmwf_ifs",
        climatology="month-hour",
        source_years=[2018],
    )
    assert mh.provenance["climatology"] == "month-hour"
    assert mh.source_times is None
    with pytest.raises(ws.WeatherSourceError, match="climatology must be one of"):
        ws.point_weather("open-meteo", cache, PTS, [2011], climatology="x")
    with pytest.raises(ws.WeatherSourceError, match="has no dni"):
        ws.point_weather("explorador", cache, PTS, [2010], need=("dni",))
    with pytest.raises(ws.WeatherSourceError, match="--climatology-years"):
        ws.point_weather(
            "open-meteo", cache, PTS, [1930], model="era5", climatology="calendar"
        )
    with pytest.raises(ws.WeatherSourceError, match="climatology years"):
        ws.point_weather(
            "explorador",
            cache,
            PTS,
            [2021],
            climatology="calendar",
            source_years=[2011],
        )
    # covered years with a climatology mode: the real years are used
    real = ws.point_weather(
        "open-meteo", cache, PTS, [2011], climatology="calendar", need=("ghi",)
    )
    assert real.provenance["climatology"] == "off"


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def test_parse_years() -> None:
    assert ws.parse_years(None) is None
    assert ws.parse_years("2008,2010-2011") == [2008, 2010, 2011]
    with pytest.raises(ws.WeatherSourceError, match="bad years"):
        ws.parse_years("x-1")


def test_cli_arguments_and_model_for() -> None:
    p = argparse.ArgumentParser()
    ws.add_weather_source_arguments(p)
    a = p.parse_args([])
    assert a.weather_source is None and a.climatology == "off"
    assert ws.model_for(a) is None
    a = p.parse_args(["--weather-source", "explorador", "--explorador-wrf", "wrf2015"])
    assert ws.model_for(a) == "wrf2015"
    a = p.parse_args(["--weather-source", "open-meteo", "--api-key", "K"])
    assert ws.model_for(a) == "era5" and a.api_key == "K"
    a = p.parse_args(
        ["--weather-source", "open-meteo", "--open-meteo-model", "ecmwf_ifs"]
    )
    assert ws.model_for(a) == "ecmwf_ifs"
    q = argparse.ArgumentParser()
    q.add_argument("--era5-model", default="era5_land")
    ws.add_weather_source_arguments(q, with_open_meteo_keys=False, with_model=False)
    a = q.parse_args(["--weather-source", "open-meteo"])
    assert ws.model_for(a) == "era5_land"
    assert not hasattr(a, "api_key")
    assert "NON-COMMERCIAL" in ws.WEATHER_SOURCE_HELP


def test_per_point_heights(tmp_path: Path) -> None:
    cache = FakeCache(tmp_path, open_meteo_get)
    pw = ws.open_meteo_points(cache, PTS, [2011], height=10.0, heights=[None, 100.0])
    assert np.allclose(pw.data["wind_speed"][:, 0], 5.0)
    assert np.allclose(pw.data["wind_speed"][:, 1], 8.0)
    assert pw.provenance["height_range_m"] == [10.0, 100.0]
    with pytest.raises(ws.WeatherSourceError, match="1 per-point heights for 2"):
        ws.open_meteo_points(cache, PTS, [2011], heights=[10.0])
    ex = ws.explorador_points(
        FakeCache(tmp_path, explorador_get),
        [PTS[0]],
        height=10.0,
        heights=[20.0],
        method="nearest",
    )
    assert ex.data["wind_speed"][0, 0] > 5.0
    assert ex.provenance["height_source"].startswith("per point")


def test_calendar_index_fallbacks() -> None:
    dt = datetime
    # local (UTC+0) source: 12-30 whole day, 12-31 only 20:00; 01-02 03:00
    src = [dt(2005, 12, 30, h) for h in range(24)] + [
        dt(2005, 12, 31, 20),
        dt(2005, 1, 2, 3),
    ]
    tgt = [dt(2021, 12, 31, 12), dt(2021, 12, 31, 20), dt(2021, 1, 1, 3)]
    rows = ws.calendar_index(src, tgt, 0.0)
    assert list(rows) == [12, 24, 25]  # day before; exact; day after
    rows = ws.calendar_index(src[24:25], [dt(2021, 12, 31, 5)], 0.0)
    assert list(rows) == [0]  # nearest hour of the same day
    with pytest.raises(ws.WeatherSourceError, match="no source hour"):
        ws.calendar_index(src[24:25], [dt(2021, 6, 1, 5)], 0.0)
