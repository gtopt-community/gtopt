# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage for the AI-analysis helpers (diagram / pandapower / AI query).

External tools (``gtopt_diagram``, ``gtopt2pp``), ``pandapower`` and the
AI provider are all replaced by fakes.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gtopt_check_json import _diagram_helper as dh
from gtopt_check_json import _general_checks as gc
from gtopt_check_json import _pp_helper as pph

_PLANNING: dict[str, Any] = {
    "simulation": {"scenario_array": [{"uid": 3}], "block_array": [{"uid": 7}]},
    "system": {
        "name": "s",
        "bus_array": [
            {"uid": 1, "name": "b1", "voltage": 220.0},
            {"uid": 2, "name": "b2", "voltage": 66.0},
            {"uid": 3, "name": "b3", "voltage": 220.0},
            {"uid": 4, "name": ""},
        ],
        "generator_array": [{"uid": 1, "name": "g1", "bus": "b1", "pmax": 5}],
        "demand_array": [{"uid": 1, "bus": "b2"}],
        "line_array": [
            {"uid": 1, "name": "tx", "bus_a": "b2", "bus_b": "b1", "tmax_ab": 100},
            {"uid": 2, "bus_a": 1, "bus_b": 3, "tmax_ab": "f.parquet"},
            {"uid": 3, "name": "big", "bus_a": "b1", "bus_b": "b2", "tmax_ab": 1e6},
            {"uid": 4, "bus_a": "b1", "bus_b": "nowhere"},
        ],
    },
}


# ---------------------------------------------------------------------------
# _diagram_helper
# ---------------------------------------------------------------------------


def test_mermaid_from_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_k: Any) -> Any:
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="graph LR\n")

    monkeypatch.setattr(dh.subprocess, "run", _run)
    assert dh.get_mermaid_summary(_PLANNING) == "graph LR"
    assert calls[0][0] == "gtopt_diagram"
    assert not Path(calls[0][1]).exists()  # temp file removed


