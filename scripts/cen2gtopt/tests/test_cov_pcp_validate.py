# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Offline tests for :mod:`cen2gtopt.pcp_validate` (network + solver mocked)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from cen2gtopt import pcp_solution, pcp_validate as pv


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

#: Opaque client handle; every network call is monkeypatched away.
_CLIENT: Any = object()


class _FakeClient:
    """Stand-in for CenApiClient (context manager, never hits the network)."""

    def __init__(self, *_a: Any, **_k: Any) -> None:
        self.closed = False

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True


class _FakeInputs:
    """Minimal PcpInputs look-alike for derive_inputs_lambda."""

    def __init__(
        self, mc: pd.DataFrame, *, is_pid: bool = False, period: int | None = None
    ) -> None:
        self._mc = mc
        self.is_pid = is_pid
        self.period = period
        self.calls: list[int] = []

    def marginal_cost_per_unit(self, *, period: int) -> pd.DataFrame:
        self.calls.append(period)
        return self._mc.copy()


def _pipe_frame(buses: tuple[str, ...] = ("CRUCERO 220KV (north)",)) -> pd.DataFrame:
    rows = []
    for bus in buses:
        for hour in (0, 1):
            for quarter in range(4):
                rows.append(
                    {
                        "bus_label": bus,
                        "hour": hour,
                        "quarter": quarter,
                        "lambda_cen": 50.0 + hour,
                        "lambda_pipe": 52.0 + hour,
                        "lambda_constructed_pid": 51.0 + hour,
                        "marginal_central": "UNIT_A" if quarter < 3 else "UNIT_B",
                        "epsilon": 0.5,
                    }
                )
    return pd.DataFrame(rows)


def _mc_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "unit_name": ["A", "B", "C", "D", "E", "F", "G", "H"],
            "mc_usd_mwh": [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, float("nan")],
        }
    )


@pytest.fixture(name="patched")
def _patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "pipe": _pipe_frame(),
        "inputs": _FakeInputs(_mc_frame(), is_pid=True, period=20),
        "plexos": pd.DataFrame(
            {
                "plexos_bus_name": ["Crucero220", "Crucero220"],
                "hour": [0, 1],
                "lambda_plexos": [49.0, 53.0],
            }
        ),
    }
    monkeypatch.setattr(pv, "CenApiClient", _FakeClient)
    monkeypatch.setattr(
        pv, "compute_marginal_units_for_day", lambda *_a, **_k: state["pipe"]
    )

    def _load(*_a: Any, **_k: Any) -> Any:
        loaded = state["inputs"]
        if isinstance(loaded, BaseException):
            raise loaded
        return loaded

    monkeypatch.setattr(pv, "load_pcp_inputs", _load)
    monkeypatch.setattr(pv, "_fetch_plexos_lmp_per_bus", lambda _d: state["plexos"])
    return state


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def test_system_lambda_per_hour_empty_and_median() -> None:
    assert not pv._system_lambda_per_hour(pd.DataFrame())
    df = pd.DataFrame({"hour": [0, 0, 0, 1], "lmp": [1.0, 2.0, 9.0, 4.0]})
    assert pv._system_lambda_per_hour(df) == {0: 2.0, 1: 4.0}


def test_date_range_inclusive() -> None:
    from datetime import date

    assert pv._date_range(date(2026, 4, 30), date(2026, 5, 2)) == [
        "2026-04-30",
        "2026-05-01",
        "2026-05-02",
    ]
    assert not pv._date_range(date(2026, 5, 2), date(2026, 5, 1))


def test_fetch_real_lambda_skips_errors_and_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buses = [("B1", 1, "x"), ("B2", 2, "y"), ("B3", 3, "z")]
    monkeypatch.setattr(pv, "REF_BUSES", buses)

    def _fetch(_client: Any, _name: str, **kw: Any) -> pd.DataFrame:
        bus = kw["extra_params"]["bar_transf"]
        if bus == "B1":
            raise RuntimeError("boom")
        if bus == "B2":
            return pd.DataFrame()
        return pd.DataFrame({"hra": ["0", "1", "x"], "cmg_usd_mwh_": [10, 20, 30]})

    monkeypatch.setattr(pv, "fetch_by_name", _fetch)
    assert pv.fetch_real_lambda(_CLIENT, date_iso="2026-04-07") == {
        0: 10.0,
        1: 20.0,
    }


