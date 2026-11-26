# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for tools/affected_tests.py — change-based test selection.

Builds a miniature fake repository (C++ headers/sources/tests, Python
packages, guiservice, tools, cases, CI workflow) in a temp dir, commits
it, and checks that each kind of change maps to the expected selection.

Run:  cd tools && python -m pytest test_affected_tests.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

# pylint: disable=wrong-import-position,redefined-outer-name
import affected_tests as at

FILES: dict[str, str] = {
    # ---- C++ library ----
    "include/gtopt/basic_types.hpp": "#pragma once\n",
    "include/gtopt/battery.hpp": "#pragma once\n#include <gtopt/basic_types.hpp>\n",
    "include/gtopt/battery_lp.hpp": "#pragma once\n#include <gtopt/battery.hpp>\n",
    "include/gtopt/system_lp.hpp": "#pragma once\n#include <gtopt/battery_lp.hpp>\n",
    "include/gtopt/sddp_method.hpp": "#pragma once\n",
    "include/gtopt/orphan.hpp": "#pragma once\n",
    "source/battery_lp.cpp": "#include <gtopt/battery_lp.hpp>\n",
    "source/sddp_method_iteration.cpp": "#include <gtopt/sddp_method.hpp>\n",
    "source/system_lp.cpp": "#include <gtopt/system_lp.hpp>\n",
    "source/uses_orphan.cpp": "#include <gtopt/orphan.hpp>\n",
    "source/lonely.cpp": "int x;\n",
    # ---- C++ tests ----
    "test/CMakeLists.txt": "",
    "test/source/main.cpp": "",
    "test/source/fixture_helpers.hpp": "#pragma once\n#include <gtopt/battery.hpp>\n",
    "test/source/test_battery.cpp": '#include "fixture_helpers.hpp"\n',
    "test/source/test_battery_lp.cpp": "#include <gtopt/battery_lp.hpp>\n",
    "test/source/test_system.cpp": "#include <gtopt/system_lp.hpp>\n",
    "test/source/test_sddp_method.cpp": "#include <gtopt/sddp_method.hpp>\n",
    "test/source/test_uses_orphan.cpp": "#include <gtopt/basic_types.hpp>\n",
    "test/source/test_case_c0.cpp": 'auto p = cases_dir / "c0";\n',
    "test/source/json/test_battery_json.cpp": "#include <gtopt/basic_types.hpp>\n",
    # ---- integration ----
    "integration_test/CMakeLists.txt": (
        "add_e2e_case(c0 system_c0.json)\n"
        "# add_e2e_case(commented x.json)\n"
        "add_sddp_case(hydro_iter0 h.json\n  CASE_DIR hydro MAX_ITERATIONS 0)\n"
        "add_pandapower_comparison(c0)\n"
    ),
    "cases/c0/system_c0.json": "{}",
    "cases/hydro/h.json": "{}",
    "standalone/source/main.cpp": "",
    # ---- Python ----
    "scripts/pyproject.toml": (
        "[project]\nname='x'\n\n[project.scripts]\n"
        'alpha = "alpha.main:main"\n'
        'beta  = "beta:main"\n\n[tool.x]\n'
    ),
    "scripts/conftest.py": "",
    "scripts/shared/__init__.py": "",
    "scripts/shared/util.py": "def f():\n    return 1\n",
    "scripts/shared/tests/test_util.py": "from shared.util import f\n",
    "scripts/alpha/__init__.py": "",
    "scripts/alpha/main.py": "from shared import util\n",
    "scripts/alpha/helpers.py": "import os, shared.util\n",
    "scripts/alpha/tests/test_main.py": "import alpha\n",
    "scripts/alpha/tests/conftest.py": "",
    "scripts/beta/__init__.py": "from alpha import main\n",
    "scripts/beta/tests/test_beta.py": "import beta\n",
    "scripts/gamma/__init__.py": "",
    "scripts/gamma/tests/test_gamma.py": "import alpha  # test-only import\n",
    "scripts/delta/__init__.py": "",
    "scripts/delta/tests/test_delta.py": "import gamma\n",
    "scripts/tests/test_cli_help.py": "import alpha, beta\n",
    "scripts/cases/plp_min/plpbar.dat": "",
    "scripts/shared/tests/test_cases.py": 'CASE = Path("cases") / "plp_min"\n',
    # ---- guiservice ----
    "guiservice/app.py": "from shared import util\n",
    "guiservice/tests/test_app.py": "import app\n",
    "guiservice/tests/test_security.py": "",
    # ---- tools ----
    "tools/conftest.py": "",
    "tools/lint_x.py": "",
    "tools/test_lint_x.py": "import lint_x\n",
    "tools/helper.py": "",
    "tools/test_other.py": "import helper\n",
    "tools/no_tests.py": "",
    "tools/validate_solution.py": "",
    # ---- CI ----
    ".github/workflows/scripts.yml": (
        "      - name: pylint\n        run: pylint --jobs=0 alpha shared\n"
        "      - name: mypy\n"
        "        run: mypy alpha beta shared gamma --ignore-missing-imports\n"
    ),
    "docs/index.md": "",
    "README.md": "",
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A committed fake repository."""
    for rel, text in FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(tmp_path, "init", "-q", "-b", "master")
    _git(tmp_path, "add", "-A")
    _git(
        tmp_path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "-m",
        "init",
    )
    return tmp_path


def select(root: Path, *paths: str, threshold: float = 1.0) -> at.Selection:
    """Selection for ``paths`` with fan-out escalation disabled by default."""
    return at.Selector(at.Repo(root), core_threshold=threshold).select(paths)


# ─── C++ unit tests ─────────────────────────────────────────────────────────


def test_changed_test_file_selects_itself(repo: Path) -> None:
    sel = select(repo, "test/source/test_battery.cpp")
    assert list(sel.cpp_unit.targets) == ["test/source/test_battery.cpp"]
    assert not sel.cpp_unit.full
    assert sel.integration.empty


def test_deleted_test_file_is_ignored(repo: Path) -> None:
    sel = select(repo, "test/source/test_gone.cpp")
    assert sel.cpp_unit.empty
    assert "test/source/test_gone.cpp" in sel.ignored


def test_header_selects_transitive_includers(repo: Path) -> None:
    sel = select(repo, "include/gtopt/battery.hpp")
    got = set(sel.cpp_unit.targets)
    # test_battery via fixture_helpers.hpp, test_battery_lp via battery_lp.hpp
    assert {"test/source/test_battery.cpp", "test/source/test_battery_lp.cpp"} <= got
    # test_system only reaches battery.hpp through the system_lp.hpp hub
    assert "test/source/test_system.cpp" not in got
    assert sel.integration.full  # library change -> e2e smoke


def test_test_helper_header(repo: Path) -> None:
    sel = select(repo, "test/source/fixture_helpers.hpp")
    assert list(sel.cpp_unit.targets) == ["test/source/test_battery.cpp"]
    assert sel.integration.empty


def test_source_maps_to_same_stem_header_and_name(repo: Path) -> None:
    sel = select(repo, "source/battery_lp.cpp")
    got = set(sel.cpp_unit.targets)
    assert "test/source/test_battery_lp.cpp" in got
    # name heuristic: battery_lp -> test_battery*
    assert "test/source/test_battery.cpp" in got
    reasons = sel.cpp_unit.targets["test/source/test_battery_lp.cpp"]
    assert any("implements include/gtopt/battery_lp.hpp" in r for r in reasons)


def test_source_maps_to_prefix_header(repo: Path) -> None:
    sel = select(repo, "source/sddp_method_iteration.cpp")
    assert "test/source/test_sddp_method.cpp" in sel.cpp_unit.targets


def test_header_without_test_follows_sources(repo: Path) -> None:
    sel = select(repo, "include/gtopt/orphan.hpp")
    assert "test/source/test_uses_orphan.cpp" in sel.cpp_unit.targets
    assert not sel.cpp_unit.full


def test_unmapped_source_escalates(repo: Path) -> None:
    sel = select(repo, "source/lonely.cpp")
    assert sel.cpp_unit.full
    assert any("no test maps" in r for r in sel.cpp_unit.full_reasons)


def test_core_stem_escalates(repo: Path) -> None:
    for path in ("include/gtopt/system_lp.hpp", "source/system_lp.cpp"):
        sel = select(repo, path)
        assert sel.cpp_unit.full, path
        assert any("core header" in r for r in sel.cpp_unit.full_reasons)


def test_fanout_threshold_escalates(repo: Path) -> None:
    # battery.hpp is reached by 2 of 7 test files (29 %)
    assert not select(repo, "include/gtopt/battery.hpp", threshold=0.5).cpp_unit.full
    sel = select(repo, "include/gtopt/battery.hpp", threshold=0.2)
    assert sel.cpp_unit.full
    assert any("of the test files" in r for r in sel.cpp_unit.full_reasons)


@pytest.mark.parametrize(
    "path",
    [
        "CMakeLists.txt",
        "cmake/CPM.cmake",
        "cmake_local/FastLinker.cmake",
        "test/CMakeLists.txt",
        "test/source/main.cpp",
        "plugins/highs/highs_solver.cpp",
    ],
)
def test_build_files_escalate_cpp(repo: Path, path: str) -> None:
    sel = select(repo, path)
    assert sel.cpp_unit.full and sel.integration.full
    assert sel.python.empty


def test_unknown_test_tree_file_escalates(repo: Path) -> None:
    assert select(repo, "test/data/blob.bin").cpp_unit.full


# ─── integration ────────────────────────────────────────────────────────────


def test_case_dir_selects_registered_ctests(repo: Path) -> None:
    sel = select(repo, "cases/c0/system_c0.json")
    assert set(sel.integration.targets) == {"^e2e_c0_", "^e2e_c0_gtopt_compare"}
    assert "test/source/test_case_c0.cpp" in sel.cpp_unit.targets
    sel = select(repo, "cases/hydro/h.json")
    assert set(sel.integration.targets) == {"^e2e_hydro_iter0_sddp_"}
    assert at.ctest_regex(sel.integration) == "^e2e_hydro_iter0_sddp_"


@pytest.mark.parametrize(
    "path",
    ["integration_test/CMakeLists.txt", "standalone/source/main.cpp"],
)
def test_integration_harness_escalates(repo: Path, path: str) -> None:
    sel = select(repo, path)
    assert sel.integration.full
    assert sel.cpp_unit.empty


def test_validator_tool_escalates_integration(repo: Path) -> None:
    sel = select(repo, "tools/validate_solution.py")
    assert sel.integration.full


# ─── Python ─────────────────────────────────────────────────────────────────


def test_package_change_selects_reverse_deps(repo: Path) -> None:
    sel = select(repo, "scripts/shared/util.py")
    assert list(sel.python.targets) == ["scripts/shared/tests"]
    # alpha imports shared, beta imports alpha (transitive); gamma's
    # *tests* import alpha; scripts/tests imports alpha and beta.
    assert set(sel.python_deps.targets) == {
        "scripts/alpha/tests",
        "scripts/beta/tests",
        "scripts/gamma/tests",
        "scripts/tests",
    }
    # delta's tests import gamma, whose *tests* import alpha: no propagation
    assert "scripts/delta/tests" not in sel.python_deps.targets
    assert "guiservice/tests" in sel.guiservice.targets
    assert set(sel.type_py_packages) == {"shared", "alpha", "beta"}
    assert set(sel.lint_py_packages) == {"shared"}
    assert sel.coverage_packages == ["shared"]
    reasons = sel.python_deps.targets["scripts/beta/tests"]
    assert any("beta -> alpha -> shared" in r for r in reasons)


def test_entry_point_module_selects_cli_help(repo: Path) -> None:
    sel = select(repo, "scripts/alpha/main.py")
    assert "scripts/tests" in sel.python.targets or (
        "scripts/tests/test_cli_help.py" in sel.python.targets
    )
    sel = select(repo, "scripts/alpha/helpers.py")
    assert "scripts/tests/test_cli_help.py" not in sel.python.targets


def test_package_test_file_selects_only_that_file(repo: Path) -> None:
    sel = select(repo, "scripts/alpha/tests/test_main.py")
    assert list(sel.python.targets) == ["scripts/alpha/tests/test_main.py"]
    assert at.pytest_args(sel.python) == ["alpha/tests/test_main.py"]
    assert not sel.coverage_packages


def test_test_file_folds_into_selected_dir(repo: Path) -> None:
    sel = select(repo, "scripts/alpha/tests/test_main.py", "scripts/alpha/helpers.py")
    assert "scripts/alpha/tests/test_main.py" not in sel.python.targets
    assert "scripts/alpha/tests" in sel.python.targets
    assert "alpha" in sel.coverage_packages


def test_package_conftest_selects_package_tests(repo: Path) -> None:
    sel = select(repo, "scripts/alpha/tests/conftest.py")
    assert list(sel.python.targets) == ["scripts/alpha/tests"]


@pytest.mark.parametrize(
    "path", ["scripts/pyproject.toml", "scripts/conftest.py", "pyproject.toml"]
)
def test_python_config_escalates(repo: Path, path: str) -> None:
    sel = select(repo, path)
    assert sel.python.full and sel.python_deps.full
    assert sel.cpp_unit.empty


def test_python_threshold_escalates_dependents(repo: Path) -> None:
    sel = at.Selector(at.Repo(repo), py_full_threshold=0.5).select(
        ["scripts/shared/util.py"]
    )
    assert sel.python_deps.full and not sel.python.full
    assert sel.coverage_packages == ["shared"]
    sel = select(repo, "scripts/pyproject.toml")
    assert sel.python_deps.full
    assert sel.coverage_packages == ["alpha", "beta", "delta", "gamma", "shared"]


def test_scripts_cases_selects_referencing_tests(repo: Path) -> None:
    sel = select(repo, "scripts/cases/plp_min/plpbar.dat")
    assert list(sel.python.targets) == ["scripts/shared/tests/test_cases.py"]


def test_scripts_tests_dir(repo: Path) -> None:
    sel = select(repo, "scripts/tests/test_cli_help.py")
    assert list(sel.python.targets) == ["scripts/tests/test_cli_help.py"]


# ─── guiservice / tools ─────────────────────────────────────────────────────


def test_guiservice(repo: Path) -> None:
    assert list(select(repo, "guiservice/app.py").guiservice.targets) == [
        "guiservice/tests"
    ]
    sel = select(repo, "guiservice/tests/test_security.py")
    assert list(sel.guiservice.targets) == ["guiservice/tests/test_security.py"]


def test_tools_mapping(repo: Path) -> None:
    assert list(select(repo, "tools/lint_x.py").tools.targets) == [
        "tools/test_lint_x.py"
    ]
    assert list(select(repo, "tools/helper.py").tools.targets) == [
        "tools/test_other.py"
    ]
    assert select(repo, "tools/no_tests.py").tools.empty
    sel = select(repo, "tools/conftest.py")
    assert set(sel.tools.targets) == {"tools/test_lint_x.py", "tools/test_other.py"}


# ─── misc ───────────────────────────────────────────────────────────────────


def test_docs_are_ignored(repo: Path) -> None:
    sel = select(repo, "docs/index.md", "README.md", "webservice/x.ts")
    for bucket in (sel.cpp_unit, sel.integration, sel.python, sel.tools):
        assert bucket.empty
    assert len(sel.ignored) == 3


def test_workflow_change_escalates_everything(repo: Path) -> None:
    sel = select(repo, ".github/workflows/scripts.yml")
    assert sel.cpp_unit.full and sel.python.full and sel.integration.full


def test_lint_lists(repo: Path) -> None:
    sel = select(
        repo,
        "source/battery_lp.cpp",
        "include/gtopt/battery.hpp",
        "scripts/beta/__init__.py",
    )
    assert sel.lint_cpp == ["include/gtopt/battery.hpp", "source/battery_lp.cpp"]
    assert at.tidy_files(sel.lint_cpp) == ["source/battery_lp.cpp"]
    assert sel.lint_py_files == ["scripts/beta/__init__.py"]
    # beta is not in the CI pylint list
    assert not sel.lint_py_packages
    assert set(sel.type_py_packages) == {"beta"}


def test_ci_python_lists_one_line_run(repo: Path) -> None:
    pylint, mypy = at.Repo(repo).ci_python_lists()
    assert pylint == {"alpha", "shared"}
    assert mypy == {"alpha", "beta", "shared", "gamma"}


def test_ci_python_lists_run_block(tmp_path: Path) -> None:
    """``run: |`` blocks: the literal full list, not the ``$VAR`` line."""
    wf = tmp_path / ".github" / "workflows" / "scripts.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text(
        "      - name: pylint\n"
        "        run: |\n"
        '          if [ "$MODE" = "full" ]; then\n'
        "            pylint --jobs=0 alpha shared\n"
        "          else\n"
        "            pylint --jobs=0 $PYLINT\n"
        "          fi\n"
        "      - name: mypy\n"
        "        run: |\n"
        "            mypy $MYPY --ignore-missing-imports\n"
        "            mypy alpha beta --ignore-missing-imports\n",
        encoding="utf-8",
    )
    pylint, mypy = at.Repo(tmp_path).ci_python_lists()
    assert pylint == {"alpha", "shared"}
    assert mypy == {"alpha", "beta"}


def test_ci_python_lists_absent(tmp_path: Path) -> None:
    wf = tmp_path / ".github" / "workflows" / "scripts.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text("      - name: pylint\n        run: pylint $PYLINT\n")
    assert at.Repo(tmp_path).ci_python_lists() == (None, None)


def test_command_packages_bare_shell_line() -> None:
    text = "Lint:\n\n```bash\npylint --jobs=0 alpha beta\nmypy gamma --strict\n```\n"
    assert at.command_packages(text, "pylint") == {"alpha", "beta"}
    assert at.command_packages(text, "mypy") == {"gamma"}
    assert at.command_packages(text, "ruff") is None


def test_real_repo_python_lists() -> None:
    """The repo's own scripts.yml (and the agent notes, when present)
    yield non-empty lists."""
    root = Path(__file__).resolve().parent.parent
    repo = at.Repo(root)
    lists = [repo.ci_python_lists()]
    if (root / "CLAUDE.md").is_file():
        lists.append(repo.doc_python_lists())
    for pylint, mypy in lists:
        assert pylint and "plp2gtopt" in pylint
        assert mypy and "gtopt_shared" in mypy


def test_sf_filter() -> None:
    assert (
        at.sf_filter(["test/source/test_b.cpp", "test/source/json/test_a.cpp"])
        == "*/source/json/test_a.cpp,*/source/test_b.cpp"
    )


# ─── git + CLI ──────────────────────────────────────────────────────────────


def _commit_change(repo: Path, rel: str, text: str) -> None:
    (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c")


def test_changed_files_from_git(repo: Path) -> None:
    _git(repo, "branch", "base")
    _commit_change(repo, "scripts/beta/__init__.py", "# edit\n")
    (repo / "scripts/gamma/new.py").write_text("", encoding="utf-8")
    assert at.changed_files(repo, "base", "HEAD", False, False) == [
        "scripts/beta/__init__.py"
    ]
    assert at.changed_files(repo, "base", "HEAD", True, True) == [
        "scripts/beta/__init__.py",
        "scripts/gamma/new.py",
    ]
    with pytest.raises(RuntimeError):
        at.changed_files(repo, "nope", "HEAD", False, False)


def test_cli_formats(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _git(repo, "branch", "base")
    _commit_change(repo, "test/source/test_battery.cpp", "// edit\n")
    base = ["--repo", str(repo), "--base", "base"]
    assert at.main([*base, "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["cpp_unit"]["targets"] == {
        "test/source/test_battery.cpp": ["test file changed"]
    }
    assert data["totals"]["cpp_test_files"] == 7

    assert at.main([*base, "--format", "shell"]) == 0
    out = capsys.readouterr().out
    assert "AFFECTED_CPP=subset" in out
    assert "AFFECTED_CPP_SF='*/source/test_battery.cpp'" in out
    assert "AFFECTED_PY=none" in out

    assert at.main([*base, "--format", "github"]) == 0
    out = capsys.readouterr().out
    assert "cpp=subset" in out.splitlines()
    assert "cpp_sf=*/source/test_battery.cpp" in out.splitlines()

    summary = repo / "summary.md"
    assert at.main([*base, "-v", "--summary", str(summary)]) == 0
    out = capsys.readouterr().out
    assert "<- test file changed" in out
    assert "gtoptTests -sf=" in out
    assert "| C++ unit | subset | 1 / 7 |" in summary.read_text(encoding="utf-8")

    assert at.main([*base, "--full", "--format", "github"]) == 0
    assert "cpp=full" in capsys.readouterr().out.splitlines()

    assert at.main(["--repo", str(repo), "--base", "nope"]) == 2


def test_cli_text_shows_escalation(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert at.main(["--repo", str(repo), "--files", "CMakeLists.txt"]) == 0
    out = capsys.readouterr().out
    assert "ESCALATED TO FULL: CMakeLists.txt: top-level build script" in out


def test_markdown_lists_reasons(repo: Path) -> None:
    sel = select(repo, "scripts/shared/util.py", "CMakeLists.txt")
    md = at.render_markdown(sel)
    assert "**full**: CMakeLists.txt" in md
    assert "`scripts/alpha/tests`" in md


def test_dry_run(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = at.main(
        [
            "--repo",
            str(repo),
            "--files",
            "test/source/test_battery.cpp",
            "scripts/alpha/helpers.py",
            "guiservice/app.py",
            "tools/lint_x.py",
            "--run",
            "--lint",
            "--dry-run",
            "-B",
            "bld",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "cmake --build" in out and "--target gtoptTests" in out
    assert "pytest" in out and "alpha/tests" in out
    assert "guiservice/tests" in out
    assert "tools/test_lint_x.py" in out
    assert "clang-format --dry-run -Werror test/source/test_battery.cpp" in out
    assert "tools/run_tidy.sh -p bld test/source/test_battery.cpp" in out
    assert "pylint --jobs=0 alpha" in out
    assert "ruff check guiservice/app.py scripts/alpha/helpers.py" in out


def test_filter_coverage(tmp_path: Path) -> None:
    src = tmp_path / "cov.json"
    src.write_text(
        json.dumps(
            {
                "meta": {},
                "files": {
                    "alpha/main.py": {"summary": {}},
                    "./beta/x.py": {"summary": {}},
                    "gamma/y.py": {"summary": {}},
                },
            }
        ),
        encoding="utf-8",
    )
    dst = tmp_path / "out.json"
    rc = at.main(
        ["--filter-coverage", str(src), str(dst), "--coverage-packages", "alpha,beta"]
    )
    assert rc == 0
    files = json.loads(dst.read_text(encoding="utf-8"))["files"]
    assert set(files) == {"alpha/main.py", "./beta/x.py"}


def test_python_commands_engines(repo: Path) -> None:
    sel = select(repo, "scripts/shared/util.py", "tools/conftest.py")
    static = at.python_commands(sel, "static", "4")
    assert [c.label for c in static] == [
        "pytest scripts (direct)",
        "pytest scripts (dependents, static)",
        "pytest guiservice",
        "pytest tools",
    ]
    assert static[0].argv[-1] == "shared/tests"
    assert "-n" in static[0].argv and "--testmon" not in static[0].argv
    testmon = at.python_commands(sel, "testmon")
    assert "--testmon" in testmon[0].argv
    assert "--testmon-forceselect" not in testmon[0].argv
    assert "--testmon-forceselect" in testmon[1].argv
    # 5 of 6 test dirs touched -> dependents run as the full testpaths
    assert sel.python_deps.full
    assert testmon[1].argv[-1] == "--testmon-forceselect"
    assert testmon[3].cwd == "tools"
    assert testmon[3].shell().startswith("(cd tools && python -m pytest")
    full = at.python_commands(select(repo, "scripts/conftest.py"), "testmon")
    assert [c.label for c in full] == ["pytest scripts (full)"]
    assert "--testmon" in full[0].argv


def test_tools_full_runs_every_tools_test(repo: Path) -> None:
    sel = select(repo, ".github/actions/foo/action.yml")
    cmd = at.python_commands(sel, "static")[-1]
    assert cmd.label == "pytest tools"
    assert cmd.argv[-2:] == ["test_lint_x.py", "test_other.py"]


def test_resolve_engine(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert at.resolve_engine("static", repo) == "static"
    assert at.resolve_engine("testmon", repo) == "testmon"
    monkeypatch.setattr(at.importlib.util, "find_spec", lambda name: object())
    assert at.resolve_engine("auto", repo) == "static"  # no DB yet
    (repo / "scripts/.testmondata").write_bytes(b"")
    assert at.resolve_engine("auto", repo) == "testmon"
    monkeypatch.setattr(at.importlib.util, "find_spec", lambda name: None)
    assert at.resolve_engine("auto", repo) == "static"


def test_parse_doctest_list() -> None:
    out = (
        "[doctest] doctest version is 2.5.0\n"
        "===============================================================\n"
        "[doctest] listing all test case names\n"
        "===============================================================\n"
        "Battery a\nBattery (b, c)\n"
        "===============================================================\n"
        "[doctest] unskipped test cases passing the current filters: 2\n"
    )
    assert at.parse_doctest_list(out) == ["Battery a", "Battery (b, c)"]


def test_select_integration(repo: Path) -> None:
    names = [
        "Battery a",
        "e2e_c0_solve",
        "e2e_hydro_iter0_sddp_solve",
        "cli_version",
        "scripts-alpha-unit",
        "scripts-plp2gtopt-min_1bus",
    ]
    unit = {"Battery a"}
    sel = select(repo, "cases/c0/system_c0.json")
    assert at.select_integration(sel, names, unit) == ["e2e_c0_solve"]
    sel = select(repo, "standalone/source/main.cpp")
    assert at.select_integration(sel, names, unit) == [
        "e2e_c0_solve",
        "e2e_hydro_iter0_sddp_solve",
        "cli_version",
        "scripts-plp2gtopt-min_1bus",
    ]
    assert at.select_integration(select(repo, "README.md"), names, unit) == []


def test_suites_and_ctest_args(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files = ["test/source/test_battery.cpp", "scripts/alpha/helpers.py"]
    base = ["--repo", str(repo), "--files", *files, "--run", "--dry-run"]
    assert at.main([*base, "--suites", "python"]) == 0
    out = capsys.readouterr().out
    assert "cmake --build" not in out and "alpha/tests" in out
    assert at.main([*base, "--suites", "cpp", "--no-build"]) == 0
    out = capsys.readouterr().out
    assert "pytest" not in out.split("commands:")[1].split("+ ")[-1]
    assert "ctest --tests-from-file" in out


def test_full_without_files(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        at.main(["--repo", str(repo), "--files", "--full", "--format", "github"]) == 0
    )
    out = capsys.readouterr().out.splitlines()
    assert "cpp=full" in out and "py=full" in out and "py_deps=full" in out


def test_split_registered_and_case_filter() -> None:
    unit = ["a", "b; c", "a", "d, e"]
    in_ctest, direct = at.split_registered(unit, ["a", "d, e", "x"])
    assert in_ctest == ["a", "d, e"]
    assert direct == ["b; c"]
    assert at.doctest_case_filter(["x, y", "z"]) == "x\\, y,z"


def test_direct_file_inside_dependent_dir_is_promoted(repo: Path) -> None:
    sel = select(repo, "scripts/shared/util.py", "scripts/alpha/tests/test_main.py")
    assert "scripts/alpha/tests" in sel.python.targets
    assert "scripts/alpha/tests/test_main.py" not in sel.python.targets
    assert "scripts/alpha/tests" not in sel.python_deps.targets
