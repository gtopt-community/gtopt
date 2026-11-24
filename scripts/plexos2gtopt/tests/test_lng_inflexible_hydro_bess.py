# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Inflexible-LNG tanks (``--lng-inflexible-mode``) and 2024 hydro-emulated BESS.

Synthetic PLEXOS tables (:class:`DbKit`) reproduce the two 2024 CEN PCP
patterns:

* ``TANK``: a Storage whose Head Storage generators are fuel-fed thermal
  units (ENGIE_MEJILLONES_GNL_INF), with a ``Vert_TANK`` spill at 7200;
* ``BAT_X``: a BESS emulated as storage ``BAT_X`` + discharge generator
  ``BAT_X`` + charge generator ``BAT_X_LOAD`` drawing from the solar
  plant's pondage ``X``, with the two ``Loss_*`` rows.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from plexos2gtopt import _hydro_bess as HB
from plexos2gtopt import _lng_inflexible as LNG
from plexos2gtopt import parsers as P
from plexos2gtopt.entities import FuelSpec, GeneratorSpec, fuel_transport_per_mwh
from plexos2gtopt.main import make_parser
from plexos2gtopt.plexos_loader import PlexosBundle
from plexos2gtopt.tests.cov_parsers_kit import DbKit, bundle

HR1 = 0.207232082  # MEJILLONES_3-TG+TV_GNL_INF
HR2 = 0.189063279  # TOCOPILLA_U16-TG+TV_GNL_INF


def _wide(cols: dict[str, list[float]]) -> str:
    names = list(cols)
    lines = ["YEAR,MONTH,DAY,PERIOD," + ",".join(names)]
    for h in range(24):
        vals = ",".join(str(cols[n][h]) for n in names)
        lines.append(f"2024,12,15,{h + 1},{vals}")
    return "\n".join(lines) + "\n"


def _long(rows: dict[str, float]) -> str:
    lines = ["NAME,YEAR,MONTH,DAY,PERIOD,BAND,VALUE"]
    for name, v in rows.items():
        lines.extend(f"{name},2024,12,15,{h},1,{v}" for h in range(1, 25))
    return "\n".join(lines) + "\n"


def _gen(name: str, pmax: float = 100.0, **kw: Any) -> GeneratorSpec:
    return GeneratorSpec(
        object_id=hash(name) % 10_000, name=name, bus_name="N1", pmax=pmax, **kw
    )


# --------------------------------------------------------------------------
# synthetic PLEXOS model
# --------------------------------------------------------------------------


class Model(DbKit):
    """DbKit holding the LNG tank and the hydro-emulated BESS."""

    def __init__(self, *, lng: bool = True, bess: bool = True) -> None:
        super().__init__()
        self.ids: dict[str, int] = {}
        self.n1 = self.obj("Node", "N1")
        self.con_coll = self.coll("System", "Constraint", "Constraints")
        if lng:
            self._lng()
        if bess:
            self._bess()

    def gen(
        self, name: str, *, cap: float, fuel: int | None = None, hr: float = 0.0
    ) -> int:
        oid = self.obj("Generator", name)
        self.ids[name] = oid
        self.mem("Generator", "Node", oid, self.n1, coll_name="Nodes")
        self.sysprop("Generator", oid, "Max Capacity", cap)
        self.sysprop("Generator", oid, "Units", 1.0)
        if fuel is not None:
            self.mem("Generator", "Fuel", oid, fuel, coll_name="Fuels")
        if hr:
            self.sysprop("Generator", oid, "Heat Rate", hr)
        return oid

    def storage(self, name: str, **props: float) -> int:
        oid = self.obj("Storage", name)
        self.ids[name] = oid
        for k, v in props.items():
            self.sysprop("Storage", oid, k.replace("_", " "), v)
        return oid

    def head(self, gen: str, sto: str) -> None:
        self.mem(
            "Generator",
            "Storage",
            self.ids[gen],
            self.ids[sto],
            coll_name="Head Storage",
        )

    def tail(self, gen: str, sto: str) -> None:
        self.mem(
            "Generator",
            "Storage",
            self.ids[gen],
            self.ids[sto],
            coll_name="Tail Storage",
        )

    def waterway(
        self, name: str, src: str, dst: str | None = None, **props: float
    ) -> int:
        oid = self.obj("Waterway", name)
        self.ids[name] = oid
        self.mem("Waterway", "Storage", oid, self.ids[src], coll_name="Storage From")
        if dst is not None:
            self.mem("Waterway", "Storage", oid, self.ids[dst], coll_name="Storage To")
        for k, v in props.items():
            self.sysprop("Waterway", oid, k.replace("_", " "), v)
        return oid

    def con(self, name: str, rhs: float = 0.0, sense: float = 0.0) -> int:
        oid = self.obj("Constraint", name)
        self.ids[name] = oid
        mid = self.sys_mid("Constraint", oid)
        self.data(mid, self.prop(self.con_coll, "Sense"), sense)
        self.data(mid, self.prop(self.con_coll, "RHS"), rhs)
        return oid

    def term(self, parent: str, name: str, con: str, prop: str, value: float) -> None:
        self.rel(
            parent,
            self.ids[name],
            "Constraint",
            self.ids[con],
            "Constraints",
            {prop: value},
        )

    def _lng(self) -> None:
        gas = self.obj("Fuel", "GAS_INF")
        self.ids["GAS_INF"] = gas
        self.sysprop("Fuel", gas, "Price", 231.0)
        self.gen("LNG1", cap=237.25, fuel=gas, hr=HR1)
        self.gen("LNG2", cap=350.425, fuel=gas, hr=HR2)
        self.storage("TANK", Max_Volume=1000.0)
        self.head("LNG1", "TANK")
        self.head("LNG2", "TANK")
        self.waterway("Vert_TANK", "TANK", Max_Flow_Penalty=7200.0)
        # A water reservoir with a hydro turbine (no Fuel) — never a tank.
        self.gen("HYD", cap=50.0)
        self.storage("WATER", Max_Volume=10.0)
        self.head("HYD", "WATER")

    def _bess(self) -> None:
        self.gen("X", cap=180.0)
        self.gen("BAT_X", cap=137.38)
        self.gen("BAT_X_LOAD", cap=137.38)
        self.sysprop("Generator", self.ids["BAT_X"], "Efficiency Incr", 1.0)
        self.sysprop("Generator", self.ids["BAT_X_LOAD"], "Efficiency Incr", 0.001)
        self.sysprop("Generator", self.ids["BAT_X_LOAD"], "Max Starts Day", 1.0)
        self.storage("BAT_X", Max_Volume=638.0, Initial_Volume=10.0)
        self.storage("X")
        self.head("BAT_X", "BAT_X")
        self.head("BAT_X_LOAD", "X")
        self.tail("BAT_X_LOAD", "BAT_X")
        self.head("X", "X")
        self.waterway("Loss_BAT_X_GEN", "BAT_X")
        self.waterway("Loss_BAT_X_LOAD", "X")
        self.waterway("Vert_BAT_X", "BAT_X", Max_Flow_Penalty=1.0)
        self.con("BAT_X_GenLosses")
        self.term(
            "Generator", "BAT_X", "BAT_X_GenLosses", "Generation Coefficient", 0.0306
        )
        self.term(
            "Waterway", "Loss_BAT_X_GEN", "BAT_X_GenLosses", "Flow Coefficient", -1.0
        )
        self.con("BAT_X_LoadLosses")
        self.term(
            "Generator",
            "BAT_X_LOAD",
            "BAT_X_LoadLosses",
            "Generation Coefficient",
            20.0,
        )
        self.term(
            "Waterway", "Loss_BAT_X_LOAD", "BAT_X_LoadLosses", "Flow Coefficient", -1.0
        )
        self.con("BAT_X_Uniq", rhs=1.0, sense=-1.0)
        self.term(
            "Generator", "BAT_X", "BAT_X_Uniq", "Units Generating Coefficient", 1.0
        )
        self.term(
            "Generator", "BAT_X_LOAD", "BAT_X_Uniq", "Units Generating Coefficient", 1.0
        )
        self.con("BAT_X_MaxInjection", rhs=180.0, sense=-1.0)
        self.term(
            "Generator", "BAT_X", "BAT_X_MaxInjection", "Generation Coefficient", 1.0
        )
        self.term("Generator", "X", "BAT_X_MaxInjection", "Generation Coefficient", 1.0)
        self.con("BAT_X_Charge", rhs=0.0, sense=-1.0)
        self.term(
            "Generator", "BAT_X_LOAD", "BAT_X_Charge", "Generation Coefficient", 1.0
        )


