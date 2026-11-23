# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""CLI subcommands, cascade-run orchestration and result/investment projection."""

from __future__ import annotations

import csv
import json
import sys
import types
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from gtopt_reduce_network import _cascade_run
from gtopt_reduce_network._busmap import (
    AggregatorRow,
    BusmapRow,
    LinemapRow,
    save_aggregator,
    save_busmap,
    save_linemap,
)
from gtopt_reduce_network._io import Case, _index_buses
from gtopt_reduce_network._project_dispatch import (
    _broadcast_bus_column,
    _rewrite_bus_column,
    _split_line_column,
    project_results,
)
from gtopt_reduce_network._project_investment import (
    ProjectInvestmentConfig,
    _cluster_uid_of_entry,
    _existing_capacity_by_bus,
    _largest_remainder_split,
    _line_weights,
    _peak_load_by_bus,
    _scalar_peak,
    project_investment,
    write_audit_csv,
    write_patch_json,
)
from gtopt_reduce_network.main import main

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _case(extra: dict[str, Any] | None = None) -> Case:
    system: dict[str, Any] = {
        "bus_array": [
            {"uid": 1, "name": "b1"},
            {"uid": 2, "name": "b2"},
            {"uid": 3, "name": "b3"},
        ],
        "generator_array": [
            {"uid": 1, "bus": "b1", "capacity": 200},
            {"uid": 2, "bus": 2, "pmax": 100},
            {"uid": 3, "bus": "nowhere", "pmax": 5},
            {"uid": 4, "bus": True, "pmax": 5},
            {"uid": 5},
            {"uid": 6, "bus": "b3", "pmax": "file"},
        ],
        "battery_array": [{"uid": 1, "bus": "b3", "pmax": 7}],
        "demand_array": [
            {"uid": 1, "bus": "b1", "lmax": [[1, 4], [2]]},
            {"uid": 2, "bus": "b2", "lmax": 3},
            {"uid": 3, "bus": "zz", "lmax": 3},
            {"uid": 4},
        ],
        "line_array": [
            {"uid": 10, "bus_a": 1, "bus_b": 2, "tmax_ab": 0},
            {"uid": 11, "bus_a": 1, "bus_b": 2, "tmax_ab": 30},
        ],
    }
    system.update(extra or {})
    case = Case(raw={"options": {}, "simulation": {}, "system": system})
    _index_buses(case)
    return case


