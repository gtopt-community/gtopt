# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""A 7-day mini CEN-PCP-shaped PLEXOS bundle for the ``--horizon-days`` tests.

Written on the fly into a test directory (no committed XML):

* ``BUS1``: 100 MW static load, ``COAL`` (300 MW, 40 $/MWh), hydro ``HYD``
  (60 MW) on storage ``RES1`` (eini 500, emax 1000), BESS ``BAT1``
  (50 MW / 200 MWh, 50 % initial SoC, 1 cycle/day);
* ``BUS2``: an island holding BESS ``BAT_VR2`` (20 % SoC) whose only
  charge source, hydro ``ALF``, has no water (rating 0) -- the PCP
  2024-12-15 BAT_ALFALFAL_VR2 shape: with gtopt's default HARD
  daily-cycle close the battery cannot make up its self-discharge and
  the LP is infeasible;
* ``Hydro_StoWaterValues.csv``: an FCF boundary cut on ``RES1``;
* ``Fuel_MaxOfftakeWeek.csv``: a weekly cap on the ``COAL`` fuel;
* the Horizon object is named ``Mini_1H_7d`` (7-day horizon, starts
  2026-11-07), like CEN's ``Coordinador_diario_1H_7d``.

:func:`solution_tables` returns a matching fake PLEXOS solution (the
tables :class:`plexos2gtopt.plexos_solution.PlexosSolution` reads), so
``--horizon-end-targets plexos`` can be exercised without an ``.accdb``.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from plexos2gtopt.plexos_xml import PlexosDb
from plexos2gtopt.tests.cov_parsers_kit import DbKit

HORIZON_NAME = "Mini_1H_7d"
FULL_DAYS = 7

#: End-of-day PLEXOS states served by the fake solution.
RES1_END = {1: 480.0, 7: 420.0}
BAT1_END = {1: 90.0, 7: 100.0}


