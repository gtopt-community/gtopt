# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage for :mod:`plexos2gtopt.plexos_block_layout` (mdb/zstd mocked)."""

from __future__ import annotations

import lzma
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

from plexos2gtopt import plexos_block_layout as pbl

# ---------------------------------------------------------------------------
# subprocess fakes
# ---------------------------------------------------------------------------


def _with_mdb(monkeypatch: pytest.MonkeyPatch, present: bool = True) -> None:
    monkeypatch.setattr(pbl, "_have_mdb_tools", lambda: present)


def _fake_run(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[list[str]]:
    cmds: list[list[str]] = []

    def _run(cmd: list[str], **kw: Any) -> Any:
        cmds.append(cmd)
        return handler(cmd, kw)

    monkeypatch.setattr(subprocess, "run", _run)
    return cmds


def _ok(stdout: Any) -> Any:
    return lambda cmd, _kw: subprocess.CompletedProcess(cmd, 0, stdout, "")


def _accdb(tmp_path: Path) -> Path:
    p = tmp_path / "sol.accdb"
    p.write_bytes(b"fake")
    return p


# ---------------------------------------------------------------------------
# load_block_layout_from_accdb
# ---------------------------------------------------------------------------


def test_have_mdb_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    assert not pbl._have_mdb_tools()


def test_layout_export_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_mdb(monkeypatch)

    def _fail(cmd: list[str], _kw: Any) -> Any:
        raise subprocess.CalledProcessError(1, cmd)

    _fake_run(monkeypatch, _fail)
    assert not pbl.load_block_layout_from_accdb(_accdb(tmp_path))


@pytest.mark.parametrize(
    "stdout",
    ["", "foo,bar\n1,2\n", "interval_id,period_id\n\n1\n,3\nx,1\n"],
)
def test_layout_empty_or_bad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    _with_mdb(monkeypatch)
    _fake_run(monkeypatch, _ok(stdout))
    assert not pbl.load_block_layout_from_accdb(_accdb(tmp_path))


def test_layout_parses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_mdb(monkeypatch)
    _fake_run(
        monkeypatch,
        _ok("Interval_ID,Period_ID\n3,2\n1,1\n2,1\n4,2\n5,\n"),
    )
    assert pbl.load_block_layout_from_accdb(_accdb(tmp_path)) == ((1, 2), (3, 4))


# ---------------------------------------------------------------------------
# infer_horizon_days_from_input
# ---------------------------------------------------------------------------


def _hz_xml(tmp_path: Path, objects: str, cls: str = "Horizon") -> Path:
    p = tmp_path / "in.xml"
    p.write_text(
        "<t_class><class_id>9</class_id><name>Other</name></t_class>"
        f"<t_class><class_id>5</class_id><name>{cls}</name></t_class>" + objects,
        encoding="latin1",
    )
    return p


def test_infer_horizon_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    obj = "<t_object><class_id>{c}</class_id><name>{n}</name></t_object>"
    p = _hz_xml(
        tmp_path,
        obj.format(c=9, n="X_7d")
        + obj.format(c=5, n="NoDays")
        + obj.format(c=5, n="H_400d")
        + obj.format(c=5, n="H_3d"),
    )
    assert pbl.infer_horizon_days_from_input(p) == 3
    p = _hz_xml(tmp_path, obj.format(c=5, n="NoDays"))
    assert pbl.infer_horizon_days_from_input(p) is None
    p = _hz_xml(tmp_path, obj.format(c=5, n="H_7d"), cls="NotHorizon")
    assert pbl.infer_horizon_days_from_input(p) is None

    def _boom(*_a: Any, **_k: Any) -> str:
        raise OSError("io")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert pbl.infer_horizon_days_from_input(p) is None


# ---------------------------------------------------------------------------
# compute_day_ending_blocks / parse_user_block_layout
# ---------------------------------------------------------------------------


def test_day_ending_blocks() -> None:
    assert not pbl.compute_day_ending_blocks(())
    hourly = tuple((h,) for h in range(1, 49))
    assert pbl.compute_day_ending_blocks(hourly) == (24, 48)
    # A block straddling day 1 -> day 1 has no ending block; an empty block
    # counts as hour 0.
    straddle = ((), tuple(range(1, 30)), tuple(range(30, 49)))
    assert pbl.compute_day_ending_blocks(straddle) == (1, 3)
    # No block ends inside day 1 -> that day is skipped.
    assert pbl.compute_day_ending_blocks((tuple(range(1, 30)),)) == (1,)


def test_user_layout_csv_variants(tmp_path: Path) -> None:
    p = tmp_path / "l.csv"
    p.write_text("Block,Hours\n2,3\n1,1\nx,\n3,0\n")
    assert pbl.parse_user_block_layout(str(p)) == ((1,), (2, 3, 4))
    p.write_text("dur\n2\n1\n")
    assert pbl.parse_user_block_layout(str(p)) == ((1, 2), (3,))
    p.write_text("foo\n1\n")
    with pytest.raises(ValueError, match="needs a 'duration'"):
        pbl.parse_user_block_layout(str(p))


def test_user_layout_inline_blank_tokens() -> None:
    assert pbl.parse_user_block_layout("{2:2,,1:1}") == ((1,), (2, 3))


# ---------------------------------------------------------------------------
# RES zip extraction
# ---------------------------------------------------------------------------


def _res_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_extract_accdb_paths(tmp_path: Path) -> None:
    assert pbl.extract_accdb_from_res_zip(tmp_path / "missing.zip") is None
    z = _res_zip(tmp_path / "RES1.zip", {"M Solution/M Solution.accdb": b"db"})
    got = pbl.extract_accdb_from_res_zip(z)
    assert got is not None and got.read_bytes() == b"db"
    xz = tmp_path / "RES2.zip.xz"
    with lzma.open(xz, "wb") as fh:
        fh.write(z.read_bytes())
    got = pbl.extract_accdb_from_res_zip(xz)
    assert got is not None and got.name == "M Solution.accdb"
    bad_xz = tmp_path / "RES3.zip.xz"
    bad_xz.write_bytes(b"garbage")
    assert pbl.extract_accdb_from_res_zip(bad_xz) is None
    no_db = _res_zip(tmp_path / "RES4.zip", {"readme.txt": b"x"})
    assert pbl.extract_accdb_from_res_zip(no_db) is None
    not_zip = tmp_path / "RES5.zip"
    not_zip.write_bytes(b"nope")
    assert pbl.extract_accdb_from_res_zip(not_zip) is None


def test_layout_from_res_zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert not pbl.load_block_layout_from_res_zip(tmp_path / "missing.zip")
    z = _res_zip(tmp_path / "RES1.zip", {"a.accdb": b"db"})
    _with_mdb(monkeypatch)
    _fake_run(monkeypatch, _ok("interval_id,period_id\n1,1\n"))
    assert pbl.load_block_layout_from_res_zip(z) == ((1,),)


# ---------------------------------------------------------------------------
# cache_plexos_tables / _read_cached_csv
# ---------------------------------------------------------------------------


def test_cache_without_mdb_or_accdb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_mdb(monkeypatch, present=False)
    assert pbl.cache_plexos_tables(_accdb(tmp_path), tmp_path) is None
    _with_mdb(monkeypatch)
    assert pbl.cache_plexos_tables(tmp_path / "missing.accdb", tmp_path) is None
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert pbl.cache_plexos_tables(_accdb(tmp_path), blocker) is None


def test_cache_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_mdb(monkeypatch)
    accdb = _accdb(tmp_path)
    out = tmp_path / "out"
    fresh = out / "plexos_cache" / "t_key.csv.zst"
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(b"cached")

    def _handler(cmd: list[str], kw: Any) -> Any:
        if cmd[0] == "mdb-export":
            if cmd[2] == "t_bad_export":
                raise subprocess.TimeoutExpired(cmd, 1)
            return subprocess.CompletedProcess(cmd, 0, b"a,b\n1,2\n", b"")
        if "t_bad_zstd" in cmd[-1]:
            raise subprocess.CalledProcessError(1, cmd)
        Path(cmd[-1]).write_bytes(kw["input"])
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    cmds = _fake_run(monkeypatch, _handler)
    cache = pbl.cache_plexos_tables(
        accdb, out, tables=("t_key", "t_object", "t_bad_export", "t_bad_zstd")
    )
    assert cache == out / "plexos_cache"
    assert (cache / "t_object.csv.zst").read_bytes() == b"a,b\n1,2\n"
    assert not any(c[0] == "mdb-export" and c[2] == "t_key" for c in cmds)


def test_read_cached_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert pbl._read_cached_csv(tmp_path, "t_object") is None
    (tmp_path / "t_object.csv.zst").write_bytes(b"x")
    _fake_run(monkeypatch, _ok("object_id,name\n1,A\n"))
    assert pbl._read_cached_csv(tmp_path, "t_object") == [
        {"object_id": "1", "name": "A"}
    ]

    def _missing(cmd: list[str], _kw: Any) -> Any:
        raise FileNotFoundError("zstd")

    _fake_run(monkeypatch, _missing)
    assert pbl._read_cached_csv(tmp_path, "t_object") is None


# ---------------------------------------------------------------------------
# extract_* readers over a synthetic solution cache
# ---------------------------------------------------------------------------

_TABLES: dict[str, list[dict[str, str]]] = {
    "t_class": [
        {"class_id": "2", "name": "Generator"},
        {"class_id": "7", "name": "Storage"},
        {"class_id": "8", "name": "Constraint"},
        {"class_id": "9", "name": "Fuel"},
    ],
    "t_object": [
        {"object_id": "1", "class_id": "2", "name": "G1"},
        {"object_id": "2", "class_id": "7", "name": "S1"},
        {"object_id": "3", "class_id": "8", "name": "FueMaxOffWeek_GasA"},
        {"object_id": "4", "class_id": "8", "name": "FueMaxOffDay_GasA"},
        {"object_id": "5", "class_id": "8", "name": "OtherConstraint"},
        {"object_id": "6", "class_id": "9", "name": "GasA"},
        {"object_id": "10", "class_id": "8", "name": "FueMaxOffWeek_Orphan"},
        {"object_id": "11", "class_id": "8", "name": "FueMaxOffDay_Zero"},
        {"object_id": "12", "class_id": "9", "name": "GasZ"},
        {"object_id": "13", "class_id": "8", "name": "FueMaxOffWeek_GasZ"},
        {"object_id": "14", "class_id": "4", "name": "Unrelated"},
    ],
    "t_membership": [
        {"membership_id": "100", "parent_object_id": "0", "child_object_id": "1"},
        {"membership_id": "101", "parent_object_id": "0", "child_object_id": "2"},
        {"membership_id": "102", "parent_object_id": "6", "child_object_id": "3"},
        {"membership_id": "103", "parent_object_id": "6", "child_object_id": "4"},
        {"membership_id": "104", "parent_object_id": "3", "child_object_id": "0"},
        {"membership_id": "105", "parent_object_id": "99", "child_object_id": "10"},
        {"membership_id": "106", "parent_object_id": "12", "child_object_id": "11"},
        {"membership_id": "107", "parent_object_id": "12", "child_object_id": "13"},
        {"membership_id": "108", "parent_object_id": "0", "child_object_id": "14"},
    ],
    "t_property": [
        {"property_id": "3073", "name": "RHS"},
        {"property_id": "1", "name": "Other"},
    ],
    "t_key": [
        {"key_id": "k_gen", "membership_id": "100", "property_id": "2"},
        {"key_id": "k_ug", "membership_id": "100", "property_id": "7"},
        {"key_id": "k_ev", "membership_id": "101", "property_id": "646"},
        {"key_id": "k_wk", "membership_id": "102", "property_id": "3073"},
        {"key_id": "k_dy", "membership_id": "103", "property_id": "3073"},
        {"key_id": "k_wk2", "membership_id": "104", "property_id": "3073"},
        {"key_id": "k_orph", "membership_id": "105", "property_id": "3073"},
        {"key_id": "k_zero", "membership_id": "106", "property_id": "3073"},
        {"key_id": "k_z7", "membership_id": "107", "property_id": "3073"},
        {"key_id": "k_none", "membership_id": "108", "property_id": "3073"},
        {"key_id": "k_skip", "membership_id": "999", "property_id": "2"},
    ],
    "t_data_0": [
        {"key_id": "k_gen", "period_id": "1", "value": "10"},
        {"key_id": "k_gen", "period_id": "2", "value": "20"},
        {"key_id": "k_gen", "period_id": "x", "value": "1"},
        {"key_id": "k_ug", "period_id": "1", "value": "1"},
        {"key_id": "k_ug", "period_id": "2", "value": "bad"},
        {"key_id": "k_ev", "period_id": "2", "value": "50"},
        {"key_id": "k_ev", "period_id": "1", "value": "40"},
        {"key_id": "k_ev", "period_id": "3", "value": "?"},
        {"key_id": "k_wk", "period_id": "1", "value": "84"},
        {"key_id": "k_wk", "period_id": "2", "value": "nan?"},
        {"key_id": "k_wk2", "period_id": "2", "value": "84"},
        {"key_id": "k_dy", "period_id": "1", "value": "48"},
        {"key_id": "k_orph", "period_id": "1", "value": "5"},
        {"key_id": "k_zero", "period_id": "1", "value": "0"},
        {"key_id": "k_z7", "period_id": "1", "value": "1"},
        {"key_id": "unknown", "period_id": "1", "value": "1"},
    ],
}


def _cache(
    monkeypatch: pytest.MonkeyPatch, tables: dict[str, list[dict[str, str]]]
) -> None:
    monkeypatch.setattr(pbl, "_read_cached_csv", lambda _d, t: tables.get(t))


_READERS = (
    pbl.extract_storage_solution_efin,
    pbl.extract_storage_volume_trajectory,
    pbl.extract_generator_generation_per_period,
    pbl.extract_generator_commit_per_period,
    pbl.extract_fuel_offtake_caps,
)


def test_readers(monkeypatch: pytest.MonkeyPatch) -> None:
    _cache(monkeypatch, _TABLES)
    d = Path("cache")
    assert pbl.extract_storage_solution_efin(d) == {"S1": 50.0}
    assert pbl.extract_storage_volume_trajectory(d) == {"S1": {1: 40.0, 2: 50.0}}
    assert pbl.extract_generator_generation_per_period(d) == {"G1": {1: 10.0, 2: 20.0}}
    assert pbl.extract_generator_commit_per_period(d) == {"G1": {1: 1.0}}
    caps = pbl.extract_fuel_offtake_caps(d)
    # Week: 84+84 = 168 over 168 h (1/h); Day: 48 over 24 h (2/h) -> Week wins.
    assert caps == {"GasA": (168.0, 168.0), "GasZ": (1.0, 168.0)}
    assert pbl.extract_fuel_offtake_caps.rhs_per_period["GasA"] == {1: 84.0, 2: 84.0}  # type: ignore[attr-defined]


def test_fuel_caps_day_tighter(monkeypatch: pytest.MonkeyPatch) -> None:
    tables = dict(_TABLES)
    tables["t_data_0"] = [
        {"key_id": "k_wk", "period_id": "1", "value": "1000"},
        {"key_id": "k_dy", "period_id": "1", "value": "1"},
    ]
    _cache(monkeypatch, tables)
    assert pbl.extract_fuel_offtake_caps(Path("c")) == {"GasA": (1.0, 24.0)}


@pytest.mark.parametrize("reader", _READERS)
def test_readers_missing_table(monkeypatch: pytest.MonkeyPatch, reader: Any) -> None:
    for drop in ("t_object", "t_key"):
        tables = {k: v for k, v in _TABLES.items() if k != drop}
        _cache(monkeypatch, tables)
        assert reader(Path("c")) is None


@pytest.mark.parametrize("reader", _READERS)
def test_readers_missing_class(monkeypatch: pytest.MonkeyPatch, reader: Any) -> None:
    tables = dict(_TABLES)
    tables["t_class"] = [{"class_id": "77", "name": "Nothing"}]
    _cache(monkeypatch, tables)
    assert reader(Path("c")) is None


def test_readers_no_objects_or_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    tables = dict(_TABLES)
    tables["t_object"] = []
    _cache(monkeypatch, tables)
    assert pbl.extract_storage_solution_efin(Path("c")) is None
    assert pbl.extract_generator_commit_per_period(Path("c")) is None
    assert pbl.extract_fuel_offtake_caps(Path("c")) == {}
    tables = dict(_TABLES)
    tables["t_key"] = []
    _cache(monkeypatch, tables)
    assert pbl.extract_storage_solution_efin(Path("c")) == {}
    assert pbl.extract_generator_commit_per_period(Path("c")) == {}


def test_fuel_caps_no_rhs_property(monkeypatch: pytest.MonkeyPatch) -> None:
    tables = dict(_TABLES)
    tables["t_property"] = [{"property_id": "1", "name": "Other"}]
    _cache(monkeypatch, tables)
    assert pbl.extract_fuel_offtake_caps(Path("c")) is None


# ---------------------------------------------------------------------------
# auto_discover_res_zip
# ---------------------------------------------------------------------------


def test_auto_discover_dir_without_datos(tmp_path: Path) -> None:
    (tmp_path / "RES20250101.zip").write_bytes(b"")
    (tmp_path / "RESnotes.txt").write_text("")
    assert pbl.auto_discover_res_zip(tmp_path) == (tmp_path / "RES20250101.zip")


def test_auto_discover_datos_without_date(tmp_path: Path) -> None:
    (tmp_path / "RES20250101.zip").write_bytes(b"")
    (tmp_path / "DATOS.zip").write_bytes(b"")
    assert pbl.auto_discover_res_zip(tmp_path) == (tmp_path / "RES20250101.zip")


def test_auto_discover_file_not_datos(tmp_path: Path) -> None:
    f = tmp_path / "bundle.zip"
    f.write_bytes(b"")
    assert pbl.auto_discover_res_zip(f) is None
