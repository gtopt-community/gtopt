"""Tests for helpers of :mod:`plp2gtopt.plp2gtopt` (stats, solver prm, templates)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from plp2gtopt import plp2gtopt as conv
from plp2gtopt.plp2gtopt import (
    _features_rows,
    _log_stats,
    _retune_cplex_prm_for_iterative,
    install_solver_param_files,
    print_pumped_storage_template,
)


def test_features_rows_empty() -> None:
    assert not _features_rows(None)
    assert not _features_rows({})


def test_features_rows_full() -> None:
    rows = dict(
        _features_rows(
            {
                "auto_water_fail_cost": False,
                "water_fail_cost": 12.0,
                "soft_storage_bounds": True,
                "pmin_as_flowright": "x.csv",
                "flow_right_fail_cost": 3.0,
                "soft_emin_cost": 4.0,
                "drop_spillway_waterway": True,
                "vrebemb_as_sink": True,
                "vert_cost_cap": 9.0,
                "ror_as_reservoirs": ["A", "B"],
                "embed_reservoir_constraints": True,
                "pasada_mode": "profile",
                "expand_water_rights": True,
                "expand_lng": True,
                "expand_ror": True,
                "plp_legacy": True,
            }
        )
    )
    assert rows["water_fail_cost"] == "12.0 $/MWh (manual)"
    assert rows["soft_min_flows"] == "on"
    assert rows["spillway_cost_cap"] == "9.0 $/hm³"
    assert rows["ror_as_reservoirs"] == "2 named"
    assert rows["pasada_mode"] == "profile"
    for key in ("expand_water_rights", "expand_lng", "expand_ror", "plp_legacy"):
        assert rows[key] == "on"


@pytest.mark.parametrize(("rar", "text"), [(True, "all"), ([], "none")])
def test_features_rows_ror_variants(rar: Any, text: str) -> None:
    rows = dict(
        _features_rows(
            {"ror_as_reservoirs": rar, "auto_water_fail_cost": True, "vert_cost_cap": 1}
        )
    )
    assert rows["ror_as_reservoirs"] == text
    assert "spillway_cost_cap" not in rows  # obsolete under auto water-fail cost


def test_log_stats_with_all_sections(capsys: pytest.CaptureFixture[str]) -> None:
    planning: dict[str, Any] = {
        "options": {"model_options": {"use_kirchhoff": True}},
        "system": {
            "bus_array": [{"uid": 1}],
            "user_constraint_file": "uc.pampl",
            "demand_array": [{"fcost": 100.0}, {"fcost": 200.0}, {"fcost": "x"}],
        },
        "simulation": {
            "block_array": [{"duration": 720.0}],
            "stage_array": [{}],
            "scenario_array": [{}],
            "aperture_array": [{}, {}],
        },
        "_boundary_cuts_count": 5,
        "_boundary_state_variables": 2,
        "_skipped_isolated": ["Z", "A"],
    }
    _log_stats(planning, 1.25, {"plp_legacy": True})
    out = capsys.readouterr()
    text = out.out + out.err
    for needle in ("uc.pampl", "Apertures", "Boundary cuts", "100.00", "Features"):
        assert needle in text, needle
    assert "skip: A" in text


def test_log_stats_minimal(capsys: pytest.CaptureFixture[str]) -> None:
    _log_stats({"system": {"demand_array": [{"fcost": 5.0}]}}, 0.0)
    out = capsys.readouterr()
    assert "5.00" in out.out + out.err


def test_retune_cplex_prm(tmp_path: Path) -> None:
    prm = tmp_path / "cplex.prm"
    prm.write_text(
        "CPXPARAM_LPMethod 4\nCPXPARAM_MIP_Cuts_Gomory 2\nCPXPARAM_Threads 8\n",
        encoding="utf-8",
    )
    _retune_cplex_prm_for_iterative(prm)
    text = prm.read_text(encoding="utf-8")
    assert "CPXPARAM_MIP" not in text and "CPXPARAM_Threads 8" in text
    assert "CPXPARAM_LPMethod                               2" in text

    prm.write_text("CPXPARAM_LPMethod 4\nCPXPARAM_Threads 8\n", encoding="utf-8")
    _retune_cplex_prm_for_iterative(prm, invariant=True)
    text = prm.read_text(encoding="utf-8")
    assert "LPMethod" not in text and "INVARIANT" in text

    _retune_cplex_prm_for_iterative(tmp_path / "missing.prm")  # OSError: no-op
    assert not (tmp_path / "missing.prm").exists()


def test_install_solver_param_files_iterative(tmp_path: Path) -> None:
    installed = install_solver_param_files(tmp_path, iterative=True)
    names = {p.name for p in installed}
    assert "cplex.prm" in names
    assert "iterative" in (tmp_path / "solvers" / "cplex.prm").read_text(
        encoding="utf-8"
    )


def test_install_solver_param_files_broken_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(conv, "_BUNDLED_SOLVERS_DIR", tmp_path / "nope")
    with pytest.raises(conv.MissingDataFileError):
        install_solver_param_files(tmp_path / "out")
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(conv, "_BUNDLED_SOLVERS_DIR", empty)
    with pytest.raises(conv.MissingDataFileError):
        install_solver_param_files(tmp_path / "out")


def test_print_pumped_storage_template_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import gtopt_expand  # pylint: disable=import-outside-toplevel

    def boom(**_kw: Any) -> Any:
        raise ValueError("bad template")

    monkeypatch.setattr(gtopt_expand, "pumped_storage_default_config", boom)
    assert print_pumped_storage_template() == 1
    assert "bad template" in capsys.readouterr().err