def _write_parquet(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


# ---------------------------------------------------------------------------
# project_dispatch
# ---------------------------------------------------------------------------


def test_rewrite_bus_column_variants():
    share = {
        ("generator", 1): [(11, 1.0)],
        ("generator", 2): [(21, 0.25), (22, 0.75)],
    }
    df = pd.DataFrame({"uid": [1, 2, 3], "bus": [100, 200, 300], "value": [4, 8, 1]})
    out = _rewrite_bus_column(df, "generator", share)
    assert out["bus"].tolist() == [11, 21, 22, 300]
    assert out["value"].tolist() == [4, 2.0, 6.0, 1]
    # bus_uid column variant
    df2 = pd.DataFrame({"uid": [1], "bus_uid": [100]})
    assert _rewrite_bus_column(df2, "generator", share)["bus_uid"].tolist() == [11]
    # passthrough: no bus column, or no uid column
    df3 = pd.DataFrame({"uid": [1], "value": [1.0]})
    assert _rewrite_bus_column(df3, "generator", share) is df3
    df4 = pd.DataFrame({"bus": [1], "value": [1.0]})
    assert _rewrite_bus_column(df4, "generator", share) is df4


def test_split_and_broadcast_columns():
    df = pd.DataFrame({"uid": [100, 5], "flow": [10.0, 3.0]})
    out = _split_line_column(df, {100: [1, 2]})
    assert out["uid"].tolist() == [1, 2, 5]
    assert out["flow"].tolist() == [5.0, 5.0, 3.0]
    no_uid = pd.DataFrame({"flow": [1.0]})
    assert _split_line_column(no_uid, {}) is no_uid
    lmp = pd.DataFrame({"uid": [7, 8], "lmp": [1.5, 2.5]})
    out = _broadcast_bus_column(lmp, {7: [1, 2]})
    assert out["uid"].tolist() == [1, 2, 8]
    assert out["lmp"].tolist() == [1.5, 1.5, 2.5]
    assert _broadcast_bus_column(no_uid, {}) is no_uid


def test_project_results_end_to_end(tmp_path: Path):
    src = tmp_path / "reduced"
    _write_parquet(
        src / "Generator" / "generation_sol.parquet",
        pd.DataFrame({"uid": [1], "bus": [1], "value": [10.0]}),
    )
    _write_parquet(
        src / "Line" / "flowp_sol.parquet",
        pd.DataFrame({"uid": [100], "value": [6.0]}),
    )
    _write_parquet(
        src / "Bus" / "balance_dual.parquet",
        pd.DataFrame({"uid": [1], "value": [3.0]}),
    )
    save_busmap([BusmapRow(1, 1), BusmapRow(2, 1)], tmp_path / "bm.csv")
    save_linemap(
        [
            LinemapRow(10, 100, "inter-cluster"),
            LinemapRow(11, 100, "inter-cluster"),
            LinemapRow(12, None, "inter-cluster"),
            LinemapRow(13, None, "intra-cluster"),
        ],
        tmp_path / "lm.csv",
    )
    save_aggregator(
        [
            AggregatorRow("generator", 1, 1, 1, 0.5),
            AggregatorRow("generator", 1, 2, 1, 0.5),
        ],
        tmp_path / "agg.csv",
    )
    dst = tmp_path / "projected"
    out = project_results(
        src,
        busmap=tmp_path / "bm.csv",
        linemap=tmp_path / "lm.csv",
        aggregator=tmp_path / "agg.csv",
        output_dir=dst,
    )
    assert sorted(out) == ["Bus", "Generator", "Line"]
    gen = pd.read_parquet(dst / "Generator" / "generation_sol.parquet")
    assert gen["bus"].tolist() == [1, 2]
    assert gen["value"].tolist() == [5.0, 5.0]
    line = pd.read_parquet(dst / "Line" / "flowp_sol.parquet")
    assert line["uid"].tolist() == [10, 11]
    bus = pd.read_parquet(dst / "Bus" / "balance_dual.parquet")
    assert bus["uid"].tolist() == [1, 2]


# ---------------------------------------------------------------------------
# project_investment
# ---------------------------------------------------------------------------


def test_investment_helpers():
    case = _case()
    assert _cluster_uid_of_entry({"bus": 3}, case) == 3
    assert _cluster_uid_of_entry({"bus": "b2"}, case) == 2
    assert _cluster_uid_of_entry({"bus": "missing"}, case) == 0
    with pytest.raises(ValueError, match="missing bus ref"):
        _cluster_uid_of_entry({}, case)
    assert _line_weights(case, [10, 11, 99], mode="capacity") == [1.0, 30.0, 1.0]
    with pytest.raises(ValueError, match="unsupported line_share_mode"):
        _line_weights(case, [10], mode="loading")
    assert _existing_capacity_by_bus(case) == {
        ("generator", 1): 200.0,
        ("generator", 2): 100.0,
        ("battery", 3): 7.0,
    }
    assert _peak_load_by_bus(case) == {1: 4.0, 2: 3.0}
    assert _scalar_peak(True) == 0.0
    assert _scalar_peak([["x"]]) == 0.0
    assert _scalar_peak("f") == 0.0
    assert _largest_remainder_split(0.0, [1.0, 2.0], integer=False) == [0.0, 0.0]
    assert not _largest_remainder_split(5.0, [], integer=False)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("existing", {1: 20.0, 2: 10.0}),
        ("nodal_lp", {1: 20.0, 2: 10.0}),
        ("resource", {1: 200 / 301 * 30, 2: 100 / 301 * 30, 3: 1 / 301 * 30}),
    ],
)
def test_investment_share_modes(mode: str, expected: dict[int, float]):
    bm = [BusmapRow(1, 1), BusmapRow(2, 1), BusmapRow(3, 1)]
    reduced = {"generator_array": [{"uid": 9, "bus": 1, "expmod_delta": 30.0}]}
    patch, _ = project_investment(
        reduced,
        original_case=_case(),
        busmap=bm,
        aggregator=[],
        linemap=[],
        config=ProjectInvestmentConfig(share_mode=mode),
    )
    got = {r["bus"]: r["expmod_delta"] for r in patch["system"]["generator_array"]}
    assert got == pytest.approx(expected)


