# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for opendss2gtopt.expansion (VAD and hosting studies)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opendss2gtopt import main as main_mod
from opendss2gtopt.convert import build_planning
from opendss2gtopt.dss_parser import DssParser, parse_dss
from opendss2gtopt.expansion import (
    add_hosting_candidates,
    add_vad_candidates,
    crf,
    item,
    load_vad_params,
)
from opendss2gtopt.feeders import synthetic_feeder_dss
from opendss2gtopt.network import build_network

DATA = Path(__file__).parent / "data"


def _tiny():
    net = build_network(parse_dss(DATA / "tiny_feeder.dss"))
    return net, build_planning(net, network_model="lindistflow", time_mode="daily")


def test_params_and_crf() -> None:
    p = load_vad_params()
    assert p["annuity_rate"] == pytest.approx(0.06)
    assert item(p, "cfld_sen_0_5pct") == pytest.approx(795.11)
    with pytest.raises(KeyError):
        item(p, "nope")
    assert crf(0.06, 40) == pytest.approx(float(p["crf_at_6pct"]["40"]), abs=1e-5)
    assert crf(0.0, 20) == pytest.approx(0.05)
    for it in p["items"]:
        assert it["source_url"].startswith("https://")


@pytest.mark.parametrize("integer", [False, True])
def test_vad_candidates(integer: bool) -> None:
    net, planning = _tiny()
    lmax0 = planning["system"]["demand_array"][0]["lmax"][0][5]
    add_vad_candidates(planning, net, growth=2.0, integer=integer)
    s = planning["system"]
    assert s["name"].endswith("_vad")
    assert s["demand_array"][0]["lmax"][0][5] == pytest.approx(2 * lmax0)
    assert all(b["vmin_pu"] == pytest.approx(0.94) for b in s["bus_array"][1:])
    cands = [x for x in s["line_array"] if x.get("type") == "candidate"]
    assert cands and all(c["capacity"] == 0 and "wacc" not in c for c in cands)
    # one social rate: the regulated VAD rate drives annuities and discounting
    assert planning["simulation"]["annual_discount_rate"] == pytest.approx(0.06)
    assert "annual_discount_rate" not in planning["options"]
    assert all("wacc" not in b for b in s["battery_array"])
    assert all(c["economic_life"] == 40 for c in cands)
    assert all(c.get("integer_expmod", False) is integer for c in cands)
    xfs = [x for x in s["line_array"] if x.get("type") == "transformer"]
    assert xfs and all(x.get("expmod") == 2 for x in xfs if "tmax_ab" not in x)
    sub = next(x for x in s["line_array"] if x.get("expmod") == 2)
    assert "tmax_ab" not in sub and sub["economic_life"] == 30
    bess = s["battery_array"][-1]
    assert bess["name"] == "bess_nwa" and bess["capacity"] == 0
    assert bess["overnight_capcost"] == pytest.approx(400_000.0)
    assert planning["options"]["model_options"]["demand_fail_cost"] == pytest.approx(
        795.11
    )


def test_hosting_candidates() -> None:
    _, planning = _tiny()
    add_hosting_candidates(planning, buses=["c3", "b5"], integer=True)
    gens = [g for g in planning["system"]["generator_array"] if "host" in g["name"]]
    assert [g["bus"] for g in gens] == ["c3", "b5"]
    assert all(g["integer_expmod"] for g in gens)
    prof = planning["system"]["generator_profile_array"][-1]["profile"][0][0]
    assert len(prof) == 24 and max(prof) == pytest.approx(1.0, abs=0.01)
    assert prof[0] == 0.0
    snap = build_planning(_tiny()[0], network_model="lindistflow")
    add_hosting_candidates(snap)
    assert snap["system"]["generator_profile_array"][-1]["profile"] == [[[1.0]]]


def test_synthetic_feeder() -> None:
    text = synthetic_feeder_dss(200, seed=3)
    net = build_network(DssParser().parse_text(text))
    assert len(net.buses) == 200
    assert len(net.branches) == 199
    assert sum(x.p_mw for x in net.loads) == pytest.approx(6.0)
    assert synthetic_feeder_dss(200, seed=3) == text


@pytest.mark.parametrize("study", ["vad", "hosting"])
def test_cli_study(study: str, tmp_path: Path) -> None:
    out = tmp_path / study
    argv = [str(DATA / "tiny_feeder.dss"), "-o", str(out), "--study", study, "--mip"]
    if study == "hosting":
        argv += ["--hosting-buses", "c3"]
    with pytest.raises(SystemExit) as exc:
        main_mod.main(argv)
    assert exc.value.code in (0, None)
    data = json.loads((out / "tiny.json").read_text())
    assert data["options"]["model_options"]["network_model"] == "lindistflow"
    assert len(data["simulation"]["block_array"]) == 24
