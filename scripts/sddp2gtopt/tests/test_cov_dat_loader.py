"""Branch coverage for :mod:`sddp2gtopt.dat_loader` (PSR NCP ``.dat`` path).

A synthetic multi-bus NCP case carrying every optional input (renewables,
import prices, AMM constraints, water values, inflow forecast, end
volumes, max-generation caps, hydro commitment) plus direct tests of the
pure helpers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sddp2gtopt.dat_loader import (
    _apply_import_cap,
    _apply_import_offers,
    _apply_max_gen,
    _infer_blocks_per_day,
    _infer_missing_kv,
    is_dat_case,
    load_dat_case,
)
from sddp2gtopt.entities import CircuitSpec, ThermalSpec
from sddp2gtopt.tests.test_dat_parsers import _fw_chidro_line


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="latin-1")
    return path


def _base_case(root: Path, *, hydro_rows: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _write(
        root / "sddp.dat",
        "Rich NCP case\nETAPA  1\nNUMERO DE ETAPAS  1\n"
        "NUMERO DE SISTEMAS  1\nNUMERO DE BLOQUES DEMANDA  1\n"
        "TASA DE DESCUENTO  0.10\n"
        "----- Costo Deficit -----\n.....% c1\n   100   500.0\n",
    )
    _write(root / "sistem.dat", " NUM NOMBRE  ID\n0001 TESTSYS  TS\n")
    _write(
        root / "ccombuTS.dat",
        "!Num Nome UComb Custo\n   1 GAS  MWh  50.00\n   2 MEX-I MWh 0.0\n",
    )
    _write(
        root / "ctermiTS.dat",
        "$version=1\n!Num Nombre #Uni Tipo PotIns GerMin GerMax Teif Ih CVaria"
        " MR Comb G1 CEsp1\n"
        "  10 GASPLANT  1 0  300.0  0.0  300.0  0. 0. 0. 0  1  100  2\n"
        "  11 MEX-A     1 0  100.0  0.0  100.0  0. 0. 0. 0  2  100  1\n"
        "  12 ESA-B     1 0   50.0  0.0   50.0  0. 0. 0. 0  1  100  1\n"
        "  13 HON-C     1 0   40.0  0.0   40.0  0. 0. 0. 0  1  100  1\n",
    )
    _write(
        root / "chidroTS.dat",
        hydro_rows
        or (
            " NUM Nombre PV VAA TAA #Uni Tipo Pot FPMed\n"
            " 201 HYDRO1  201 202  1 0  60.0 1.5\n"
            " 202 HYDRO2  202 0  1 0  30.0 2.0\n"
        ),
    )
    _write(
        root / "dbus.dat",
        " NUM. Tp NombreB Id  # tg Plnt Nombre\n"
        " 1 0 GEN-230 ts  1 0 10 GASPLANT\n"
        " 1 0 GEN-230 ts  2 0 201 HYDRO1\n"
        " 2 0 LOAD-230 ts  0\n"
        " 3 0 TERMBUS ts  1 0 202 HYDRO2\n",
    )
    _write(
        root / "dcirc.dat",
        "#BOR.   #BDE.    .RESIS.REACT Nome (MW)   #\n"
        " 1       2         1.00  5.00 L12                          300.0    1 0 0\n"
        " 3       1         0.00  0.00 T31                          300.0    2 0 0\n",
    )
    _write(root / "Volt.dat", "(Bus) Nome O kV\n 1 GEN-230 230.\n")
    _write(
        root / "cpdexbus.dat",
        "Infxbloque : 1\ndd/mm/aaaa:hh   2   3  99\n"
        "01/01/2026:00   150.0  0.0  5.0\n"
        "01/01/2026:01   160.0  0.0  5.0\n"
        "02/01/2026:00   170.0  0.0  5.0\n"
        "02/01/2026:01   180.0  0.0  5.0\n",
    )
    return root


def _add_optional_inputs(root: Path) -> None:
    _write(
        root / "cgndgu.dat",
        "$version=3\n"
        "!Num Name........ .Bus. Tipo #Uni .PotIns ..FatOpe ProbFal SFal Stat "
        "....O&M CurtCos TechTyp\n"
        "   1 SNT-E            2    0   16   53.59       1.      0.    0     1"
        "    0.63      0.      0.\n"
        "   9 SDT-F            2    0    1     40.       1.            0     9"
        "      0.      0.      0.\n",
    )
    _write(
        root / "cpgndgu.dat",
        "****    COD:    1 TP: 0\n"
        "dd/mm/aaaa ....00h ....01h\n"
        "01/01/2026  12.0  13.0\n02/01/2026  14.0  15.0\n",
    )
    _write(
        root / "PRECIOSMEX.csv",
        "dd/mm/aaaa:hh,'MEX-A','INTER-SAL'\n"
        "01/01/2026:00h,10.0,0\n01/01/2026:01h,0,0\n"
        "02/01/2026:00h,10.0,0\n02/01/2026:01h,10.0,0\n",
    )
    _write(
        root / "RESTMEX.csv",
        "#1,,\nTipo,>,<\ndd/mm/aaaa:hh,6-ESA-B V,1-NOT-IN X\n"
        "01/01/2026:00h,,1.0\n01/01/2026:01h,7.0,1.0\n",
    )
    _write(
        root / "watervcp.csv",
        "Unit,k$/hm3\nStag,Seq.,Blck,HYDRO2,HYDRO1\n   1,   1,   1,  50.0, 0.0\n",
    )
    _write(
        root / "inflow.csv",
        "Stag,Seq.,Blck,HYDRO1\n1,1,1,4.0\n2,1,1,4.0\n3,1,1,6.0\n4,1,1,6.0\n",
    )
    _write(
        root / "volfincp.csv",
        "Stag,Seq.,Blck,HYDRO1,NOPE\n1,1,1,10.0,1.0\n2,1,1,12.0,1.0\n",
    )
    _write(
        root / "cprmxhgu_u.dat",
        "Unidades   :    2  (1=MW,2=%G)\n"
        "****          201 HYDRO1      \n"
        "dd/mm/aaaa ....00h ....01h\n01/01/2026   50.0   -1.0\n",
    )
    _write(
        root / "cprmxtgu_u.dat",
        "Unidades   :    1  (1=MW,2=%G)\n"
        "****           10 GASPLANT    \n"
        "dd/mm/aaaa ....00h ....01h\n01/01/2026  200.0  300.0\n",
    )
    _write(root / "commith.dat", "0! 202,!HYDRO2\n1! 201,!HYDRO1\n")


def test_rich_ncp_case_legacy_hydro(tmp_path: Path) -> None:
    root = _base_case(tmp_path / "case")
    _add_optional_inputs(root)
    case = load_dat_case(root, blocks_per_stage=2)

    assert (case.study.num_stages, case.study.num_blocks) == (2, 2)
    assert [d.bus_number for d in case.demands] == [2]
    # Terminal bus inherits its neighbour's 230 kV.
    assert {b.number: b.base_kv for b in case.buses}[3] == pytest.approx(230.0)

    th = {t.name: t for t in case.thermals}
    assert {"SNT-E", "SDT-F"} <= set(th)
    assert th["SNT-E"].max_gen == [12.0, 13.0, 14.0, 15.0]
    # MEX-A has its own bid, closed when the price is 0.
    assert th["MEX-A"].gcost_profile == [10.0, 0.0, 10.0, 10.0]
    assert th["MEX-A"].max_gen == [100.0, 0.0, 100.0, 100.0]
    # ESA-B inherits INTER-SAL (all 0) but the AMM floor keeps hour 2 open.
    assert th["ESA-B"].amm_tipo == ">"
    assert th["ESA-B"].max_gen[:2] == [0.0, 50.0]
    # HON-C: no offer at all → unavailable.
    assert th["HON-C"].pmax == 0.0
    # cprmxtgu cap binds below installed.
    assert th["GASPLANT"].max_gen == [200.0, 300.0]

    hy = {h.name: h for h in case.hydros}
    assert not case.hydro_topology
    assert hy["HYDRO2"].committed is False
    assert hy["HYDRO2"].gcost > 0.0
    assert hy["HYDRO1"].inflow_profile == [4.0, 6.0]
    assert hy["HYDRO1"].efin == pytest.approx(12.0)
    assert hy["HYDRO1"].max_gen == [30.0, 60.0]
    # Free run-of-river capped at inflow × FPMed = 5 × 1.5.
    assert hy["HYDRO1"].p_inst == pytest.approx(7.5)


def test_ncp_case_with_storage_topology(tmp_path: Path) -> None:
    header = (
        " NUM ...Nombre... .PV. .VAA .TAA #Uni Tipo ....Pot .FPMed. "
        ".QMin.. .QMax.. .VMin.. .VMax.. .VInic.\n"
    )
    rows = header + _fw_chidro_line(
        201, "HYDRO1", 202, 60.0, 1.5, 15.0, 90.0, 340.0, 200.0
    )
    root = _base_case(tmp_path / "case", hydro_rows=rows)
    _write(
        root / "watervcp.csv",
        "Unit,k$/hm3\nStag,Seq.,Blck,HYDRO1\n   1,   1,   1,  50.0\n",
    )
    case = load_dat_case(root, blocks_per_stage=3)  # 4 % 3 ≠ 0 → single stage
    assert case.hydro_topology
    assert case.hydros[0].gcost == 0.0
    assert case.hydros[0].water_value == pytest.approx(50_000.0)
    assert (case.study.num_stages, case.study.num_blocks) == (1, 4)


def test_single_bus_fallbacks_and_detection(tmp_path: Path) -> None:
    root = tmp_path / "case"
    root.mkdir()
    with pytest.raises(FileNotFoundError):
        load_dat_case(root)
    _write(root / "sddp.dat", "x\nETAPA 1\n")
    assert is_dat_case(root)
    case = load_dat_case(root)  # no sistem.dat → default system, no demand
    assert case.systems[0].name == "SYSTEM"
    assert not case.demands and not case.multi_bus
    _write(root / "psrclasses.json", "{}")
    assert not is_dat_case(root)
    assert not is_dat_case(tmp_path / "missing")


def test_infer_missing_kv_propagates() -> None:
    kv = {1: 230.0, 2: 0.0, 3: 0.0, 4: 0.0}
    circuits = [
        CircuitSpec(from_bus=1, to_bus=2, name="a"),
        CircuitSpec(from_bus=2, to_bus=3, name="b"),
    ]
    _infer_missing_kv(kv, circuits)
    assert kv == {1: 230.0, 2: 230.0, 3: 230.0, 4: 0.0}


def test_infer_blocks_per_day() -> None:
    assert _infer_blocks_per_day({"a": [1.0] * 4 + [2.0] * 4}) == 4
    assert _infer_blocks_per_day({"a": [1, 1, 2, 2, 3, 3], "b": [5.0] * 6}) == 2
    # Degenerate GCD → ~daily over 24 days.
    assert _infer_blocks_per_day({"a": [1.0, 2.0] * 24}) == 2
    assert _infer_blocks_per_day({}) == 1


def test_apply_max_gen_skips(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "cprmx.dat",
        "Unidades   :    1\n"
        "****            1 A\ndd/mm/aaaa ....00h\n01/01/2026   50.0\n"
        "****            2 B\ndd/mm/aaaa ....00h\n01/01/2026  500.0\n"
        "****            3 C\ndd/mm/aaaa ....00h\n01/01/2026   10.0\n",
    )
    specs = [
        ThermalSpec(code=1, name="A", reference_id=1, pmax=100.0),
        ThermalSpec(code=2, name="B", reference_id=2, pmax=100.0),  # not binding
        ThermalSpec(code=3, name="C", reference_id=3, pmax=0.0),  # nothing installed
        ThermalSpec(code=4, name="D", reference_id=4, pmax=100.0),  # no profile
    ]
    assert _apply_max_gen(specs, path, lambda t: t.pmax) == 1
    assert specs[0].max_gen == [50.0]
    assert not specs[1].max_gen and not specs[2].max_gen


def _imp(name: str, pmax: float, **kw) -> ThermalSpec:
    return ThermalSpec(
        code=len(name), name=name, reference_id=1, pmax=pmax, is_import=True, **kw
    )


def test_import_offers_with_price_file() -> None:
    none = _imp("NOPRICE", 10.0)
    full = _imp("FULL", 10.0, gcost_profile=[5.0, 5.0])
    capped = _imp("CAP", 10.0, gcost_profile=[5.0, 0.0], max_gen=[4.0])
    dom = ThermalSpec(code=9, name="DOM", reference_id=9, pmax=10.0)
    _apply_import_offers([none, full, capped, dom], has_price_file=True)
    assert none.pmax == 10.0 and not none.max_gen
    assert not full.max_gen
    assert capped.max_gen == [4.0, 0.0]


def test_import_offers_without_price_file() -> None:
    free = _imp("FREE", 10.0, g_segments=[(100.0, 0.0)])
    fixed = _imp("FIX", 10.0, amm_tipo="=", amm_profile=[3.0])
    priced = _imp("PRICED", 10.0, g_segments=[(100.0, 7.0)])
    off = _imp("OFF", 0.0)
    _apply_import_offers([free, fixed, priced, off], has_price_file=False)
    assert free.pmax == 0.0
    assert fixed.pmax == 10.0 and priced.pmax == 10.0


def test_import_cap_fixed_over_limit() -> None:
    fixed = _imp("FIX", 100.0, amm_tipo="=", amm_profile=[None, 150.0])
    flex = _imp("FLEX", 50.0)
    _apply_import_cap([fixed, flex], 100.0)
    assert fixed.pmax == 100.0
    assert flex.pmax == 0.0
    # Room left and below the flexible total: no scaling needed.
    flex2 = _imp("FLEX2", 50.0)
    _apply_import_cap([flex2], 100.0)
    assert flex2.pmax == 50.0
