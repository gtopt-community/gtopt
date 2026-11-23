# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for gtopt_check_cases (pure helpers + CLI guards)."""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path

import pytest

from gtopt_check_cases import checks

# ``gtopt_check_cases.main`` the function shadows the submodule attribute.
main_mod = importlib.import_module("gtopt_check_cases.main")


def test_gtopt_command_without_threads(tmp_path: Path) -> None:
    spec = checks.RunSpec("gtopt", 0, None, [])
    cmd = checks.gtopt_command(spec, tmp_path / "c.json", None, tmp_path / "o")
    assert not any("solver_options.threads" in c for c in cmd)
    assert cmd[0] == "gtopt"


def test_read_solution_missing_empty_and_bad_numbers(tmp_path: Path) -> None:
    assert checks.read_solution(tmp_path) is None
    (tmp_path / "solution.csv").write_text("", encoding="utf-8")
    assert checks.read_solution(tmp_path) is None
    (tmp_path / "solution.csv").write_text(
        "scene,phase,status,obj_value,kappa,scale_objective\n0,0,1,5.0,bad,1\n",
        encoding="utf-8",
    )
    sol = checks.read_solution(tmp_path)
    assert sol is not None
    assert sol["status"] == "1"
    assert sol["kappa"] is None
    assert sol["gap"] is None


def test_check_case_solve_without_reference(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    (repo / "a").mkdir(parents=True)
    rcs = iter([0, 3])
    monkeypatch.setattr(checks, "run_gtopt", lambda *a, **k: next(rcs))
    res = checks.check_case(
        repo,
        {"id": "x/a", "path": "a", "case_json": "a.json"},
        checks.RunSpec("gtopt", 1, None, []),
        tmp_path / "work",
        solve_dir=tmp_path / "solve",
        keep=True,
    )
    assert res["lp"] is None
    assert "lp_match" not in res
    assert res["solve_rc"] == 3
    assert res["solution"] is None
    assert "obj_match" not in res
    assert checks.has_problem(res)
    line = checks.format_result(res)
    assert "status=None" in line


def test_check_case_records_lp_match(tmp_path: Path, monkeypatch) -> None:
    dims = {"lps": 1, "rows": 1, "cols": 2, "nnz": 3}
    monkeypatch.setattr(checks, "run_gtopt", lambda *a, **k: 0)
    monkeypatch.setattr(checks, "lp_dims", lambda _p: dict(dims))
    res = checks.check_case(
        tmp_path / "repo",
        {"id": "x", "path": "a", "case_json": "a.json", "size": {"lp": dims}},
        checks.RunSpec("gtopt", 1, None, []),
        tmp_path / "work",
    )
    assert res["lp_match"] is True


def test_check_case_clears_stale_workdir(tmp_path: Path, monkeypatch) -> None:
    stale = tmp_path / "work" / "x__a"
    stale.mkdir(parents=True)
    (stale / "old.txt").write_text("x")
    monkeypatch.setattr(checks, "run_gtopt", lambda *a, **k: 0)
    res = checks.check_case(
        tmp_path / "repo",
        {"id": "x/a", "path": "a", "case_json": "a.json"},
        checks.RunSpec("gtopt", 1, None, []),
        tmp_path / "work",
    )
    assert res["lp"] is None
    assert not stale.exists()


def test_cpu_slot_variants() -> None:
    ns = argparse.Namespace(cpus="0-3", cpus_base=None, threads=4)
    assert main_mod._cpu_slot(2, ns) == "0-3"
    ns = argparse.Namespace(cpus=None, cpus_base=8, threads=4)
    assert main_mod._cpu_slot(2, ns) == "16-19"
    ns = argparse.Namespace(cpus=None, cpus_base=None, threads=4)
    assert main_mod._cpu_slot(0, ns) is None


def test_main_without_gtopt_binary(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "catalog.json").write_text(json.dumps({"cases": []}))
    monkeypatch.delenv("GTOPT_BIN", raising=False)
    monkeypatch.setattr(main_mod.shutil, "which", lambda _name: None)
    assert main_mod.main(["--repo", str(tmp_path)]) == 2
    assert "gtopt binary not found" in capsys.readouterr().err


@pytest.mark.parametrize("rc", [0])
def test_has_problem_clean(rc: int) -> None:
    assert not checks.has_problem({"lp_rc": rc})
