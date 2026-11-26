# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Grids, interpolation and weather sources (recorded/synthetic replies)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from dlr2gtopt import _weather as wx
from dlr2gtopt._spans import Span
from dlr2gtopt._towers import PoliteCache
from dlr2gtopt.tests._synthetic import explorador_csv, make_spans, make_weather


def test_bilinear_is_exact_on_linear_fields() -> None:
    grid = wx.RegularGrid(0.01, 0.01)

    def f(la: float, lo: float) -> float:
        return 3.0 + 2.0 * la - 5.0 * lo

    for la, lo in [(-31.9037, -71.4712), (-31.25, -71.33), (-31.2999, -71.0001)]:
        st = grid.stencil(la, lo, "bilinear")
        assert sum(w for _, w in st) == pytest.approx(1.0)
        val = sum(w * f(*grid.node_coords(n)) for n, w in st)
        assert val == pytest.approx(f(la, lo), abs=1e-9)


def test_nearest_and_errors() -> None:
    grid = wx.RegularGrid(0.25, 0.25, 90.0, 0.0)
    assert grid.stencil(89.9, 0.1, "nearest") == [((0, 0), 1.0)]
    with pytest.raises(ValueError, match="interpolation"):
        grid.stencil(0, 0, "cubic")


def test_nodes_for_dedupes() -> None:
    grid = wx.RegularGrid(0.01, 0.01)
    pts = [(-31.9031, -71.4711), (-31.9032, -71.4712), (-31.9131, -71.4711)]
    nodes = grid.nodes_for(pts)
    assert len(nodes) == 6
    assert len(grid.nodes_for(pts, "nearest")) == 2


def test_uv_round_trip_and_wrap() -> None:
    d = np.array([0.0, 90.0, 180.0, 270.0, 359.0])
    u, v = wx.uv_from_dir(np.ones(5), d)
    assert u[1] == pytest.approx(-1.0)  # wind from the east blows west
    assert wx.dir_from_uv(u, v) == pytest.approx(d)
    # averaging 350 and 10 deg through u/v gives 0, not 180
    u2, v2 = wx.uv_from_dir(np.ones(2), np.array([350.0, 10.0]))
    assert float(
        np.mod(
            wx.dir_from_uv(u2.mean(keepdims=True), v2.mean(keepdims=True))[0] + 180, 360
        )
        - 180
    ) == pytest.approx(0.0, abs=1e-9)


def test_vertical_profiles() -> None:
    lv = {10.0: np.array([4.0]), 20.0: np.array([5.0])}
    log = wx.vertical_wind(lv, 15.0, "log")
    assert float(log[0]) == pytest.approx(4.0 + np.log(1.5) / np.log(2.0))
    pw = wx.vertical_wind(lv, 20.0, "power")
    assert float(pw[0]) == pytest.approx(5.0)
    assert float(wx.vertical_wind(lv, 16.0, "none")[0]) == pytest.approx(5.0)
    assert float(wx.vertical_wind({10.0: np.array([3.0])}, 15.0)[0]) == pytest.approx(
        3.0
    )
    calm = wx.vertical_wind(
        {10.0: np.array([0.0]), 20.0: np.array([1.0])}, 15.0, "power"
    )
    assert float(calm[0]) == 0.0
    assert (
        float(wx.vertical_wind({10.0: np.array([5.0]), 20.0: np.array([0.0])}, 30.0)[0])
        == 0.0
    )
    with pytest.raises(ValueError, match="profile"):
        wx.vertical_wind(lv, 15.0, "cubic")
    t = wx.vertical_linear({2.0: np.array([20.0]), 80.0: np.array([19.22])}, 15.0)
    assert float(t[0]) == pytest.approx(20.0 - 0.01 * 13)
    assert float(wx.vertical_linear({2.0: np.array([20.0])}, 15.0)[0]) == 20.0


def test_parse_explorador_csv_gaps_and_utc() -> None:
    text = explorador_csv([10, 20], [[1.0, 2.0], [None, 4.0], [3.0, None]])
    times, vals = wx.parse_explorador_csv(text)
    assert times[0] == datetime(2010, 1, 1, 4)
    assert vals[10.0].tolist() == pytest.approx([1.0, 2.0, 3.0])
    assert vals[20.0].tolist() == pytest.approx([2.0, 4.0, 4.0])
    with pytest.raises(ValueError, match="no data"):
        wx.fill_gaps(np.array([np.nan]))


