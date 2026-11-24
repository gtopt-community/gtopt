# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage of the reserve / provision extractors and inflow flows."""

# pylint: disable=unbalanced-tuple-unpacking

from __future__ import annotations

from pathlib import Path

import pytest

from plexos2gtopt import parsers as P
from plexos2gtopt.entities import BatterySpec, GeneratorSpec, ReserveSpec
from plexos2gtopt.gtopt_writer import build_reserve_zone_array
from plexos2gtopt.tests.cov_parsers_kit import DbKit, bundle


def test_extract_flows_drops_unknown_junctions(tmp_path: Path) -> None:
    """Inflow columns without a junction are dropped when filtering."""
    body = "YEAR,MONTH,DAY,PERIOD,R1,FILT\n2026,1,1,1,3,4\n"
    b = bundle(tmp_path, {"Hydro_WaterFlows.csv": body})
    out = P.extract_flows(DbKit().db, b, known_junctions=frozenset({"R1"}))
    assert [f.junction_name for f in out] == ["R1"]
    out = P.extract_flows(DbKit().db, b, known_junctions=frozenset({"R1", "FILT"}))
    assert len(out) == 2


def test_parse_res_timeslice_csv_edges(tmp_path: Path) -> None:
    """Empty file -> blank slices; short rows / no flag -> blank; padding."""
    empty = tmp_path / "e.csv"
    empty.write_text("")
    assert P._parse_res_timeslice_csv(empty, n_days=2) == ["", ""]
    path = tmp_path / "t.csv"
    path.write_text("YEAR,MONTH,DAY,WD,WE\n2026,1,1,0,1\n2026,1,2\n")
    assert P._parse_res_timeslice_csv(path, n_days=3) == ["WE", "", ""]


def test_parse_res_requirement_csv_edges(tmp_path: Path) -> None:
    """Bad patterns / values / hours are skipped."""
    path = tmp_path / "r.csv"
    path.write_text(
        "NAME,PATTERN,VALUE\nR1,nohour,5\nR1,H3,abc\nR1,H30,5\nR1,H2,7\nOTHER,H1,9\n"
    )
    out = P._parse_res_requirement_csv(path, frozenset({"R1"}), n_days=1)
    assert out["R1"][1] == pytest.approx(7.0)
    assert sum(out["R1"]) == pytest.approx(7.0)


def test_extract_reserves_timeslice_and_dangling_eligibility(tmp_path: Path) -> None:
    """Res_Timeslice selects the per-day slice; dangling eligibility skipped."""
    kit = DbKit()
    rsv = kit.obj("Reserve", "CPF_RS")
    gen = kit.obj("Generator", "G")
    kit.mem("Reserve", "Generator", rsv, gen, coll_name="Generators")
    kit.mem("Reserve", "Generator", rsv, 9999, coll_name="Generators")
    b = bundle(
        tmp_path,
        {
            "Res_Timeslice.csv": "YEAR,MONTH,DAY,WD,WE\n2026,1,1,1,0\n",
            "Res_Requirement.csv": 'NAME,PATTERN,VALUE\nCPF_RS,"WD,H1",11\nCPF_RS,"WE,H1",22\n',
        },
    )
    (spec,) = P.extract_reserves(kit.db, b)
    assert spec.eligible_generators == ("G",)
    assert spec.ur_requirement[0] == pytest.approx(11.0)


def test_extract_reserves_excluded_from_st(tmp_path: Path) -> None:
    """``Include in ST Schedule = 0`` drops the static Min Provision floor.

    CEN PCP: CTF_RS (Min Provision 305, excluded from ST) provides 77 MW in
    PLEXOS; its Res_Requirement profile stays (the RHS of the
    ``CTF_UpMinProvision`` row).
    """
    kit = DbKit()
    for name, in_st in (("CTF_RS", 0.0), ("CSF_RS", -1.0)):
        oid = kit.obj("Reserve", name)
        kit.sysprop("Reserve", oid, "Min Provision", 300.0)
        kit.sysprop("Reserve", oid, "Include in ST Schedule", in_st)
    body = "NAME,PATTERN,VALUE\n" + "".join(
        f"{n},H1,50\n" for n in ("CTF_RS", "CSF_RS")
    )
    b = bundle(tmp_path, {"Res_Requirement.csv": body})
    out = {r.name: r for r in P.extract_reserves(kit.db, b)}
    assert out["CTF_RS"].ur_requirement[0] == pytest.approx(50.0)
    assert out["CTF_RS"].ur_min_provision == 0.0
    assert out["CSF_RS"].ur_requirement[0] == pytest.approx(50.0)
    assert out["CSF_RS"].ur_min_provision == pytest.approx(300.0)
    assert not out["CTF_RS"].in_st_schedule
    assert out["CSF_RS"].in_st_schedule
    zones = {z["name"]: z for z in build_reserve_zone_array(tuple(out.values()))}
    assert set(zones["CTF_RS"]["urreq"][0]) == {0.0}
    assert "urmin" not in zones["CTF_RS"]
    assert zones["CSF_RS"]["urreq"][0][0] == pytest.approx(50.0)


