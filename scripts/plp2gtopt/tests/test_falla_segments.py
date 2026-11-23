"""Tests for ``--falla-segments {first,all,split}`` (PLP failure tiers).

Fixture ``fixtures/plp_falla_tiers``: 1 bus, Thermal1 60 MW @ 50 $/MWh,
demand 80 / 100 MW over two 4 h blocks, three falla tiers
FALLA_001_{1,2,3} @ 300 / 500 / 1000 $/MWh whose plpmance capacity is
10 % / 30 % / 60 % of the demand.
"""

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from plp2gtopt.demand_writer import DemandWriter
from plp2gtopt.falla_segments import (
    FallaTier,
    build_falla_tiers,
    split_fractions,
    tier_name,
    tier_uid,
    tier_uid_offset,
)
from plp2gtopt.main import make_parser
from plp2gtopt.plp2gtopt import convert_plp_case

_FIXTURE = Path(__file__).parent / "fixtures" / "plp_falla_tiers"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_tier_naming_and_uids() -> None:
    assert tier_name("Bus1", 3) == "Bus1_falla3"
    assert tier_uid_offset(4) == 10
    assert tier_uid_offset(245) == 1000
    assert tier_uid_offset(3055) == 10000
    assert tier_uid(7, 1, 1000) == 7
    assert tier_uid(7, 3, 1000) == 2007


def test_split_fractions_sum_to_one() -> None:
    fracs = split_fractions([2.42, 2.42, 4.85, 38.76])
    assert sum(fracs) == pytest.approx(1.0, abs=1e-15)
    assert fracs[0] == pytest.approx(0.05, abs=1e-3)
    assert fracs[3] == pytest.approx(0.80, abs=1e-3)
    with pytest.raises(ValueError):
        split_fractions([0.0, 0.0])


class _Mance:
    def __init__(self, caps: dict[str, list[float]]) -> None:
        self._caps = caps

    def get_mance_by_name(self, name: str) -> dict[str, Any] | None:
        if name not in self._caps:
            return None
        return {"name": name, "pmax": np.array(self._caps[name])}


def _falla(num: int, name: str, bus: int, gcost: float) -> dict[str, Any]:
    return {"number": num, "name": name, "bus": bus, "gcost": gcost, "type": "falla"}


def test_build_falla_tiers_orders_by_cost_and_skips() -> None:
    centrals = [
        {"number": 1, "name": "T", "bus": 1, "gcost": 10.0, "type": "termica"},
        _falla(2, "F1_b", 1, 500.0),
        _falla(3, "F1_a", 1, 300.0),
        _falla(4, "F2", 2, 300.0),  # single tier → not split
        _falla(5, "F3_a", 3, 300.0),  # no mance for F3_b → not split
        _falla(6, "F3_b", 3, 400.0),
    ]
    mance = _Mance({"F1_a": [1.0, 3.0], "F1_b": [3.0, 9.0], "F3_a": [1.0]})
    tiers = build_falla_tiers(centrals, mance)
    assert list(tiers) == [1]
    assert [t.central["name"] for t in tiers[1]] == ["F1_a", "F1_b"]
    assert [t.k for t in tiers[1]] == [1, 2]
    assert [t.fraction for t in tiers[1]] == pytest.approx([0.25, 0.75])
    assert not build_falla_tiers(centrals, None)


# ---------------------------------------------------------------------------
# DemandWriter split
# ---------------------------------------------------------------------------


class _Costs:
    def __init__(self, sched: dict[str, tuple[list[int], list[float]]]) -> None:
        self._sched = sched

    def get_cost_by_name(self, name: str) -> dict[str, Any] | None:
        if name not in self._sched:
            return None
        stages, costs = self._sched[name]
        return {"stage": np.array(stages), "cost": np.array(costs)}


class _Stages:
    items = [{"number": 1}, {"number": 2}]


class _Centrals:
    def __init__(self, centrals: list[dict[str, Any]]) -> None:
        self.centrals = centrals


def test_tier_fcost_schedule_per_tier(tmp_path: Path) -> None:
    f1, f2 = _falla(2, "FA", 5, 300.0), _falla(3, "FB", 5, 500.0)
    tiers = {5: [FallaTier(1, f1, 0.4), FallaTier(2, f2, 0.6)]}
    dw = DemandWriter(None, None, {"output_dir": tmp_path}, tiers, 100)
    dw.tier_centrals = {5: f1, 105: f2}
    dems = [
        {"uid": 5, "name": "D_falla1", "bus": 5},
        {"uid": 105, "name": "D_falla2", "bus": 5},
    ]
    # FB has a schedule, FA does not → only uid 105 is file-based.
    df, filed = dw.to_fcost_dataframe(
        dems,
        {5: f1},
        _Costs({"FB": ([2], [700.0])}),
        _Stages(),
        _Centrals([f1, f2]),
    )
    assert filed == {105}
    assert list(df.columns) == ["stage", "uid:105"]
    assert df["uid:105"].tolist() == [500.0, 700.0]


