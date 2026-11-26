# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI: history, typical-year, to-blocks, repeat (synthetic data, no network)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dlr2gtopt import _towers as tw
from dlr2gtopt import _typical as ty
from dlr2gtopt.__main__ import main
from dlr2gtopt._spans import write_spans
from dlr2gtopt._weather import Weather
from dlr2gtopt.tests._typical_synth import history, spans3
from dlr2gtopt.tests.test_history import FakeArchive
from dlr2gtopt.tests.test_weather import FakeExplorador

COND = ["--conductor", "flint", "--tmax", "55"]

#: ``--plot`` needs matplotlib (the ``plot`` extra, not in ``dev``).
HAVE_MPL = importlib.util.find_spec("matplotlib") is not None
PLOT = ["--plot"] if HAVE_MPL else []


@pytest.fixture(scope="module")
def case_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("cli")
    write_spans(d / "spans.csv", spans3())
    history((2001, 2002, 2003)).write(d / "history.parquet")
    (d / "critical.json").write_text(
        json.dumps({"critical_spans": [{"line": "L", "span": s} for s in (1, 2, 3)]})
    )
    rc = main(
        [
            "typical-year",
            "--history",
            str(d / "history.parquet"),
            "--spans",
            str(d / "spans.csv"),
            "--critical",
            str(d / "critical.json"),
            *COND,
            "--utc-offset",
            "-4",
            "--test-years",
            "2003",
            "--static-mw",
            "250",
            *PLOT,
            "-o",
            str(d / "typ"),
        ]
    )
    assert rc == 0
    return d


def test_typical_year_cli(case_dir: Path) -> None:
    out = case_dir / "typ"
    for name in (
        "typical_mean.parquet",
        "typical_p90.parquet",
        "rating_stats_month_hour.parquet",
        "typical_report.json",
        *(("heatmap_p90_month_hour.png", "duration_curves.png") if HAVE_MPL else ()),
    ):
        assert (out / name).exists(), name
    rep = json.loads((out / "typical_report.json").read_text())
    assert rep["fit_years"] == [2001, 2002] and rep["test_years"] == [2003]
    assert rep["mean"]["method"] == "tmy" and rep["p90"]["method"] == "analogue"
    assert rep["p90"]["exceedance"] == pytest.approx(0.9, abs=0.03)
    ho = rep["p90"]["holdout"]
    assert ho["p_over"] < rep["mean"]["holdout"]["p_over"]
    assert "static" in ho


@pytest.mark.parametrize(
    "extra",
    [
        ["--case", "quantile:0.2", "--p90-method", "month", "--blend-hours", "0"],
        ["--case", "mean", "--mean-method", "analogue", "--selection", "rotate"],
        [
            "--case",
            "p95",
            "--selection",
            "knn-mean",
            "--analogues",
            "3",
            "--years",
            "2001-2002",
        ],
        ["--case", "mean", "--fs-weights", "rating_mean=1", "--tmy-candidates", "1"],
    ],
)
def test_typical_year_options(case_dir: Path, tmp_path: Path, extra: list[str]) -> None:
    rc = main(
        [
            "typical-year",
            "--history",
            str(case_dir / "history.parquet"),
            "--spans",
            str(case_dir / "spans.csv"),
            *COND,
            "--format",
            "csv",
            *extra,
            "-o",
            str(tmp_path),
        ]
    )
    assert rc == 0
    assert len(list(tmp_path.glob("typical_*.csv"))) == 1


def test_typical_year_no_spans(case_dir: Path, tmp_path: Path) -> None:
    crit = tmp_path / "c.json"
    crit.write_text(json.dumps({"critical_spans": [{"line": "X", "span": 9}]}))
    with pytest.raises(SystemExit):
        main(
            [
                "typical-year",
                "--history",
                str(case_dir / "history.parquet"),
                "--spans",
                str(case_dir / "spans.csv"),
                "--critical",
                str(crit),
                *COND,
                "-o",
                str(tmp_path),
            ]
        )


def _map(path: Path) -> None:
    rows = []
    for m, nd, b0 in ((1, 31, 1), (2, 28, 3)):
        for d in range(1, nd + 1):
            for hr in range(1, 25):
                rows.append((2030, m, d, hr, b0 if 19 <= hr <= 23 else b0 + 1))
    pd.DataFrame(rows, columns=["year", "month", "day", "hour", "block"]).to_parquet(
        path
    )


