# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Inventory, equipment libraries and the Pmax-reproduction invariant."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from re2gtopt import _equipment as eq
from re2gtopt import _plants as pl


def _row(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "W1",
        "type": "wind",
        "lat": -31.5,
        "lon": -71.5,
        "pmax_mw": 90.0,
    }
    base.update(kw)
    return base


def test_inventory_csv_json_roundtrip(tmp_path: Path) -> None:
    f = tmp_path / "p.csv"
    f.write_text(
        "name,type,lat,lon,capacity_mw,hub_height,commissioning,wake_loss\n"
        "W1,eolica,-31.5,-71.5,90,,2014,0.05\n"
        "S1,solar,-23,-69,100,,,\n"
    )
    plants = pl.read_inventory(f)
    assert [p.type for p in plants] == ["wind", "pv"]
    assert plants[0].pmax_mw == 90 and plants[0].commissioning == 2014
    assert plants[0].get("wake_loss") == 0.05
    assert plants[0].get("hub_height", 7) == 7
    assert plants[1].uid == 2 and plants[1].generator == "S1"
    out = tmp_path / "o.csv"
    pl.write_inventory(plants, out)
    again = pl.read_inventory(out)
    assert [p.name for p in again] == ["W1", "S1"]
    j = tmp_path / "p.json"
    j.write_text(json.dumps({"plants": [_row(uid=7)]}))
    assert pl.read_inventory(j)[0].uid == 7


