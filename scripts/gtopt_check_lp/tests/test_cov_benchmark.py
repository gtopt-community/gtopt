# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for ``gtopt_check_lp._benchmark`` (binaries mocked)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gtopt_check_lp import _benchmark as bm

_RUNNERS = {
    "cplex": bm._run_cplex,  # noqa: SLF001
    "highs": bm._run_highs,  # noqa: SLF001
    "clp": bm._run_clp,  # noqa: SLF001
    "cbc": bm._run_cbc,  # noqa: SLF001
}


@pytest.fixture(autouse=True)
def _binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bm.shutil, "which", lambda n: f"/x/{n}")
    monkeypatch.setattr(bm, "find_cplex_binary", lambda: "/x/cplex")


@pytest.mark.parametrize("solver", sorted(_RUNNERS))
@pytest.mark.parametrize("threads", [0, 3])
def test_runner_wall_time_fallback(
    solver: str, threads: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cmds: list[list[str]] = []

    def _run(cmd: list[str], **_k: Any) -> Any:
        cmds.append(cmd)
        return SimpleNamespace(stdout="no timing here\n", stderr="")

    monkeypatch.setattr(bm.subprocess, "run", _run)
    res = _RUNNERS[solver](tmp_path / "m.lp", "barrier", threads, 5)
    assert res is not None
    assert res.solver == solver
    assert res.time_s >= 0.0  # wall clock used when no time is parsed
    flat = " ".join(cmds[0])
    if solver in ("clp", "cbc"):
        assert ("-threads 3" in flat) is (threads > 0)
    if solver == "highs":
        assert ("--parallel on" in flat) is (threads > 0)


@pytest.mark.parametrize("solver", sorted(_RUNNERS))
@pytest.mark.parametrize(
    "exc", [subprocess.TimeoutExpired("x", 5), OSError("cannot exec")]
)
def test_runner_errors(
    solver: str, exc: Exception, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _run(*_a: Any, **_k: Any) -> Any:
        raise exc

    monkeypatch.setattr(bm.subprocess, "run", _run)
    res = _RUNNERS[solver](tmp_path / "m.lp", "dual", 0, 5)
    assert res is not None
    assert res.time_s == -1.0
    assert res.status.startswith("error") or "timeout" in res.status.lower()


def test_run_benchmark_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lp = tmp_path / "m.lp"
    lp.write_text("Minimize\n obj: x\nEnd\n")
    seen: list[tuple[str, int]] = []

    def _runner(_p: Path, algo: str, threads: int, _t: int) -> Any:
        seen.append((algo, threads))
        if threads == 0:
            return None
        return bm.BenchmarkResult("fake", algo, threads, 1.0, "optimal", "1")

    monkeypatch.setattr(bm, "_SOLVER_RUNNERS", [("fake", _runner)])
    report = bm.run_benchmark(lp)
    assert seen == [(a, t) for a in bm._DEFAULT_ALGOS for t in bm._DEFAULT_THREADS]  # noqa: SLF001
    assert len(report.results) == 4


def test_format_table_skips_results_without_dimensions() -> None:
    report = bm.BenchmarkReport(
        lp_path=Path("x.lp"),
        results=[
            bm.BenchmarkResult("a", "dual", 0, 1.0, "optimal", "1"),
            bm.BenchmarkResult("b", "dual", 0, 2.0, "optimal", "1", rows=3, cols=4),
        ],
    )
    text = bm.format_benchmark_table(report, use_color=False)
    assert "LP size: 3 rows, 4 columns" in text
