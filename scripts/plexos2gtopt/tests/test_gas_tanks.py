# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``--gas-bands-as-tanks``: capped gas Fuel bands modelled as tanks."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from plexos2gtopt import _gas_tanks as GT
from plexos2gtopt import parsers as P
from plexos2gtopt.entities import FuelSpec, GeneratorSpec, PlexosCase
from plexos2gtopt.main import make_parser
from plexos2gtopt.tests.cov_parsers_kit import bundle
from plexos2gtopt.tests.test_lng_inflexible_hydro_bess import Model, _tank_bundle


def _gen(name: str, fuel: str, hr: float = 0.25, **kw: Any) -> GeneratorSpec:
    return GeneratorSpec(
        object_id=1,
        name=name,
        bus_name="N",
        pmax=kw.pop("pmax", 100.0),
        heat_rate=hr,
        fuel_names=(fuel,),
        **kw,
    )


def _fuel(name: str, price: float = 200.0, cap: float | None = 4800.0, **kw: Any):
    return FuelSpec(
        object_id=1, name=name, price=price, max_offtake=cap, type_tag="gas", **kw
    )


def test_parse_gas_refill() -> None:
    """instant / ramp:<hours>; malformed specs raise."""
    assert GT.parse_gas_refill(None) == 0
    assert GT.parse_gas_refill("instant") == 0
    assert GT.parse_gas_refill("ramp:6") == 6
    for bad in ("ramp:0", "ramp:x", "slow:3"):
        with pytest.raises(ValueError, match="gas-refill"):
            GT.parse_gas_refill(bad)


def test_fill_forward_defined() -> None:
    """Zeros are undefined: forward fill, leading back-fill."""
    assert GT.fill_forward_defined([0.0, 2.0, 0.0, 3.0]) == [2.0, 2.0, 2.0, 3.0]
    assert GT.fill_forward_defined([0.0, 0.0]) == [0.0, 0.0]


def test_fuel_offtake_periods(tmp_path: Path) -> None:
    """Thursday weeks from the CSV dates, pro-rated, with end hours."""
    b = bundle(
        tmp_path,
        {
            "Fuel_MaxOfftakeWeek.csv": (
                "NAME,YEAR,MONTH,DAY,PERIOD,VALUE\n"
                "G,2026,5,21,1,7\n"
                "G,2026,5,28,1,14\n"
                "G,2026,6,4,1,99\n"
                "G,bad,5,21,1,1\n"
                ",2026,5,21,1,1\n"
            )
        },
        n_days=7,
    )
    out = GT.fuel_offtake_periods(b, datetime(2026, 5, 25))
    assert out == {"G": ((1, 3000.0, 72), (73, 8000.0, 240))}
    assert not GT.fuel_offtake_periods(b, None)


def test_build_horizon_mode(caplog: pytest.LogCaptureFixture) -> None:
    """One allotment; price 0; slope = price × 24; rejected bands logged."""
    fuels = (
        _fuel("GA"),
        _fuel("GB", price_profile=(0.0, 100.0, 0.0, 300.0)),
        _fuel("MULTI"),
        _fuel("NOCAP", cap=None),
        _fuel("OFF"),
        _fuel("SOFT", max_offtake_cost=50.0),
        _fuel("LNG_INF"),
    )
    gens = (
        _gen("A1", "GA"),
        _gen("A2", "GA", pmax=0.0),
        _gen("B1", "GB", hr=0.5),
        GeneratorSpec(
            object_id=1, name="M", bus_name="N", pmax=1.0, fuel_names=("MULTI", "x")
        ),
        _gen("O", "OFF", pmax=0.0),
        _gen("S", "SOFT", vom_charge=1.0),
        _gen("L", "LNG_INF"),
    )
    with caplog.at_level(logging.INFO, logger=GT.logger.name):
        model = GT.build_gas_band_tanks(
            fuels,
            gens,
            mode="horizon",
            periods={},
            block_layout=(),
            horizon_hours=4,
            skip_fuels=frozenset({"LNG_INF"}),
        )
    names = {r.name: r for r in model.reservoirs}
    assert set(names) == {"gastank_GA", "gastank_GB", "gastank_SOFT"}
    assert names["gastank_GA"].eini == pytest.approx(200.0)
    assert {t.generator_name for t in model.turbines} == {"A1", "B1", "S"}
    fut = {f.tank: f for f in model.futures}
    assert fut["gastank_GA"].value == pytest.approx(4800.0)
    assert fut["gastank_GA"].hours_after is None
    # GB: p_ref = last price 300, residual (p_t - 300) kept on the units.
    gb = {f.name: f for f in model.fuels}["GB"]
    assert gb.price_profile == (-200.0, -200.0, -200.0, 0.0)
    assert gb.price_profile_complete and gb.max_offtake is None
    assert fut["gastank_GB"].value == pytest.approx(7200.0)
    purchase = [f for f in model.flows if f.name == "gastank_SOFT_purchase"]
    assert purchase[0].fcost == pytest.approx(250.0)
    assert names["gastank_SOFT"].emax > 200.0  # room for purchases
    by_fuel = {f.name: f for f in model.fuels}
    assert by_fuel["LNG_INF"].max_offtake is None and by_fuel["LNG_INF"].price == 200
    assert by_fuel["MULTI"].max_offtake == 4800.0
    assert model.dropped_constant == pytest.approx(3 * 200.0 * 4800.0 + 100 * 4800)
    assert "burns several fuels" in caplog.text
    assert "no online generator" in caplog.text
    assert "supersedes" in caplog.text


