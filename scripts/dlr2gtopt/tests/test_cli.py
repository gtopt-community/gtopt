# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""The dlr2gtopt command line, end to end on synthetic data (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dlr2gtopt import __main__ as cli
from dlr2gtopt import _towers as tw
from dlr2gtopt import _weather as wx
from dlr2gtopt._spans import read_spans, write_towers
from dlr2gtopt.tests._synthetic import make_towers, make_weather
from dlr2gtopt.tests.test_towers import CEN_TEXT_ROWS, FakeGet, osm_reply
from dlr2gtopt.tests.test_weather import om_reply


def run(*argv: Any) -> int:
    return cli.main([str(a) for a in argv])


@pytest.fixture(name="case")
def fixture_case(tmp_path: Path) -> Path:
    """towers.csv, spans.csv and a synthetic weather.csv."""
    write_towers(tmp_path / "towers.csv", make_towers(13))
    assert run("spans", tmp_path / "towers.csv", "-o", tmp_path / "spans.csv") == 0
    spans = read_spans(tmp_path / "spans.csv")
    make_weather(spans, hours=72).write(tmp_path / "weather.csv")
    return tmp_path


def test_pipeline(
    case: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    try:
        import matplotlib  # noqa: F401  # pylint: disable=unused-import
    except ImportError:
        from unittest import mock

        mpl = mock.MagicMock()
        mpl.pyplot.subplots.return_value = (
            mock.MagicMock(),
            (mock.MagicMock(), mock.MagicMock()),
        )
        monkeypatch.setitem(__import__("sys").modules, "matplotlib", mpl)
        monkeypatch.setitem(__import__("sys").modules, "matplotlib.pyplot", mpl.pyplot)
    assert (
        run(
            "weather",
            "--spans",
            case / "spans.csv",
            "--source",
            "csv",
            "--file",
            case / "weather.csv",
            "-o",
            case / "w.parquet",
        )
        == 0
    )
    assert (
        run(
            "ampacity",
            "--spans",
            case / "spans.csv",
            "--weather",
            case / "w.parquet",
            "--conductor",
            "flint",
            "--tmax",
            55,
            "-o",
            case / "amp.parquet",
        )
        == 0
    )
    assert (
        run(
            "critical-spans",
            case / "amp.parquet",
            "--target-rho",
            0.95,
            "--max-spans",
            3,
            "--months",
            "1",
            "--eta-quantile",
            0.05,
            "--segments",
            2,
            "--plot",
            case / "trace.png",
            "-o",
            case / "crit.json",
        )
        == 0
    )
    if (case / "trace.png").exists():  # real matplotlib installed
        assert (case / "trace.png").stat().st_size > 1000
    crit = json.loads((case / "crit.json").read_text(encoding="utf-8"))
    assert 1 <= len(crit["critical_spans"]) <= 3
    assert "eta" in crit and len(crit["segments"]) == 2
    assert all(seg["n"] == len(seg["critical"]) for seg in crit["segments"])
    assert crit["trace"][0]["span"] == crit["critical_spans"][0]["span"]
    assert [p["n"] for p in crit["trace"]] == list(range(1, len(crit["trace"]) + 1))
    assert set(crit["n_needed"]) == {"0.80", "0.85", "0.90", "0.95"}
    assert (
        run(
            "emit",
            "--spans",
            case / "spans.csv",
            "--weather",
            case / "w.parquet",
            "--critical",
            case / "crit.json",
            "--conductor",
            "flint",
            "--tmax",
            55,
            "--voltage",
            220,
            "--block-hours",
            24,
            "--format",
            "csv",
            "-o",
            case / "out",
        )
        == 0
    )
    dlr = json.loads((case / "out" / "dlr_line1.json").read_text(encoding="utf-8"))[
        "dlr"
    ]
    assert len(dlr["critical_spans"]) == len(crit["critical_spans"])
    assert (case / "out" / "Line" / "dlr_ambient_temperature.csv").exists()
    (case / "blocks.csv").write_text("block,hours\n1,24\n2,48\n", encoding="utf-8")
    assert (
        run(
            "emit",
            "--spans",
            case / "spans.csv",
            "--weather",
            case / "weather.csv",
            "--mode",
            "rating",
            "--blocks",
            case / "blocks.csv",
            "--conductor",
            "drake",
            "--subconductors",
            2,
            "-o",
            case / "out2",
        )
        == 0
    )
    assert (case / "out2" / "Line" / "dlr_rating.parquet").exists()
    assert "critical spans" in capsys.readouterr().out


def test_weather_critical_subset_and_amp_csv(case: Path) -> None:
    (case / "c.json").write_text(
        json.dumps({"critical_spans": [{"line": "L1", "span": 2}]}), encoding="utf-8"
    )
    assert (
        run(
            "weather",
            "--spans",
            case / "spans.csv",
            "--critical",
            case / "c.json",
            "--source",
            "csv",
            "--file",
            case / "weather.csv",
            "-o",
            case / "w2.csv",
        )
        == 0
    )
    assert (
        run(
            "ampacity",
            "--spans",
            case / "spans.csv",
            "--weather",
            case / "weather.csv",
            "--diameter",
            0.03,
            "--r25",
            7e-5,
            "--r75",
            8.5e-5,
            "-o",
            case / "a.csv",
        )
        == 0
    )
    assert run("critical-spans", case / "a.csv", "-o", case / "c2.json") == 0
    (case / "none.json").write_text(
        json.dumps({"critical_spans": []}), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="no spans"):
        run(
            "emit",
            "--spans",
            case / "spans.csv",
            "--weather",
            case / "weather.csv",
            "--critical",
            case / "none.json",
            "--conductor",
            "flint",
            "-o",
            case / "o",
        )


def test_weather_sources(case: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def get(url: str) -> bytes:
        n = url.split("latitude=")[1].split("&")[0].count(",") + 1
        return json.dumps(om_reply(n)).encode()

    monkeypatch.setattr(tw, "urllib_get", get)
    cache = ["--cache-dir", case / "cache", "--pause", 0]
    assert (
        run(
            "weather",
            "--spans",
            case / "spans.csv",
            "--source",
            "open-meteo",
            "--model",
            "ecmwf_ifs025",
            *cache,
            "-o",
            case / "om.parquet",
        )
        == 0
    )
    with pytest.raises(SystemExit, match="grib"):
        run(
            "weather",
            "--spans",
            case / "spans.csv",
            "--source",
            "ecmwf-opendata",
            "-o",
            case / "x.parquet",
        )
    with pytest.raises(SystemExit, match="--file"):
        run(
            "weather",
            "--spans",
            case / "spans.csv",
            "--source",
            "csv",
            "-o",
            case / "x.csv",
        )
    with pytest.raises(SystemExit, match="era5"):
        run(
            "weather",
            "--spans",
            case / "spans.csv",
            "--source",
            "era5",
            "-o",
            case / "x.csv",
        )

    from dlr2gtopt.tests.test_weather import ecmwf_fields

    def fields(_p: Path, **_k: Any) -> dict[str, wx.GridField]:
        f = ecmwf_fields()
        lats = f["2t"][1] + 0.0
        return {k: (v[0], lats, v[2] + 0.0, v[3]) for k, v in f.items()}

    monkeypatch.setattr(wx, "read_grib", fields)
    assert (
        run(
            "weather",
            "--spans",
            case / "spans.csv",
            "--source",
            "ecmwf-opendata",
            "--grib",
            case / "x.grib2",
            "-o",
            case / "ec.csv",
        )
        == 0
    )


def test_weather_explorador(case: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dlr2gtopt.tests.test_weather import FakeExplorador

    monkeypatch.setattr(tw, "urllib_get", FakeExplorador(hours=4))
    assert (
        run(
            "-v",
            "weather",
            "--spans",
            case / "spans.csv",
            "--source",
            "explorador-eolico",
            "--interp",
            "nearest",
            "--wind-profile",
            "power",
            "--cache-dir",
            case / "c",
            "--pause",
            0,
            "-o",
            case / "ex.parquet",
        )
        == 0
    )


def test_fetch_ecmwf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    got: dict[str, Any] = {}

    def fake(target: Path, **kw: Any) -> Path:
        got.update(kw)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"GRIB")
        return target

    monkeypatch.setattr(wx, "ecmwf_fetch", fake)
    assert run("fetch-ecmwf", "--archive", tmp_path, "--max-step", 6, "--step", 3) == 0
    assert got["steps"] == [0, 3, 6]
    assert run("fetch-ecmwf", "--archive", tmp_path, "--max-step", 6) == 0  # exists


def test_towers_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "s.txt").write_text(CEN_TEXT_ROWS, encoding="utf-8")
    monkeypatch.setattr(tw, "pdf_to_text", lambda _p: CEN_TEXT_ROWS)
    (tmp_path / "s.pdf").write_bytes(b"%PDF")
    assert (
        run(
            "towers",
            "--source",
            "cen",
            "--line",
            "AB",
            "--file",
            tmp_path / "s.pdf",
            "--circuit",
            "C1",
            "-o",
            tmp_path / "t1.csv",
        )
        == 0
    )
    docs = [
        {
            "id": 9,
            "nombre": "Listado de distribucion de estructuras",
            "extension": ".pdf",
        }
    ]
    get = FakeGet(
        {
            "search=one": json.dumps([{"id": 5, "nombre": "A-B"}]).encode(),
            "search=two": json.dumps(
                [{"id": 5, "nombre": "A-B"}, {"id": 6, "nombre": "A-C"}]
            ).encode(),
            "documentos/9/": b"%PDF",
            "6/documentos/": b"[]",
            "/documentos/": json.dumps(docs).encode(),
            "interpreter": json.dumps(osm_reply()).encode(),
        }
    )
    monkeypatch.setattr(tw, "urllib_get", get)
    net = ["--cache-dir", tmp_path / "c", "--pause", 0]
    assert (
        run(
            "towers",
            "--source",
            "cen",
            "--line",
            "AB",
            "--search",
            "one",
            *net,
            "-o",
            tmp_path / "t2.csv",
        )
        == 0
    )
    assert (
        run(
            "towers",
            "--source",
            "cen",
            "--line",
            "AB",
            "--search",
            "two",
            *net,
            "-o",
            tmp_path / "t3.csv",
        )
        == 1
    )
    assert (
        run(
            "towers",
            "--source",
            "cen",
            "--line",
            "AB",
            "--section-id",
            5,
            "--doc-id",
            3,
            *net,
            "-o",
            tmp_path / "t4.csv",
        )
        == 1
    )
    assert (
        run(
            "towers",
            "--source",
            "cen",
            "--line",
            "AB",
            "--section-id",
            6,
            *net,
            "-o",
            tmp_path / "t4.csv",
        )
        == 1
    )
    with pytest.raises(SystemExit, match="section-id"):
        run("towers", "--source", "cen", "--line", "AB", "-o", tmp_path / "t.csv")
    assert (
        run(
            "towers",
            "--source",
            "osm",
            "--line",
            "AB",
            "--way-id",
            1,
            "--way-id",
            2,
            *net,
            "-o",
            tmp_path / "t5.csv",
        )
        == 0
    )
    assert (
        run(
            "towers",
            "--source",
            "osm",
            "--line",
            "AB",
            "--bbox",
            -32,
            -72,
            -31,
            -71,
            "--towers-only",
            *net,
            "-o",
            tmp_path / "t6.csv",
        )
        == 0
    )
    (tmp_path / "m.kml").write_text(
        "<kml><Placemark><LineString><coordinates>-71.5,-31.9 -71.5,-31.896"
        "</coordinates></LineString></Placemark></kml>",
        encoding="utf-8",
    )
    assert (
        run(
            "towers",
            "--source",
            "manual",
            "--line",
            "AB",
            "--file",
            tmp_path / "m.kml",
            "-o",
            tmp_path / "t7.csv",
        )
        == 0
    )
    assert (
        run(
            "towers",
            "--source",
            "manual",
            "--line",
            "AB",
            "--file",
            tmp_path / "t7.csv",
            "-o",
            tmp_path / "t8.csv",
        )
        == 0
    )
    with pytest.raises(SystemExit, match="--file"):
        run("towers", "--source", "manual", "--line", "AB", "-o", tmp_path / "t.csv")


def test_errors_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("spans", tmp_path / "missing.csv", "-o", tmp_path / "s.csv") == 2
    assert "error" in capsys.readouterr().err