@pytest.mark.parametrize(
    "outcome",
    [
        SimpleNamespace(returncode=1, stdout="x"),
        SimpleNamespace(returncode=0, stdout="  "),
        FileNotFoundError(),
        subprocess.TimeoutExpired("gtopt_diagram", 30),
    ],
)
def test_mermaid_fallback(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> None:
    def _run(*_a: Any, **_k: Any) -> Any:
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(dh.subprocess, "run", _run)
    text = dh.get_mermaid_summary(_PLANNING)
    assert "Bus: b1" in text
    assert "Generator: g1 at bus b1, pmax=5, gcost=?" in text
    assert "Demand: 1 at bus b2" in text
    assert "Line: 2 from 1 to 3" in text


# ---------------------------------------------------------------------------
# _pp_helper
# ---------------------------------------------------------------------------


class _FakeBusTable:
    def __init__(self) -> None:
        self.kv: dict[int, float] = {}

    @property
    def at(self) -> Any:
        table = self

        class _At:
            def __getitem__(self, key: tuple[int, str]) -> float:
                return table.kv[key[0]]

        return _At()


class _FakePandapowerModule(types.ModuleType):
    """Fake ``pandapower`` module recording the created trafos / lines.

    ``created`` is declared on the class so linters (pylint ``no-member``)
    see it; a bare ``types.ModuleType`` with a dynamic attribute was
    flagged depending on the pylint/astroid inference path.
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.created: dict[str, list[dict[str, Any]]] = {"trafo": [], "line": []}


def _fake_pandapower(
    diag_result: Any, raise_on_from_json: bool = False
) -> _FakePandapowerModule:
    mod = _FakePandapowerModule("pandapower")
    created = mod.created

    def create_empty_network(name: str) -> Any:
        return SimpleNamespace(name=name, bus=_FakeBusTable())

    def create_bus(net: Any, vn_kv: float, name: str) -> int:
        idx = len(net.bus.kv)
        net.bus.kv[idx] = vn_kv
        return idx

    def create_transformer_from_parameters(_net: Any, **kw: Any) -> None:
        created["trafo"].append(kw)

    def create_line_from_parameters(_net: Any, **kw: Any) -> None:
        created["line"].append(kw)

    def diagnostic(_net: Any, report_style: Any = None) -> Any:
        if isinstance(diag_result, BaseException):
            raise diag_result
        return diag_result

    def from_json(_path: str) -> Any:
        if raise_on_from_json:
            raise ValueError("bad json")
        return SimpleNamespace()

    for attr, fn in {
        "create_empty_network": create_empty_network,
        "create_bus": create_bus,
        "create_transformer_from_parameters": create_transformer_from_parameters,
        "create_line_from_parameters": create_line_from_parameters,
        "diagnostic": diagnostic,
        "from_json": from_json,
    }.items():
        setattr(mod, attr, fn)
    return mod


def test_pp_via_gtopt2pp(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_pandapower({"overload": ["l1"]})
    monkeypatch.setitem(sys.modules, "pandapower", fake)
    cmds: list[list[str]] = []

    def _run(cmd: list[str], **_k: Any) -> Any:
        cmds.append(cmd)
        Path(cmd[-1]).write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pph.subprocess, "run", _run)
    assert pph.get_pandapower_diagnostics(_PLANNING) == "  overload: ['l1']"
    assert cmds[0][3:6] == ["3", "--block", "7"]
    assert not Path(cmds[0][-1]).exists()


def test_pp_diagnostics_file_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "pandapower", _fake_pandapower({}))
    assert pph._run_pp_diagnostics(str(tmp_path / "x")) == (  # noqa: SLF001
        "pandapower diagnostic: no issues found"
    )
    monkeypatch.setitem(
        sys.modules, "pandapower", _fake_pandapower({}, raise_on_from_json=True)
    )
    assert "failed: bad json" in pph._run_pp_diagnostics("x")  # noqa: SLF001


def test_pp_direct_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_pandapower({"isolated": [3]})
    monkeypatch.setitem(sys.modules, "pandapower", fake)
    monkeypatch.setattr(
        pph.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=1)
    )
    assert pph.get_pandapower_diagnostics(_PLANNING) == "  isolated: [3]"
    # b2(66kV) <-> b1(220kV) are promoted to transformers; b1 <-> b3 is a line
    assert len(fake.created["trafo"]) == 2
    assert fake.created["trafo"][0]["hv_bus"] == 0
    assert fake.created["trafo"][1]["sn_mva"] == 100.0  # "unlimited" tmax
    assert len(fake.created["line"]) == 1
    assert fake.created["line"][0]["max_i_ka"] == pytest.approx(250.0 / 220.0)


def test_pp_direct_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(*_a: Any, **_k: Any) -> Any:
        raise FileNotFoundError

    monkeypatch.setattr(pph.subprocess, "run", _missing)
    monkeypatch.setitem(sys.modules, "pandapower", _fake_pandapower({}))
    assert (
        pph.get_pandapower_diagnostics({}) == "pandapower diagnostic: no issues found"
    )
    monkeypatch.setitem(
        sys.modules, "pandapower", _fake_pandapower(RuntimeError("diag boom"))
    )
    assert "failed: diag boom" in pph.get_pandapower_diagnostics({})
    monkeypatch.setitem(sys.modules, "pandapower", None)
    assert pph.get_pandapower_diagnostics({}) == ""


# ---------------------------------------------------------------------------
# check_ai_system_analysis
# ---------------------------------------------------------------------------


def test_ai_analysis_disabled() -> None:
    assert not gc.check_ai_system_analysis({}, None)


@pytest.mark.parametrize(
    ("ok", "response", "severity"),
    [(True, "looks fine", "WARNING"), (False, "no key", "NOTE"), (True, "", None)],
)
def test_ai_analysis(
    monkeypatch: pytest.MonkeyPatch, ok: bool, response: str, severity: str | None
) -> None:
    monkeypatch.setattr(dh, "get_mermaid_summary", lambda _p: "graph")
    monkeypatch.setattr(pph, "get_pandapower_diagnostics", lambda _p: "pp ok")
    seen: dict[str, Any] = {}

    def _query(prompt: str, **kw: Any) -> tuple[bool, str]:
        seen["prompt"] = prompt
        seen.update(kw)
        return ok, response

    monkeypatch.setattr("gtopt_check_lp._ai.query_ai", _query)
    opts = SimpleNamespace(provider="openai", model="", key="", timeout=5)
    findings = gc.check_ai_system_analysis({}, opts)
    assert "graph" in seen["prompt"]
    assert "pp ok" in seen["prompt"]
    assert seen["provider"] == "openai"
    assert seen["model"] is None
    if severity is None:
        assert not findings
    else:
        assert findings[0].severity.name == severity


def test_ai_analysis_without_helpers_or_ai_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "gtopt_check_json._diagram_helper", None)
    monkeypatch.setitem(sys.modules, "gtopt_check_json._pp_helper", None)
    monkeypatch.setitem(sys.modules, "gtopt_check_lp._ai", None)
    findings = gc.check_ai_system_analysis({}, SimpleNamespace())
    assert len(findings) == 1
    assert "require gtopt_check_lp" in findings[0].message


def test_ai_analysis_empty_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dh, "get_mermaid_summary", lambda _p: "")
    monkeypatch.setattr(pph, "get_pandapower_diagnostics", lambda _p: "")
    assert not gc.check_ai_system_analysis({}, SimpleNamespace())