def _tank_bundle(
    tmp_path: Path, *, deadline: int | None = 20, eini: float = 100.0
) -> PlexosBundle:
    emax = [1000.0] * 24
    if deadline is not None:
        emax[deadline:] = [0.0] * (24 - deadline)
    b = bundle(
        tmp_path,
        {
            "Hydro_MaxVolume.csv": _wide({"TANK": emax, "WATER": [10.0] * 24}),
            "Hydro_InitialVolume.csv": _long({"TANK": eini, "WATER": 5.0}),
            "BESS_IniValue.csv": _long({"BAT_X": 351.44167}),
        },
        n_days=1,
    )
    b.block_layout = tuple((h,) for h in range(1, 25))
    return b


# --------------------------------------------------------------------------
# LNG tank detection and models
# --------------------------------------------------------------------------


def test_detect_lng_tanks_structural(tmp_path: Path) -> None:
    """Only the Storage fed solely by fuel-fed generators is a tank."""
    kit = Model(bess=False)
    kit.storage("MEMBERLESS_GNL_INF")
    tanks = LNG.detect_lng_tanks(
        kit.db, _tank_bundle(tmp_path), P._gen_fuel_map(kit.db)
    )
    assert list(tanks) == ["TANK"]
    tank = tanks["TANK"]
    assert tank.generators == ("LNG1", "LNG2")
    assert tank.fuels == ("GAS_INF",)
    assert tank.eini == pytest.approx(100.0)
    assert tank.cargo_fuel == pytest.approx(2400.0)
    assert tank.deadline_hour == 20
    assert tank.spill_waterway == "Vert_TANK"
    assert tank.spill_penalty == pytest.approx(7200.0)


def test_detect_lng_tanks_static_fallbacks() -> None:
    """Without CSVs: static Initial Volume, no deadline; no heads -> none."""
    kit = Model(bess=False)
    kit.sysprop("Storage", kit.ids["TANK"], "Initial Volume", 42.0)
    tank = LNG.detect_lng_tanks(kit.db, None, P._gen_fuel_map(kit.db))["TANK"]
    assert tank.eini == pytest.approx(42.0)
    assert tank.deadline_hour is None
    assert tank.emax_series == ()
    assert not LNG.detect_lng_tanks(DbKit().db, None, {})


def test_spill_waterways_edge_cases() -> None:
    """2-ended / unpenalised waterways are not spills; the max penalty wins."""
    kit = Model(bess=False)
    kit.waterway("Vert2_TANK", "TANK", Max_Flow_Penalty=9000.0)
    kit.waterway("Link", "TANK", "WATER", Max_Flow_Penalty=5.0)
    kit.waterway("Free", "WATER")
    spills = LNG._spill_waterways(kit.db)
    assert spills == {"TANK": ("Vert2_TANK", 9000.0)}
    assert not LNG._spill_waterways(DbKit().db)


