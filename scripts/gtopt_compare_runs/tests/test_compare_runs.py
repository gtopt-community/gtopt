# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests of gtopt_compare_runs on synthetic gtopt output directories."""

from __future__ import annotations

import io
import json
import runpy
import shutil
import sys
from pathlib import Path
from types import ModuleType
from unittest import mock

import numpy as np
import pandas as pd
import pytest
from rich.console import Console

from gtopt_compare_runs import CompareOptions, Run, compare_runs
from gtopt_compare_runs.engine import element_aggregates, match_names, name_key
from gtopt_compare_runs.main import main
from gtopt_compare_runs.reader import (
    array_of,
    field_kind,
    resolve_planning,
    simulation_from_data,
)
from gtopt_compare_runs.report import render, write_csv, write_json, write_plots
from gtopt_shared.testing.tiny_cases import (
    tiny_planning,
    write_tiny_case,
    write_tiny_results,
)

GEN = ("Generator", "generation_sol.parquet")


def _run_dir(tmp_path: Path, name: str, layout: str = "hive") -> Path:
    """``<tmp>/<name>/{tiny.json, results/}``; returns the output dir."""
    case = tmp_path / name
    write_tiny_case(case)
    return write_tiny_results(case / "results", layout)


def _scale(out: Path, cls: str, fld: str, uid: int, factor: float) -> None:
    path = out / cls / f"{fld}.parquet"
    df = pd.read_parquet(path)
    df.loc[df["uid"] == uid, "value"] *= factor
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    df.to_parquet(path, index=False)


def _solution(out: Path, *rows: tuple[int, int, float]) -> None:
    lines = ["scene,phase,status,obj_value"]
    lines += [f"{s},{p},0,{v}" for s, p, v in rows]
    (out / "solution.csv").write_text("\n".join(lines) + "\n")


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, width=200), buf


# ── self comparison ────────────────────────────────────────────────────


@pytest.mark.parametrize("layout", ["hive", "parquet", "csv"])
def test_self_comparison_is_zero(tmp_path: Path, layout: str) -> None:
    a = _run_dir(tmp_path, "a", layout)
    b = _run_dir(tmp_path, "b", layout)
    rep = compare_runs(Run.load(a), Run.load(b))
    assert rep.passed
    assert rep.streams
    for s in rep.streams:
        assert s.max_abs == 0.0 and s.n_fail == 0, s.key
        assert not s.only_a and not s.only_b
        assert s.total_a == pytest.approx(s.total_b)
    assert rep.reservoirs["diff"].abs().max() == 0.0
    assert not rep.technology.empty


def test_aggregates_are_weighted(tmp_path: Path) -> None:
    run = Run.load(_run_dir(tmp_path, "a"))
    dur = np.array([10.0, 14.0, 12.0, 12.0])
    # TERMO: 10 * scenario + block MW, scenarios equally likely.
    termo = sum(0.5 * (10 * s + b) * dur[b - 1] for s in (1, 2) for b in (1, 2, 3, 4))
    gen = element_aggregates(run, "Generator", "generation_sol")
    assert gen["TERMO"] == pytest.approx(termo)
    lmp = element_aggregates(run, "Bus", "balance_dual")
    assert lmp["Norte220"] == pytest.approx(
        float(np.dot(50.0 + np.arange(1, 5), dur) / dur.sum())
    )
    # per-stage stream: mean of the end-of-stage volume, weighted by stage hours
    efin = element_aggregates(run, "Reservoir", "efin_sol")
    expected = 0.5 * (450 * 24 + 400 * 24) + 0.5 * (480 * 24 + 300 * 24)
    assert efin["LAGO"] == pytest.approx(expected / 48)
    assert element_aggregates(run, "Generator", "nope_sol") == {}


# ── perturbed copy ─────────────────────────────────────────────────────


