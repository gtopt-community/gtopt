# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""E2E smoke cases for the public checker / runner / results scripts.

Offline cases run on committed fixtures:

* ``cases/ieee_4b_ori/ieee_4b_ori.json`` -- the 4-bus DC OPF case;
* ``scripts/tests/data/ieee_4b_ori_output/`` -- its gtopt output in the
  current layout (per-(scene, phase) CSV shards, ``solution.csv`` with the
  scale/version columns, ``solver_status.json``), produced by gtopt with
  HiGHS;
* ``scripts/tests/data/ieee_4b_ori.lp`` -- its LP (``gtopt --lp-only -l``).

Regenerate the two data fixtures after an output-format change with::

    gtopt cases/ieee_4b_ori/ieee_4b_ori.json -d <dir> --solver highs
    gtopt cases/ieee_4b_ori/ieee_4b_ori.json --lp-only -l ieee_4b_ori \\
        --solver highs

``needs_gtopt`` cases solve a fresh copy of the case first, so the tools are
also exercised on whatever the current gtopt binary writes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from _cli_e2e import (
    CASES_DIR,
    SCRIPT_CASES_DIR,
    SCRIPTS_DIR,
    CliRun,
    assert_planning_json,
    assert_solved,
    e2e,
    load_json,
)

_DATA = Path(__file__).resolve().parent / "data"
_CASE4 = CASES_DIR / "ieee_4b_ori" / "ieee_4b_ori.json"
_OUT4 = _DATA / "ieee_4b_ori_output"
_LP4 = _DATA / "ieee_4b_ori.lp"
_LINES4 = {"l1_2", "l1_3", "l2_3", "l2_4", "l3_4"}


def _isolate_home(ctx: CliRun) -> None:
    """Point HOME / XDG dirs at tmp so no tool reads or writes ~/.gtopt.conf."""
    home = ctx.tmp_path / "home"
    home.mkdir(exist_ok=True)
    ctx.env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_CACHE_HOME": str(home / ".cache"),
        }
    )


def _solve_case4(ctx: CliRun) -> tuple[Path, Path]:
    """Copy ieee_4b_ori into tmp, solve it, return ``(case_json, output)``."""
    case_dir = ctx.tmp_path / "case"
    case_dir.mkdir()
    case_json = Path(shutil.copy(_CASE4, case_dir))
    out = ctx.run_gtopt(case_json)
    assert_solved(out)
    return case_json, out


# ---------------------------------------------------------------------------
# Pre-flight checkers
# ---------------------------------------------------------------------------


@e2e("gtopt_check_json")
def case_validate_ieee_4b(ctx: CliRun) -> None:
    """Validate a case JSON and print its statistics."""
    _isolate_home(ctx)
    res = ctx.run([_CASE4, "--no-ai", "--no-color"])
    assert "Summary:" in res.output
    assert "0 critical" in res.output, res.output[-2000:]
    info = ctx.run(["--info", _CASE4, "--no-color"])
    assert "Peak demand" in info.output
    assert "250.00 MW" in info.output


@e2e("gtopt_check_lp")
def case_static_analysis_bad_bounds(ctx: CliRun) -> None:
    """Static analysis of an infeasible LP finds the lb > ub variable."""
    _isolate_home(ctx)
    src = SCRIPT_CASES_DIR / "lp_infeasible" / "bad_bounds.lp"
    lp = Path(shutil.copy(src, ctx.tmp_path))
    res = ctx.run(
        [
            lp,
            "--analyze-only",
            "--no-neos",
            "--no-setup",
            "--no-ai",
            "--no-color",
        ]
    )
    assert "Conflicting variable bounds" in res.output, res.output[-2000:]
    assert "x1" in res.output


@e2e("gtopt_check_pampl")
def case_check_ieee_24b_rts_pampl(ctx: CliRun) -> None:
    """A valid shipped .pampl file (with ``var slack_*``) has no warnings."""
    _isolate_home(ctx)
    res = ctx.run(
        [CASES_DIR / "ieee_24b_rts" / "uc_opf_limits.pampl", "--no-ai", "--no-color"]
    )
    assert "Summary:" in res.output
    assert "WARNING" not in res.output, res.output[-2000:]
    assert "CRITICAL" not in res.output


@e2e("gtopt_check_pampl")
def case_flags_bad_operators(ctx: CliRun) -> None:
    """An erroneous .pampl file is reported (exit code stays informative)."""
    _isolate_home(ctx)
    bad = SCRIPTS_DIR / "gtopt_check_pampl" / "examples" / "errors"
    res = ctx.run(
        [bad / "bad_operators.pampl", "--no-ai", "--no-color"], expect_rc=(0, 1)
    )
    assert "==" in res.output