def test_investment_manual_and_errors(tmp_path: Path):
    shares = tmp_path / "shares.csv"
    with shares.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["kind", "bus_uid", "weight"])
        writer.writerow([" generator ", "2", "1"])
    bm = [BusmapRow(1, 1), BusmapRow(2, 1)]
    reduced = {
        "generator_array": [
            {"uid": 9, "bus": 1, "expmod_delta": 4.0, "integer_expmod": True},
            {"uid": 8, "bus": 1, "expmod_delta": 0.0},
        ],
        "line_array": [
            {"uid": 100, "expmod_delta": 0.0},
            {"uid": 555, "expmod_delta": 5.0},
            {"uid": 100, "expmod_delta": 30.0},
        ],
    }
    lm = [
        LinemapRow(10, 100, "inter-cluster"),
        LinemapRow(11, 100, "inter-cluster"),
        LinemapRow(12, 100, "parallel-merge"),
    ]
    patch, audit = project_investment(
        reduced,
        original_case=_case(),
        busmap=bm,
        aggregator=[],
        linemap=lm,
        config=ProjectInvestmentConfig(share_mode="manual", share_csv=shares),
    )
    assert patch["system"]["generator_array"] == [
        {"based_on": 9, "bus": 2, "expmod_delta": 4.0}
    ]
    lines = {r["uid"]: r["expmod_delta"] for r in patch["system"]["line_array"]}
    assert lines == pytest.approx({10: 30 / 31, 11: 30 * 30 / 31})
    assert [r["kind"] for r in audit] == ["generator", "line", "line"]

    # integer split of 1 unit over 2 corridors: one original gets 0
    patch, _ = project_investment(
        {"line_array": [{"uid": 100, "expmod_delta": 1.0, "integer_expmod": True}]},
        original_case=_case(),
        busmap=bm,
        aggregator=[],
        linemap=lm,
        config=ProjectInvestmentConfig(),
    )
    assert patch["system"]["line_array"] == [{"uid": 11, "expmod_delta": 1.0}]

    with pytest.raises(ValueError, match="requires share_csv"):
        project_investment(
            reduced,
            original_case=_case(),
            busmap=bm,
            aggregator=[],
            linemap=[],
            config=ProjectInvestmentConfig(share_mode="manual"),
        )
    with pytest.raises(ValueError, match="unknown share_mode"):
        project_investment(
            {},
            original_case=_case(),
            busmap=bm,
            aggregator=[],
            linemap=[],
            config=ProjectInvestmentConfig(share_mode="bogus"),
        )


def test_write_audit_and_patch(tmp_path: Path):
    empty = tmp_path / "sub" / "empty.csv"
    write_audit_csv([], empty)
    assert empty.read_text(encoding="utf-8") == ""
    full = tmp_path / "audit.csv"
    write_audit_csv([{"b": 1, "a": 2}, {"a": 3, "c": 4}], full)
    rows = list(csv.DictReader(full.open(encoding="utf-8")))
    assert rows[0] == {"a": "2", "b": "1", "c": ""}
    patch = tmp_path / "p" / "patch.json"
    write_patch_json({"system": {}}, patch)
    assert json.loads(patch.read_text(encoding="utf-8")) == {"system": {}}


