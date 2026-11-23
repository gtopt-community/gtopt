# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Driver branches of ``gtopt_marginal_units.main`` (CLI + per-cell loop)."""

from __future__ import annotations

import argparse
import json
import logging
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from gtopt_canonical_feed import Bus, Cells, Generator, Line, Topology
from gtopt_marginal_units import main as mu
from gtopt_marginal_units.constants import (
    EXIT_INPUT_ERROR,
    EXIT_OK,
    EXIT_UNATTRIBUTED,
    Tolerances,
)
from gtopt_marginal_units.errors import (
    AttributionError,
    InputValidationError,
    MarginalUnitsError,
)
from gtopt_marginal_units._lp_duals import GtoptLpDuals

_KEY = {
    "scenario": [1],
    "stage": [1],
    "block": [1],
    "date_utc": [None],
    "hour": [None],
    "data_source": ["simulated"],
}
TOL = Tolerances.default()


def _frame(uid_col: str, val_col: str, values: dict[int, float]) -> pd.DataFrame:
    n = len(values)
    return pd.DataFrame(
        {
            **{k: v * n for k, v in _KEY.items()},
            uid_col: list(values),
            val_col: list(values.values()),
        }
    )


def _gen(uid: int, bus: int, mc: float = 10.0, **kw) -> Generator:
    return Generator(
        uid=uid,
        name=kw.pop("name", f"g{uid}"),
        bus_uid=bus,
        pmin=0.0,
        pmax=kw.pop("pmax", 100.0),
        declared_MC=mc,
        kind=kw.pop("kind", "thermal"),
        emission_rate=0.4,
        **kw,
    )


def _two_bus(tmax: float = 100.0, extra_lines: tuple[Line, ...] = ()) -> Topology:
    return Topology(
        buses=[Bus(uid=1, name="b1"), Bus(uid=2, name="b2")],
        generators=[_gen(10, 1, 10.0), _gen(20, 2, 50.0)],
        lines=[Line(uid=100, bus_a_uid=1, bus_b_uid=2, tmax_ab=tmax, tmax_ba=tmax)]
        + list(extra_lines),
    )


def _process(topology: Topology, cells: Cells, out: Path, **kw):
    params = {
        "lp_duals": GtoptLpDuals.empty(),
        "tol": TOL,
        "merit_ladder_depth": 0,
        "single_bus": False,
        "zone_mode": "lp-duals",
        "demand_fail_cost": 1000.0,
    }
    params.update(kw)
    return mu._process_cells(topology=topology, cells=cells, out_root=out, **params)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# CLI wrapper
# ---------------------------------------------------------------------------


def _sim_args(planning: Path, output: Path, out: Path) -> list[str]:
    return [
        "--input-kind",
        "gtopt-dir",
        "--mode",
        "simulated",
        "--planning",
        str(planning),
        "--output",
        str(output),
        "--out",
        str(out),
    ]


def test_cli_reads_sys_argv(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gtopt-marginal-units", "-q"])
    assert mu.cli() == EXIT_INPUT_ERROR


@pytest.mark.parametrize("exc", [AttributionError("bad"), MarginalUnitsError("worse")])
def test_cli_maps_library_errors(monkeypatch, exc):
    def _boom(_args):
        raise exc

    monkeypatch.setattr(mu, "_run", _boom)
    assert mu.cli(["-vv"]) == EXIT_INPUT_ERROR


@pytest.mark.parametrize(
    ("verbose", "quiet", "level"),
    [(0, True, logging.ERROR), (2, False, logging.DEBUG), (1, False, logging.INFO)],
)
def test_setup_logging_levels(monkeypatch, verbose, quiet, level):
    seen = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: seen.update(kw))
    mu._setup_logging(verbose, quiet)
    assert seen["level"] == level


@pytest.mark.parametrize(
    "extra",
    [
        ["--loss-factor-warn", "0.5"],
        ["--loss-factor-warn", "2.0", "--loss-factor-error", "1.5"],
    ],
)
def test_cli_rejects_bad_loss_factor_band(
    tiny_planning, tiny_output_dir, tmp_path, extra
):
    argv = _sim_args(tiny_planning, tiny_output_dir, tmp_path / "ds") + extra
    assert mu.cli(argv) == EXIT_INPUT_ERROR


def test_cli_report_and_unattributed_exit(tiny_planning, tiny_output_dir, tmp_path):
    # No generator dispatches and λ is neither 0 nor the fail cost:
    # the single island is unattributed -> audit table + exit code 3.
    disp = pd.read_csv(tiny_output_dir / "Generator/generation_sol.csv")
    disp["uid:10"] = 0.0
    disp.to_csv(tiny_output_dir / "Generator/generation_sol.csv", index=False)
    lmp = pd.read_csv(tiny_output_dir / "Bus/balance_dual.csv")
    lmp[["uid:1", "uid:2"]] = 55.0
    lmp.to_csv(tiny_output_dir / "Bus/balance_dual.csv", index=False)
    report = tmp_path / "report.md"
    argv = _sim_args(tiny_planning, tiny_output_dir, tmp_path / "ds") + [
        "--report",
        str(report),
        "--merit-ladder-depth",
        "0",
        "-v",
    ]
    assert mu.cli(argv) == EXIT_UNATTRIBUTED
    assert (tmp_path / "ds/audit/unattributed.parquet").exists()
    assert report.read_text(encoding="utf-8")


