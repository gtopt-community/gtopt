# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for ``gtopt_check_lp._solvers`` with every solver mocked.

``highspy`` is replaced by an in-memory fake module and every external
binary call goes through a patched ``subprocess.run`` / ``shutil.which``,
so these tests never run a real solver.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gtopt_check_lp import _solvers as sv

_LP = "Minimize\n obj: x\nSubject To\n c1: x >= 1\nEnd\n"


@pytest.fixture(name="lp")
def _lp(tmp_path: Path) -> Path:
    path = tmp_path / "m.lp"
    path.write_text(_LP, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Fake highspy
# ---------------------------------------------------------------------------


class _Enum:
    def __init__(self, value: int) -> None:
        self.value = value

    def __str__(self) -> str:
        return f"Enum({self.value})"


class FakeHighs:
    """Configurable stand-in for ``highspy.Highs``."""

    status = "HighsModelStatus.kInfeasible"
    iis: Any = None  # object with col_index_/row_index_ ...
    iis_option_error_at: int | None = None
    get_iis_error = False
    has_relax = False
    relax_status = "HighsStatus.kOk"
    row_duals: list[float] = []
    write_iis_error = False
    read_error: BaseException | None = None
    block: threading.Event | None = None

    def __init__(self) -> None:
        self._strategy_calls = 0

    def silent(self) -> None:
        pass

    def readModel(self, _path: str) -> None:  # noqa: N802
        if self.read_error is not None:
            raise self.read_error

    def setOptionValue(self, name: str, _value: object) -> None:  # noqa: N802
        if name == "iis_strategy":
            self._strategy_calls += 1
            if self.iis_option_error_at == self._strategy_calls:
                raise RuntimeError("no such option")

    def run(self) -> None:
        if self.block is not None:
            self.block.wait(5)

    def getModelStatus(self) -> str:  # noqa: N802
        return self.status

    def getIis(self) -> tuple[int, Any]:  # noqa: N802
        if self.get_iis_error:
            raise RuntimeError("iis exploded")
        return 0, self.iis

    def getColName(self, idx: int) -> Any:  # noqa: N802
        if idx == 99:
            raise RuntimeError("no name")
        return (0, f"col{idx}")

    def getRowName(self, idx: int) -> str:  # noqa: N802
        return f"row{idx}"

    def writeIisModel(self, _path: str) -> None:  # noqa: N802
        if self.write_iis_error:
            raise RuntimeError("cannot write")

    def getSolution(self) -> Any:  # noqa: N802
        return SimpleNamespace(row_dual=self.row_duals)

    def getNumRow(self) -> int:  # noqa: N802
        return len(self.row_duals)


def _install_highspy(
    monkeypatch: pytest.MonkeyPatch, relax: bool = False, **attrs: Any
) -> type[FakeHighs]:
    cls = type("H", (FakeHighs,), dict(attrs))
    if relax:

        def feasibilityRelaxation(self: FakeHighs, *_a: float) -> str:  # noqa: N802
            return self.relax_status

        cls.feasibilityRelaxation = feasibilityRelaxation  # type: ignore[attr-defined]
    mod = types.ModuleType("highspy")
    mod.Highs = cls  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "highspy", mod)
    return cls