def test_untiered_demand_keeps_bus_min_schedule(tmp_path: Path) -> None:
    f1, f2 = _falla(2, "FA", 5, 300.0), _falla(3, "FB", 5, 500.0)
    dw = DemandWriter(None, None, {"output_dir": tmp_path})
    df, filed = dw.to_fcost_dataframe(
        [{"uid": 5, "name": "D", "bus": 5}],
        {5: f1},
        _Costs({"FA": ([2], [900.0])}),
        _Stages(),
        _Centrals([f1, f2]),
    )
    # min(FA schedule 300/900, FB 500) = 300 / 500 — legacy semantics.
    assert filed == {5}
    assert df["uid:5"].tolist() == [300.0, 500.0]


# ---------------------------------------------------------------------------
# Full conversion on the fixture
# ---------------------------------------------------------------------------


def _convert(tmp_path: Path, mode: str | None) -> tuple[dict[str, Any], Path]:
    out = tmp_path / f"case_{mode}"
    opts: dict[str, Any] = {
        "input_dir": _FIXTURE,
        "output_dir": out,
        "output_file": out / f"case_{mode}.json",
        "hydrologies": "1",
        "method": "mono",
    }
    if mode is not None:
        opts["falla_segments"] = mode
    convert_plp_case(opts)
    return json.loads(Path(opts["output_file"]).read_text("utf-8")), out


def _lmax(out: Path) -> pd.DataFrame:
    df = pd.read_parquet(out / "Demand" / "lmax.parquet")
    return df.pivot_table(index="block", columns="uid", values="value")


def test_cli_flag_default_and_choices() -> None:
    parser = make_parser()
    assert parser.parse_args([]).falla_segments == "first"
    assert parser.parse_args(["--falla-segments", "all"]).falla_segments == "all"
    assert parser.parse_args(["--falla-segments", "split"]).falla_segments == "split"
    with pytest.raises(SystemExit):
        parser.parse_args(["--falla-segments", "some"])


def test_first_mode_is_default_and_unsplit(tmp_path: Path) -> None:
    default, out_d = _convert(tmp_path, None)
    first, out_f = _convert(tmp_path, "first")
    assert default["system"]["demand_array"] == first["system"]["demand_array"]
    assert first["system"]["demand_array"] == [
        {"uid": 1, "name": "Bus1", "bus": 1, "lmax": "lmax", "fcost": 300.0},
    ]
    pd.testing.assert_frame_equal(_lmax(out_d), _lmax(out_f))
    assert _lmax(out_f)[1].tolist() == [80.0, 100.0]


def test_all_mode_emits_native_curve(tmp_path: Path) -> None:
    case, out = _convert(tmp_path, "all")
    _, out_f = _convert(tmp_path, "first")
    assert case["system"]["demand_array"] == [
        {
            "uid": 1,
            "name": "Bus1",
            "bus": 1,
            "lmax": "lmax",
            "fail_segments": [
                {"fraction": pytest.approx(0.1), "cost": 300.0},
                {"fraction": pytest.approx(0.3), "cost": 500.0},
                {"cost": 1000.0},
            ],
        },
    ]
    # Same lmax as ``first`` (no sibling split).
    pd.testing.assert_frame_equal(_lmax(out), _lmax(out_f))
    assert not (out / "Demand" / "fail_cost_seg1.parquet").exists()


def test_apply_fail_segments_writes_tier_schedule(tmp_path: Path) -> None:
    f1, f2 = _falla(2, "FA", 5, 300.0), _falla(3, "FB", 5, 500.0)
    tiers = {5: [FallaTier(1, f1, 0.4), FallaTier(2, f2, 0.6)]}
    dw = DemandWriter(None, None, {"output_dir": tmp_path})
    dems: list[dict[str, Any]] = [
        {"uid": 5, "name": "D", "bus": 5, "fcost": 300.0},
        {"uid": 6, "name": "E", "bus": 6, "fcost": 100.0},
    ]
    dw.apply_fail_segments(dems, tiers, _Costs({"FB": ([2], [700.0])}), _Stages())
    assert dems[0]["fail_segments"] == [
        {"fraction": 0.4, "cost": 300.0},
        {"cost": "fail_cost_seg2"},
    ]
    assert "fcost" not in dems[0]
    assert dems[1] == {"uid": 6, "name": "E", "bus": 6, "fcost": 100.0}
    df = pd.read_parquet(tmp_path / "Demand" / "fail_cost_seg2.parquet")
    assert df["stage"].tolist() == [1, 2]
    assert not (tmp_path / "Demand" / "fail_cost_seg1.parquet").exists()