class FakeExplorador:
    """Recorded-response getter for the Explorador's two-step download."""

    def __init__(self, hours: int = 6) -> None:
        self.hours = hours
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if "/python-router/" in url:
            q = json.loads(
                __import__("urllib.parse").parse.unquote(
                    url.split("/python-router/")[1]
                )
            )
            d = q["datos"]
            name = f"{d['lat']}_{d['lon']}_{d['variable']}_{'-'.join(map(str, d['alturas']))}"
            return json.dumps({"_serverData": name + ".csv"}).encode()
        name = url.rsplit("/", 1)[1][: -len(".csv")]
        lat_s, lon_s, var, hs = name.split("_")
        lat, lon = float(lat_s), float(lon_s)
        heights = [float(h) for h in hs.split("-")]
        base = {
            "vel": 5.0 + 100 * (lat + 31.9) + 50 * (lon + 71.5),
            "vdir": 350.0 if lon < -71.5 else 10.0,
            "temp": 15.0 + 100 * (lat + 31.9),
            "glb": 500.0,
        }[var]
        rows = [
            [base + (0.5 * (h - 10) if var == "vel" else 0.0) for h in heights]
            for _ in range(self.hours)
        ]
        return explorador_csv(heights, rows).encode()


def test_explorador_weather_bilinear(tmp_path: Path) -> None:
    get = FakeExplorador()
    client = wx.Explorador(PoliteCache(tmp_path, get, pause=0.0))
    spans = [Span("L", 1, -31.8975, -71.4975, 0.0, 300.0, 0.15)]
    w = wx.explorador_weather(
        client, spans, method="bilinear", height=15.0, progress=lambda k, n: None
    )
    assert len(w.times) == 6
    ta = w.data["ambient_temperature"][0, 0]
    assert ta == pytest.approx(15.0 + 100 * (-31.8975 + 31.9), abs=1e-6)
    spd = w.data["wind_speed"][0, 0]
    assert spd == pytest.approx(
        5.0 + 100 * 0.0025 + 50 * 0.0025 + 2.5 * np.log(1.5) / np.log(2.0) * 2, abs=1e-6
    )
    assert w.data["solar_irradiance"][0, 0] == pytest.approx(500.0)
    n_calls = len(get.calls)
    assert n_calls == 4 * 4 * 2  # 4 nodes x 4 variables x (router + file)
    wx.explorador_weather(client, spans)
    assert len(get.calls) == n_calls  # cached
    with pytest.raises(ValueError, match="model"):
        wx.Explorador(PoliteCache(tmp_path), model="era5")


def test_explorador_direction_across_north(tmp_path: Path) -> None:
    client = wx.Explorador(PoliteCache(tmp_path, FakeExplorador(), pause=0.0))
    spans = [Span("L", 1, -31.8975, -71.505, 0.0, 300.0, 0.15)]
    w = wx.explorador_weather(client, spans, method="bilinear")
    d = float(w.data["wind_direction"][0, 0])
    assert (
        min(d, 360.0 - d) < 1.0
    )  # ~0 (speeds differ slightly), never ~180  # between 350 and 10, never ~180


