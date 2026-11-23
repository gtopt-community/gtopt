"""Edge-case tests for psse2gtopt: malformed RAW records, OOS transformers,
overlay CSV errors, aux spreadsheets and CLI error paths."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

import psse2gtopt.main as cli
from psse2gtopt.aux_data import parse_ldm_merit_order, parse_nomenclatura
from psse2gtopt.gtopt_writer import build_bus_names, build_planning
from psse2gtopt.info_display import display_psse_info
from psse2gtopt.overlay import parse_gen_costs, parse_line_limits
from psse2gtopt.psse2gtopt import resolve_raw_file, validate_psse_case
from psse2gtopt.raw_parser import _to_float, parse_raw

_BUS = "{n},'{name:<12}', 230.0000,{ide},   1,   1,   1,1.00000,   0.0000"
_GEN = (
    "{b},'1 ',   50.000,     0.000,    50.000,   -50.000,1.00000,     0,"
    "   100.000, 0.0, 0.2, 0.0, 0.0,1.00000,1,  100.0,   {pmax},     0.000,   1,1.0"
)
_BRANCH = "{a},{b},'1 ', 0.01, 0.1, 0.0,  200.00,  250.00,  300.00, 0, 0, 0, 0,1,1"
_W = "1.00000, 230.000,   0.000,  100.00,  100.00,    0.00"


def _xfmr2(i: int, j: int, cz: int, status: int) -> list[str]:
    return [
        f"{i},{j},0,'1 ',1,{cz},1, 0.0, 0.0,2,'T2W',{status},1,1.0",
        " 0.0, 0.05, 100.00",
        _W,
        "1.00000, 230.000",
    ]


def _xfmr3(i: int, j: int, k: int, cz: int, status: int) -> list[str]:
    return [
        f"{i},{j},{k},'1 ',1,{cz},1, 0.0, 0.0,2,'T3W',{status},1,1.0",
        " 0.0, 0.2, 100.0, 0.0, 0.3, 100.0, 0.0, 0.4, 100.0,1.0, 0.0",
        _W,
        _W,
        _W,
    ]


def _edge_raw(rev: int = 31) -> str:
    lines = [
        f"0,   100.00, {rev}, 0, 1, 60.00     / edge fixture",
        "TITLE ONE",
        "",
        _BUS.format(n=1, name="AGU-230", ide=3),
        _BUS.format(n=2, name="AGU-230", ide=2),  # duplicate expanded name
        _BUS.format(n=3, name="ISO", ide=4),
        _BUS.format(n=4, name="LOW", ide=1),
        "   ",  # blank line inside a line section
        _BUS.format(n=5, name="TER", ide=1),
        "9,'SHORT'",  # < 4 fields: skipped
        "0 / end bus",
        "2,'1 ',1,1,1,  80.000, abc, 0, 0, 0, 0,1,1,0",  # 'abc' -> 0.0
        "4,'1 ',1,1,1, -30.000, 0, 0, 0, 0, 0,1,1,0",  # negative load
        "4,'2 ',1",  # short load
        "0 / end load",
        "0 / end shunt",
        _GEN.format(b=1, pmax="200.000"),
        "2,'1 '",  # short gen
        "0 / end gen",
        _BRANCH.format(a=1, b=2),
        "1,2,'2 '",  # short branch
        "0 / end branch",
        "",
        *_xfmr2(1, 4, 1, 0),  # out of service
        *_xfmr2(1, 3, 3, 1),  # CZ=3, touches isolated bus 3
        *_xfmr3(1, 4, 3, 1, 1),  # 3-winding touching isolated bus
        *_xfmr3(1, 4, 5, 1, 0),  # 3-winding out of service
        *_xfmr2(4, 5, 2, 1),  # ok; file ends without terminator
    ]
    return "\n".join(lines) + "\n"


@pytest.fixture(name="edge_raw")
def fixture_edge_raw(tmp_path: Path) -> Path:
    path = tmp_path / "edge.raw"
    path.write_text(_edge_raw(), encoding="latin-1")
    return path


# ── raw_parser ──────────────────────────────────────────────────────────


def test_to_float_tolerates_blank_and_garbage() -> None:
    assert _to_float("  ") == 0.0
    assert _to_float("'x'") == 0.0
    assert _to_float(" '1.5' ") == 1.5


def test_parse_edge_raw(edge_raw: Path, caplog) -> None:
    case = parse_raw(edge_raw)
    assert "older than the tested range" in caplog.text
    assert "CZ=3" in caplog.text
    assert [b.number for b in case.buses] == [1, 2, 3, 4, 5]
    assert len(case.loads) == 2
    assert len(case.gens) == 1
    assert len(case.branches) == 1
    assert len(case.transformers) == 5
    assert case.case.title1 == "TITLE ONE"
    assert case.case.title2 == ""


def test_parse_raw_truncated_line_section(tmp_path: Path) -> None:
    path = tmp_path / "trunc.raw"
    path.write_text(
        "0, 100.0, 33, 0, 1, 60.0\nT1\nT2\n" + _BUS.format(n=1, name="A", ide=3)
    )
    case = parse_raw(path)
    assert [b.number for b in case.buses] == [1]
    assert not case.loads
    assert not case.transformers


# ── gtopt_writer ────────────────────────────────────────────────────────


def test_build_planning_skips_oos_and_isolated_transformers(edge_raw: Path) -> None:
    case = parse_raw(edge_raw)
    planning = build_planning(case, name="edge")
    names = [ln["name"] for ln in planning["system"]["line_array"]]
    assert names == ["l1_2_1", "t4_5_1"]
    assert not any(b["uid"] >= 1000 for b in planning["system"]["bus_array"])


def test_build_bus_names_disambiguates_collisions(edge_raw: Path) -> None:
    case = parse_raw(edge_raw)
    names = build_bus_names(case.buses, {"AGU": "Aguacapa"})
    assert names[1] == "Aguacapa-230"
    assert names[2] == "Aguacapa-230_2"


# ── info_display ────────────────────────────────────────────────────────


def test_info_requires_input() -> None:
    with pytest.raises(ValueError, match="'input' option is required"):
        display_psse_info({})


def test_info_reports_negative_loads(edge_raw: Path, capsys) -> None:
    display_psse_info({"input": edge_raw})
    out = capsys.readouterr().out
    assert "negative loads" in out
    assert "title           : TITLE ONE" in out


# ── psse2gtopt façade ───────────────────────────────────────────────────


def test_resolve_raw_file_picks_first_of_many(tmp_path: Path, caplog) -> None:
    for name in ("b.raw", "a.raw", "c.txt"):
        (tmp_path / name).write_text("x")
    assert resolve_raw_file(tmp_path).name == "a.raw"
    assert "2 .raw files" in caplog.text


def test_resolve_raw_file_single_and_dir_named_raw(tmp_path: Path) -> None:
    (tmp_path / "dir.raw").mkdir()  # not a file
    (tmp_path / "only.raw").write_text("x")
    assert resolve_raw_file(tmp_path).name == "only.raw"


def test_convert_default_output_dir_next_to_input(
    edge_raw: Path, tmp_path: Path
) -> None:
    cli.main(["-i", str(edge_raw), "-l", "ERROR"])
    assert (tmp_path / "gtopt_edge" / "gtopt_edge.json").is_file()
    src = tmp_path / "src"
    src.mkdir()
    (src / "edge.raw").write_text(edge_raw.read_text(encoding="latin-1"))
    cli.main(["-i", str(src), "-l", "ERROR"])
    assert (src / "gtopt_edge" / "gtopt_edge.json").is_file()


def test_info_without_titles_or_loads(tmp_path: Path, capsys) -> None:
    path = tmp_path / "bare.raw"
    path.write_text(
        "0, 100.0, 33, 0, 1, 60.0\n\n\n" + _BUS.format(n=1, name="A", ide=3) + "\n"
    )
    display_psse_info({"input": path})
    out = capsys.readouterr().out
    assert "title           :" not in out
    assert "load by area" not in out


def test_validate_rejects_case_without_live_buses(tmp_path: Path, caplog) -> None:
    path = tmp_path / "dead.raw"
    path.write_text(
        "0, 100.0, 33, 0, 1, 60.0\nT1\nT2\n" + _BUS.format(n=1, name="A", ide=4)
    )
    assert validate_psse_case({"input": path}) is False
    assert "no in-service buses" in caplog.text


def test_validate_requires_input(caplog) -> None:
    assert validate_psse_case({}) is False
    assert "'input' option is required" in caplog.text


# ── overlay CSVs ────────────────────────────────────────────────────────


def test_gen_costs_errors(tmp_path: Path) -> None:
    p = tmp_path / "g.csv"
    p.write_text("bus,cost\n1,2\n")
    with pytest.raises(ValueError, match="needs 'bus' and 'id' columns"):
        parse_gen_costs(p)
    p.write_text("bus,id,mw1,cost1\n1,1,10,5\n1,'1',20,6\n")
    with pytest.raises(ValueError, match="duplicate generator"):
        parse_gen_costs(p)
    p.write_text("bus,id,mw1,cost1,mw2,cost2\n1,1,20,5,10,6\n")
    with pytest.raises(ValueError, match="breakpoints must increase"):
        parse_gen_costs(p)


def test_gen_costs_skips_blank_segments(tmp_path: Path) -> None:
    p = tmp_path / "g.csv"
    p.write_text("bus,id,mw1,cost1,mw2,cost2\n1,1,10,5,,\n")
    costs = parse_gen_costs(p)
    assert list(costs) == [(1, "1")]


def test_line_limits_requires_columns(tmp_path: Path) -> None:
    p = tmp_path / "l.csv"
    p.write_text("from,to,ckt\n1,2,1\n")
    with pytest.raises(ValueError, match="needs a 'tmax' column"):
        parse_line_limits(p)


# ── aux spreadsheets ────────────────────────────────────────────────────


def _write_xlsx(path: Path, rows: list[list[object]]) -> Path:
    import openpyxl  # pylint: disable=import-outside-toplevel

    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def test_nomenclatura_skips_header_rows(tmp_path: Path) -> None:
    p = _write_xlsx(
        tmp_path / "nom.xlsx",
        [
            ["NOMENCLATURA", "x"],
            ["Codigo", "Descripcion"],
            ["AGU", "Aguacapa"],
            ["TOOLONGCODE", "skipped"],
            ["123", "numeric code skipped"],
        ],
    )
    assert parse_nomenclatura(p) == {"AGU": "Aguacapa"}


def test_ldm_stops_after_blank_run(tmp_path: Path) -> None:
    rows: list[list[object]] = [
        ["Nemo", "Planta Generadora", "Potencia"],
        ["AGU", "Aguacapa", 10],
        [None, "gap", None],
        [None, "gap", None],
        [None, "gap", None],
        [None, "gap", None],
        ["CHX", "Chixoy", 20],
    ]
    assert parse_ldm_merit_order(_write_xlsx(tmp_path / "ldm.xlsx", rows)) == ["AGU"]


# ── CLI ─────────────────────────────────────────────────────────────────


def test_cli_info_error_exits_one(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--info", str(tmp_path / "missing.raw")])
    assert exc.value.code == 1
    assert "error:" in capsys.readouterr().err


def test_cli_convert_error_exits_one(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["-i", str(tmp_path / "missing.raw"), "-l", "ERROR"])
    assert exc.value.code == 1
    assert "does not exist" in capsys.readouterr().err


def test_cli_converts_edge_case(edge_raw: Path, tmp_path: Path) -> None:
    out = tmp_path / "edge_out"
    cli.main(["-i", str(edge_raw), "-o", str(out), "-l", "ERROR"])
    data = json.loads((out / "edge_out.json").read_text(encoding="utf-8"))
    assert data["system"]["name"] == "edge"


def test_module_entry_point(
    edge_raw: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(sys, "argv", ["psse2gtopt", "--info", str(edge_raw)])
    monkeypatch.delitem(sys.modules, "psse2gtopt.__main__", raising=False)
    runpy.run_module("psse2gtopt", run_name="__main__")
    assert "PSS/E RAW case" in capsys.readouterr().out