def test_fetch_real_lambda_no_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pv, "REF_BUSES", [("B1", 1, "x")])
    monkeypatch.setattr(pv, "fetch_by_name", lambda *_a, **_k: pd.DataFrame())
    assert not pv.fetch_real_lambda(_CLIENT, date_iso="2026-04-07")


def test_fetch_pcp_lambda_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> pd.DataFrame:
        raise RuntimeError("down")

    monkeypatch.setattr(pv, "fetch_by_name", _boom)
    assert not pv.fetch_pcp_lambda(_CLIENT, date_iso="2026-04-07")

    monkeypatch.setattr(pv, "fetch_by_name", lambda *_a, **_k: pd.DataFrame())
    assert not pv.fetch_pcp_lambda(_CLIENT, date_iso="2026-04-07")

    raw = pd.DataFrame(
        {
            "fecha_hora": ["2026-04-07 00:00", "2026-04-07 00:00", "2026-04-07 05:00"],
            "cmg_usd_mwh": ["10", "30", "7"],
        }
    )
    monkeypatch.setattr(pv, "fetch_by_name", lambda *_a, **_k: raw)
    assert pv.fetch_pcp_lambda(_CLIENT, date_iso="2026-04-07") == {
        0: 20.0,
        5: 7.0,
    }


def test_derive_inputs_lambda() -> None:
    inputs = _FakeInputs(_mc_frame())
    lam, unit = pv.derive_inputs_lambda(inputs, hour=17)
    assert inputs.calls == [18]  # 1-indexed periods
    # 6 positive MCs → top quartile has 1 unit (60).
    assert lam == pytest.approx(60.0)
    assert unit == "G"


def test_derive_inputs_lambda_empty_and_nonpositive() -> None:
    assert pv.derive_inputs_lambda(_FakeInputs(pd.DataFrame()), hour=0)[1] == ""
    zero = pd.DataFrame({"unit_name": ["A"], "mc_usd_mwh": [0.0]})
    lam, unit = pv.derive_inputs_lambda(_FakeInputs(zero), hour=0)
    assert pd.isna(lam) and unit == ""


# ---------------------------------------------------------------------------
# _fetch_plexos_lmp_per_bus
# ---------------------------------------------------------------------------


def test_fetch_plexos_lmp_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "cen2gtopt.pcp_solution", None)
    assert pv._fetch_plexos_lmp_per_bus("2026-04-07").empty


def test_fetch_plexos_lmp_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(*_a: Any, **_k: Any) -> Any:
        raise FileNotFoundError("no accdb")

    monkeypatch.setattr(pcp_solution, "resolve_solution", _missing)
    assert pv._fetch_plexos_lmp_per_bus("2026-04-07").empty


def test_fetch_plexos_lmp_empty_and_grouped(monkeypatch: pytest.MonkeyPatch) -> None:
    sol = pcp_solution.SolutionPaths(
        accdb_path=Path("x.accdb"), source="PCP", period=None
    )
    monkeypatch.setattr(pcp_solution, "resolve_solution", lambda *_a, **_k: sol)
    monkeypatch.setattr(
        pcp_solution, "extract_property", lambda *_a, **_k: pd.DataFrame()
    )
    assert pv._fetch_plexos_lmp_per_bus("2026-04-07").empty

    df = pd.DataFrame(
        {
            "object_name": ["N1", "N1", "N2"],
            "hour": [0, 0, 0],
            "value": [10.0, 20.0, 5.0],
        }
    )
    monkeypatch.setattr(pcp_solution, "extract_property", lambda *_a, **_k: df)
    out = pv._fetch_plexos_lmp_per_bus("2026-04-07")
    assert list(out.columns) == ["plexos_bus_name", "hour", "lambda_plexos"]
    assert out.set_index("plexos_bus_name")["lambda_plexos"].to_dict() == {
        "N1": 15.0,
        "N2": 5.0,
    }


# ---------------------------------------------------------------------------
# validate_one_day
# ---------------------------------------------------------------------------


def test_validate_one_day_full(patched: dict[str, Any], tmp_path: Path) -> None:
    out = pv.validate_one_day(date_iso="2026-05-06", period=20, output_dir=tmp_path)
    assert (tmp_path / "validation_2026-05-06.parquet").exists()
    assert len(out) == 2
    row0 = out[out["hour"] == 0].iloc[0]
    assert row0["lambda_real"] == pytest.approx(50.0)
    assert row0["lambda_pipeline"] == pytest.approx(52.0)
    assert row0["marginal_pipeline"] == "UNIT_A"
    assert row0["n_quarters"] == 4
    assert row0["lambda_plexos"] == pytest.approx(49.0)
    assert row0["delta_pipeline_vs_plexos"] == pytest.approx(3.0)
    assert row0["delta_plexos_vs_real"] == pytest.approx(-1.0)
    assert row0["source"] == "PID"
    assert row0["period"] == 20
    assert row0["lambda_inputs"] == pytest.approx(60.0)
    assert "plexos_bus_name" not in out.columns