@pytest.mark.parametrize(
    "row",
    [
        {"name": "x", "type": "wind", "lat": 1, "lon": 1},
        _row(type="hydro"),
        _row(pmax_mw=0),
        _row(pmax_basis="weird"),
    ],
)
def test_inventory_errors(row: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        pl.plant_from_row(row, 1)


def test_inventory_duplicates(tmp_path: Path) -> None:
    j = tmp_path / "p.json"
    j.write_text(json.dumps([_row(uid=1), _row(uid=1, name="W2")]))
    with pytest.raises(ValueError):
        pl.read_inventory(j)
    j.write_text(json.dumps([_row(uid=1), _row(uid=2)]))
    with pytest.raises(ValueError):
        pl.read_inventory(j)


@pytest.mark.parametrize("pmax", [2.5, 50.0, 90.0, 99.0, 115.0, 151.2, 400.0])
def test_turbine_rating_reproduces_pmax(pmax: float) -> None:
    r = pl.choose_turbine_mw(pmax)
    n = max(1, round(pmax / r))
    assert abs(n * r - pmax) <= 0.5 * r + 1e-9
    if pmax > 4.0:
        assert 4.0 <= r <= 6.0
        # a divisor exists for these within 0.05 MW steps
        assert abs(n * r - pmax) < 0.6


def test_size_wind_generic_and_net_basis() -> None:
    p = pl.plant_from_row(_row(pmax_mw=90.0), 1)
    sz = pl.size_wind(p, "auto", 0.02)
    assert sz.source == "generic"
    assert sz.n_turbines * sz.turbine_mw == pytest.approx(90.0, abs=0.5 * sz.turbine_mw)
    assert sz.installed_ratio == pytest.approx(sz.n_turbines * sz.turbine_mw / 90.0)
    pn = pl.plant_from_row(_row(pmax_mw=90.0, pmax_basis="net"), 1)
    szn = pl.size_wind(pn, "auto", 0.02)
    assert szn.n_turbines * szn.turbine_mw * 0.98 == pytest.approx(90.0, rel=0.05)
    assert sz.hub_height == 120.0
    old = pl.plant_from_row(_row(commissioning=2010), 1)
    assert pl.size_wind(old, "auto", 0.02).hub_height == 95.0


def test_size_wind_cen_mismatch_warns() -> None:
    p = pl.plant_from_row(
        _row(
            pmax_mw=90.0, n_turbines=40, turbine_mw=3.0, turbine="Vestas V112-3.45 MW"
        ),
        1,
    )
    sz = pl.size_wind(p, "auto", 0.02)
    assert sz.source == "cen"
    assert sz.installed_ratio == pytest.approx(120 / 90)
    assert any("disagrees" in w for w in sz.warnings)
    # generic mode ignores the CEN equipment
    g = pl.size_wind(p, "auto", 0.02, equipment_source="generic")
    assert g.source == "generic" and not g.warnings
    q = pl.plant_from_row(_row(turbine="Acme ZZ-9"), 1)
    assert any("OEDB" in w for w in pl.size_wind(q, "auto", 0.02).warnings)
    s = pl.plant_from_row(_row(turbine="iec3"), 1)
    assert pl.size_wind(s, "auto", 0.02).turbine == "iec3"


def test_size_pv() -> None:
    p = pl.plant_from_row(_row(type="pv", pmax_mw=100.0, injection_mw=95.0), 1)
    sz = pl.size_pv(p, 1.3)
    assert sz.source == "generic"
    assert sz.ac_limit == pytest.approx(0.95)
    assert sz.dc_mw == pytest.approx(130.0)
    assert sz.n_modules == round(130e6 / pl.GENERIC_MODULE_WP)
    rep = pl.plant_from_row(
        _row(
            type="pv",
            pmax_mw=100.0,
            module_wp=550,
            n_modules=250000,
            inverter_kw=2500,
            n_inverters=36,
        ),
        1,
    )
    s2 = pl.size_pv(rep, 1.3)
    assert s2.source == "cen"
    assert s2.dc_ac_ratio == pytest.approx(1.375)
    assert s2.ac_limit == pytest.approx(0.9)
    assert any("inverters" in w for w in s2.warnings)
    crazy = pl.plant_from_row(_row(type="pv", dc_ac_ratio=5.0), 1)
    assert pl.size_pv(crazy, 1.3).dc_ac_ratio == 1.3


def test_check_peak() -> None:
    p = pl.plant_from_row(_row(type="pv", pmax_mw=100.0), 1)
    sz = pl.size_pv(p, 1.3)
    assert pl.check_peak(sz, 0.99)["peak_ok"]
    bad = pl.check_peak(sz, 0.8)
    assert not bad["peak_ok"] and bad["modelled_peak_mw"] == 80.0


def test_auto_turbine_table() -> None:
    assert eq.auto_turbine(2020, 9.0) == "oedb:V112/3450"
    assert eq.auto_turbine(None, 7.0) == "oedb:E-141/4200"
    assert eq.auto_turbine(2012, 9.0) == "oedb:V112/3075"
    assert eq.auto_turbine(2012, 6.0) == "oedb:V100/1800"


def test_oedb_fallback_without_windpowerlib(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eq, "_oedb_tables", lambda: None)
    c = eq.oedb_curve("E-141/4200")
    assert c.power.max() == pytest.approx(1.0)
    with pytest.raises(ValueError):
        eq.oedb_curve("Unknown/1")
    assert eq.oedb_types() == []
    assert eq.match_oedb("Vestas V112 3.45 MW") == "V112/3450"
    assert eq.match_oedb("V126/3450") == "V126/3450"
    assert eq.match_oedb("no model") is None


def test_oedb_library() -> None:
    pytest.importorskip("windpowerlib")
    eq._oedb_tables.cache_clear()  # noqa: SLF001
    c = eq.oedb_curve("V112/3450")
    assert c.power.max() == pytest.approx(1.0)
    assert 11.0 < c.rated_speed < 14.0
    assert c(np.array([2.0]))[0] == 0.0
    assert "E-141/4200" in eq.oedb_types()
    with pytest.raises(ValueError):
        eq.oedb_curve("Nope/1")


def test_pv_libraries() -> None:
    pytest.importorskip("pvlib")
    assert eq.match_module("") is None
    assert eq.match_module("abc") is None
    m = eq.match_module("Canadian Solar CS6U-330P")
    if m is not None:
        assert -0.006 < m.gamma_pdc < -0.002
    assert eq.match_module("zzzzzzzz-not-a-module") is None
    assert eq.match_inverter("") is None
    inv = eq.match_inverter("SMA America: SC 2200")
    if inv is not None:
        assert inv[1] > 1e5
    assert eq.match_inverter("qqqqqqqqqq") is None


@pytest.mark.parametrize(
    ("model", "rating", "want"),
    [
        ("Vestas V100 -1.8 MW V100 MK7", None, "V100/1800"),
        ("Vestas V90 2MW MK5 Vestas V90", None, "V90/2000"),
        ("Vestas V90", 2.0, "V90/2000"),
        ("Vestas V90", None, None),  # 2.0 and 3.0 MW variants: ambiguous
        ("V112/3450", None, "V112/3450"),
        ("Nordex", None, None),
    ],
)
def test_match_oedb_cen_names(
    model: str, rating: float | None, want: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        eq,
        "oedb_types",
        lambda: [
            "V90/2000",
            "V90/2000/GS",
            "V90/3000",
            "V100/1800",
            "V100/1800/GS",
            "V112/3450",
        ],
    )
    assert eq.match_oedb(model, rating) == want


def test_unmatched_cen_turbine_uses_specific_power() -> None:
    p = pl.plant_from_row(
        _row(pmax_mw=60.0, turbine="ACCIONA AW-1500/82", turbine_mw=1.5, n_turbines=40),
        1,
    )
    sz = pl.size_wind(p, "auto", 0.02)
    sp = 1.5e6 / (np.pi * 82**2 / 4)
    assert sz.turbine == f"sp{round(sp)}"
    assert any("generic curve" in w for w in sz.warnings)
    assert eq._model_kw("SWT-2.3-101") == 2300  # noqa: SLF001
    assert eq._model_kw("AW-1500/82") == 1500  # noqa: SLF001
    assert eq._model_kw("V112/3450") == 3450  # noqa: SLF001
    assert eq._model_kw("Nordex") is None  # noqa: SLF001


def test_generic_dc_ac_by_year() -> None:
    eq = pl.eq
    assert eq.generic_dc_ac(2012) == 1.05
    assert eq.generic_dc_ac(2015) == 1.05
    assert eq.generic_dc_ac(2017) == 1.2
    assert eq.generic_dc_ac(2023) == 1.3
    assert eq.generic_dc_ac(None) == 1.3
    t = eq.parse_dc_ac_table("2019:1.1, 2010:1.0")
    assert t == ((2010, 1.0), (2019, 1.1))
    assert eq.generic_dc_ac(2030, t) == 1.1  # after the last row
    for bad in ("2015", "2015:5", ""):
        with pytest.raises(ValueError):
            eq.parse_dc_ac_table(bad)
    with pytest.raises(ValueError):
        eq.generic_dc_ac(2015, ())
    old = pl.plant_from_row(_row(type="pv", pmax_mw=100.0, commissioning=2014), 1)
    assert pl.size_pv(old, None).dc_ac_ratio == pytest.approx(1.05)
    mid = pl.plant_from_row(_row(type="pv", pmax_mw=100.0, commissioning=2018), 1)
    assert pl.size_pv(mid, None).dc_mw == pytest.approx(120.0)
    assert pl.size_pv(mid, 1.4).dc_ac_ratio == pytest.approx(1.4)  # fixed
    assert pl.size_pv(mid, None, dc_ac_table=t).dc_ac_ratio == pytest.approx(1.1)