def om_reply(n_points: int, hours: int = 4) -> Any:
    """Open-Meteo reply shape (recorded layout, made-up values)."""
    t0 = datetime(2026, 11, 25)
    times = [(t0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    one = {
        "latitude": -31.9,
        "longitude": -71.5,
        "hourly": {
            "time": times,
            "temperature_2m": [20.0] * hours,
            "temperature_80m": [19.0] * hours,
            "wind_speed_10m": [3.0, None, 3.0, 3.0][:hours],
            "wind_speed_80m": [6.0] * hours,
            "wind_direction_10m": [180.0] * hours,
            "wind_direction_80m": [200.0] * hours,
            "shortwave_radiation": [0.0, 100.0, 400.0, 800.0][:hours],
        },
    }
    return one if n_points == 1 else [one] * n_points


def test_open_meteo_url_options() -> None:
    url = wx.open_meteo_url([-31.9, -31.8], [-71.5, -71.4], model="ecmwf_ifs025")
    assert url.startswith(wx.OPEN_METEO_URL)
    assert "latitude=-31.90000,-31.80000" in url and "models=ecmwf_ifs025" in url
    paid = wx.open_meteo_url([-31.9], [-71.5], api_key="K")
    assert paid.startswith(wx.OPEN_METEO_CUSTOMER_URL) and "apikey=K" in paid
    own = wx.open_meteo_url(
        [-31.9], [-71.5], base_url="http://localhost:8080/v1/forecast"
    )
    assert own.startswith("http://localhost:8080")


def test_open_meteo_weather_batches(tmp_path: Path) -> None:
    calls: list[str] = []

    def get(url: str) -> bytes:
        calls.append(url)
        n = url.split("latitude=")[1].split("&")[0].count(",") + 1
        return json.dumps(om_reply(n)).encode()

    spans = make_spans(6)
    w = wx.open_meteo_weather(
        PoliteCache(tmp_path, get, pause=0.0), spans, batch=2, height=15.0
    )
    assert len(calls) == 3
    assert w.data["wind_speed"].shape == (4, 5)
    assert w.data["wind_speed"][1, 0] == pytest.approx(
        3.0 + 3.0 * np.log(1.5) / np.log(8.0)
    )
    assert w.data["ambient_temperature"][0, 0] == pytest.approx(20.0 - 13.0 / 78.0)
    assert w.data["solar_irradiance"][3, 4] == pytest.approx(800.0)
    assert w.spans[0].lat == spans[0].lat
    _, single = wx.parse_open_meteo(om_reply(1), 15.0)
    assert len(single) == 1


def test_open_meteo_archive_url_and_snap(tmp_path: Path) -> None:
    url = wx.open_meteo_url(
        [-31.9], [-71.5], model="era5", start_date="2024-01-01", end_date="2025-12-31"
    )
    assert url.startswith(wx.OPEN_METEO_ARCHIVE_URL)
    assert "start_date=2024-01-01" in url and "end_date=2025-12-31" in url
    assert "wind_speed_100m" in url and "forecast_days" not in url
    paid = wx.open_meteo_url([-31.9], [-71.5], api_key="K", start_date="2024-01-01")
    assert paid.startswith(wx.OPEN_METEO_CUSTOMER_ARCHIVE_URL)
    assert "end_date=2024-01-01" in paid

    reply = om_reply(1)
    h = reply["hourly"]
    for old, new in (
        ("wind_speed_80m", "wind_speed_100m"),
        ("wind_direction_80m", "wind_direction_100m"),
    ):
        h[new] = h.pop(old)
    del h["temperature_80m"]
    calls: list[str] = []

    def get(url: str) -> bytes:
        calls.append(url)
        n = url.split("latitude=")[1].split("&")[0].count(",") + 1
        return json.dumps(reply if n == 1 else [reply] * n).encode()

    spans = make_spans(6)  # all within one 0.25 deg cell
    w = wx.open_meteo_weather(
        PoliteCache(tmp_path, get, pause=0.0),
        spans,
        snap=0.25,
        height=15.0,
        model="era5",
        start_date="2024-01-01",
    )
    assert len(calls) == 1 and "latitude=-32.00000&" in calls[0] + "&"
    assert w.data["wind_speed"].shape == (4, 5)
    assert w.data["ambient_temperature"][0, 0] == pytest.approx(20.0)  # 2 m only
    assert w.data["wind_speed"][0, 0] == pytest.approx(
        3.0 + 3.0 * np.log(1.5) / np.log(10.0)
    )


def ecmwf_fields(hours: int = 3) -> dict[str, wx.GridField]:
    """Synthetic 0.25 deg fields (descending latitudes, like IFS)."""
    t0 = datetime(2026, 11, 25)
    times = [t0 + timedelta(hours=3 * k) for k in range(hours)]
    lats = np.array([-31.5, -31.75, -32.0, -32.25])
    lons = np.array([-72.0, -71.75, -71.5, -71.25])
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    shape = (hours, 4, 4)
    t2 = np.broadcast_to(288.15 + 4.0 * (la + 32.0) + 2.0 * (lo + 71.5), shape).copy()
    u = np.full(shape, -3.0)
    v = np.zeros(shape)
    acc = np.stack([np.full((4, 4), k * 3 * 3600 * 200.0) for k in range(hours)])
    return {
        "10u": (times, lats, lons, u),
        "10v": (times, lats, lons, v),
        "100u": (times, lats, lons, 2 * u),
        "100v": (times, lats, lons, v),
        "2t": (times, lats, lons, t2),
        "ssrd": (times, lats, lons, acc),
    }


def test_ecmwf_weather_bilinear() -> None:
    spans = [Span("L", 1, -31.9, -71.6, 0.0, 300.0, 0.15)]
    w = wx.ecmwf_weather(ecmwf_fields(), spans, height=15.0)
    assert w.data["ambient_temperature"][0, 0] == pytest.approx(
        15.0 + 0.4 - 0.2, abs=1e-9
    )
    assert w.data["wind_direction"][0, 0] == pytest.approx(90.0)  # u<0: from the east
    assert w.data["wind_speed"][0, 0] == pytest.approx(
        3.0 + 3.0 * np.log(1.5) / np.log(10.0)
    )
    assert w.data["solar_irradiance"][1, 0] == pytest.approx(200.0)


def test_ecmwf_without_ssrd_and_errors(caplog: pytest.LogCaptureFixture) -> None:
    f = ecmwf_fields()
    del f["ssrd"], f["100u"], f["100v"]
    spans = [Span("L", 1, -31.9, -71.6, 0.0, 300.0, 0.15)]
    with caplog.at_level(logging.WARNING, logger="dlr2gtopt"):
        w = wx.ecmwf_weather(f, spans)
    assert "solar_irradiance" not in w.data
    assert "ssrd" in caplog.text
    del f["2t"]
    with pytest.raises(ValueError, match="2t"):
        wx.ecmwf_weather(f, spans)


def test_deaccumulate() -> None:
    t = [datetime(2026, 1, 1, h) for h in (0, 1, 3)]
    acc = np.array([0.0, 3600.0 * 100, 3600.0 * 500])
    assert wx.deaccumulate(t, acc).tolist() == pytest.approx([100.0, 100.0, 200.0])


class FakeDA:
    def __init__(self, vals: np.ndarray, times: list[datetime]) -> None:
        self.values = vals
        self.shape = vals.shape
        self._c = {
            "valid_time": type(
                "C", (), {"values": np.array(times, dtype="datetime64[s]")}
            )(),
            "latitude": type("C", (), {"values": np.array([-31.75, -32.0])})(),
            "longitude": type("C", (), {"values": np.array([-71.75, -71.5])})(),
        }

    def __getitem__(self, k: str) -> Any:
        return self._c[k]


class FakeDS:
    def __init__(self, das: dict[str, FakeDA]) -> None:
        self.data_vars = list(das)
        self._d = das

    def __getitem__(self, k: str) -> FakeDA:
        return self._d[k]


def test_read_grib_with_mocked_reader(tmp_path: Path) -> None:
    times = [datetime(2026, 11, 25, 0), datetime(2026, 11, 25, 3)]
    vals = np.arange(8.0).reshape(2, 2, 2)
    ds = FakeDS(
        {
            "u10": FakeDA(vals, times),
            "t2m": FakeDA(vals + 280, times),
            "msl": FakeDA(vals, times),
        }
    )
    out = wx.read_grib(tmp_path / "x.grib2", open_datasets=lambda _p: [ds])
    assert sorted(out) == ["10u", "2t"]
    assert out["2t"][0] == times
    assert out["10u"][3].shape == (2, 2, 2)


def test_read_grib_needs_cfgrib(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "cfgrib", None)
    with pytest.raises(RuntimeError, match="cfgrib"):
        wx.read_grib(tmp_path / "x.grib2")


def test_ecmwf_fetch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    class Client:
        def __init__(self, **kw: Any) -> None:
            seen["init"] = kw

        def retrieve(self, **kw: Any) -> None:
            seen["retrieve"] = kw
            Path(kw["target"]).write_bytes(b"GRIB")

    out = wx.ecmwf_fetch(
        tmp_path / "a" / "x.grib2", client_factory=Client, steps=[0, 3]
    )
    assert out.read_bytes() == b"GRIB"
    assert seen["init"] == {"source": "ecmwf", "model": "ifs"}
    assert seen["retrieve"]["step"] == [0, 3]
    import sys

    monkeypatch.setitem(sys.modules, "ecmwf.opendata", None)
    monkeypatch.setitem(sys.modules, "ecmwf", None)
    with pytest.raises(RuntimeError, match="ecmwf-opendata"):
        wx.ecmwf_fetch(tmp_path / "y.grib2")


def test_weather_io_and_select(tmp_path: Path) -> None:
    spans = make_spans(5)
    w = make_weather(spans, hours=6)
    for name in ("w.parquet", "w.csv"):
        w.write(tmp_path / name)
        back = wx.read_weather(tmp_path / name, spans)
        assert back.data["wind_speed"] == pytest.approx(w.data["wind_speed"], abs=1e-3)
        assert back.times[0] == w.times[0]
    sub = w.select([1, 3])
    assert sub.data["ambient_temperature"].shape == (6, 2)
    with pytest.raises(ValueError, match="shape"):
        wx.Weather(w.times, spans, {"wind_speed": np.zeros((2, 2))})
    (tmp_path / "noline.csv").write_text(
        "time,span,ambient_temperature\n2010-01-01 00:00:00,1,20\n", encoding="utf-8"
    )
    nl = wx.read_weather(tmp_path / "noline.csv", spans[:1])
    assert nl.data["ambient_temperature"][0, 0] == pytest.approx(20.0)


def test_correction_hook() -> None:
    w = make_weather(make_spans(3), hours=2)
    assert wx.apply_correction(w) is w
    with pytest.raises(ValueError, match="correction"):
        wx.apply_correction(w, "mos")


def test_interpolate_without_ghi(caplog: pytest.LogCaptureFixture) -> None:
    ns = wx.NodeSeries(
        {10.0: np.ones(2)}, {10.0: np.zeros(2)}, {10.0: np.ones(2)}, None
    )
    grid = wx.RegularGrid(1.0, 1.0)
    sp = [Span("L", 1, 0.0, 0.0, 0.0, 1.0, 0.0)]
    with caplog.at_level(logging.WARNING, logger="dlr2gtopt"):
        w = wx.interpolate_spans(
            grid, sp, {(0, 0): ns}, [datetime(2026, 1, 1)] * 2, method="nearest"
        )
    assert "solar_irradiance" not in w.data


def test_interpolate_per_span_heights() -> None:
    """``span_heights`` uses each span's own height; blanks use the scalar."""
    ns = wx.NodeSeries(
        {10.0: np.full(2, 4.0), 80.0: np.full(2, 8.0)},
        {10.0: np.zeros(2), 80.0: np.zeros(2)},
        {2.0: np.full(2, 20.0), 80.0: np.full(2, 12.2)},
        np.zeros(2),
    )
    grid = wx.RegularGrid(1.0, 1.0)
    sp = [
        Span("L", 1, 0.0, 0.0, 0.0, 1.0, 0.0, height=30.0),
        Span("L", 2, 0.0, 0.0, 0.0, 1.0, 0.0),
        Span("L", 3, 0.0, 0.0, 0.0, 1.0, 0.0, height=10.0),
    ]
    t = [datetime(2026, 1, 1)] * 2
    kw: dict[str, Any] = {"method": "nearest", "height": 20.0, "profile": "power"}
    one = wx.interpolate_spans(grid, sp, {(0, 0): ns}, t, **kw)
    per = wx.interpolate_spans(grid, sp, {(0, 0): ns}, t, span_heights=True, **kw)
    ws1, ws2 = one.data["wind_speed"], per.data["wind_speed"]
    # default: every span at the scalar height
    assert ws1[0, 0] == pytest.approx(ws1[0, 1]) == pytest.approx(ws1[0, 2])
    # per-span: higher span sees more wind, blank span keeps the scalar
    assert ws2[0, 0] > ws2[0, 1] > ws2[0, 2]
    assert ws2[0, 1] == pytest.approx(ws1[0, 1])
    assert ws2[0, 2] == pytest.approx(4.0)
    ta = per.data["ambient_temperature"]
    assert ta[0, 2] == pytest.approx(20.0 - 7.8 * 8 / 78)
