"""Tests for :mod:`plexos2gtopt.aemo_isp_constraints`.

A small synthetic ISP-shaped XML (two sub-regions, one REZ wind farm, a
gas unit, an electrolyser purchaser and a handful of PLEXOS constraints)
exercises RHS resolution (dated rows, timeslices, Data File traces,
period pro-rating), capacity-term folding, sense / penalty mapping,
time scaling, REZ-bus conversion, purchasers / hydrogen and the
``--legacy`` switch.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from plexos2gtopt import aemo_isp_constraints as isp_c
from plexos2gtopt.aemo_isp import IspCaseOptions, build_dispatch_case
from plexos2gtopt.tests.test_aemo_isp import _halfhourly_csv, _Xml


class _CXml(_Xml):
    """``_Xml`` plus categories and non-System membership data."""

    def __init__(self) -> None:
        self.cats: dict[tuple[str, str], int] = {}
        self.cprops: dict[tuple[int, str], int] = {}
        super().__init__()

    def cat(self, cls: str, name: str) -> int:
        key = (cls, name)
        if key not in self.cats:
            cid = len(self.cats) + 1
            self.cats[key] = cid
            self._row("t_category", category_id=cid, class_id=self.cls(cls), name=name)
        return self.cats[key]

    def cobj(self, cls: str, name: str, category: str) -> int:
        cat = self.cat(cls, category)
        oid = self.next["obj"]
        self.next["obj"] += 1
        self._row(
            "t_object",
            object_id=oid,
            class_id=self.cls(cls),
            name=name,
            category_id=cat,
        )
        self.objects[f"{cls}:{name}"] = oid
        self.sysmem[oid] = self.member("System", cls, cls + "s", self.system, oid)
        return oid

    def data(
        self,
        cls: str,
        oid: int,
        prop: str,
        value: float,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        tags: tuple[int, ...] = (),
        text: str | None = None,
    ) -> int:
        did = super().data(
            cls, oid, prop, value, date_from=date_from, tags=tags, text=text
        )
        if date_to:
            self._row("t_date_to", data_id=did, date=date_to)
        return did

    def mdata(self, pcls: str, coll: str, mid: int, prop: str, value: float) -> None:
        """Data row on membership ``mid`` of collection ``pcls.coll``."""
        cid = self.colls[(pcls, coll)]
        key = (cid, prop)
        if key not in self.cprops:
            pid = self.next["prop"]
            self.next["prop"] += 1
            self.cprops[key] = pid
            self._row("t_property", property_id=pid, collection_id=cid, name=prop)
        did = self.next["data"]
        self.next["data"] += 1
        self._row(
            "t_data",
            data_id=did,
            membership_id=mid,
            property_id=self.cprops[key],
            value=value,
        )

    def coef(self, pcls: str, parent: int, con: int, prop: str, value: float) -> None:
        """Coefficient ``prop`` of ``parent`` in constraint ``con``."""
        mid = self.member(pcls, "Constraint", "Constraints", parent, con)
        cid = self.colls[(pcls, "Constraints")]
        key = (cid, prop)
        if key not in self.cprops:
            pid = self.next["prop"]
            self.next["prop"] += 1
            self.cprops[key] = pid
            self._row("t_property", property_id=pid, collection_id=cid, name=prop)
        did = self.next["data"]
        self.next["data"] += 1
        self._row(
            "t_data",
            data_id=did,
            membership_id=mid,
            property_id=self.cprops[key],
            value=value,
        )


def _constraint(
    x: _CXml,
    name: str,
    category: str,
    *,
    sense: int | None = None,
    penalty: float | None = None,
    **rhs: float,
) -> int:
    con = x.cobj("Constraint", name, category)
    if sense is not None:
        x.data("Constraint", con, "Sense", sense)
    if penalty is not None:
        x.data("Constraint", con, "Penalty Price", penalty)
    for prop, value in rhs.items():
        x.data("Constraint", con, prop.replace("_", " "), value)
    return con


@pytest.fixture(name="ccase")
def _ccase(tmp_path: Path) -> tuple[Path, Path]:
    return _make_ccase(tmp_path)


def _make_ccase(
    tmp_path: Path, gt_aux: float = 0.0, odp_aug: bool = False
) -> tuple[Path, Path]:
    """Write the synthetic model; ``gt_aux`` = GT1 ``Aux Incr`` [%].

    ``odp_aug`` adds two network augmentations to ``ExportLimit_S1``: a
    discrete ``S1 Option 1`` (10 MW) and a linear one (up to 200 MW).
    """
    x = _CXml()
    nth = x.obj("Node", "NTH")
    sth = x.obj("Node", "STH")
    for node, name in ((nth, "NTH"), (sth, "STH")):
        df = x.data_file(name, f"Traces\\demand\\{name}_dem.csv")
        x.data("Node", node, "Load", 0)
        x.data("Node", node, "Load", 0, tags=(df,))
    summer = x.obj("Timeslice", "Summer")
    x.data("Timeslice", summer, "Include", -1, text="M1-3,11,12")
    # gas unit at NTH: 100 MW, HR 10 GJ/MWh, fuel 5 $/GJ, VOM 2, aux 0
    gas = x.obj("Fuel", "Gas")
    x.data("Fuel", gas, "Price", 5.0)
    gt = x.obj("Generator", "GT1")
    x.member("Generator", "Node", "Nodes", gt, nth)
    x.member("Generator", "Fuel", "Fuels", gt, gas)
    x.data("Generator", gt, "Units", 1)
    x.data("Generator", gt, "Max Capacity", 100)
    x.data("Generator", gt, "Heat Rate", 10)
    x.data("Generator", gt, "VO&M Charge", 2)
    if gt_aux:
        x.data("Generator", gt, "Aux Incr", gt_aux)
    # REZ wind farm at STH, 80 MW flat
    wind = x.obj("Generator", "WF1")
    x.member("Generator", "Node", "Nodes", wind, sth)
    x.data("Generator", wind, "Units", 1)
    x.data("Generator", wind, "Max Capacity", 80)
    x.data("Generator", wind, "Rating", 80)
    # line NTH → STH
    line = x.obj("Line", "NTH-STH")
    x.member("Line", "Node", "Node From", line, nth)
    x.member("Line", "Node", "Node To", line, sth)
    x.data("Line", line, "Units", 1)
    x.data("Line", line, "Max Flow", 300)
    x.data("Line", line, "Min Flow", -300)
    # electrolyser purchaser at STH (20 MW, bid 0) + a fixed-load one
    ely = x.obj("Purchaser", "ELY1")
    x.member("Purchaser", "Node", "Nodes", ely, sth)
    x.data("Purchaser", ely, "Units", 1)
    x.data("Purchaser", ely, "Max Load", 20)
    x.data("Purchaser", ely, "Bid Price", 0)
    fixed = x.obj("Purchaser", "FIX1")
    x.member("Purchaser", "Node", "Nodes", fixed, sth)
    x.data("Purchaser", fixed, "Units", 1)
    x.data("Purchaser", fixed, "Fixed Load", 2)

    # REZ export limit: WF1 ≤ 50 (60 in summer) → radial line
    rez = _constraint(x, "ExportLimit_S1", "REZ Network Export Constraints", RHS=50)
    x.data("Constraint", rez, "RHS", 60, tags=(summer,))
    x.coef("Generator", wind, rez, "Generation Coefficient", 1.0)
    if odp_aug:
        for aname, mub in (("S1_S1 Option 1", 10), ("S1_Linear Augmentation 1", 200)):
            aug = x.cobj("Generator", aname, "Network Augmentation Single REZ")
            x.data("Generator", aug, "Units", 0)
            x.data("Generator", aug, "Max Capacity", 1)
            x.data(
                "Generator",
                aug,
                "Max Units Built",
                mub,
                date_from="2026-07-01T00:00:00",
            )
            x.coef("Generator", aug, rez, "Installed Capacity Coefficient", -1.0)
    # group constraint, soft, dated RHS: GT1 + flow ≤ 120 from 2027
    grp = _constraint(x, "Group1", "Group Constraints", sense=-1, penalty=500)
    x.data("Constraint", grp, "RHS", 999, date_from="2025-07-01T00:00:00")
    x.data("Constraint", grp, "RHS", 120, date_from="2027-01-01T00:00:00")
    x.coef("Generator", gt, grp, "Generation Coefficient", 1.0)
    x.coef("Line", line, grp, "Flow Coefficient", 1.0)
    # operational + capacity: GT1 − 0.5 · installed capacity ≤ 0 → GT1 ≤ 50
    opc = _constraint(x, "OpCap1", "Hybrid Constraints", sense=-1, RHS=0)
    x.coef("Generator", gt, opc, "Generation Coefficient", 1.0)
    x.coef("Generator", gt, opc, "Installed Capacity Coefficient", -0.5)
    # ≥ constraint with a summer timeslice RHS
    mng = _constraint(x, "MinGen1", "Min Gen Constraints", sense=1)
    x.data("Constraint", mng, "RHS", 0)
    x.data("Constraint", mng, "RHS", 10, tags=(summer,))
    x.coef("Generator", gt, mng, "Generation Coefficient", 1.0)
    # monthly energy cap: 31 GWh in January → 3 000 MWh for 3 days
    mon = _constraint(x, "Month1", "Energy Constraints", sense=-1, RHS_Month=31)
    x.coef("Generator", gt, mon, "Generation Coefficient", 1.0)
    # soft period budgets: penalties are $ per RHS unit (GWh / TJ)
    smon = _constraint(
        x, "SoftMonth1", "Energy Constraints", sense=-1, penalty=2000, RHS_Month=31
    )
    x.coef("Generator", gt, smon, "Generation Coefficient", 1.0)
    sday = _constraint(
        x, "SoftDay1", "Energy Constraints", sense=-1, penalty=3000, RHS_Day=1.5
    )
    x.coef("Generator", gt, sday, "Generation Coefficient", 1.0)
    # gas zone daily offtake: 2 TJ/day
    gz = _constraint(x, "Gas Zone 1", "Gas Constraints", sense=-1, RHS_Day=2)
    x.coef("Fuel", gas, gz, "Offtake Coefficient", 1.0)
    # H2 domestic consumption: 0.744 GWh in January → 72 MWh_e for 3 days
    h2 = _constraint(
        x,
        "STH H2 Domestic Consumption",
        "H2 Subregion Domestic Consumption",
        sense=0,
        RHS_Month=0.744,
    )
    x.coef("Purchaser", ely, h2, "Load Coefficient", 1.0)
    # yearly policy target (fiscal year), ≥
    yr = _constraint(x, "Year1", "Policies", sense=1, RHS_Year=365)
    x.coef("Generator", wind, yr, "Generation Coefficient", 1.0)
    # forward-flow limit (convex) and a ≥ use of back flow (non-convex)
    fwd = _constraint(x, "FwdLim1", "Group Constraints", sense=-1, RHS=200)
    x.coef("Line", line, fwd, "Flow Forward Coefficient", 1.0)
    bad = _constraint(x, "BackMin1", "Group Constraints", sense=1, RHS=5)
    x.coef("Line", line, bad, "Flow Back Coefficient", 1.0)
    # emissions: GT1 0.5 t/MWh; a one-year and a multi-year budget (kt)
    co2 = x.obj("Emission", "CO2")
    mid = x.member("Emission", "Generator", "Generators", co2, gt)
    x.mdata("Emission", "Generators", mid, "Production Rate", 500.0)
    for cname, value, d_to in (
        ("Budget 1y", 365.0, "2027-06-30T00:00:00"),
        ("Budget 4y", 1461.0, "2030-06-30T00:00:00"),
    ):
        b = x.cobj("Constraint", cname, "Emissions Budget")
        x.data(
            "Constraint",
            b,
            "RHS Custom",
            value,
            date_from="2026-07-01T00:00:00",
            date_to=d_to,
        )
        x.coef("Emission", co2, b, "Production Coefficient", 0.001)
    # capacity-only (build) constraint → skipped
    bld = _constraint(x, "Build1", "Resource Limits", RHS=1000)
    x.coef("Generator", wind, bld, "Capacity Built Coefficient", 1.0)
    # disabled constraint
    off = _constraint(x, "Off1", "Group Constraints", RHS=1)
    x.data("Constraint", off, "Include in LT Plan", 0)
    x.coef("Generator", gt, off, "Generation Coefficient", 1.0)

    xml = tmp_path / "model" / "ISP C Model.xml"
    xml.parent.mkdir(parents=True)
    x.write(xml)
    start = date(2027, 1, 1)
    for name, val in (("NTH", 60.0), ("STH", 30.0)):
        _halfhourly_csv(
            xml.parent / "Traces" / "demand" / f"{name}_dem.csv",
            start,
            31,
            lambda k, h, v=val: v,
        )
    traces = tmp_path / "traces"
    traces.mkdir()
    return xml, traces


#: Synthetic electrolyser consumption [kWh/kg] for the fixtures (the real
#: value comes from the AEMO IASR workbook, which is not redistributable).
KWH_PER_KG = 50.0


def _build(xml: Path, traces: Path, **kw: object):
    kw.setdefault("electrolyser_kwh_per_kg", KWH_PER_KG)
    opts = IspCaseOptions(
        xml=xml,
        trace_roots=[xml.parent, traces],
        start=date(2027, 1, 18),
        days=3,
        name="t",
        isp=isp_c.ConstraintOptions(**kw),  # type: ignore[arg-type]
    )
    return build_dispatch_case(opts)


def _ucs(case) -> dict[str, dict]:
    return {
        uc["name"]: uc
        for uc in case.planning["system"].get("user_constraint_array", [])
    }


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_fiscal_year_and_month() -> None:
    assert isp_c.fiscal_year(date(2027, 1, 18)) == (date(2026, 7, 1), date(2027, 6, 30))
    assert isp_c.fiscal_year(date(2027, 7, 1)) == (date(2027, 7, 1), date(2028, 6, 30))
    assert isp_c.calendar_month(date(2027, 2, 10)) == (
        date(2027, 2, 1),
        date(2027, 2, 28),
    )


def test_sense_map() -> None:
    assert isp_c.SENSE_OP == {-1: "<=", 0: "=", 1: ">="}


# ---------------------------------------------------------------------------
# full conversion on the synthetic model
# ---------------------------------------------------------------------------


def test_groups_and_skips(ccase: tuple[Path, Path]) -> None:
    case = _build(*ccase)
    groups = case.stats["constraint_groups"]
    assert (
        groups["capacity_build"]["skipped: capacity / build condition (fixed fleet)"]
        == 1
    )
    assert groups["operational"]["skipped: disabled (Include in LT Plan = 0)"] == 1
    assert groups["fuel_offtake"]["converted: daily_sum"] == 1
    assert (
        groups["h2_consumption"]["converted: HydrogenDemand (pro-rated period RHS)"]
        == 1
    )


def test_dated_rhs_and_soft_penalty(ccase: tuple[Path, Path]) -> None:
    uc = _ucs(_build(*ccase))["isp_Group1"]
    # 2027 row wins over the 2025 row; Flow → signed line.flow
    assert uc["expression"].endswith("<= 120")
    assert 'generator("GT1").generation' in uc["expression"]
    assert 'line("NTH-STH").flow' in uc["expression"]
    assert uc["penalty"] == pytest.approx(500.0)


def test_capacity_term_folded(ccase: tuple[Path, Path]) -> None:
    uc = _ucs(_build(*ccase))["isp_OpCap1"]
    # 0 − (−0.5 × 1 unit × 100 MW) = 50, hard (no penalty)
    assert uc["expression"] == 'generator("GT1").generation <= 50'
    assert "penalty" not in uc


def test_timeslice_rhs_and_ge_sense(ccase: tuple[Path, Path]) -> None:
    uc = _ucs(_build(*ccase))["isp_MinGen1"]
    # January is in the Summer timeslice
    assert uc["expression"] == 'generator("GT1").generation >= 10'


def test_period_rhs_prorated(ccase: tuple[Path, Path]) -> None:
    uc = _ucs(_build(*ccase))["isp_Month1"]
    # 31 GWh × 1000 × 72 h / 744 h = 3000 MWh over the window
    assert uc["expression"].endswith("<= 3000")
    assert uc["scope"] == "stage"
    assert "sum{b in stage}" in uc["expression"]


def test_rhs_day_daily_sum(ccase: tuple[Path, Path]) -> None:
    uc = _ucs(_build(*ccase))["isp_Gas_Zone_1"]
    assert uc["daily_sum"] is True
    # aux 0: offtake = heat rate 10 GJ/MWh × sent-out generation
    assert uc["expression"] == '10 * generator("GT1").generation <= 2000'
    assert uc["constraint_type"] == "energy"
    gens = {g["name"]: g for g in _build(*ccase).planning["system"]["generator_array"]}
    assert gens["GT1"]["fuel"] == "Gas"
    assert gens["GT1"]["heat_rate"] == pytest.approx(10.0)


def test_fuel_offtake_uses_aux_adjusted_heat_rate(tmp_path: Path) -> None:
    """PLEXOS Heat Rate is per gross MWh; gtopt generation is sent out.

    With 10 % aux the gas offtake per sent-out MWh is 10 / 0.9 GJ — the
    same gross-to-sent-out factor the emission rates use.  The fuel cost
    (``heat_rate`` × price) is left unchanged.
    """
    case = _build(*_make_ccase(tmp_path, gt_aux=10.0))
    uc = _ucs(case)["isp_Gas_Zone_1"]
    assert uc["daily_sum"] is True
    assert uc["constraint_type"] == "energy"
    lhs, rhs = uc["expression"].split(" <= ")
    assert float(rhs) == pytest.approx(2000.0)  # 2 TJ → GJ
    coef, ref = lhs.split(" * ")
    assert ref == 'generator("GT1").generation'
    assert float(coef) == pytest.approx(10.0 / 0.9, rel=1e-6)
    gens = {g["name"]: g for g in case.planning["system"]["generator_array"]}
    # fuel cost uses the same sent-out heat rate as offtake / emissions
    assert gens["GT1"]["heat_rate"] == pytest.approx(10.0 / 0.9)


def test_period_rhs_and_penalty_scaled_together(ccase: tuple[Path, Path]) -> None:
    """PLEXOS period RHS are "(000)": GWh rows are built in MWh.

    The Penalty Price is $ per unit of violation in the RHS unit, so a
    $/GWh penalty becomes $/MWh (÷ 1000) whenever the RHS is × 1000;
    per-block ``RHS`` rows keep both unscaled.
    """
    ucs = _ucs(_build(*ccase))
    month = ucs["isp_SoftMonth1"]
    # 31 GWh × 1000 × 72 h / 744 h = 3000 MWh; 2000 $/GWh → 2 $/MWh
    assert month["expression"].endswith("<= 3000")
    assert month["penalty"] == pytest.approx(2.0)
    day = ucs["isp_SoftDay1"]
    # 1.5 GWh/day → 1500 MWh/day; 3000 $/GWh → 3 $/MWh
    assert day["daily_sum"] is True
    assert day["expression"].endswith("<= 1500")
    assert day["penalty"] == pytest.approx(3.0)
    # per-block RHS (MW) and its penalty ($/MW) are not rescaled
    assert ucs["isp_Group1"]["penalty"] == pytest.approx(500.0)


def test_rez_bus_and_radial_line(ccase: tuple[Path, Path]) -> None:
    case = _build(*ccase)
    sys_ = case.planning["system"]
    rez_buses = [b["name"] for b in sys_["bus_array"] if b["name"].startswith("REZ_")]
    assert len(rez_buses) == 1
    gens = {g["name"]: g for g in sys_["generator_array"]}
    assert gens["WF1"]["bus"] == rez_buses[0]
    line = next(ln for ln in sys_["line_array"] if ln.get("type") == "rez")
    assert {line["bus_a"], line["bus_b"]} == {rez_buses[0], "STH"}
    tab = case.schedules[("Line", "tmax_ab")].get(line["uid"])
    limit = tab if tab is not None else np.full(72, line["tmax_ab"])
    # the Summer timeslice row (60) wins over the untagged 50 in January
    assert np.allclose(limit, 60.0)
    assert not any("export" in n for n in _ucs(case))
    assert (
        case.stats["constraint_groups"]["operational"][
            "converted: REZ radial line (export limit)"
        ]
        == 1
    )


def test_hydrogen_needs_electrolyser_consumption(ccase: tuple[Path, Path]) -> None:
    """No built-in IASR value: H2 targets require the CLI value."""
    with pytest.raises(ValueError, match="--electrolyser-kwh-per-kg"):
        _build(*ccase, electrolyser_kwh_per_kg=None)


def test_purchasers_and_hydrogen_native(ccase: tuple[Path, Path]) -> None:
    case = _build(*ccase)
    sys_ = case.planning["system"]
    cc = sys_["carrier_converter_array"]
    assert [c["name"] for c in cc] == ["ELY1"]
    assert cc[0]["capacity"] == pytest.approx(20.0)
    assert cc[0]["efficiency"] == pytest.approx(33.33 / KWH_PER_KG, rel=1e-5)
    hd = sys_["hydrogen_demand_array"][0]
    # 0.744 GWh × 1000 × 72/744 = 72 MWh_e → × η MWh_LHV, equality
    e = 72.0 * 33.33 / KWH_PER_KG
    assert hd["min_energy"] == pytest.approx(e, rel=1e-5)
    assert hd["max_energy"] == pytest.approx(e, rel=1e-5)
    assert hd["hydrogen_node"] == cc[0]["to_node"]
    fixed = [d for d in sys_["demand_array"] if d.get("type") == "purchaser_fixed"]
    assert [d["name"] for d in fixed] == ["FIX1"]


def test_hydrogen_demand_mode(ccase: tuple[Path, Path]) -> None:
    case = _build(*ccase, h2_model="demand")
    sys_ = case.planning["system"]
    assert "carrier_converter_array" not in sys_
    ely = next(d for d in sys_["demand_array"] if d["name"] == "ELY1")
    assert ely["fcost"] == 0
    uc = next(u for u in _ucs(case).values() if "H2" in u["name"])
    assert 'demand("ELY1").load' in uc["expression"]
    assert "= 72" in uc["expression"]


def test_legacy_switch(ccase: tuple[Path, Path]) -> None:
    case = _build(
        *ccase,
        constraints=False,
        purchasers=False,
        emissions=False,
        fuels=False,
        rez_buses=False,
    )
    sys_ = case.planning["system"]
    assert not sys_.get("user_constraint_array")
    assert "carrier_converter_array" not in sys_
    assert [b["name"] for b in sys_["bus_array"]] == ["NTH", "STH"]
    gens = {g["name"]: g for g in sys_["generator_array"]}
    assert "fuel" not in gens["GT1"]
    assert gens["GT1"]["gcost"] == pytest.approx(10 * 5.0 + 2)


def test_directional_flow_decision_variable(ccase: tuple[Path, Path]) -> None:
    case = _build(*ccase)
    ucs = _ucs(case)
    assert ucs["isp_FwdLim1"]["expression"] == (
        'decision_variable("flow_fwd_NTH_STH").value <= 200'
    )
    dvs = case.planning["system"]["decision_variable_array"]
    assert [d["name"] for d in dvs] == ["flow_fwd_NTH_STH"]
    assert dvs[0]["lower_bound"] == 0.0
    assert ucs["def_flow_fwd_NTH_STH"]["expression"] == (
        'decision_variable("flow_fwd_NTH_STH").value - line("NTH-STH").flow >= 0'
    )
    assert "isp_BackMin1" not in ucs
    assert (
        case.stats["constraint_groups"]["operational"][
            "skipped: Flow Forward/Back term in a non-convex position"
        ]
        == 1
    )


def test_emission_sources_and_budgets(ccase: tuple[Path, Path]) -> None:
    sys_ = _build(*ccase).planning["system"]
    src = sys_["emission_source_array"]
    gt = [x for x in src if x["generator"] == "GT1"]
    assert gt and all(x["rate"] == pytest.approx(0.5) for x in gt)
    zones = {z["name"]: z for z in sys_["emission_zone_array"]}
    # long-period budgets are tracked only by default (no cap)
    assert "cap" not in zones["isp_Budget_1y"]
    assert "cap" not in zones["isp_Budget_4y"]
    assert "cap" not in zones["isp_co2_total"]
    prorated = _build(*ccase, long_periods="prorate").planning["system"]
    zp = {z["name"]: z for z in prorated["emission_zone_array"]}
    # 365 kt over 365 days → 3 kt for 3 days (t)
    assert zp["isp_Budget_1y"]["cap"] == pytest.approx(3000.0)
    assert zp["isp_Budget_4y"]["cap"] == pytest.approx(1461.0e3 * 3 / 1461)


def test_long_period_target_skipped(ccase: tuple[Path, Path]) -> None:
    case = _build(*ccase)
    assert "isp_Year1" not in _ucs(case)
    assert (
        case.stats["constraint_groups"]["operational"][
            "skipped: long-period target (> 1 month) not enforced in a week"
        ]
        == 1
    )
    uc = _ucs(_build(*ccase, long_periods="prorate"))["isp_Year1"]
    # 365 GWh over the fiscal year → 3 000 MWh for 3 days
    assert uc["expression"].endswith(">= 3000")