@e2e("gtopt_check_fingerprint")
def case_compute_verify_compare(ctx: CliRun) -> None:
    """compute -> verify (PASS) -> compare against itself (match)."""
    golden = ctx.tmp_path / "golden.json"
    ctx.run(["compute", _LP4, "-o", golden])
    data = load_json(golden)
    assert data.get("structural"), data
    res = ctx.run(["verify", "--lp-file", _LP4, "--golden", golden])
    assert "PASS" in res.output
    ctx.run(["compare", "--actual", golden, "--expected", golden])


@e2e("gtopt_check_fingerprint", needs_gtopt=True)
def case_fingerprint_fresh_lp(ctx: CliRun) -> None:
    """Fingerprint of a freshly assembled LP matches the committed fixture."""
    case_dir = ctx.tmp_path / "case"
    case_dir.mkdir()
    case_json = Path(shutil.copy(_CASE4, case_dir))
    ctx.run_gtopt(case_json, "--lp-only", "-l", "model")
    lp = case_dir / "model.lp"
    assert lp.is_file(), sorted(p.name for p in case_dir.iterdir())
    golden = ctx.tmp_path / "golden.json"
    ctx.run(["compute", _LP4, "-o", golden])
    res = ctx.run(["verify", "--lp-file", lp, "--golden", golden])
    assert "PASS" in res.output


@e2e("gtopt_check_topology")
def case_topology_ieee_4b(ctx: CliRun) -> None:
    """Topology report (inline-valued case) incl. machine-readable JSON."""
    report = ctx.tmp_path / "topo.json"
    res = ctx.run([_CASE4, "--json-out", report, "--no-color"])
    assert "Summary:" in res.output
    data = load_json(report)
    assert data, "empty topology report"


@e2e("gtopt_compress_lp")
def case_gzip_lp(ctx: CliRun) -> None:
    """Compress an LP debug file with gzip; the original is replaced."""
    _isolate_home(ctx)
    lp = Path(shutil.copy(_LP4, ctx.tmp_path / "debug.lp"))
    ctx.run(["--quiet", "--codec", "gzip", "--config", ctx.tmp_path / "c.conf", lp])
    gz = ctx.tmp_path / "debug.lp.gz"
    assert gz.is_file() and gz.stat().st_size > 0
    assert not lp.exists()


@e2e("gtopt_diagram")
def case_mermaid_and_dot(ctx: CliRun) -> None:
    """Topology diagram as Mermaid and Graphviz DOT source."""
    mmd = ctx.tmp_path / "d.md"
    ctx.run([_CASE4, "--format", "mermaid", "-o", mmd])
    text = mmd.read_text(encoding="utf-8")
    assert "graph" in text or "flowchart" in text, text[:300]
    dot = ctx.tmp_path / "d.dot"
    ctx.run([_CASE4, "--format", "dot", "-o", dot])
    assert "digraph" in dot.read_text(encoding="utf-8") or "graph" in dot.read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Running / monitoring
# ---------------------------------------------------------------------------


@e2e("run_gtopt")
def case_list_checks_and_dry_run(ctx: CliRun) -> None:
    """--list-checks, and --dry-run with a stand-in binary (no solve)."""
    res = ctx.run(["--list-checks"])
    assert "check_output" in res.output
    fake = ctx.tmp_path / "fake_gtopt"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    ctx.env["GTOPT_BIN"] = str(fake)
    case_json = Path(shutil.copy(_CASE4, ctx.tmp_path))
    res = ctx.run(["--dry-run", case_json, "--no-color"])
    assert "[dry-run]" in res.output and "ieee_4b_ori.json" in res.output


@e2e("run_gtopt", needs_gtopt=True)
def case_solve_ieee_4b(ctx: CliRun) -> None:
    """run_gtopt solves a case end to end (pre/post-flight checks on)."""
    case_json = Path(shutil.copy(_CASE4, ctx.tmp_path))
    ctx.run([case_json, "--no-color"])
    assert_solved(ctx.tmp_path / "output")


@e2e("gtopt_check_solvers")
def case_missing_binary_is_reported(ctx: CliRun) -> None:
    """A bad --gtopt-bin path fails with a clear message, not a traceback."""
    res = ctx.run(
        ["--gtopt-bin", ctx.tmp_path / "no-such-gtopt", "--list"], expect_rc=(1, 2)
    )
    assert "not found" in res.output
    assert "Traceback" not in res.output