def test_to_blocks_block_map(case_dir: Path, tmp_path: Path) -> None:
    _map(tmp_path / "bhm.parquet")
    planning = {
        "simulation": {
            "block_array": [{"uid": u, "duration": 1} for u in (1, 2, 3, 4)],
            "stage_array": [
                {"uid": 1, "first_block": 0, "count_block": 2},
                {"uid": 2, "first_block": 2, "count_block": 2},
            ],
        }
    }
    (tmp_path / "case.json").write_text(json.dumps(planning))
    rc = main(
        [
            "to-blocks",
            str(case_dir / "typ" / "typical_p90.parquet"),
            "--spans",
            str(case_dir / "spans.csv"),
            *COND,
            "--block-map",
            str(tmp_path / "bhm.parquet"),
            "--planning",
            str(tmp_path / "case.json"),
            "--mode",
            "both",
            "--tmax-profile",
            "--scenarios",
            "1,2",
            "-o",
            str(tmp_path / "out"),
        ]
    )
    assert rc == 0
    df = pd.read_parquet(tmp_path / "out" / "Line" / "dlr_wind_speed.parquet")
    assert set(df["block"]) == {1, 2, 3, 4} and set(df["scenario"]) == {1, 2}
    assert (tmp_path / "out" / "Line" / "dlr_rating.parquet").exists()
    js = json.loads((tmp_path / "out" / "dlr_line1.json").read_text())
    assert (
        js["dlr"]["critical_spans"][0]["ambient_temperature"]
        == "dlr_ambient_temperature"
    )
    blocks = pd.read_parquet(tmp_path / "out" / "dlr_blocks.parquet")
    assert set(blocks["stage"]) == {1, 2}


def test_to_blocks_bands_and_repeat(case_dir: Path, tmp_path: Path) -> None:
    t = str(case_dir / "typ" / "typical_mean.parquet")
    sp = ["--spans", str(case_dir / "spans.csv"), *COND]
    rc = main(
        [
            "to-blocks",
            t,
            *sp,
            "--hour-bands",
            "1-7,8-18,19-24",
            "--block-stat",
            "p90",
            "--format",
            "csv",
            "-o",
            str(tmp_path / "tpl"),
        ]
    )
    assert rc == 0
    rc = main(
        [
            "repeat",
            str(tmp_path / "tpl" / "dlr_blocks.csv"),
            *sp,
            "--first-month",
            "4",
            "--stages",
            "240",
            "--mode",
            "rating",
            "-o",
            str(tmp_path / "h20"),
        ]
    )
    assert rc == 0
    df = pd.read_parquet(tmp_path / "h20" / "Line" / "dlr_rating.parquet")
    assert df["stage"].nunique() == 240 and df["block"].max() == 720
    pd.DataFrame({"stage": [1, 2], "month": [1, 2]}).to_csv(
        tmp_path / "sm.csv", index=False
    )
    rc = main(
        [
            "to-blocks",
            t,
            *sp,
            "--stage-months",
            str(tmp_path / "sm.csv"),
            "--block-hours",
            "24",
            "--selection",
            "knn-mean",
            "-o",
            str(tmp_path / "chrono"),
        ]
    )
    assert rc == 0
    df = pd.read_parquet(tmp_path / "chrono" / "Line" / "dlr_wind_speed.parquet")
    assert df["block"].nunique() == 31 + 28


def test_writer_missing_span(case_dir: Path, tmp_path: Path) -> None:
    write_spans(tmp_path / "s.csv", spans3()[:1])
    with pytest.raises(SystemExit, match="lacks"):
        main(
            [
                "to-blocks",
                str(case_dir / "typ" / "typical_p90.parquet"),
                "--spans",
                str(tmp_path / "s.csv"),
                *COND,
                "--hour-bands",
                "1-24",
                "-o",
                str(tmp_path),
            ]
        )