def test_depletion_hand_check() -> None:
    """pf = 1/HR and fcr = 1/24: 237.25 + 350.425 MW for 1 h -> 4.8091."""
    tank = LNG.LngTank(
        "T", 1, ("LNG1", "LNG2"), ("GAS",), 872.083333, (), 0.0, None, 7200.0, None
    )
    turbines = LNG.lng_turbine_specs(tank, {"LNG1": HR1, "LNG2": HR2})
    flows = [
        237.25 / turbines[0].production_factor,
        350.425 / turbines[1].production_factor,
    ]
    assert sum(flows) / 24.0 == pytest.approx(4.8091, abs=5e-5)


def test_lng_reservoir_spec_profiles(tmp_path: Path) -> None:
    """emax keeps the drop to 0; a flat profile collapses to a scalar."""
    kit = Model(bess=False)
    b = _tank_bundle(tmp_path)
    tank = LNG.detect_lng_tanks(kit.db, b, P._gen_fuel_map(kit.db))["TANK"]
    res = LNG.lng_reservoir_spec(tank, b.block_layout)
    assert res.emax == pytest.approx(1000.0)
    assert res.emin == 0.0 and res.efin == 0.0
    assert res.eini == pytest.approx(100.0)
    assert res.spill_flow_penalty == pytest.approx(7200.0)
    assert res.emax_profile[19] == pytest.approx(1000.0)
    assert res.emax_profile[20] == 0.0
    # Two-hour blocks take the min over the block (deadline block -> 0).
    two = tuple((h, h + 1) for h in range(1, 25, 2))
    assert LNG.lng_reservoir_spec(tank, two).emax_profile[10] == 0.0
    flat = LNG.lng_reservoir_spec(
        LNG.LngTank("T", 1, (), (), 5.0, (7.0,) * 24, 0.0, None, 0.0, None),
        b.block_layout,
    )
    assert flat.emax == pytest.approx(7.0)
    assert not flat.emax_profile
    # No series, no static cap -> eini; a block outside the series -> static.
    bare = LNG.LngTank("T", 1, (), (), 5.0, (), 0.0, None, 0.0, None)
    assert LNG.lng_reservoir_spec(bare).emax == pytest.approx(5.0)
    short = LNG.LngTank("T", 1, (), (), 5.0, (3.0, 0.0), 9.0, 1, 0.0, None)
    assert LNG._block_emax_profile(short, ((1,), (2,), (30,))) == (3.0, 0.0, 9.0)


def test_lng_turbine_without_heat_rate_warns(caplog: pytest.LogCaptureFixture) -> None:
    """A tank generator with HR <= 0 gets no turbine and a warning."""
    tank = LNG.LngTank("T", 1, ("A", "B"), (), 1.0, (), 0.0, None, 0.0, None)
    with caplog.at_level(logging.WARNING, logger=LNG.logger.name):
        out = LNG.lng_turbine_specs(tank, {"A": 0.5})
    assert [t.generator_name for t in out] == ["A"]
    assert out[0].production_factor == pytest.approx(2.0)
    assert "'B'" in caplog.text


def test_offtake_reservoir_has_free_spill(tmp_path: Path) -> None:
    """offtake: same reservoir, spill_flow_penalty 0 (lost gas is free)."""
    kit = Model(bess=False)
    b = _tank_bundle(tmp_path)
    tank = LNG.detect_lng_tanks(kit.db, b, P._gen_fuel_map(kit.db))["TANK"]
    res = LNG.lng_reservoir_spec(tank, b.block_layout, mode="offtake")
    assert res.spill_flow_penalty == 0.0
    assert res.emax_profile[20] == 0.0 and res.eini == pytest.approx(100.0)


def test_cli_lng_options() -> None:
    """The CLI exposes the mode with its default; no penalty option."""
    parser = make_parser()
    args = parser.parse_args(["-i", "x"])
    assert args.lng_inflexible_mode == "storage"
    args = parser.parse_args(["-i", "x", "--lng-inflexible-mode", "offtake"])
    assert args.lng_inflexible_mode == "offtake"
    args = parser.parse_args(
        [
            "-i",
            "x",
            "--lng-inflexible-value",
            "diesel",
            "--lng-inflexible-diesel-cost",
            "270",
            "--lng-inflexible-cuts",
            "c.csv",
        ]
    )
    assert args.lng_inflexible_value == "diesel"
    assert args.lng_inflexible_diesel_cost == 270.0
    assert args.lng_inflexible_cuts == "c.csv"
    with pytest.raises(SystemExit):
        parser.parse_args(["-i", "x", "--lng-inflexible-penalty", "3"])


def test_zero_empty_tank_generators() -> None:
    """Units of an empty tank get pmax 0; others are untouched."""
    tanks = {
        "T": LNG.LngTank("T", 1, ("A",), (), 10.0, (), 0.0, 2, 0.0, None),
        "E": LNG.LngTank("E", 2, ("C",), (), 0.0, (), 0.0, None, 0.0, None),
    }
    gens = (_gen("A", 10.0), _gen("C", 10.0, pmax_profile=(1.0, 2.0)))
    out = LNG.zero_empty_tank_generators(gens, tanks)
    assert out[0] is gens[0]
    assert out[1].pmax == 0.0 and out[1].pmax_profile == ()


def test_sunk_lng_fuels(caplog: pytest.LogCaptureFixture) -> None:
    """Exclusive tank fuels -> price 0; shared fuels keep it with a warning."""
    tanks = {
        "T": LNG.LngTank(
            "T", 1, ("A", "B"), ("GAS", "SHARED"), 10.0, (), 0.0, 2, 0.0, None
        ),
        "E": LNG.LngTank("E", 2, ("C",), ("EMPTY",), 0.0, (), 0.0, None, 0.0, None),
    }
    gens = (
        _gen("A", 10.0, fuel_names=("GAS",)),
        _gen("B", 10.0, fuel_names=("SHARED",)),
        _gen("OUT", 10.0, fuel_names=("SHARED",)),
    )
    fuels = (
        FuelSpec(object_id=1, name="GAS", price=231.0, price_profile=(231.0, 232.0)),
        FuelSpec(object_id=2, name="SHARED", price=100.0),
        FuelSpec(object_id=3, name="EMPTY", price=50.0),
    )
    with caplog.at_level(logging.INFO, logger=LNG.logger.name):
        out = {f.name: f for f in LNG.sunk_lng_fuels(tanks, gens, fuels)}
    assert out["GAS"].price == 0.0 and out["GAS"].price_profile == (0.0, 0.0)
    assert out["SHARED"].price == 100.0
    assert out["EMPTY"].price == 50.0
    assert "'SHARED' also feeds generator(s) outside the tank (OUT)" in caplog.text


