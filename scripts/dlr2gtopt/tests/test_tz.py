# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Time basis: --timezone auto, --dst, --utc-offset."""

from __future__ import annotations

import builtins
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from dlr2gtopt import _tz as tz
from dlr2gtopt.__main__ import main


def _no_tzf(monkeypatch: pytest.MonkeyPatch) -> None:
    real = builtins.__import__

    def fake(name: str, *a: Any, **k: Any) -> Any:
        if name == "timezonefinder":
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)


def test_zone_from_boxes(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tzf(monkeypatch)
    assert tz.zone_from_coords([-31.9], [-71.5]) == "America/Santiago"
    assert tz.zone_from_coords([-53.1], [-70.9]) == "America/Punta_Arenas"
    assert tz.zone_from_coords([-27.1], [-109.4]) == "Pacific/Easter"
    with pytest.raises(ValueError, match="--timezone"):
        tz.zone_from_coords([40.0], [-3.0])
    with pytest.raises(ValueError, match="several zones"):
        tz.zone_from_coords([-31.9, -53.1], [-71.5, -70.9])
    with pytest.raises(ValueError, match="no coordinates"):
        tz.zone_from_coords([], [])


def test_zone_with_timezonefinder(monkeypatch: pytest.MonkeyPatch) -> None:
    class Fake:
        def timezone_at(self, lat: float, lng: float) -> str | None:
            return "Europe/Madrid" if lat > 0 else None

    import sys
    import types

    mod = types.ModuleType("timezonefinder")
    mod.TimezoneFinder = Fake  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "timezonefinder", mod)
    assert tz.zone_from_coords([40.0], [-3.0]) == "Europe/Madrid"
    # no answer -> the built-in table
    assert tz.zone_from_coords([-31.9], [-71.5]) == "America/Santiago"


def test_standard_offset_and_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_tzf(monkeypatch)
    assert tz.standard_offset("America/Santiago") == -4.0
    assert tz.standard_offset("America/Punta_Arenas") == -3.0
    assert tz.standard_offset("Europe/Madrid") == 1.0
    assert tz.standard_offset("UTC") == 0.0
    b = tz.resolve("auto", "off", None, [-31.9], [-71.5])
    assert b == tz.TimeBasis(-4.0) and b.spec == -4.0 and "fixed" in b.describe()
    d = tz.resolve("America/Santiago", "on", None)
    assert d.zone == "America/Santiago" and d.spec == "America/Santiago"
    assert "DST" in d.describe()
    # summer: civil UTC-3; fixed: UTC-4
    t = [datetime(2019, 1, 1, 3)]
    assert d.local(t)[0].hour == 0 and b.local(t)[0].hour == 23
    assert tz.resolve("auto", "on", -5.0).spec == -5.0
    assert tz.resolve("UTC", "on", None).spec == 0.0
    with pytest.raises(ValueError):
        tz.resolve("auto", "maybe", None)
    with pytest.raises(Exception):
        tz.resolve("Not/AZone", "off", None)


def test_cli_timezone_auto_matches_offset(tmp_path: Path) -> None:
    pytest.importorskip("pandas")
    from dlr2gtopt._spans import write_spans
    from dlr2gtopt.tests._typical_synth import history, spans3
    from dlr2gtopt.tests.test_typical_cli import COND

    d = tmp_path
    write_spans(d / "spans.csv", spans3())
    history((2001, 2002)).write(d / "history.parquet")
    outs = {}
    for name, extra in (
        ("auto", []),
        ("fixed", ["--utc-offset", "-4"]),
        ("dst", ["--dst", "on"]),
    ):
        o = tmp_path / name
        rc = main(
            [
                "typical-year",
                "--history",
                str(d / "history.parquet"),
                "--spans",
                str(d / "spans.csv"),
                *COND,
                *extra,
                "--case",
                "mean",
                "-o",
                str(o),
            ]
        )
        assert rc == 0
        outs[name] = (o / "typical_mean.parquet").read_bytes()
    assert outs["auto"] == outs["fixed"]
    rep = json.loads((tmp_path / "dst" / "typical_report.json").read_text())
    assert "DST" in rep["calendar"]
