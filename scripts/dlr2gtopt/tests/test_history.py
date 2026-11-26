# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Multi-year weather histories: ERA5 (Open-Meteo archive) + recon wind."""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from dlr2gtopt import _history as hi
from dlr2gtopt import _weather as wx
from dlr2gtopt._spans import Span
from dlr2gtopt._towers import PoliteCache
from dlr2gtopt._weather import Weather
from dlr2gtopt.tests._synthetic import explorador_csv
from dlr2gtopt.tests.test_weather import FakeExplorador


def _spans() -> list[Span]:
    return [
        Span("L", 1, -31.8975, -71.4975, 0.0, 300.0, 0.15),
        Span("L", 2, -31.8875, -71.4925, 90.0, 300.0, 0.45),
    ]


class FakeArchive:
    """Recorded-response getter for the Open-Meteo archive API: every point
    gets T = 10 + hour/10, GHI = 100·hour (one null), wind 4 m/s from 200°."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        lats = q["latitude"][0].split(",")
        start = datetime.strptime(q["start_date"][0], "%Y-%m-%d")
        end = datetime.strptime(q["end_date"][0], "%Y-%m-%d") + timedelta(days=1)
        n = int((end - start).total_seconds() // 3600)
        times = [
            (start + timedelta(hours=k)).strftime("%Y-%m-%dT%H:%M") for k in range(n)
        ]
        hrs = [int(t[11:13]) for t in times]
        one = {
            "hourly": {
                "time": times,
                "temperature_2m": [10 + h / 10 for h in hrs],
                "shortwave_radiation": [
                    None if k == 1 else 100.0 * h for k, h in enumerate(hrs)
                ],
                "wind_speed_10m": [4.0] * n,
                "wind_direction_10m": [200.0] * n,
            }
        }
        body = [one] * len(lats) if len(lats) > 1 else one
        return json.dumps(body).encode()


def test_era5_url() -> None:
    u = hi.era5_url([-31.9], [-71.5], "2010-01-01", "2010-12-31")
    assert (
        u.startswith(hi.ERA5_URL)
        and "models=era5" in u
        and "start_date=2010-01-01" in u
    )
    k = hi.era5_url([-31.9], [-71.5], "a", "b", api_key="SECRET", model="era5_land")
    assert k.startswith(hi.ERA5_CUSTOMER_URL) and "apikey=SECRET" in k
    b = hi.era5_url([-31.9], [-71.5], "a", "b", base_url="http://own/v1/archive")
    assert b.startswith("http://own/v1/archive?")
    with pytest.raises(ValueError, match="ERA5 model"):
        hi.era5_url([0], [0], "a", "b", model="gfs")


def test_parse_era5_single_and_list() -> None:
    raw = json.loads(
        FakeArchive()(hi.era5_url([1.0], [2.0], "2010-01-01", "2010-01-01"))
    )
    t, pts = hi.parse_era5(raw)
    assert len(t) == 24 and len(pts) == 1
    assert pts[0].ghi[1] == pytest.approx(100.0)  # null gap interpolated
    t2, pts2 = hi.parse_era5([raw, raw])
    assert len(pts2) == 2 and t2 == t


def test_era5_points_cached_per_year(tmp_path: Path) -> None:
    get = FakeArchive()
    cache = PoliteCache(tmp_path, get, pause=0.0)
    t, pts = hi.era5_points(
        cache, [(-31.9, -71.5), (-31.8, -71.5)], [2011, 2010], api_key="K"
    )
    assert len(t) == 8760 * 2 and t[0] == datetime(2010, 1, 1)
    assert len(pts) == 2 and pts[1].temperature.size == len(t)
    assert len(get.urls) == 2
    hi.era5_points(
        cache, [(-31.9, -71.5), (-31.8, -71.5)], [2010, 2011], api_key="OTHER"
    )
    assert len(get.urls) == 2  # the key is not part of the cache key


def test_recon_wind(tmp_path: Path) -> None:
    get = FakeExplorador(hours=5)
    client = wx.Explorador(PoliteCache(tmp_path, get, pause=0.0), model="recon")
    calls: list[int] = []
    w = hi.recon_wind(
        client, _spans(), height=16.0, progress=lambda k, n: calls.append(k)
    )
    assert set(w.data) == {"wind_speed", "wind_direction"}
    assert w.data["wind_speed"].shape == (5, 2)
    assert calls and all(
        "SerieHorariaVientoRecon" in u for u in get.calls if "router" in u
    )
    with pytest.raises(ValueError, match="wind only"):
        client.node_series(-31.9, -71.5)


def test_explorador_models_table() -> None:
    assert set(wx.EXPLORADOR_MODELS) == {"wrf2010", "wrf2015", "recon"}
    assert wx.EXPLORADOR_DOWNLOADS["wrf2015"] == "SerieHorariaVientoWrf2015"


def _era5(n: int, t0: float = 20.0, ghi: float = 400.0) -> hi.Era5Point:
    return hi.Era5Point(
        np.full(n, t0), np.full(n, ghi), np.full(n, 5.0), np.full(n, 180.0)
    )


def test_combine_era5_wind_log_law() -> None:
    times = hi.year_hours([2010])[:48]
    w = hi.combine_history(times, _spans(), [_era5(48), _era5(48)], height=16.0)
    ratio = np.log(16 / 0.03) / np.log(10 / 0.03)
    assert w.data["wind_speed"][0, 0] == pytest.approx(5.0 * ratio)
    assert w.data["wind_direction"][0, 1] == pytest.approx(180.0)
    assert set(w.data) == set(wx.FIELDS)


def test_combine_recon_and_calibration() -> None:
    times = hi.year_hours([2010])[:48]
    sp = _spans()
    wind = Weather(
        times + [times[-1] + timedelta(hours=1)],
        sp,
        {"wind_speed": np.full((49, 2), 3.0), "wind_direction": np.full((49, 2), 90.0)},
    )
    calib = Weather(
        times[:24],
        sp,
        {
            "ambient_temperature": np.full((24, 2), 22.0),
            "solar_irradiance": np.full((24, 2), 600.0),
            "wind_speed": np.ones((24, 2)),
            "wind_direction": np.ones((24, 2)),
        },
    )
    w = hi.combine_history(times, sp, [_era5(48), _era5(48)], wind=wind, calib=calib)
    assert w.data["wind_speed"][5, 0] == 3.0 and w.data["wind_direction"][5, 1] == 90.0
    # same month x hour in the second day gets the first day's calibration
    assert w.data["ambient_temperature"][30, 0] == pytest.approx(22.0)
    assert w.data["solar_irradiance"][30, 0] == pytest.approx(600.0)
    short = Weather(times[:10], sp, {k: v[:10] for k, v in wind.data.items()})
    with pytest.raises(ValueError, match="misses"):
        hi.combine_history(times, sp, [_era5(48), _era5(48)], wind=short)
    far = Weather([datetime(1990, 1, 1)], sp, {k: v[:1] for k, v in calib.data.items()})
    with pytest.raises(ValueError, match="overlap"):
        hi.combine_history(times, sp, [_era5(48), _era5(48)], calib=far)
    no_ghi = Weather(times[:24], sp, {"ambient_temperature": np.full((24, 2), 22.0)})
    w2 = hi.combine_history(times, sp, [_era5(48), _era5(48)], calib=no_ghi)
    assert w2.data["solar_irradiance"][0, 0] == pytest.approx(400.0)


def test_month_hour_delta_ratio() -> None:
    keys = np.array([0, 0, 1, 1])
    tgt = np.array([[2.0], [2.0], [5.0], [5.0]])
    src = np.array([[1.0], [1.0], [0.0], [0.0]])
    d = hi.month_hour_delta(tgt, src, keys)
    assert d[0, 0] == 1.0 and d[1, 0] == 5.0 and d[2, 0] == 0.0
    r = hi.month_hour_delta(tgt * 10, src * 10, keys, ratio=True)
    assert r[0, 0] == 2.0 and r[1, 0] == 1.0  # source ~0 -> 1
    r2 = hi.month_hour_delta(tgt * 100, src * 10, keys, ratio=True)
    assert r2[0, 0] == 2.0  # clipped


def test_year_hours_and_parse_years() -> None:
    assert len(hi.year_hours([2004])) == 8784
    assert len(hi.year_hours([2003, 2004])) == 8760 + 8784
    assert hi.parse_years("2008-2010, 2015") == [2008, 2009, 2010, 2015]
    with pytest.raises(ValueError, match="no years"):
        hi.parse_years(" , ")


def test_explorador_wind_helper(tmp_path: Path) -> None:
    client = wx.Explorador(
        PoliteCache(tmp_path, FakeExplorador(hours=3), pause=0.0), "wrf2015"
    )
    t, spd, wdir = wx.explorador_wind(client, -31.9, -71.5)
    assert len(t) == 3 and set(spd) == {10.0, 20.0} and set(wdir) == {10.0, 20.0}
    assert explorador_csv([10], [[1.0]]).startswith(",10")


def test_era5_snap_and_retry(tmp_path: Path) -> None:
    import urllib.error

    archive = FakeArchive()
    state = {"n": 0}

    def flaky(url: str) -> bytes:
        state["n"] += 1
        if state["n"] == 1:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)  # type: ignore[arg-type]
        return archive(url)

    slept: list[float] = []
    cache = PoliteCache(tmp_path, flaky, pause=0.0, sleep=slept.append)
    pts = [(-31.90, -71.47), (-31.88, -71.46), (-31.30, -71.59)]
    t, got = hi.era5_points(cache, pts, [2010], snap=0.25, retries=2)
    assert len(got) == 3 and len(t) == 8760
    assert slept == [60.0]
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(archive.urls[-1]).query)
    assert q["latitude"][0] == "-32.0000,-31.2500"  # two ERA5 cells, not 3 points

    def always(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)  # type: ignore[arg-type]

    with pytest.raises(urllib.error.HTTPError):
        hi.era5_points(
            PoliteCache(tmp_path / "b", always, pause=0.0, sleep=slept.append),
            pts,
            [2011],
            retries=1,
        )

    def boom(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 500, "err", None, None)  # type: ignore[arg-type]

    with pytest.raises(urllib.error.HTTPError):
        hi.era5_points(
            PoliteCache(tmp_path / "c", boom, pause=0.0), pts, [2011], retries=3
        )


def test_era5_ghi_lead(tmp_path: Path) -> None:
    cache = PoliteCache(tmp_path, FakeArchive(), pause=0.0)
    _, raw = hi.era5_points(cache, [(-31.9, -71.5)], [2010], ghi_lead=0)
    _, led = hi.era5_points(cache, [(-31.9, -71.5)], [2010])
    assert led[0].ghi[5] == raw[0].ghi[6]
    assert led[0].ghi[-1] == raw[0].ghi[-1]