def test_perturbed_copy_fails_and_ranks(tmp_path: Path) -> None:
    a = _run_dir(tmp_path, "a")
    b = _run_dir(tmp_path, "b")
    _scale(b, "Generator", "generation_sol", 4, 1.1)
    _solution(a, (1, 1, 100.0), (2, 1, 100.0), (1, 2, 40.0))
    _solution(b, (1, 1, 110.0), (2, 1, 100.0))
    rep = compare_runs(Run.load(a, label="A"), Run.load(b, label="B"))
    assert not rep.passed
    assert rep.objective_a == pytest.approx(200.0)
    assert rep.objective_b == pytest.approx(210.0)
    gen = rep.stream("Generator/generation_sol")
    assert gen is not None and not gen.passed
    assert gen.n_fail == 8
    assert gen.elements.iloc[0]["name_a"] == "TERMO"
    assert gen.elements.iloc[0]["rel_diff"] == pytest.approx(0.1 / 1.1)
    assert gen.max_rel == pytest.approx(0.1 / 1.1)
    others = rep.stream("Bus/balance_dual")
    assert others is not None and others.passed
    tech = rep.technology.set_index("technology")
    assert tech.loc["thermal", "diff"] > 0
    assert rep.stream("Nope/x") is None
    # a loose tolerance accepts the change
    loose = compare_runs(Run.load(a), Run.load(b), CompareOptions(rel_tol=0.2))
    assert loose.passed


def test_missing_and_renamed_elements(tmp_path: Path) -> None:
    a = _run_dir(tmp_path, "a")
    b_case = tmp_path / "b"
    plan = tiny_planning()
    for d in plan["system"]["demand_array"]:
        d["name"] = f"d_{d['name']}"
    plan["system"]["generator_array"] = [
        g for g in plan["system"]["generator_array"] if g["name"] != "TERMO"
    ]
    b_case.mkdir()
    (b_case / "tiny.json").write_text(json.dumps(plan))
    b = write_tiny_results(b_case / "results", "hive")
    rep = compare_runs(Run.load(a), Run.load(b))
    load = rep.stream("Demand/load_sol")
    assert load is not None and load.passed and not load.only_a
    assert set(load.elements["name_b"]) == {f"d_{n}" for n in load.elements["name_a"]}
    gen = rep.stream("Generator/generation_sol")
    assert gen is not None
    assert gen.only_a == ["TERMO"] and gen.only_b == ["uid:4"]
    # elements in one run only are reported, not failed
    assert gen.passed


def test_stream_filters_and_skipped_classes(tmp_path: Path) -> None:
    a = _run_dir(tmp_path, "a")
    (a / "Commitment").mkdir()
    pd.DataFrame(
        {"scenario": [1], "stage": [1], "block": [1], "uid": [1], "value": [1.0]}
    ).to_parquet(a / "Commitment" / "status_sol.parquet", index=False)
    (a / "logs").mkdir()
    (a / "logs" / "x.parquet").write_text("")
    run = Run.load(a)
    rep = compare_runs(run, run)
    assert all(s.cls != "Commitment" for s in rep.streams)
    rep = compare_runs(
        run, run, CompareOptions(streams=("Bus", "Generator/generation_sol"))
    )
    assert {s.key for s in rep.streams} == {
        "Bus/balance_dual",
        "Generator/generation_sol",
    }


def test_one_side_empty_stream(tmp_path: Path) -> None:
    a = _run_dir(tmp_path, "a")
    b = _run_dir(tmp_path, "b")
    shutil.rmtree(b / "Reservoir")
    rep = compare_runs(Run.load(a), Run.load(b))
    efin = rep.stream("Reservoir/efin_sol")
    assert efin is not None and efin.only_a == ["LAGO"] and efin.n_cells == 0
    assert rep.reservoirs["efin_b"].isna().all()
    shutil.rmtree(a / "Reservoir")
    rep = compare_runs(Run.load(a), Run.load(b))
    assert rep.reservoirs.empty


# ── names / reader ─────────────────────────────────────────────────────


def test_name_matching() -> None:
    assert name_key("d_Sur 220") == "sur"
    assert name_key("d_Sur") == "sur"
    assert name_key("RAPEL_12") == "rapel"
    assert name_key("tb_") == "tb_"
    m = match_names(["A", "Sur220", "X_1", "X_2"], ["A", "d_sur220", "x"])
    assert m == {"A": "A", "Sur220": "d_sur220"}


