# SPDX-License-Identifier: BSD-3-Clause
"""Tests for pp2gtopt time-series mode (hourly load / renewable profiles)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pandapower as pp
import pandapower.networks as pn
import pytest

from gtopt_shared.time_grid import TimeGrid
from pp2gtopt.convert import TimeSeriesData, convert, load_network
from pp2gtopt.main import main
from pp2gtopt.profiles import (
    ColumnTarget,
    ProfileError,
    ProfileSpec,
    load_spec,
    profiles_from_net_controllers,
    read_profile_table,
    resolve_profiles,
)

_H = 48
_HOURS = np.arange(_H)
_SHAPE = 0.8 + 0.2 * np.sin(2 * np.pi * (_HOURS - 8) / 24)


def _case9_with_sgens() -> pp.pandapowerNet:
    """case9 + a 100 MW wind sgen (bus 5) and a 50 MW solar sgen (bus 7)."""
    net = pn.case9()
    pp.create_sgen(net, 4, p_mw=0.0, max_p_mw=100.0, name="wind1")
    pp.create_sgen(net, 6, p_mw=0.0, max_p_mw=50.0, name="pv1")
    return net


def _wind_pu() -> np.ndarray:
    return np.clip(0.5 + 0.4 * np.sin(2 * np.pi * _HOURS / 17), 0.0, 1.0)


def _solar_mw() -> np.ndarray:
    return 50.0 * np.clip(np.sin(np.pi * ((_HOURS % 24) - 6) / 12), 0.0, None)


def _wide_frame() -> pd.DataFrame:
    """48-h wide profile table for case9 (3 loads, 2 sgens, 1 unmatched)."""
    return pd.DataFrame(
        {
            "hour": _HOURS,
            "load:0": 90.0 * _SHAPE,
            "load@bus:7": 100.0 * _SHAPE,  # bus *name* 7 = pandapower bus 6
            "load:2": 125.0 * _SHAPE,
            "wind1": _wind_pu(),  # bare sgen name, auto → per-unit
            "sgen:pv1": _solar_mw(),  # MW (max > 1 → auto MW)
            "nomatch": np.ones(_H),
        }
    )


@pytest.fixture(name="wide_csv")
def fixture_wide_csv(tmp_path: Path) -> Path:
    path = tmp_path / "profiles.csv"
    _wide_frame().to_csv(path, index=False)
    return path


@pytest.fixture(name="net_json")
def fixture_net_json(tmp_path: Path) -> Path:
    path = tmp_path / "case9s.json"
    pp.to_json(_case9_with_sgens(), str(path))
    return path


def _read_long(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def _lmax_matrix(case_dir: Path) -> pd.DataFrame:
    df = _read_long(case_dir / "Demand" / "lmax.parquet")
    return df.pivot(index="block", columns="uid", values="value")


# ---------------------------------------------------------------------------
# Reading + resolution
# ---------------------------------------------------------------------------


class TestReadAndResolve:
    def test_wide_resolution(self, wide_csv: Path, caplog) -> None:
        net = _case9_with_sgens()
        wide = read_profile_table(wide_csv, ProfileSpec())
        assert "hour" not in wide.columns
        with caplog.at_level(logging.WARNING):
            res = resolve_profiles(net, wide, ProfileSpec())
        assert res.unmatched_columns == ["nomatch"]
        assert "nomatch" in caplog.text
        assert sorted(res.load_mw) == [0, 1, 2]
        np.testing.assert_allclose(res.load_mw[1], 100.0 * _SHAPE)
        factors = res.generator_factors(net)
        sgen_idx = {n: i for i, n in net.sgen["name"].items()}
        np.testing.assert_allclose(factors[("sgen", sgen_idx["wind1"])], _wind_pu())
        np.testing.assert_allclose(
            factors[("sgen", sgen_idx["pv1"])], _solar_mw() / 50.0
        )

    def test_long_layout_matches_wide(self, tmp_path: Path, wide_csv: Path) -> None:
        wide_df = _wide_frame()
        long_df = wide_df.melt(id_vars="hour", var_name="id", value_name="value")
        long_df = long_df.sample(frac=1.0, random_state=3)  # unordered rows
        path = tmp_path / "long.parquet"
        long_df.to_parquet(path, index=False)
        from_long = read_profile_table(path, ProfileSpec())
        from_wide = read_profile_table(wide_csv, ProfileSpec())
        pd.testing.assert_frame_equal(
            from_long[sorted(from_long.columns)],
            from_wide[sorted(from_wide.columns)],
        )

    def test_long_layout_element_column(self, tmp_path: Path) -> None:
        df = pd.DataFrame(
            {
                "time": np.repeat(_HOURS[:4], 2),
                "element": ["load", "sgen"] * 4,
                "id": ["0", "wind1"] * 4,
                "value": [50.0, 0.5] * 4,
            }
        )
        path = tmp_path / "long.csv"
        df.to_csv(path, index=False)
        wide = read_profile_table(path, ProfileSpec())
        assert set(wide.columns) == {"load:0", "sgen:wind1"}
        res = resolve_profiles(_case9_with_sgens(), wide, ProfileSpec())
        assert list(res.load_mw) == [0]
        assert len(res.gen_mw) == 1

    def test_negative_load_errors(self) -> None:
        wide = pd.DataFrame({"load:0": [10.0, -1.0]})
        with pytest.raises(ProfileError, match="negative"):
            resolve_profiles(pn.case9(), wide, ProfileSpec())

    def test_nothing_matched_errors(self) -> None:
        wide = pd.DataFrame({"foo": [1.0, 2.0]})
        with pytest.raises(ProfileError, match="no column matched"):
            resolve_profiles(pn.case9(), wide, ProfileSpec())

    def test_load_all_splits_by_base(self) -> None:
        net = pn.case9()
        wide = pd.DataFrame({"load@all": [315.0, 630.0]})
        res = resolve_profiles(net, wide, ProfileSpec())
        np.testing.assert_allclose(res.load_mw[0], [90.0, 180.0])
        np.testing.assert_allclose(res.load_mw[2], [125.0, 250.0])

    def test_pu_load_multiplies_base(self) -> None:
        spec = ProfileSpec(units={"load": "pu"})
        res = resolve_profiles(pn.case9(), pd.DataFrame({"load@all": [0.5]}), spec)
        np.testing.assert_allclose(res.load_mw[1], [50.0])

    def test_generator_overshoot_is_clipped(self, caplog) -> None:
        net = _case9_with_sgens()
        wide = pd.DataFrame({"sgen:pv1": [10.0, 75.0]})  # 75 MW > 50 MW rated
        res = resolve_profiles(net, wide, ProfileSpec())
        with caplog.at_level(logging.WARNING):
            factors = res.generator_factors(net)
        assert "clipped" in caplog.text
        np.testing.assert_allclose(next(iter(factors.values())), [0.2, 1.0])

    def test_net_controller_profiles(self) -> None:
        from pandapower.control import ConstControl  # noqa: PLC0415
        from pandapower.timeseries import DFData  # noqa: PLC0415

        net = pn.case9()
        src = DFData(pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]}))
        ConstControl(
            net,
            element="load",
            variable="p_mw",
            element_index=[0, 2],
            data_source=src,
            profile_name=["a", "b"],
        )
        wide = profiles_from_net_controllers(net)
        assert list(wide.columns) == ["load:0", "load:2"]
        np.testing.assert_allclose(wide["load:2"], [4.0, 5.0, 6.0])


class TestSpec:
    def test_yaml_spec(self, tmp_path: Path) -> None:
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            "default: {element: load, by: bus}\n"
            "units: {sgen: pu}\n"
            "ignore: [total]\n"
            "columns:\n"
            "  W: {element: sgen, by: name, ref: wind1, scale: 0.5}\n"
        )
        spec = load_spec(spec_path)
        wide = pd.DataFrame(
            {"5": [90.0], "9": [125.0], "W": [1.0], "total": [999.0], "77": [1.0]}
        )
        wide = wide.drop(columns=spec.ignore)
        res = resolve_profiles(_case9_with_sgens(), wide, spec)
        assert sorted(res.load_mw) == [0, 2]  # buses named 5 and 9
        assert res.unmatched_columns == ["77"]
        np.testing.assert_allclose(next(iter(res.gen_mw.values())), [50.0])

    def test_json_spec_columns(self, tmp_path: Path) -> None:
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(
            json.dumps(
                {"columns": {"R1": {"element": "load", "by": "zone", "ref": "1"}}}
            )
        )
        spec = load_spec(spec_path)
        res = resolve_profiles(pn.case9(), pd.DataFrame({"R1": [315.0]}), spec)
        assert sorted(res.load_mw) == [0, 1, 2]

    def test_bad_spec_key(self, tmp_path: Path) -> None:
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps({"colums": {}}))
        with pytest.raises(ProfileError, match="unknown spec keys"):
            load_spec(spec_path)

    def test_bad_target(self) -> None:
        with pytest.raises(ProfileError, match="element must be"):
            ColumnTarget.from_dict({"element": "line"}, "x")


# ---------------------------------------------------------------------------
# convert() with a TimeSeriesData
# ---------------------------------------------------------------------------


class TestConvertTimeSeries:
    def _convert(self, tmp_path: Path, grid: TimeGrid) -> tuple[dict, Path]:
        net = _case9_with_sgens()
        res = resolve_profiles(net, _wide_frame().drop(columns="hour"), ProfileSpec())
        ts = TimeSeriesData(
            grid=grid, load_mw=res.load_mw, gen_factor=res.generator_factors(net)
        )
        out = tmp_path / "c9.json"
        data = convert(out, net=net, name="c9", solver_type="monolithic", timeseries=ts)
        assert ts.summary["blocks"] == grid.n_blocks
        return data, tmp_path / "c9"

    def test_hourly_single_stage(self, tmp_path: Path) -> None:
        data, case_dir = self._convert(tmp_path, TimeGrid(n_hours=_H))
        sim = data["simulation"]
        assert len(sim["block_array"]) == _H
        assert sim["stage_array"] == [
            {"uid": 1, "first_block": 0, "count_block": _H, "active": 1}
        ]
        assert "phase_array" not in sim
        assert data["options"]["input_directory"] == "c9"
        assert data["options"]["method"] == "monolithic"
        assert all(d["lmax"] == "lmax" for d in data["system"]["demand_array"])
        lmax = _lmax_matrix(case_dir)
        assert lmax.shape == (_H, 3)
        np.testing.assert_allclose(lmax[2].to_numpy(), 100.0 * _SHAPE)

    def test_block_aggregation_and_daily_stages(self, tmp_path: Path) -> None:
        grid = TimeGrid(n_hours=_H, block_hours=4, stage_hours=24)
        data, case_dir = self._convert(tmp_path, grid)
        sim = data["simulation"]
        assert [b["duration"] for b in sim["block_array"]] == [4.0] * 12
        assert [(s["first_block"], s["count_block"]) for s in sim["stage_array"]] == [
            (0, 6),
            (6, 6),
        ]
        assert len(sim["phase_array"]) == 2
        lmax_long = _read_long(case_dir / "Demand" / "lmax.parquet")
        assert list(lmax_long.columns) == ["stage", "block", "uid", "value"]
        assert set(lmax_long.loc[lmax_long.block > 6, "stage"]) == {2}
        lmax = lmax_long.pivot(index="block", columns="uid", values="value")
        expected = (125.0 * _SHAPE).reshape(12, 4).mean(axis=1)
        np.testing.assert_allclose(lmax[3].to_numpy(), expected)
        # energy preserved by the block mean
        assert float((lmax[3] * 4).sum()) == pytest.approx(
            float((125.0 * _SHAPE).sum())
        )

    def test_generator_profiles(self, tmp_path: Path) -> None:
        data, case_dir = self._convert(tmp_path, TimeGrid(n_hours=_H))
        gens = data["system"]["generator_array"]
        assert [g["name"] for g in gens] == ["g1", "g2", "g3", "g4", "g5"]
        profiles = data["system"]["capacity_profile_array"]
        assert [p["owner"] for p in profiles] == [4, 5]
        assert all(p["owner_kind"] == "generator" for p in profiles)
        assert all(p["profile"] == "profile" for p in profiles)
        prof = _read_long(case_dir / "GeneratorProfile" / "profile.parquet")
        assert list(prof.columns) == ["scenario", "stage", "block", "uid", "value"]
        wind = prof[prof.uid == 1].sort_values("block")["value"].to_numpy()
        np.testing.assert_allclose(wind, _wind_pu())

    def test_unprofiled_load_is_constant(self, tmp_path: Path) -> None:
        net = pn.case9()
        ts = TimeSeriesData(
            grid=TimeGrid(n_hours=2), load_mw={0: np.array([10.0, 20.0])}
        )
        data = convert(tmp_path / "x.json", net=net, timeseries=ts)
        lmax = [d["lmax"] for d in data["system"]["demand_array"]]
        assert lmax == ["lmax", 100.0, 125.0]
        assert "capacity_profile_array" not in data["system"]
        assert ts.summary["load_energy_mwh"] == pytest.approx(30.0 + 2 * 225.0)

    def test_snapshot_unchanged_without_timeseries(self, tmp_path: Path) -> None:
        data = convert(tmp_path / "s.json", net=pn.case9(), name="s")
        assert data["system"]["demand_array"][0]["lmax"] == [[90.0]]
        assert "input_directory" not in data["options"]
        assert not (tmp_path / "s").exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str]) -> int:
    return main([*argv, "--no-check"])


class TestCli:
    def test_cli_week_case(
        self, tmp_path: Path, wide_csv: Path, net_json: Path
    ) -> None:
        out = tmp_path / "out" / "c9.json"
        rc = _run_cli(
            [
                "-f",
                str(net_json),
                "--profiles",
                str(wide_csv),
                "--hours",
                "24:48",
                "--block-hours",
                "2",
                "--stage-hours",
                "6",
                "-o",
                str(out),
            ]
        )
        assert rc == 0
        data = json.loads(out.read_text())
        assert len(data["simulation"]["block_array"]) == 12
        assert len(data["simulation"]["stage_array"]) == 4
        assert data["options"]["method"] == "monolithic"
        lmax = _lmax_matrix(out.parent / "c9")
        expected = (90.0 * _SHAPE[24:48]).reshape(12, 2).mean(axis=1)
        np.testing.assert_allclose(lmax[1].to_numpy(), expected)
        state = json.loads((out.parent / "c9" / "pp2gtopt_state.json").read_text())
        assert state["args"]["profiles"] == os.path.relpath(wide_csv, out.parent / "c9")
        assert state["extra"]["summary"]["unmatched_columns"] == ["nomatch"]
        assert (out.parent / "c9" / "README.md").exists()

    def test_from_state_reproduces(
        self, tmp_path: Path, wide_csv: Path, net_json: Path, monkeypatch
    ) -> None:
        out = tmp_path / "a" / "c9.json"
        argv = ["-f", str(net_json), "--profiles", str(wide_csv), "-o", str(out)]
        argv += ["--stage-hours", "24", "--block-hours", "3"]
        assert _run_cli(argv) == 0
        first_json = out.read_text()
        first_lmax = _lmax_matrix(out.parent / "c9")
        first_prof = _read_long(
            out.parent / "c9" / "GeneratorProfile" / "profile.parquet"
        )
        out.unlink()
        shutil.rmtree(out.parent / "c9" / "Demand")
        # Replay from an unrelated cwd: relative paths resolve against the
        # state file's directory.
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        state = out.parent / "c9" / "pp2gtopt_state.json"
        assert _run_cli(["--from-state", str(state)]) == 0
        assert out.read_text() == first_json
        pd.testing.assert_frame_equal(_lmax_matrix(out.parent / "c9"), first_lmax)
        pd.testing.assert_frame_equal(
            _read_long(out.parent / "c9" / "GeneratorProfile" / "profile.parquet"),
            first_prof,
        )

    def test_from_state_override(
        self, tmp_path: Path, wide_csv: Path, net_json: Path
    ) -> None:
        out = tmp_path / "c9.json"
        argv = ["-f", str(net_json), "--profiles", str(wide_csv), "-o", str(out)]
        assert _run_cli(argv) == 0
        state = tmp_path / "c9" / "pp2gtopt_state.json"
        assert _run_cli(["--from-state", str(state), "--block-hours", "4"]) == 0
        data = json.loads(out.read_text())
        assert len(data["simulation"]["block_array"]) == _H // 4

    def test_negative_profile_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "neg.csv"
        pd.DataFrame({"load:0": [1.0, -2.0]}).to_csv(path, index=False)
        rc = _run_cli(
            ["-n", "case9", "--profiles", str(path), "-o", str(tmp_path / "n.json")]
        )
        assert rc == 1

    def test_bad_block_hours_fails(self, tmp_path: Path, wide_csv: Path) -> None:
        rc = _run_cli(
            [
                "-n",
                "case9",
                "--profiles",
                str(wide_csv),
                "--block-hours",
                "5",
                "-o",
                str(tmp_path / "b.json"),
            ]
        )
        assert rc == 1

    def test_bad_hour_range_fails(self, tmp_path: Path, wide_csv: Path) -> None:
        rc = _run_cli(
            [
                "-n",
                "case9",
                "--profiles",
                str(wide_csv),
                "--hours",
                "40:99",
                "-o",
                str(tmp_path / "h.json"),
            ]
        )
        assert rc == 1

    def test_matpower_mat_input(self, tmp_path: Path) -> None:
        from pandapower.converter.matpower.to_mpc import to_mpc  # noqa: PLC0415

        mat = tmp_path / "case9.mat"
        to_mpc(pn.case9(), str(mat), init="flat")
        net = load_network(mat)
        assert len(net.bus) == 9
        assert _run_cli(["--matpower", str(mat), "-o", str(tmp_path / "m.json")]) == 0


# ---------------------------------------------------------------------------
# End-to-end with the gtopt binary (skipped when unavailable)
# ---------------------------------------------------------------------------


def _find_gtopt_binary() -> str | None:
    env_bin = os.environ.get("GTOPT_BIN")
    if env_bin and Path(env_bin).exists():
        return env_bin
    repo_root = Path(__file__).resolve().parents[3]
    for rel in ("build/standalone/gtopt", "build-release/standalone/gtopt"):
        candidate = repo_root / rel
        if candidate.exists():
            return str(candidate)
    return shutil.which("gtopt")


@pytest.mark.integration
def test_e2e_gtopt_lp_only_and_solve(
    tmp_path: Path, wide_csv: Path, net_json: Path
) -> None:
    binary = _find_gtopt_binary()
    if binary is None:
        pytest.skip("gtopt binary not found (set GTOPT_BIN)")
    out = tmp_path / "c9.json"
    argv = ["-f", str(net_json), "--profiles", str(wide_csv), "-o", str(out)]
    assert _run_cli([*argv, "--stage-hours", "24", "--block-hours", "2"]) == 0
    lp_only = subprocess.run(
        [binary, out.name, "--lp-only"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert lp_only.returncode == 0, lp_only.stderr[-2000:]
    solve = subprocess.run(
        [binary, out.name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert solve.returncode == 0, solve.stderr[-2000:]
    sol = pd.read_csv(tmp_path / "output" / "solution.csv")
    assert len(sol) == 2
    assert (sol["status"] == 0).all()
    fails = [pd.read_csv(f) for f in (tmp_path / "output" / "Demand").glob("fail_sol*")]
    assert all(f.empty or f["value"].abs().max() < 1e-6 for f in fails)


def _year_case118(tmp_path: Path, n_hours: int) -> tuple[Path, Path]:
    """case118 + 10 wind / 5 solar sgens and a synthetic hourly year."""
    net = pn.case118()
    rng = np.random.default_rng(7)
    hours = np.arange(n_hours)
    daily = 0.85 + 0.15 * np.sin(2 * np.pi * (hours - 9) / 24)
    season = 1.0 + 0.1 * np.cos(2 * np.pi * hours / 8760)
    cols: dict[str, np.ndarray] = {
        "hour": hours,
        "load@all": float(net.load["p_mw"].sum()) * daily * season,
    }
    for k, bus in enumerate(rng.choice(net.bus.index, 15, replace=False)):
        name = f"W{k}" if k < 10 else f"S{k}"
        pp.create_sgen(net, bus, p_mw=0.0, max_p_mw=150.0, name=name)
        if k < 10:
            noise = 0.1 * rng.standard_normal(n_hours)
            cols[name] = np.clip(0.4 + 0.25 * np.sin(hours / 6 + k) + noise, 0, 1)
        else:
            sun = np.clip(np.sin(np.pi * ((hours % 24) - 6) / 12), 0.0, None)
            cols[f"sgen:{name}"] = 150.0 * sun
    net_path = tmp_path / "case118_res.json"
    pp.to_json(net, str(net_path))
    prof_path = tmp_path / "year.parquet"
    pd.DataFrame(cols).to_parquet(prof_path, index=False)
    return net_path, prof_path


@pytest.mark.slow
def test_full_year_case118(tmp_path: Path) -> None:
    """A full hourly year converts into ONE case: 8760 blocks, 365 daily
    stages / phases, every block carrying load + renewable values.

    The ``gtopt --lp-only`` build (~1 min, ~2 GB) runs only when a gtopt
    binary is found AND ``GTOPT_PP2GTOPT_YEAR_TEST=1`` is set.
    """
    net_path, prof_path = _year_case118(tmp_path, 8760)
    out = tmp_path / "year.json"
    argv = ["-f", str(net_path), "--profiles", str(prof_path)]
    assert _run_cli([*argv, "--stage-hours", "24", "-o", str(out)]) == 0
    data = json.loads(out.read_text())
    sim = data["simulation"]
    assert len(sim["block_array"]) == 8760
    assert len(sim["stage_array"]) == 365
    assert len(sim["phase_array"]) == 365
    lmax = _read_long(tmp_path / "year" / "Demand" / "lmax.parquet")
    assert len(lmax) == 8760 * len(data["system"]["demand_array"])
    prof = _read_long(tmp_path / "year" / "GeneratorProfile" / "profile.parquet")
    assert len(prof) == 8760 * 15
    assert prof["stage"].max() == 365

    binary = _find_gtopt_binary()
    if binary is None or os.environ.get("GTOPT_PP2GTOPT_YEAR_TEST") != "1":
        pytest.skip("year LP build needs GTOPT_BIN + GTOPT_PP2GTOPT_YEAR_TEST=1")
    lp_only = subprocess.run(
        [binary, out.name, "--lp-only"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=1200,
        check=False,
    )
    assert lp_only.returncode == 0, lp_only.stderr[-2000:]