# ---------------------------------------------------------------------------
# CLI subcommands
# ---------------------------------------------------------------------------


def _run_main(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    return int(exc.value.code or 0)


def test_cli_project_results_and_investment(tmp_path: Path, capsys):
    save_busmap([BusmapRow(1, 1), BusmapRow(2, 1)], tmp_path / "bm.csv")
    save_linemap([LinemapRow(10, 100, "inter-cluster")], tmp_path / "lm.csv")
    save_aggregator([], tmp_path / "agg.csv")
    maps = [
        "--busmap",
        str(tmp_path / "bm.csv"),
        "--linemap",
        str(tmp_path / "lm.csv"),
        "--aggregator",
        str(tmp_path / "agg.csv"),
    ]
    out_dir = tmp_path / "proj"
    rc = _run_main(
        ["project-results", str(tmp_path / "none"), "-o", str(out_dir)] + maps
    )
    assert rc == 0
    assert str(out_dir) in capsys.readouterr().out

    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(_case().raw), encoding="utf-8")
    inv = tmp_path / "inv.json"
    inv.write_text(
        json.dumps({"generator_array": [{"uid": 9, "bus": 1, "expmod_delta": 3}]}),
        encoding="utf-8",
    )
    patch = tmp_path / "patch.json"
    audit = tmp_path / "audit.csv"
    base = [
        "project-investment",
        str(inv),
        "--original-case",
        str(case_path),
        "-o",
        str(patch),
    ] + maps
    assert _run_main(base + ["--audit-csv", str(audit)]) == 0
    assert audit.exists()
    patch.unlink()
    assert _run_main(base + ["--share-mode", "load"]) == 0
    assert patch.exists()
    # error path: manual without csv -> ValueError -> exit 1
    assert _run_main(base + ["--share-mode", "manual"]) == 1


def test_cli_reduce_bus_ratio_protect_and_uc_drop(tmp_path: Path, capsys):
    buses = [{"uid": i, "name": f"b{i}"} for i in range(1, 7)]
    lines = [
        {
            "uid": 100 + i,
            "name": f"l{i}",
            "bus_a": f"b{i}",
            "bus_b": f"b{i + 1}",
            "reactance": 0.1 * i,
            "tmax_ab": 100,
            "tmax_ba": 100,
        }
        for i in range(1, 6)
    ]
    case = {
        "options": {},
        "simulation": {},
        "system": {
            "bus_array": buses,
            "line_array": lines,
            "generator_array": [{"uid": 1, "bus": "b1", "pmax": 50, "gcost": 1}],
            "demand_array": [{"uid": 1, "bus": "b6", "lmax": 40}],
            "user_constraint_array": [
                {"name": "uc_l3", "expression": 'line("l3").flow <= 10'},
                {"name": "uc_l2", "expression": 'line("l2").flow <= 10'},
            ],
            "line_commitment_array": [{"line": "l3"}],
        },
    }
    inp = tmp_path / "case.json"
    inp.write_text(json.dumps(case), encoding="utf-8")
    rc = _run_main(
        ["reduce", str(inp), "--bus-ratio", "0.5", "--protect-constraint-lines"]
    )
    assert rc == 0
    out_path = Path(capsys.readouterr().out.strip())
    reduced = json.loads(out_path.read_text(encoding="utf-8"))
    names = {ln["name"] for ln in reduced["system"]["line_array"]}
    assert "l3" in names
    kept = [uc["name"] for uc in reduced["system"]["user_constraint_array"]]
    assert "uc_l3" in kept

    # without protection the l2/l3 constraints dangle and are dropped
    out2 = tmp_path / "plain.json"
    assert _run_main(["reduce", str(inp), "-K", "2", "-o", str(out2)]) == 0
    plain = json.loads(out2.read_text(encoding="utf-8"))
    assert not plain["system"]["user_constraint_array"]

    # invalid ratio / both or neither -> exit 1
    assert _run_main(["reduce", str(inp), "--bus-ratio", "1.5"]) == 1
    assert _run_main(["reduce", str(inp)]) == 1