# --------------------------------------------------------------------------
# hydro-emulated BESS
# --------------------------------------------------------------------------


def test_detect_hydro_bess_standard_group() -> None:
    """Storage + discharge/charge generators + two loss rows -> one group."""
    kit = Model(lng=False)
    (grp,) = HB.detect_hydro_bess(kit.db)
    assert (grp.storage, grp.discharge_gen, grp.charge_gen) == (
        "BAT_X",
        "BAT_X",
        "BAT_X_LOAD",
    )
    assert grp.source_storage == "X"
    assert grp.standard
    assert grp.members == ("BAT_X", "BAT_X_LOAD")
    assert grp.loss_rows == ("BAT_X_GenLosses", "BAT_X_LoadLosses")
    assert grp.loss_waterways == ("Loss_BAT_X_GEN", "Loss_BAT_X_LOAD")
    # PLEXOS 2024-12-15 solution: 1 MWh out drains 1.0306; 1 MWh in draws 1.02.
    assert grp.output_efficiency == pytest.approx(1.0 / 1.0306)
    assert grp.input_efficiency == pytest.approx(1.0 / 1.02)
    assert grp.charge_gen_factor == pytest.approx(0.001 / 1.02)
    aliases = HB.generator_aliases([grp])
    assert aliases["BAT_X"] == HB.GeneratorAlias("BAT_X", "discharge", 1.0)
    assert aliases["BAT_X_LOAD"].accessor == "charge"


def _pumped_model(*, shared_tail: bool = False) -> Model:
    """2025 pumped form (BAT_DEL_DESIERTO, PCP 2025-04-27).

    One generator ``BAT_P`` with Head Storage ``BAT_P`` and Tail Storage
    ``P`` (the mirror tank), ``Pump Load`` 200, ``Pump Efficiency`` 97 and a
    3 % generation loss row.
    """
    kit = Model(lng=False, bess=False)
    kit.gen("BAT_P", cap=200.0)
    gid = kit.ids["BAT_P"]
    kit.sysprop("Generator", gid, "Efficiency Incr", 1.0)
    kit.sysprop("Generator", gid, "Pump Load", 200.0)
    kit.sysprop("Generator", gid, "Pump Efficiency", 97.0)
    kit.storage("BAT_P", Max_Volume=800.0)
    kit.storage("P", Max_Volume=800.0, Initial_Volume=800.0)
    kit.head("BAT_P", "BAT_P")
    kit.tail("BAT_P", "P")
    if shared_tail:
        kit.gen("OTHER", cap=10.0)
        kit.head("OTHER", "P")
    kit.waterway("Loss_BAT_P_GEN", "BAT_P")
    kit.con("BAT_P_GenLosses")
    kit.term("Generator", "BAT_P", "BAT_P_GenLosses", "Generation Coefficient", 0.03)
    kit.term("Waterway", "Loss_BAT_P_GEN", "BAT_P_GenLosses", "Flow Coefficient", -1.0)
    return kit


def test_detect_hydro_bess_pumped_single_generator(tmp_path: Path) -> None:
    """Pumped generator (Head S, Tail T, Pump Load) -> a chargeable battery.

    Regression (PCP 2025-04-27): the group was non-standard, only the
    discharge turbine was emitted and the PLEXOS end volume (95 MWh from
    eini 0) was unreachable -> infeasible LP.
    """
    kit = _pumped_model()
    (grp,) = HB.detect_hydro_bess(kit.db)
    assert grp.standard
    assert grp.charge_gen is None
    assert grp.source_storage == "P"
    assert grp.pump_load == pytest.approx(200.0)
    assert grp.input_efficiency == pytest.approx(0.97)
    assert grp.output_efficiency == pytest.approx(1.0 / 1.03)
    bat = HB.build_battery(kit.db, None, grp, {"BAT_P": _gen("BAT_P", 200.0)})
    assert bat.pmax_charge == pytest.approx(200.0)
    assert bat.pmax_discharge == pytest.approx(200.0)
    assert bat.input_efficiency == pytest.approx(0.97)
    assert HB.charge_source_constraint(grp, frozenset({"X"})) is None


def test_detect_hydro_bess_pumped_shared_tail_stays_non_standard() -> None:
    """A tail storage used by another generator is not a mirror tank."""
    (grp,) = HB.detect_hydro_bess(_pumped_model(shared_tail=True).db)
    assert not grp.standard
    assert grp.pump_load == 0.0


def test_detect_hydro_bess_non_standard_and_ignored_rows() -> None:
    """Extra head generator -> non-standard; non-loss rows are ignored."""
    kit = Model(lng=False)
    kit.gen("BAT_X_CSF", cap=10.0)
    kit.head("BAT_X_CSF", "BAT_X")
    # Not loss rows: non-zero RHS, non-zero Sense, foreign property kind,
    # two-ended waterway, negative generation coefficient.
    kit.waterway("Link", "X", "BAT_X")
    for name, rhs, sense in (
        ("R1", 1.0, 0.0),
        ("R2", 0.0, -1.0),
        ("R3", 0.0, 0.0),
        ("R4", 0.0, 0.0),
        ("R5", 0.0, 0.0),
    ):
        kit.con(name, rhs=rhs, sense=sense)
        kit.term("Waterway", "Loss_BAT_X_GEN", name, "Flow Coefficient", -1.0)
    for name in ("R1", "R2"):
        kit.term("Generator", "BAT_X", name, "Generation Coefficient", 1.0)
    kit.term("Generator", "BAT_X", "R3", "Units Generating Coefficient", 1.0)
    kit.term("Generator", "BAT_X", "R4", "Generation Coefficient", -1.0)
    kit.term("Generator", "BAT_X", "R5", "Generation Coefficient", 1.0)
    kit.term("Waterway", "Link", "R5", "Flow Coefficient", 0.5)
    groups = {g.storage: g for g in HB.detect_hydro_bess(kit.db)}
    assert not groups["BAT_X"].standard
    assert "BAT_X_CSF" in groups["BAT_X"].members
    assert not HB.detect_hydro_bess(DbKit().db)


