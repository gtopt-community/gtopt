# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI tests for gtopt2opendss."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

from gtopt2opendss import ac
from gtopt2opendss import main as main_mod
from gtopt2opendss.tests.test_ac import fake_engine
from gtopt2opendss.tests.test_convert import _planning, _write


def _run(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        main_mod.main(argv)
    return int(exc.value.code or 0)


@pytest.fixture(name="case")
def fixture_case(tmp_path: Path) -> Path:
    p = tmp_path / "case.json"
    p.write_text(json.dumps(_planning()))
    return p


def test_export(case: Path) -> None:
    assert _run([str(case)]) == 0
    assert "New Circuit" in case.with_suffix(".dss").read_text()


def test_bad_json(tmp_path: Path) -> None:
    bad = tmp_path / "x.json"
    bad.write_text("{")
    assert _run([str(bad)]) == 1
    assert _run([str(tmp_path / "missing.json")]) == 1


def test_solve_without_engine(case: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing():
        raise ImportError("no engine")

    monkeypatch.setattr(ac, "load_engine", missing)
    assert _run([str(case), "--solve"]) == 1


def test_solve_and_compare(
    case: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(ac, "load_engine", fake_engine)
    out = tmp_path / "out"
    _write(out / "Bus" / "vsq_sol_s0_p0.csv", [(1, 1, 1.0), (1, 2, 0.9409)])
    assert (
        _run([str(case), "-r", str(out), "-o", str(tmp_path / "x.dss"), "--solve"]) == 0
    )
    text = capsys.readouterr().out
    assert "gtopt vs AC" in text and "converged" in text
    out2 = tmp_path / "out2"
    out2.mkdir()
    assert _run([str(case), "-r", str(out2), "--solve"]) == 0
    monkeypatch.setattr(ac, "load_engine", lambda: fake_engine(converged=False))
    assert _run([str(case), "--solve"]) == 1


def test_dunder_main() -> None:
    with pytest.raises(SystemExit):
        runpy.run_module("gtopt2opendss", run_name="__main__", alter_sys=True)
