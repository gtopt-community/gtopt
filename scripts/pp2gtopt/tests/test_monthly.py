# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""pp2gtopt monthly stages with representative blocks (hour-of-day / ldc),
net.storage → Battery, and the SDDP / monolithic method choice."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pandapower as pp
import pandapower.networks as pn
import pytest

from pp2gtopt.convert import _build_batteries
from pp2gtopt.main import main

_H = 8760
_MONTH_HOURS_2023 = [d * 24 for d in (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)]


def _year_frame(n_hours: int = _H, dates: bool = False) -> pd.DataFrame:
    hours = np.arange(n_hours)
    rng = np.random.default_rng(3)
    shape = (0.8 + 0.15 * np.sin(2 * np.pi * (hours - 9) / 24)) * (
        1.0 + 0.15 * np.cos(2 * np.pi * hours / 8760)
    )
    wind = np.clip(
        0.45 + 0.3 * np.sin(hours / 40) + 0.1 * rng.standard_normal(n_hours), 0, 1
    )
    sun = 80.0 * np.clip(np.sin(np.pi * ((hours % 24) - 6) / 12), 0.0, None)
    time: object = hours
    if dates:
        time = pd.date_range("2024-03-01", periods=n_hours, freq="h").strftime(
            "%Y-%m-%d %H:%M"
        )
    return pd.DataFrame(
        {"time": time, "load@all": 315.0 * shape, "wind1": wind, "sgen:pv1": sun}
    )


def _net(storage: bool = True) -> pp.pandapowerNet:
    net = pn.case9()
    pp.create_sgen(net, 4, p_mw=0.0, max_p_mw=100.0, name="wind1")
    pp.create_sgen(net, 6, p_mw=0.0, max_p_mw=80.0, name="pv1")
    if storage:
        pp.create_storage(
            net,
            8,
            p_mw=0.0,
            max_e_mwh=400.0,
            max_p_mw=50.0,
            min_p_mw=-40.0,
            soc_percent=25.0,
            name="bess1",
        )
    return net


@pytest.fixture(name="year_case", scope="module")
def fixture_year_case(tmp_path_factory) -> tuple[Path, Path, Path]:
    base = tmp_path_factory.mktemp("year")
    with_storage = base / "case9s.json"
    pp.to_json(_net(storage=True), str(with_storage))
    no_storage = base / "case9.json"
    pp.to_json(_net(storage=False), str(no_storage))
    prof = base / "year.csv"
    _year_frame().to_csv(prof, index=False)
    return with_storage, no_storage, prof


def _run(argv: list[str]) -> int:
    return main([*argv, "--no-check"])


def _convert(tmp_path: Path, net: Path, prof: Path, *extra: str) -> tuple[dict, Path]:
    out = tmp_path / "case.json"
    argv = ["-f", str(net), "--profiles", str(prof), "-o", str(out), *extra]
    assert _run(argv) == 0
    return json.loads(out.read_text()), tmp_path / "case"


def _blocks(data: dict) -> pd.DataFrame:
    sim = data["simulation"]
    rows = []
    for stage in sim["stage_array"]:
        for b in range(
            stage["first_block"], stage["first_block"] + stage["count_block"]
        ):
            rows.append(
                {
                    "stage": stage["uid"],
                    "block": sim["block_array"][b]["uid"],
                    "duration": sim["block_array"][b]["duration"],
                }
            )
    return pd.DataFrame(rows)


def _schedule(case_dir: Path, cls: str, field: str) -> pd.DataFrame:
    return pd.read_parquet(case_dir / cls / f"{field}.parquet")


def _stage_energy(case_dir: Path, data: dict, cls: str, field: str) -> pd.DataFrame:
    sched = _schedule(case_dir, cls, field).merge(
        _blocks(data)[["block", "duration"]], on="block"
    )
    sched["energy"] = sched["value"] * sched["duration"]
    return sched.groupby(["stage", "uid"])["energy"].sum().unstack()


MONTHLY = ["--stage-period", "month", "--blocks-per-stage", "12"]


