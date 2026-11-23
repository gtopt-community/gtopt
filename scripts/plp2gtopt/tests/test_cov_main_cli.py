"""CLI paths of :mod:`plp2gtopt.main` not covered by ``test_main_coverage``."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

from plp2gtopt.main import build_options, make_parser

# ``plp2gtopt.main`` the function shadows the submodule on the package.
main_mod = importlib.import_module("plp2gtopt.main")

_CASES = Path(__file__).resolve().parents[2] / "cases"
_MIN = _CASES / "plp_min_1bus"


def _opts(argv: list[str]) -> dict[str, Any]:
    return build_options(make_parser().parse_args(argv))


def test_build_options_sddp_tuning_flags(tmp_path: Path) -> None:
    ps = tmp_path / "ps.json"
    opts = _opts(
        [
            str(_MIN),
            "-o",
            str(tmp_path / "out"),
            "--forward-sampling-mode",
            "resampled",
            "--integer-cuts-mode",
            "none",
            "--min-iterations",
            "3",
            "--convergence-confidence",
            "0.9",
            "--stationary-gap-ceiling",
            "0.05",
            "--disable-discharge-limit-for",
            "RALCO",
            "--flow-right-fail-cost",
            "7",
            "--pumped-storage",
            str(ps),
            "--scale-theta",
            "2.5",
        ]
    )
    assert opts["forward_sampling_mode"] == "resampled"
    assert opts["integer_cuts_mode"] == "none"
    assert opts["min_iterations"] == 3
    assert opts["convergence_confidence"] == pytest.approx(0.9)
    assert opts["stationary_gap_ceiling"] == pytest.approx(0.05)
    assert opts["disable_discharge_limit_for"] == "RALCO"
    assert opts["flow_right_fail_cost"] == pytest.approx(7.0)
    assert opts["pumped_storage_files"] == [ps]
    assert opts["model_options"]["scale_theta"] == pytest.approx(2.5)


def test_main_tech_list(capsys: pytest.CaptureFixture[str]) -> None:
    main_mod.main(["--tech-list"])
    out = capsys.readouterr().out
    assert "Known generator technology types:" in out


def test_main_validate_exits(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main_mod.main([str(_MIN), "--validate", "-o", str(tmp_path / "o")])
    assert exc.value.code == 0


def test_main_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_mod, "print_variable_scales_template", lambda _o: 0)
    with pytest.raises(SystemExit) as exc:
        main_mod.main([str(_MIN), "--variable-scales-template"])
    assert exc.value.code == 0
    monkeypatch.setattr(main_mod, "print_pumped_storage_template", lambda: 3)
    with pytest.raises(SystemExit) as exc:
        main_mod.main([str(_MIN), "--pumped-storage-template"])
    assert exc.value.code == 3


def test_main_from_state_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main_mod.main(["--from-state", str(tmp_path / "nope.json")])
    assert exc.value.code == 2
    assert "--from-state" in capsys.readouterr().err


def test_main_convert_error_prints_usage_without_args(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def boom(_opts: dict[str, Any]) -> int:
        raise FileNotFoundError("no plpblo.dat")

    monkeypatch.setattr(main_mod, "convert_plp_case", boom)
    monkeypatch.setattr(sys, "argv", ["plp2gtopt"])
    with pytest.raises(SystemExit) as exc:
        main_mod.main([str(tmp_path), "-o", str(tmp_path / "o")])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "no plpblo.dat" in err and "Usage: plp2gtopt" in err


def test_main_snapshot_failure_and_critical(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(main_mod, "convert_plp_case", lambda _o: 2)

    def bad_snapshot(**_kw: Any) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(main_mod, "write_state_snapshot", bad_snapshot)
    with pytest.raises(SystemExit) as exc:
        main_mod.main([str(_MIN), "-o", str(tmp_path / "o")])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "failed to write state snapshot" in err
    assert "2 CRITICAL" in err


def test_plp_legacy_switches_method(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["plp2gtopt", "--plp-legacy"])
    args = make_parser().parse_args(["--plp-legacy", "-M", "monolithic"])
    main_mod._apply_plp_legacy_bundle(args)  # pylint: disable=protected-access
    assert args.method == "sddp"


def test_build_options_scale_theta_left_to_gtopt(tmp_path: Path) -> None:
    """Without --scale-theta the θ scale is not emitted (C++ auto-scales)."""
    for mode in ("node_angle", "cycle_basis"):
        opts = _opts([str(_MIN), "-o", str(tmp_path / "o"), "--kirchhoff-mode", mode])
        assert "scale_theta" not in opts["model_options"]