def test_reader_helpers(tmp_path: Path) -> None:
    assert field_kind("balance_dual") == "mean"
    assert field_kind("efin_sol") == "mean"
    assert field_kind("generation_sol") == "sum"
    assert array_of("GeneratorProfile") == "generator_profile_array"
    out = _run_dir(tmp_path, "a")
    assert resolve_planning(out, None) is not None
    (out / "planning.json").write_text("[1]")
    assert resolve_planning(out, None) is None
    (out / "planning.json").write_text("{bad")
    assert resolve_planning(out, None) is None
    lone = tmp_path / "lone"
    lone.mkdir()
    assert resolve_planning(lone, None) is None
    with pytest.raises(FileNotFoundError):
        Run.load(tmp_path / "missing")


def test_run_without_planning(tmp_path: Path) -> None:
    out = write_tiny_results(tmp_path / "bare", "hive")
    run = Run.load(out)
    assert run.grid.n_scenarios == 2
    assert len(run.grid.stages) == 2 and len(run.grid.block_uids) == 4
    assert not run.names("Generator")
    cells = run.cells("Generator", "generation_sol")
    assert set(cells["name"]) == {"uid:1", "uid:4", "uid:8"}
    assert run.types() == {}
    rep = compare_runs(run, Run.load(out))
    assert rep.passed
    (out / "Generator" / "notes.txt").write_text("x")
    pd.DataFrame(columns=["scenario", "stage", "block", "uid", "value"]).to_parquet(
        out / "Generator" / "pmax_sol.parquet", index=False
    )
    assert simulation_from_data(out)["scenario_array"][0]["uid"] == 1
    assert ("Generator", "notes") not in Run.load(out).streams()
    named = Run(out, {"system": {"bus_array": [{"uid": "x"}, {"uid": 2}]}})
    assert named.names("Bus") == {2: "uid:2"}
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not simulation_from_data(empty)
    assert Run.load(empty).streams() == []


def test_cells_off_grid_and_objective_edges(tmp_path: Path) -> None:
    out = _run_dir(tmp_path, "a")
    df = pd.DataFrame(
        {
            "scenario": [1, 9],
            "stage": [1, 1],
            "block": [1, 1],
            "uid": [1, 1],
            "value": [2.0, 3.0],
        }
    )
    path = out / "Bus" / "theta_sol.parquet"
    df.to_parquet(path, index=False)
    run = Run.load(out)
    assert len(run.cells("Bus", "theta_sol")) == 1
    pd.DataFrame({"uid": [1], "value": [1.0]}).to_parquet(path, index=False)
    assert Run.load(out).cells("Bus", "theta_sol").empty
    (out / "solution.csv").unlink()
    assert run.objective() is None
    (out / "solution.csv").mkdir()
    assert run.objective() is None
    (out / "solution.csv").rmdir()
    (out / "solution.csv").write_text("scene,status\n1,0\n")
    assert run.objective() is None
    (out / "solution.csv").write_text("")
    assert run.objective() is None
    (out / "solution.csv").write_text("obj_value\n5\n7\n")
    assert run.objective() == pytest.approx(12.0)
    plan = tiny_planning()
    plan["simulation"]["scenario_array"] = [
        {"uid": 1, "probability_factor": 0.0},
        {"uid": 2, "probability_factor": 0.0},
    ]
    zero = Run(out, plan)
    assert zero.weights.tolist() == [0.5, 0.5]


# ── report / CLI ───────────────────────────────────────────────────────