def test_validate_one_day_empty_pipeline(patched: dict[str, Any]) -> None:
    patched["pipe"] = pd.DataFrame()
    assert pv.validate_one_day(date_iso="2026-05-06").empty


def test_validate_one_day_without_inputs_or_plexos(
    patched: dict[str, Any], tmp_path: Path
) -> None:
    patched["inputs"] = FileNotFoundError("no bundle")
    patched["plexos"] = pd.DataFrame()
    out = pv.validate_one_day(date_iso="2026-05-06", output_dir=tmp_path)
    assert out["lambda_inputs"].isna().all()
    assert (out["marginal_inputs"] == "").all()
    assert out["lambda_plexos"].isna().all()
    assert (out["source"] == "PCP").all()
    assert (out["period"] == 0).all()


def test_validate_one_day_exclude_plexos_no_write(
    patched: dict[str, Any], tmp_path: Path
) -> None:
    out = pv.validate_one_day(
        date_iso="2026-05-06",
        output_dir=tmp_path,
        write_parquet=False,
        include_plexos=False,
    )
    assert out["lambda_plexos"].isna().all()
    assert not list(tmp_path.iterdir())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_requires_dates(capsys: pytest.CaptureFixture[str]) -> None:
    assert pv.main([]) == 2
    assert "must specify" in capsys.readouterr().out
    assert pv.main(["--start-date", "2026-04-01"]) == 2


def test_main_single_day(
    patched: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert pv.main(["--date", "2026-05-06", "-q"]) == 0
    out = capsys.readouterr().out
    assert "=== validation for 2026-05-06 (PID period=20) ===" in out
    assert "per-bus mean" in out
    assert "delta_pipeline_vs_real" in out


def test_main_single_day_pcp_label(
    patched: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    patched["inputs"] = _FakeInputs(_mc_frame(), is_pid=False, period=None)
    patched["plexos"] = pd.DataFrame()
    assert pv.main(["--date", "2026-05-06", "-v"]) == 0
    out = capsys.readouterr().out
    assert "=== validation for 2026-05-06 (PCP) ===" in out
    # All-NaN Δ columns are skipped in the system aggregate.
    assert "delta_plexos_vs_real " not in out.split("system aggregate")[1]


def test_main_single_day_empty_output_does_not_crash(
    patched: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: an empty validation frame used to raise KeyError on
    ``df['source']`` when printing the single-day header."""
    patched["pipe"] = pd.DataFrame()
    assert pv.main(["--date", "2026-05-06"]) == 0
    assert "=== validation" not in capsys.readouterr().out


def test_main_failure_is_logged_and_skipped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _boom(**_k: Any) -> pd.DataFrame:
        raise RuntimeError("network down")

    monkeypatch.setattr(pv, "validate_one_day", _boom)
    assert pv.main(["--date", "2026-05-06"]) == 0
    assert capsys.readouterr().out == ""


def test_main_multi_day_summary(
    patched: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: the multi-day summary used to index the non-existent
    ``delta_pipeline_vs_pcp`` / ``delta_pcp_vs_real`` columns (KeyError)."""
    rc = pv.main(
        [
            "--start-date",
            "2026-05-06",
            "--end-date",
            "2026-05-07",
            "--output",
            str(tmp_path),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "PERIOD SUMMARY — 2 days" in out
    assert "delta_pipeline_vs_plexos" in out
    assert (tmp_path / "validation_aggregate.parquet").exists()
    agg = pd.read_parquet(tmp_path / "validation_aggregate.parquet")
    assert sorted(agg["date_iso"].unique()) == ["2026-05-06", "2026-05-07"]


def test_main_multi_day_no_output_skips_nan_columns(
    patched: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    patched["plexos"] = pd.DataFrame()
    assert pv.main(["--start-date", "2026-05-06", "--end-date", "2026-05-07"]) == 0
    out = capsys.readouterr().out
    summary = out.split("PERIOD SUMMARY")[1]
    assert "delta_pipeline_vs_real" in summary
    assert "delta_plexos_vs_real" not in summary
    assert "aggregate parquet" not in out
