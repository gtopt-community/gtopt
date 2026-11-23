# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for the CLI (``main``) and loss-arbitrage helpers."""

from __future__ import annotations

import io
import json
import runpy
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gtopt_check_output import _loss_arbitrage as la
from gtopt_check_output import main as cli
from gtopt_check_output._checks import Finding, OutputReport


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _case(tmp_path: Path, results: str = "results") -> Path:
    case = tmp_path / "case"
    (case / results).mkdir(parents=True)
    (case / "case.json").write_text(json.dumps({"system": {}}))
    return case


def _fake_report(
    monkeypatch: pytest.MonkeyPatch, findings: list[Finding], la_report=None
) -> None:
    report = OutputReport(findings=list(findings))
    if la_report is not None:
        report.indicators["loss_arbitrage"] = la_report
    monkeypatch.setattr(cli, "run_all_checks", lambda *_a, **_k: report)


# ---------------------------------------------------------------------------
# _print_finding / _resolve_paths / _init_config
# ---------------------------------------------------------------------------


def test_print_finding(capsys: pytest.CaptureFixture[str]) -> None:
    cli._print_finding(Finding("c", "INFO", "hidden"), use_color=True, quiet=True)  # noqa: SLF001
    assert capsys.readouterr().out == ""
    cli._print_finding(Finding("c", "WARNING", "w"), use_color=True, quiet=False)  # noqa: SLF001
    assert "\033[93m[WARNING ]\033[0m w" in capsys.readouterr().out
    cli._print_finding(Finding("c", "ODD", "o"), use_color=True, quiet=False)  # noqa: SLF001
    assert "[ODD     ]" in capsys.readouterr().out


def test_resolve_paths_variants(tmp_path: Path) -> None:
    parser = cli.make_parser()
    case = _case(tmp_path, results="output")
    rdir, jpath = cli._resolve_paths(parser.parse_args([str(case)]))  # noqa: SLF001
    assert rdir == case / "output"
    assert jpath == case / "case.json"

    (case / "case.json").rename(case / "other.json")
    _, jpath = cli._resolve_paths(parser.parse_args([str(case)]))  # noqa: SLF001
    assert jpath == case / "other.json"

    args = parser.parse_args(
        ["-r", str(tmp_path / "r"), "-j", str(tmp_path / "p.json")]
    )
    rdir, jpath = cli._resolve_paths(args)  # noqa: SLF001
    assert rdir == tmp_path / "r"
    assert jpath == tmp_path / "p.json"


def test_resolve_paths_no_json_anywhere(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    _, jpath = cli._resolve_paths(cli.make_parser().parse_args([str(empty)]))  # noqa: SLF001
    assert jpath == empty / "empty.json"


def test_init_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    conf = tmp_path / "g.conf"
    cli.main(["--init-config", "--config", str(conf)])
    assert "[gtopt_check_output]" in conf.read_text()
    assert "Initialized" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_bad_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    case = _case(tmp_path)
    (case / "case.json").write_text("{not json")
    with pytest.raises(SystemExit) as exc:
        cli.main([str(case)])
    assert exc.value.code == 2
    assert "cannot load" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("severities", "code", "color"),
    [
        (["CRITICAL"], 1, "\033[91m"),
        (["WARNING", "INFO"], 0, "\033[93m"),
        (["INFO"], 0, "\033[92m"),
    ],
)
def test_main_colored_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    severities: list[str],
    code: int,
    color: str,
) -> None:
    case = _case(tmp_path)
    _fake_report(monkeypatch, [Finding(f"chk{s}", s, s.lower()) for s in severities])
    out = _Tty()
    monkeypatch.setattr(sys, "stdout", out)
    with pytest.raises(SystemExit) as exc:
        cli.main([str(case)])
    assert exc.value.code == code
    assert f"{color}  Summary:" in out.getvalue()


def test_main_quiet_skips_info_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _case(tmp_path)
    _fake_report(
        monkeypatch,
        [
            Finding("a", "INFO", "only-info"),
            Finding("b", "WARNING", "warned"),
            Finding("b", "INFO", "b-info"),
        ],
    )
    with pytest.raises(SystemExit):
        cli.main([str(case), "--quiet", "--no-color"])
    out = capsys.readouterr().out
    assert "only-info" not in out
    assert "b-info" not in out
    assert "[WARNING ] warned" in out
    assert "---" not in out
    assert "Summary" not in out


def test_emit_overrides_stdout_without_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _case(tmp_path)
    _fake_report(monkeypatch, [])
    with pytest.raises(SystemExit):
        cli.main([str(case), "--quiet", "--emit-overrides"])
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"line_array": []}
    assert "note:" not in captured.err


def test_emit_overrides_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _case(tmp_path)
    report = la.LossArbitrageReport(
        diagnoses=[
            la.LineDiagnosis(1, "L1", la.SYMPTOM_PHANTOM, 2.0, -10.0, 1),
        ]
    )
    _fake_report(monkeypatch, [], la_report=report)
    out_file = tmp_path / "eps.json"
    with pytest.raises(SystemExit):
        cli.main([str(case), "--quiet", "--emit-overrides", str(out_file)])
    data = json.loads(out_file.read_text())
    assert data["line_array"]
    err = capsys.readouterr().err
    assert "wrote 1 loss_cost_eps override(s)" in err
    assert "note:" in err


def test_dunder_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conf = tmp_path / "g.conf"
    monkeypatch.setattr(sys, "argv", ["x", "--init-config", "--config", str(conf)])
    monkeypatch.delitem(sys.modules, "gtopt_check_output.__main__", raising=False)
    runpy.run_module("gtopt_check_output", run_name="__main__")
    assert conf.exists()


# ---------------------------------------------------------------------------
# _loss_arbitrage helpers
# ---------------------------------------------------------------------------


def test_load_long_wide_without_uid_columns(tmp_path: Path) -> None:
    path = tmp_path / "Line" / "flow_sol.parquet" / "scene=0/phase=0/part.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.table({"block": [1], "x": [1.0]}), path)
    assert la._load_long(tmp_path, "Line/flow_sol") is None  # noqa: SLF001


def test_load_long_fills_missing_index_columns(tmp_path: Path) -> None:
    pq.write_table(pa.table({"uid:3": [2.5]}), tmp_path / "s.parquet")
    df = la._load_long(tmp_path, "s")  # noqa: SLF001
    assert df is not None
    assert df.to_dict("records") == [
        {"scenario": 1, "stage": 1, "block": 1, "uid": 3, "value": 2.5}
    ]


def test_bus_uid_map_and_resolution() -> None:
    planning = {"system": {"bus_array": [{"uid": 1, "name": "b1"}, {"uid": 2}]}}
    mapping = la._bus_uid_map(planning)  # noqa: SLF001
    assert mapping == {"1": 1, "b1": 1, "2": 2}
    assert la._resolve_bus_uid(True, mapping) is None  # noqa: SLF001
    assert la._resolve_bus_uid(2, mapping) == 2  # noqa: SLF001
    assert la._resolve_bus_uid("b1", mapping) == 1  # noqa: SLF001
    assert la._resolve_bus_uid("nope", mapping) is None  # noqa: SLF001
