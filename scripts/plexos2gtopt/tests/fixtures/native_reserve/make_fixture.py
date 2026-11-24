# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Regenerate the ``native_reserve`` mini PLEXOS bundle (DBSEN_PRGDIARIO.xml).

A one-node CEN-PCP-shaped system for ``--reserve-model native``:

* ``COAL`` (250 MW, 40 $/MWh, committed, H·S 500 MWs), ``GAS`` (150 MW,
  80 $/MWh, committed, H·S 300 MWs), ``PV1`` (100 MW, 0 $/MWh), BESS
  ``BAT1`` (50 MW / 200 MWh);
* ``Generation_SEN`` / ``Inertia_SEN`` / ``MaxInjection_SEN`` /
  ``MaxLoad_SEN`` / ``limited_generation_group`` decision variables and
  their defining rows, as in CEN PCP;
* ``CPF_Up10Calculation_I`` (with a DATED coefficient set on 2026-11-07
  overlapping an older open-ended one), ``CPF_Up5Calculation``,
  ``CPF_DownCalculation`` and the three ``*MinProvision`` rows;
* ``CSF_UpMinProvision`` / ``CTF_UpMinProvision`` provision-sum rows, a
  ``CTFOFF_GAS`` row and a ``SpecialCPFNorth`` row (+ its dated
  ``_Definition``) that ship ``Include in ST Schedule = 0``;
* PLEXOS Sense 0 / unset (equality) on the ``*MinProvision`` rows, a
  priced ``CPF10s_Slack``, the ``CSF_RS_Def`` definition with its
  ``MAXCSF_COAL`` cap;
* ``InertiaNRMin_D`` (timesliced RHS, ``InertiaGuacolda2`` credit through
  ``InertiaCommitGuacolda2``) / ``InertiaNRMin_N`` over
  ``Inertia_Calculation_e2``;
* the BESS SSCC rows of ``BAT1`` (``UP/DOWNStorageBound``,
  ``CF_LOAD/GEN_COMP`` over a ``BAT1_AUX`` battery), per-reserve
  ``Max Response`` / ``Max Charge Response`` (scalar and a ``CFdata``
  file) and a dated ``Capacity``; ``SSCC_Activation_BESS.csv`` and
  ``CFdata/CSF/CSF_BAT1_MRU.csv`` and the CSF_RS ``Res_Requirement.csv``
  profile sit next to the XML.

Run ``python make_fixture.py`` from this directory to rewrite the XML.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))

# pylint: disable=wrong-import-position
from plexos2gtopt.plexos_xml import NS, PlexosDb  # noqa: E402
from plexos2gtopt.tests.cov_parsers_kit import DbKit  # noqa: E402

GE, LE, EQ = 1.0, -1.0, 0.0  # PLEXOS Sense codes (>=, <=, =)
NO_LIMIT = 1e30
#: CEN's 2026 day/night timeslice of ``InertiaNRMin_D`` (November: H9-19).
DAY_SLICE = "M4-9,H10-17;M1-3,H9-19;M10-12,H9-19"


