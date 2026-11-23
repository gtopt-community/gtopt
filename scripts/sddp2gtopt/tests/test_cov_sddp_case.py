"""Branch coverage for :mod:`sddp2gtopt.sddp_case` (block-case loader).

Builds an enriched copy of the ``sddp_block_case`` fixture in ``tmp_path``
with the optional PSR schedule files (maintenance, fuel prices, reserve
derates, generation constraints, hydro operating tables, batteries,
renewable modifications, stage / typical-year profiles …) and checks the
loaded values.  The pure helpers are exercised directly.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from sddp2gtopt.sddp_case import (
    SddpHydro,
    StageInfo,
    _availability,
    _initial_volume,
    _load_inflows,
    _load_stages,
    _route_dropped_inflows,
    _table_value,
    is_sddp_block_case,
    load_sddp_block_case,
    summarize,
)
from sddp2gtopt.sddp_case_writer import build_sddp_planning
from sddp2gtopt.sddp_dat_parsers import (
    MonthlyFile,
    MonthlyTable,
    SddpControl,
)

CASE = Path(__file__).parent / "data" / "sddp_block_case"

_MONTHS = "".join(f" {'.' * (7 - len(str(m)))}{m}" for m in range(1, 13))


def _monthly(head: str, values: dict[int, float], *, blocks: int = 0) -> str:
    """One ``****`` table: ``values[year]`` constant over the 12 months."""
    lines = [head]
    if blocks:
        lines.append("A~no Blk" + _MONTHS)
        for year, val in values.items():
            for b in range(1, blocks + 1):
                lines.append(
                    f"{year}{b:4d}" + "".join(f"{val:8.2f}" for _ in range(12))
                )
    else:
        lines.append("A~NO" + _MONTHS)
        for year, val in values.items():
            lines.append(f"{year}" + "".join(f"{val:8.2f}" for _ in range(12)))
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="latin-1")
    return path


def _row(code: int, name: str, bus: int, tipo: int, pot: float, stat: int, tech: int):
    return (
        f"{code:4d} {name:<12s}{bus:6d}{tipo:5d}    1{pot:7.0f}.       1."
        f"      1.    0{stat:6d}      0.      0.{tech:8d}"
    )


@pytest.fixture(name="rich_dir")
def fixture_rich_dir(tmp_path: Path) -> Path:
    case = tmp_path / "rich"
    shutil.copytree(CASE, case)
    # Must-run TERMO_A (MR=1) so GerMin is enforced.
    cterm = (case / "ctermitt.dat").read_text(encoding="latin-1")
    cterm = cterm.replace(
        "     1.0    0    7    100.     10.", "     1.0    1    7    100.     10.", 1
    )
    _write(case / "ctermitt.dat", cterm)
    # Two-segment deficit curve.
    ctl = (case / "sddp.dat").read_text(encoding="latin-1")
    _write(
        case / "sddp.dat",
        ctl.replace("  100.    5000.     0.       0.", "    50    500.     50  1000."),
    )
    _write(
        case / "pmtrmett.dat",
        "Tipo Info. :    1  (1=Reduc;2=Absoluto)\n"
        "Unidades   :    2  (1=No.Unid;2=%;3=MW)\n"
        + _monthly("**** Planta: 9001 TERMO_A", {2030: 10.0}),
    )
    _write(case / "combmett.dat", _monthly("**** Fuel  :    7 GAS", {2030: 4.0}))
    _write(
        case / "gmme02tt.dat",
        _monthly("**** Planta: 9001 TERMO_A", {2030: 15.0}, blocks=2),
    )
    _write(
        case / "rrme02tt.dat",
        _monthly("****  9001 TERMO_A                    t", {2030: 10.0}, blocks=2)
        + _monthly("****  9101 Embalse                    h", {2030: 10.0}, blocks=2),
    )
    rgme = (case / "rgme02tt.dat").read_text(encoding="latin-1")
    rgme += _monthly(
        "****       -1 201 TA_max       :<: T:9001", {2030: 70.0}, blocks=2
    )
    rgme += _monthly(
        "****       -1 202 TA_eq        :=: T:9001", {2030: 30.0}, blocks=2
    )
    rgme += _monthly("****       -1 203 TA_late      :>: T:9001", {2031: 9.0}, blocks=2)
    rgme += _monthly(
        "****       -1 204 EM_max       :<: H:9101", {2030: 20.0}, blocks=2
    )
    rgme += _monthly("****       -1 205 EM_late      :>: H:9101", {2031: 9.0}, blocks=2)
    _write(case / "rgme02tt.dat", rgme)
    _write(
        case / "pmhimett.dat",
        "Tipo Info. :    2\nUnidades   :    2\n"
        + _monthly("**** Planta: 9101 Embalse", {2030: 50.0}),
    )
    _write(case / "mxtbmett.dat", _monthly("**** Planta: 9101 Embalse", {2030: 40.0}))
    _write(case / "vmxomett.dat", _monthly("**** Planta: 9101 Embalse", {2030: 100.0}))
    _write(
        case / "mntomett.dat",
        _monthly("**** Planta: 9101 Embalse      Penal:          2.", {2030: 5.0}),
    )
    _write(case / "mxtomett.dat", _monthly("**** Planta: 9101 Embalse", {2030: 80.0}))
    _write(
        case / "riegmett.dat",
        _monthly("**** Planta: 9101 Embalse      Penal:  0       3.", {2030: 2.0}),
    )
    # Renewables: a future plant connected through busconnection.dat, one
    # with only a typical-year profile, one with no profile at all.
    head = (
        "$version=3\n!Num Name........ .Bus. Tipo #Uni .PotIns ..FatOpe ProbFal"
        " SFal Stat. ....O&M CurtCos TechTyp\n"
    )
    rows = [
        _row(101, "Viento_E", 2, 0, 40, 101, 1),
        _row(102, "Solar_F", 0, 1, 30, 102, 2),
        _row(103, "Mini_G", 2, 0, 10, 103, 4),
        _row(104, "Bio_H", 2, 0, 5, 104, 9),
        _row(105, "Ghost_R", 0, 0, 5, 105, 1),  # no bus anywhere
        _row(106, "Never_N", 2, 1, 5, 106, 1),  # future, never commissioned
    ]
    _write(case / "cgndtt.dat", head + "\n".join(rows) + "\n")
    _write(
        case / "mgndtt.dat",
        "$version=1\n!dd/mm/yyyy .Num Name........ .PotIns\n"
        "01/04/2030  102 Solar_F          30.\n"
        "garbage line\n",
    )
    _write(
        case / "busconnection.dat",
        "$version=1\n!SystemId,BusCode,PlantType,PlantCode\ntt,3,R,102\n",
    )
    _write(
        case / "gndme02tt.dat",
        "!Unit: pu\n!Stg NScn LBlk Solar_F\n"
        "   1    1    1  0.25\n   1    1    2  0.75\n"
        "   2    1    1   0.5\n   2    1    2   2.0\n",
    )
    _write(
        case / "blrenw002tt0.dat",
        "!Scn.,Year,Month,Block,Mini_G\n"
        "1,0,3,1,0.2\n1,0,3,2,0.3\n1,0,4,1,0.4\n1,0,4,2,0.5\n",
    )
    _write(
        case / "cbatterytt.dat",
        "$version=1\n!Num,Name,Bus,Type,Amin,Amax,Ainic,Pot,RC,RD\n"
        "1,BAT_A,2,0,1,10,5,4,0.9,0.95\n"
        "2,BAT_F,3,1,0,8,0,2,,\n"
        "3,BAT_X,3,1,0,8,0,2,1,1\n"
        ",NOCODE,3,0,0,8,0,2,1,1\n",
    )
    _write(
        case / "mbatterytt.dat",
        "!Num,Year,Month,Day\n2,2030,4,\n9,,4,1\n",
    )
    dbf = (case / "dbf002tt.dat").read_text(encoding="latin-1")
    dbf += "    9 GHOST         100 01/01/2030    1      1.0    0\n"
    _write(case / "dbf002tt.dat", dbf)
    _write(case / "csumcirc.dat", "$version=1\n")
    _write(case / "rsme02tt.dat", "\n")
    _write(case / "sisgas.dat", "\n")
    _write(case / "hrloadtt.dat", "\n")
    return case


def test_rich_case_thermals(rich_dir: Path) -> None:
    case = load_sddp_block_case(rich_dir)
    th = {t.name: t for t in case.thermals}
    ta = th["TERMO_A"]
    # GerMax 100 × 90 % maintenance × 90 % reserve, then capped by the
    # ``<`` (70) and ``=`` (30) generation constraints.
    assert ta.pmax[0] == [pytest.approx(30.0), pytest.approx(30.0)]
    # Must-run GerMin 20 × 0.9 availability.
    assert ta.pmin[0][0] == pytest.approx(18.0)
    # Fuel price schedule (4 $/u) overrides the ccombu cost (3 $/u).
    assert ta.gcost[0] == pytest.approx(10.0 * 4.0 + 1.0)
    # gmme floor 15 then the ``=`` constraint raises it to 30.
    assert ta.pmin_soft is not None
    assert ta.pmin_soft[0][0] == pytest.approx(30.0)


def test_rich_case_hydros(rich_dir: Path) -> None:
    case = load_sddp_block_case(rich_dir)
    emb = next(h for h in case.hydros if h.name == "Embalse")
    assert emb.qmax[0] == pytest.approx(40.0)
    assert emb.emax[0] == pytest.approx(100.0)
    assert emb.min_outflow[0] == pytest.approx(5.0)
    assert emb.min_outflow_cost == pytest.approx(2.0 * 1000.0 * 0.0036)
    assert emb.max_outflow[0] == pytest.approx(80.0)
    assert emb.irrigation[0] == pytest.approx(2.0)
    assert emb.irrigation_cost == pytest.approx(3.0 * 1000.0 * 0.0036)
    # 50 MW × 50 % availability × 90 % reserve, capped at 20 by rgme.
    assert emb.pmax[0][0] == pytest.approx(20.0)
    assert any("mxtome" in s for s in case.skipped)


def test_rich_case_renewables_batteries_and_skips(rich_dir: Path) -> None:
    case = load_sddp_block_case(rich_dir)
    ren = {r.name: r for r in case.renewables}
    assert set(ren) == {"Viento_E", "Solar_F", "Mini_G", "Bio_H"}
    assert ren["Solar_F"].bus == 3
    assert ren["Solar_F"].capacity == [0.0, pytest.approx(30.0)]
    # Stage profile clipped to [0, 1].
    assert case.profiles["Solar_F"][0][1] == [pytest.approx(0.5), pytest.approx(1.0)]
    assert case.profiles["Mini_G"][0][0] == [pytest.approx(0.2), pytest.approx(0.3)]
    assert "Bio_H" not in case.profiles

    bats = {b.name: b for b in case.batteries}
    assert set(bats) == {"BAT_A", "BAT_F"}
    assert bats["BAT_A"].eff_charge == pytest.approx(0.9)
    assert bats["BAT_F"].eff_charge == pytest.approx(1.0)

    assert 9 not in case.demands
    text = "\n".join(case.skipped)
    for needle in (
        "deficit cost curve",
        "csumcirc",
        "GWh",
        "Bio_H",
        "joint reserve",
        "gas network",
        "hourly chronology",
        "batteries",
    ):
        assert needle in text, needle

    summ = summarize(case)
    assert summ["batteries"] == 2
    assert "tech9" in summ["renewable_mw"]
    assert "small hydro" in summ["renewable_mw"]


def test_rich_case_writer_and_convert(rich_dir: Path, tmp_path: Path) -> None:
    case = load_sddp_block_case(rich_dir)
    out = tmp_path / "out"
    planning, sink = build_sddp_planning(case, name="rich")
    system = planning["system"]
    assert system["battery_array"]
    assert any("Solar_F" in g["name"] for g in system["generator_array"])
    sink.write(out)
    assert (out / "Generator").is_dir()


def test_stage_durations_from_durfix(tmp_path: Path) -> None:
    _write(tmp_path / "durfix002.dat", " 40.\n 60.\n\n")
    ctl = SddpControl(initial_stage=2, initial_year=2030, num_stages=12, num_blocks=2)
    stages = _load_stages(tmp_path, ctl, "002")
    assert stages[0].durations == [
        pytest.approx(28 * 24 * 0.4),
        pytest.approx(28 * 24 * 0.6),
    ]
    # December → January wraps into the next year.
    assert (stages[-1].year, stages[-1].month) == (2031, 1)


def test_stage_durations_equal_split(tmp_path: Path) -> None:
    ctl = SddpControl(initial_stage=1, initial_year=2030, num_stages=1, num_blocks=4)
    stages = _load_stages(tmp_path, ctl, "004")
    assert len(stages) == 1
    assert stages[0].durations == [pytest.approx(31 * 24 / 4)] * 4


def test_table_value_falls_back_to_earlier_year() -> None:
    t = MonthlyTable(code=1)
    t.values[(2028, 0)] = [1.0] * 12
    t.values[(2029, 0)] = [None] * 12
    assert _table_value(t, 2031, 5) == pytest.approx(1.0)
    assert _table_value(t, 2027, 5) is None
    assert _table_value(None, 2030, 1) is None


def _mf(
    val: float,
    units: int | None,
    info: int | None,
    table_units: int | None = None,
    table_info: int | None = None,
) -> MonthlyFile:
    t = MonthlyTable(code=1, units=table_units, info_type=table_info)
    t.values[(2030, 0)] = [val] * 12
    return MonthlyFile(tables=[t], units=units, info_type=info)


@pytest.mark.parametrize(
    ("val", "units", "info", "installed", "nuni", "expected"),
    [
        (25.0, 2, 1, 100.0, 1, 0.75),  # reduction in %
        (40.0, 3, 1, 100.0, 1, 0.6),  # reduction in MW
        (1.0, 1, 1, 100.0, 2, 0.5),  # reduction in units
        (1.0, 1, 1, 100.0, 0, 1.0),  # units, no unit count
        (1.0, 7, 1, 100.0, 1, 1.0),  # unknown unit kind
        (150.0, 2, 2, 100.0, 1, 1.0),  # absolute %, clipped
        (40.0, 3, 2, 100.0, 1, 0.4),  # absolute MW
        (1.0, 1, 2, 100.0, 4, 0.25),  # absolute units
        (1.0, 1, 2, 100.0, 0, 1.0),
        (1.0, 7, 2, 100.0, 1, 1.0),
        (50.0, None, None, 100.0, 1, 0.5),  # defaults: % absolute
        (50.0, 2, 2, 0.0, 1, 1.0),  # nothing installed
    ],
)
def test_availability_kinds(
    val: float,
    units: int | None,
    info: int | None,
    installed: float,
    nuni: int,
    expected: float,
) -> None:
    mf = _mf(val, units, info)
    assert _availability(mf, 1, 2030, 3, installed, nuni) == pytest.approx(expected)


def test_availability_table_overrides_and_missing() -> None:
    mf = _mf(40.0, 2, 2, table_units=3, table_info=1)
    assert _availability(mf, 1, 2030, 3, 100.0, 1) == pytest.approx(0.6)
    assert _availability(mf, 99, 2030, 3, 100.0, 1) == 1.0
    assert _availability(None, 1, 2030, 3, 100.0, 1) == 1.0


def test_initial_volume_variants() -> None:
    cota = [("Cota1", "Vol1"), ("Cota2", "Vol2"), ("Cota3", "Vol3")]
    rec = {
        "tini": "1",
        "Cota1": "100",
        "Vol1": "10",
        "Cota2": "200",
        "Vol2": "50",
        "Cota3": "300",
        "Vol3": "110",
    }
    assert _initial_volume({}, 10.0, 10.0, cota) == 0.0
    assert _initial_volume({**rec, "VInic": "150"}, 10.0, 110.0, cota) == pytest.approx(
        30.0
    )
    assert _initial_volume({**rec, "VInic": "50"}, 10.0, 110.0, cota) == pytest.approx(
        10.0
    )
    assert _initial_volume({**rec, "VInic": "999"}, 10.0, 100.0, cota) == pytest.approx(
        100.0
    )
    assert _initial_volume(
        {"tini": "1", "VInic": "150", "Cota1": "100", "Vol1": "10"}, 0.0, 100.0, cota
    ) == pytest.approx(50.0)
    assert _initial_volume({"VInic": "2"}, 0.0, 100.0, cota) == pytest.approx(100.0)


def _hydro(code: int, station: int) -> SddpHydro:
    return SddpHydro(
        code=code,
        name=f"h{code}",
        station=station,
        downstream=None,
        spill_to=None,
        active=[True],
        pot=[1.0],
        pmax=[[1.0]],
        fp=[1.0],
        qmax=[1.0],
        bus=None,
        vmin=0.0,
        vmax=0.0,
        eini=0.0,
        emax=[0.0],
        soft_emin=[0.0],
        soft_emin_cost=0.0,
        min_outflow=[0.0],
        min_outflow_cost=0.0,
        max_outflow=[0.0],
        irrigation=[0.0],
        irrigation_cost=0.0,
    )


def test_route_dropped_inflows_chain_and_loss() -> None:
    target = _hydro(1, 10)
    skipped: list[str] = []
    dropped: dict[int, tuple[int, int | None]] = {
        2: (20, 3),  # → 3 (dropped) → 1 (in service)
        3: (30, 1),
        4: (0, 1),  # no station
        5: (10, 1),  # station already owned
        6: (60, 7),  # → 7 → 6: loop, lost
        7: (70, 6),
        8: (80, None),  # no downstream: lost
    }
    _route_dropped_inflows([target], dropped, skipped)
    assert target.extra_stations == [20, 30]
    assert skipped and "3 out-of-service" in skipped[0]


def test_inflows_default_initial_year(tmp_path: Path) -> None:
    shutil.copy(CASE / "hinflw.dat", tmp_path / "hinflw.dat")
    ctl = SddpControl(hydro_initial_year=0)
    stages = [StageInfo(year=2030, month=3, durations=[1.0])]
    inflows, years = _load_inflows(tmp_path, ctl, stages, {701, 999}, [1, 2])
    assert years == [2000, 2001]
    assert inflows[701][0][0] == pytest.approx(11.5)
    assert 999 not in inflows
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _load_inflows(empty, ctl, stages, {701}, [1]) == ({}, [])


def test_missing_control_and_detection(tmp_path: Path) -> None:
    assert not is_sddp_block_case(tmp_path / "nope")
    with pytest.raises(FileNotFoundError):
        load_sddp_block_case(tmp_path)


def test_minimal_case_without_optional_files(tmp_path: Path) -> None:
    for name in ("sddp.dat", "deme02tt.dat", "duvame02.dat"):
        shutil.copy(CASE / name, tmp_path / name)
    case = load_sddp_block_case(tmp_path, num_scenarios=2)
    assert case.suffix == ""
    assert not case.buses and not case.thermals and not case.hydros
    assert len(case.scenarios) == 2
    summ = summarize(case)
    assert summ["demand_gwh"] == 0.0
