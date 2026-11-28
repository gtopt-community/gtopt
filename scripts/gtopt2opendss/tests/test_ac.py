# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for gtopt2opendss.ac with a fake DSS engine."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gtopt2opendss import ac


def fake_engine(converged: bool = True) -> SimpleNamespace:
    cmds: list[str] = []

    def command(c: str) -> None:
        cmds.append(c)
        if c.startswith("Compile"):
            os.chdir("/")  # OpenDSS changes the working directory

    return SimpleNamespace(
        cmds=cmds,
        Text=SimpleNamespace(Command=command),
        Solution=SimpleNamespace(Solve=lambda: None, Converged=lambda: converged),
        Circuit=SimpleNamespace(
            AllNodeNames=lambda: ["s.1", "s.2", "s.3", "a.1", "a.2"],
            AllBusMagPu=lambda: [1.0, 1.0, 1.0, 0.98, 0.96],
            Losses=lambda: [1500.0, 3000.0],
            TotalPower=lambda: [-100.0, -20.0],
        ),
    )


def test_solve_text_restores_cwd() -> None:
    cwd = os.getcwd()
    eng = fake_engine()
    sol = ac.solve_text("Clear\n", eng)
    assert os.getcwd() == cwd
    assert sol.converged and sol.vpu["a"] == pytest.approx(0.97)
    assert sol.vpu_min["a"] == pytest.approx(0.96)
    assert sol.losses_kw == pytest.approx(1.5) and sol.losses_kvar == pytest.approx(3.0)
    assert sol.source_kw == pytest.approx(100.0) and sol.source_kvar == pytest.approx(
        20.0
    )
    assert any(c.startswith("Compile") for c in eng.cmds)


def test_compare_and_helpers() -> None:
    ref = ac.AcSolution(converged=True, vpu={"s": 1.0, "a": 0.97}, losses_kw=2.0)
    cmp = ac.compare_voltages(
        {"S": 1.0, "a": 0.95, "zz": 1.0}, ref, model_losses_kw=1.8
    )
    assert cmp.max_abs_dv == pytest.approx(0.02) and cmp.worst_bus == "a"
    assert cmp.n_buses == 2
    assert cmp.loss_rel_err == pytest.approx(-0.1)
    assert ac.compare_voltages({"a": 0.97}, ref).loss_rel_err is None
    zero = ac.AcSolution(converged=True, vpu={"a": 1.0})
    assert (
        ac.compare_voltages({"a": 1.0}, zero, model_losses_kw=1.0).loss_rel_err is None
    )
    with pytest.raises(ValueError):
        ac.compare_voltages({"a": 1.0}, ref, skip={"a"})
    assert ac.vpu_from_vsq({"a": 0.81, "b": -1e-9}) == {"a": 0.9, "b": 0.0}


def test_load_engine_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "opendssdirect", None)
    with pytest.raises(ImportError):
        ac.load_engine()


def test_solve_file_default_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    eng = fake_engine(converged=False)
    monkeypatch.setattr(ac, "load_engine", lambda: eng)
    p = tmp_path / "m.dss"
    p.write_text("Clear\n")
    assert not ac.solve_file(p).converged
