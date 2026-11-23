# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for the gtopt_check_json CLI, engine and misc checks."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from gtopt_check_json import _engine_validate as ev
from gtopt_check_json import _general_checks as gc
from gtopt_check_json import gtopt_check_json as cj
from gtopt_check_json._checks_common import Finding, Severity
from gtopt_check_json._hydro_checks import (
    check_affluent_nonneg,
    check_seepage_at_vmin,
)

_PKG_DIR = Path(cj.__file__).resolve().parent


def _fresh_module(
    monkeypatch: pytest.MonkeyPatch, name: str, filename: str
) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _PKG_DIR / filename)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolve annotations through sys.modules[cls.__module__]
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# import-time fallbacks
# ---------------------------------------------------------------------------


def test_ai_options_shim_without_check_lp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "gtopt_check_lp._ai", None)
    mod = _fresh_module(monkeypatch, "_cj_no_lp", "gtopt_check_json.py")
    opts = mod.AiOptions()
    assert opts.enabled
    assert opts.provider == "claude"
    assert opts.timeout == 60


def test_uc_family_fallback_without_plexos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "plexos2gtopt.uc_families", None)
    mod = _fresh_module(monkeypatch, "_info_no_plexos", "_info.py")
    assert mod.UC_FAMILY_ORDER == ("operational",)
    assert mod.uc_family("uc_anything") == "operational"


# ---------------------------------------------------------------------------
# helpers of the CLI module
# ---------------------------------------------------------------------------


def _write(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_planning_merges_and_resolves_suffix(tmp_path: Path) -> None:
    _write(tmp_path / "a.json", {"options": {"x": 1}, "system": {"name": "s"}})
    _write(tmp_path / "b.json", {"options": {"y": 2}, "system": 5, "extra": [1]})
    planning, case_dir = cj._load_planning(  # noqa: SLF001
        [str(tmp_path / "a"), str(tmp_path / "b.json")]
    )
    assert planning == {"options": {"x": 1, "y": 2}, "system": 5, "extra": [1]}
    assert case_dir == str(tmp_path.resolve())


def test_load_planning_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        cj._load_planning([str(tmp_path / "none.json")])  # noqa: SLF001
    assert exc.value.code == 1
    assert "file not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("severity", "tag"),
    [
        (Severity.CRITICAL, "[CRITICAL]"),
        (Severity.WARNING, "[WARNING]"),
        (Severity.NOTE, "[NOTE]"),
    ],
)
def test_format_finding(severity: Severity, tag: str) -> None:
    text = cj._format_finding(  # noqa: SLF001
        Finding(check_id="cid", severity=severity, message="msg")
    )
    assert tag in text
    assert "(cid) msg" in text


# ---------------------------------------------------------------------------
# check_json
# ---------------------------------------------------------------------------


def _case(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "case.json",
        {
            "system": {
                "bus_array": [{"uid": 1, "name": "b1"}, {"uid": 2, "name": "b2"}],
                "generator_array": [{"uid": 1, "name": "g1", "bus": "b1"}],
                "demand_array": [{"uid": 1, "name": "d1", "bus": "b2"}],
            },
        },
    )


def _config(tmp_path: Path, ai: str = "false", engine: str = "false") -> Path:
    cfg = tmp_path / "gtopt.conf"
    cfg.write_text(
        f"[global]\nai_enabled = {ai}\n\n"
        "[gtopt_check_json]\n"
        f"check_engine_validate = {engine}\n"
        "check_ai_system_analysis = true\n",
        encoding="utf-8",
    )
    return cfg


def test_check_json_info_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cj.check_json(
        [str(_case(tmp_path))], info_only=True, config_path=_config(tmp_path)
    )
    assert rc == 0
    assert "Buses" in capsys.readouterr().err


def test_check_json_full_run_with_islands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def _fake_run_all(planning: Any, **kwargs: Any) -> list[Finding]:
        seen.update(kwargs)
        return [
            Finding(
                check_id="bus_connectivity", severity=Severity.WARNING, message="i"
            ),
            Finding(check_id="x", severity=Severity.CRITICAL, message="boom"),
            Finding(check_id="y", severity=Severity.NOTE, message="fyi"),
        ]

    monkeypatch.setattr(cj, "run_all_checks", _fake_run_all)
    rc = cj.check_json([str(_case(tmp_path))], config_path=_config(tmp_path, ai="yes"))
    assert rc == 1
    assert seen["ai_options"] is not None
    assert seen["ai_options"].provider == "claude"
    assert "engine_validate" not in seen["enabled_checks"]
    out = capsys.readouterr()
    assert "Case:" in out.out
    assert "boom" in out.err
    assert "Summary: 1 critical, 1 warnings, 1 notes" in out.err


def test_check_json_disabled_ai_options_and_no_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def _fake_run_all(_planning: Any, **kwargs: Any) -> list[Finding]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(cj, "run_all_checks", _fake_run_all)
    monkeypatch.setattr(cj, "default_config_path", lambda: _config(tmp_path))
    path = _write(tmp_path / "one.json", {"system": {"bus_array": [{"uid": 1}]}})
    rc = cj.check_json([str(path)], ai_options=cj.AiOptions(enabled=False))
    assert rc == 0
    assert seen["ai_options"] is None
    assert "All checks passed" in capsys.readouterr().err

    # AI enabled in the config but the check itself disabled -> no options.
    cfg = tmp_path / "noai.conf"
    cfg.write_text(
        "[global]\nai_enabled = true\n\n[gtopt_check_json]\n"
        "check_engine_validate = false\n",
        encoding="utf-8",
    )
    assert cj.check_json([str(path)], config_path=cfg) == 0
    assert seen["ai_options"] is None
    # Explicitly enabled options are passed through unchanged.
    opts = cj.AiOptions(enabled=True)
    cj.check_json([str(path)], config_path=cfg, ai_options=opts)
    assert seen["ai_options"] is opts


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_config_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _config(tmp_path)
    calls: list[tuple[Path, bool]] = []
    monkeypatch.setattr(
        cj, "run_interactive_setup", lambda p, use_color: calls.append((p, use_color))
    )
    monkeypatch.setattr(cj.col, "USE_COLOR", True)
    assert cj.main(["--init-config", "--no-color", "--config", str(cfg)]) == 0
    assert calls == [(cfg, False)]
    assert cj.main(["--show-config", "--config", str(cfg)]) == 0
    assert "check_engine_validate = 'false'" in capsys.readouterr().out
    assert cj.main(["--config", str(cfg)]) == 2
    assert "no JSON files" in capsys.readouterr().err


def test_main_show_simulation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[dict[str, Any]] = []
    fake = ModuleType("plp2gtopt.plp2gtopt")
    fake.show_simulation_summary = shown.append  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "plp2gtopt.plp2gtopt", fake)
    path = _case(tmp_path)
    assert (
        cj.main(["--show-simulation", str(path), "--config", str(_config(tmp_path))])
        == 0
    )
    assert shown[0]["system"]["bus_array"][0]["name"] == "b1"


def test_main_ai_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Any] = []

    def _fake_check_json(paths: list[str], **kwargs: Any) -> int:
        captured.append((paths, kwargs))
        return 7

    monkeypatch.setattr(cj, "check_json", _fake_check_json)
    cfg = tmp_path / "ai.conf"
    cfg.write_text(
        "[global]\nai_enabled = true\nai_provider = openai\nai_model = m0\n"
        "ai_prompt = p0\n",
        encoding="utf-8",
    )
    assert cj.main(["c.json", "--config", str(cfg), "--info"]) == 7
    _paths, kw = captured[-1]
    opts = kw["ai_options"]
    assert (opts.enabled, opts.provider, opts.model, opts.prompt, opts.key) == (
        True,
        "openai",
        "m0",
        "p0",
        "",
    )
    assert kw["info_only"]
    cj.main(
        [
            "c.json",
            "--config",
            str(cfg),
            "--no-ai",
            "--ai-provider",
            "deepseek",
            "--ai-model",
            "m1",
            "--ai-prompt",
            "p1 {report}",
            "--ai-key",
            "k1",
        ]
    )
    opts = captured[-1][1]["ai_options"]
    assert (opts.enabled, opts.provider, opts.model, opts.prompt, opts.key) == (
        False,
        "deepseek",
        "m1",
        "p1 {report}",
        "k1",
    )


# ---------------------------------------------------------------------------
# engine validation (gtopt binary is never executed)
# ---------------------------------------------------------------------------


def test_find_binary_search_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GTOPT_BIN", raising=False)
    monkeypatch.setattr(ev.shutil, "which", lambda _n: "/opt/bin/gtopt")
    assert ev._find_gtopt_binary() == "/opt/bin/gtopt"  # noqa: SLF001

    monkeypatch.setattr(ev.shutil, "which", lambda _n: None)
    monkeypatch.setattr(ev, "_standard_build_paths", lambda: [tmp_path / "none"])
    assert ev._find_gtopt_binary() is None  # noqa: SLF001

    exe = tmp_path / "gtopt"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(ev, "_standard_build_paths", lambda: [tmp_path / "none", exe])
    assert ev._find_gtopt_binary() == str(exe)  # noqa: SLF001

    monkeypatch.setattr(ev, "_standard_build_paths", list)
    monkeypatch.setenv("GTOPT_BIN", str(exe))
    assert ev._find_gtopt_binary() == str(exe)  # noqa: SLF001


def test_engine_validate_invocation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ev, "_find_gtopt_binary", lambda: "/opt/bin/gtopt")

    def _fail(*_a: Any, **_k: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="gtopt", timeout=120)

    monkeypatch.setattr(ev.subprocess, "run", _fail)
    findings = ev.check_engine_validate({}, json_paths=["c.json"])
    assert len(findings) == 1
    assert "invocation failed" in findings[0].message


def test_engine_validate_parses_latest_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ev, "_find_gtopt_binary", lambda: "/opt/bin/gtopt")

    def _fake_run(cmd: list[str], **_k: Any) -> SimpleNamespace:
        out_dir = Path(
            next(a for a in cmd if a.startswith("log_directory=")).split("=", 1)[1]
        )
        (out_dir / "gtopt_1.log").write_text("[warning] Validation: old\n", "utf-8")
        (out_dir / "gtopt_2.log").write_text(
            "[2026] [warning] Validation: low pmax\n"
            "[2026] [error] [file.cpp:10] Validation: bad ref\n"
            "plain line\n",
            "utf-8",
        )
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(ev.subprocess, "run", _fake_run)
    findings = ev.check_engine_validate({}, json_paths=["c.json"])
    assert [(f.severity, f.message) for f in findings] == [
        (Severity.WARNING, "low pmax"),
        (Severity.CRITICAL, "bad ref"),
    ]


# ---------------------------------------------------------------------------
# general / hydro checks
# ---------------------------------------------------------------------------


def test_uid_uniqueness_in_simulation() -> None:
    planning = {"simulation": {"block_array": [{"uid": 1}, {"uid": 1}]}}
    findings = gc.check_uid_uniqueness(planning)
    assert any("duplicate uid=1" in f.message for f in findings)


def test_state_variable_names() -> None:
    planning = {
        "system": {
            "junction_array": [
                {"uid": 1, "name": "j1"},
                {"uid": 2, "name": "j2"},
                {"uid": 3},
            ],
            "reservoir_array": [
                {"name": "r_off", "junction": 1, "use_state_variable": False},
                {"name": "r_named"},
                {},
                {"junction": "j2"},
                {"junction": "ghost"},
                {"junction": 1},
                {"junction": 99},
            ],
            "battery_array": [{"name": "bat"}, {}],
        }
    }
    names = gc._build_state_variable_names(planning)  # noqa: SLF001
    assert names == {"r_named", "j1", "j2", "bat"}


