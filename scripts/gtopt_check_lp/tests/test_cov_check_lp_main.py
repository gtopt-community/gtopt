# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for ``check_lp`` / ``main`` with solvers and AI mocked."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

import pytest

from gtopt_check_lp import gtopt_check_lp as m

_LP = "Minimize\n obj: x\nSubject To\n c1: x >= 1\nBounds\n x <= 0\nEnd\n"


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture(name="lp")
def _lp(tmp_path: Path) -> Path:
    path = tmp_path / "error_0.lp"
    path.write_text(_LP, encoding="utf-8")
    return path


@pytest.fixture(name="conf")
def _conf(tmp_path: Path) -> Path:
    path = tmp_path / "g.conf"
    path.write_text("[global]\ncolor = never\nai_enabled = false\n")
    return path


def _no_solvers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "detect_local_solvers", lambda: [])


# ---------------------------------------------------------------------------
# AI diagnostics helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ok", [True, False])
def test_run_ai_diagnostics(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], ok: bool
) -> None:
    seen: dict[str, Any] = {}

    def _query(report: str, **kw: Any) -> tuple[bool, str]:
        seen["report"] = report
        seen.update(kw)
        return ok, "diagnosis text"

    monkeypatch.setattr(m, "query_ai", _query)
    parts = ["a", "b"]
    m._run_ai_diagnostics(  # noqa: SLF001
        parts,
        provider="claude",
        model=None,
        prompt_template="T",
        api_key="k",
        timeout=3,
    )
    assert seen["report"] == "a\n\nb"
    assert seen["api_key"] == "k"
    assert len(parts) == 3
    assert "AI Diagnostics (claude)" in parts[-1]
    out = capsys.readouterr().out
    assert ("AI diagnostic unavailable" in out) is (not ok)
    assert "diagnosis text" in out


# ---------------------------------------------------------------------------
# check_lp branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("quiet", "code"), [(True, 0), (False, 1)])
def test_check_lp_resolved_path_vanished(
    lp: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    quiet: bool,
    code: int,
) -> None:
    gone = tmp_path / "gone.lp.gz"
    monkeypatch.setattr(m, "resolve_lp_path", lambda _p: gone)
    assert m.check_lp(lp, quiet=quiet) == code
    captured = capsys.readouterr()
    assert f"not found; using {gone}" in captured.out
    assert "not found" in captured.err