def test_build_battery_and_charge_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """BatterySpec from the group; CRCA-style charge-source row."""
    kit = Model(lng=False)
    (grp,) = HB.detect_hydro_bess(kit.db)
    prof = tuple([0.0] * 6 + [137.38] * 18)
    gens = {
        "BAT_X": _gen("BAT_X", 137.38, pmax_profile=prof),
        "BAT_X_LOAD": _gen("BAT_X_LOAD", 137.38, pmax_profile=prof),
    }
    monkeypatch.setenv("GTOPT_BATTERY_PIN_EFIN", "1")
    bat = HB.build_battery(kit.db, _tank_bundle(tmp_path), grp, gens)
    assert bat.name == "BAT_X" and bat.bus_name == "N1"
    assert bat.emax == pytest.approx(638.0)
    assert bat.eini == pytest.approx(351.44167)
    assert bat.efin == pytest.approx(351.44167)
    assert bat.pmax_discharge == pytest.approx(137.38)
    assert bat.pmax_charge == pytest.approx(137.38 * 1.02)
    assert bat.power_profile == prof
    assert bat.max_cycles_day == pytest.approx(1.0)
    monkeypatch.setenv("GTOPT_BATTERY_PIN_EFIN", "0")
    bat = HB.build_battery(kit.db, None, grp, {"BAT_X": _gen("BAT_X", 50.0)})
    assert bat.eini == pytest.approx(10.0)  # static Initial Volume fallback
    assert bat.efin == 0.0
    assert bat.pmax_charge == 0.0
    assert not bat.power_profile

    row = HB.charge_source_constraint(grp, frozenset({"X"}))
    assert row is not None
    assert (
        row.expression
        == '1 * battery("BAT_X").charge - 1 * generator("X").generation <= 0'
    )
    with caplog.at_level(logging.WARNING, logger=HB.logger.name):
        assert HB.charge_source_constraint(grp, frozenset()) is None
    assert "may charge from the grid" in caplog.text
    assert HB.group_is_online(grp, gens)
    assert not HB.group_is_online(grp, {"BAT_X": _gen("BAT_X", 0.0)})