class TestHighsPython:
    """``run_local_highs_python`` against the fake ``highspy``."""

    def test_not_installed(self, lp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "highspy", None)
        assert sv.run_local_highs_python(lp) == (
            False,
            "highspy Python package not installed.",
        )

    def test_optimal(self, lp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_highspy(monkeypatch, status="HighsModelStatus.kOptimal")
        ok, out = sv.run_local_highs_python(lp)
        assert ok
        assert out == "HiGHS model status: HighsModelStatus.kOptimal"

    def test_iis_found(
        self, lp: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        iis = SimpleNamespace(
            col_index_=[0, 99, 2],
            row_index_=[1, 3],
            col_bound_=[_Enum(2), 3],
            row_bound_=[_Enum(42)],
        )
        _install_highspy(monkeypatch, iis=iis)
        ok, out = sv.run_local_highs_python(lp)
        assert ok
        assert "Infeasible variable bounds (3)" in out
        assert "• col0  [bound=lower]" in out
        assert "• 99  [bound=upper]" in out  # name lookup failed -> index
        assert "• col2\n" in out
        assert "• row1  [bound=Enum(42)]" in out
        assert "• row3" in out
        assert "IIS sub-model written to" in out

    def test_iis_rows_only_write_fails(
        self, lp: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        iis = SimpleNamespace(row_index_=[5])
        _install_highspy(monkeypatch, iis=iis, write_iis_error=True)
        ok, out = sv.run_local_highs_python(lp)
        assert ok
        assert "Infeasible variable bounds" not in out
        assert "• row5" in out
        assert "IIS sub-model" not in out

    def test_iis_cols_only(self, lp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_highspy(monkeypatch, iis=SimpleNamespace(col_index_=[4]))
        _ok, out = sv.run_local_highs_python(lp)
        assert "• col4" in out
        assert "Infeasible constraints" not in out

    def test_iis_option_missing_and_no_relaxation(
        self, lp: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_highspy(monkeypatch, iis_option_error_at=1)
        ok, out = sv.run_local_highs_python(lp)
        assert ok
        assert "Could not identify specific infeasible members" in out

    def test_relaxation_with_many_violations(
        self, lp: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty = SimpleNamespace(col_index_=None, row_index_=[])
        duals = [0.0] + [float(i) for i in range(1, 53)]
        _install_highspy(monkeypatch, relax=True, iis=empty, row_duals=duals)
        ok, out = sv.run_local_highs_python(lp)
        assert ok
        assert "52 constraint(s) involved" in out
        assert "• row52  (dual=+52)" in out
        assert "… and 2 more" in out

    def test_relaxation_few_violations(
        self, lp: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty = SimpleNamespace()
        _install_highspy(monkeypatch, relax=True, iis=empty, row_duals=[0.0, -2.0])
        _ok, out = sv.run_local_highs_python(lp)
        assert "1 constraint(s) involved" in out
        assert "more" not in out

    def test_relaxation_without_violations(
        self, lp: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_highspy(
            monkeypatch, relax=True, iis=SimpleNamespace(), row_duals=[0.0]
        )
        _ok, out = sv.run_local_highs_python(lp)
        assert "Could not identify" in out

    def test_relaxation_not_ok(self, lp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_highspy(
            monkeypatch,
            relax=True,
            iis=SimpleNamespace(),
            relax_status="HighsStatus.kError",
        )
        _ok, out = sv.run_local_highs_python(lp)
        assert "Could not identify" in out

    def test_iis_analysis_exception(
        self, lp: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_highspy(monkeypatch, get_iis_error=True)
        ok, out = sv.run_local_highs_python(lp)
        assert ok
        assert "IIS/relaxation analysis failed: iis exploded" in out

    def test_read_error(self, lp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_highspy(monkeypatch, read_error=RuntimeError("bad file"))
        assert sv.run_local_highs_python(lp) == (False, "highspy error: bad file")

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_worker_without_result(
        self, lp: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_highspy(monkeypatch, read_error=SystemExit(3))
        ok, out = sv.run_local_highs_python(lp)
        assert not ok
        assert "without a result" in out

    def test_timeout(self, lp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        gate = threading.Event()
        _install_highspy(monkeypatch, block=gate)
        try:
            ok, out = sv.run_local_highs_python(lp, timeout=0)
        finally:
            gate.set()
        assert not ok
        assert "timed out after 0s" in out


# ---------------------------------------------------------------------------
# Binary discovery / detection
# ---------------------------------------------------------------------------


def test_find_cplex_well_known_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "cplex").write_text("")  # not executable
    good = tmp_path / "good"
    good.mkdir()
    exe = good / "cplex"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(sv.shutil, "which", lambda _n: None)
    monkeypatch.setattr(
        sv, "_CPLEX_WELL_KNOWN_DIRS", [str(tmp_path / "missing*"), str(bad), str(good)]
    )
    assert sv.find_cplex_binary() == str(exe)
    monkeypatch.setattr(sv, "_CPLEX_WELL_KNOWN_DIRS", [str(bad)])
    assert sv.find_cplex_binary() is None


def test_find_cplex_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sv.shutil, "which", lambda n: f"/x/{n}")
    assert sv.find_cplex_binary() == "/x/cplex"


@pytest.mark.parametrize(
    ("bins", "highspy", "expected"),
    [
        ({"cplex", "cbc", "highs"}, True, ["CPLEX", "CBC (COIN-OR)", "HiGHS (Python)"]),
        ({"clp", "highs"}, False, ["CLP (COIN-OR)", "HiGHS (binary)"]),
        (set(), False, []),
    ],
)
def test_detect_local_solvers(
    monkeypatch: pytest.MonkeyPatch,
    bins: set[str],
    highspy: bool,
    expected: list[str],
) -> None:
    monkeypatch.setattr(sv.shutil, "which", lambda n: f"/x/{n}" if n in bins else None)
    monkeypatch.setattr(sv, "_CPLEX_WELL_KNOWN_DIRS", [])
    if highspy:
        _install_highspy(monkeypatch)
    else:
        monkeypatch.setitem(sys.modules, "highspy", None)
    assert sv.detect_local_solvers() == expected


# ---------------------------------------------------------------------------
# CPLEX / HiGHS binary / COIN-OR runners
# ---------------------------------------------------------------------------


def test_cplex_script_tolerances(tmp_path: Path) -> None:
    script = sv._cplex_script(  # noqa: SLF001
        tmp_path / "a.lp",
        algo="Primal",
        optimal_eps=1e-7,
        feasible_eps=1e-8,
        barrier_eps=1e-9,
    )
    assert "set lpmethod 1" in script
    assert "set simplex tolerances dual 1e-07" in script
    assert "set simplex tolerances primal 1e-08" in script
    assert "set barrier convergetol 1e-09" in script
    assert "barrier limits growth" not in script
    assert "set lpmethod" not in sv._cplex_script(tmp_path / "a.lp", algo="weird")  # noqa: SLF001


def test_run_local_cplex_success_and_errors(
    lp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sv, "find_cplex_binary", lambda: "/x/cplex")
    scripts: list[str] = []

    def _ok(cmd: list[str], **_kw: Any) -> Any:
        scripts.append(Path(cmd[2]).read_text(encoding="utf-8"))
        return SimpleNamespace(stdout="conflict found\n", stderr="warn")

    monkeypatch.setattr(sv.subprocess, "run", _ok)
    ok, out = sv.run_local_cplex(lp, algo="dual")
    assert ok
    assert out == "conflict found\nwarn"
    assert "set lpmethod 2" in scripts[0]

    def _timeout(*_a: Any, **_k: Any) -> Any:
        raise subprocess.TimeoutExpired("cplex", 3)

    monkeypatch.setattr(sv.subprocess, "run", _timeout)
    assert sv.run_local_cplex(lp, timeout=3) == (False, "CPLEX timed out after 3s.")

    def _oserror(*_a: Any, **_k: Any) -> Any:
        raise OSError("exec format error")

    monkeypatch.setattr(sv.subprocess, "run", _oserror)
    ok, out = sv.run_local_cplex(lp)
    assert not ok
    assert "exec format error" in out


def test_run_local_highs_binary_success(
    lp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sv.shutil, "which", lambda n: f"/x/{n}")
    monkeypatch.setattr(
        sv.subprocess,
        "run",
        lambda cmd, **_k: SimpleNamespace(stdout=" ".join(cmd[1:3]), stderr=""),
    )
    ok, out = sv.run_local_highs_binary(lp)
    assert ok
    assert out.startswith("--model_file")


def test_run_local_coinor_variants(lp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_k: Any) -> Any:
        calls.append(cmd)
        return SimpleNamespace(stdout="Problem is fine\n", stderr="")

    monkeypatch.setattr(sv.subprocess, "run", _run)
    monkeypatch.setattr(sv.shutil, "which", lambda n: "/x/clp" if n == "clp" else None)
    ok, out = sv.run_local_coinor(lp, algo="default")
    assert ok
    assert out.startswith("--- CLP output ---")
    assert "Key infeasibility" not in out
    assert calls[-1][-1] == "solve"

    def _oserror(*_a: Any, **_k: Any) -> Any:
        raise OSError("boom")

    monkeypatch.setattr(sv.subprocess, "run", _oserror)
    ok, out = sv.run_local_coinor(lp)
    assert not ok
    assert "Neither cbc nor clp" in out


# ---------------------------------------------------------------------------
# Dispatchers
# ---------------------------------------------------------------------------


def _stub_runners(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    called: list[str] = []

    def _mk(name: str, ok: bool) -> Any:
        def _runner(*_a: Any, **_k: Any) -> tuple[bool, str]:
            called.append(name)
            return ok, f"{name}-out"

        return _runner

    monkeypatch.setattr(sv, "run_local_highs_python", _mk("highspy", True))
    monkeypatch.setattr(sv, "run_local_highs_binary", _mk("highs", False))
    monkeypatch.setattr(sv, "run_local_cplex", _mk("cplex", True))
    monkeypatch.setattr(sv, "run_local_coinor", _mk("coinor", False))

    class _Neos:
        def __init__(self, url: str, timeout: int) -> None:
            called.append(f"neos:{url}:{timeout}")

        def submit_and_wait(self, *_a: Any) -> tuple[bool, str]:
            return True, "neos-out"

    monkeypatch.setattr(sv, "NeosClient", _Neos)
    return called


@pytest.mark.parametrize("dispatch", ["auto", "all"])
def test_dispatch_with_highspy_and_cplex(
    lp: Path, monkeypatch: pytest.MonkeyPatch, dispatch: str
) -> None:
    called = _stub_runners(monkeypatch)
    _install_highspy(monkeypatch)
    monkeypatch.setattr(sv.shutil, "which", lambda n: f"/x/{n}")
    monkeypatch.setattr(sv, "find_cplex_binary", lambda: "/x/cplex")
    ok, name, out = sv.run_iis(lp, solver=dispatch, email="a@b")
    assert ok
    assert name == dispatch
    assert "HiGHS (Python)" in out
    assert "CPLEX (local)" in out
    assert "NEOS" not in out
    assert called[0] == "highspy"
    assert ("coinor" in called) is (dispatch == "all")


@pytest.mark.parametrize("dispatch", ["auto", "all"])
def test_dispatch_with_highs_binary_and_neos(
    lp: Path, monkeypatch: pytest.MonkeyPatch, dispatch: str
) -> None:
    called = _stub_runners(monkeypatch)
    monkeypatch.setitem(sys.modules, "highspy", None)
    monkeypatch.setattr(
        sv.shutil, "which", lambda n: "/x/highs" if n == "highs" else None
    )
    monkeypatch.setattr(sv, "find_cplex_binary", lambda: None)
    ok, _name, out = sv.run_iis(lp, solver=dispatch, email="a@b", timeout=5)
    assert ok
    assert "HiGHS (binary)" in out
    assert "NEOS (CPLEX)" in out
    assert any(c.startswith("neos:") and c.endswith(":120") for c in called)


def test_dispatch_auto_nothing_available(
    lp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_runners(monkeypatch)
    monkeypatch.setitem(sys.modules, "highspy", None)
    monkeypatch.setattr(sv.shutil, "which", lambda _n: None)
    monkeypatch.setattr(sv, "find_cplex_binary", lambda: None)
    ok, name, out = sv.run_iis(lp, solver="auto")
    assert not ok
    assert name == "auto"
    assert "pip install highspy" in out


@pytest.mark.parametrize("has_highspy", [True, False])
def test_run_iis_highs(
    lp: Path, monkeypatch: pytest.MonkeyPatch, has_highspy: bool
) -> None:
    called = _stub_runners(monkeypatch)
    if has_highspy:
        _install_highspy(monkeypatch)
    else:
        monkeypatch.setitem(sys.modules, "highspy", None)
    _ok, name, _out = sv.run_iis(lp, solver="HiGHS")
    assert name == "HiGHS"
    assert called == ["highspy" if has_highspy else "highs"]


def test_run_local_coinor_cbc_timeout(
    lp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sv.shutil, "which", lambda n: f"/x/{n}")

    def _timeout(*_a: Any, **_k: Any) -> Any:
        raise subprocess.TimeoutExpired("cbc", 4)

    monkeypatch.setattr(sv.subprocess, "run", _timeout)
    ok, out = sv.run_local_coinor(lp, timeout=4)
    assert ok
    assert out == "--- CBC ---\nCBC timed out after 4s."


def test_run_local_coinor_reports_findings(
    lp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sv.shutil, "which", lambda n: f"/x/{n}")
    monkeypatch.setattr(
        sv.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(stdout="Primal infeasible - x\n", stderr=""),
    )
    ok, out = sv.run_local_coinor(lp)
    assert ok
    assert "Key infeasibility findings:" in out
    assert "  ✗ Primal infeasible - x" in out
