# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Explorador Solar: profile declaration, hourly CSV parser, month-hour
aggregates and the ``explorador-solar`` weather source (synthetic HTTP)."""

from __future__ import annotations

import argparse
import json
import urllib.parse
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from gtopt_shared import explorador_solar as es
from gtopt_shared import weather_source as ws
from gtopt_shared.tests._solar_fake import hourly_csv_text, server_data, solar_get
from gtopt_shared.tests.test_weather_source import FakeCache

PTS = [(-31.6, -71.4), (-31.7, -71.3)]
ACADEMIA = "Academia o Investigación"


def _cache(tmp: Path) -> FakeCache:
    from dlr2gtopt._towers import PoliteCache

    pc = PoliteCache(tmp, get=solar_get, pause=0.0)
    fc = FakeCache(tmp, solar_get)
    fc.fetch = lambda key, url: (fc.urls.append(url), pc.fetch(key, url))[1]  # type: ignore[method-assign]
    return fc


# ---------------------------------------------------------------------------
# declaration
# ---------------------------------------------------------------------------


def test_registro_requires_an_explicit_profile() -> None:
    with pytest.raises(es.SolarError) as e:
        es.registro(es.SolarOptions())
    for p in es.PROFILES:
        assert p in str(e.value)
    with pytest.raises(es.SolarError, match="is not one of"):
        es.registro(es.SolarOptions(profile="Academia"))
    with pytest.raises(es.SolarError, match="profile-other"):
        es.registro(es.SolarOptions(profile=es.PROFILE_OTHER, profile_other=" \u200b "))
    r = es.registro(
        es.SolarOptions(profile=es.PROFILE_OTHER, profile_other="  grid\tstudy ")
    )
    # as the site's limpiarPerfilOtro: control characters (the tab) are
    # removed before the spaces are collapsed
    assert r["perfilOtro"] == "gridstudy"
    a = es.registro(es.SolarOptions(profile=ACADEMIA))
    b = es.registro(es.SolarOptions(profile=ACADEMIA))
    assert a["perfil"] == ACADEMIA and a["perfilOtro"] is None
    assert a["sesionId"] != b["sesionId"] and len(a["sesionId"]) == 36
    assert (
        es.registro(es.SolarOptions(profile=ACADEMIA, session_id="x"))["sesionId"]
        == "x"
    )


def test_router_url_round_trip() -> None:
    u = es.router_url({"tipo": "DHC_E", "datos": {"lat": -31.6}})
    q = json.loads(urllib.parse.unquote(u.split("/python-router/", 1)[1]))
    assert u.startswith(es.SOLAR_URL) and q["tipo"] == "DHC_E"


# ---------------------------------------------------------------------------
# hourly CSV
# ---------------------------------------------------------------------------


def test_parse_hourly_csv_real_layout() -> None:
    t, d, desc = es.parse_hourly_csv(hourly_csv_text((2004,), nan_tail=5))
    assert t[0] == datetime(2004, 1, 1, 4)  # UTC-4 local -> UTC
    assert t[-1] == datetime(2004, 12, 31, 3)  # the file ends 12-30 23:00
    assert set(d) == {"ghi", "dni", "dhi", "temperature", "wind_speed"}
    # horizontal diffuse comes from difh, not the tilted-plane dif
    assert desc["columns"]["dhi"] == "difh"
    meta = desc["metadata"]
    assert meta["model_version"] == "2.0" and meta["site_altitude_m"] == "154.0"
    assert meta["method"].startswith("Promedio")
    assert desc["variables"]["temp"]["unit"] == "C"
    assert desc["variables"]["dni"]["description"] == "Radiacion Directa Normal"
    assert desc["gaps_filled"] == {"ghi": 5, "dni": 5, "dhi": 5, "temperature": 1}
    assert desc["hour_label"].startswith("file stamp = centre")
    assert desc["time_basis"] == "UTC-4 (fixed)"
    assert not any(np.isnan(a).any() for a in d.values())
    # centre stamps: hour-start value = mean of stamps h and h+1
    k = t.index(datetime(2004, 6, 1, 16))  # local 12:00
    assert d["ghi"][k] == pytest.approx(
        0.5 * (1000.0 + 1000.0 * np.cos(np.pi / 12)), abs=0.01
    )
    # the NaN tail repeats the day before
    assert d["ghi"][-3] == pytest.approx(d["ghi"][-27])
    # the temperature gap (local 01-02 03:00) is interpolated
    k = t.index(datetime(2004, 1, 2, 7))
    assert d["temperature"][k] == pytest.approx(18.5)


def test_parse_hourly_csv_start_label_and_offset() -> None:
    t, d, desc = es.parse_hourly_csv(
        hourly_csv_text((2004,)), utc_offset=-3.0, label="start"
    )
    assert t[0] == datetime(2004, 1, 1, 3)
    k = t.index(datetime(2004, 6, 1, 15))  # local 12:00
    assert d["ghi"][k] == pytest.approx(1000.0)
    assert desc["hour_label"] == "file stamp = hour start"
    assert desc["time_basis"] == "UTC-3 (fixed)"


def test_parse_hourly_csv_errors() -> None:
    with pytest.raises(es.SolarError, match="label"):
        es.parse_hourly_csv(hourly_csv_text((2004,)), label="end")
    with pytest.raises(es.SolarError, match="no 'Fecha/Hora' header"):
        es.parse_hourly_csv("a,b\n1,2\n")
    with pytest.raises(es.SolarError, match="no ghi or temp"):
        es.parse_hourly_csv("Fecha/Hora,vel\n2004-01-01 00:00:00,1\n")
    with pytest.raises(es.SolarError, match="does not match"):
        es.parse_hourly_csv("Fecha/Hora,ghi,temp\nyesterday,1,2\n")
    # an all-NaN column is dropped; a short row reads as NaN
    _, d, desc = es.parse_hourly_csv(
        "Fecha/Hora,ghi,temp,dni\n2004-01-01 00:00:00,1,2,NaN\n"
        "2004-01-01 01:00:00,1,NaN\n2004-01-01 02:00:00,1,4,NaN\n"
    )
    assert "dni" not in d and desc["gaps_filled"] == {"temperature": 1}
    assert not desc["metadata"] and not desc["variables"]["ghi"]


def test_fill_radiation_forward_and_backward() -> None:
    a = np.arange(72, dtype=float)
    a[[2, 60]] = np.nan
    b, n = es._fill_radiation(a)
    assert n == 2 and b[2] == 26.0 and b[60] == 36.0
    c, m = es._interp(np.full(3, np.nan))
    assert m == 3 and np.isnan(c).all()


def test_hourly_csv_download_and_cache(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    opt = es.SolarOptions(profile=ACADEMIA, session_id="anon-1")
    text = es.hourly_csv(cache, *PTS[0], opt)
    assert text.startswith("DATOS HORARIOS")
    router = [u for u in cache.urls if "python-router" in u]
    q = json.loads(urllib.parse.unquote(router[0].split("/python-router/", 1)[1]))
    assert q["registro"] == {
        "perfil": ACADEMIA,
        "perfilOtro": None,
        "sesionId": "anon-1",
    }
    assert q["tipo"] == "DHC_E" and set(q["datos"]) == {
        "lat",
        "lon",
        "address",
        "nombre_sitio",
    }
    assert not list(tmp_path.rglob("*.router.json"))
    n = len(cache.urls)
    assert es.hourly_csv(cache, *PTS[0], opt) == text  # from the cache
    assert len(cache.urls) == n


def test_hourly_csv_server_refusals(tmp_path: Path) -> None:
    from dlr2gtopt._towers import PoliteCache

    def bad_profile(url: str) -> bytes:
        return solar_get(url.replace("Academia", "Nadie"))

    with pytest.raises(es.SolarError, match="HTTP 400"):
        es.hourly_csv(
            PoliteCache(tmp_path / "a", get=bad_profile, pause=0.0),
            *PTS[0],
            es.SolarOptions(profile=ACADEMIA),
        )

    def no_file(url: str) -> bytes:
        return b'{"_serverStatus":"error","_serverText":"sin datos"}'

    with pytest.raises(es.SolarError, match="sin datos"):
        es.hourly_csv(
            PoliteCache(tmp_path / "b", get=no_file, pause=0.0),
            *PTS[0],
            es.SolarOptions(profile=ACADEMIA),
        )
    with pytest.raises(es.SolarError, match="aggregates failed"):
        es.aggregate_payload(PoliteCache(tmp_path / "c", get=no_file, pause=0.0), 0, 0)


# ---------------------------------------------------------------------------
# aggregates
# ---------------------------------------------------------------------------


def test_parse_month_hour_layout_and_dhi() -> None:
    tab = es.parse_month_hour(server_data())
    assert tab["Temp"].shape == (12, 24)
    assert tab["Temp"][6, 13] == pytest.approx(613.0)  # July, 13:00
    assert np.allclose(tab["DHI"], tab["GHI"] * 0.25)
    bad = server_data()
    bad["HM"] = [v[:100] for v in bad["HM"]]  # type: ignore[attr-defined]
    with pytest.raises(es.SolarError, match="not 288"):
        es.parse_month_hour(bad)


def test_expand_month_hour_centres_and_months() -> None:
    tab = es.parse_month_hour(server_data())["Temp"]
    # UTC 16:00 -> centre 16:30 UTC = 12:30 local: halfway 12 and 13 (Jan)
    v = es.expand_month_hour(tab, [datetime(2021, 1, 15, 16)], -4.0)
    assert v[0] == pytest.approx(12.5)
    # UTC 1 Feb 03:00 -> local 31 Jan 23:30: January, wraps 23 -> 00
    v = es.expand_month_hour(tab, [datetime(2021, 2, 1, 3)], -4.0)
    assert v[0] == pytest.approx(0.5 * 23 + 0.5 * 0)


def test_log_wind() -> None:
    v = np.array([3.0])
    assert es.log_wind(v, 5.5, 5.5)[0] == pytest.approx(3.0)
    assert es.log_wind(v, 20.0, 5.5)[0] > 3.0
    with pytest.raises(es.SolarError, match="above z0"):
        es.log_wind(v, 0.01, 5.5)


# ---------------------------------------------------------------------------
# the weather source
# ---------------------------------------------------------------------------


def test_coverage_table() -> None:
    c = ws.coverage("explorador-solar")
    assert (c.model, c.first_year, c.last_year) == ("hourly", 2004, 2016)
    assert "wind_direction" not in c.fields and "dhi" in c.fields
    assert "5.5 m" in ws.coverage("explorador-solar").resolution


def test_hourly_source_years_fill_and_heights(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    opt = es.SolarOptions(profile=ACADEMIA, wind_height=8.0)
    with pytest.raises(ws.WeatherSourceError, match="--explorador-solar-profile"):
        ws.point_weather("explorador-solar", cache, PTS, [2004])
    with pytest.raises(ws.WeatherSourceError, match="needs a height"):
        ws.point_weather(
            "explorador-solar",
            cache,
            PTS,
            [2004],
            solar=es.SolarOptions(profile=ACADEMIA, wind_height=None),
            need=("wind_speed",),
        )
    assert not cache.urls  # refused before any request
    no_wind = ws.point_weather(
        "explorador-solar",
        cache,
        PTS,
        [2004],
        solar=es.SolarOptions(profile=ACADEMIA, wind_height=None),
    )
    assert "wind_speed" not in no_wind.data
    assert "left out" in no_wind.provenance["wind_speed"]
    pw = ws.point_weather(
        "explorador-solar",
        cache,
        PTS,
        [2004],
        solar=opt,
        heights=[None, 50.0],
        need=("ghi", "dni", "dhi", "temperature"),
    )
    assert len(pw.times) == 8784 and pw.provenance["filled_hours"] == 4
    assert np.allclose(
        pw.data["wind_speed"][:, 0], es.log_wind(np.array([3.0]), 10.0, 8.0)
    )
    assert pw.data["wind_speed"][0, 1] > pw.data["wind_speed"][0, 0]
    assert pw.provenance["download_profile"] == ACADEMIA
    assert "fitted" in pw.provenance["time_basis"]
    assert pw.provenance["csv_metadata"][str(PTS[0])]["model_version"] == "2.0"
    assert pw.provenance["wind_height_m"] == 8.0
    cal = ws.point_weather(
        "explorador-solar",
        cache,
        PTS,
        [2021],
        solar=opt,
        climatology="calendar",
        source_years=[2005],
    )
    assert cal.source_times is not None
    assert {t.year for t in cal.source_times} <= {2005, 2006}
    mh = ws.point_weather(
        "explorador-solar",
        cache,
        PTS,
        [2021],
        solar=opt,
        climatology="month-hour",
        source_years=[2004, 2005],
    )
    assert mh.source_times is None


def test_hourly_source_refusals(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    opt = es.SolarOptions(profile=ACADEMIA)
    with pytest.raises(ws.WeatherSourceError, match="has no wind_direction"):
        ws.point_weather(
            "explorador-solar", cache, PTS, [2004], solar=opt, need=("wind_direction",)
        )
    with pytest.raises(ws.WeatherSourceError, match="no hours of"):
        ws.point_weather("explorador-solar", cache, PTS, [2010], solar=opt)

    def ghi_only(url: str) -> bytes:
        if "/temp/" in url:
            return b"Fecha/Hora,ghi,temp\n2004-01-01 00:00:00,0,1\n2004-01-01 01:00:00,0,1\n"
        return solar_get(url)

    from dlr2gtopt._towers import PoliteCache

    with pytest.raises(ws.WeatherSourceError, match="CSV has no dni"):
        ws.point_weather(
            "explorador-solar",
            PoliteCache(tmp_path / "g", get=ghi_only, pause=0.0),
            PTS,
            [2004],
            solar=opt,
            need=("dni",),
        )

    def shifted(url: str) -> bytes:
        if "/temp/" in url and "31.70000" in url:
            return hourly_csv_text((2005,)).encode()
        return solar_get(url)

    def keyed(tmp: Path):  # type: ignore[no-untyped-def]
        class C(PoliteCache):
            def fetch(self, key: str, url: str) -> bytes:
                return super().fetch(key, url + ("#" + key if "/temp/" in url else ""))

        return C(tmp, get=shifted, pause=0.0)

    with pytest.raises(ws.WeatherSourceError, match="different hours"):
        ws.point_weather(
            "explorador-solar", keyed(tmp_path / "s"), PTS, [2004], solar=opt
        )


def test_aggregate_source(tmp_path: Path) -> None:
    from dlr2gtopt._tz import TimeBasis

    cache = _cache(tmp_path)
    for clim, msg in (
        ("off", "use --climatology month-hour"),
        ("calendar", "not a time series"),
    ):
        with pytest.raises(ws.WeatherSourceError, match=msg):
            ws.point_weather(
                "explorador-solar",
                cache,
                PTS,
                [2021],
                model="aggregate",
                climatology=clim,
            )
    with pytest.raises(ws.WeatherSourceError, match="only has month x hour means"):
        ws.point_weather(
            "explorador-solar",
            cache,
            PTS,
            [2021],
            model="aggregate",
            climatology="month-hour",
            stat="q10",
        )
    with pytest.raises(ws.WeatherSourceError, match="has no wind_direction"):
        ws.point_weather(
            "explorador-solar",
            cache,
            PTS,
            [2021],
            model="aggregate",
            climatology="month-hour",
            need=("wind_direction",),
        )
    kw = {
        "model": "aggregate",
        "climatology": "month-hour",
        "heights": [None, 30.0],
        "solar": es.SolarOptions(wind_height=8.0),
    }
    fixed = ws.point_weather("explorador-solar", cache, PTS, [2021], **kw)  # type: ignore[arg-type]
    civil = ws.point_weather(
        "explorador-solar",
        cache,
        PTS,
        [2021],
        utc_offset=TimeBasis(0.0, "America/Santiago"),
        **kw,  # type: ignore[arg-type]
    )
    # the source labels are fixed UTC-4: the tool's DST never shifts them
    for f, a in fixed.data.items():
        assert np.array_equal(a, civil.data[f])
    assert civil.provenance["local_calendar"].startswith("America/Santiago")
    assert len(fixed.times) == 8760 and fixed.source_times is None
    jan = fixed.times.index(datetime(2021, 1, 15, 16))
    assert fixed.data["temperature"][jan, 0] == pytest.approx(12.5)
    assert fixed.data["wind_speed"][0, 1] > fixed.data["wind_speed"][0, 0]
    assert np.allclose(fixed.data["dhi"], fixed.data["ghi"] * 0.25)
    assert "hour-major" in fixed.provenance["aggregate"]

    def no_dni(url: str) -> bytes:
        d = server_data()
        k = d["VarsHM"].index("DNI")  # type: ignore[attr-defined]
        d["VarsHM"] = [n for n in d["VarsHM"] if n != "DNI"]  # type: ignore[attr-defined]
        d["HM"] = [v for i, v in enumerate(d["HM"]) if i != k]  # type: ignore[attr-defined]
        return json.dumps({"_serverStatus": "ok", "_serverData": d}).encode()

    from dlr2gtopt._towers import PoliteCache

    with pytest.raises(ws.WeatherSourceError, match="aggregate has no dni"):
        ws.point_weather(
            "explorador-solar",
            PoliteCache(tmp_path / "n", get=no_dni, pause=0.0),
            PTS,
            [2021],
            model="aggregate",
            climatology="month-hour",
            need=("dni",),
        )
    with pytest.raises(ws.WeatherSourceError, match="above z0"):
        ws.point_weather(
            "explorador-solar",
            cache,
            PTS,
            [2021],
            model="aggregate",
            climatology="month-hour",
            heights=[0.01, None],
        )


def test_cli_solar_options() -> None:
    p = argparse.ArgumentParser()
    ws.add_weather_source_arguments(p)
    a = p.parse_args(["--weather-source", "explorador-solar"])
    assert ws.model_for(a) == "hourly"
    o = ws.solar_options(a)
    assert o.profile is None and o.z0 == pytest.approx(0.03)
    a = p.parse_args(
        [
            "--weather-source",
            "explorador-solar",
            "--explorador-solar-mode",
            "aggregate",
            "--explorador-solar-profile",
            ACADEMIA,
        ]
    )
    assert ws.model_for(a) == "aggregate"
    assert ws.solar_options(a).profile == ACADEMIA
    assert ws.model_for(argparse.Namespace(weather_source=None)) is None
    assert ws.solar_options(argparse.Namespace()).utc_offset == -4.0


def test_solar_options_from_args() -> None:
    p = argparse.ArgumentParser()
    ws.add_weather_source_arguments(p)
    args = p.parse_args(
        [
            "--weather-source",
            "explorador-solar",
            "--explorador-solar-wind-height",
            "3",
            "--explorador-solar-label",
            "start",
            "--explorador-wrf",
            "wrf2015",
        ]
    )
    opt = ws.solar_options(args)
    assert (opt.wind_height, opt.label) == (3.0, "start")