def test_prepare_storage_models(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Empty-tank zeroing + BESS translation + orphan pondage."""
    kit = Model()
    gens = (
        _gen("LNG1", 237.25, heat_rate=HR1),
        _gen("LNG2", 350.425, heat_rate=HR2),
        _gen("HYD", 50.0),
        _gen("X", 180.0),
        _gen("BAT_X", 137.38),
        _gen("BAT_X_LOAD", 137.38),
    )
    with pytest.raises(ValueError, match="lng_inflexible_mode"):
        P._prepare_storage_models(kit.db, _tank_bundle(tmp_path), gens, "bogus")
    with caplog.at_level(logging.INFO, logger=P.logger.name):
        models = P._prepare_storage_models(
            kit.db, _tank_bundle(tmp_path), gens, "offtake"
        )
    names = [g.name for g in models.generators]
    assert names == ["LNG1", "LNG2", "HYD", "X"]
    assert models.generators[0].pmax == pytest.approx(237.25)
    assert [b.name for b in models.bess_batteries] == ["BAT_X"]
    assert models.orphan_storages == frozenset({"X"})
    assert set(models.skip_constraints) == {"BAT_X_GenLosses", "BAT_X_LoadLosses"}
    assert "translated 1 2024 hydro-emulated BESS" in caplog.text
    # An empty tank's units can never run (both modes).
    models = P._prepare_storage_models(
        kit.db, _tank_bundle(tmp_path, eini=0.0), gens, "storage"
    )
    assert models.generators[0].pmax == 0.0


def test_prepare_storage_models_offline_and_non_standard(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Offline group: loss rows skipped; non-standard online: warning."""
    kit = Model(lng=False)
    off = (_gen("X", 180.0), _gen("BAT_X", 0.0), _gen("BAT_X_LOAD", 0.0))
    models = P._prepare_storage_models(kit.db, _tank_bundle(tmp_path), off, "storage")
    assert not models.bess_batteries
    assert "offline for the whole horizon" in models.skip_constraints["BAT_X_GenLosses"]
    kit.gen("BAT_X_CSF", cap=10.0)
    kit.head("BAT_X_CSF", "BAT_X")
    on = (_gen("X", 180.0), _gen("BAT_X", 10.0), _gen("BAT_X_LOAD", 10.0))
    with caplog.at_level(logging.WARNING, logger=P.logger.name):
        models = P._prepare_storage_models(
            kit.db, _tank_bundle(tmp_path), on, "storage"
        )
    assert not models.bess_batteries and not models.skip_constraints
    assert "does not match the standard" in caplog.text


def test_user_constraints_with_bess_aliases(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Generation terms -> battery; commitment terms drop the row; loss rows skipped."""
    kit = Model(lng=False)
    (grp,) = HB.detect_hydro_bess(kit.db)
    aliases = HB.generator_aliases([grp])
    skip = {r: "absorbed" for r in grp.loss_rows}
    emitted = {"generator": frozenset({"X"}), "battery": frozenset({"BAT_X"})}
    with caplog.at_level(logging.INFO, logger=P.logger.name):
        ucs = {
            u.name: u
            for u in P.extract_user_constraints(
                kit.db,
                bundle(tmp_path),
                emitted_names=emitted,
                pmax_by_gen={"X": 180.0},
                generator_aliases=aliases,
                skip_constraints=skip,
            )
        }
    assert set(ucs) == {"BAT_X_MaxInjection", "BAT_X_Charge"}
    assert 'battery("BAT_X").discharge' in ucs["BAT_X_MaxInjection"].expression
    assert 'generator("X").generation' in ucs["BAT_X_MaxInjection"].expression
    assert 'battery("BAT_X").charge' in ucs["BAT_X_Charge"].expression
    assert "constraint BAT_X_GenLosses not converted: absorbed" in caplog.text
    assert "BAT_X_Uniq not converted" in caplog.text
    # --drop-batteries: aliased terms vanish with the batteries.
    ucs2 = P.extract_user_constraints(
        kit.db,
        bundle(tmp_path),
        emitted_names=emitted,
        pmax_by_gen={"X": 180.0},
        generator_aliases=aliases,
        skip_constraints=skip,
        drop_batteries=True,
    )
    assert all("battery(" not in u.expression for u in ucs2)


def test_user_constraints_on_dead_waterways(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Terms on the arcs of an orphan pondage contribute 0 (PCP 2024-09-01).

    The offline BAT_Y group's ``Act_BAT_Y_CSFLW`` (X -> BAT_Y) is dropped
    with the orphan pondage X, but ``BAT_Y_ActCSFLW`` still references it:
    the conversion failed with an unresolved waterway reference.  A row
    keeping another term keeps it; a row left with no term becomes the
    inactive ``0 <op> 0`` stub; both are logged.
    """
    kit = Model(lng=False)
    kit.gen("BAT_Y", cap=10.0)
    kit.gen("BAT_Y_LOAD", cap=10.0)
    kit.storage("BAT_Y", Max_Volume=40.0, Initial_Volume=2.0)
    kit.head("BAT_Y", "BAT_Y")
    kit.head("BAT_Y_LOAD", "X")
    kit.tail("BAT_Y_LOAD", "BAT_Y")
    kit.waterway("Act_BAT_Y_CSFLW", "X", "BAT_Y")
    kit.con("BAT_Y_ActCSFLW")
    kit.term("Waterway", "Act_BAT_Y_CSFLW", "BAT_Y_ActCSFLW", "Flow Coefficient", 1.0)
    kit.con("BAT_Y_ActMixed", rhs=5.0, sense=-1.0)
    kit.term("Waterway", "Act_BAT_Y_CSFLW", "BAT_Y_ActMixed", "Flow Coefficient", 1.0)
    kit.term("Generator", "X", "BAT_Y_ActMixed", "Generation Coefficient", 1.0)
    dead = P._waterways_touching(kit.db, frozenset({"X"}))
    assert "Act_BAT_Y_CSFLW" in dead
    assert not P._waterways_touching(kit.db, frozenset())
    # Everything but the dead arc resolves (the BAT_X group rows too).
    emitted = {
        "Generator": frozenset(o.name for o in kit.db.objects_of_class("Generator")),
        "Waterway": frozenset(o.name for o in kit.db.objects_of_class("Waterway"))
        - dead,
        "Commitment": frozenset(
            f"uc_{o.name}" for o in kit.db.objects_of_class("Generator")
        ),
    }
    with caplog.at_level(logging.INFO, logger=P.logger.name):
        ucs = {
            u.name: u
            for u in P.extract_user_constraints(
                kit.db,
                bundle(tmp_path),
                emitted_names=emitted,
                pmax_by_gen={"X": 180.0},
                dead_waterways=dead,
            )
        }
    only_dead = ucs["BAT_Y_ActCSFLW"]
    assert only_dead.active is False
    assert only_dead.expression.startswith("0 ") and only_dead.expression.endswith(" 0")
    assert "offline hydro-emulated BESS" in only_dead.description
    mixed = ucs["BAT_Y_ActMixed"]
    assert mixed.active is not False
    assert "waterway(" not in mixed.expression
    assert 'generator("X").generation' in mixed.expression
    assert "dropping term on waterway 'Act_BAT_Y_CSFLW'" in caplog.text
    # Without the dead set the reference is unresolved (the regression).
    with pytest.raises(P.UnresolvedConstraintReferenceError, match="Act_BAT_Y_CSFLW"):
        P.extract_user_constraints(
            kit.db,
            bundle(tmp_path),
            emitted_names=emitted,
            pmax_by_gen={"X": 180.0},
        )


def test_storage_model_constraints(tmp_path: Path) -> None:
    """Charge-source rows unless batteries are dropped."""
    kit = Model()
    gens = (_gen("X", 180.0), _gen("BAT_X", 137.38), _gen("BAT_X_LOAD", 137.38))
    models = P._prepare_storage_models(kit.db, _tank_bundle(tmp_path), gens, "offtake")
    case = P.PlexosCase(bundle=None, generators=models.generators)  # type: ignore[arg-type]
    rows = P._storage_model_constraints(models, case, drop_batteries=False)
    assert [r.name for r in rows] == ["bess_charge_source_BAT_X"]
    assert not P._storage_model_constraints(models, case, drop_batteries=True)


@pytest.mark.parametrize("mode", ["storage", "offtake"])
def test_extract_case_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Full extract_case on the synthetic model in both LNG modes."""
    kit = Model()
    monkeypatch.setattr(P, "load_xml", lambda _p: kit.db)
    monkeypatch.delenv("GTOPT_DROP_BATTERIES", raising=False)
    case = P.extract_case(_tank_bundle(tmp_path), lng_inflexible_mode=mode)
    gen_names = {g.name for g in case.generators}
    assert {"BAT_X", "BAT_X_LOAD"}.isdisjoint(gen_names)
    assert [b.name for b in case.batteries] == ["BAT_X"]
    res = {r.name for r in case.reservoirs}
    turb = {t.generator_name: t for t in case.turbines}
    ucs = {u.name for u in case.user_constraints}
    assert "X" not in turb and "BAT_X" not in res
    assert {j.name for j in case.junctions}.isdisjoint({"X", "BAT_X"})
    assert {w.name for w in case.waterways}.isdisjoint(
        {"Vert_TANK", "Vert_BAT_X", "Loss_BAT_X_GEN"}
    )
    assert "bess_charge_source_BAT_X" in ucs
    assert not any(n.startswith("lng_") for n in ucs)
    tank = next(r for r in case.reservoirs if r.name == "TANK")
    assert turb["LNG1"].production_factor == pytest.approx(1.0 / HR1)
    gas = next(f for f in case.fuels if f.name == "GAS_INF")
    if mode == "storage":
        assert tank.spill_flow_penalty == pytest.approx(7200.0)
        assert gas.price == pytest.approx(231.0)
    else:
        assert tank.spill_flow_penalty == 0.0
        assert gas.price == 0.0


# --------------------------------------------------------------------------
# offtake terminal value (deadline beyond the horizon)
# --------------------------------------------------------------------------


def test_reference_diesel_cost() -> None:
    """Median SRMC of online diesel units; None without any."""
    fuels = (
        FuelSpec(object_id=1, name="D1", price=1000.0, type_tag="diesel"),
        FuelSpec(object_id=2, name="G", price=200.0, type_tag="gas"),
    )
    gens = (
        _gen("A", 10.0, fuel_names=("D1",), heat_rate=0.25, vom_charge=5.0),
        _gen("B", 10.0, fuel_names=("D1",), heat_rate=0.30),
        _gen("C", 0.0, fuel_names=("D1",), heat_rate=0.9),  # offline
        _gen("GAS", 10.0, fuel_names=("G",), heat_rate=0.2),
    )
    assert LNG.reference_diesel_cost(gens, fuels) == pytest.approx(277.5)
    assert LNG.reference_diesel_cost(gens[:1], fuels) == pytest.approx(255.0)
    assert LNG.reference_diesel_cost(gens[3:], fuels) is None


def test_read_tank_cut_values(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Coefficient -c -> value c; first row only."""
    path = tmp_path / "cuts.csv"
    path.write_text("scene,rhs,TANK,R\n0,10,-5000,\n0,11,-1,-2\n")
    with caplog.at_level(logging.WARNING, logger=LNG.logger.name):
        assert LNG.read_tank_cut_values(path) == {"TANK": 5000.0}
    assert "only the first" in caplog.text
    empty = tmp_path / "e.csv"
    empty.write_text("scene,rhs\n")
    assert not LNG.read_tank_cut_values(empty)


def test_tank_terminal_value(caplog: pytest.LogCaptureFixture) -> None:
    """bundle / diesel / cuts (with fallback); none inside the horizon."""
    open_tank = LNG.LngTank(
        "T", 1, ("A", "B"), ("GAS",), 10.0, (), 0.0, None, 0.0, None
    )
    fuels = (FuelSpec(object_id=1, name="GAS", price=231.0),)
    hrs = {"A": HR1, "B": HR2}
    kw: dict[str, Any] = {"fuels": fuels, "heat_rates": hrs, "diesel_cost": 270.0}
    assert LNG.tank_terminal_value(open_tank, "bundle", **kw) == pytest.approx(5544.0)
    assert LNG.tank_terminal_value(open_tank, "diesel", **kw) == pytest.approx(
        270.0 / HR2 * 24.0
    )
    assert (
        LNG.tank_terminal_value(open_tank, "cuts", cut_values={"T": 9.0}, **kw) == 9.0
    )
    with caplog.at_level(logging.WARNING, logger=LNG.logger.name):
        assert LNG.tank_terminal_value(
            open_tank, "cuts", cut_values={}, **kw
        ) == pytest.approx(5544.0)
    assert "no 'T' column" in caplog.text
    kw["diesel_cost"] = None
    assert LNG.tank_terminal_value(open_tank, "diesel", **kw) is None
    assert LNG.tank_terminal_value(open_tank, "bundle", **{**kw, "fuels": ()}) is None
    closed = LNG.LngTank("T", 1, ("A",), ("GAS",), 10.0, (), 0.0, 5, 0.0, None)
    assert LNG.tank_terminal_value(closed, "bundle", **kw) is None


def test_lng_future_values(tmp_path: Path) -> None:
    """One future value per open tank (none for a deadline inside); errors."""
    tanks = {
        "T": LNG.LngTank(
            "T", 1, ("A",), ("GAS",), 10.0, (30.0, 40.0), 0.0, None, 0.0, None
        ),
        "S": LNG.LngTank("S", 2, ("A",), ("GAS",), 10.0, (), 0.0, 3, 0.0, None),
        "Q": LNG.LngTank("Q", 3, ("A",), ("GAS",), 10.0, (), 0.0, None, 0.0, None),
    }
    gens = (
        _gen("A", 10.0, heat_rate=0.2, fuel_names=("GAS",), pmax_profile=(5.0, 12.0)),
        _gen("D", 10.0, heat_rate=0.25, fuel_names=("DIE",)),
    )
    fuels = (
        FuelSpec(object_id=1, name="GAS", price=200.0),
        FuelSpec(object_id=2, name="DIE", price=1000.0, type_tag="diesel"),
    )
    kw: dict[str, Any] = {
        "source": "bundle",
        "diesel_cost": None,
        "cuts_file": None,
        "periods": {"GAS": ((1, 1.0, 72),)},
        "horizon_hours": 48,
    }
    out = {f.tank: f for f in P._lng_future_values(tanks, gens, fuels, **kw)}
    assert set(out) == {"T", "Q"}
    assert out["T"].value == pytest.approx(4800.0)
    assert out["T"].cap_end == 40.0 and out["Q"].cap_end == 10.0  # eini fallback
    assert out["T"].burn_rate == pytest.approx(12.0 * 0.2 / 24.0)
    assert out["T"].hours_after == 24.0
    kw["source"] = "diesel"
    out = {f.tank: f for f in P._lng_future_values(tanks, gens, fuels, **kw)}
    assert out["T"].value == pytest.approx(250.0 / 0.2 * 24.0)
    path = tmp_path / "c.csv"
    path.write_text("scene,rhs,T\n0,0,-77\n")
    kw.update(source="cuts", cuts_file=str(path), periods={})
    out = {f.tank: f for f in P._lng_future_values(tanks, gens, fuels, **kw)}
    assert out["T"].value == 77.0 and out["T"].hours_after is None
    with pytest.raises(ValueError, match="needs --lng-inflexible-cuts"):
        P._lng_future_values(tanks, gens, fuels, **{**kw, "cuts_file": None})
    with pytest.raises(ValueError, match="unknown lng value source"):
        P._lng_future_values(tanks, gens, fuels, **{**kw, "source": "x"})


def test_extract_case_offtake_terminal_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deadline beyond the horizon -> own α + cut row; hydro cut untouched."""
    kit = Model()
    monkeypatch.setattr(P, "load_xml", lambda _p: kit.db)
    case = P.extract_case(
        _tank_bundle(tmp_path, deadline=None), lng_inflexible_mode="offtake"
    )
    assert case.boundary_cut is None
    assert any(d.name == "alpha_gas_TANK" for d in case.decision_variables)
    cut = {u.name: u for u in case.user_constraints}["alpha_gas_TANK_cut"]
    assert 'reservoir("TANK").efin' in cut.expression
    assert "for(block in {24})" in cut.expression
    assert f"{231.0 * 24.0:.12g} *" in cut.expression
    storage = P.extract_case(
        _tank_bundle(tmp_path, deadline=None), lng_inflexible_mode="storage"
    )
    assert not any(d.name.startswith("alpha_gas") for d in storage.decision_variables)


def test_clamp_sunk_gas_costs(caplog: pytest.LogCaptureFixture) -> None:
    """Negative transport on sunk gas -> variable cost clamped to 0."""
    gens = (
        _gen("A", 10.0, fuel_names=("GAS",), heat_rate=0.2, vom_charge=2.0),
        _gen("B", 10.0, fuel_names=("GAS",), heat_rate=0.2, vom_charge=1.0),
        _gen("C", 10.0, fuel_names=("OTHER",), heat_rate=0.2),
    )
    gens = (
        replace(gens[0], fuel_transport=-20.0),  # 2 - 4 < 0
        replace(gens[1], fuel_transport=-1.0),  # 1 - 0.2 >= 0
        replace(gens[2], fuel_transport=-50.0),  # not sunk
    )
    with caplog.at_level(logging.INFO, logger=LNG.logger.name):
        out = LNG.clamp_sunk_gas_costs(gens, frozenset({"GAS"}))
    assert out[0].vom_charge + fuel_transport_per_mwh(out[0]) == pytest.approx(0.0)
    assert out[1] is gens[1] and out[2] is gens[2]
    assert "clamped to 0" in caplog.text


def test_offline_group_does_not_keep_pondage_alive(tmp_path: Path) -> None:
    """A pondage shared with an OFFLINE group is still a charge-source orphan.

    CEN's ALFALFAL feeds the online BAT_ALFALFAL_VR2 and is also linked to
    the offline BAT_ALFALFAL_VR1, whose generators never dispatch.  Counting
    those as live kept ALFALFAL a turbine on an inflow-less Junction (no
    generation possible); the plant must be a plain generator instead.
    """
    kit = Model(lng=False)
    kit.gen("BAT_Y", cap=10.0)
    kit.gen("BAT_Y_LOAD", cap=10.0)
    kit.storage("BAT_Y", Max_Volume=40.0, Initial_Volume=2.0)
    kit.head("BAT_Y", "BAT_Y")
    kit.head("BAT_Y_LOAD", "X")
    kit.tail("BAT_Y_LOAD", "BAT_Y")
    kit.waterway("Loss_BAT_Y_GEN", "BAT_Y")
    kit.waterway("Loss_BAT_Y_LOAD", "X")
    kit.waterway("Act_BAT_Y_CSFLW", "X", "BAT_Y")
    kit.con("BAT_Y_GenLosses")
    kit.term("Generator", "BAT_Y", "BAT_Y_GenLosses", "Generation Coefficient", 0.05)
    kit.term("Waterway", "Loss_BAT_Y_GEN", "BAT_Y_GenLosses", "Flow Coefficient", -1.0)
    kit.con("BAT_Y_LoadLosses")
    kit.term(
        "Generator", "BAT_Y_LOAD", "BAT_Y_LoadLosses", "Generation Coefficient", 1.0
    )
    kit.term(
        "Waterway", "Loss_BAT_Y_LOAD", "BAT_Y_LoadLosses", "Flow Coefficient", -1.0
    )
    gens = (
        _gen("X", 180.0),
        _gen("BAT_X", 137.38),
        _gen("BAT_X_LOAD", 137.38),
        _gen("BAT_Y", 0.0),
        _gen("BAT_Y_LOAD", 0.0),
    )
    models = P._prepare_storage_models(kit.db, _tank_bundle(tmp_path), gens, "storage")
    assert [b.name for b in models.bess_batteries] == ["BAT_X"]
    assert "offline" in models.skip_constraints["BAT_Y_GenLosses"]
    assert models.orphan_storages == frozenset({"X"})
    # Online BAT_Y_LOAD drawing from X does keep the pondage alive.
    live = (*gens[:3], _gen("BAT_Y", 10.0), _gen("BAT_Y_LOAD", 10.0))
    models = P._prepare_storage_models(kit.db, _tank_bundle(tmp_path), live, "storage")
    assert "X" not in models.orphan_storages
