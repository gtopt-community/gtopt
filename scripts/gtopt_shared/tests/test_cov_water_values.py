# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for :class:`gtopt_shared.water_values.WaterValueResolver`."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gtopt_shared.water_values import WaterValueResolver, _cenre_efficiency_at


def _resolver(centrals, *, cenre=None, options=None) -> WaterValueResolver:
    return WaterValueResolver(
        central_parser=SimpleNamespace(centrals=centrals),
        cenre_parser=None if cenre is None else SimpleNamespace(efficiencies=cenre),
        options=options,
    )


def test_cenre_efficiency_empty_and_max():
    assert _cenre_efficiency_at([], 1.0) is None
    segs = [
        {"constant": 2.0, "slope": 0.0, "volume": 0.0},
        {"constant": 1.0, "slope": 0.0, "volume": 0.0},
    ]
    assert _cenre_efficiency_at(segs, 5.0) == 2.0


def test_anchor_invalid_explicit_and_bad_gcosts():
    centrals = [
        {"type": "termica", "gcost": "bad"},
        {"type": "termica", "gcost": 0.0},
        {"type": "termica", "gcost": 100.0},
        {"type": "falla", "gcost": 0.0},
        {"type": "falla", "gcost": 400.0},
        {"type": "pasada", "gcost": 5.0},
    ]
    r = _resolver(centrals, options={"water_fail_cost": "not-a-number"})
    assert r.anchor == pytest.approx(0.75 * 100.0 + 0.25 * 400.0)
    assert r.is_active


def test_max_rendi_cenre_paths():
    centrals = [
        {"name": "A", "number": 1, "efficiency": 1.0, "emax": 10.0},
        {"name": "B", "number": 2, "efficiency": 3.0, "emax": 10.0},
        {"name": "C", "number": 3, "efficiency": 1.0, "emax": 0.0},
    ]
    cenre = [
        {"name": "A", "segments": [{"constant": 2.0, "slope": 0.0, "volume": 0.0}]},
        {"name": "B", "segments": []},
        {"name": "C", "segments": [{"constant": 9.0}]},
        {"name": 5},
    ]
    r = _resolver(centrals, cenre=cenre)
    assert r.max_rendi("A") == 2.0
    assert r.max_rendi("A") == 2.0  # cached
    assert r.max_rendi("B") == 3.0  # empty curve → static
    assert r.max_rendi("C") == 1.0  # vmax 0 → curve skipped
    assert r.max_rendi("missing") == 0.0


def test_cascade_lost_pf_edge_cases():
    centrals = [
        {"name": "E1", "number": 1, "type": "embalse", "bus": "x", "ser_hid": 2},
        {
            "name": "S2",
            "number": 2,
            "type": "serie",
            "bus": 1,
            "ser_hid": "bad",
            "efficiency": 2.0,
        },
        {
            "name": "E3",
            "number": 3,
            "type": "embalse",
            "bus": 1,
            "ser_hid": 4,
            "efficiency": 1.0,
        },
        {"name": None, "number": 4, "type": "serie", "bus": 1, "ser_hid": 9},
        {
            "name": "E5",
            "number": 5,
            "type": "embalse",
            "bus": 1,
            "ser_hid": 6,
            "efficiency": 1.0,
        },
        {"name": "E6", "number": 6, "type": "embalse", "bus": 1, "ser_hid": 0},
    ]
    r = _resolver(centrals)
    assert r.cascade_lost_pf(None) == 0.0
    assert r.cascade_lost_pf("x") == 0.0
    # E1 bus invalid → 0; S2 contributes 2; ser_hid invalid stops.
    assert r.cascade_lost_pf(1) == pytest.approx(2.0)
    # E3 contributes 1; unnamed serie contributes nothing; 9 unknown → stop.
    assert r.cascade_lost_pf(3) == pytest.approx(1.0)
    # E5 contributes 1 then stops at the next embalse E6.
    assert r.cascade_lost_pf(5) == pytest.approx(1.0)


def test_junction_lost_pf_edge_cases():
    centrals = [
        {"name": "A", "number": 1, "bus": "x"},
        {"name": "B", "number": 2, "bus": 0},
        {"name": None, "number": 3, "bus": 1},
        {"name": "D", "number": 4, "bus": 1, "efficiency": 2.5},
    ]
    r = _resolver(centrals)
    assert r.junction_lost_pf(None) == 0.0
    assert r.junction_lost_pf("x") == 0.0
    assert r.junction_lost_pf(99) == 0.0
    assert r.junction_lost_pf(1) == 0.0
    assert r.junction_lost_pf(2) == 0.0
    assert r.junction_lost_pf(3) == 0.0
    assert r.junction_lost_pf(4) == 2.5


def test_anchor_zero_without_falla_and_cascade_cache():
    r = _resolver([{"type": "termica", "gcost": 10.0}])
    assert r.anchor == 0.0
    r2 = _resolver(
        [{"name": "S", "number": 1, "type": "serie", "bus": 1, "efficiency": 1.5}]
    )
    assert r2.cascade_lost_pf(1) == 1.5
    assert r2.cascade_lost_pf(1) == 1.5  # served from the cache