class FakeServers:
    """Explorador recon + Open-Meteo archive in one getter."""

    def __init__(self) -> None:
        self.archive = FakeArchive()
        self.explorador = FakeExplorador(hours=8784)

    def __call__(self, url: str) -> bytes:
        if "archive" in url:
            return self.archive(url)
        return self.explorador(url)


def test_history_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dlr2gtopt.tests.test_history import _spans

    sp = _spans()
    write_spans(tmp_path / "spans.csv", sp)
    calib = Weather(
        list(history((2010,), spans=sp).times[:48]),
        sp,
        {
            "ambient_temperature": np.full((48, 2), 25.0),
            "solar_irradiance": np.full((48, 2), 500.0),
        },
    )
    calib.write(tmp_path / "wrf.parquet")
    monkeypatch.setattr(tw, "urllib_get", FakeServers())
    base = [
        "history",
        "--spans",
        str(tmp_path / "spans.csv"),
        "--years",
        "2010",
        "--cache-dir",
        str(tmp_path / "cache"),
        "--pause",
        "0",
    ]
    rc = main(
        [
            *base,
            "--calibrate",
            "wrf",
            "--calib-weather",
            str(tmp_path / "wrf.parquet"),
            "--height",
            "16",
            "-o",
            str(tmp_path / "h.parquet"),
        ]
    )
    assert rc == 0
    df = pd.read_parquet(tmp_path / "h.parquet")
    assert df["time"].nunique() == 8760 - 4  # recon starts at local midnight
    rc = main([*base, "--wind", "era5", "-o", str(tmp_path / "h2.csv")])
    assert rc == 0
    assert pd.read_csv(tmp_path / "h2.csv")["time"].nunique() == 8760
    with pytest.raises(SystemExit, match="calib-weather"):
        main([*base, "--calibrate", "wrf", "-o", str(tmp_path / "x.csv")])
    crit = tmp_path / "c.json"
    crit.write_text(json.dumps({"critical_spans": [{"line": "Z", "span": 1}]}))
    with pytest.raises(SystemExit, match="no spans"):
        main([*base, "--critical", str(crit), "-o", str(tmp_path / "x.csv")])


def test_case_label_from_filename(case_dir: Path) -> None:
    assert ty.parse_case(Path("typical_p90.parquet").stem.split("_", 1)[-1]).q == 0.1


def test_to_blocks_from_history_and_overlap(case_dir: Path, tmp_path: Path) -> None:
    _map(tmp_path / "bhm.parquet")
    pd.read_parquet(tmp_path / "bhm.parquet").assign(
        stage=lambda d: d["month"]
    ).to_parquet(tmp_path / "bhm_s.parquet")
    rc = main(
        [
            "to-blocks",
            str(case_dir / "typ" / "typical_p90.parquet"),
            "--spans",
            str(case_dir / "spans.csv"),
            *COND,
            "--block-map",
            str(tmp_path / "bhm_s.parquet"),
            "--history",
            str(case_dir / "history.parquet"),
            "--utc-offset",
            "-4",
            "--years",
            "2001-2002",
            "-o",
            str(tmp_path / "out"),
        ]
    )
    assert rc == 0
    blocks = pd.read_parquet(tmp_path / "out" / "dlr_blocks.parquet")
    assert set(blocks["block"]) == {1, 2, 3, 4}
    with pytest.raises(SystemExit, match="overlap"):
        main(
            [
                "typical-year",
                "--history",
                str(case_dir / "history.parquet"),
                "--spans",
                str(case_dir / "spans.csv"),
                *COND,
                "--years",
                "2001-2003",
                "--test-years",
                "2003",
                "-o",
                str(tmp_path / "x"),
            ]
        )


def test_typical_year_aligned_cli(case_dir: Path, tmp_path: Path) -> None:
    rc = main(
        [
            "typical-year",
            "--history",
            str(case_dir / "history.parquet"),
            "--spans",
            str(case_dir / "spans.csv"),
            *COND,
            "--case",
            "p90",
            "--source-hours",
            str(case_dir / "typ" / "typical_mean.parquet"),
            "--align",
            "conditional",
            "--condition-k",
            "30",
            "-o",
            str(tmp_path),
        ]
    )
    assert rc == 0
    rep = json.loads((tmp_path / "typical_report.json").read_text())
    assert rep["p90"]["method"] == "aligned-conditional"