class TestMonthlyLdc:
    def test_structure_and_energy(self, tmp_path: Path, year_case) -> None:
        net, _, prof = year_case
        data, case_dir = _convert(
            tmp_path, net, prof, *MONTHLY, "--block-method", "ldc", "--method", "sddp"
        )
        blocks = _blocks(data)
        assert len(data["simulation"]["stage_array"]) == 12
        assert len(blocks) == 144
        assert len(data["simulation"]["phase_array"]) == 12
        durations = blocks.groupby("stage")["duration"].sum().tolist()
        assert durations == _MONTH_HOURS_2023
        # equal-hours split: block durations within a month differ by ≤ 1 h
        spread = blocks.groupby("stage")["duration"].agg(lambda d: d.max() - d.min())
        assert spread.max() <= 1.0

        # energy per load per month equals the hourly energy
        hourly = _year_frame()
        month = pd.Series(pd.date_range("2023-01-01", periods=_H, freq="h")).dt.month
        hourly_load = hourly["load@all"].groupby(month).sum()
        load_energy = _stage_energy(case_dir, data, "Demand", "lmax").sum(axis=1)
        np.testing.assert_allclose(load_energy.to_numpy(), hourly_load.to_numpy())
        # per element: d2 = load index 1 (100 MW of 315 MW base)
        np.testing.assert_allclose(
            _stage_energy(case_dir, data, "Demand", "lmax")[2].to_numpy(),
            hourly_load.to_numpy() * 100.0 / 315.0,
        )
        # renewable availability energy per month (pv1: profile uid 2, 80 MW)
        prof_energy = _stage_energy(case_dir, data, "GeneratorProfile", "profile")
        hourly_pv = hourly["sgen:pv1"].groupby(month).sum() / 80.0
        np.testing.assert_allclose(prof_energy[2].to_numpy(), hourly_pv.to_numpy())

    def test_block_hour_map(self, tmp_path: Path, year_case) -> None:
        net, _, prof = year_case
        data, case_dir = _convert(
            tmp_path, net, prof, *MONTHLY, "--block-method", "ldc"
        )
        assert data["simulation"]["block_hour_map"] == "BlockHourMap/block_hour_map"
        bhm = pd.read_parquet(case_dir / "BlockHourMap" / "block_hour_map.parquet")
        assert list(bhm.columns) == [
            "year",
            "month",
            "day",
            "hour",
            "stage",
            "block",
            "hour_index",
        ]
        assert bhm["hour_index"].tolist() == list(range(_H))
        counts = bhm.groupby("block").size()
        dur = _blocks(data).set_index("block")["duration"]
        np.testing.assert_allclose(counts.to_numpy(), dur.loc[counts.index].to_numpy())
        assert (bhm["stage"] == bhm["month"]).all()
        # ldc: block 1 of every month has the highest mean net load
        hourly = _year_frame()
        net_load = (
            hourly["load@all"] - 100.0 * hourly["wind1"] - hourly["sgen:pv1"]
        ).to_numpy()
        bhm["net"] = net_load
        means = bhm.groupby(["stage", "block"])["net"].mean().reset_index()
        for _stage, grp in means.groupby("stage"):
            assert grp["net"].is_monotonic_decreasing

    def test_equal_energy_split(self, tmp_path: Path, year_case) -> None:
        net, _, prof = year_case
        data, _ = _convert(
            tmp_path,
            net,
            prof,
            *MONTHLY,
            "--block-method",
            "ldc",
            "--ldc-split",
            "energy",
            "--ldc-key",
            "load",
        )
        blocks = _blocks(data)
        jan = blocks[blocks.stage == 1]["duration"].to_numpy()
        assert jan.sum() == 744
        assert jan[0] < jan[-1]  # peak block shorter than the base block


class TestHourOfDay:
    def test_typical_day_blocks(self, tmp_path: Path, year_case) -> None:
        net, _, prof = year_case
        data, case_dir = _convert(
            tmp_path, net, prof, *MONTHLY, "--block-method", "hour-of-day"
        )
        blocks = _blocks(data)
        days = np.array(_MONTH_HOURS_2023) // 24
        for stage, grp in blocks.groupby("stage"):
            assert (grp["duration"] == 2 * days[stage - 1]).all()
        bhm = pd.read_parquet(case_dir / "BlockHourMap" / "block_hour_map.parquet")
        first = bhm[bhm.stage == 1]
        assert (first.groupby("block")["hour"].agg(lambda h: sorted(set(h)))).iloc[
            0
        ] == [1, 2]
        energy = _stage_energy(case_dir, data, "Demand", "lmax").sum(axis=1)
        month = pd.Series(pd.date_range("2023-01-01", periods=_H, freq="h")).dt.month
        expected = _year_frame()["load@all"].groupby(month).sum()
        np.testing.assert_allclose(energy.to_numpy(), expected.to_numpy())

    def test_blocks_per_stage_defaults_to_chronological_day(
        self, tmp_path: Path, year_case
    ) -> None:
        """--blocks-per-stage 12 alone: block k = hours 2k, 2k+1 of every day."""
        net, _, prof = year_case
        data, case_dir = _convert(tmp_path, net, prof, *MONTHLY)
        blocks = _blocks(data)
        assert len(blocks) == 144
        bhm = pd.read_parquet(case_dir / "BlockHourMap" / "block_hour_map.parquet")
        for _stage, grp in bhm.groupby("stage"):
            hours = grp.groupby("block")["hour"].agg(lambda h: sorted(set(h)))
            assert hours.tolist() == [[2 * k + 1, 2 * k + 2] for k in range(12)]

    def test_dates_from_profile_and_start_date(self, tmp_path: Path) -> None:
        net = tmp_path / "n.json"
        pp.to_json(_net(), str(net))
        prof = tmp_path / "dated.csv"
        _year_frame(n_hours=24 * 61, dates=True).to_csv(prof, index=False)
        data, case_dir = _convert(
            tmp_path, net, prof, *MONTHLY, "--block-method", "hour-of-day"
        )
        assert _blocks(data).groupby("stage")["duration"].sum().tolist() == [744, 720]
        bhm = pd.read_parquet(case_dir / "BlockHourMap" / "block_hour_map.parquet")
        assert bhm["month"].iloc[0] == 3 and bhm["year"].iloc[0] == 2024
        # --start-date overrides: Feb 2024 is a leap month (29 days)
        data, _ = _convert(
            tmp_path,
            net,
            prof,
            *MONTHLY,
            "--block-method",
            "hour-of-day",
            "--start-date",
            "2024-02-01",
        )
        assert _blocks(data).groupby("stage")["duration"].sum().tolist() == [
            696,
            744,
            24,
        ]


