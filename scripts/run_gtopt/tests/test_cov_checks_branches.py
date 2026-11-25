# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch tests for run_gtopt._checks (pre/post-flight checks).

External tools (gtopt_check_json, gtopt_check_lp, gtopt_check_output) are
replaced by fakes; nothing is spawned.
"""

from __future__ import annotations

import builtins
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from run_gtopt import _checks, _runner
from run_gtopt._checks import (
    CheckResult,
    check_input_files,
    check_json_syntax,
    check_json_tool,
    check_output_directory,
    post_check_lp,
    post_check_output,
    run_postflight_checks,
    run_preflight_checks,
)


def _plan(tmp_path: Path, data: Any, name: str = "plan.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _deny(monkeypatch: pytest.MonkeyPatch, denied: Path) -> None:
    real_access = _checks.os.access

    def _access(path: Any, mode: int) -> bool:
        if Path(path) == denied:
            return False
        return real_access(path, mode)

    monkeypatch.setattr(_checks.os, "access", _access)


# ---------------------------------------------------------------------------
# json_syntax
# ---------------------------------------------------------------------------


def test_json_syntax_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unreadable = tmp_path / "dir.json"
    unreadable.mkdir()  # is_file() False -> "does not exist"
    assert not check_json_syntax(unreadable).passed

    path = _plan(tmp_path, {})
    real_open = builtins.open

    def _open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(file) == path:
            raise PermissionError("denied")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _open)
    res = check_json_syntax(path)
    assert not res.passed
    assert "cannot read" in res.errors[0]


# ---------------------------------------------------------------------------
# input_files
# ---------------------------------------------------------------------------


def test_input_files_absolute_dir_and_reference_kinds(tmp_path: Path) -> None:
    input_dir = tmp_path / "abs_input"
    (input_dir / "Generator").mkdir(parents=True)
    (input_dir / "Generator" / "pmax.parquet").write_text("", encoding="utf-8")
    (input_dir / "Demand").mkdir()
    plan = _plan(
        tmp_path,
        {
            "options": {"input_directory": str(input_dir), "input_format": "csv"},
            "system": {
                "generator_array": [
                    "not-a-dict",
                    {
                        "uid": 1,
                        "name": "g1",
                        "pmax": "pmax",
                        "pmin": 0.0,
                        "gcost": "",
                        "cogen_mode": "dispatched",
                        "lmax": "Demand@lmax#12",
                        "lmin": "Demand@lmin#x",
                    },
                    {"pmax": "pmax"},  # duplicate reference -> checked once
                ],
                "demand_array": {"not": "a list"},
            },
        },
    )
    res = check_input_files(plan)
    assert res.passed
    assert res.warnings == [
        f"missing input file: Demand/lmax.csv (in {input_dir / 'Demand'})",
        f"missing input file: Demand/lmin#x.csv (in {input_dir / 'Demand'})",
    ]


def test_input_files_unreadable_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "input").mkdir()
    plan = _plan(tmp_path, {"system": {}})
    _deny(monkeypatch, tmp_path / "input")
    res = check_input_files(plan)
    assert res.warnings == [f"input_directory is not readable: {tmp_path / 'input'}"]


# ---------------------------------------------------------------------------
# output_dir
# ---------------------------------------------------------------------------


def test_output_dir_bad_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert check_output_directory(bad).passed


def test_output_dir_existing_not_writable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "abs_out"
    out.mkdir()
    plan = _plan(tmp_path, {"options": {"output_directory": str(out)}})
    _deny(monkeypatch, out)
    res = check_output_directory(plan)
    assert not res.passed
    assert "not writable" in res.errors[0]


def test_output_dir_parent_not_writable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _plan(tmp_path, {"options": {"output_directory": "new_out"}})
    _deny(monkeypatch, tmp_path)
    res = check_output_directory(plan)
    assert not res.passed
    assert "parent" in res.errors[0]
    assert "not writable" in res.errors[0]


def test_output_dir_parent_missing(tmp_path: Path) -> None:
    plan = _plan(tmp_path, {"options": {"output_directory": "a/b/c"}})
    res = check_output_directory(plan)
    assert not res.passed
    assert "does not exist" in res.errors[0]


def test_output_dir_ok_when_creatable(tmp_path: Path) -> None:
    plan = _plan(tmp_path, {"options": {}})
    assert check_output_directory(plan).passed


# ---------------------------------------------------------------------------
# check_json tool
# ---------------------------------------------------------------------------


class _FakeSubprocess:
    def __init__(self, results: list[SimpleNamespace]):
        self.results = results
        self.cmds: list[list[str]] = []

    def run(self, cmd: list[str], **_kw: Any) -> SimpleNamespace:
        self.cmds.append(cmd)
        return self.results.pop(0)


def test_check_json_tool_missing_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_checks.shutil, "which", lambda _n: None)
    res = check_json_tool(tmp_path / "p.json")
    assert res.passed
    assert not res.warnings


def test_check_json_tool_reports_issues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeSubprocess(
        [
            SimpleNamespace(returncode=0, stdout="stats line\n", stderr=""),
            SimpleNamespace(returncode=1, stdout="  bad uid\n\n", stderr="oops\n"),
        ]
    )
    monkeypatch.setattr(_checks.shutil, "which", lambda _n: "/bin/gtopt_check_json")
    monkeypatch.setattr(_checks, "subprocess", fake)
    res = check_json_tool(tmp_path / "p.json")
    assert res.warnings == [
        "gtopt_check_json found issues (exit code 1)",
        "bad uid",
        "oops",
    ]
    assert fake.cmds[0][1] == "--info"
    assert fake.cmds[1][1] == "--quiet"


def test_check_json_tool_clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeSubprocess(
        [
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
    )
    monkeypatch.setattr(_checks.shutil, "which", lambda _n: "/bin/gtopt_check_json")
    monkeypatch.setattr(_checks, "subprocess", fake)
    assert not check_json_tool(tmp_path / "p.json").warnings


# ---------------------------------------------------------------------------
# Post-flight: check_output
# ---------------------------------------------------------------------------


def test_post_check_output_skips_missing_inputs(tmp_path: Path) -> None:
    res = post_check_output(tmp_path / "missing", tmp_path / "missing.json")
    assert res.passed
    assert not res.warnings


@pytest.fixture(name="fake_check_output")
def _fake_check_output(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import gtopt_check_output._checks as co_checks  # pylint: disable=import-outside-toplevel
    import gtopt_check_output._reader as co_reader  # pylint: disable=import-outside-toplevel
    import gtopt_check_output.main as co_main  # pylint: disable=import-outside-toplevel

    state: dict[str, Any] = {"printed": [], "report": None}
    monkeypatch.setattr(co_reader, "load_planning", lambda p: {"path": p})
    monkeypatch.setattr(co_checks, "run_all_checks", lambda _r, _p: state["report"])
    monkeypatch.setattr(
        co_main,
        "_print_finding",
        lambda f, _c, quiet: state["printed"].append((f.severity, quiet)),
    )
    return state


def test_post_check_output_reports_findings(
    fake_check_output: dict[str, Any], tmp_path: Path
) -> None:
    fake_check_output["report"] = SimpleNamespace(
        findings=[
            SimpleNamespace(severity="INFO"),
            SimpleNamespace(severity="WARNING"),
        ],
        ok=False,
    )
    plan = _plan(tmp_path, {})
    res = post_check_output(tmp_path, plan)
    assert fake_check_output["printed"] == [("WARNING", False)]
    assert res.warnings == ["output analysis found issues"]


def test_post_check_output_ok_report(
    fake_check_output: dict[str, Any], tmp_path: Path
) -> None:
    fake_check_output["report"] = SimpleNamespace(findings=[], ok=True)
    res = post_check_output(tmp_path, _plan(tmp_path, {}))
    assert not res.warnings


def test_post_check_output_swallows_errors(
    fake_check_output: dict[str, Any], tmp_path: Path
) -> None:
    fake_check_output["report"] = None  # .findings -> AttributeError
    res = post_check_output(tmp_path, _plan(tmp_path, {}))
    assert res.passed


# ---------------------------------------------------------------------------
# Post-flight: check_lp
# ---------------------------------------------------------------------------


def test_post_check_lp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    assert not post_check_lp(results, gtopt_rc=0).warnings

    logs = tmp_path / "output" / "logs"
    logs.mkdir(parents=True)
    (logs / "error_1.lp").write_text("", encoding="utf-8")
    (logs / "error_2.lp.gz").write_text("", encoding="utf-8")
    seen: list[list[Path]] = []
    monkeypatch.setattr(_runner, "run_check_lp", seen.append)
    res = post_check_lp(results, gtopt_rc=1)
    assert res.warnings == ["2 error LP file(s) found"]
    assert len(seen) == 1

    empty = tmp_path / "empty" / "results"
    empty.mkdir(parents=True)
    assert not post_check_lp(empty, gtopt_rc=1).warnings


# ---------------------------------------------------------------------------
# Orchestrators
# ---------------------------------------------------------------------------


def test_run_preflight_checks_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    checks = {
        "ok": lambda _p: CheckResult(name="ok", passed=True),
        "warn": lambda _p: CheckResult(name="warn", passed=True, warnings=["w"]),
        "fail": lambda _p: CheckResult(name="fail", passed=False, errors=["e"]),
    }
    monkeypatch.setattr(_checks, "_PRE_CHECKS", checks)
    with caplog.at_level(logging.DEBUG):
        assert run_preflight_checks(tmp_path, enabled={"ok", "warn"}) is True
    assert "ok: OK" in caplog.text
    assert run_preflight_checks(tmp_path, strict=True, enabled={"warn"}) is False
    assert run_preflight_checks(tmp_path) is False


def test_run_postflight_checks_filters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def _mk(name: str):
        def _fn(**kwargs: Any) -> CheckResult:
            calls.append((name, kwargs))
            return CheckResult(name=name, passed=True)

        return _fn

    monkeypatch.setattr(_checks, "_POST_CHECKS", {"a": _mk("a"), "b": _mk("b")})
    run_postflight_checks(tmp_path, tmp_path / "p.json", gtopt_rc=3, enabled={"b"})
    assert [c[0] for c in calls] == ["b"]
    assert calls[0][1]["gtopt_rc"] == 3
    run_postflight_checks(tmp_path, tmp_path / "p.json")
    assert [c[0] for c in calls] == ["b", "a", "b"]
