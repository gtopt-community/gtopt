# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Orchestration tests for run_gtopt.main.main with every collaborator mocked.

No subprocess is ever launched: binary discovery, conversion, the gtopt
run, sanitizing, reporting and the pre/post-flight checks are replaced by
recording fakes so each branch of ``main`` is exercised deterministically.
"""

from __future__ import annotations

import json
import logging
import os
import signal
from pathlib import Path
from typing import Any

import pytest

from run_gtopt import main as main_mod
from run_gtopt._detect import CaseType


class _Recorder:
    """Collects calls made to the faked collaborators."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def fake(self, name: str, ret: Any = None):
        def _fn(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return ret

        return _fn

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def get(self, name: str) -> tuple[tuple[Any, ...], dict[str, Any]]:
        for n, a, k in self.calls:
            if n == name:
                return a, k
        raise KeyError(name)


@pytest.fixture(name="rec")
def _rec(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    # Never install real signal handlers in the test process.
    monkeypatch.setattr(main_mod.signal, "signal", rec.fake("signal"))
    monkeypatch.setattr(main_mod.logging, "basicConfig", rec.fake("basicConfig"))
    monkeypatch.setattr(main_mod, "detect_cpu_count", lambda: 4)
    monkeypatch.setattr(main_mod, "detect_compression_codec", lambda c: c)
    monkeypatch.setattr(main_mod, "find_gtopt_binary", lambda: "/bin/gtopt")
    monkeypatch.setattr(main_mod, "find_plp2gtopt", lambda: "/bin/plp2gtopt")
    monkeypatch.setattr(main_mod, "find_plexos2gtopt", lambda: "/bin/plexos2gtopt")
    monkeypatch.setattr(main_mod, "run_gtopt", rec.fake("run_gtopt", 0))
    monkeypatch.setattr(main_mod, "run_plp2gtopt", rec.fake("run_plp2gtopt", 0))
    monkeypatch.setattr(main_mod, "run_plexos2gtopt", rec.fake("run_plexos2gtopt", 0))
    monkeypatch.setattr(main_mod, "report_solution", rec.fake("report_solution"))
    monkeypatch.setattr(
        main_mod, "run_preflight_checks", rec.fake("run_preflight_checks", True)
    )
    monkeypatch.setattr(
        main_mod, "run_postflight_checks", rec.fake("run_postflight_checks")
    )
    return rec


def _gtopt_case(tmp_path: Path, name: str = "case") -> Path:
    case = tmp_path / name
    case.mkdir()
    (case / f"{name}.json").write_text(json.dumps({"options": {}}), encoding="utf-8")
    return case


def _plp_case(tmp_path: Path) -> Path:
    plp = tmp_path / "plp_x"
    plp.mkdir()
    (plp / "plpblo.dat").write_text("", encoding="utf-8")
    (plp / "plpbar.dat").write_text("", encoding="utf-8")
    return plp


def _exit_code(argv: list[str]) -> int | str | None:
    with pytest.raises(SystemExit) as exc:
        main_mod.main(argv)
    return exc.value.code


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def test_split_extra_args() -> None:
    assert not main_mod._split_extra_args(None)  # pylint: disable=protected-access
    assert main_mod._split_extra_args("-y 1 'a b'") == [  # pylint: disable=protected-access
        "-y",
        "1",
        "a b",
    ]


def test_signal_handler_exits_with_128_plus_sig(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main_mod._signal_handler(signal.SIGTERM, None)  # pylint: disable=protected-access
    assert exc.value.code == 128 + signal.SIGTERM
    assert "Caught signal" in capsys.readouterr().err


def test_dunder_main_invokes_main(monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy  # pylint: disable=import-outside-toplevel
    import sys  # pylint: disable=import-outside-toplevel

    called: list[bool] = []
    monkeypatch.setattr(main_mod, "main", lambda: called.append(True))
    sys.modules.pop("run_gtopt.__main__", None)
    runpy.run_module("run_gtopt.__main__", run_name="not_main")
    assert called == [True]


# ---------------------------------------------------------------------------
# Early-exit modes
# ---------------------------------------------------------------------------


def test_list_checks(rec: _Recorder, capsys: pytest.CaptureFixture[str]) -> None:
    main_mod.main(["--list-checks"])
    out = capsys.readouterr().out
    assert "CHECK         STAGE  DEFAULT  DESCRIPTION" in out
    assert "check_json    pre    off" in out
    assert "check_lp      post   on" in out
    assert "run_gtopt" not in rec.names()


def test_list_runs(rec: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_mod, "list_runs", lambda: ["r1"])
    monkeypatch.setattr(main_mod, "print_run_list", rec.fake("print_run_list"))
    main_mod.main(["--list"])
    assert rec.get("print_run_list")[0] == (["r1"],)


def test_attach_unresolved(
    rec: _Recorder,  # pylint: disable=unused-argument
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(main_mod, "resolve_target", lambda _t: None)
    assert _exit_code(["--attach", "999"]) == 2
    assert "could not resolve --attach" in capsys.readouterr().err


def test_attach_resolved(rec: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_mod, "resolve_target", lambda _t: "INFO")
    monkeypatch.setattr(main_mod, "run_attach_loop", rec.fake("attach", 7))
    assert _exit_code(["--attach", "123"]) == 7
    assert rec.get("attach")[0] == ("INFO",)


def test_tty_info_level_quiets_root_handlers(
    rec: _Recorder,  # pylint: disable=unused-argument
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Tty:
        @staticmethod
        def isatty() -> bool:
            return True

        @staticmethod
        def write(_s: str) -> int:
            return 0

        @staticmethod
        def flush() -> None:
            return None

    handler = logging.NullHandler()
    handler.setLevel(logging.DEBUG)
    monkeypatch.setattr(logging.root, "handlers", [handler])
    monkeypatch.setattr(main_mod.sys, "stdout", _Tty())
    main_mod.main(["--list-checks"])
    assert handler.level == logging.WARNING


# ---------------------------------------------------------------------------
# gtopt case directory
# ---------------------------------------------------------------------------


def test_gtopt_case_full_run_cleans_sanitized_and_reports(
    rec: _Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = _gtopt_case(tmp_path)
    # A previous run's results (and cuts) must survive; only the per-run
    # status / stop-request files are dropped.
    prev = case / "results"
    prev.mkdir()
    (prev / "old.csv").write_text("x", encoding="utf-8")
    out = case / "output"
    out.mkdir()
    (out / "sddp_cuts.csv").write_text("cuts", encoding="utf-8")
    (out / "solver_status.json").write_text("{}", encoding="utf-8")
    (out / "sddp_stop_request.json").write_text("{}", encoding="utf-8")
    sanitized = tmp_path / "sanitized.json"
    sanitized.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main_mod, "sanitize_json", rec.fake("sanitize", sanitized))

    code = _exit_code(
        [
            str(case),
            "--enable-check",
            "check_json",
            "--disable-check",
            "check_json",
            "--",
            "--solver",
            "highs",
        ]
    )
    assert code == 0
    assert (prev / "old.csv").exists()
    assert (out / "sddp_cuts.csv").exists()
    assert not (out / "solver_status.json").exists()
    assert not (out / "sddp_stop_request.json").exists()
    assert not sanitized.exists()
    args, _ = rec.get("run_gtopt")
    assert args[0] == "/bin/gtopt"
    assert args[1] == sanitized
    assert args[4] == ["--solver", "highs"]
    _, pre_kw = rec.get("run_preflight_checks")
    assert pre_kw["enabled"] == set()
    rargs, _ = rec.get("report_solution")
    assert rargs[2] == case / "case.json"
    _, post_kw = rec.get("run_postflight_checks")
    assert post_kw["gtopt_rc"] == 0


def test_gtopt_case_uses_results_planning_json_and_keeps_export(
    rec: _Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = _gtopt_case(tmp_path)
    sanitized = tmp_path / "export.json"
    sanitized.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main_mod, "sanitize_json", rec.fake("sanitize", sanitized))

    def _run(*args: Any, **_kw: Any) -> int:
        res = case / "output"
        res.mkdir(exist_ok=True)
        (res / "planning.json").write_text("{}", encoding="utf-8")
        rec.calls.append(("run_gtopt", args, {}))
        return 3

    monkeypatch.setattr(main_mod, "run_gtopt", _run)
    code = _exit_code([str(case), "--export-json", str(sanitized)])
    assert code == 3
    assert sanitized.exists()  # exported -> kept
    rargs, _ = rec.get("report_solution")
    assert rargs[0] == case / "output"
    assert rargs[2] == case / "output" / "planning.json"
    pargs, pkw = rec.get("run_postflight_checks")
    assert pargs[0] == case / "output"
    assert pargs[1] == case / "output" / "planning.json"
    assert pkw["gtopt_rc"] == 3
    assert "check_json" not in pkw["enabled"]


def test_gtopt_case_sanitized_unlink_error_ignored(
    rec: _Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = _gtopt_case(tmp_path)
    missing = tmp_path / "never_written.json"  # unlink -> FileNotFoundError
    monkeypatch.setattr(main_mod, "sanitize_json", rec.fake("sanitize", missing))
    assert _exit_code([str(case), "--no-check"]) == 0
    assert "run_preflight_checks" not in rec.names()
    assert "run_postflight_checks" not in rec.names()


def test_gtopt_case_sanitize_returns_same_file_runs_dir(
    rec: _Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = _gtopt_case(tmp_path)
    monkeypatch.setattr(
        main_mod, "sanitize_json", rec.fake("sanitize", case / "case.json")
    )
    assert _exit_code([str(case)]) == 0
    assert rec.get("run_gtopt")[0][1] == case


def test_gtopt_case_preflight_failure(
    rec: _Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = _gtopt_case(tmp_path)
    monkeypatch.setattr(main_mod, "run_preflight_checks", rec.fake("pre", False))
    assert _exit_code([str(case), "--strict"]) == 2
    assert rec.get("pre")[1]["strict"] is True


def test_gtopt_case_sanitize_failure(
    rec: _Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    case = _gtopt_case(tmp_path)
    monkeypatch.setattr(main_mod, "sanitize_json", rec.fake("sanitize", None))
    assert _exit_code([str(case)]) == 2
    assert "run_gtopt" not in rec.names()


def test_gtopt_case_binary_missing(
    rec: _Recorder,  # pylint: disable=unused-argument
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _gtopt_case(tmp_path)
    monkeypatch.setattr(main_mod, "find_gtopt_binary", lambda: None)
    assert _exit_code([str(case), "--dry-run"]) == 2
    assert "gtopt binary not found" in capsys.readouterr().err


def test_gtopt_case_dry_run_prints_real_argv(
    rec: _Recorder,  # pylint: disable=unused-argument
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--dry-run prints only real gtopt flags plus the sanitize diff."""
    case = _gtopt_case(tmp_path)
    main_mod.main(
        [str(case), "--dry-run", "-t", "8", "-C", "zstd", "--", "--solver", "highs"]
    )
    out = capsys.readouterr().out
    assert "--lp-threads" not in out
    assert "--output-compression" not in out
    assert "[dry-run] sanitize options.solver_options.threads: None -> 8" in out
    assert "[dry-run] sanitize options.output_compression: None -> 'zstd'" in out
    sanitized = case / "case_sanitized.json"
    assert f"[dry-run] /bin/gtopt {sanitized} --solver highs" in out
    assert not sanitized.exists()  # preview only


def test_gtopt_case_dry_run_unchanged_json(
    rec: _Recorder,  # pylint: disable=unused-argument
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _gtopt_case(tmp_path)
    monkeypatch.setattr(main_mod, "sanitize_json", lambda p, **_k: p)
    main_mod.main([str(case), "--dry-run"])
    out = capsys.readouterr().out
    assert "sanitize" not in out
    assert f"[dry-run] /bin/gtopt {case}" in out


def test_gtopt_case_dry_run_without_json(
    rec: _Recorder,  # pylint: disable=unused-argument
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _gtopt_case(tmp_path)
    (case / "case.json").unlink()
    monkeypatch.setattr(main_mod, "detect_case_type", lambda _p: CaseType.GTOPT)
    main_mod.main([str(case), "--dry-run"])
    assert f"[dry-run] /bin/gtopt {case}" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# PLP case
# ---------------------------------------------------------------------------


def test_plp_missing_converter(
    rec: _Recorder,  # pylint: disable=unused-argument
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(main_mod, "find_plp2gtopt", lambda: None)
    assert _exit_code([str(_plp_case(tmp_path))]) == 2
    assert "plp2gtopt not found" in capsys.readouterr().err


def test_plp_dry_run_with_extra_args_continues_to_solve_dry_run(
    rec: _Recorder,  # pylint: disable=unused-argument
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plp = _plp_case(tmp_path)
    out_dir = tmp_path / "out"
    main_mod.main([str(plp), "--dry-run", "-o", str(out_dir), "--plp-args", "-y 1"])
    out = capsys.readouterr().out
    assert f"/bin/plp2gtopt {plp} -o {out_dir} -y 1" in out
    assert f"[dry-run] /bin/gtopt {out_dir}" in out


def test_plp_conversion_failure_propagates_rc(
    rec: _Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(main_mod, "run_plp2gtopt", rec.fake("plp", 5))
    assert _exit_code([str(_plp_case(tmp_path))]) == 5


def test_plp_convert_only(rec: _Recorder, tmp_path: Path) -> None:
    main_mod.main([str(_plp_case(tmp_path)), "--convert-only"])
    assert "run_plp2gtopt" in rec.names()
    assert "run_gtopt" not in rec.names()


# ---------------------------------------------------------------------------
# PLEXOS bundle
# ---------------------------------------------------------------------------


@pytest.fixture(name="plexos")
def _plexos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    bundle = tmp_path / "PLEXOS2026.zip"
    bundle.write_text("", encoding="utf-8")
    monkeypatch.setattr(main_mod, "detect_case_type", lambda _p: CaseType.PLEXOS)
    return bundle


def test_plexos_missing_converter(
    rec: _Recorder,  # pylint: disable=unused-argument
    plexos: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(main_mod, "find_plexos2gtopt", lambda: None)
    assert _exit_code([str(plexos)]) == 2
    assert "plexos2gtopt not found" in capsys.readouterr().err


def test_plexos_dry_run_with_args(
    rec: _Recorder,  # pylint: disable=unused-argument
    plexos: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out_dir = tmp_path / "g"
    main_mod.main(
        [str(plexos), "--dry-run", "-o", str(out_dir), "--plexos-args", "--a b"]
    )
    out = capsys.readouterr().out
    assert f"/bin/plexos2gtopt {plexos} -o {out_dir} --a b" in out


def test_plexos_conversion_failure(
    rec: _Recorder, plexos: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_mod, "run_plexos2gtopt", rec.fake("px", 4))
    assert _exit_code([str(plexos)]) == 4


def test_plexos_convert_only(rec: _Recorder, plexos: Path) -> None:
    main_mod.main([str(plexos), "--convert-only"])
    assert "run_plexos2gtopt" in rec.names()
    assert "run_gtopt" not in rec.names()


def test_plexos_runs_with_stem_json(
    rec: _Recorder, plexos: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out_dir = tmp_path / "gx"

    def _convert(*args: Any, **_kw: Any) -> int:
        out_dir.mkdir()
        (out_dir / "PLEXOS2026.json").write_text("{}", encoding="utf-8")
        rec.calls.append(("run_plexos2gtopt", args, {}))
        return 0

    monkeypatch.setattr(main_mod, "run_plexos2gtopt", _convert)
    sanitized = tmp_path / "s.json"
    sanitized.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main_mod, "sanitize_json", rec.fake("sanitize", sanitized))
    assert _exit_code([str(plexos), "-o", str(out_dir)]) == 0
    sargs, _ = rec.get("sanitize")
    assert sargs[0] == out_dir / "PLEXOS2026.json"


# ---------------------------------------------------------------------------
# Passthrough
# ---------------------------------------------------------------------------


def test_passthrough_binary_missing(
    rec: _Recorder,  # pylint: disable=unused-argument
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = tmp_path / "p.json"
    plan.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main_mod, "find_gtopt_binary", lambda: None)
    assert _exit_code([str(plan)]) == 2
    assert "GTOPT_BIN" in capsys.readouterr().err


def test_passthrough_runs_gtopt(rec: _Recorder, tmp_path: Path) -> None:
    plan = tmp_path / "p.json"
    plan.write_text("{}", encoding="utf-8")
    assert _exit_code([str(plan), "-t", "2", "--", "--foo"]) == 0
    args, _ = rec.get("run_gtopt")
    assert args == ("/bin/gtopt", plan, 2, None, ["--foo"])


def test_argv_defaults_to_sys_argv(
    rec: _Recorder,  # pylint: disable=unused-argument
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(main_mod.sys, "argv", ["run_gtopt", "--list-checks"])
    main_mod.main()
    assert "STAGE  DEFAULT" in capsys.readouterr().out


def test_plexos_dry_run_without_extra_args(
    rec: _Recorder,  # pylint: disable=unused-argument
    plexos: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main_mod.main([str(plexos), "--dry-run", "--convert-only"])
    out = capsys.readouterr().out
    assert out.strip().endswith("gtopt_PLEXOS2026")


@pytest.mark.parametrize(("argv", "expected"), [([], None), (["-C", "lz4"], "lz4")])
def test_compression_only_forwarded_when_user_sets_it(
    rec: _Recorder,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    expected: str | None,
) -> None:
    """No default -C: the case / gtopt codec stays unless the user asks."""
    case = _gtopt_case(tmp_path)
    monkeypatch.setattr(
        main_mod, "sanitize_json", rec.fake("sanitize", case / "case.json")
    )
    assert _exit_code([str(case), *argv]) == 0
    assert rec.get("sanitize")[1]["compression"] == expected


def test_output_dir_follows_json_and_cli_override(
    rec: _Recorder, tmp_path: Path
) -> None:
    """Report / postflight / run_gtopt all use gtopt's real output dir."""
    case = tmp_path / "c"
    case.mkdir()
    (case / "c.json").write_text(
        json.dumps({"options": {"output_directory": "results"}}), encoding="utf-8"
    )
    assert _exit_code([str(case), "--no-check"]) == 0
    assert rec.get("run_gtopt")[1]["output_dir"] == case / "results"
    assert rec.get("report_solution")[0][0] == case / "results"
    rec.calls.clear()
    other = tmp_path / "elsewhere"
    assert _exit_code([str(case), "--no-check", "--", "-d", str(other)]) == 0
    assert rec.get("run_gtopt")[1]["output_dir"] == other
    assert rec.get("report_solution")[1]["since"] is not None


def test_sanitize_preview_lists_only_changed_keys(tmp_path: Path) -> None:
    plan = tmp_path / "p.json"
    plan.write_text(
        json.dumps({"options": {"method": "sddp", "solver_options": {"x": 1}}}),
        encoding="utf-8",
    )
    target, changes = main_mod._sanitize_preview(plan, None, 4)  # pylint: disable=protected-access
    assert target == tmp_path / "p_sanitized.json"
    assert changes == ["options.solver_options.threads: None -> 4"]


def test_header_gets_original_case_name(rec: _Recorder, tmp_path: Path) -> None:
    case = _gtopt_case(tmp_path, name="ieee_9b")
    assert _exit_code([str(case), "--no-check", "-t", "2"]) == 0
    assert rec.get("run_gtopt")[1]["case_name"] == "ieee_9b"


@pytest.mark.parametrize(
    ("extra", "env", "solver"),
    [
        ([], None, "auto"),
        ([], "cplex", "cplex"),
        (["--solver", "highs"], "cplex", "highs"),
        (["--solver=clp"], None, "clp"),
        (["--solver"], None, "auto"),
    ],
)
def test_run_summary_line(
    monkeypatch: pytest.MonkeyPatch,
    extra: list[str],
    env: str | None,
    solver: str,
) -> None:
    if env is None:
        monkeypatch.delenv("GTOPT_SOLVER", raising=False)
    else:
        monkeypatch.setenv("GTOPT_SOLVER", env)
    line = main_mod.run_summary("/bin/gtopt", "ieee_9b", 8, Path("/o"), extra)
    assert line == (
        f"gtopt /bin/gtopt | solver {solver} | 8 threads | case ieee_9b | output /o"
    )


def test_pre_run_chatter_is_debug(
    rec: _Recorder,  # pylint: disable=unused-argument
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """At INFO only the one-line run summary is logged by main."""
    case = _gtopt_case(tmp_path)
    with caplog.at_level(logging.INFO, logger="run_gtopt.main"):
        assert _exit_code([str(case), "--no-check"]) == 0
    main_lines = [r.getMessage() for r in caplog.records if r.name == "run_gtopt.main"]
    assert len(main_lines) == 1
    assert main_lines[0].startswith("gtopt /bin/gtopt | solver")


def test_short_l_is_deprecated_alias(
    rec: _Recorder, capsys: pytest.CaptureFixture[str]
) -> None:
    main_mod.main(["-l", "DEBUG", "--list-checks"])
    assert "-l is deprecated" in capsys.readouterr().err
    _, kw = rec.get("basicConfig")
    assert kw["level"] == logging.DEBUG


def test_log_level_long_form_no_warning(
    rec: _Recorder,  # pylint: disable=unused-argument
    capsys: pytest.CaptureFixture[str],
) -> None:
    main_mod.main(["--log-level", "WARNING", "--list-checks"])
    assert "deprecated" not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--solver", "highs"], ["--solver", "highs"]),
        (["--solver", "highs", "--", "--stats"], ["--solver", "highs", "--stats"]),
        (["--solver", "highs", "--", "--solver", "cplex"], ["--solver", "cplex"]),
        (["--solver", "highs", "--", "--solver=cplex"], ["--solver=cplex"]),
    ],
)
def test_solver_flag_forwarded(
    rec: _Recorder, tmp_path: Path, argv: list[str], expected: list[str]
) -> None:
    case = _gtopt_case(tmp_path)
    assert _exit_code([str(case), "--no-check", *argv]) == 0
    assert rec.get("run_gtopt")[0][4] == expected


@pytest.mark.parametrize("mode", ["off", "estimate", "exact"])
def test_kappa_mode_flag_forwarded(rec: _Recorder, tmp_path: Path, mode: str) -> None:
    case = _gtopt_case(tmp_path)
    assert _exit_code([str(case), "--no-check", "--kappa-mode", mode]) == 0
    assert rec.get("run_gtopt")[0][4] == [
        "--set",
        f"solver_options.kappa_mode={mode}",
    ]


def test_kappa_mode_flag_rejects_unknown() -> None:
    with pytest.raises(SystemExit):
        main_mod.make_parser().parse_args(["x", "--kappa-mode", "fast"])


@pytest.mark.parametrize("flag", ["--convert-dir", "-o", "--output-dir"])
def test_convert_dir_aliases(flag: str) -> None:
    args = main_mod.make_parser().parse_args(["x", flag, "out"])
    assert args.output_dir == Path("out")


@pytest.mark.parametrize(
    ("runs", "code", "attached", "msg"),
    [
        (["only"], 7, True, ""),
        ([], 2, False, ""),
        (["a", "b"], 2, False, "several live gtopt runs"),
    ],
)
def test_attach_without_argument(
    rec: _Recorder,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runs: list[str],
    code: int,
    attached: bool,
    msg: str,
) -> None:
    monkeypatch.setattr(main_mod, "list_runs", lambda: runs)
    monkeypatch.setattr(main_mod, "print_run_list", rec.fake("print_run_list"))
    monkeypatch.setattr(main_mod, "run_attach_loop", rec.fake("attach", 7))
    assert _exit_code(["--attach"]) == code
    assert ("attach" in rec.names()) is attached
    assert ("print_run_list" in rec.names()) is not attached
    assert msg in capsys.readouterr().err


def test_ascii_flag_sets_env(rec: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GTOPT_TUI_ASCII", raising=False)
    monkeypatch.setattr(main_mod, "list_runs", lambda: [])
    monkeypatch.setattr(main_mod, "print_run_list", rec.fake("print_run_list"))
    main_mod.main(["--ascii", "--list"])
    # main() sets it directly; pop it (monkeypatch.delenv here would
    # restore "1" at teardown and leak ASCII mode into later tests).
    assert os.environ.pop("GTOPT_TUI_ASCII", None) == "1"


def _status_writer(out: Path, status: dict[str, Any], rec: _Recorder) -> Any:
    def _run(*args: Any, **_kw: Any) -> int:
        out.mkdir(parents=True, exist_ok=True)
        (out / "solver_status.json").write_text(json.dumps(status), encoding="utf-8")
        rec.calls.append(("run_gtopt", args, {}))
        return 0

    return _run


@pytest.mark.parametrize(
    ("status", "flag", "code"),
    [
        ({"status": "max_iterations", "converged": False}, True, 4),
        ({"status": "stopped"}, True, 4),
        ({"status": "converged", "converged": True}, True, 0),
        ({"status": "max_iterations"}, False, 0),
    ],
)
def test_fail_on_not_converged(
    rec: _Recorder,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    status: dict[str, Any],
    flag: bool,
    code: int,
) -> None:
    case = _gtopt_case(tmp_path)
    monkeypatch.setattr(
        main_mod, "sanitize_json", rec.fake("sanitize", case / "case.json")
    )
    # gtopt gets the case directory (no sanitized copy): with no
    # options.output_directory it writes to <case>/results.
    monkeypatch.setattr(
        main_mod, "run_gtopt", _status_writer(case / "results", status, rec)
    )
    argv = [str(case), "--no-check"] + (["--fail-on-not-converged"] if flag else [])
    assert _exit_code(argv) == code
    assert ("did not converge" in capsys.readouterr().err) is (code == 4)


def test_fail_on_not_converged_direct_json(
    rec: _Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = tmp_path / "p.json"
    plan.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        main_mod,
        "run_gtopt",
        _status_writer(tmp_path / "output", {"status": "max_iterations"}, rec),
    )
    assert _exit_code([str(plan), "--fail-on-not-converged"]) == 4


def test_convergence_exit_code_edge_cases(tmp_path: Path) -> None:
    fn = main_mod.convergence_exit_code
    assert fn(3, tmp_path, 0.0) == 3
    assert fn(0, None, 0.0) == 0
    assert fn(0, tmp_path, 0.0) == 0  # no status file
    status = tmp_path / "solver_status.json"
    status.write_text(json.dumps({"status": "stopped"}), encoding="utf-8")
    assert fn(0, tmp_path, status.stat().st_mtime + 10) == 0  # stale
    # Written just after the run started, but the coarse mtime clock put it
    # a few ms before ``since``: still this run's file.
    assert fn(0, tmp_path, status.stat().st_mtime + 0.005) == 4
    status.write_text("[1]", encoding="utf-8")
    assert fn(0, tmp_path, 0.0) == 0
    status.write_text("{bad", encoding="utf-8")
    assert fn(0, tmp_path, 0.0) == 0
