# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for ``gtopt_check_output --numerics`` (``_numerics``)."""

from __future__ import annotations

import gzip
import json
import math
import stat
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gtopt_check_output import _numerics as nm
from gtopt_check_output import main as cli

LP_TEXT = """\\ File written by HiGHS .lp file handler
min
 obj: -1000 demand_load_1_1 +5 generator_generation_1_1
 +0.05 generator_generation_2_1  +2400000
st
 bus_balance_1_1: -1 demand_load_1_1 +1 generator_generation_1_1
 +1 generator_generation_2_1 = +0
 cap_1_1: +1e-3 generator_generation_1_1 - line_flow_1 <= 250
 rng_1: -5 <= +2 line_flow_1 -1 x <= 7.5
 lim_1: +3 line_flow_1 >= - 2
bounds
 demand_load_1_1 <= 100
 -inf <= line_flow_1 <= 40
 x free
 0.5 <= generator_generation_2_1 <= 1e30
general
 x
end
"""


def test_range_basics() -> None:
    r = nm.Range()
    assert r.decades() == 0.0
    for v in (0.0, math.inf, 1e20, -10.0, 0.1):
        r.add(v)
    assert r.count == 2
    assert r.decades() == pytest.approx(2.0)
    other = nm.Range()
    other.add(1000.0)
    r.merge(other)
    assert r.decades() == pytest.approx(4.0)


def test_family() -> None:
    assert nm.family("generator_output_12_3_4") == "generator_output"
    assert nm.family("x") == "x"
    assert nm.family("12_3") == "12_3"
    assert nm.family("a_-1") == "a"


def test_terms_and_helpers() -> None:
    toks = nm._TOKEN_RE.findall("- x + 2 y -3e-1 z [ q ] ^ 2")
    terms = nm._terms(toks)
    assert terms[0] == (-1.0, "x")
    assert terms[1] == (2.0, "y")
    assert terms[2] == pytest.approx((-0.3, "z"))
    assert ("q" in {n for _, n in terms}) is True
    toks = nm._TOKEN_RE.findall("x >= - 2 <= inf")
    assert nm._number_after(toks, 1) == -2.0
    assert nm._number_after(toks, 4) == math.inf
    assert nm._number_after(["x", "<="], 1) is None
    assert nm._TOKEN_RE.findall("+1e-3 x2e") == ["+", "1e-3", "x2e"]
    assert nm._lhs_tokens(["x", "y"]) == ["x", "y"]
    assert nm._split_statement("a b: c")[0] == ""


def test_analyze_lp_text() -> None:
    lp = nm.analyze_lp_text(LP_TEXT)
    assert lp.files == 1
    # matrix: 1, 1, 1, 1e-3, 1, 2, 1, 3
    assert lp.matrix.lo == pytest.approx(1e-3)
    assert lp.matrix.hi == pytest.approx(3.0)
    assert lp.objective.lo == pytest.approx(0.05)
    assert lp.objective.hi == pytest.approx(1000.0)
    # rhs: 250, -5 (ranged lower), 7.5, -2   (zeros ignored)
    assert lp.rhs.lo == pytest.approx(2.0)
    assert lp.rhs.hi == pytest.approx(250.0)
    # bounds: 100, 40, 0.5 (inf and 1e30 ignored)
    assert lp.bounds.lo == pytest.approx(0.5)
    assert lp.bounds.hi == pytest.approx(100.0)
    assert lp.tiny == 0 and lp.huge == 0
    assert ("cap", "generator_generation") in lp.families


def test_tiny_huge_and_offenders() -> None:
    lp = nm.LpNumerics()
    lp.add_coeff(0.0, "r_1", "c_1")
    lp.add_coeff(1e-12, "r_1", "c_1")
    lp.add_coeff(1e12, "r_1", "c_1")
    lp.add_coeff(2.0, "s_1", "c_1")
    assert lp.tiny == 1 and lp.huge == 1
    lines = nm.offender_lines(lp, 1)
    assert "1.000e+12" in lines[1]
    assert "1.000e-12" in lines[3]
    total = nm.LpNumerics()
    total.merge(lp)
    total.merge(lp)
    assert total.tiny == 2
    assert total.families[("r", "c")][1].mag == pytest.approx(1e12)