class TestBlockConsistency:
    """Load and renewables are folded over the SAME hours of every block."""

    @pytest.mark.parametrize("method", [[], ["--block-method", "ldc"]])
    def test_block_values_are_means_of_the_same_hours(
        self, tmp_path: Path, year_case, method
    ) -> None:
        net, _, prof = year_case
        _data, case_dir = _convert(tmp_path, net, prof, *MONTHLY, *method)
        bhm = pd.read_parquet(case_dir / "BlockHourMap" / "block_hour_map.parquet")
        hourly = _year_frame()
        bhm["load"] = hourly["load@all"].to_numpy() * 100.0 / 315.0  # d2
        bhm["pv"] = hourly["sgen:pv1"].to_numpy() / 80.0  # pv1 profile
        expected = bhm.groupby("block")[["load", "pv"]].mean()
        load = _schedule(case_dir, "Demand", "lmax")
        load = load[load.uid == 2].set_index("block")["value"]
        pv = _schedule(case_dir, "GeneratorProfile", "profile")
        pv = pv[pv.uid == 2].set_index("block")["value"]
        np.testing.assert_allclose(
            load.loc[expected.index].to_numpy(), expected["load"].to_numpy()
        )
        np.testing.assert_allclose(
            pv.loc[expected.index].to_numpy(), expected["pv"].to_numpy(), atol=1e-12
        )

    def test_sun_shines_only_in_daytime_blocks(self, tmp_path: Path, year_case) -> None:
        net, _, prof = year_case
        _data, case_dir = _convert(tmp_path, net, prof, *MONTHLY)
        pv = _schedule(case_dir, "GeneratorProfile", "profile")
        pv = pv[pv.uid == 2].sort_values("block")
        pv["k"] = pv.groupby("stage").cumcount()  # 0..11 -> hours 2k, 2k+1
        night = pv[pv.k.isin([0, 1, 2, 9, 10, 11])]  # 00-06 h and 18-24 h
        day = pv[pv.k.isin([4, 5, 6, 7])]  # 08-16 h
        assert (night["value"].abs() < 1e-12).all()
        assert (day["value"] > 0.3).all()


class TestStagePeriods:
    @pytest.mark.parametrize(
        ("period", "hours"),
        [
            ("quarter", [2160, 2184, 2208, 2208]),
            ("year", [8760]),
            ("week", [168] * 52 + [24]),
        ],
    )
    def test_calendar_stage_lengths(
        self, tmp_path: Path, year_case, period, hours
    ) -> None:
        net, _, prof = year_case
        data, _ = _convert(
            tmp_path, net, prof, "--stage-period", period, "--blocks-per-stage", "12"
        )
        assert _blocks(data).groupby("stage")["duration"].sum().tolist() == hours


