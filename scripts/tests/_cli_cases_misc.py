# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""E2E smoke cases for public scripts not covered by the themed modules."""

from __future__ import annotations

import json
import shutil

from _cli_e2e import CASES_DIR, CliRun, e2e, load_json


def _mini_collection(ctx: CliRun):
    """A one-case ``gtopt-cases``-style collection around ieee_4b_ori."""
    root = ctx.tmp_path / "collection"
    case_dir = root / "ieee" / "case"
    case_dir.mkdir(parents=True)
    shutil.copy(CASES_DIR / "ieee_4b_ori" / "ieee_4b_ori.json", case_dir)
    catalog = {
        "cases": [
            {
                "id": "ieee/ieee_4b_ori",
                "path": "ieee",
                "application": "daily_scheduling",
                "case_json": "case/ieee_4b_ori.json",
            }
        ]
    }
    (root / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    return root


@e2e("gtopt_check_cases")
def case_missing_catalog(ctx: CliRun) -> None:
    """A directory without catalog.json is a clean usage error (rc 2)."""
    res = ctx.run(["--repo", ctx.tmp_path], expect_rc=2)
    assert "no catalog.json" in res.stderr


@e2e("gtopt_check_cases", needs_gtopt=True)
def case_update_then_check(ctx: CliRun) -> None:
    """``--update`` records the LP size; a second run then matches it."""
    root = _mini_collection(ctx)
    common = ["--repo", root, "--workdir", ctx.tmp_path / "work", "--threads", "1"]
    ctx.run([*common, "--update"])
    size = load_json(root / "catalog.json")["cases"][0]["size"]["lp"]
    assert size["rows"] > 0 and size["cols"] > 0, size
    res = ctx.run(common)
    assert "0 with problems" in res.stdout, res.stdout


@e2e("gtopt_compare_runs")
def case_compare_runs_self_and_perturbed(ctx: CliRun) -> None:
    """Two copies of a synthetic gtopt output: equal (rc 0), then perturbed (rc 1)."""
    import pandas as pd  # pylint: disable=import-outside-toplevel

    from gtopt_shared.testing.tiny_cases import (  # pylint: disable=import-outside-toplevel
        write_tiny_case,
        write_tiny_results,
    )

    runs = []
    for name in ("a", "b"):
        write_tiny_case(ctx.tmp_path / name)
        runs.append(write_tiny_results(ctx.tmp_path / name / "results"))
    report = ctx.tmp_path / "report.json"
    res = ctx.run([*runs, "--json", report])
    assert "PASS" in res.stdout
    assert load_json(report)["passed"] is True
    gen = runs[1] / "Generator" / "generation_sol.parquet"
    df = pd.read_parquet(gen)
    df["value"] *= 1.5
    df.to_parquet(gen, index=False)
    res = ctx.run([*runs, "--csv", ctx.tmp_path / "csv"], expect_rc=1)
    assert "FAIL" in res.stdout
    assert (ctx.tmp_path / "csv" / "elements.csv").stat().st_size > 0
    ctx.run([runs[0], ctx.tmp_path / "missing"], expect_rc=2)