def test_uc_reserve_provision_gens() -> None:
    """Generator reserve-provision coefficients are collected by name."""
    kit = DbKit()
    con = kit.obj("Constraint", "C")
    gen = kit.obj("Generator", "G")
    kit.rel(
        "Generator",
        gen,
        "Constraint",
        con,
        "Constraints",
        {"Raise Reserve Provision Coefficient": 1.0},
    )
    assert P._uc_reserve_provision_gens(kit.db) == frozenset({"G"})


def _gen(name: str, pmax: float, profile: tuple[float, ...] = ()) -> GeneratorSpec:
    return GeneratorSpec(
        object_id=hash(name) & 0xFFFF,
        name=name,
        bus_name="n1",
        pmax=pmax,
        pmax_profile=profile,
    )


def test_reserve_provisions_min_provision_floors(tmp_path: Path) -> None:
    """Min Provision floors: filters for varying / zero / uncommitted gens."""
    kit = DbKit()
    up = kit.obj("Reserve", "CPF_RS")
    dn = kit.obj("Reserve", "CPF_LW")
    names = ("G_OK", "G_VAR", "G_ZERO", "G_FREE", "G_NOFLOOR")
    ids = {n: kit.obj("Generator", n) for n in names}
    cid = kit.coll("Reserve", "Generator", "Generators")
    p_min = kit.prop(cid, "Min Provision")
    p_spin = kit.prop(cid, "Min Spinning Provision")
    for rsv in (up, dn):
        for n in names:
            mid = kit.mem("Reserve", "Generator", rsv, ids[n], coll_name="Generators")
            if n != "G_NOFLOOR":
                kit.data(mid, p_min, 5.0)
                kit.data(mid, p_spin, 1e30)
                kit.data(mid, p_spin, 8.0 if rsv == up else 3.0)
    kit.mem("Reserve", "Generator", up, 9999, coll_name="Generators")
    up2 = kit.obj("Reserve", "CSF_RS")
    weaker = kit.mem("Reserve", "Generator", up2, ids["G_OK"], coll_name="Generators")
    kit.data(weaker, p_min, 1.0)  # weaker floor does not lower the max
    gens = (
        _gen("G_OK", 100.0),
        _gen("G_VAR", 100.0, (50.0, 100.0)),
        _gen("G_ZERO", 0.0),
        _gen("G_FREE", 100.0),
        _gen("G_NOFLOOR", 100.0),
    )
    reserves = (
        ReserveSpec(object_id=up, name="CPF_RS", eligible_generators=names + ("NOPE",)),
    )
    # CFdata with an MRU series for G_OK only.
    cf = tmp_path / "CFdata" / "CPF"
    cf.mkdir(parents=True)
    (cf / "CPF_G_OK_MRU.csv").write_text("VALUE\n\n4\n6\n")
    (cf / "CPF_G_OK_MRD.csv").write_text("VALUE\n0\n")
    out = {
        p.generator_name: p
        for p in P.extract_reserve_provisions(
            reserves,
            gens,
            db=kit.db,
            committed_gens=frozenset({"G_OK", "G_VAR", "G_ZERO", "G_NOFLOOR"}),
            extra_provision_gens=frozenset({"G_EXTRA", "G_FREE"}),
            bundle=bundle(tmp_path),
        )
    }
    assert out["G_OK"].urmin == pytest.approx(8.0)
    assert out["G_OK"].drmin == pytest.approx(5.0)
    assert out["G_OK"].urmax_profile == (4.0, 6.0)
    assert out["G_OK"].drmax_profile == ()
    assert out["G_VAR"].urmin == 0.0
    assert out["G_FREE"].urmin == 0.0
    assert "G_EXTRA" not in out


def test_reserve_provisions_without_min_props() -> None:
    """An eligibility collection without Min Provision props adds no floors."""
    kit = DbKit()
    kit.coll("Reserve", "Generator", "Generators")
    out = P.extract_reserve_provisions(
        (ReserveSpec(object_id=1, name="R", eligible_generators=("G",)),),
        (_gen("G", 10.0),),
        db=kit.db,
        committed_gens=frozenset({"G"}),
    )
    assert out[0].urmin == 0.0


