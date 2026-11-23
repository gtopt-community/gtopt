"""Edge-case coverage for :mod:`sddp2gtopt.dat_parsers` (NCP ``.dat`` path)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sddp2gtopt.dat_parsers import (
    BusDemandParser,
    CircuitParser,
    ControlParser,
    DemandParser,
    FuelParser,
    ThermalParser,
    VoltParser,
    _interp_cota_vol,
    _split_reactance_region,
    _to_float,
    find_dat,
    parse_commitment,
    parse_final_volumes,
    parse_gen_constraints,
    parse_inflows,
    parse_max_generation,
    parse_renewable_profiles,
    parse_renewables,
    parse_unit_prices,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="latin-1")
    return path


def test_to_float_bad_token() -> None:
    assert _to_float("abc") == 0.0


def test_control_parser_defaults(tmp_path: Path) -> None:
    study = ControlParser(
        _write(
            tmp_path / "sddp.dat",
            "ETAPA x\nTASA DE DESCUENTO   none\nCosto Deficit\nfoo\n",
        )
    ).parse()
    assert study.stage_type == 1
    assert study.deficit_cost == pytest.approx(1000.0)


def test_fuel_and_thermal_parsers_skip_noise(tmp_path: Path) -> None:
    fuels = FuelParser(
        _write(tmp_path / "ccombu.dat", "!h\n x a b c\n 1 GAS MWh 5.0\n 2 X\n")
    ).parse()
    assert [f.code for f in fuels] == [1]
    therm = ThermalParser(
        _write(
            tmp_path / "ctermi.dat",
            "!h\n"
            "  10 T1  1 0 100.0 0.0 100.0 0. 0. 2.0 0 1\n"  # no segments
            "  11 T2  1 0 100.0 0.0 100.0 0. 0. 2.0 0 1 0 5 50 3 50 4\n",
        )
    ).parse(fuels={1: 10.0})
    by = {t.name: t for t in therm}
    assert by["T1"].g_segments == [(100.0, 2.0)]
    assert by["T2"].g_segments == [(50.0, 32.0), (50.0, 42.0)]


def test_interp_cota_vol_edges() -> None:
    pts = [(100.0, 10.0), (100.0, 20.0), (200.0, 30.0)]
    assert _interp_cota_vol(50.0, pts) == 10.0
    assert _interp_cota_vol(250.0, pts) == 30.0
    assert _interp_cota_vol(150.0, pts) == pytest.approx(25.0)


def test_demand_volt_and_bus_demand_noise(tmp_path: Path) -> None:
    assert DemandParser(
        _write(tmp_path / "cpde.dat", "\nxx\n01/01/2026 1.0 2.0\n")
    ).parse() == [1.0, 2.0]
    assert VoltParser(
        _write(tmp_path / "Volt.dat", "(Bus) Nome O kV\n 1 A 0.\n 2 B 230.\n")
    ).parse() == {2: 230.0}
    assert not BusDemandParser(_write(tmp_path / "x.dat", "\n01/01:00 1\n")).parse()


def test_split_reactance_region_variants() -> None:
    assert _split_reactance_region("1.00 5.00") == (1.0, 5.0)
    assert _split_reactance_region("5.0") == (5.0, 0.0)
    assert _split_reactance_region("   ") == (0.0, 0.0)


def test_circuit_parser_skips(tmp_path: Path) -> None:
    circuits = CircuitParser(
        _write(
            tmp_path / "dcirc.dat",
            "#BOR. header line\n"
            "       1      abc  1.00  5.00 L1 100.0\n"
            "       1        2  1.00  5.00\n",
        )
    ).parse()
    assert len(circuits) == 1
    assert circuits[0].name == "" and circuits[0].rating == 0.0


def test_block_csv_profiles_without_header(tmp_path: Path) -> None:
    assert not parse_inflows(_write(tmp_path / "inflow.csv", "1,1,1,2.0\n"))
    prof = parse_inflows(
        _write(tmp_path / "i2.csv", "Stag,Seq,Blck,A,B\n1,1,1,,\n1,1,1,2.0\n")
    )
    assert prof == {"A": [2.0]}
    assert not parse_final_volumes(_write(tmp_path / "v.csv", "1,1,1,2\n"))
    assert not parse_final_volumes(_write(tmp_path / "v2.csv", "Stag,S,B,A\n1,1,1,\n"))
    assert parse_final_volumes(
        _write(tmp_path / "v3.csv", "Stag,S,B,A,B\n1,1,1,,4.0\n")
    ) == {"B": 4.0}


def test_renewables_and_profiles_noise(tmp_path: Path) -> None:
    assert not parse_renewables(
        _write(tmp_path / "cgnd.dat", "$v\n!h\n\n x 1 2 3 4 5 6 7 8\n")
    )
    prof = parse_renewable_profiles(
        _write(
            tmp_path / "cpgnd.dat",
            "orphan 1/1/2026 1.0\n****  COD: xx\n01/01/2026 9.0\n"
            "****  COD:  3\ndd/mm/aaaa 00h\n01/01/2026 1.0\n",
        )
    )
    assert prof == {3: [1.0]}


def test_prices_and_constraints_empty(tmp_path: Path) -> None:
    assert not parse_unit_prices(_write(tmp_path / "p.csv", ""))
    assert not parse_unit_prices(_write(tmp_path / "p2.csv", "h,A\n"))
    assert not parse_gen_constraints(_write(tmp_path / "r.csv", "x\n"))
    assert not parse_gen_constraints(
        _write(tmp_path / "r2.csv", "Tipo,?,<\nh,1-A X,2-B Y\n1,5.0,\n")
    )


def test_max_generation_and_commitment_noise(tmp_path: Path) -> None:
    profiles, pct = parse_max_generation(
        _write(
            tmp_path / "cprmx.dat",
            "Unidades : 2\n01/01/2026 5.0\n****  1 A\n****  2 B\n01/01/2026  50.0\n",
        )
    )
    assert pct and profiles == {"B": [50.0]}
    assert parse_commitment(_write(tmp_path / "c.dat", "garbage\n1! 5,!U5\n")) == {
        "U5": True
    }


def test_find_dat_misses_directories(tmp_path: Path) -> None:
    (tmp_path / "chidroXX.dat").mkdir()
    assert find_dat(tmp_path, "chidro*.dat") is None
