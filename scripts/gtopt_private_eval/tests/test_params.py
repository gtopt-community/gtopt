# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Parameter loading and precedence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtopt_private_eval.defaults import DEFAULTS
from gtopt_private_eval.params import Params, deep_merge, load_config, set_path


def test_every_market_default_has_sources() -> None:
    srcs = DEFAULTS["sources"]
    for key, src in srcs.items():
        assert src["url"].startswith("http"), key
        assert src["date"], key
    assert set(DEFAULTS["markets"]) == {
        "energy_only",
        "capacity_market",
        "reliability_option",
        "cfd",
        "cl",
    }


def test_deep_merge_does_not_alias() -> None:
    base = {"a": {"b": 1, "c": [1]}, "d": 1}
    out = deep_merge(base, {"a": {"b": 2}, "e": {"f": 3}})
    assert out == {"a": {"b": 2, "c": [1]}, "d": 1, "e": {"f": 3}}
    out["a"]["c"].append(2)
    assert base["a"]["c"] == [1]


def test_load_config_json_yaml(tmp_path: Path) -> None:
    assert load_config(None)["finance"]["fom_pct_capex"] == 0.02
    j = tmp_path / "c.json"
    j.write_text(json.dumps({"finance": {"wacc": 0.1}}), encoding="utf-8")
    cfg = load_config(j)
    assert cfg["finance"]["wacc"] == 0.1
    assert cfg["finance"]["fom_pct_capex"] == 0.02
    pytest.importorskip("yaml")
    y = tmp_path / "c.yaml"
    y.write_text("co2:\n  price: 25\n", encoding="utf-8")
    assert load_config(y)["co2"]["price"] == 25
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(bad)


def test_set_path() -> None:
    cfg: dict = {"a": {"b": 1}}
    set_path(cfg, "a.b", "2.5")
    set_path(cfg, "x.y", "moderate")
    set_path(cfg, "x.z", 3)
    assert cfg == {"a": {"b": 2.5}, "x": {"y": "moderate", "z": 3}}
    with pytest.raises(ValueError):
        set_path(cfg, "a.b.c", "1")


def test_technology_and_value_precedence() -> None:
    cfg = deep_merge(
        DEFAULTS,
        {
            "projects": {"p1": {"technology": "wind", "life": 7}},
            "technologies": {"wind": {"wacc": 0.07}},
        },
    )
    prm = Params(cfg)
    assert prm.technology("p1", "Generator") == "wind"
    assert prm.technology("x", "Battery") == "bess"
    assert prm.technology("x", "Line") == "line"
    assert prm.technology("Solar_North", "Generator") == "solar_pv"
    assert prm.technology("peaker", "Generator") == "ocgt"
    assert prm.technology("unit7", "Generator") == "thermal"
    assert prm.value("p1", "wind", "life") == 7
    assert prm.value("p2", "wind", "wacc") == 0.07
    assert prm.value("p2", "thermal", "fom_pct_capex") == 0.02
    assert prm.value("p2", "thermal", "nothing", 9) == 9
    assert prm.market("cl")["capacity_price_usd_kw_month"] > 0
    assert prm.co2["price"] == 0.0
    assert "cne_r462" in prm.sources()