def test_analyze_lp_files_gz(tmp_path: Path) -> None:
    (tmp_path / "a.lp").write_text(LP_TEXT)
    (tmp_path / "b.lp.gz").write_bytes(gzip.compress(LP_TEXT.encode()))
    lp = nm.analyze_lp_files([tmp_path / "a.lp", tmp_path / "b.lp.gz"])
    assert lp.files == 2
    assert lp.matrix.count == 16


def _write_solution(out: Path, rows: list[tuple[str, str]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    lines = ["scene,phase,status,status_name,obj_value,kappa"]
    lines += [f"1,{i + 1},{s},x,1.0,{k}" for i, (s, k) in enumerate(rows)]
    (out / "solution.csv").write_text("\n".join(lines) + "\n")


def test_read_solution(tmp_path: Path) -> None:
    run = nm.RunNumerics()
    _write_solution(tmp_path, [("0", "100"), ("0", "1e4"), ("0", "bad")])
    nm.read_solution(tmp_path / "solution.csv", run)
    assert run.metrics["kappa_log10"] == pytest.approx(4.0)
    assert not run.failures

    run = nm.RunNumerics()
    _write_solution(tmp_path, [("2", "-1")])
    nm.read_solution(tmp_path / "solution.csv", run)
    assert "kappa_log10" not in run.metrics
    assert run.failures and run.notes

    run = nm.RunNumerics()
    (tmp_path / "solution.csv").write_text("scene,phase,status\n")
    nm.read_solution(tmp_path / "solution.csv", run)
    assert run.failures == [f"{tmp_path / 'solution.csv'}: no cells"]


def test_read_logs(tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    log.write_text(
        "    iter 1  obj   ub=1  lb=1  gap=1%  Δgap=1% kappa=1.00e+02\n"
        "    iter 2  obj   ub=1  lb=1  gap=1%  Δgap=1% kappa=1.00e+04\n"
        "    iter 3  obj   ub=1  lb=1  gap=1%  Δgap=1%\n"
        "WARNING: model has unscaled infeasibilities\n"
    )
    run = nm.RunNumerics()
    nm.read_logs([log], run, nm.WARNING_PATTERNS)
    assert run.metrics["kappa_growth"] == pytest.approx(2.0)
    assert len(run.failures) == 1
    assert "unscaled infeasibilit" in run.failures[0]


def _write_cuts(path: Path, vals: list[list[float]]) -> None:
    struct = pa.struct(
        [
            ("cls", pa.utf8()),
            ("var", pa.utf8()),
            ("uid", pa.int32()),
            ("val", pa.float64()),
        ]
    )
    coeffs = [
        [{"cls": "R", "var": "e", "uid": 1, "val": v} for v in cut] for cut in vals
    ]
    table = pa.table({"coeffs": pa.array(coeffs, type=pa.list_(struct))})
    pq.write_table(table, path)


def test_read_cuts(tmp_path: Path) -> None:
    run = nm.RunNumerics()
    nm.read_cuts(tmp_path, run)  # no files: nothing
    assert "cut_decades" not in run.metrics
    _write_cuts(tmp_path / "c.parquet", [[1.0, 0.0, 100.0], []])
    nm.read_cuts(tmp_path, run)
    assert run.metrics["cut_decades"] == pytest.approx(2.0)

    run = nm.RunNumerics()
    _write_cuts(tmp_path / "c.parquet", [[0.0]])
    nm.read_cuts(tmp_path, run)
    assert "cut_decades" not in run.metrics


def _work_dir(tmp_path: Path, lp: str = LP_TEXT) -> Path:
    work = tmp_path / "work"
    _write_solution(work / "out", [("0", "1e3")])
    (work / "lp").mkdir(parents=True)
    (work / "lp" / "model.lp").write_text(lp)
    (work / "gtopt.log").write_text("ok\n")
    (work / "out" / "logs").mkdir()
    (work / "out" / "logs" / "gtopt_1.log").write_text(
        "    iter 1  obj   ub=1 kappa=1.0e+03\n"
    )
    (work / "out" / "cuts").mkdir()
    _write_cuts(work / "out" / "cuts" / "sddp_cuts.parquet", [[1.0, 10.0]])
    return work


def test_measure(tmp_path: Path) -> None:
    work = _work_dir(tmp_path)
    run = nm.measure(work, top=2)
    assert set(run.metrics) == {
        "kappa_log10",
        "kappa_growth",
        "matrix_decades",
        "objective_decades",
        "rhs_decades",
        "bounds_decades",
        "cut_decades",
    }
    assert not run.failures
    assert any("largest" in n for n in run.notes)

    empty = tmp_path / "empty"
    (empty / "lp").mkdir(parents=True)
    (empty / "lp" / "m.lp").write_text("min\n obj:\nst\nbounds\nend\n")
    run = nm.measure(empty, top=0)
    assert any("missing" in f for f in run.failures)
    assert any("without matrix entries" in f for f in run.failures)

    bad = tmp_path / "bad"
    _write_solution(bad / "out", [("0", "10")])
    (bad / "lp").mkdir(parents=True)
    (bad / "lp" / "m.lp").write_text(
        "min\n obj: x\nst\n r: 1e-12 x + 1e12 y <= 1\nend\n"
    )
    run = nm.measure(bad, top=0)
    assert any("entries below" in f for f in run.failures)


def test_baselines_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    data = nm.load_baselines(path)
    assert data["cases"] == {}
    run = nm.RunNumerics(metrics={"kappa_log10": 3.004, "matrix_decades": 1.0})
    nm.update_baselines(path, data, "c", "highs", run)
    data = nm.load_baselines(path)
    assert data["cases"]["c"]["highs"]["kappa_log10"] == 3.0

    rows = nm.compare(data, "c", "highs", run)
    assert all(r.ok for r in rows)
    worse = nm.RunNumerics(metrics={"kappa_log10": 4.5, "matrix_decades": 1.2})
    rows = nm.compare(data, "c", "highs", worse)
    assert [r.ok for r in rows] == [False, True]
    table = nm.format_table("c", "highs", rows)
    assert "REGRESSION" in table

    # absolute caps and missing baselines
    data["limits"] = {"matrix_decades": 1.1, "objective_decades": 2.0}
    worse.metrics["objective_decades"] = 3.0
    rows = {r.metric: r for r in nm.compare(data, "c", "highs", worse)}
    assert rows["matrix_decades"].limit == pytest.approx(1.1)
    assert not rows["matrix_decades"].ok
    assert rows["objective_decades"].baseline is None
    assert rows["objective_decades"].limit == pytest.approx(2.0)
    assert "-" in nm.format_table("c", "highs", [nm.Row("m", 1.0, None, None)])

    # a file without the optional sections
    path.write_text(json.dumps({}))
    assert nm.load_baselines(path)["tolerances"] == nm.DEFAULT_TOLERANCES


def test_truncate_phases() -> None:
    planning = {
        "simulation": {
            "phase_array": [
                {"first_stage": 0, "count_stage": 1},
                {"first_stage": 1, "count_stage": 2},
                {"first_stage": 3},
            ],
            "stage_array": [{}, {}, {}, {}, {}],
        }
    }
    nm.truncate_phases(planning, 0)
    assert "active" not in planning["simulation"]["phase_array"][1]
    nm.truncate_phases(planning, 1)
    stages = planning["simulation"]["stage_array"]
    assert [s.get("active", 1) for s in stages] == [1, 0, 0, 0, 0]

    flat = {"simulation": {"stage_array": [{}, {}, {}]}}
    nm.truncate_phases(flat, 2)
    assert [s.get("active", 1) for s in flat["simulation"]["stage_array"]] == [1, 1, 0]


FAKE_GTOPT = """#!{python}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args == ["--solvers"]:
    print("Available LP solvers:")
    print("  highs  (1.16)")
    print("  cplex")
    print("other")
    sys.exit(0)
mode = os.environ.get("FAKE_MODE", "ok")
def opt(name):
    for i, a in enumerate(args):
        if a == "--set" and args[i + 1].startswith(name + "="):
            return args[i + 1].split("=", 1)[1]
    return None
if "--lp-only" in args:
    lp = Path(args[args.index("--lp-file") + 1])
    lp.with_name("model_scene_0_phase_1.lp").write_text({lp!r})
    sys.exit(0)
if mode == "fail":
    print("boom")
    sys.exit(3)
out = Path(opt("output_directory"))
out.mkdir(parents=True, exist_ok=True)
kappa = "1e8" if mode == "worse" else "1e3"
assert os.environ.get("GTOPT_KAPPA_MODE")
(out / "solution.csv").write_text(
    "scene,phase,status,status_name,obj_value,kappa\\n1,1,0,optimal,1," + kappa + "\\n")
"""


@pytest.fixture(name="fake_gtopt")
def fixture_fake_gtopt(tmp_path: Path) -> Path:
    path = tmp_path / "gtopt"
    path.write_text(FAKE_GTOPT.format(python=sys.executable, lp=LP_TEXT))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _case_json(tmp_path: Path, with_input_dir: bool = True) -> Path:
    case = tmp_path / "case"
    case.mkdir(exist_ok=True)
    options = {"input_directory": "inp"} if with_input_dir else {}
    planning = {
        "options": options,
        "simulation": {"stage_array": [{}, {}]},
    }
    path = case / "c.json"
    path.write_text(json.dumps(planning))
    return path


def test_main_run_update_and_check(
    tmp_path: Path, fake_gtopt: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.delenv("GTOPT_KAPPA_MODE", raising=False)
    case_json = _case_json(tmp_path)
    baselines = tmp_path / "bl.json"
    work = tmp_path / "w"
    common = [
        "--case",
        "c",
        "--solver",
        "highs",
        "--work-dir",
        str(work),
        "--baselines",
        str(baselines),
        "--gtopt",
        str(fake_gtopt),
        "--case-json",
        str(case_json),
    ]
    assert (
        nm.main([*common, "--update-baselines", "--phases", "1", "--set", "a=b"]) == 0
    )
    assert (
        json.loads(baselines.read_text())["cases"]["c"]["highs"]["kappa_log10"] == 3.0
    )
    assert (work / "c_phases1.json").is_file()
    assert nm.main([*common, "--top", "2"]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "largest" in out

    monkeypatch.setenv("FAKE_MODE", "worse")
    assert nm.main(common) == 1
    assert "REGRESSION" in capsys.readouterr().out

    monkeypatch.setenv("FAKE_MODE", "fail")
    assert nm.main(common) == 1
    assert "exited with code 3" in capsys.readouterr().out


def test_main_skips_and_errors(tmp_path: Path, fake_gtopt: Path, capsys) -> None:
    case_json = _case_json(tmp_path, with_input_dir=False)
    base = [
        "--case",
        "c",
        "--work-dir",
        str(tmp_path / "w"),
        "--baselines",
        str(tmp_path / "bl.json"),
        "--gtopt",
        str(fake_gtopt),
    ]
    assert nm.main([*base, "--solver", "highs"]) == 2
    assert (
        nm.main([*base, "--solver", "highs", "--case-json", str(tmp_path / "no.json")])
        == nm.SKIP
    )
    assert (
        nm.main([*base, "--solver", "gurobi", "--case-json", str(case_json)]) == nm.SKIP
    )
    assert "not available" in capsys.readouterr().out


def test_cli_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = _work_dir(tmp_path)
    argv = [
        "--numerics",
        "--case",
        "c",
        "--solver",
        "highs",
        "--work-dir",
        str(work),
        "--baselines",
        str(tmp_path / "bl.json"),
    ]
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 0
    monkeypatch.setattr(sys, "argv", ["gtopt_check_output", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
