# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for gtopt_check_cases (a fake gtopt binary, no solver)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from gtopt_check_cases import checks
from gtopt_check_cases.main import main

#: Fake gtopt: on --lp-only writes <-l path>.scales.json with fixed
#: dimensions; otherwise writes a solution.csv into the cwd.  Records
#: its argv in args.txt.
FAKE_GTOPT = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
argv = sys.argv[1:]
Path("args.txt").write_text("\\n".join(argv))
if "--lp-only" in argv:
    stem = Path(argv[argv.index("-l") + 1])
    Path(str(stem) + ".lp").write_text("lp")
    Path(str(stem) + ".scales.json").write_text(json.dumps(
        {"num_rows": 10, "num_cols": 20, "numerics": {"nnz": 30},
         "gtopt": {"git_revision": "abc123"}}))
else:
    Path("solution.csv").write_text(
        "scene,phase,status,status_name,obj_value,kappa,scale_objective\\n"
        "0,0,0,optimal,105.0,12.5,1000\\n")
"""


@pytest.fixture
def fake_gtopt(tmp_path: Path) -> str:
    exe = tmp_path / "bin" / "gtopt"
    exe.parent.mkdir()
    exe.write_text(FAKE_GTOPT)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "cases"
    (root / "a" / "case").mkdir(parents=True)
    (root / "a" / "case" / "a.json").write_text("{}")
    (root / "profiles").mkdir()
    (root / "profiles" / "p.json").write_text("{}")
    cat = {
        "cases": [
            {
                "id": "x/a",
                "path": "a",
                "application": "daily_scheduling",
                "case_json": "case/a.json",
                "profile": "profiles/p.json",
                "size": {"lp": {"lps": 1, "rows": 10, "cols": 20, "nnz": 30}},
            },
            {"id": "x/todo", "path": "todo", "placeholder": True},
            {
                "id": "y/b",
                "path": "b",
                "application": "medium_long_term",
                "case_json": "b.json",
                "size": {"lp": {"rows": 1, "cols": 2, "nnz": 3}},
            },
        ]
    }
    (root / "catalog.json").write_text(json.dumps(cat))
    return root


def test_select_cases_filters(repo: Path) -> None:
    cat = checks.load_catalog(repo)
    assert [c["id"] for c in checks.select_cases(cat)] == ["x/a", "y/b"]
    assert [c["id"] for c in checks.select_cases(cat, only=["b"])] == ["y/b"]
    sel = checks.select_cases(cat, application="daily_scheduling")
    assert [c["id"] for c in sel] == ["x/a"]


def test_outside_refuses_collection_paths(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        checks.outside(repo, repo / "a" / "output")
    assert checks.outside(repo, tmp_path / "out") == (tmp_path / "out").resolve()


def test_gtopt_command_layout(tmp_path: Path) -> None:
    case = tmp_path / "c" / "c.json"
    spec = checks.RunSpec("gtopt", threads=4, cpus="0-3", sets=["a=1"])
    cmd = checks.gtopt_command(spec, case, None, tmp_path / "o", lp_only=True)
    assert cmd[:4] == ["taskset", "-c", "0-3", "gtopt"]
    assert f"input_directory={case.resolve().parent}" in cmd
    assert "solver_options.threads=4" in cmd and "a=1" in cmd
    assert "--lp-only" in cmd


def test_lp_dims_sums_sidecars(tmp_path: Path) -> None:
    for i in range(2):
        (tmp_path / f"model_{i}.scales.json").write_text(
            json.dumps({"num_rows": 3, "num_cols": 4, "numerics": {"nnz": 5}})
        )
    dims = checks.lp_dims(tmp_path)
    assert dims == {
        "lps": 2,
        "rows": 6,
        "cols": 8,
        "nnz": 10,
        "gtopt_revision": None,
    }
    assert checks.lp_dims(tmp_path / "missing") is None
    assert checks.lp_matches(dims, {"rows": 6, "cols": 8, "nnz": 10})
    assert checks.lp_matches(dims, {"rows": 6, "cols": 8, "nnz": 11}) is False
    assert checks.lp_matches(None, {"rows": 1}) is None


def test_main_lp_only_and_update(repo, fake_gtopt, tmp_path, capsys) -> None:
    rc = main(
        [
            "--repo",
            str(repo),
            "--gtopt",
            fake_gtopt,
            "--workdir",
            str(tmp_path / "work"),
            "--update",
        ]
    )
    out = capsys.readouterr().out
    # y/b has a stale size in the catalog -> reported as a problem.
    assert rc == 1
    assert "x/a" in out and "match=True" in out and "match=False" in out
    cat = json.loads((repo / "catalog.json").read_text())
    sizes = {c["id"]: c.get("size", {}).get("lp") for c in cat["cases"]}
    assert sizes["y/b"]["rows"] == 10
    assert sizes["y/b"]["gtopt_revision"] == "abc123"
    assert not list((tmp_path / "work").rglob("*.lp"))


def test_main_solve_compares_reference(repo, fake_gtopt, tmp_path, capsys) -> None:
    refs = tmp_path / "refs.json"
    refs.write_text(
        json.dumps({"cases": [{"id": "x/a", "reference": {"objective": 100.0}}]})
    )
    out_root = tmp_path / "results"
    rc = main(
        [
            "--repo",
            str(repo),
            "--gtopt",
            fake_gtopt,
            "--only",
            "x/a",
            "--solve",
            "--out",
            str(out_root),
            "--results",
            str(refs),
            "--rtol",
            "0.1",
            "--workdir",
            str(tmp_path / "work"),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "status=optimal" in out and "obj_match=True" in out
    args = (out_root / "x" / "a" / "output" / "args.txt").read_text().splitlines()
    assert args[1].endswith("profiles/p.json")


def test_main_refuses_out_inside_repo(repo, fake_gtopt, tmp_path) -> None:
    with pytest.raises(ValueError):
        main(
            [
                "--repo",
                str(repo),
                "--gtopt",
                fake_gtopt,
                "--solve",
                "--out",
                str(repo / "results"),
                "--workdir",
                str(tmp_path / "w"),
            ]
        )


def test_main_without_catalog(tmp_path: Path) -> None:
    assert main(["--repo", str(tmp_path), "--gtopt", "gtopt"]) == 2