def test_cli_cascade_run_strips_separator(tmp_path: Path, monkeypatch, capsys):
    calls: list[dict[str, Any]] = []

    def fake_cascade(inp: Path, **kwargs: Any) -> dict[str, Path]:
        calls.append({"input": inp, **kwargs})
        return {"reduced_case": tmp_path / "r.json"}

    monkeypatch.setattr("gtopt_reduce_network.main.cascade_run", fake_cascade)
    rc = _run_main(
        [
            "cascade-run",
            str(tmp_path / "c.json"),
            "-K",
            "3",
            "-o",
            str(tmp_path / "o"),
            "--also-run-full",
            "--",
            "--lp-only",
        ]
    )
    assert rc == 0
    assert calls[0]["extra_gtopt_args"] == ["--lp-only"]
    assert calls[0]["skip_full"] is False
    assert "reduced_case:" in capsys.readouterr().out
    _run_main(["cascade-run", str(tmp_path / "c.json"), "-K", "3", "-o", "x"])
    assert not calls[1]["extra_gtopt_args"]


# ---------------------------------------------------------------------------
# cascade_run
# ---------------------------------------------------------------------------


def test_cascade_run_pipeline(tmp_path: Path, ieee14_path: Path, monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setattr(
        _cascade_run.subprocess,
        "run",
        lambda cmd, check: commands.append(cmd),
    )
    fake_bin = tmp_path / "gtopt"
    fake_bin.write_text("", encoding="utf-8")
    paths = _cascade_run.cascade_run(
        ieee14_path,
        target_buses=5,
        output_dir=tmp_path / "out",
        gtopt_bin=fake_bin,
        extra_gtopt_args=["--x"],
        skip_full=False,
    )
    assert set(paths) == {"reduced_case", "reduced_dir", "projected_dir", "full_dir"}
    assert paths["reduced_case"].exists()
    assert [c[-1] for c in commands] == ["--x", "--x"]
    assert commands[1][1] == str(ieee14_path)

    commands.clear()
    _cascade_run.cascade_run(
        ieee14_path,
        target_buses=5,
        output_dir=tmp_path / "out3",
        gtopt_bin=fake_bin,
        skip_full=False,
    )
    assert commands[1][-1] == str(tmp_path / "out3" / "full_out")

    commands.clear()
    paths = _cascade_run.cascade_run(
        ieee14_path,
        target_buses=5,
        output_dir=tmp_path / "out2",
        gtopt_bin=fake_bin,
    )
    assert "full_dir" not in paths
    assert len(commands) == 1
    assert commands[0][-1] == str(tmp_path / "out2" / "reduced_out")


def test_resolve_gtopt_bin_fallbacks(tmp_path: Path, monkeypatch):
    real = tmp_path / "gtopt"
    real.write_text("", encoding="utf-8")
    assert _cascade_run._resolve_gtopt_bin(real) == real

    monkeypatch.setattr(_cascade_run.shutil, "which", lambda _: "/usr/bin/gtopt")
    assert _cascade_run._resolve_gtopt_bin(tmp_path / "missing") == Path(
        "/usr/bin/gtopt"
    )

    monkeypatch.setattr(_cascade_run.shutil, "which", lambda _: None)
    fake_mod = types.ModuleType("get_gtopt_binary")
    fake_mod.resolve_gtopt_bin = lambda: "/opt/gtopt"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "get_gtopt_binary", fake_mod)
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert _cascade_run._resolve_gtopt_bin(None) == Path("/opt/gtopt")

    monkeypatch.setitem(sys.modules, "get_gtopt_binary", None)
    with pytest.raises(FileNotFoundError, match="gtopt binary not found"):
        _cascade_run._resolve_gtopt_bin(None)