def test_check_lp_quiet_solver_exception_and_hint(
    lp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_solvers(monkeypatch)

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("solver crashed")

    monkeypatch.setattr(m, "run_iis", _boom)
    rc = m.check_lp(lp, quiet=True, ai=m.AiOptions(enabled=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Solver error: solver crashed" in out
    assert "Note (quiet mode):" in out


def test_check_lp_solver_exception_raises(
    lp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_solvers(monkeypatch)

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("solver crashed")

    monkeypatch.setattr(m, "run_iis", _boom)
    with pytest.raises(RuntimeError, match="solver crashed"):
        m.check_lp(lp, solver="cplex", ai=m.AiOptions(enabled=False))


def test_check_lp_quiet_with_email_no_hint(
    lp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_solvers(monkeypatch)
    monkeypatch.setattr(m, "run_iis", lambda *_a, **_k: (True, "NEOS", "ok"))
    rc = m.check_lp(lp, quiet=True, email="a@b", ai=m.AiOptions(enabled=False))
    assert rc == 0
    assert "Note (quiet mode)" not in capsys.readouterr().out


@pytest.mark.parametrize("quiet", [True, False])
def test_check_lp_ai_exception(
    lp: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    quiet: bool,
) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("ai down")

    monkeypatch.setattr(m, "_run_ai_diagnostics", _boom)
    out_file = tmp_path / "report.txt"
    if quiet:
        monkeypatch.setattr(m, "run_iis", lambda *_a, **_k: (False, "x", "y"))
        rc = m.check_lp(
            lp,
            solver="cplex",
            quiet=True,
            output_file=out_file,
            ai=m.AiOptions(enabled=True),
        )
        assert rc == 0
        assert "AI diagnostics failed: ai down" in capsys.readouterr().err
        assert out_file.exists()
    else:
        _no_solvers(monkeypatch)
        with pytest.raises(RuntimeError, match="ai down"):
            m.check_lp(lp, solver="auto", ai=m.AiOptions(enabled=True))


# ---------------------------------------------------------------------------
# main() branches
# ---------------------------------------------------------------------------


def test_main_init_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        m, "run_interactive_setup", lambda p, use_color: calls.append((p, use_color))
    )
    conf = tmp_path / "c.conf"
    assert m.main(["--init-config", "--config", str(conf), "--no-color"]) == 0
    assert calls == [(conf, False)]


def test_main_color_always_and_show_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = tmp_path / "c.conf"
    conf.write_text("[global]\ncolor = always\n")
    monkeypatch.setattr(m, "print_solver_status", lambda use_color: None)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert m.main(["--config", str(conf), "--show-config"]) == 0
    assert m._USE_COLOR is True  # noqa: SLF001
    assert "\033[" in out.getvalue()


def test_main_first_run_setup_on_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conf = tmp_path / "missing.conf"
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(sys, "stdout", _Tty())
    ran: list[bool] = []

    def _setup(_p: Path, use_color: bool) -> dict[str, str]:
        ran.append(use_color)
        return {"color": "auto"}

    monkeypatch.setattr(m, "run_interactive_setup", _setup)
    monkeypatch.setattr(m, "print_solver_status", lambda use_color: None)
    assert m.main(["--config", str(conf), "--show-config"]) == 0
    assert ran == [True]


@pytest.mark.parametrize(("quiet", "code"), [(True, 0), (False, 1)])
def test_main_last_without_error_files(
    conf: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    quiet: bool,
    code: int,
) -> None:
    _no_solvers(monkeypatch)
    monkeypatch.setattr(m, "_find_latest_error_lp", lambda: None)
    args = ["--config", str(conf), "--last"] + (["--quiet"] if quiet else [])
    assert m.main(args) == code
    assert "no error*.lp file" in capsys.readouterr().err


def test_main_last_found(
    lp: Path,
    conf: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _no_solvers(monkeypatch)
    monkeypatch.setattr(m, "_find_latest_error_lp", lambda: lp)
    seen: list[Path] = []
    monkeypatch.setattr(m, "check_lp", lambda p, **_k: seen.append(p) or 0)
    assert m.main(["--config", str(conf), "--last"]) == 0
    assert seen == [lp]
    assert "Auto-selected LP file" in capsys.readouterr().out


def test_main_benchmark_with_output(
    lp: Path,
    conf: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _no_solvers(monkeypatch)
    monkeypatch.setattr(m, "run_benchmark", lambda p, timeout: {"p": p, "t": timeout})
    monkeypatch.setattr(
        m, "format_benchmark_table", lambda rep, use_color: f"TABLE t={rep['t']}"
    )
    out_file = tmp_path / "bench.txt"
    rc = m.main(
        [
            "--config",
            str(conf),
            "--benchmark",
            "--timeout",
            "7",
            "--output",
            str(out_file),
            str(lp),
        ]
    )
    assert rc == 0
    assert "TABLE t=7" in capsys.readouterr().out
    assert "TABLE t=7" in out_file.read_text()


def test_main_benchmark_without_output(
    lp: Path, conf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_solvers(monkeypatch)
    monkeypatch.setattr(m, "run_benchmark", lambda p, timeout: {})
    monkeypatch.setattr(m, "format_benchmark_table", lambda rep, use_color: "T")
    assert m.main(["--config", str(conf), "--benchmark", str(lp)]) == 0


def test_format_iis_report_full() -> None:
    from gtopt_check_lp._checker import _format_iis_report  # noqa: PLC0415

    body = "\n".join(f"line {i}" for i in range(200))
    out = f"\n┌── HiGHS ──\n{body}\n┌── CPLEX ──\nshort"
    screen, ai_text = _format_iis_report("all", True, out, full=True)
    assert "line 199" in screen
    assert "short" in screen
    assert "line 199" in ai_text