def _native_fixture() -> ModuleType:
    path = Path(__file__).resolve().parent / "fixtures/native_reserve/make_fixture.py"
    spec = importlib.util.spec_from_file_location("_native_make_fixture", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build() -> PlexosDb:
    """The mini database."""
    k = DbKit()
    k.obj("Horizon", "Horizon")
    bus1 = k.obj("Node", "BUS1")
    k.sysprop("Node", bus1, "Load", 100.0)
    bus2 = k.obj("Node", "BUS2")
    fuel = k.obj("Fuel", "Coal_A")
    k.sysprop("Fuel", fuel, "Price", 4.0)
    coal = k.obj("Generator", "COAL")
    for pname, val in (("Max Capacity", 300.0), ("Units", 1.0), ("Heat Rate", 10.0)):
        k.sysprop("Generator", coal, pname, val)
    k.rel("Generator", coal, "Node", bus1, coll_name="Nodes")
    k.rel("Generator", coal, "Fuel", fuel, coll_name="Fuels")
    res = k.obj("Storage", "RES1")
    for pname, val in (
        ("Max Volume", 1000.0),
        ("Min Volume", 0.0),
        ("Initial Volume", 500.0),
    ):
        k.sysprop("Storage", res, pname, val)
    for name, node, cap, storage in (
        ("HYD", bus1, 60.0, res),
        ("ALF", bus2, 10.0, None),
    ):
        gid = k.obj("Generator", name)
        k.sysprop("Generator", gid, "Max Capacity", cap)
        k.sysprop("Generator", gid, "Units", 1.0)
        k.rel("Generator", gid, "Node", node, coll_name="Nodes")
        if storage is not None:
            k.sysprop("Generator", gid, "Efficiency Incr", 1.0)
            k.mem("Generator", "Storage", gid, storage, coll_name="Head Storage")
    for name, node, cap in (("BAT1", bus1, 200.0), ("BAT_VR2", bus2, 50.0)):
        bat = k.obj("Battery", name)
        for pname, val in (
            ("Max Power", 50.0 if name == "BAT1" else 10.0),
            ("Capacity", cap),
            ("Charge Efficiency", 95.0),
            ("Discharge Efficiency", 95.0),
            ("Max Cycles Day", 1.0),
        ):
            k.sysprop("Battery", bat, pname, val)
        k.rel("Battery", bat, "Node", node, coll_name="Nodes")
    return k.reset_indexes()


def write_bundle(root: Path) -> Path:
    """Write the bundle (XML + CSVs) into ``root``; returns ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    xml = root / "DBSEN_PRGDIARIO.xml"
    _native_fixture().to_xml(build(), xml)
    text = xml.read_text(encoding="utf-8")
    # Rename the Horizon OBJECT (not the class) to carry the day count.
    text = re.sub(
        r"(<t_object>(?:(?!</t_object>).)*?<name>)Horizon(</name>)",
        rf"\g<1>{HORIZON_NAME}\g<2>",
        text,
        count=1,
        flags=re.S,
    )
    xml.write_text(text, encoding="utf-8")
    hours = range(1, 25)
    days = range(7, 7 + FULL_DAYS)
    soc = ["NAME,YEAR,MONTH,DAY,PERIOD,VALUE"]
    soc += ["BAT1,2026,11,7,1,50", "BAT_VR2,2026,11,7,1,20"]
    (root / "BESS_IniValue.csv").write_text("\n".join(soc) + "\n", encoding="utf-8")
    rating = ["NAME,YEAR,MONTH,DAY,PERIOD,BAND,VALUE"]
    rating += [f"ALF,2026,11,{d},{h},1,0" for d in days for h in hours]
    (root / "Gen_Rating.csv").write_text("\n".join(rating) + "\n", encoding="utf-8")
    wv = ["NAME,YEAR,MONTH,DAY,PERIOD,VALUE", "FCF,2026,11,7,1,1000000"]
    wv += ["RES1,2026,11,7,1,30"]
    (root / "Hydro_StoWaterValues.csv").write_text(
        "\n".join(wv) + "\n", encoding="utf-8"
    )
    cap = ["NAME,YEAR,MONTH,DAY,VALUE", "Coal_A,2026,11,7,700"]
    (root / "Fuel_MaxOfftakeWeek.csv").write_text(
        "\n".join(cap) + "\n", encoding="utf-8"
    )
    return root


def solution_tables(n_days: int = FULL_DAYS) -> dict[str, str]:
    """Fake PLEXOS solution tables (``csv_table_source`` input).

    Hourly ST blocks (period = hour).  ``RES1`` End Volume and ``BAT1``
    Energy follow :data:`RES1_END` / :data:`BAT1_END` at the day ends;
    ``Generation_SEN`` is 0.105 GW every hour (factor 1.05 over the
    100 MW load).
    """
    n_hours = 24 * n_days
    tables = {
        "t_class": "class_id,name\n1,System\n7,Battery\n8,Storage\n"
        "72,Decision Variable\n",
        "t_collection": "collection_id,parent_class_id,child_class_id,name\n"
        "80,1,7,Batteries\n93,1,8,Storages\n707,1,72,Decision Variables\n",
        "t_property": "property_id,collection_id,name\n"
        "10,80,Energy\n20,93,End Volume\n30,707,Value\n",
        "t_object": "object_id,class_id,name\n1,7,BAT1\n2,8,RES1\n"
        "3,72,Generation_SEN\n",
        "t_membership": "membership_id,collection_id,parent_object_id,"
        "child_object_id\n100,80,0,1\n200,93,0,2\n300,707,0,3\n",
        "t_key": "key_id,membership_id,phase_id,property_id,band_id,sample_id\n"
        "1,100,3,10,1,0\n2,200,3,20,1,0\n3,300,3,30,1,0\n",
        "t_phase_3": "interval_id,period_id\n"
        + "".join(f"{h},{h}\n" for h in range(1, n_hours + 1)),
    }
    rows = ["key_id,period_id,value"]
    for h in range(1, n_hours + 1):
        day = (h + 23) // 24
        rows.append(f"1,{h},{BAT1_END.get(day, 95.0) if h % 24 == 0 else 80.0}")
        rows.append(f"2,{h},{RES1_END.get(day, 450.0) if h % 24 == 0 else 490.0}")
        rows.append(f"3,{h},0.105")
    tables["t_data_0"] = "\n".join(rows) + "\n"
    return tables