def test_render_and_writers(tmp_path: Path) -> None:
    a = _run_dir(tmp_path, "a")
    b = _run_dir(tmp_path, "b")
    _scale(b, "Bus", "balance_dual", 1, 2.0)
    _solution(a, (1, 1, 1e8))
    rep = compare_runs(Run.load(a, label="A"), Run.load(b, label="B"))
    rep.streams[0].only_a = [f"x{i}" for i in range(12)]
    con, buf = _console()
    render(rep, con, all_streams=True)
    text = buf.getvalue()
    assert "Bus/balance_dual: top 10" in text and "FAIL" in text
    assert "Generation by technology" in text and "x0" in text and "..." in text
    assert "1e+08" in text
    paths = write_csv(rep, tmp_path / "csv")
    assert [p.name for p in paths] == [
        "streams.csv",
        "elements.csv",
        "technology.csv",
        "reservoirs.csv",
    ]
    streams = pd.read_csv(paths[0])
    assert not streams.set_index("stream").loc["Bus/balance_dual", "passed"]
    data = json.loads(write_json(rep, tmp_path / "r" / "rep.json").read_text())
    assert data["passed"] is False and data["objective_b"] == pytest.approx(1.0)
    empty = compare_runs(Run.load(a), Run.load(b), CompareOptions(streams=("Nothing",)))
    write_csv(empty, tmp_path / "csv2")
    empty.technology = empty.technology.iloc[0:0]
    empty.reservoirs = empty.reservoirs.iloc[0:0]
    empty.objective_a = float("nan")
    con, buf = _console()
    render(empty, con)
    text = buf.getvalue()
    assert "PASS" in text and "technology" not in text and "Reservoir" not in text


def test_plots_with_and_without_matplotlib(tmp_path: Path) -> None:
    a = _run_dir(tmp_path, "a")
    rep = compare_runs(Run.load(a), Run.load(a))
    with mock.patch.dict(sys.modules, {"matplotlib": None}):
        assert not write_plots(rep, tmp_path / "p0")
    fake = ModuleType("matplotlib")
    fake.use = mock.MagicMock()  # type: ignore[attr-defined]
    pyplot = ModuleType("matplotlib.pyplot")
    fig, ax = mock.MagicMock(), mock.MagicMock()
    pyplot.subplots = mock.MagicMock(return_value=(fig, ax))  # type: ignore[attr-defined]
    pyplot.close = mock.MagicMock()  # type: ignore[attr-defined]
    fake.pyplot = pyplot  # type: ignore[attr-defined]
    with mock.patch.dict(
        sys.modules, {"matplotlib": fake, "matplotlib.pyplot": pyplot}
    ):
        paths = write_plots(rep, tmp_path / "p1")
        assert [p.name for p in paths] == ["streams.png", "technology.png"]
        empty = compare_runs(Run.load(a), Run.load(a), CompareOptions(streams=("X",)))
        rep.technology = rep.technology.iloc[0:0]
        assert [p.name for p in write_plots(rep, tmp_path / "p2")] == ["streams.png"]
        assert [p.name for p in write_plots(empty, tmp_path / "p3")] == [
            "technology.png"
        ]


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = _run_dir(tmp_path, "a")
    b = _run_dir(tmp_path, "b")
    assert main([str(a), str(b), "--width", "200"]) == 0
    assert "PASS" in capsys.readouterr().out
    _scale(b, "Generator", "generation_sol", 1, 1.5)
    rc = main(
        [
            str(a),
            str(b),
            "--label-a",
            "gtopt",
            "--label-b",
            "other",
            "--streams",
            "Generator, Bus",
            "--csv",
            str(tmp_path / "csv"),
            "--json",
            str(tmp_path / "rep.json"),
            "--plot",
            str(tmp_path / "plots"),
            "--planning-a",
            str(tmp_path / "a" / "tiny.json"),
            "-v",
        ]
    )
    assert rc == 1
    assert (tmp_path / "csv" / "elements.csv").is_file()
    assert json.loads((tmp_path / "rep.json").read_text())["run_a"] == "gtopt"
    assert main([str(a), str(tmp_path / "nope")]) == 2
    assert "not a directory" in capsys.readouterr().err
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main([str(empty), str(empty)]) == 2
    assert "nothing to compare" in capsys.readouterr().err


def test_module_entry(tmp_path: Path) -> None:
    a = _run_dir(tmp_path, "a")
    argv = ["gtopt_compare_runs", str(a), str(a)]
    with mock.patch.object(sys, "argv", argv):
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("gtopt_compare_runs", run_name="__main__")
    assert exc.value.code == 0