def test_cli_report_without_unattributed(tiny_planning, tiny_output_dir, tmp_path):
    report = tmp_path / "report.md"
    argv = _sim_args(tiny_planning, tiny_output_dir, tmp_path / "ds") + [
        "--report",
        str(report),
    ]
    assert mu.cli(argv) == EXIT_OK
    assert report.exists()


def test_resolve_input_kind_auto():
    ns = argparse.Namespace(
        input_kind="auto", planning=Path("p"), output=None, feed=None
    )
    assert mu._resolve_input_kind(ns) == "gtopt-dir"
    ns = argparse.Namespace(
        input_kind="auto", planning=None, output=None, feed=Path("f")
    )
    assert mu._resolve_input_kind(ns) == "feed-parquet"


def test_package_cli_and_dunder_main(monkeypatch):
    import gtopt_marginal_units as pkg  # noqa: PLC0415

    monkeypatch.setattr(sys, "argv", ["gtopt-marginal-units", "-q"])
    assert pkg.cli() == EXIT_INPUT_ERROR
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("gtopt_marginal_units", run_name="__main__")
    assert exc.value.code == EXIT_INPUT_ERROR


# ---------------------------------------------------------------------------
# _process_cells
# ---------------------------------------------------------------------------


def test_process_cells_rejects_empty_dispatch(tmp_path):
    with pytest.raises(InputValidationError, match="empty"):
        _process(_two_bus(), Cells(), tmp_path)


def test_flow_cost_saturation_splits_islands(tmp_path):
    cells = Cells(
        dispatch=_frame("gen_uid", "dispatch", {10: 80.0, 20: 20.0}),
        lmp=_frame("bus_uid", "lmp", {1: 10.0, 2: 50.0}),
        flow_dual=_frame("line_uid", "flow_dual", {100: -40.0}),
    )
    summary = _process(_two_bus(), cells, tmp_path, merit_ladder_depth=2)
    assert summary.rows_per_zone == 2
    zones = pd.read_parquet(tmp_path / "attribution/per_zone.parquet")
    assert sorted(zones["zone_lmp"]) == [10.0, 50.0]


def test_primal_fallback_saturation_and_orphan_bus(tmp_path):
    topo = _two_bus(
        tmax=50.0,
        extra_lines=(Line(uid=101, bus_a_uid=1, bus_b_uid=3, tmax_ab=0, tmax_ba=0),),
    )
    topo.buses.append(Bus(uid=3, name="orphan"))
    cells = Cells(
        dispatch=_frame("gen_uid", "dispatch", {10: 80.0, 20: 20.0}),
        lmp=_frame("bus_uid", "lmp", {1: 10.0, 2: 50.0}),
        flow=_frame("line_uid", "flow", {100: 50.0, 101: 0.0, 999: 7.0}),
    )
    _process(topo, cells, tmp_path)
    per_bus = pd.read_parquet(tmp_path / "attribution/per_bus.parquet")
    assert 3 not in set(per_bus["bus_uid"])
    assert per_bus["zone_id"].nunique() == 2


def test_single_bus_reconstruct_path_with_dict_fail_cost(tmp_path):
    cells = Cells(
        dispatch=_frame("gen_uid", "dispatch", {10: 50.0}),
        load=_frame("bus_uid", "load", {1: 50.0}),
    )
    summary = _process(
        _two_bus(), cells, tmp_path, single_bus=True, demand_fail_cost={1: 900.0}
    )
    assert summary.rows_per_zone == 1


def _recipe(status: str, raw: float = 1.0, neg: str | None = None):
    return SimpleNamespace(
        loss_factor_status=status, loss_factor_raw=raw, negative_lmp_kind=neg
    )


