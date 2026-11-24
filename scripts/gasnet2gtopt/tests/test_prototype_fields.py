# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Description fields of the Norte Grande / Peru studies: source volume
and profile rate cap, transfer arcs, open line pack, line-pack p_ref,
user constraints and profile-valued generator limits."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from gasnet2gtopt import common as cm
from gasnet2gtopt.legacy import build_legacy
from gasnet2gtopt.native import build_native

from .conftest import load_case


def _desc() -> dict[str, Any]:
    desc = copy.deepcopy(load_case("tiny_linepack"))
    gas = desc["gas_network"]
    desc["profiles"]["lng_cap"] = [100.0, 50.0, 80.0, 100.0]
    src = gas["gas_source_array"][0]
    src["fmax"] = "lng_cap"
    src["volume"] = 250.0
    gas["linepack_cyclic"] = False
    gas["gas_pipeline_array"][0]["linepack_p_ref"] = 52.0
    gas["gas_transfer_array"] = [
        {"name": "TR", "from_node": "S", "to_node": "T", "fmax": 25.0}
    ]
    desc["user_constraints"] = [{"name": "uc1", "expression": "1 <= 2"}]
    gens = desc["power_system"]["generator_array"]
    gens[0]["pmin"] = "lng_cap"
    gens[0]["capacity"] = "lng_cap"
    return desc


def test_source_rate_forms() -> None:
    opts = cm.BuildOptions(nblocks=2)
    prof = {"cap": [3.0, 5.0]}
    assert cm.source_rate({"fmax": 7}, prof, opts) == (7.0, None)
    assert cm.source_rate({"fmax": {"winter": 2, "summer": 1}}, prof, opts) == (
        2.0,
        None,
    )
    assert cm.source_rate({"fmax": "cap"}, prof, opts) == (5.0, [3.0, 5.0])


def test_native_maps_the_study_fields() -> None:
    opts = cm.BuildOptions(linepack=True, nblocks=4)
    planning, _ = build_native(_desc(), opts)
    sys_ = planning["system"]
    sup = sys_["gas_supply_array"][0]
    assert sup["fmax"] == [[100.0, 50.0, 80.0, 100.0]]
    assert sup["volume"] == 250.0
    assert planning["options"]["gas_options"]["linepack_cyclic"] is False
    pipe, arc = sys_["gas_pipe_array"]
    assert pipe["linepack_pressure_ref"] == 52.0
    assert arc == {
        "uid": 2,
        "name": "TR",
        "gas_node_a": "S",
        "gas_node_b": "T",
        "fmax": 25.0,
        "weymouth_mode": "none",
    }
    assert sys_["user_constraint_array"] == [
        {"uid": 1, "name": "uc1", "expression": "1 <= 2"}
    ]
    gen = sys_["generator_array"][0]
    assert gen["pmin"] == [[100.0, 50.0, 80.0, 100.0]]
    assert gen["capacity"] == 100.0


def test_native_rejects_a_transfer_cost() -> None:
    desc = _desc()
    desc["gas_network"]["gas_transfer_array"][0]["cost"] = 1.0
    with pytest.raises(NotImplementedError, match="transfer cost"):
        build_native(desc, cm.BuildOptions())


def test_legacy_maps_the_study_fields() -> None:
    opts = cm.BuildOptions(linepack=True, nblocks=4)
    planning, meta = build_legacy(_desc(), opts)
    sys_ = planning["system"]
    dvs = {d["name"]: d for d in sys_["decision_variable_array"]}
    ucs = {u["name"]: u for u in sys_["user_constraint_array"]}
    assert dvs["src_SUPPLY"]["upper_bound"] == 100.0
    assert ucs["src_cap_SUPPLY"]["rhs"] == [[100.0, 50.0, 80.0, 100.0]]
    assert dvs["vol_SUPPLY"]["upper_bound"] == 250.0
    assert "vol_bal_SUPPLY" in ucs
    assert dvs["tr_TR"]["upper_bound"] == 25.0
    assert "linepack_end_S_T" not in ucs  # linepack_cyclic false
    assert meta["pipes"]["S_T"]["p_ref"] == 52.0
    assert ucs["uc1"]["expression"] == "1 <= 2"


def test_legacy_profile_pmax_uses_capacity_for_the_gate() -> None:
    desc = copy.deepcopy(load_case("tiny_plant"))
    desc["profiles"]["avail"] = [0.5] * 24
    gen = next(
        g
        for g in desc["power_system"]["generator_array"]
        if g["name"] == desc["gas_network"]["generator_gas_link_array"][0]["generator"]
    )
    gen["pmax"] = "avail"
    planning, meta = build_legacy(desc, cm.BuildOptions())
    assert planning["system"]["user_constraint_array"]
    assert meta["plants"][gen["name"]]["rows"]