def test_build_period_mode(caplog: pytest.LogCaptureFixture) -> None:
    """Sub-period inflows, emptying at each end, expiry ⇒ no terminal value."""
    fuels = (_fuel("GA", cap=300.0), _fuel("GX", cap=300.0), _fuel("GM", cap=50.0))
    gens = (_gen("A", "GA"), _gen("X", "GX"), _gen("M", "GM"))
    layout = ((1, 2), (3,), (4, 5), (6,))
    periods = {
        "GA": ((1, 100.0, 3), (4, 200.0, 99)),
        "GX": ((1, 300.0, 6),),
        "GM": ((1, 10.0, 3),),  # does not add up to the cap
    }
    with caplog.at_level(logging.INFO, logger=GT.logger.name):
        model = GT.build_gas_band_tanks(
            fuels,
            gens,
            mode="period",
            periods=periods,
            block_layout=layout,
            horizon_hours=6,
            refill_hours=2,
        )
    res = {r.name: r for r in model.reservoirs}
    assert res["gastank_GA"].eini == pytest.approx(100.0 / 24.0)
    assert res["gastank_GA"].emax_profile == (12.5, 0.0, 12.5, 12.5)
    allot = {f.name: f for f in model.flows}["gastank_GA_allotment"]
    assert allot.discharge_profile == (0.0, 0.0, 0.0, 100.0, 100.0, 0.0)
    fut = {f.tank: f for f in model.futures}
    assert fut["gastank_GA"].value == pytest.approx(4800.0)
    assert fut["gastank_GA"].hours_after == 93.0
    assert fut["gastank_GA"].cap_end == 12.5
    # GX: its only sub-period ends inside the horizon ⇒ worthless after.
    assert "gastank_GX" not in fut
    assert res["gastank_GX"].emax_profile[-1] == 0.0
    assert "do not add up" in caplog.text
    assert not res["gastank_GM"].emax_profile


def test_build_misaligned_and_terminal_fn(caplog: pytest.LogCaptureFixture) -> None:
    """A boundary inside a block falls back; the terminal callback wins."""
    with caplog.at_level(logging.WARNING, logger=GT.logger.name):
        model = GT.build_gas_band_tanks(
            (_fuel("G_INF", cap=30.0),),
            (_gen("I", "G_INF", vom_charge=1.0, fuel_transport=-40.0),),
            mode="period",
            periods={"G_INF": ((1, 10.0, 1), (2, 20.0, 99))},
            block_layout=((1, 2),),
            horizon_hours=2,
            terminal_value=lambda fuel, gens, price: 7.0,
        )
    assert "inside a block" in caplog.text
    assert [(f.tank, f.value) for f in model.futures] == [("gastank_G_INF", 7.0)]
    assert model.fuels[0].price == 0.0 and not model.fuels[0].price_profile
    # VOM 1 + transport -40·0.25 < 0 ⇒ clamped.
    g = model.generators[0]
    assert g.vom_charge + g.fuel_transport * g.heat_rate == pytest.approx(0.0)


def test_build_off_and_bad_mode() -> None:
    """``off`` is the identity; unknown modes raise."""
    fuels, gens = (_fuel("GA"),), (_gen("A", "GA"),)
    kw: dict[str, Any] = {"periods": {}, "block_layout": (), "horizon_hours": 1}
    off = GT.build_gas_band_tanks(fuels, gens, mode="off", **kw)
    assert off.fuels == fuels and not off.reservoirs
    with pytest.raises(ValueError, match="unknown gas-bands-as-tanks"):
        GT.build_gas_band_tanks(fuels, gens, mode="x", **kw)


def _case_with_band() -> PlexosCase:
    return PlexosCase(
        bundle=None,  # type: ignore[arg-type]
        fuels=(_fuel("GA"), _fuel("G_INF", price=231.0)),
        generators=(_gen("A", "GA"), _gen("I", "G_INF")),
    )


class _Db:
    horizon_start = None