def build() -> PlexosDb:  # pylint: disable=too-many-locals,too-many-statements
    """The mini CEN-PCP-shaped database."""
    k = DbKit()
    node = k.obj("Node", "BUS1")
    k.sysprop("Node", node, "Load", 300.0)
    gens = {}
    for name, cap, price in (
        ("COAL", 250.0, 40.0),
        ("GAS", 150.0, 80.0),
        ("PV1", 100.0, 0.0),
    ):
        gid = k.obj("Generator", name)
        gens[name] = gid
        k.sysprop("Generator", gid, "Max Capacity", cap)
        k.sysprop("Generator", gid, "Units", 1.0)
        k.sysprop("Generator", gid, "Offer Price", price)
        k.rel("Generator", gid, "Node", node, coll_name="Nodes")
        if name != "PV1":
            k.sysprop("Generator", gid, "Min Up Time", 2.0)
            k.sysprop("Generator", gid, "Min Stable Level", 0.4 * cap)
    bat = k.obj("Battery", "BAT1")
    for pname, val in (
        ("Max Power", 50.0),
        ("Capacity", 200.0),
        ("Initial SoC", 50.0),
        ("Charge Efficiency", 95.0),
        ("Discharge Efficiency", 95.0),
    ):
        k.sysprop("Battery", bat, pname, val)
    # Dated maintenance de-rating on the horizon day.
    k.sysprop(
        "Battery",
        bat,
        "Capacity",
        150.0,
        date_from=datetime(2026, 11, 7),
        date_to=datetime(2026, 11, 7),
    )
    k.rel("Battery", bat, "Node", node, coll_name="Nodes")
    aux = k.obj("Battery", "BAT1_AUX")
    for pname, val in (("Max Power", 1000.0), ("Capacity", 99999.0)):
        k.sysprop("Battery", aux, pname, val)
    k.rel("Battery", aux, "Node", node, coll_name="Nodes")

    rsv = {}
    for name, rtype in (
        ("CPF_RS", 3.0),
        ("CPF_LW", 4.0),
        ("CSF_RS", 3.0),
        ("CTF_RS", 5.0),
    ):
        rid = k.obj("Reserve", name)
        rsv[name] = rid
        k.sysprop("Reserve", rid, "Type", rtype)
        if name == "CSF_RS":  # a Min Provision PLEXOS does not enforce
            k.sysprop("Reserve", rid, "Min Provision", 18.0)
        for g in ("COAL", "GAS"):
            k.rel("Reserve", rid, "Generator", gens[g], coll_name="Generators")
    # BESS SSCC reserves: Max (Charge) Response scalar or from a data file.
    for name, rtype, resp, chg, text in (
        ("CPF_RS_BESS", 3.0, 20.0, NO_LIMIT, None),
        ("CSF_RS_BESS", 3.0, NO_LIMIT, NO_LIMIT, "CFdata\\CSF\\CSF_BAT1_MRU.csv"),
        ("CSF_LW_BESS", 4.0, NO_LIMIT, NO_LIMIT, None),
    ):
        rid = k.obj("Reserve", name)
        rsv[name] = rid
        k.sysprop("Reserve", rid, "Type", rtype)
        mid = k.rel("Reserve", rid, "Battery", bat, coll_name="Batteries")
        coll = k.coll("Reserve", "Battery", "Batteries")
        k.data(mid, k.prop(coll, "Max Response"), resp, text=text)
        k.data(mid, k.prop(coll, "Max Charge Response"), chg, text=text)

    dvs = {}
    for name, lb, ub, cost in (
        ("Generation_SEN", None, None, None),
        ("Inertia_SEN", None, None, None),
        ("MaxInjection_SEN", 120.0, 400.0, 0.01),
        ("MaxLoad_SEN", 20.0, 400.0, None),
        ("limited_generation_group", None, None, None),
        ("CPF10s_Requirement", None, None, 10.0),
        ("CPF10s_VoRS", None, None, 10000.0),
        ("CPF5mUp_Requirement", None, None, 10.0),
        ("CPF5mUp_VoRS", None, None, 10000.0),
        ("CPF5mDown_Requirement", None, None, 10.0),
        ("CPF5mDown_VoRS", None, None, 10000.0),
        ("CPF10s_Slack", None, None, 1.0),
        ("CTF_VoRS", None, None, 1000.0),
        ("CSF_RS_Requirement", 0.0, None, None),
        ("Inertia_NR", None, None, None),
        ("InertiaGuacolda2", 0.0, 1.0, None),
        ("SpecialCPFNorth_Requirement", None, None, None),
        ("CPF_North_Tx", 0.0, None, 10000.0),
    ):
        did = k.obj("Decision Variable", name)
        dvs[name] = did
        if lb is not None:
            k.sysprop("Decision Variable", did, "Lower Bound", lb)
        if ub is not None:
            k.sysprop("Decision Variable", did, "Upper Bound", ub)
        if cost is not None:
            k.sysprop("Decision Variable", did, "Objective Function Coefficient", cost)

    def con(
        name: str, sense: float | None, rhs: float, *, include_st: float = 1.0
    ) -> int:
        cid = k.obj("Constraint", name)
        if sense is not None:  # unset Sense = PLEXOS equality
            k.sysprop("Constraint", cid, "Sense", sense)
        k.sysprop("Constraint", cid, "RHS", rhs)
        k.sysprop("Constraint", cid, "Include in ST Schedule", include_st)
        return cid

    mems: dict[tuple[str, int, int], int] = {}

    def term(
        parent: str, pid: int, cid: int, prop: str, coef: float, **kw: object
    ) -> None:
        key = (parent, pid, cid)
        if key not in mems:
            mems[key] = k.mem(parent, "Constraint", pid, cid, coll_name="Constraints")
        mid = mems[key]
        coll = k.coll(parent, "Constraint", "Constraints")
        k.data(mid, k.prop(coll, prop), coef, **kw)  # type: ignore[arg-type]

    def dv(cid: int, name: str, coef: float, **kw: object) -> None:
        term("Decision Variable", dvs[name], cid, "Value Coefficient", coef, **kw)

    def gen(cid: int, name: str, prop: str, coef: float) -> None:
        term("Generator", gens[name], cid, prop, coef)

    # Generation_SEN = (Σ gen + discharge − charge) / 1000
    c = con("Generation_Calculation", GE, 0.0)
    for g in gens:
        gen(c, g, "Generation Coefficient", 1.0)
    term("Battery", bat, c, "Generation Coefficient", 1.0)
    term("Battery", bat, c, "Load Coefficient", -1.0)
    dv(c, "Generation_SEN", -1000.0)
    # Inertia_SEN = (Σ H·S·u + 100) / 1000
    c = con("Inertia_Calculation_e1", GE, -100.0)
    gen(c, "COAL", "Units Generating Coefficient", 500.0)
    gen(c, "GAS", "Units Generating Coefficient", 300.0)
    dv(c, "Inertia_SEN", -1000.0)
    c = con("InertiaGlobalMin_New", GE, 0.2)
    dv(c, "Inertia_SEN", 1.0)
    # limited_generation_group >= PV1 − BAT1 charge
    c = con("limited_generation_calculation", GE, 0.0)
    gen(c, "PV1", "Generation Coefficient", -1.0)
    term("Battery", bat, c, "Load Coefficient", 1.0)
    dv(c, "limited_generation_group", 1.0)
    # Contingencies
    for g, off in (("COAL", 8.4), ("GAS", 0.0)):
        c = con(f"{g}_Comparison", GE, off)
        gen(c, g, "Generation Coefficient", -1.0)
        dv(c, "limited_generation_group", -1.0)
        dv(c, "MaxInjection_SEN", 1.0)
    c = con("BAT_BAT1_Comparison", GE, 0.0)
    term("Battery", bat, c, "Load Coefficient", -1.0)
    dv(c, "MaxLoad_SEN", 1.0)

    # CPF10s: dated coefficient set on 2026-11-07 (overrides an older
    # open-ended set from 2026-01-01 and the undated base).
    c = con("CPF_Up10Calculation_I", GE, 50.0)
    dv(c, "CPF10s_Requirement", 1.0)
    dv(c, "Generation_SEN", 20.0)
    dv(c, "Generation_SEN", 30.0, date_from=datetime(2026, 1, 1))
    dv(
        c,
        "Generation_SEN",
        36.6,
        date_from=datetime(2026, 11, 7),
        date_to=datetime(2026, 11, 7),
    )
    dv(c, "Inertia_SEN", 12.3)
    dv(c, "MaxInjection_SEN", -1.25)
    c = con("CPF_Up10Calculation_II", GE, 30.0)
    dv(c, "CPF10s_Requirement", 1.0)
    dv(c, "Generation_SEN", 10.0)
    c = con("CPF_Up10MinProvision", EQ, 0.0)
    gen(c, "COAL", "Regulation Raise Reserve Provision Coefficient", 0.9)
    gen(c, "GAS", "Regulation Raise Reserve Provision Coefficient", 1.0)
    dv(c, "CPF10s_VoRS", 1.0)
    dv(c, "CPF10s_Requirement", -1.0)
    dv(c, "CPF10s_Slack", -1.0)

    c = con("CPF_Up5Calculation", GE, 4.125)
    dv(c, "CPF5mUp_Requirement", 1.0)
    dv(c, "Generation_SEN", 10.75)
    dv(c, "MaxInjection_SEN", -1.0912)
    c = con("CPF_UpMinProvision", EQ, 0.0)
    gen(c, "COAL", "Regulation Raise Reserve Provision Coefficient", 0.7)
    gen(c, "GAS", "Regulation Raise Reserve Provision Coefficient", 1.0)
    dv(c, "CPF5mUp_VoRS", 1.0)
    dv(c, "CPF5mUp_Requirement", -1.0)

    c = con("CPF_DownCalculation", GE, -8.0)
    dv(c, "CPF5mDown_Requirement", 1.0)
    dv(c, "MaxLoad_SEN", -0.96)
    dv(c, "Generation_SEN", 4.0)
    c = con("CPF_DownMinProvision", EQ, 0.0)
    gen(c, "COAL", "Regulation Lower Reserve Provision Coefficient", 0.5)
    gen(c, "GAS", "Regulation Lower Reserve Provision Coefficient", 1.0)
    dv(c, "CPF5mDown_VoRS", 1.0)
    dv(c, "CPF5mDown_Requirement", -1.0)

    # As in CEN PCP the CSF rows sum whole Reserve provisions (CSF_RS of
    # COAL / GAS plus CSF_RS_BESS of BAT1); the requirement is the
    # Res_Requirement.csv profile of CSF_RS.
    c = con("CSF_UpMinProvision", None, 20.0)
    term("Reserve", rsv["CSF_RS"], c, "Provision Coefficient", 1.0)
    term("Reserve", rsv["CSF_RS_BESS"], c, "Provision Coefficient", 1.0)
    # CSF_RS_Requirement := Σ CSF provision (a definition), capped per unit.
    c = con("CSF_RS_Def", None, 0.0)
    term("Reserve", rsv["CSF_RS"], c, "Provision Coefficient", 1.0)
    term("Reserve", rsv["CSF_RS_BESS"], c, "Provision Coefficient", 1.0)
    dv(c, "CSF_RS_Requirement", -1.0)
    c = con("MAXCSF_COAL", LE, -5.0)
    gen(c, "COAL", "Regulation Raise Reserve Provision Coefficient", 1.0)
    dv(c, "CSF_RS_Requirement", -1.0)
    c = con("CTF_UpMinProvision", GE, 15.0)
    gen(c, "COAL", "Replacement Reserve Provision Coefficient", 1.0)
    gen(c, "GAS", "Replacement Reserve Provision Coefficient", 1.0)
    dv(c, "CTF_VoRS", 1.0)
    c = con("CTFOFF_GAS", LE, 0.0)
    gen(c, "GAS", "Replacement Reserve Provision Coefficient", 1.0)
    # ST-excluded northern-corridor row: must never be converted.
    c = con("SpecialCPFNorth", EQ, 0.0, include_st=0.0)
    gen(c, "COAL", "Regulation Raise Reserve Provision Coefficient", 1.0)
    dv(c, "SpecialCPFNorth_Requirement", -1.0)
    c = con("SpecialCPFNorth_Definition", GE, 0.0, include_st=0.0)
    k.sysprop(
        "Constraint",
        c,
        "RHS",
        7.0,
        date_from=datetime(2026, 11, 7),
        date_to=datetime(2026, 11, 7),
    )
    dv(c, "SpecialCPFNorth_Requirement", 1.0)
    dv(c, "CPF_North_Tx", -1.0)

    # Northern inertia minima (InertiaNRMin_D/_N, Guacolda credit).
    c = con("Inertia_Calculation_e2", GE, 0.0)
    gen(c, "COAL", "Units Generating Coefficient", 500.0)
    gen(c, "GAS", "Units Generating Coefficient", 300.0)
    dv(c, "Inertia_NR", -1000.0)
    c = con("InertiaNRMin_D", GE, 0.0)
    k.sysprop("Constraint", c, "RHS", 700.0, timeslice=DAY_SLICE)
    gen(c, "COAL", "Units Generating Coefficient", 500.0)
    gen(c, "GAS", "Units Generating Coefficient", 300.0)
    dv(c, "InertiaGuacolda2", 200.0)
    c = con("InertiaNRMin_N", GE, 300.0)
    gen(c, "COAL", "Units Generating Coefficient", 500.0)
    gen(c, "GAS", "Units Generating Coefficient", 300.0)
    c = con("InertiaCommitGuacolda2", GE, 0.0)
    gen(c, "COAL", "Units Generating Coefficient", 1.0)
    gen(c, "GAS", "Units Generating Coefficient", 1.0)
    dv(c, "InertiaGuacolda2", -2.0)

    # BESS SSCC rows of BAT1.
    c = con("UPStorageBound_BAT1", LE, 0.0)
    term("Battery", bat, c, "Regulation Raise Reserve Provision Coefficient", 1.0)
    term("Battery", bat, c, "Energy Coefficient", -0.95)
    c = con("DOWNStorageBound_BAT1", LE, 200.0)
    term("Battery", bat, c, "Energy Coefficient", 1.0)
    term("Battery", bat, c, "Regulation Lower Reserve Provision Coefficient", 0.95)
    for suffix, prop, coefs in (
        ("LOAD", "Load Coefficient", (("CSF_RS_BESS", 0.3), ("CPF_RS_BESS", 0.0))),
        ("GEN", "Generation Coefficient", (("CSF_LW_BESS", 0.3),)),
    ):
        c = con(f"BAT1_CF_{suffix}_COMP", None, 0.0)
        for rname, coef in coefs:
            term("Reserve", rsv[rname], c, "Provision Coefficient", coef)
        term("Battery", aux, c, prop, -1.0)

    # Horizon (start 2026-11-07, OLE serial).
    k.obj("Horizon", "Horizon")
    return k.reset_indexes()


