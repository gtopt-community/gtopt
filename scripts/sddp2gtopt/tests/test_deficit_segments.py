"""``sddp2gtopt --deficit-segments {first,all}`` (sddp.dat deficit curve).

``first`` (default) keeps one demand per bus priced by
``demand_fail_cost`` = first segment cost; ``all`` splits each demand
into ``d_<bus>_deficit<k>`` siblings (``lmax`` = segment depth x demand,
the last segment taking the rest; ``fcost`` = segment cost).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from sddp2gtopt.main import make_parser
from sddp2gtopt.sddp2gtopt import convert_sddp_case
from sddp2gtopt.sddp_case import load_sddp_block_case
from sddp2gtopt.sddp_case_writer import build_sddp_planning, deficit_tiers
from sddp2gtopt.sddp_dat_parsers import SddpControl

CASE = Path(__file__).parent / "data" / "sddp_block_case"
_SINGLE = "  100.    5000.     0.       0."
# Guatemala-style two-segment curve: 2 % @ 314.95, 98 % @ 1968.44 $/MWh.
_TWO = "     2   314.95     98  1968.44"


def _case_with_curve(tmp_path: Path, curve: str, *, demand_scale: float = 1.0) -> Path:
    """Copy the block fixture with *curve* as the Costo Deficit row."""
    case = tmp_path / "sddp_case"
    shutil.copytree(CASE, case)
    ctl = case / "sddp.dat"
    text = ctl.read_text(encoding="latin-1")
    assert _SINGLE in text
    ctl.write_text(text.replace(_SINGLE, curve), encoding="latin-1")
    if demand_scale != 1.0:
        deme = case / "deme02tt.dat"
        lines = deme.read_text(encoding="latin-1").splitlines()
        out = [
            re.sub(
                r" *\d+\.\d+",
                lambda m: f"{float(m.group(0)) * demand_scale:{len(m.group(0))}.3f}",
                ln,
            )
            if re.match(r"^\d{4} ", ln)
            else ln
            for ln in lines
        ]
        deme.write_text("\n".join(out) + "\n", encoding="latin-1")
    return case


def _convert(case: Path, parent: Path, mode: str | None) -> dict[str, Any]:
    """Convert *case* into ``<parent>/tiny`` and return the planning."""
    out = parent / "tiny"
    opts: dict[str, Any] = {
        "input_dir": case,
        "output_dir": out,
        "name": "tiny",
        "method": "monolithic",
    }
    if mode is not None:
        opts["deficit_segments"] = mode
    assert convert_sddp_case(opts) == 0
    planning: dict[str, Any] = json.loads(
        (out / "tiny.json").read_text(encoding="utf-8")
    )
    return planning


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ── deficit_tiers ────────────────────────────────────────────────────────────


def test_deficit_tiers_modes() -> None:
    ctl = SddpControl(
        deficit_cost=314.95, deficit_segments=[(2.0, 314.95), (98.0, 1968.44)]
    )
    assert deficit_tiers(ctl, "first") is None
    tiers = deficit_tiers(ctl, "all")
    assert tiers is not None
    assert [c for _, c in tiers] == [314.95, 1968.44]
    assert [f for f, _ in tiers] == pytest.approx([0.02, 0.98])
    # A single segment has nothing to split.
    assert deficit_tiers(SddpControl(deficit_segments=[(100.0, 500.0)]), "all") is None


def test_deficit_tiers_partial_depths_warn(caplog: pytest.LogCaptureFixture) -> None:
    ctl = SddpControl(deficit_segments=[(5.0, 100.0), (10.0, 200.0), (50.0, 900.0)])
    with caplog.at_level(logging.WARNING):
        tiers = deficit_tiers(ctl, "all")
    assert tiers is not None
    assert [f for f, _ in tiers] == pytest.approx([0.05, 0.10, 0.85])
    assert "sum to 65" in caplog.text


def test_deficit_tiers_bad_depths_fall_back(caplog: pytest.LogCaptureFixture) -> None:
    ctl = SddpControl(deficit_segments=[(60.0, 100.0), (40.0, 200.0), (5.0, 900.0)])
    with caplog.at_level(logging.WARNING):
        assert deficit_tiers(ctl, "all") is None
    assert "already cover 100" in caplog.text


# ── first: byte-identical ────────────────────────────────────────────────────


def test_first_is_default_and_byte_identical(tmp_path: Path) -> None:
    case = _case_with_curve(tmp_path, _TWO)
    _convert(case, tmp_path / "implicit", None)
    _convert(case, tmp_path / "explicit", "first")
    assert _tree_bytes(tmp_path / "implicit") == _tree_bytes(tmp_path / "explicit")


def test_all_on_single_segment_is_byte_identical(tmp_path: Path) -> None:
    _convert(CASE, tmp_path / "first", "first")
    _convert(CASE, tmp_path / "all", "all")
    assert _tree_bytes(tmp_path / "first") == _tree_bytes(tmp_path / "all")


def test_first_keeps_single_demand_and_simplified_note(tmp_path: Path) -> None:
    case = _case_with_curve(tmp_path, _TWO)
    planning = _convert(case, tmp_path / "out", "first")
    demands = planning["system"]["demand_array"]
    assert [d["name"] for d in demands] == ["d_CENTRO220", "d_SUR_220"]
    assert all("fcost" not in d for d in demands)
    assert planning["options"]["model_options"]["demand_fail_cost"] == pytest.approx(
        314.95
    )
    notes = load_sddp_block_case(case).skipped
    assert any(n.startswith("deficit cost curve") for n in notes)
    for mode in ("all", "split"):
        notes = load_sddp_block_case(case, deficit_segments=mode).skipped
        assert not any(n.startswith("deficit cost curve") for n in notes)


# ── all: native fail_segments curve ──────────────────────────────────────────


def test_all_emits_native_curve(tmp_path: Path) -> None:
    case = _case_with_curve(tmp_path, _TWO)
    base = _convert(case, tmp_path / "first", "first")
    planning = _convert(case, tmp_path / "all", "all")
    # Same demands as ``first`` (one per bus, no per-demand fcost) ...
    assert planning["system"] == base["system"]
    mo = planning["options"]["model_options"]
    # ... plus the study-wide curve, last segment open.
    assert mo["demand_fail_segments"] == [
        {"fraction": pytest.approx(0.02), "cost": pytest.approx(314.95)},
        {"cost": pytest.approx(1968.44)},
    ]
    assert mo["demand_fail_cost"] == pytest.approx(314.95)
    assert "demand_fail_segments" not in base["options"]["model_options"]


# ── split: per-segment siblings ──────────────────────────────────────────────


def test_split_splits_each_demand(tmp_path: Path) -> None:
    case = _case_with_curve(tmp_path, _TWO)
    base = _convert(case, tmp_path / "first", "first")
    planning = _convert(case, tmp_path / "all", "split")
    demands = planning["system"]["demand_array"]
    assert [(d["uid"], d["name"], d["bus"], d["fcost"]) for d in demands] == [
        (1, "d_CENTRO220_deficit1", "CENTRO220", 314.95),
        (11, "d_CENTRO220_deficit2", "CENTRO220", 1968.44),
        (2, "d_SUR_220_deficit1", "SUR_220", 314.95),
        (12, "d_SUR_220_deficit2", "SUR_220", 1968.44),
    ]
    # Global fallback (and thermal soft-floor penalty) stay at segment 1.
    assert planning["options"]["model_options"]["demand_fail_cost"] == pytest.approx(
        314.95
    )
    # Everything but the demands is unchanged.
    for key in ("generator_array", "line_array", "bus_array"):
        assert planning["system"][key] == base["system"][key]

    orig = pd.read_parquet(tmp_path / "first" / "tiny" / "Demand" / "lmax.parquet")
    tier = pd.read_parquet(tmp_path / "all" / "tiny" / "Demand" / "lmax.parquet")
    keys = ["stage", "block"]
    for duid in (1, 2):
        o = orig[orig["uid"] == duid].set_index(keys)["value"]
        t1 = tier[tier["uid"] == duid].set_index(keys)["value"]
        t2 = tier[tier["uid"] == duid + 10].set_index(keys)["value"]
        assert (t1 / o).to_numpy() == pytest.approx(0.02)
        assert (t2 / o).to_numpy() == pytest.approx(0.98)
        assert (t1 + t2).to_numpy() == pytest.approx(o.to_numpy())


def test_split_constant_lmax_stays_scalar() -> None:
    case = load_sddp_block_case(CASE)
    case.control.deficit_segments = [(10.0, 100.0), (90.0, 900.0)]
    bus = next(iter(case.demands))
    case.demands = {bus: [[50.0, 50.0] for _ in case.stages]}
    planning, sink = build_sddp_planning(case, name="x", deficit_segments="split")
    demands = planning["system"]["demand_array"]
    assert [d["lmax"] for d in demands] == pytest.approx([5.0, 45.0])
    assert ("Demand", "lmax") not in sink.rows


def test_non_block_front_end_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, dat_case_dir: Path
) -> None:
    with caplog.at_level(logging.WARNING):
        convert_sddp_case(
            {
                "input_dir": dat_case_dir,
                "output_dir": tmp_path / "out",
                "deficit_segments": "all",
            }
        )
    assert "only applies to SDDP block studies" in caplog.text


# ── CLI ──────────────────────────────────────────────────────────────────────


def test_cli_flag() -> None:
    parser = make_parser()
    assert parser.parse_args(["x"]).deficit_segments == "first"
    for mode in ("all", "split"):
        assert (
            parser.parse_args(["x", "--deficit-segments", mode]).deficit_segments
            == mode
        )
    with pytest.raises(SystemExit):
        parser.parse_args(["x", "--deficit-segments", "bogus"])
    help_text = parser.format_help()
    assert "--deficit-segments" in help_text
    assert "_deficit<k>" in help_text


# ── e2e with gtopt ───────────────────────────────────────────────────────────


@pytest.mark.integration
def test_split_e2e_cheaper_segment_curtailed_first(
    tmp_path: Path, gtopt_bin: str
) -> None:
    """Scarce supply: segment 1 is shed in full before segment 2."""
    case = _case_with_curve(tmp_path, _TWO, demand_scale=10.0)
    _convert(case, tmp_path / "all", "split")
    out = tmp_path / "all" / "tiny"
    res = subprocess.run(
        [gtopt_bin, "tiny.json"],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    sol = pd.read_csv(out / "results" / "solution.csv")
    assert (sol["status"] == 0).all()

    fail = pd.read_parquet(out / "results" / "Demand" / "fail_sol.parquet")
    lmax = pd.read_parquet(out / "Demand" / "lmax.parquet")
    keys = ["stage", "block", "uid"]
    merged = fail.merge(lmax, on=keys, suffixes=("_fail", "_lmax"))
    seg1 = merged[merged["uid"] < 10]
    seg2 = merged[merged["uid"] > 10]
    assert not seg2.empty and (seg2["value_fail"] > 1e-6).all()
    # Every block that sheds segment 2 sheds all of segment 1.
    assert seg1["value_fail"].to_numpy() == pytest.approx(
        seg1["value_lmax"].to_numpy(), rel=1e-6
    )


@pytest.mark.integration
def test_all_e2e_native_curve_curtails_cheaper_first(
    tmp_path: Path, gtopt_bin: str
) -> None:
    """Native curve: fail_seg1 is full (2 % of lmax) wherever seg2 sheds."""
    case = _case_with_curve(tmp_path, _TWO, demand_scale=10.0)
    _convert(case, tmp_path / "all", "all")
    out = tmp_path / "all" / "tiny"
    res = subprocess.run(
        [gtopt_bin, "tiny.json"],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    sol = pd.read_csv(out / "results" / "solution.csv")
    assert (sol["status"] == 0).all()
    keys = ["stage", "block", "uid"]
    seg1 = pd.read_parquet(out / "results" / "Demand" / "fail_seg1_sol.parquet")
    seg2 = pd.read_parquet(out / "results" / "Demand" / "fail_seg2_sol.parquet")
    total = pd.read_parquet(out / "results" / "Demand" / "fail_sol.parquet")
    lmax = pd.read_parquet(out / "Demand" / "lmax.parquet")
    skeys = [k for k in ("scenario", *keys) if k in total.columns]
    m = (
        seg2[seg2["value"] > 1e-6]
        .merge(seg1, on=skeys, suffixes=("_s2", "_s1"))
        .merge(lmax.rename(columns={"value": "lmax"}), on=keys)
    )
    assert not m.empty
    assert m["value_s1"].to_numpy() == pytest.approx(
        0.02 * m["lmax"].to_numpy(), rel=1e-6
    )
    both = seg1.merge(seg2, on=skeys, how="outer", suffixes=("_s1", "_s2")).fillna(
        {"value_s1": 0.0, "value_s2": 0.0}
    )
    both = both.merge(total, on=skeys)
    assert (both["value_s1"] + both["value_s2"]).to_numpy() == pytest.approx(
        both["value"].to_numpy(), rel=1e-6, abs=1e-6
    )