def test_split_mode_splits_into_tiers(tmp_path: Path) -> None:
    case, out = _convert(tmp_path, "split")
    dems = case["system"]["demand_array"]
    assert [(d["uid"], d["name"], d["fcost"]) for d in dems] == [
        (1, "Bus1_falla1", 300.0),
        (11, "Bus1_falla2", 500.0),
        (21, "Bus1_falla3", 1000.0),
    ]
    assert all(d["bus"] == 1 and d["lmax"] == "lmax" for d in dems)
    lmax = _lmax(out)
    assert lmax[1].tolist() == pytest.approx([8.0, 10.0])
    assert lmax[11].tolist() == pytest.approx([24.0, 30.0])
    assert lmax[21].tolist() == pytest.approx([48.0, 60.0])
    # Siblings reproduce the original demand exactly.
    assert lmax.sum(axis=1).tolist() == pytest.approx([80.0, 100.0], abs=1e-12)
    # Falla centrals stay out of the generator array in both modes.
    assert all(
        not g["name"].startswith("FALLA") for g in case["system"]["generator_array"]
    )


# ---------------------------------------------------------------------------
# End-to-end solve
# ---------------------------------------------------------------------------


def _solve(gtopt_bin: str, out: Path, stem: str) -> tuple[float, pd.DataFrame]:
    res = subprocess.run(
        ["bash", "-c", f'ulimit -c unlimited; exec "{gtopt_bin}" {stem}.json'],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    sol = pd.read_csv(out / "results" / "solution.csv")
    fail_path = out / "results" / "Demand" / "fail_sol.parquet"
    fail = pd.read_parquet(fail_path)
    # Zero columns may be omitted by the writer — reindex to every uid.
    uids = [int(u) for u in pd.read_parquet(out / "Demand" / "lmax.parquet")["uid"]]
    wide = fail.pivot_table(index="block", columns="uid", values="value")
    return float(sol["obj_value"].iloc[0]), wide.reindex(
        columns=sorted(set(uids)), fill_value=0.0
    ).fillna(0.0)


@pytest.mark.integration
def test_e2e_all_vs_first(tmp_path: Path, gtopt_bin: str) -> None:
    """Shedding fills the cheapest tiers first; cost reflects the curve."""
    _, out_f = _convert(tmp_path, "first")
    _, out_a = _convert(tmp_path, "split")
    obj_f, fail_f = _solve(gtopt_bin, out_f, "case_first")
    obj_a, fail_a = _solve(gtopt_bin, out_a, "case_split")

    # first: 20 / 40 MW shed at 300 $/MWh, 4 h blocks.
    assert fail_f[1].tolist() == pytest.approx([20.0, 40.0], abs=1e-6)
    assert obj_f == pytest.approx(4 * (2 * 3000.0 + 60 * 300.0), rel=1e-6)
    # all: tier 1 full (8 / 10), rest in tier 2, tier 3 untouched.
    assert fail_a[1].tolist() == pytest.approx([8.0, 10.0], abs=1e-6)
    assert fail_a[11].tolist() == pytest.approx([12.0, 30.0], abs=1e-6)
    assert fail_a[21].tolist() == pytest.approx([0.0, 0.0], abs=1e-6)
    expected = 4 * (2 * 3000.0 + 18 * 300.0 + 42 * 500.0)
    assert obj_a == pytest.approx(expected, rel=1e-6)


@pytest.mark.integration
def test_e2e_native_all_matches_split(tmp_path: Path, gtopt_bin: str) -> None:
    """Native curve: same objective as the sibling split, per-tier output."""
    _, out_s = _convert(tmp_path, "split")
    _, out_a = _convert(tmp_path, "all")
    obj_s, _ = _solve(gtopt_bin, out_s, "case_split")
    obj_a, fail_a = _solve(gtopt_bin, out_a, "case_all")
    assert obj_a == pytest.approx(obj_s, rel=1e-6)
    assert fail_a[1].tolist() == pytest.approx([20.0, 40.0], abs=1e-6)
    seg = {
        k: pd.read_parquet(
            out_a / "results" / "Demand" / f"fail_seg{k}_sol.parquet"
        ).pivot_table(index="block", columns="uid", values="value")
        for k in (1, 2)
    }
    assert seg[1][1].tolist() == pytest.approx([8.0, 10.0], abs=1e-6)
    assert seg[2][1].tolist() == pytest.approx([12.0, 30.0], abs=1e-6)
