# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage for :mod:`plexos2gtopt.auto_lift_lines` (pandapower/gtopt mocked)."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from plexos2gtopt import auto_lift_lines as al
from plexos2gtopt.auto_lift_lines import (
    OverloadedLine,
    detect_overloaded_lines,
    detect_overloaded_lines_via_gtopt,
    patch_bundle_with_lifts,
)

# ---------------------------------------------------------------------------
# pandapower engine
# ---------------------------------------------------------------------------


def _net() -> SimpleNamespace:
    return SimpleNamespace(
        line=pd.DataFrame(
            {
                "max_i_ka": [1.0, 1.0, 0.0],
                "from_bus": [0, 1, 0],
                "name": ["L_hot", "L_cool", "L_norate"],
            }
        ),
        bus=pd.DataFrame({"vn_kv": [100.0, 0.0]}),
        res_line=pd.DataFrame(),
    )


def _install_pp(
    monkeypatch: pytest.MonkeyPatch, net: SimpleNamespace, rundcopp: Any
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    pp_mod = ModuleType("pandapower")
    pp_mod.rundcopp = rundcopp  # type: ignore[attr-defined]
    conv_mod = ModuleType("gtopt2pp.convert")

    def _convert(case: Any, scenario: Any = None, block: Any = None) -> Any:
        calls.append({"case": case, "scenario": scenario, "block": block})
        return net

    conv_mod.convert = _convert  # type: ignore[attr-defined]
    conv_mod.load_gtopt_case = lambda p: {"path": p}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pandapower", pp_mod)
    monkeypatch.setitem(sys.modules, "gtopt2pp.convert", conv_mod)
    return calls


def test_pandapower_detects_overload(monkeypatch: pytest.MonkeyPatch) -> None:
    net = _net()
    rated_mw = 100.0 * math.sqrt(3.0)

    def _run(n: Any) -> None:
        # Caps were lifted before the OPF.
        assert n.line["max_i_ka"].iloc[0] == pytest.approx(1000.0)
        n.res_line = pd.DataFrame({"p_from_mw": [-2.0 * rated_mw, 5.0, 1.0]})

    calls = _install_pp(monkeypatch, net, _run)
    out = detect_overloaded_lines(Path("x.json"), scenario_uid=1, block_uid=2)
    assert calls[0]["scenario"] == 1 and calls[0]["block"] == 2
    assert [o.name for o in out] == ["L_hot"]
    assert out[0].ratio == pytest.approx(2.0)


def test_pandapower_opf_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_n: Any) -> None:
        raise RuntimeError("diverged")

    _install_pp(monkeypatch, _net(), _boom)
    assert not detect_overloaded_lines(Path("x.json"))


def test_pandapower_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_pp(monkeypatch, _net(), lambda _n: None)
    assert not detect_overloaded_lines(Path("x.json"))


def test_pandapower_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pandapower", None)
    assert not detect_overloaded_lines(Path("x.json"))


def test_gtopt2pp_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pandapower", ModuleType("pandapower"))
    monkeypatch.setitem(sys.modules, "gtopt2pp.convert", None)
    assert not detect_overloaded_lines(Path("x.json"))


# ---------------------------------------------------------------------------
# gtopt engine
# ---------------------------------------------------------------------------


def _bundle(tmp_path: Path, **sim: Any) -> Path:
    data = {
        "simulation": sim or {"block_array": [{"uid": 1}, {"uid": 2}]},
        "system": {
            "line_array": [
                {"uid": 1, "name": "A", "tmax_ab": 100.0, "enforce_level": 2},
                {"uid": 2, "name": "B", "tmax_ab": [[50.0, 80.0]]},
                {"uid": 3, "name": "C", "tmax_ab": [10.0, 40.0]},
                {"uid": 4, "name": "D"},
                {"uid": 5, "name": "E", "tmax_ab": "weird"},
                {"name": "F", "tmax_ab": 5.0},
            ]
        },
    }
    p = tmp_path / "case.json"
    p.write_text(json.dumps(data))
    return p


def _fake_binary(tmp_path: Path) -> Path:
    exe = tmp_path / "gtopt"
    exe.write_text("#!/bin/sh\n")
    return exe


def _write_flows(out_dir: Path, name: str, rows: list[tuple[int, int, float]]) -> None:
    (out_dir / "Line").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["uid", "block", "value"]).to_parquet(
        out_dir / "Line" / name
    )


def _fake_run(
    monkeypatch: pytest.MonkeyPatch,
    writer: Any = None,
    returncode: int = 0,
    exc: BaseException | None = None,
) -> list[list[str]]:
    cmds: list[list[str]] = []

    def _run(cmd: list[str], **_kw: Any) -> Any:
        cmds.append(cmd)
        if exc is not None:
            raise exc
        out_dir = Path(cmd[cmd.index("--set") + 1].split("=", 1)[1])
        if writer is not None:
            writer(out_dir)
        return subprocess.CompletedProcess(cmd, returncode, "", "boom")

    monkeypatch.setattr(subprocess, "run", _run)
    return cmds


def test_gtopt_engine_signed_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _bundle(tmp_path)

    def _writer(out_dir: Path) -> None:
        _write_flows(
            out_dir,
            "flow_sol.parquet",
            [(1, 1, -150.0), (2, 1, 40.0), (3, 1, 50.0), (1, 2, 999.0)],
        )

    cmds = _fake_run(monkeypatch, _writer)
    out = detect_overloaded_lines_via_gtopt(
        src, gtopt_binary=_fake_binary(tmp_path), time_limit_s=5
    )
    assert "--no-mip" in cmds[0]
    assert [(o.name, round(o.ratio, 3)) for o in out] == [("A", 1.5), ("C", 1.25)]
    lifted = json.loads((tmp_path / "case_autolift.json").read_text())
    levels = {
        ln["name"]: ln.get("enforce_level") for ln in lifted["system"]["line_array"]
    }
    assert levels["A"] == 0 and levels["D"] is None


def test_gtopt_engine_directional_pair_nested_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _bundle(tmp_path)

    def _writer(out_dir: Path) -> None:
        nested = out_dir / "output"
        _write_flows(nested, "flowp_sol.parquet", [(1, 2, 60.0)])
        _write_flows(nested, "flown_sol.parquet", [(1, 2, -50.0)])

    _fake_run(monkeypatch, _writer)
    out = detect_overloaded_lines_via_gtopt(
        src, gtopt_binary=str(_fake_binary(tmp_path)), block_uid=2
    )
    assert [o.name for o in out] == ["A"]
    assert out[0].flow_mw == pytest.approx(110.0)


def test_gtopt_engine_env_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GTOPT_BIN", str(tmp_path / "nope"))
    assert not detect_overloaded_lines_via_gtopt(_bundle(tmp_path))
    monkeypatch.delenv("GTOPT_BIN")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert not detect_overloaded_lines_via_gtopt(_bundle(tmp_path))


def test_gtopt_engine_no_lines(tmp_path: Path) -> None:
    exe = _fake_binary(tmp_path)
    p = tmp_path / "empty.json"
    p.write_text(json.dumps({"system": {}}))
    assert not detect_overloaded_lines_via_gtopt(p, gtopt_binary=exe)
    p.write_text(json.dumps({"system": {"line_array": [{"name": "X"}]}}))
    assert not detect_overloaded_lines_via_gtopt(p, gtopt_binary=exe)


@pytest.mark.parametrize(
    ("exc", "rc"),
    [
        (subprocess.TimeoutExpired(cmd="gtopt", timeout=1), 0),
        (OSError("exec format error"), 0),
        (None, 3),
    ],
)
def test_gtopt_engine_solve_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc: BaseException | None,
    rc: int,
) -> None:
    _fake_run(monkeypatch, returncode=rc, exc=exc)
    assert not detect_overloaded_lines_via_gtopt(
        _bundle(tmp_path), gtopt_binary=_fake_binary(tmp_path)
    )


def test_gtopt_engine_no_pyarrow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(monkeypatch)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)
    assert not detect_overloaded_lines_via_gtopt(
        _bundle(tmp_path), gtopt_binary=_fake_binary(tmp_path)
    )


def test_gtopt_engine_no_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(monkeypatch)
    src = _bundle(tmp_path, block_array=[])
    assert not detect_overloaded_lines_via_gtopt(
        src, gtopt_binary=_fake_binary(tmp_path)
    )


def test_gtopt_engine_no_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(monkeypatch)
    assert not detect_overloaded_lines_via_gtopt(
        _bundle(tmp_path), gtopt_binary=_fake_binary(tmp_path), block_uid=1
    )


# ---------------------------------------------------------------------------
# patch_bundle_with_lifts
# ---------------------------------------------------------------------------


def test_patch_bundle_with_lifts(tmp_path: Path) -> None:
    src = _bundle(tmp_path)
    assert patch_bundle_with_lifts(src, []) == 0
    ol = [
        OverloadedLine(name="A", flow_mw=150.0, rated_mw=100.0, ratio=1.5),
        OverloadedLine(name="B", flow_mw=1.0, rated_mw=1.0, ratio=1.0),
    ]
    assert patch_bundle_with_lifts(src, ol) == 2
    # Idempotent: already EL=0 lines are not counted again.
    before = src.read_text()
    assert patch_bundle_with_lifts(src, ol) == 0
    assert src.read_text() == before


def test_default_threshold() -> None:
    assert al.DEFAULT_THRESHOLD == 1.0