def _add(parent: ET.Element, tag: str, **fields: object) -> None:
    el = ET.SubElement(parent, f"{NS}{tag}")
    for key, val in fields.items():
        if val is None:
            continue
        sub = ET.SubElement(el, f"{NS}{key}")
        sub.text = str(val)


def to_xml(db: PlexosDb, path: Path) -> None:
    """Serialise ``db`` as a PLEXOS ``MasterDataSet`` XML."""
    ET.register_namespace("", NS[1:-1])
    root = ET.Element(f"{NS}MasterDataSet")
    for cid, name in sorted(db.classes_by_id.items()):
        _add(root, "t_class", class_id=cid, name=name)
    for o in db.objects:
        _add(root, "t_object", object_id=o.object_id, class_id=o.class_id, name=o.name)
    for c in db.collections:
        _add(
            root,
            "t_collection",
            collection_id=c.collection_id,
            parent_class_id=c.parent_class_id,
            child_class_id=c.child_class_id,
            name=c.name,
        )
    for m in db.memberships:
        _add(
            root,
            "t_membership",
            membership_id=m.membership_id,
            collection_id=m.collection_id,
            parent_object_id=m.parent_object_id,
            child_object_id=m.child_object_id,
        )
    for p in db.properties:
        _add(
            root,
            "t_property",
            property_id=p.property_id,
            collection_id=p.collection_id,
            name=p.name,
        )
    for r in db.data_rows:
        _add(
            root,
            "t_data",
            data_id=r.data_id,
            membership_id=r.membership_id,
            property_id=r.property_id,
            value=r.value,
        )
        if r.date_from is not None:
            _add(root, "t_date_from", data_id=r.data_id, date=r.date_from.isoformat())
        if r.date_to is not None:
            _add(root, "t_date_to", data_id=r.data_id, date=r.date_to.isoformat())
        if r.timeslice:
            _add(root, "t_text", data_id=r.data_id, class_id=76, value=r.timeslice)
        elif r.data_id in db.text_for_data:
            _add(
                root,
                "t_text",
                data_id=r.data_id,
                class_id=1,
                value=db.text_for_data[r.data_id],
            )
    # Horizon Date From (OLE serial of 2026-11-07).
    horizon = next(o for o in db.objects if o.name == "Horizon")
    _add(
        root,
        "t_attribute",
        attribute_id=1,
        class_id=db.classes_by_name["Horizon"],
        name="Date From",
    )
    serial = (datetime(2026, 11, 7) - datetime(1899, 12, 30)).days
    _add(
        root,
        "t_attribute_data",
        object_id=horizon.object_id,
        attribute_id=1,
        value=float(serial),
    )
    ET.indent(root)
    path.write_text(
        '<?xml version="1.0" standalone="yes"?>\n'
        + ET.tostring(root, encoding="unicode")
        + "\n",
        encoding="utf-8",
    )