@e2e("gtopt_check_solvers", needs_gtopt=True)
def case_list_and_check_one_solver(ctx: CliRun) -> None:
    """--list the plugins, then run the LP test suite on one of them."""
    assert ctx.gtopt_bin
    res = ctx.run(["--gtopt-bin", ctx.gtopt_bin, "--list", "--no-color"])
    names = [
        line.split("•", 1)[1].strip() for line in res.output.splitlines() if "•" in line
    ]
    assert names, res.output
    # Prefer an open-source simplex/IPM backend other than CLP.
    pick = next((n for n in ("highs", "cbc") if n in names), None)
    if pick is None:
        return
    res = ctx.run(
        ["--gtopt-bin", ctx.gtopt_bin, "--solver", pick, "--no-color"], timeout=600
    )
    assert "0 failed" in res.output


@e2e("gtopt_monitor")
def case_text_mode_finished_run(ctx: CliRun) -> None:
    """--no-gui prints the status of a finished run and exits."""
    res = ctx.run(
        ["--no-gui", "--poll", "0.1", "--status-file", _OUT4 / "solver_status.json"],
        timeout=60,
    )
    assert "finished" in res.output.lower() or "converged" in res.output.lower()


@e2e("gtopt_monitor")
def case_get_option(ctx: CliRun) -> None:
    """--get reads a planning option from the case directory."""
    case_dir = ctx.tmp_path / "case"
    case_dir.mkdir()
    shutil.copy(_CASE4, case_dir)
    res = ctx.run(["--get", "model_options.demand_fail_cost", "--case-dir", case_dir])
    assert res.stdout.strip() == "1000", res.output


# ---------------------------------------------------------------------------
# Post-processing of solver output
# ---------------------------------------------------------------------------


def _check_output(ctx: CliRun, case_json: Path, out: Path) -> None:
    res = ctx.run(["-r", out, "-j", case_json, "--no-color"])
    assert "0 critical" in res.output, res.output[-3000:]
    assert "generation/demand ratio: 1.0000" in res.output
    assert "l1_3" in res.output  # congestion ranking lists the lines


@e2e("gtopt_check_output")
def case_analyze_fixture_output(ctx: CliRun) -> None:
    """Analyze the committed ieee_4b_ori output."""
    _check_output(ctx, _CASE4, _OUT4)


@e2e("gtopt_check_output", needs_gtopt=True)
def case_analyze_fresh_output(ctx: CliRun) -> None:
    """Analyze the output of a fresh solve."""
    _check_output(ctx, *_solve_case4(ctx))


def _results_summary(ctx: CliRun, out: Path) -> None:
    res = ctx.run([out, "--pretty"])
    kpi = json.loads(res.stdout)
    assert kpi["status"] == "0"
    assert kpi["obj_value"] == 5000.0
    assert kpi["total_generation"] == 250.0, kpi
    assert kpi["total_load"] == 250.0
    assert kpi["n_buses"] == 4 and kpi["n_lines"] == 5
    assert kpi["lmp_mean"] == 20.0


@e2e("gtopt_results_summary")
def case_summary_fixture_output(ctx: CliRun) -> None:
    """KPI JSON of the committed output (current CSV-shard layout)."""
    _results_summary(ctx, _OUT4)


@e2e("gtopt_results_summary", needs_gtopt=True)
def case_summary_fresh_parquet_output(ctx: CliRun) -> None:
    """KPI JSON of a fresh solve written as hive-partitioned Parquet."""
    case_dir = ctx.tmp_path / "case"
    case_dir.mkdir()
    case_json = Path(shutil.copy(_CASE4, case_dir))
    out = ctx.run_gtopt(case_json, "--set", "output_format=parquet")
    assert_solved(out)
    _results_summary(ctx, out)


def _timeseries_export(ctx: CliRun, out: Path) -> None:
    xlsx = ctx.tmp_path / "results.xlsx"
    ctx.run([out, "-o", xlsx])
    sheets = pd.read_excel(xlsx, sheet_name=None)
    assert "Overview" in sheets
    assert "Generator_generation_sol" in sheets, sorted(sheets)
    assert "Line_flow_sol" in sheets
    overview = dict(zip(sheets["Overview"]["Metric"], sheets["Overview"]["Value"]))
    assert overview["Total generation (MWh)"] == 250.0


@e2e("gtopt_timeseries_export")
def case_xlsx_fixture_output(ctx: CliRun) -> None:
    """Excel workbook from the committed output."""
    _timeseries_export(ctx, _OUT4)