def test_schema_keys_issue_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    issues = [
        SimpleNamespace(kind="unknown", message="bad key"),
        SimpleNamespace(kind="alias", message="alias key"),
        SimpleNamespace(kind="deprecated", message="old key"),
    ]
    monkeypatch.setattr(gc, "find_schema_problems", lambda _p: issues)
    findings = gc.check_schema_keys({})
    assert [(f.severity, f.message) for f in findings] == [
        (Severity.CRITICAL, "bad key"),
        (Severity.NOTE, "alias key"),
    ]


def test_schema_keys_real_unknown_key() -> None:
    findings = gc.check_schema_keys({"system": {"bus_array": [{"uid": 1, "zzz": 1}]}})
    assert any(f.severity == Severity.CRITICAL for f in findings)


def _cuts_planning(cuts: str, **system: Any) -> dict[str, Any]:
    return {
        "system": {
            "future_cost_array": [{"uid": 1, "name": "fcf", "cuts_file": cuts}],
            **system,
        }
    }


def test_boundary_cuts_edge_cases(tmp_path: Path) -> None:
    # unreadable (a directory)
    (tmp_path / "dir.csv").mkdir()
    findings = gc.check_boundary_cuts(_cuts_planning("dir.csv"), base_dir=str(tmp_path))
    assert "cannot read" in findings[0].message
    # all columns fixed -> nothing to check
    (tmp_path / "fixed.csv").write_text("iteration,scene,rhs\n1,1,2\n", "utf-8")
    assert not gc.check_boundary_cuts(
        _cuts_planning("fixed.csv"), base_dir=str(tmp_path)
    )
    # empty file -> no header
    (tmp_path / "empty.csv").write_text("", "utf-8")
    assert not gc.check_boundary_cuts(
        _cuts_planning("empty.csv"), base_dir=str(tmp_path)
    )
    # state columns but model has no state variables
    (tmp_path / "cuts.csv").write_text("scene,rhs,r1,r2\n1,0,1,2\n", "utf-8")
    findings = gc.check_boundary_cuts(
        _cuts_planning("cuts.csv"), base_dir=str(tmp_path)
    )
    assert "2 state variable column(s)" in findings[0].message
    # all known -> clean
    planning = _cuts_planning(
        "cuts.csv", reservoir_array=[{"name": "r1"}], battery_array=[{"name": "r2"}]
    )
    assert not gc.check_boundary_cuts(planning, base_dir=str(tmp_path))


def test_affluent_nonneg_mixed() -> None:
    planning = {
        "system": {
            "flow_array": [
                {"uid": 1, "affluent": 3.0},
                {"uid": 2, "affluent": [1.0, -2.0]},
            ]
        }
    }
    findings = check_affluent_nonneg(planning)
    assert len(findings) == 1
    assert "index 1" in findings[0].message


def test_seepage_at_vmin() -> None:
    planning = {
        "system": {
            "reservoir_array": [{"name": "r1", "emin": 10.0}, {"name": "r2"}],
            "reservoir_seepage_array": [
                {
                    "uid": 1,
                    "name": "ok",
                    "reservoir": "r1",
                    "segments": [{"slope": 0.0, "constant": 0.0}],
                },
                {
                    "uid": 2,
                    "reservoir": "r1",
                    "segments": [{"slope": 0.5, "constant": 1.0}],
                },
                {"uid": 3, "name": "noseg", "reservoir": "r2", "segments": []},
                {
                    "uid": 4,
                    "name": "r2s",
                    "reservoir": "r2",
                    "segments": [{"constant": 2.0}],
                },
            ],
        }
    }
    findings = check_seepage_at_vmin(planning)
    assert len(findings) == 2
    assert "index 1" in findings[0].message
    assert "q=6.0000" in findings[0].message
    assert "'r2s'" in findings[1].message
