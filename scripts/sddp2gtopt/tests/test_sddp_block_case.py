"""Tests for the multi-file PSR SDDP block-case path.

Covers the fixed-width parsers in :mod:`sddp2gtopt.sddp_dat_parsers`,
the loader in :mod:`sddp2gtopt.sddp_case` and the writer in
:mod:`sddp2gtopt.sddp_case_writer`, using the synthetic fixture under
``tests/data/sddp_block_case`` (3 buses, 2 monthly stages from 3/2030,
2 blocks, 3 historical inflow series).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from sddp2gtopt.sddp2gtopt import convert_sddp_case
from sddp2gtopt.sddp_case import is_sddp_block_case, load_sddp_block_case
from sddp2gtopt.sddp_case_writer import build_sddp_planning
from sddp2gtopt.sddp_dat_parsers import (
    fw_record,
    header_spans,
    parse_block_profiles,
    parse_bus_load_factors,
    parse_circuit_mods,
    parse_circuits,
    parse_csv_dat,
    parse_date,
    parse_dbus,
    parse_fuels,
    parse_fw_file,
    parse_hist_inflows,
    parse_load_classes,
    parse_mod_file,
    parse_monthly_file,
    parse_sddp_control,
    parse_system_suffix,
    parse_volt,
)

CASE = Path(__file__).parent / "data" / "sddp_block_case"


# ─────────────────────────────── parsers ────────────────────────────────


def test_header_spans_and_record() -> None:
    hdr = "!Num ...Nombre... .PotIns .GerMin"
    spans = header_spans(hdr)
    assert [s.label for s in spans] == ["Num", "Nombre", "PotIns", "GerMin"]
    rec = fw_record("9001 TERMO A        100.     20.", spans)
    assert rec == {
        "Num": "9001",
        "Nombre": "TERMO A",
        "PotIns": "100.",
        "GerMin": "20.",
    }


def test_parse_date() -> None:
    assert parse_date("01/04/2030") == (2030, 4, 1)
    assert parse_date("garbage") is None


def test_control_and_suffix() -> None:
    ctl = parse_sddp_control(CASE / "sddp.dat")
    assert (ctl.stage_type, ctl.initial_stage, ctl.initial_year) == (2, 3, 2030)
    assert (ctl.num_stages, ctl.num_blocks, ctl.num_series) == (2, 2, 3)
    assert ctl.inflow_type == 2
    assert (ctl.hydro_initial_month, ctl.hydro_initial_year) == (1, 2001)
    assert ctl.use_network and ctl.use_losses
    assert ctl.discount_rate == pytest.approx(0.10)
    assert ctl.deficit_cost == pytest.approx(5000.0)
    assert ctl.spill_penalty == pytest.approx(0.2)
    assert ctl.selected_series == [1, 3]
    assert parse_system_suffix(CASE / "Sistem.dat") == "tt"


def test_monthly_tables() -> None:
    dur = parse_monthly_file(CASE / "duvame02.dat")
    assert len(dur.tables) == 1
    assert dur.tables[0].get(2030, 4, 2) == pytest.approx(520.0)

    deme = parse_monthly_file(CASE / "deme02tt.dat").by_code()
    assert deme[100].name == "DEM_A"
    assert deme[100].get(2030, 4, 1) == pytest.approx(22.0)

    vmin = parse_monthly_file(CASE / "vminmett.dat").by_code()
    assert vmin[9101].penalty == pytest.approx(50.0)
    assert vmin[9101].get(2030, 3) == pytest.approx(30.0)

    rgme = parse_monthly_file(CASE / "rgme02tt.dat").tables[0]
    assert rgme.sense == ">"
    assert rgme.target == ("H", 9102)
    assert rgme.get(2030, 3, 2) == pytest.approx(5.0)


def test_monthly_info_header(tmp_path: Path) -> None:
    path = tmp_path / "pmtrmezz.dat"
    path.write_text(
        "Tipo Info. :    2  (1=Reduc;2=Absoluto)\n"
        "Unidades   :    3  (1=No.Unid;2=%;3=MW)\n"
        "**** Planta: 9001 TERMO_A\n"
        "A~NO ......1 ......2 ......3 ......4 ......5 ......6 ......7 ......8"
        " ......9 .....10 .....11 .....12\n"
        "2030     80.     80.     70.\n",
        encoding="latin-1",
    )
    mf = parse_monthly_file(path)
    assert (mf.info_type, mf.units) == (2, 3)
    table = mf.by_code()[9001]
    assert table.get(2030, 3) == pytest.approx(70.0)
    assert table.get(2030, 4) is None


def test_fixed_width_and_mods() -> None:
    _, recs = parse_fw_file(CASE / "ctermitt.dat", key_label="Nombre")
    assert [r["Num"] for r in recs] == ["9001", "9002", "9003"]
    assert recs[2]["CEsp1"] == "10."
    mods = parse_mod_file(CASE / "mtermitt.dat")
    assert mods == [((2030, 4, 1), mods[0][1])]
    assert mods[0][1]["GerMax"] == "50."
    assert mods[0][1]["PotIns"] == ""  # blank = unchanged
    assert parse_fuels(CASE / "ccombutt.dat") == {7: 3.0, 9: 20.0}


def test_demand_factors_and_classes(tmp_path: Path) -> None:
    dbf = parse_bus_load_factors(CASE / "dbf002tt.dat")
    assert dbf[(100, (2030, 1, 1))][1] == {2: 6.0, 3: 4.0}
    path = tmp_path / "cloadszz.dat"
    path.write_text(" 100 DEM_A\n 101 DEM_B\n", encoding="latin-1")
    assert parse_load_classes(path) == {100: "DEM_A", 101: "DEM_B"}


def test_inflows_and_profiles() -> None:
    hist = parse_hist_inflows(CASE / "hinflw.dat")
    assert sorted(hist) == [701, 702, 703]
    assert hist[701][(2001, 3)] == pytest.approx(13.5)
    names, prof = parse_block_profiles(CASE / "blrenw002tt.dat", years={2030})
    assert names == ["Viento_E"]
    assert prof[(2, 2030, 4, 2)] == [pytest.approx(0.5)]
    _, none = parse_block_profiles(CASE / "blrenw002tt.dat", years={1999})
    assert not none
    sites = parse_csv_dat(CASE / "gndsitett.dat")
    assert sites == [
        {"Code": "101", "Name": "Viento_E", "TechType": "1", "Datasource": "1"}
    ]


def test_network_parsers() -> None:
    buses, gens = parse_dbus(CASE / "dbus.dat")
    assert buses == {1: "NORTE220", 2: "CENTRO220", 3: "SUR 220"}
    assert gens[(2, 9001)] == 1 and gens[(1, 9101)] == 2
    assert parse_volt(CASE / "volt.dat") == {1: 220.0, 2: 220.0, 3: 220.0}
    circs = {c.code: c for c in parse_circuits(CASE / "dcirc.dat")}
    assert circs[10].x_pct == pytest.approx(10.0)
    assert circs[10].rating == pytest.approx(150.0)
    assert circs[12].future and not circs[10].future
    mods = parse_circuit_mods(CASE / "mcirc.dat")
    assert [(d, c.code) for d, c in mods] == [((2030, 4, 1), 12)]


# ─────────────────────────────── loader ─────────────────────────────────


@pytest.fixture(name="case")
def fixture_case():
    return load_sddp_block_case(CASE)


def test_detects_block_case() -> None:
    assert is_sddp_block_case(CASE)
    assert not is_sddp_block_case(CASE.parent / "dat_case")


def test_time_grid(case) -> None:
    assert case.suffix == "tt"
    assert [(s.year, s.month) for s in case.stages] == [(2030, 3), (2030, 4)]
    assert case.stages[1].durations == [200.0, 520.0]
    assert case.scenarios == [1, 2, 3]
    assert case.hydro_years == [2001, 2002, 2003]


def test_subset_of_scenarios() -> None:
    sub = load_sddp_block_case(CASE, num_scenarios=2)
    assert sub.scenarios == [1, 2]
    assert len(sub.inflows[701]) == 2


def test_thermals_entry_and_cost(case) -> None:
    th = {t.code: t for t in case.thermals}
    # 9002 is future without a modification → never operates.
    assert set(th) == {9001, 9003}
    assert th[9001].gcost == [pytest.approx(31.0)] * 2  # 10 × 3.0 + 1.0
    assert th[9001].bus == 1
    # 9003 enters with its 01/04/2030 modification (GerMax 50).
    assert th[9003].pmax == [[0.0, 0.0], [50.0, 50.0]]
    assert th[9003].gcost[1] == pytest.approx(205.0)


def test_hydros(case) -> None:
    hy = {h.code: h for h in case.hydros}
    assert set(hy) == {9101, 9102}
    res = hy[9101]
    assert res.has_storage
    assert res.eini == pytest.approx(60.0)  # 10 + 0.5 × (110 − 10)
    assert res.soft_emin == [30.0, 30.0]
    assert res.soft_emin_cost == pytest.approx(50_000.0)
    # Out-of-service 9103 hands its station to the next plant downstream.
    assert res.extra_stations == [703]
    ror = hy[9102]
    assert not ror.has_storage
    assert ror.pmin_soft == [[5.0, 5.0], [5.0, 5.0]]


def test_inflows_follow_calendar(case) -> None:
    # Series 1 = hydro year 2001; stage months are March/April.
    assert case.inflows[701][0] == [pytest.approx(13.5), pytest.approx(14.0)]
    assert case.inflows[701][2] == [pytest.approx(17.5), pytest.approx(18.0)]
    assert case.inflows[703][1] == [pytest.approx(3.0)] * 2


def test_demand_split(case) -> None:
    # 100 MW (block 1, March) split 6:4 between buses 2 and 3.
    assert case.demands[2][0][0] == pytest.approx(60.0)
    assert case.demands[3][1][1] == pytest.approx(36.0)


def test_lines(case) -> None:
    ln = {line.code: line for line in case.lines}
    assert ln[12].active == [False, True]
    # X = 15 % on 100 MVA at 220 kV → 0.15 × 220² / 100 Ω.
    assert ln[12].reactance == pytest.approx(72.6)
    assert ln[10].resistance == pytest.approx(4.84)


def test_renewables(case) -> None:
    (wind,) = case.renewables
    assert wind.capacity == [40.0, 40.0]
    assert case.profiles["Viento_E"][2][1] == [pytest.approx(0.55), pytest.approx(0.6)]


# ─────────────────────────────── writer ─────────────────────────────────


def test_build_planning_structure(case) -> None:
    planning, _ = build_sddp_planning(case, name="tiny", method="sddp")
    sim = planning["simulation"]
    assert len(sim["block_array"]) == 4
    assert [b["uid"] for b in sim["block_array"]] == [1, 2, 3, 4]
    assert len(sim["scenario_array"]) == 3
    assert len(sim["phase_array"]) == 2
    # Per-phase aperture order: a permutation, driest-ranked series first.
    for k, phase in enumerate(sim["phase_array"]):
        assert sorted(phase["apertures"]) == [1, 2, 3]
        totals = [sum(s[i][k] for s in case.inflows.values()) for i in range(3)]
        assert phase["apertures"][0] == totals.index(min(totals)) + 1
    assert len(sim["scene_array"]) == 3
    assert sim["annual_discount_rate"] == pytest.approx(0.10)
    sysd = planning["system"]
    assert len(sysd["bus_array"]) == 3
    assert {r["name"] for r in sysd["reservoir_array"]} == {"r_Embalse"}
    inflow = {f["name"] for f in sysd["flow_array"]}
    assert inflow == {"in_Embalse", "in_Pasada"}
    opts = planning["options"]
    assert opts["method"] == "sddp"
    assert opts["model_options"]["demand_fail_cost"] == pytest.approx(5000.0)
    assert opts["model_options"]["line_losses_mode"] == "piecewise"


def test_convert_end_to_end(tmp_path: Path) -> None:
    out = tmp_path / "tiny"
    rc = convert_sddp_case(
        {"input_dir": CASE, "output_dir": out, "name": "tiny", "method": "sddp"}
    )
    assert rc == 0
    planning = json.loads((out / "tiny.json").read_text(encoding="utf-8"))
    gens = {g["name"]: g for g in planning["system"]["generator_array"]}
    assert {"TERMO_A", "DIESEL_C", "Embalse", "Pasada", "Viento_E"} <= set(gens)

    # Global block uids: stage 2 owns blocks 3..4.
    pmax = pd.read_parquet(out / "Generator" / "pmax.parquet")
    diesel = pmax[pmax["uid"] == gens["DIESEL_C"]["uid"]]
    assert sorted(diesel["block"].unique()) == [1, 2, 3, 4]
    assert diesel.set_index("block")["value"].to_dict() == {
        1: 0.0,
        2: 0.0,
        3: 50.0,
        4: 50.0,
    }

    active = pd.read_parquet(out / "Line" / "active.parquet")
    assert str(active["value"].dtype) == "int32"

    flows = pd.read_parquet(out / "Flow" / "discharge.parquet")
    assert set(flows["scenario"].unique()) == {1, 2, 3}
    # Embalse receives its own station plus the out-of-service 9103.
    emb = next(
        f["uid"] for f in planning["system"]["flow_array"] if f["name"] == "in_Embalse"
    )
    row = flows[
        (flows["uid"] == emb) & (flows["scenario"] == 1) & (flows["block"] == 1)
    ]
    assert row["value"].iloc[0] == pytest.approx(16.5)


# ───────────────────── SDDP ≥ 17 / legacy layout variants ─────────────────────


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="latin-1")
    return path


def test_csv_bus_circuit_fuel_layouts(tmp_path: Path) -> None:
    from sddp2gtopt.sddp_dat_parsers import (  # pylint: disable=import-outside-toplevel
        parse_bus_connections,
    )

    dbus = _write(
        tmp_path / "dbus.dat",
        "$version=1\n!Code,Name,System,Area\n10,BUS-A,1,1\n20,BUS-B,1,1\n",
    )
    buses, gens = parse_dbus(dbus)
    assert buses == {10: "BUS-A", 20: "BUS-B"} and not gens
    conn = _write(
        tmp_path / "busconnection.dat",
        "$version=1\n!SystemId,BusCode,PlantType,PlantCode\n"
        "zz,10,H,1\nzz,20,T,7\nzz,20,R,3\n",
    )
    assert parse_bus_connections(conn) == {(1, 1): 10, (2, 7): 20, (3, 3): 20}
    dcirc = _write(
        tmp_path / "dcirc.dat",
        "$version=1\n!Code,Name,BusFromCode,BusToCode,CircuitType,Length,"
        "Existing,Decommissioned,Resistance,Reactance,TotalSusceptance,Rating\n"
        "5,L5,10,20,0,0,0,0,0.4,2.5,0,300\n6,L6,10,20,0,0,0,1,0.4,2.5,0,300\n",
    )
    circs = {c.code: c for c in parse_circuits(dcirc)}
    assert circs[5].x_pct == pytest.approx(2.5) and circs[5].rating == 300.0
    assert not circs[5].future and circs[6].future  # decommissioned
    mcirc = _write(
        tmp_path / "mcirc.dat",
        "$version=1\n!Day,Month,Year,Code,Name,BusFrom,BusTo,Existing,"
        "Decommissioned,Resistance,Reactance,TotalSusceptance,Rating\n"
        "1,7,2031,5,L5,10,20,0,0,0.4,2.5,0,350\n",
    )
    ((date, mod),) = parse_circuit_mods(mcirc)
    assert date == (2031, 7, 1) and mod.rating == pytest.approx(350.0)
    fuel = _write(
        tmp_path / "ccombuzz.dat",
        "$version=2\n!Code,Name,FuelUnit,Cost,EmissionFactor,CostType\n"
        "1,GAS,MWh,60.5,0,0\n",
    )
    assert parse_fuels(fuel) == {1: 60.5}


def test_legacy_dcirc_layout(tmp_path: Path) -> None:
    hdr = "#BOR.   #BDE.    .RESIS.REACT Nome........(MVAR)(Tmn)(Tmx)(  MW)(MW)   # I C"
    row = " 1101    1124      0.41  2.65 AGULVG_230A                   374. 375 100 0 0"
    path = _write(tmp_path / "dcirc.dat", f"{hdr}\n{row}\n")
    (c,) = parse_circuits(path)
    assert (c.code, c.from_bus, c.to_bus, c.name) == (100, 1101, 1124, "AGULVG_230A")
    assert c.r_pct == pytest.approx(0.41) and c.x_pct == pytest.approx(2.65)
    assert c.rating == pytest.approx(374.0) and not c.future


def test_monthly_year_header_and_table_flags(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "pmhimezz.dat",
        "Tipo Info. :    1\nUnidades   :    1\n"
        "**** Planta:  601 AGU-H                             2  1\n"
        "YEAR .....01 .....02 .....03 .....04 .....05 .....06 .....07 .....08"
        " .....09 .....10 .....11 .....12\n"
        "2026       0       0       0       0  44.232  23.333       0       0"
        "       0       0       0       0\n",
    )
    mf = parse_monthly_file(path)
    t = mf.by_code()[601]
    assert (t.units, t.info_type) == (2, 1)
    assert t.get(2026, 5) == pytest.approx(44.232)


def test_deficit_segments(tmp_path: Path) -> None:
    text = (
        (CASE / "sddp.dat")
        .read_text(encoding="latin-1")
        .replace("  100.    5000.     0.       0.", "     2   314.95     98  1968.44")
    )
    ctl = parse_sddp_control(_write(tmp_path / "sddp.dat", text))
    assert ctl.deficit_cost == pytest.approx(314.95)
    assert ctl.deficit_segments == [(2.0, 314.95), (98.0, 1968.44)]


def test_stage_profiles(tmp_path: Path) -> None:
    from sddp2gtopt.sddp_dat_parsers import (  # pylint: disable=import-outside-toplevel
        parse_stage_profiles,
    )

    path = _write(
        tmp_path / "gndme09zz.dat",
        "!Unit: pu\n!Stg NScn LBlk WIND-A       SUN-B\n"
        "   1    1    1        0.25           0.\n"
        "   2    1    3         0.5          0.7\n",
    )
    names, rows = parse_stage_profiles(path)
    assert names == ["WIND-A", "SUN-B"]
    assert rows[(1, 2, 3)] == [pytest.approx(0.5), pytest.approx(0.7)]


def test_binary_block_profiles(tmp_path: Path) -> None:
    import struct  # pylint: disable=import-outside-toplevel

    def rec(payload: bytes) -> bytes:
        n = struct.pack("<i", len(payload))
        return n + payload + n

    n_st, nb, names = 2, 2, ["SUN-A", "WIND-B"]
    head = rec(struct.pack("<i", 2))
    head += rec(
        struct.pack("<10i", 1, n_st, 1, len(names), 1, 1, 0, 2, 1, 1900)
        + b"pu     "
        + struct.pack("<i", 12)
    )
    head += rec(struct.pack(f"<{n_st + 1}i", *[nb * i for i in range(n_st + 1)]))
    for nm in names:
        head += rec(nm.ljust(12).encode())
    body = b"".join(
        struct.pack("<2f", 0.1 * (s * nb + b), 0.5)
        for s in range(n_st)
        for b in range(nb)
    )
    path = tmp_path / "blrenw002zz0.dat"
    path.write_bytes(head + body)
    got_names, rows = parse_block_profiles(path)
    assert got_names == names
    assert rows[(1, 0, 2, 2)] == [pytest.approx(0.3), pytest.approx(0.5)]
    assert len(rows) == n_st * nb


def test_initial_volume_from_elevation() -> None:
    from sddp2gtopt.sddp_case import (  # pylint: disable=import-outside-toplevel
        _initial_volume,
    )

    rec = {
        "VInic": "785",
        "tini": "1",
        "Cota1": "772",
        "Vol1@3": "95",
        "Cota2": "796",
        "Vol1@4": "255",
    }
    pairs = [("Cota1", "Vol1@3"), ("Cota2", "Vol1@4")]
    assert _initial_volume(rec, 95.0, 341.0, pairs) == pytest.approx(95 + 160 * 13 / 24)
    assert _initial_volume({"VInic": "0.5"}, 10.0, 110.0, []) == pytest.approx(60.0)


def test_repeated_labels_kept() -> None:
    spans = header_spans("!Num .Vol1.. .Cota1. .Vol1..")
    rec = fw_record("   1     10.    700.     55.", spans)
    assert rec["Vol1"] == "10." and rec["Vol1@2"] == "55."