class TestStorageAndMethod:
    def test_battery_conversion(self) -> None:
        bat = _build_batteries(_net(), efficiency=0.9)
        assert bat == [
            {
                "uid": 1,
                "name": "bess1",
                "bus": 9,
                "input_efficiency": 0.9,
                "output_efficiency": 0.9,
                "emin": 0.0,
                "emax": 400.0,
                "capacity": 400.0,
                "eini": 100.0,
                "pmax_charge": 50.0,
                "pmax_discharge": 40.0,
            }
        ]
        net = _net()
        net.storage["efficiency_percent"] = 81.0
        assert _build_batteries(net)[0]["input_efficiency"] == pytest.approx(0.9)
        net.storage.loc[0, "in_service"] = False
        assert not _build_batteries(net)

    def test_sddp_iterations_follow_state(self, tmp_path: Path, year_case) -> None:
        with_storage, no_storage, prof = year_case
        args = [*MONTHLY, "--block-method", "ldc", "--method", "sddp"]
        data, _ = _convert(tmp_path / "a", with_storage, prof, *args)
        assert len(data["system"]["battery_array"]) == 1
        assert data["options"]["sddp_options"] == {
            "max_iterations": 100,
            "convergence_tol": 0.01,
        }
        data, _ = _convert(tmp_path / "b", no_storage, prof, *args)
        assert "battery_array" not in data["system"]
        assert data["options"]["sddp_options"]["max_iterations"] == 0
        data, _ = _convert(tmp_path / "c", with_storage, prof, *args, "--no-storage")
        assert data["options"]["sddp_options"]["max_iterations"] == 0
        data, _ = _convert(
            tmp_path / "d", with_storage, prof, *args, "--sddp-max-iterations", "7"
        )
        assert data["options"]["sddp_options"]["max_iterations"] == 7

    def test_default_method_monolithic(self, tmp_path: Path, year_case) -> None:
        _, no_storage, prof = year_case
        data, _ = _convert(
            tmp_path, no_storage, prof, *MONTHLY, "--block-method", "ldc"
        )
        assert data["options"]["method"] == "monolithic"
        assert "sddp_options" not in data["options"]

    @pytest.mark.parametrize(
        "extra",
        [
            ["--stage-period", "month", "--stage-hours", "24"],
            ["--block-method", "ldc", "--block-hours", "2"],
            ["--block-method", "ldc", "--blocks-per-stage", "0"],
            ["--block-method", "hour-of-day", "--blocks-per-stage", "30"],
            ["--block-method", "chrono", "--blocks-per-stage", "12"],
        ],
    )
    def test_bad_options(self, tmp_path: Path, year_case, extra) -> None:
        net, _, prof = year_case
        out = tmp_path / "x.json"
        argv = ["-f", str(net), "--profiles", str(prof), "-o", str(out), *extra]
        assert _run(argv) == 1


# ---------------------------------------------------------------------------
# End-to-end with the gtopt binary (skipped when unavailable)
# ---------------------------------------------------------------------------


def _gtopt() -> str | None:
    env_bin = os.environ.get("GTOPT_BIN")
    if env_bin and Path(env_bin).exists():
        return env_bin
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / "build" / "standalone" / "gtopt"
    if candidate.exists():
        return str(candidate)
    return shutil.which("gtopt")


def _solve(binary: str, json_path: Path, *extra: str) -> Path:
    out_dir = json_path.parent / f"out_{json_path.stem}"
    res = subprocess.run(
        [binary, json_path.name, "-d", out_dir.name, *extra],
        cwd=json_path.parent,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert res.returncode == 0, res.stderr[-2000:]
    return out_dir


@pytest.mark.integration
def test_e2e_monthly_methods(tmp_path: Path, year_case) -> None:
    binary = _gtopt()
    if binary is None:
        pytest.skip("gtopt binary not found (set GTOPT_BIN)")
    with_storage, no_storage, prof = year_case
    base = [*MONTHLY, "--block-method", "ldc"]

    # LP build of the monthly SDDP case
    sddp_json = tmp_path / "s" / "case.json"
    _convert(tmp_path / "s", with_storage, prof, *base, "--method", "sddp")
    _solve(binary, sddp_json, "--lp-only")

    # with a battery: SDDP iterates (cuts, ≥ 2 iterations) and converges to
    # the monolithic (serial phases) cost
    sddp_out = _solve(binary, sddp_json)
    status = json.loads((sddp_out / "solver_status.json").read_text())
    assert status["converged"]
    assert len(status["history"]) >= 2
    assert status["history"][0]["cuts_added"] > 0
    mono_json = tmp_path / "m" / "case.json"
    _convert(tmp_path / "m", with_storage, prof, *base, "--method", "monolithic")
    mono = pd.read_csv(_solve(binary, mono_json) / "solution.csv")
    assert (mono["status"] == 0).all()
    assert status["upper_bound"] == pytest.approx(mono["obj_value"].sum(), rel=1e-6)

    # no storage: monolithic and SDDP with max_iterations = 0 agree
    totals = {}
    for method in ("monolithic", "sddp"):
        js = tmp_path / f"n_{method}" / "case.json"
        _convert(js.parent, no_storage, prof, *base, "--method", method)
        sol = pd.read_csv(_solve(binary, js) / "solution.csv")
        assert len(sol) == 12
        assert (sol["status"] == 0).all()
        totals[method] = sol["obj_value"].sum()
    assert totals["sddp"] == pytest.approx(totals["monolithic"], rel=1e-9)