def test_parse_sscc_activation_edges(tmp_path: Path) -> None:
    """Bad patterns / values skipped; out-of-range hours ignored; n_days."""
    path = tmp_path / "s.csv"
    path.write_text(
        "Year,Pattern,CPF_RS_BESS,CSF_LW_BESS,ZERO\n"
        "2026,junk,1,1,0\n"
        "2026,H1-2,50,abc,0\n"
        "2026,H23-26,10,20,0\n"
    )
    out = P._parse_sscc_activation_bess_csv(path, n_days=2)
    assert set(out) == {"CPF_RS_BESS", "CSF_LW_BESS"}
    assert len(out["CPF_RS_BESS"]) == 48
    assert out["CPF_RS_BESS"][0] == pytest.approx(0.5)
    assert out["CSF_LW_BESS"][23] == pytest.approx(0.2)


def _bat(name: str, pmax: float) -> BatterySpec:
    return BatterySpec(object_id=1, name=name, bus_name="n1", pmax_discharge=pmax)


def test_sscc_bess_provisions(tmp_path: Path) -> None:
    """Per-zone BESS provisions with CFdata caps and zone prefixes."""
    kit = DbKit()
    zones = ("CPF_RS_BESS", "CSF_LW_BESS", "CTF_RS_BESS", "XX_RS_BESS", "EMPTY_RS_BESS")
    zid = {z: kit.obj("Reserve", z) for z in zones}
    kit.obj("Reserve", "NOT_A_BESS_ZONE")
    b1 = kit.obj("Battery", "B1")
    b0 = kit.obj("Battery", "B0")
    bx = kit.obj("Battery", "BX")
    kit.mem("Reserve", "Battery", zid["EMPTY_RS_BESS"], bx, coll_name="Batteries")
    for z in zones[:4]:
        kit.mem("Reserve", "Battery", zid[z], b1, coll_name="Batteries")
        kit.mem("Reserve", "Battery", zid[z], b0, coll_name="Batteries")
    kit.mem("Reserve", "Battery", zid["CPF_RS_BESS"], 9999, coll_name="Batteries")
    kit.mem("Reserve", "Battery", 8888, b1, coll_name="Batteries")
    hdr = "Year,Pattern," + ",".join(zones) + "\n"
    row = "2026,H1-24," + ",".join("50" for _ in zones) + "\n"
    cf = tmp_path / "CFdata" / "CPF"
    cf.mkdir(parents=True)
    (cf / "CPF_B1_MRU.csv").write_text("VALUE\n7\n")
    b = bundle(tmp_path, {"SSCC_Activation_BESS.csv": hdr + row})
    out = P.extract_sscc_bess_provisions(kit.db, b, (_bat("B1", 10.0), _bat("B0", 0.0)))
    by = {p.name: p for p in out}
    assert set(by) == {f"provision_B1_gen__{z}" for z in zones[:4]}
    cpf = by["provision_B1_gen__CPF_RS_BESS"]
    assert cpf.urmax == 10.0 and cpf.urmax_profile == (7.0,)
    assert by["provision_B1_gen__CSF_LW_BESS"].drmax == 10.0


def test_sscc_bess_provisions_short_circuits(tmp_path: Path) -> None:
    """No CSV / no batteries / all-zero factors / no BESS reserves."""
    kit = DbKit()
    assert not P.extract_sscc_bess_provisions(
        kit.db, bundle(tmp_path), (_bat("B", 1.0),)
    )
    b = bundle(tmp_path, {"SSCC_Activation_BESS.csv": "Year,Pattern,Z\n2026,H1-2,0\n"})
    assert not P.extract_sscc_bess_provisions(kit.db, b, ())
    assert not P.extract_sscc_bess_provisions(kit.db, b, (_bat("B", 1.0),))
    b = bundle(
        tmp_path, {"SSCC_Activation_BESS.csv": "Year,Pattern,Z_RS_BESS\n2026,H1-2,5\n"}
    )
    assert not P.extract_sscc_bess_provisions(kit.db, b, (_bat("B", 1.0),))


def test_bess_zone_eligibility_without_collections() -> None:
    """BESS reserves without Reserve->Battery collections yield nothing."""
    kit = DbKit()
    kit.obj("Reserve", "CPF_RS_BESS")
    assert not P._bess_zone_eligibility(kit.db, frozenset({"B"}))