#: ``Res_Requirement.csv`` profile of CSF_RS (MW per hour).
CSF_REQUIREMENT = [20.0 if h <= 12 else 25.0 for h in range(1, 25)]


def write_csvs(root: Path) -> None:
    """Activation %, the CSF MRU file and the CSF_RS requirement profile."""
    req = ["NAME,PATTERN,VALUE"]
    req += [f'CSF_RS,"TR_1,H{h}",{v:g}' for h, v in enumerate(CSF_REQUIREMENT, 1)]
    (root / "Res_Requirement.csv").write_text("\n".join(req) + "\n", encoding="utf-8")
    zones = ("CPF_RS_BESS", "CSF_RS_BESS", "CSF_LW_BESS")
    lines = ["Year,Pattern," + ",".join(zones)]
    for h in range(1, 24, 2):
        lines.append(f"2026,H{h}-{h + 1},50,40,60")
    (root / "SSCC_Activation_BESS.csv").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    cf = root / "CFdata" / "CSF"
    cf.mkdir(parents=True, exist_ok=True)
    rows = ["YEAR,MONTH,DAY,PERIOD,BAND,VALUE"]
    rows += [f"2026,11,7,{p},1,{30.0 if p <= 12 else 40.0}" for p in range(1, 25)]
    (cf / "CSF_BAT1_MRU.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    to_xml(build(), HERE / "DBSEN_PRGDIARIO.xml")
    write_csvs(HERE)