@e2e("gtopt_timeseries_export", needs_gtopt=True)
def case_xlsx_fresh_output(ctx: CliRun) -> None:
    """Excel workbook from a fresh solve."""
    _timeseries_export(ctx, _solve_case4(ctx)[1])


@e2e("gtopt_compare")
def case_pandapower_ieee_4b_fixture(ctx: CliRun) -> None:
    """Committed gtopt output agrees with a pandapower DC OPF."""
    res = ctx.run(["--case", "ieee_4b_ori", "--gtopt-output", _OUT4])
    assert "RESULT: PASS" in res.output, res.output[-2000:]


@e2e("gtopt_compare", needs_gtopt=True)
def case_pandapower_s1b_fresh(ctx: CliRun) -> None:
    """A fresh s1b solve agrees with pandapower."""
    case_dir = ctx.tmp_path / "case"
    case_dir.mkdir()
    case_json = Path(shutil.copy(CASES_DIR / "s1b" / "s1b.json", case_dir))
    out = ctx.run_gtopt(case_json)
    assert_solved(out)
    res = ctx.run(["--case", "s1b", "--gtopt-output", out])
    assert "RESULT: PASS" in res.output, res.output[-2000:]


@e2e("plexos2gtopt-line-lifts")
def case_rank_lines_fixture(ctx: CliRun) -> None:
    """Rank lines of a solved case; JSON table + --suggest line."""
    table = ctx.tmp_path / "lifts.json"
    res = ctx.run([_CASE4, _OUT4, "--suggest", "--json", table])
    assert "--lift-line-caps" in res.output
    rows = load_json_list(table)
    assert {r["line"] for r in rows} == _LINES4
    assert all(r["max_util"] is not None for r in rows), rows


@e2e("plexos2gtopt-line-lifts", needs_gtopt=True)
def case_rank_lines_fresh(ctx: CliRun) -> None:
    """Rank lines of a fresh solve (reads the current Line/flow_* stems)."""
    case_json, out = _solve_case4(ctx)
    table = ctx.tmp_path / "lifts.json"
    ctx.run([case_json, out, "--json", table])
    rows = load_json_list(table)
    assert {r["line"] for r in rows} == _LINES4
    assert all(r["max_util"] is not None for r in rows), rows


def load_json_list(path: Path) -> list[dict]:
    """Load a JSON array file."""
    assert path.is_file(), f"{path} missing"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    return data


@e2e("gtopt2pbi")
def case_wide_to_long(ctx: CliRun) -> None:
    """Wide uid:N field tables become long [<index>, uid, value] + zstd."""
    tree = ctx.tmp_path / "case"
    (tree / "Generator").mkdir(parents=True)
    wide = pd.DataFrame(
        {
            "scenario": [1, 1],
            "stage": [1, 1],
            "block": [1, 2],
            "uid:1": [10.0, 11.0],
            "uid:2": [20.0, 21.0],
        }
    )
    wide.to_parquet(tree / "Generator" / "pmax.parquet", compression="gzip")
    wide.to_csv(tree / "Generator" / "pmin.csv", index=False)
    res = ctx.run([tree])
    assert "converted 2" in res.output, res.output
    for df in (
        pd.read_parquet(tree / "Generator" / "pmax.parquet"),
        pd.read_csv(tree / "Generator" / "pmin.csv"),
    ):
        assert list(df.columns) == ["scenario", "stage", "block", "uid", "value"]
        assert len(df) == 4
    meta = pq.ParquetFile(tree / "Generator" / "pmax.parquet").metadata
    assert meta.row_group(0).column(0).compression == "ZSTD"


@e2e("gtopt-net")
def case_reduce_ieee_14b(ctx: CliRun) -> None:
    """Reduce the 14-bus case to 4 buses; reduced JSON + bus map."""
    reduced = ctx.tmp_path / "r.json"
    ctx.run(
        ["reduce", CASES_DIR / "ieee_14b" / "ieee_14b.json", "-K", "4", "-o", reduced]
    )
    data = assert_planning_json(reduced)
    assert len(data["system"]["bus_array"]) == 4
    assert (ctx.tmp_path / "r.busmap.csv").is_file()


@e2e("gtopt-net", needs_gtopt=True)
def case_reduce_and_solve(ctx: CliRun) -> None:
    """The reduced 4-bus case solves to optimality."""
    reduced = ctx.tmp_path / "r.json"
    ctx.run(
        ["reduce", CASES_DIR / "ieee_14b" / "ieee_14b.json", "-K", "4", "-o", reduced]
    )
    assert_solved(ctx.run_gtopt(reduced))