def _apply(tmp_path: Path, source: str, mode: str, cuts: str | None):
    return P._apply_gas_band_tanks(
        _case_with_band(),
        _Db(),  # type: ignore[arg-type]
        bundle(tmp_path, n_days=1),
        mode="horizon",
        lng_tanks={},
        lng_inflexible_mode=mode,
        lng_value_source=source,
        lng_diesel_cost=None,
        lng_cuts_file=cuts,
    )


@pytest.mark.parametrize(
    ("source", "mode", "inf"),
    [
        ("bundle", "offtake", 231.0 * 24.0),
        # diesel: no diesel unit in the case => no value => the band price.
        ("diesel", "offtake", 231.0 * 24.0),
        ("cuts", "offtake", 99.0),
        ("bundle", "storage", 231.0 * 24.0),
    ],
)
def test_apply_gas_band_tanks_values(
    tmp_path: Path, source: str, mode: str, inf: float
) -> None:
    """Manageable band at its price; INF band per --lng-inflexible-value."""
    cuts = tmp_path / "c.csv"
    cuts.write_text("scene,rhs,gastank_G_INF\n0,0,-99\n")
    case, futures = _apply(tmp_path, source, mode, str(cuts))
    fut = {f.tank: f.value for f in futures}
    assert fut["gastank_GA"] == pytest.approx(4800.0)
    assert fut["gastank_G_INF"] == pytest.approx(inf)
    assert {r.name for r in case.reservoirs} == {"gastank_GA", "gastank_G_INF"}
    assert case.boundary_cut is None  # the hydro cut is never touched


def test_gas_future_value_usable() -> None:
    """capacity saturates at what can be burnt before the deadline."""
    fv = GT.GasFutureValue("T", 10.0, cap_end=100.0, burn_rate=2.0, hours_after=30)
    assert fv.usable() == 60.0
    assert fv.usable("full") == 100.0
    assert GT.GasFutureValue("T", 10.0, cap_end=100.0).usable() == 100.0
    assert GT.GasFutureValue("T", 1.0, 5.0, 1.0, -3.0).usable() == 0.0


def test_gas_future_cost_specs() -> None:
    """One alpha (raw cost 1, >= 0) and one last-block cut per valued tank."""
    values = (
        GT.GasFutureValue("A", 5.0, cap_end=10.0, burn_rate=1.0, hours_after=4),
        GT.GasFutureValue("Z", 0.0, cap_end=10.0),
        GT.GasFutureValue("E", 5.0, cap_end=10.0, burn_rate=1.0, hours_after=0),
    )
    dvs, ucs = GT.gas_future_cost_specs(values, last_block=24)
    assert [(d.name, d.lower_bound, d.cost) for d in dvs] == [("alpha_gas_A", 0.0, 1.0)]
    assert ucs[0].name == "alpha_gas_A_cut"
    assert ucs[0].expression == (
        'decision_variable("alpha_gas_A").value + 5 * reservoir("A").efin '
        ">= 20, for(block in {24})"
    )
    dvs, ucs = GT.gas_future_cost_specs(values, last_block=24, mode="full")
    assert len(dvs) == 2 and "50, for" in ucs[1].expression
    with pytest.raises(ValueError, match="unknown gas-usable"):
        GT.gas_future_cost_specs(values, last_block=1, mode="x")


def test_extract_case_lng_tank_supersedes_band(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A modelled LNG Storage replaces its INF band's cap; no double tank."""
    kit = Model()
    monkeypatch.setattr(P, "load_xml", lambda _p: kit.db)
    b = _tank_bundle(tmp_path, deadline=None)
    (tmp_path / "Fuel_MaxOfftakeWeek.csv").write_text(
        "NAME,YEAR,MONTH,DAY,PERIOD,VALUE\nGAS_INF,2024,12,12,1,36.6\n"
    )
    case = P.extract_case(b, gas_bands_as_tanks="horizon", gas_refill="ramp:2")
    assert not any(r.name.startswith("gastank_") for r in case.reservoirs)
    assert {f.name: f for f in case.fuels}["GAS_INF"].max_offtake is None
    off = P.extract_case(b)
    assert {f.name: f for f in off.fuels}["GAS_INF"].max_offtake == pytest.approx(
        36600.0
    )


def test_cli_gas_options() -> None:
    """--gas-bands-as-tanks / --gas-refill parse."""
    args = make_parser().parse_args(
        ["-i", "x", "--gas-bands-as-tanks", "period", "--gas-refill", "ramp:12"]
    )
    assert args.gas_bands_as_tanks == "period"
    assert args.gas_refill == "ramp:12"
    assert args.gas_usable == "capacity"
    full = make_parser().parse_args(["-i", "x", "--gas-usable", "full"])
    assert full.gas_usable == "full"
    with pytest.raises(SystemExit):
        make_parser().parse_args(["-i", "x", "--gas-bands-as-tanks", "weekly"])