def test_loss_factor_summary_warnings(tmp_path, monkeypatch, caplog):
    statuses = [
        _recipe("ok", 1.01),
        _recipe("warn_radial", 1.3),
        _recipe("warn_meshed", 1.4),
        _recipe("critical_radial", 3.0),
        _recipe("critical_meshed", 4.0),
        _recipe("cross_island"),
        _recipe("zero_srmc_hydro"),
        _recipe("no_emission_data"),
        _recipe("negative", -1.0, neg="no_marginal"),
        _recipe("missing", neg="storage_clamped"),
    ]
    monkeypatch.setattr(
        mu, "build_recipes_for_cell", lambda **_kw: ([], list(statuses))
    )
    monkeypatch.setattr(
        mu,
        "write_dataset",
        lambda *_a, **_kw: SimpleNamespace(
            rows_per_bus=0,
            rows_per_zone=0,
            rows_merit_ladder=0,
            rows_price_recipe=0,
        ),
    )
    cells = Cells(
        dispatch=_frame("gen_uid", "dispatch", {10: 50.0}),
        lmp=_frame("bus_uid", "lmp", {1: 10.0, 2: 10.0}),
    )
    topo = Topology(buses=_two_bus().buses, generators=_two_bus().generators)
    with caplog.at_level(logging.WARNING, logger="gtopt_marginal_units"):
        _process(topo, cells, tmp_path, carbon_price=5.0)
    text = caplog.text
    for needle in (
        "hydro/storage",
        "no emission_rate",
        "RADIAL path had raw bus_LMP",
        "MESHED path had raw bus_LMP",
        "RADIAL path had raw >",
        "MESHED path had raw >",
        "cross-island",
        "negative raw",
        "NO storage marginal",
        "STORAGE marginal",
    ):
        assert needle in text


def test_summary_without_recipe_rows(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(mu, "build_recipes_for_cell", lambda **_kw: ([], []))
    cells = Cells(dispatch=_frame("gen_uid", "dispatch", {10: 50.0}))
    with caplog.at_level(logging.INFO, logger="gtopt_marginal_units"):
        _process(_two_bus(), cells, tmp_path, single_bus=True)
    assert "loss-factor scaling" not in caplog.text


# ---------------------------------------------------------------------------
# LP-dual zone helpers
# ---------------------------------------------------------------------------


def test_matches_any_demand_fail_cost_dict():
    dfc = {1: 1000.0, 2: 500.0, 3: 800.0}
    zone_of = {1: 0, 2: 0, 3: 1}
    assert mu._matches_any_demand_fail_cost(500.0, dfc, 0, zone_of, 1e-3)
    assert not mu._matches_any_demand_fail_cost(800.0, dfc, 0, zone_of, 1e-3)
    assert not mu._matches_any_demand_fail_cost(9.0, {}, 0, {1: 0}, 1e-3)
    assert mu._matches_any_demand_fail_cost(9.0, 9.0, 0, {}, 1e-3)


def test_zone_results_by_lmp_bucket_and_demand_fail():
    topo = Topology(
        buses=[Bus(uid=b, name=f"b{b}") for b in (1, 2, 3)],
        generators=[_gen(10, 1, 10.0)],
    )
    zone_of, results = mu._zone_results_from_lp_duals(
        topology=topo,
        lmp_by_bus={1: 10.0, 2: 10.0 + 1e-4, 3: 1000.0},
        dispatch_by_uid={10: 5.0},
        tol=TOL,
        merit_eligible={10: True},
        demand_fail_cost=1000.0,
    )
    assert zone_of == {1: 0, 2: 0, 3: 1}
    assert results[0].formula_kind == "single_unit"
    assert results[1].formula_kind == "demand_fail"


def test_is_synthetic_gen_with_unparsable_pmax():
    assert mu._is_synthetic_gen(SimpleNamespace(pmax=None, name="TAMA_INF"))
    assert not mu._is_synthetic_gen(SimpleNamespace(pmax="x", name=None))


def test_select_candidates_rc_edge_cases():
    topo = Topology(
        buses=[Bus(uid=1, name="b1")],
        generators=[_gen(10, 1, 10.0), _gen(11, 1, 10.0)],
    )
    common = {
        "topology": topo,
        "zone_bus_uids": {1},
        "lam": 10.0,
        "dispatch_by_uid": {10: 5.0, 11: 5.0},
        "srmc_by_uid": {},
        "tol": TOL,
        "merit_eligible": {10: True, 11: True},
    }
    # rc present for another gen only -> declared-MC fallback
    cands, reason = mu._select_marginal_candidates(rc_by_uid={99: 0.0}, **common)
    assert [g.uid for g in cands] == [10, 11]
    assert reason
    # NaN rc never satisfies the band -> declared-MC fallback as well
    cands, _ = mu._select_marginal_candidates(
        rc_by_uid={10: float("nan"), 11: float("nan")}, **common
    )
    assert [g.uid for g in cands] == [10, 11]


def test_group_by_cell_none():
    assert not mu._group_by_cell(None, "gen_uid", "dispatch")


def test_storage_impact_subcommand(monkeypatch):
    import gtopt_marginal_units.storage_impact as si  # noqa: PLC0415

    monkeypatch.setattr(si, "main", len)
    assert mu.cli(["storage-impact", "a", "b"]) == 2


def test_cli_end_to_end_ok(tiny_planning, tiny_output_dir, tmp_path):
    out = tmp_path / "ds"
    assert mu.cli(_sim_args(tiny_planning, tiny_output_dir, out)) == EXIT_OK
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest
