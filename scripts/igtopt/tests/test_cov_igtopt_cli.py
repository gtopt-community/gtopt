"""CLI (main), error-path and option-nesting tests for igtopt.

Workbooks are tiny synthetic ``.xlsx`` files written with pandas.
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys

import pandas as pd
import pytest

import igtopt.igtopt as ig
from igtopt import template_builder as tb


def _write_book(path: pathlib.Path, sheets: dict[str, pd.DataFrame]) -> pathlib.Path:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    return path


def _basic_sheets() -> dict[str, pd.DataFrame]:
    return {
        "options": pd.DataFrame(
            {
                "option": ["demand_fail_cost", "solver_algorithm", "mip_start"],
                "value": [500, "barrier", 1],
            }
        ),
        "block_array": pd.DataFrame({"uid": [1, 2], "duration": [1.0, 2.0]}),
        "stage_array": pd.DataFrame(
            {"uid": [1], "first_block": [0], "count_block": [2]}
        ),
        "bus_array": pd.DataFrame({"uid": [1, 2], "name": ["b1", "b2"]}),
        "demand_array": pd.DataFrame(
            {"uid": [1], "name": ["d1"], "bus": ["b1"], "lmax": ["lmax"]}
        ),
        "Demand@lmax": pd.DataFrame(
            {"scenario": [1, 1], "stage": [1, 1], "block": [1, 2], "d1": [5.0, 6.0]}
        ),
        ".notes": pd.DataFrame({"x": [1]}),
        "Demand@ ": pd.DataFrame({"x": [1]}),
        "mystery": pd.DataFrame({"uid": [1], "name": ["m"]}),
    }


@pytest.fixture(name="book")
def fixture_book(tmp_path: pathlib.Path) -> pathlib.Path:
    return _write_book(tmp_path / "case.xlsx", _basic_sheets())


def _main(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        ig.main(argv)
    code = exc.value.code
    return 0 if code is None else int(code)


def _args(xlsx: pathlib.Path, tmp_path: pathlib.Path, **kw: object):
    import argparse  # noqa: PLC0415  pylint: disable=import-outside-toplevel

    base: dict[str, object] = {
        "filenames": [str(xlsx)],
        "json_file": tmp_path / "out.json",
        "input_directory": tmp_path / "out",
        "input_format": "parquet",
        "name": "case",
        "compression": "",
        "skip_nulls": True,
        "parse_unexpected_sheets": False,
        "pretty": False,
        "zip": False,
        "validate": False,
        "ignore_errors": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


# ── main(): normal conversion ───────────────────────────────────────────


def test_main_derives_defaults_from_workbook(
    book: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.INFO):
        rc = _main([str(book), "-f", "csv", "-l", "INFO"])
    assert rc == 0
    planning = json.loads((tmp_path / "case.json").read_text())
    assert planning["system"]["name"] == "case"
    opts = planning["options"]
    assert opts["input_directory"].endswith("case")
    assert opts["input_format"] == "csv"
    assert opts["solver_options"] == {"algorithm": "barrier"}
    assert opts["monolithic_options"] == {"mip_start": 1}
    assert opts["model_options"]["demand_fail_cost"] == 500
    lmax = pd.read_csv(tmp_path / "case" / "Demand" / "lmax.csv")
    assert lmax["uid"].tolist() == [1, 1]
    assert lmax["value"].tolist() == [5.0, 6.0]
    assert "malformed '@'-sheet name" in caplog.text
    assert "skipping sheet .notes" in caplog.text
    assert "skipping unexpected sheet 'mystery'" in caplog.text


def test_main_parse_unexpected_and_debug_level(
    book: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    out = tmp_path / "x.json"
    argv = [str(book), "-j", str(out), "-d", str(tmp_path / "x"), "-n", "sys"]
    rc = _main([*argv, "-U", "-l", "DEBUG", "--compression-level", "3"])
    assert rc == 0
    planning = json.loads(out.read_text())
    assert planning["system"]["mystery"] == [{"uid": 1, "name": "m"}]
    assert planning["system"]["name"] == "sys"
    assert (tmp_path / "x" / "Demand" / "lmax.parquet").is_file()


def test_main_without_clean_formatter(
    book: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "gtopt_check_json._terminal", None)
    out = tmp_path / "y.json"
    assert _main([str(book), "-j", str(out), "-d", str(tmp_path / "y")]) == 0
    assert out.is_file()


def test_main_requires_a_workbook(capsys) -> None:
    assert _main([]) == 2
    assert "at least one XLSX file is required" in capsys.readouterr().err


def test_main_reports_value_errors(
    book: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    def boom(_args: object) -> int:
        raise ValueError("bad thing")

    monkeypatch.setattr(ig, "_run", boom)
    assert _main([str(book), "-j", "o.json", "-d", "o", "-n", "o"]) == 1
    assert "unexpected error: bad thing" in caplog.text


# ── main(): template modes ──────────────────────────────────────────────


def test_main_template_missing_header_dir(tmp_path: pathlib.Path, caplog) -> None:
    rc = _main(["--make-template", "--header-dir", str(tmp_path / "nope")])
    assert rc == 1
    assert "header directory" in caplog.text


def test_main_list_sheets_fallback(tmp_path: pathlib.Path, capsys) -> None:
    hdr = tmp_path / "hdr"
    hdr.mkdir()
    ig.main(["--list-sheets", "--header-dir", str(hdr)])
    out = capsys.readouterr().out
    assert "json_simulation.hpp not found" in out
    assert "json_system.hpp not found" in out


def test_main_make_template_default_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "include" / "gtopt").mkdir(parents=True)
    monkeypatch.setattr(ig, "_find_repo_root", lambda _start: tmp_path)
    ig.main(["--make-template"])
    assert (tmp_path / "docs" / "templates" / "gtopt_template.xlsx").is_file()


def test_build_workbook_requires_openpyxl(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    with pytest.raises(SystemExit) as exc:
        tb._build_workbook(tmp_path / "t.xlsx", tmp_path)
    assert exc.value.code == 1
    assert "openpyxl is required" in capsys.readouterr().err


def test_build_array_sheets_skips_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    added: list[str] = []
    monkeypatch.setattr(
        tb, "_add_data_sheet", lambda _wb, _st, sheet, _f, _e: added.append(sheet)
    )
    tb._build_array_sheets(object(), tb._WorkbookStyles(), ["no_such_array"])
    assert not added


def test_list_sheets_reads_headers_without_json_subdir(
    tmp_path: pathlib.Path, capsys
) -> None:
    (tmp_path / "json_simulation.hpp").write_text(
        'json_array_null<"block_array", Array<Block>, &Simulation::block_array>\n'
    )
    (tmp_path / "json_system.hpp").write_text(
        'json_array_null<"bus_array", Array<Bus>, &System::bus_array>\n'
    )
    tb._list_sheets(tmp_path)
    out = capsys.readouterr().out
    assert "fallback" not in out


# ── _run(): error collection ────────────────────────────────────────────


def test_run_rejects_unsupported_suffix(tmp_path: pathlib.Path, caplog) -> None:
    bad = tmp_path / "data.txt"
    bad.write_text("x")
    assert ig._run(_args(bad, tmp_path)) == 1
    assert "unsupported file type '.txt'" in caplog.text
    assert "1 error(s) occurred" in caplog.text


def test_run_reports_unreadable_workbook(tmp_path: pathlib.Path, caplog) -> None:
    bad = tmp_path / "broken.xlsx"
    bad.write_bytes(b"not a zip")
    assert ig._run(_args(bad, tmp_path, validate=True)) == 1
    assert "could not read" in caplog.text


@pytest.mark.parametrize(
    ("exc", "text"),
    [
        (FileNotFoundError("gone"), "may have been removed"),
        (PermissionError("locked"), "permission denied"),
    ],
)
def test_run_open_errors(
    book: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
    exc: Exception,
    text: str,
) -> None:
    def raise_exc(*_a: object, **_k: object) -> None:
        raise exc

    monkeypatch.setattr(ig.pd, "read_excel", raise_exc)
    assert ig._run(_args(book, tmp_path)) == 1
    assert text in caplog.text


def test_run_collects_sheet_level_errors(
    book: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    sheets = _basic_sheets()
    sheets["boundary_cuts"] = pd.DataFrame({"name": ["c1"], "rhs": [1.0]})
    _write_book(book, sheets)

    def fail(*_a: object, **_k: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(ig, "df_to_file", fail)
    monkeypatch.setattr(ig, "_write_boundary_cuts_csv", fail)
    monkeypatch.setattr(ig, "df_to_str", fail)
    assert ig._run(_args(book, tmp_path)) == 1
    assert "could not write data file for sheet 'Demand@lmax'" in caplog.text
    assert "could not write boundary_cuts" in caplog.text
    assert "could not parse sheet 'bus_array'" in caplog.text
    assert not (tmp_path / "out.json").exists()


def test_run_system_only_and_no_skip_nulls(tmp_path: pathlib.Path) -> None:
    book = _write_book(
        tmp_path / "sys.xlsx",
        {"bus_array": pd.DataFrame({"uid": [1], "name": ["b1"], "x": [None]})},
    )
    assert ig._run(_args(book, tmp_path, skip_nulls=False)) == 0
    planning = json.loads((tmp_path / "out.json").read_text())
    assert "simulation" not in planning
    assert planning["system"]["bus_array"] == [{"uid": 1, "name": "b1", "x": None}]


def test_run_options_sheet_without_columns_exits(
    tmp_path: pathlib.Path, caplog
) -> None:
    book = _write_book(
        tmp_path / "o.xlsx", {"options": pd.DataFrame({"key": ["a"], "val": [1]})}
    )
    with pytest.raises(SystemExit) as exc:
        ig._run(_args(book, tmp_path))
    assert exc.value.code == 1
    assert "'options' sheet requires" in caplog.text


# ── helpers ─────────────────────────────────────────────────────────────


def test_build_name_to_uid_maps_ignores_bad_uids() -> None:
    xls = {
        "bus_array": pd.DataFrame(
            {"uid": ["x", 2, 3], "name": ["a", "b", float("nan")]}
        )
    }
    assert ig._build_name_to_uid_maps(xls) == {"Bus": {"b": "uid:2"}}


def test_wide_to_long_passthrough() -> None:
    long_df = pd.DataFrame({"block": [1], "uid": [1], "value": [2.0]})
    assert ig._wide_to_long(long_df) is long_df
    plain = pd.DataFrame({"block": [1], "value": [2.0]})
    assert ig._wide_to_long(plain) is plain


def test_df_to_file_parquet_with_codec_and_level(tmp_path: pathlib.Path) -> None:
    df = pd.DataFrame({"block": [1], "uid:3": [2.5]})
    path = ig.df_to_file(df, tmp_path, "Demand", "lmax", "parquet", "zstd", 3)
    back = pd.read_parquet(path)
    assert back["uid"].tolist() == [3]
    assert back["value"].tolist() == [2.5]
    plain = ig.df_to_file(df, tmp_path, "Demand", "lmin", "parquet", "", None)
    assert pd.read_parquet(plain)["uid"].tolist() == [3]


def test_nest_sub_options_merges_prebuilt_sections() -> None:
    flat = {
        "sddp_options": {"z": 0},
        "monolithic_options": {"a": 1},
        "model_options": {"b": 2},
        "lp_matrix_options": {"c": 3},
        "solver_options": {"d": 4},
        "annual_discount_rate": 0.1,  # dropped: no simulation dict
        "compute_stats": True,  # LP matrix key
        "solver_not_a_solver_key": 5,  # unknown solver_ key stays top-level
        "mip_start": 1,  # monolithic key without prefix
    }
    out = ig._nest_sub_options(flat)
    assert out["monolithic_options"] == {"a": 1, "mip_start": 1}
    assert out["model_options"] == {"b": 2}
    assert out["lp_matrix_options"] == {"c": 3, "compute_stats": True}
    assert out["solver_options"] == {"d": 4}
    assert out["sddp_options"] == {"z": 0}
    assert out["solver_not_a_solver_key"] == 5
    assert "annual_discount_rate" not in out


def test_log_conversion_stats_plain_fallback(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    monkeypatch.setitem(sys.modules, "gtopt_check_json._terminal", None)
    with caplog.at_level(logging.INFO):
        ig.log_conversion_stats({"bus_array": 3}, {}, 0.25)
    assert "=== System statistics ===" in caplog.text
    assert "Buses" in caplog.text
    assert "0.250s" in caplog.text
