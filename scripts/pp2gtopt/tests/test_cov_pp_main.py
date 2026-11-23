# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI error / option paths of pp2gtopt.main (time-series mode)."""

from __future__ import annotations

import argparse
import json
import logging
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pandapower as pp
import pandapower.networks as pn
import pytest

import pp2gtopt.main as m

_H = 24


@pytest.fixture(name="net_json")
def fixture_net_json(tmp_path: Path) -> Path:
    net = pn.case9()
    pp.create_sgen(net, 4, p_mw=0.0, max_p_mw=100.0, name="wind1")
    path = tmp_path / "c9.json"
    pp.to_json(net, str(path))
    return path


@pytest.fixture(name="prof_csv")
def fixture_prof_csv(tmp_path: Path) -> Path:
    path = tmp_path / "p.csv"
    pd.DataFrame({"hour": np.arange(_H), "load:0": 90.0, "wind1": 0.5}).to_csv(
        path, index=False
    )
    return path


def _run(net_json: Path, out: Path, *extra: str) -> int:
    return m.main(["--file", str(net_json), "-o", str(out), "--no-check", *extra])


def test_missing_profiles_file(net_json: Path, tmp_path: Path, caplog) -> None:
    rc = _run(net_json, tmp_path / "o.json", "--profiles", str(tmp_path / "nope.csv"))
    assert rc == 1
    assert "profiles file not found" in caplog.text


def test_profiles_from_net_without_controllers(
    net_json: Path, tmp_path: Path, caplog
) -> None:
    assert _run(net_json, tmp_path / "o.json", "--profiles-from-net") == 1
    assert "network carries no ConstControl" in caplog.text
    assert "no profile data" in caplog.text


def test_profiles_from_net_length_mismatch(
    net_json: Path, tmp_path: Path, prof_csv: Path, monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:  # fmt: skip
    monkeypatch.setattr(
        m,
        "profiles_from_net_controllers",
        lambda _net: pd.DataFrame({"load:1": np.ones(_H + 1)}),
    )
    rc = _run(
        net_json,
        tmp_path / "o.json",
        "--profiles",
        str(prof_csv),
        "--profiles-from-net",
    )
    assert rc == 1
    assert "differ in length" in caplog.text


def test_profiles_from_net_combined(
    net_json: Path, tmp_path: Path, prof_csv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        m,
        "profiles_from_net_controllers",
        lambda _net: pd.DataFrame({"load:1": np.full(_H, 50.0)}),
    )
    out = tmp_path / "o.json"
    assert _run(net_json, out, "--profiles", str(prof_csv), "--profiles-from-net") == 0
    data = json.loads(out.read_text())
    lmax = [d["lmax"] for d in data["system"]["demand_array"]]
    assert lmax[:2] == ["lmax", "lmax"]


@pytest.mark.parametrize(
    ("extra", "stages"),
    [
        (["--stage-hours", "12", "--block-method", "hour-of-day"], 2),
        (
            ["--block-method", "chrono", "--stage-period", "day", "--block-hours", "2"],
            1,
        ),
        (["--block-method", "hour-of-day", "--blocks-per-stage", "4"], 1),
    ],
)
def test_block_grid_stage_variants(
    net_json: Path, tmp_path: Path, prof_csv: Path, extra: list[str], stages: int
) -> None:
    out = tmp_path / "o.json"
    assert _run(net_json, out, "--profiles", str(prof_csv), *extra) == 0
    data = json.loads(out.read_text())
    assert len(data["simulation"]["stage_array"]) == stages


def test_peak_load_warning(net_json: Path, tmp_path: Path, caplog) -> None:
    path = tmp_path / "big.csv"
    pd.DataFrame({"hour": np.arange(_H), "load:0": 5000.0}).to_csv(path, index=False)
    with caplog.at_level(logging.INFO):
        assert _run(net_json, tmp_path / "o.json", "--profiles", str(path)) == 0
    assert "expect demand curtailment" in caplog.text


def test_many_profile_columns_logged_at_debug(net_json: Path, tmp_path: Path) -> None:
    path = tmp_path / "many.csv"
    cols = {"hour": np.arange(_H)} | {f"load:{i}": 10.0 for i in range(3)}
    cols |= {f"load@all:{i}": 1.0 for i in range(20)}
    pd.DataFrame(cols).to_csv(path, index=False)
    assert _run(net_json, tmp_path / "o.json", "--profiles", str(path)) == 0


def test_from_state_missing_file(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        m.main(["--from-state", str(tmp_path / "nope.json")])
    assert exc.value.code == 2
    assert "--from-state" in capsys.readouterr().err


def test_write_state_failure_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    import gtopt_shared.state_snapshot as snap  # noqa: PLC0415  pylint: disable=import-outside-toplevel

    def boom(**_kw: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(snap, "write_state_snapshot", boom)
    args = argparse.Namespace(from_state=None, output=None, file=None)
    m._write_state(args, tmp_path / "s", {})
    assert "failed to write state snapshot" in caplog.text


def test_list_networks_exits(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        m.main(["--list-networks"])
    assert exc.value.code == 0
    assert capsys.readouterr().out


def test_make_grid_stage_period_and_hours_conflict() -> None:
    args = types.SimpleNamespace(
        block_method="hour-of-day",
        blocks_per_stage=None,
        stage_period="month",
        stage_hours=24,
        block_hours=1,
    )
    resolved = types.SimpleNamespace(n_hours=4)
    with pytest.raises(m.ProfileError, match="not both"):
        m._make_grid(args, resolved, pd.date_range("2023-01-01", periods=4, freq="h"))
