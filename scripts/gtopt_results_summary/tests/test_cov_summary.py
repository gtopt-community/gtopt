# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for :mod:`gtopt_results_summary.summary`."""

from __future__ import annotations

import builtins
import io
import zipfile

import pandas as pd
import pytest

from gtopt_results_summary import summarize_output_dict, summarize_results
from gtopt_results_summary import summary as summary_mod

_SOL = (
    "scene,phase,status,status_name,obj_value,kappa,max_kappa,"
    "scale_objective\n0,0,0,optimal,3.0,1,1,1\n"
)


def _long(rows: list[list[float]], with_block: bool = True) -> dict:
    cols = ["scenario", "stage", "block", "uid", "value"]
    if not with_block:
        cols = ["scenario", "stage", "uid", "value"]
        rows = [[r[0], r[1], r[3], r[4]] for r in rows]
    return {"columns": cols, "data": rows}


def test_long_layout_counts_and_stats():
    """Long-layout outputs: per-uid grouping, distinct blocks and elements."""
    gen = _long([[1, 1, 1, 1, 10.0], [1, 1, 2, 1, 5.0], [1, 1, 1, 2, 7.0]])
    fail = _long([[1, 1, 1, 3, 0.5], [1, 1, 2, 3, 2.5]])
    bal = _long([[1, 1, 1, 1, 10.0], [1, 1, 1, 2, 30.0]])
    flow = _long([[1, 1, 1, 7, 1.0], [1, 1, 1, 8, 1.0], [1, 1, 1, 9, 1.0]])
    results = {
        "solution": {},
        "outputs": {
            "Generator/generation_sol": gen,
            "Demand/fail_sol": fail,
            "Bus/balance_dual": bal,
            "Line/flow_sol": flow,
        },
    }
    s = summarize_output_dict(results, tech_map={"1": "hydro"})
    assert s["total_generation"] == pytest.approx(22.0)
    assert s["generation_by_tech"] == {"hydro": 15.0, "other": 7.0}
    assert s["n_generators"] == 2
    assert s["n_blocks"] == 2
    assert s["total_unserved"] == pytest.approx(3.0)
    assert s["peak_unserved"] == pytest.approx(2.5)
    assert s["lmp_min"] == pytest.approx(10.0)
    assert s["lmp_max"] == pytest.approx(30.0)
    assert s["lmp_mean"] == pytest.approx(20.0)
    assert s["n_buses"] == 2
    assert s["n_lines"] == 3


def test_long_layout_without_block_counts_rows():
    gen = _long([[1, 1, 1, 1, 10.0], [1, 1, 2, 1, 5.0]], with_block=False)
    s = summarize_output_dict({"outputs": {"Generator/generation_sol": gen}})
    assert s["n_blocks"] == 2


def test_basename_fallback_and_index_only_tables():
    """Bare basename keys are accepted; index-only tables give empty stats."""
    results = {
        "outputs": {
            "load_sol": {
                "columns": ["scenario", "stage", "block", "uid:1"],
                "data": [[1, 1, 1, 4.0]],
            },
            "fail_sol": {
                "columns": ["scenario", "stage", "block"],
                "data": [[1, 1, 1]],
            },
            "balance_dual": {"columns": ["block"], "data": []},
        }
    }
    s = summarize_output_dict(results)
    assert s["total_load"] == pytest.approx(4.0)
    assert s["peak_unserved"] == 0.0
    assert s["lmp_min"] is None
    assert s["n_buses"] == 0


def test_duplicate_columns_are_skipped():
    """A duplicated column name yields a DataFrame slice; it is skipped."""
    df = pd.DataFrame([[1.0, 2.0, 3.0]], columns=["uid:1", "uid:1", "uid:2"])
    assert summary_mod._sum_numeric(df, set()) == {"uid:2": 3.0}


def test_read_output_dir_mixed_files(tmp_path):
    (tmp_path / "solution.csv").write_text(_SOL, encoding="utf-8")
    gen_dir = tmp_path / "Generator"
    gen_dir.mkdir()
    pd.DataFrame({"block": [1, 2], "uid:1": [3.0, 4.0]}).to_parquet(
        gen_dir / "generation_sol.parquet"
    )
    # Root-level CSV (no parent) and an unreadable/garbage CSV.
    (tmp_path / "load_sol.csv").write_text("block,uid:9\n1,6.0\n", encoding="utf-8")
    (gen_dir / "generation_cost.csv").write_bytes(b"")  # EmptyDataError
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")
    s = summarize_results(tmp_path)
    assert s["total_generation"] == pytest.approx(7.0)
    assert s["total_load"] == pytest.approx(6.0)
    assert s["total_cost_generation"] == 0.0
    assert s["obj_value"] == pytest.approx(3.0)


def test_read_zip_inline_fallback(tmp_path, monkeypatch):
    """Without guiservice the ZIP is parsed inline (csv/parquet/bad/other)."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "guiservice.app":
            raise ImportError("no guiservice")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    buf = io.BytesIO()
    pd.DataFrame({"block": [1], "uid:2": [9.0]}).to_parquet(buf)
    zip_path = tmp_path / "out.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("solution.csv", _SOL)
        zf.writestr("Generator/generation_sol.parquet", buf.getvalue())
        zf.writestr("load_sol.csv", "block,uid:1\n1,2.0\n")
        zf.writestr("Demand/fail_sol.csv", "")  # EmptyDataError -> skipped
        zf.writestr("README.txt", "ignored")
    s = summarize_results(zip_path)
    assert s["total_generation"] == pytest.approx(9.0)
    assert s["total_load"] == pytest.approx(2.0)
    assert s["total_unserved"] == 0.0
    assert s["obj_value"] == pytest.approx(3.0)


def test_read_zip_prefers_guiservice_parser(tmp_path, monkeypatch):
    """When guiservice.app is importable its parser is used verbatim."""
    import sys
    import types

    seen: list[bytes] = []

    def fake_parse(data: bytes) -> dict:
        seen.append(data)
        return {"solution": {}, "outputs": {}}

    pkg = types.ModuleType("guiservice")
    app = types.ModuleType("guiservice.app")
    app._parse_results_zip = fake_parse  # type: ignore[attr-defined]
    pkg.app = app  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "guiservice", pkg)
    monkeypatch.setitem(sys.modules, "guiservice.app", app)

    zip_path = tmp_path / "out.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("solution.csv", _SOL)
    s = summarize_results(zip_path)
    assert seen and seen[0] == zip_path.read_bytes()
    assert s["total_generation"] == 0.0
