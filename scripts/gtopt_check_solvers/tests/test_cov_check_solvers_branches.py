# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch tests for gtopt_check_solvers (fake gtopt, no solver is run)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gtopt_check_solvers import _solver_tests
from gtopt_check_solvers import gtopt_check_solvers as cli
from gtopt_check_solvers._solver_tests import (
    SolverTestReport,
    SolverTestResult,
    _parse_cpp_check_output,
    _run_builtin_solver_tests,
    _run_one_test,
)

_PLAN: dict[str, Any] = {"options": {}}


def _fake_gtopt(
    monkeypatch: pytest.MonkeyPatch,
    *,
    csv_text: str | None = None,
    exc: BaseException | None = None,
) -> None:
    def _run(cmd: list[str], **_kw: Any) -> SimpleNamespace:
        if exc is not None:
            raise exc
        with open(cmd[1], encoding="utf-8") as fh:
            out = Path(json.load(fh)["options"]["output_directory"])
        if csv_text is not None:
            out.mkdir(parents=True, exist_ok=True)
            (out / "solution.csv").write_text(csv_text, encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_solver_tests.subprocess, "run", _run)


@pytest.mark.parametrize(
    ("exc", "message"),
    [
        (subprocess.TimeoutExpired(cmd="gtopt", timeout=5), "timed out after 5s"),
        (FileNotFoundError("gtopt"), "gtopt binary not found"),
    ],
)
def test_run_one_test_process_errors(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException, message: str
) -> None:
    _fake_gtopt(monkeypatch, exc=exc)
    res = _run_one_test("gtopt", "clp", "t", _PLAN, timeout=5)
    assert not res.passed
    assert res.message == message


def test_run_one_test_bad_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_gtopt(monkeypatch, csv_text="status,obj_value\nabc,1\n")
    res = _run_one_test("gtopt", "clp", "t", _PLAN)
    assert not res.passed
    assert "unexpected solution.csv values" in res.message


def test_builtin_tests_expected_status_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_one(*_a: Any, **_k: Any) -> SolverTestResult:
        return SolverTestResult(name="x", passed=True, message="ok", status=7)

    monkeypatch.setattr(_solver_tests, "_run_one_test", _fake_one)
    name = _solver_tests.BUILTIN_TESTS[0][0]
    report = _run_builtin_solver_tests("gtopt", "clp", test_names=[name])
    assert len(report.results) == 1
    assert not report.results[0].passed
    assert report.results[0].message.startswith("expected status")


def test_parse_cpp_output_skips_summary_in_details() -> None:
    out = "  ✗ bad_test   0.010s\n  reason here\n  3 passed, 1 failed\n\n"
    report = _parse_cpp_check_output(out, "clp")
    assert report.results[0].details.strip() == "reason here"


# ---------------------------------------------------------------------------
# CLI printing / main
# ---------------------------------------------------------------------------


class _Out:
    """Captures everything printed through the shared Rich console."""

    def __init__(self) -> None:
        self.text = ""

    def print(self, *args: Any, **_kw: Any) -> None:
        self.text += " ".join(str(a) for a in args) + "\n"


@pytest.fixture(name="out")
def _out(monkeypatch: pytest.MonkeyPatch) -> _Out:
    rec = _Out()
    monkeypatch.setattr(cli, "console", lambda: rec)
    monkeypatch.setattr(cli, "_init_terminal", lambda **_kw: None)
    return rec


def test_print_solver_list_empty(out: _Out) -> None:
    cli._print_solver_list([], "/bin/gtopt")  # pylint: disable=protected-access
    text = out.text
    assert "No LP solver plugins found" in text
    assert "GTOPT_PLUGIN_DIR" in text


def test_print_test_result_variants(out: _Out) -> None:
    cli._print_test_result(  # pylint: disable=protected-access
        SolverTestResult(name="a", passed=True, message="ok"), verbose=False
    )
    cli._print_test_result(  # pylint: disable=protected-access
        SolverTestResult(name="b", passed=False, message="bad", details="l1\nl2"),
        verbose=True,
    )
    cli._print_test_result(  # pylint: disable=protected-access
        SolverTestResult(name="c", passed=False, message="no details"), verbose=True
    )
    assert "obj=" not in out.text
    assert "l1" in out.text
    assert "l2" in out.text


@pytest.fixture(name="fake_bin")
def _fake_bin(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "find_gtopt_binary", lambda: "/bin/gtopt")
    monkeypatch.setattr(cli, "list_available_solvers", lambda _b: ["clp"])

    def _check(gtopt_bin: str, solvers: list[str], **kw: Any) -> int:
        seen.update(bin=gtopt_bin, solvers=solvers, **kw)
        return 0

    monkeypatch.setattr(cli, "check_solvers", _check)
    return seen


def test_main_verbose_maps_to_debug(fake_bin: dict[str, Any]) -> None:
    assert cli.main(["--verbose", "-s", "clp"]) == 0
    assert fake_bin["verbose"] is True
    assert fake_bin["solvers"] == ["clp"]


def test_main_bin_not_found(monkeypatch: pytest.MonkeyPatch, out: _Out) -> None:
    monkeypatch.setattr(cli, "find_gtopt_binary", lambda: None)
    assert cli.main([]) == 2
    assert "gtopt binary not found" in out.text


def test_main_explicit_bin_ok(fake_bin: dict[str, Any], tmp_path: Path) -> None:
    exe = tmp_path / "gtopt"
    exe.write_text("", encoding="utf-8")
    assert cli.main(["--gtopt-bin", str(exe)]) == 0
    assert fake_bin["bin"] == str(exe)


def test_main_no_solvers(
    monkeypatch: pytest.MonkeyPatch,
    fake_bin: dict[str, Any],  # pylint: disable=unused-argument
    out: _Out,
) -> None:
    monkeypatch.setattr(cli, "list_available_solvers", lambda _b: [])
    assert cli.main([]) == 1
    assert "No solvers to test" in out.text


def test_report_helpers_pass_state() -> None:
    rep = SolverTestReport(solver="clp")
    rep.results.append(SolverTestResult(name="a", passed=True, message="ok"))
    assert rep.passed


def test_main_unknown_solver_requested(
    monkeypatch: pytest.MonkeyPatch,
    fake_bin: dict[str, Any],  # pylint: disable=unused-argument
    out: _Out,
) -> None:
    monkeypatch.setattr(cli, "list_available_solvers", lambda _b: [])
    assert cli.main(["--solver", "nope"]) == 1
    assert "available: none" in out.text
