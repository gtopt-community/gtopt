"""Edge-case coverage for :mod:`sddp2gtopt.sddp_dat_parsers`."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from sddp2gtopt.sddp_dat_parsers import (
    _fortran_records,
    parse_binary_block_profiles,
    parse_block_profiles,
    parse_bus_load_factors,
    parse_circuit_mods,
    parse_circuits,
    parse_csv_dat,
    parse_dated_fw_file,
    parse_dbus,
    parse_fixed_durations,
    parse_fw_file,
    parse_hist_inflows,
    parse_mod_file,
    parse_monthly_file,
    parse_sddp_control,
    parse_stage_profiles,
    parse_system_suffix,
    parse_volt,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="latin-1")
    return path


def test_control_without_values(tmp_path: Path) -> None:
    ctl = parse_sddp_control(
        _write(
            tmp_path / "sddp.dat",
            "ETAPA  x\nREPRESENTACION DE LA RED  0\n"
            "PEN. DEFL. MINIMA  0. vert  none\n"
            "Costo Deficit\n  -- header --\n  0.  100.\n"
            "---- SERIES A SIMULAR ----\nfoo bar\n",
        )
    )
    assert ctl.stage_type == 2  # default kept: no numeric token
    assert not ctl.use_network
    assert ctl.discount_rate == 0.0
    assert not ctl.deficit_segments
    assert not ctl.selected_series


def test_system_suffix_variants(tmp_path: Path) -> None:
    assert parse_system_suffix(_write(tmp_path / "a.dat", "")) == ""
    assert parse_system_suffix(_write(tmp_path / "b.dat", " NUM NOME\n\nxx\n")) == ""
    assert (
        parse_system_suffix(_write(tmp_path / "c.dat", " NUM NOME UnM\n   1 Sys  GU\n"))
        == "gu"
    )
    assert (
        parse_system_suffix(_write(tmp_path / "d.dat", " NUM NOME\n   1 Sys\n")) == ""
    )


def test_fw_and_mod_files_skip_noise(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "ctermi.dat",
        "$version=1\n!Num Nombre\n\n! comment\nxx bad\n   1 A\n",
    )
    _, recs = parse_fw_file(path, key_label="Nombre")
    assert [r["Num"] for r in recs] == ["1"]
    _, recs = parse_fw_file(path)
    assert [r["Num"] for r in recs] == ["1"]
    mods = parse_mod_file(
        _write(
            tmp_path / "mtermi.dat",
            "!Num Nombre\n   1 orphan\nMOD: 01/02/2030\n!Num Nombre\n   1 A\nxx\n",
        )
    )
    assert mods == [((2030, 2, 1), {"Num": "1", "Nombre": "A"})]


def test_dated_and_csv_without_header(tmp_path: Path) -> None:
    assert not parse_dated_fw_file(_write(tmp_path / "m.dat", "01/01/2030 1\n"))
    assert not parse_csv_dat(_write(tmp_path / "c.dat", "1,2\n"))
    rows = parse_csv_dat(_write(tmp_path / "d.dat", "!A,B\n1,2\n , \n"))
    assert rows == [{"A": "1", "B": "2"}]


def test_monthly_penalty_and_noise(tmp_path: Path) -> None:
    mf = parse_monthly_file(
        _write(
            tmp_path / "x.dat",
            "2030  1.\n"  # data before any header: ignored
            "**** Planta: 5 P   Penal: 7\n"
            "A~NO ......1\n"
            "xxxx      1.\n"
            "2030      2.\n",
        )
    )
    (t,) = mf.tables
    assert t.penalty == pytest.approx(7.0)
    assert t.get(2030, 1) == pytest.approx(2.0)
    assert t.get(2030, 2) is None


def test_load_factors_durations_and_inflows(tmp_path: Path) -> None:
    dbf = parse_bus_load_factors(
        _write(
            tmp_path / "dbf.dat",
            "!hdr\n   1 A\n   x B  100 01/01/2030    1  1.0  0\n"
            "   2 B  100 bad        1  1.0  0\n",
        )
    )
    assert not dbf
    assert parse_fixed_durations(_write(tmp_path / "f.dat", "\nx\n 50.\n")) == [50.0]
    assert not parse_hist_inflows(_write(tmp_path / "e.dat", "\n"))
    hist = parse_hist_inflows(
        _write(
            tmp_path / "h.dat",
            " 1/2000    701       \n"
            " 1/2000   1.50       \n"
            "garbage\n"
            " 2/2000              \n",
        )
    )
    assert hist[701] == {(2000, 1): 1.5, (2000, 2): 0.0}


def test_block_and_stage_profiles_noise(tmp_path: Path) -> None:
    assert parse_block_profiles(_write(tmp_path / "a.dat", "$x\n1,2\n")) == ([], {})
    names, rows = parse_block_profiles(
        _write(
            tmp_path / "b.dat",
            "!Scn,Year,Month,Block,W\n!c\n1,2\nx,2030,1,1,0.5\n"
            "1,2031,1,1,0.5\n1,2030,1,1,0.25\n",
        ),
        years={2030},
    )
    assert names == ["W"] and rows == {(1, 2030, 1, 1): [0.25]}
    assert parse_stage_profiles(_write(tmp_path / "c.dat", "x\n")) == ([], {})
    _, rows_s = parse_stage_profiles(
        _write(tmp_path / "d.dat", "!Stg NScn LBlk W\n x 1 1 0.5\n 1 1\n")
    )
    assert not rows_s


def _rec(payload: bytes) -> bytes:
    n = struct.pack("<i", len(payload))
    return n + payload + n


def test_binary_profiles_truncated_and_bad_marker(tmp_path: Path) -> None:
    head = _rec(struct.pack("<i", 2))
    head += _rec(struct.pack("<10i", 0, 1, 0, 1, 0, 0, 0, 0, 0, 2030))
    head += _rec(struct.pack("<2i", 0, 3))
    head += _rec(b"SUN-A       ")
    body = struct.pack("<f", 0.5)  # only one of the three block rows
    path = tmp_path / "blrenw0.dat"
    path.write_bytes(head + body)
    names, rows = parse_binary_block_profiles(path)
    assert names == ["SUN-A"] and list(rows) == [(1, 0, 1, 1)]
    bad = struct.pack("<i", 1) + b"x" + struct.pack("<i", 2)
    with pytest.raises(ValueError, match="bad Fortran record marker"):
        _fortran_records(bad, 1)


def test_network_parsers_noise(tmp_path: Path) -> None:
    buses, gens = parse_dbus(
        _write(
            tmp_path / "dbus.dat",
            " NUM. Tp ...NombreB.. Id  # tg Plnt\n xxxx\n    4  0 B4\n",
        )
    )
    assert buses == {4: "B4"}
    assert not gens
    buses, _ = parse_dbus(_write(tmp_path / "dbus2.dat", "!Code,Name\nx,y\n7,\n"))
    assert buses == {7: "b7"}
    assert parse_volt(_write(tmp_path / "v.dat", " 1 A 0\n 2 B 230\n")) == {2: 230.0}
    assert not parse_circuits(_write(tmp_path / "c.dat", "$version=2\n\nshort\n"))
    circuits = parse_circuits(
        _write(
            tmp_path / "legacy.dat",
            "#BOR.   #BDE.    .RESIS.REACT Nome........   (  MW)   # I\n"
            "\n bad\n"
            "    1       2             2.0 L12             100.0   5 1\n"
            "    1       3                 L13             100.0   6 0\n",
        )
    )
    assert [(c.code, c.x_pct, c.future) for c in circuits] == [
        (5, 2.0, True),
        (6, 0.0, False),
    ]
    assert not parse_circuit_mods(_write(tmp_path / "m.dat", "   1 orphan\n"))
