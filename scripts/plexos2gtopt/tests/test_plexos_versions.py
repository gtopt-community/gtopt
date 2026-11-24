# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""PLEXOS multi-version support: detection, override, name-based ids.

Fixtures are shaped after the real CEN bundles: PLEXOS 8.100 (2021,
``.mdb`` solution, Node ``Price`` = property 1285, Storage ``End
Volume`` = 343) and 10.000 (2025-2026, ``.accdb``, Price 1412, End
Volume 646).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import shutil
import subprocess
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from gtopt_shared.source_versions import (
    PROVENANCE_KEY,
    SourceFormat,
    format_source_versions,
)
from plexos2gtopt import _plexos_versions as pv
from plexos2gtopt import compare_with_plexos as cwp
from plexos2gtopt import plexos2gtopt as conv
from plexos2gtopt import plexos_block_layout as pbl
from plexos2gtopt import plexos_csv as pcsv
from plexos2gtopt.main import make_parser
from plexos2gtopt.plexos2gtopt import convert_plexos_bundle
from plexos2gtopt.plexos_loader import locate_bundle
from plexos2gtopt.plexos_xml import NS, load_xml
from plexos2gtopt.tests import horizon_fixture as fx

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def _xml(version: str | None, saved_with: str | None = None) -> str:
    rows = []
    if version is not None:
        rows.append(("Version", version))
    if saved_with is not None:
        rows.append(("Saved With", saved_with))
    rows.insert(0, ("Dynamic", "0"))
    cfg = "".join(
        f"<t_config><element>{e}</element><value>{v}</value></t_config>"
        for e, v in rows
    )
    return (
        f'<?xml version="1.0" standalone="yes"?>\n<MasterDataSet xmlns="{NS[1:-1]}">'
        f"{cfg}<t_class><class_id>1</class_id><name>System</name></t_class>"
        "</MasterDataSet>\n"
    )


def _solution_meta(version: str) -> dict[str, list[dict[str, str]]]:
    """t_class / t_collection / t_property rows of a solution DB."""
    classes = [
        {"class_id": "1", "name": "System"},
        {"class_id": "22", "name": "Node"},
        {"class_id": "16", "name": "Storage"},
        {"class_id": "2", "name": "Generator"},
    ]
    if version == "8.100":
        colls = [("34", "1", "22", "Nodes"), ("6", "1", "16", "Storages")]
        colls.append(("1", "1", "2", "Generators"))
        props = [
            ("1285", "34", "Price"),
            ("343", "6", "End Volume"),
            ("1", "1", "Generation"),
            # an unrelated property that sits on the 10.000 End Volume id
            ("646", "34", "Phase Angle"),
        ]
    else:
        colls = [("281", "1", "22", "Nodes"), ("93", "1", "16", "Storages")]
        colls.append(("1", "1", "2", "Generators"))
        props = [
            ("1412", "281", "Price"),
            ("646", "93", "End Volume"),
            ("2", "1", "Generation"),
        ]
    return {
        "t_class": classes,
        "t_collection": [
            {
                "collection_id": c,
                "parent_class_id": p,
                "child_class_id": ch,
                "name": n,
            }
            for c, p, ch, n in colls
        ],
        "t_property": [
            {"property_id": i, "collection_id": c, "name": n} for i, c, n in props
        ],
        "t_config": [{"element": "Version", "value": f"{version} R01 Gold"}],
    }


def _csv(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


# --------------------------------------------------------------------------
# PlexosVersion
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("8.100", (8, 100)),
        ("8.100 R01 Gold", (8, 100)),
        ("PLEXOS Desktop 10.000 R08", (10, 0)),
        ("PLEXOS 9.000 R09 x64 Edition", (9, 0)),
        ("8.1", (8, 100)),
        ("10.2", (10, 200)),
    ],
)
def test_version_parse(text: str, expected: tuple[int, int]) -> None:
    v = pv.PlexosVersion.parse(text)
    assert v == pv.PlexosVersion(*expected)


@pytest.mark.parametrize("text", [None, "", "no version here"])
def test_version_parse_none(text: str | None) -> None:
    assert pv.PlexosVersion.parse(text) is None


def test_version_str_and_order() -> None:
    assert str(pv.PlexosVersion(8, 100)) == "8.100"
    assert str(pv.PlexosVersion(10, 0)) == "10.000"
    assert pv.PlexosVersion(8, 100) < pv.PlexosVersion(9, 0) < pv.PlexosVersion(10, 0)
    assert pv.PlexosVersion(9, 0).ordinal == 9000


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------


def test_detect_version_2021_and_2026_xml(tmp_path: Path) -> None:
    old = tmp_path / "old.xml"
    old.write_text(_xml("8.100", "PLEXOS 8.100 R01 x64 Edition"))
    new = tmp_path / "new.xml"
    new.write_text(_xml("10.000", "PLEXOS Desktop 10.000 R08"))
    assert pv.detect_version(old) == pv.PlexosVersion(8, 100)
    assert pv.detect_version(new) == pv.PlexosVersion(10, 0)
    cfg = pv.read_xml_config(old)
    assert cfg["Saved With"] == "PLEXOS 8.100 R01 x64 Edition"
    assert cfg["Dynamic"] == "0"


def test_detect_version_saved_with_fallback(tmp_path: Path) -> None:
    p = tmp_path / "x.xml"
    p.write_text(_xml(None, "PLEXOS 9.000 R09 x64 Edition"))
    assert pv.detect_version(p) == pv.PlexosVersion(9, 0)


def test_detect_version_absent(tmp_path: Path) -> None:
    p = tmp_path / "x.xml"
    p.write_text(_xml(None))
    assert pv.detect_version(p) is None


def test_detect_solution_version() -> None:
    assert pv.detect_solution_version(_solution_meta("8.100")["t_config"]) == (
        pv.PlexosVersion(8, 100)
    )
    assert pv.detect_solution_version([{"element": "Date", "value": "x"}]) is None


# --------------------------------------------------------------------------
# resolution / override / fallback
# --------------------------------------------------------------------------


def test_resolve_detected() -> None:
    prov: dict[str, Any] = {}
    r = pv.resolve_version(pv.PlexosVersion(8, 100), provenance=prov)
    assert r.source == "detected"
    assert r.version == pv.PlexosVersion(8, 100)
    assert r.profile.solution_ext == ".mdb"
    d = r.as_dict()
    assert d["plexos_version"] == "8.100"
    assert d["change_point_csvs"] is True
    rec = prov[PROVENANCE_KEY]
    assert rec["format"] == "PLEXOS"
    assert rec["version"] == "8.100"
    assert rec["detected"] == "8.100"
    assert rec["override"] is None
    assert rec["tested"] is True


def test_resolve_override_wins(caplog: pytest.LogCaptureFixture) -> None:
    prov: dict[str, Any] = {}
    with caplog.at_level(logging.INFO):
        r = pv.resolve_version(pv.PlexosVersion(10, 0), "8.1", provenance=prov)
    assert r.source == "override"
    assert r.version == pv.PlexosVersion(8, 100)  # "8.1" normalised to 8.100
    assert prov[PROVENANCE_KEY]["override"] == "8.100"
    assert prov[PROVENANCE_KEY]["detected"] == "10.000"
    assert "PLEXOS source version: 8.100 (override)" in caplog.text
    assert "overrides the version 10.000 detected" in caplog.text
    r2 = pv.resolve_version(None, pv.PlexosVersion(9, 0))
    assert r2.version == pv.PlexosVersion(9, 0)


@pytest.mark.parametrize(
    ("want", "got"),
    [
        ((8, 0), (8, 100)),  # older than all: the oldest
        ((9, 500), (9, 0)),  # newest declared not newer than it
        ((11, 0), (10, 0)),
    ],
)
def test_resolve_unknown_falls_back(
    want: tuple[int, int], got: tuple[int, int], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        r = pv.resolve_version(pv.PlexosVersion(*want))
    assert r.source == "closest"
    assert r.version == pv.PlexosVersion(*got)
    assert "unknown version" in caplog.text


def test_resolve_default_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        r = pv.resolve_version(None)
    assert r.source == "default"
    assert r.version == pv.DEFAULT_VERSION
    assert "not found in the bundle" in caplog.text
    assert "PLEXOS source version: 10.000 (default)" in caplog.text


def test_resolve_bad_override_raises() -> None:
    with pytest.raises(ValueError, match="unparseable"):
        pv.resolve_version(None, "latest")


def test_resolve_untested_profile_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    extra = pv.VersionProfile(
        version=pv.PlexosVersion(11, 0),
        tested=False,
        solution_ext=".accdb",
        bess_model="Battery class",
        data_files=(),
    )
    profiles = (*pv.PROFILES, extra)
    monkeypatch.setattr(pv, "PROFILES", profiles)
    monkeypatch.setattr(
        pv,
        "SOURCE_FORMAT",
        SourceFormat(
            name="PLEXOS",
            supported=tuple(str(p.version) for p in profiles),
            tested=tuple(str(p.version) for p in profiles if p.tested),
            default="10.000",
        ),
    )
    with caplog.at_level(logging.WARNING):
        r = pv.resolve_version(pv.PlexosVersion(11, 0))
    assert r.source == "detected"
    assert "supported but untested" in caplog.text
    assert "untested" in pv.versions_help()


def test_resolve_profile_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pv, "PROFILES", pv.PROFILES[:1])
    with pytest.raises(LookupError):
        pv.resolve_version(pv.PlexosVersion(10, 0))


def test_source_format_matches_profiles() -> None:
    sf = pv.SOURCE_FORMAT
    assert isinstance(sf, SourceFormat)
    assert sf.name == "PLEXOS"
    assert sf.supported == tuple(str(p.version) for p in pv.PROFILES)
    assert list(sf.supported) == sorted(
        sf.supported, key=lambda v: pv.PlexosVersion.parse(v) or pv.DEFAULT_VERSION
    )  # oldest first
    assert set(sf.tested) <= set(sf.supported)
    assert sf.default in sf.supported
    assert {"8.100", "9.000", "10.000"} <= set(sf.supported)


# --------------------------------------------------------------------------
# CLI: alias + generated help
# --------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--plexos-version", "--source-version"])
def test_cli_version_flags_alias(flag: str) -> None:
    args = make_parser().parse_args(["x", flag, "8.100"])
    assert args.plexos_version == "8.100"
    assert make_parser().parse_args(["x"]).plexos_version is None


def test_help_lists_versions_from_table() -> None:
    """``--help`` lists every supported and tested version, from PROFILES."""
    text = make_parser().format_help()
    assert pv.versions_help() in text
    for p in pv.PROFILES:
        line = next(
            (ln for ln in text.splitlines() if ln.split()[:1] == [str(p.version)]),
            None,
        )
        assert line is not None, f"{p.version} missing from --help"
        assert ("tested" if p.tested else "untested") in line
        assert p.solution_ext in line
    flat = re.sub(r"\s+", " ", text)
    assert format_source_versions(pv.SOURCE_FORMAT) in flat
    assert "--plexos-version X alias of --source-version" in flat


def test_help_follows_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """The listing is generated: a new profile shows up without new text."""
    extra = pv.VersionProfile(
        version=pv.PlexosVersion(12, 0),
        tested=False,
        solution_ext=".accdb",
        bess_model="Battery class",
        data_files=(),
    )
    monkeypatch.setattr(pv, "PROFILES", (*pv.PROFILES, extra))
    assert "12.000  untested" in pv.versions_help()


# --------------------------------------------------------------------------
# name-based ids
# --------------------------------------------------------------------------


def test_resolve_property_ids_by_name() -> None:
    old, new = _solution_meta("8.100"), _solution_meta("10.000")
    args_old = (old["t_class"], old["t_collection"], old["t_property"])
    args_new = (new["t_class"], new["t_collection"], new["t_property"])
    assert pv.resolve_property_ids(*args_old, "Node", "Price") == {"1285"}
    assert pv.resolve_property_ids(*args_new, "Node", "Price") == {"1412"}
    assert pv.resolve_property_ids(*args_new, "Node", "Price", collection="X") == set()
    assert pv.resolve_property_ids(*args_new, "Battery", "Load") == set()
    assert pv.resolve_solution_ids(*args_old) == {
        "NODE_PRICE": 1285,
        "STORAGE_END_VOLUME": 343,
        "GENERATOR_GENERATION": 1,
    }


def test_resolve_solution_ids_ambiguous(caplog: pytest.LogCaptureFixture) -> None:
    m = _solution_meta("10.000")
    m["t_property"].append(
        {"property_id": "9999", "collection_id": "281", "name": "Price"}
    )
    with caplog.at_level(logging.WARNING):
        ids = pv.resolve_solution_ids(m["t_class"], m["t_collection"], m["t_property"])
    assert "NODE_PRICE" not in ids
    assert "ambiguous" in caplog.text


def test_canonical_ids_match_legacy_constants() -> None:
    """The canonical (10.000) ids are the legacy compare_with_plexos ones."""
    for key, (*_rest, canon) in pv.SOLUTION_PROPERTIES.items():
        assert getattr(cwp, f"PLEXOS_PROP_{key}") == canon, key


def test_canonical_property_map() -> None:
    new = _solution_meta("10.000")
    assert not pv.canonical_property_map(*(new[t] for t in _META))
    old = _solution_meta("8.100")
    m = pv.canonical_property_map(*(old[t] for t in _META))
    assert m["1285"] == "1412"
    assert m["343"] == "646"
    assert m["1"] == "2"
    # the unrelated 8.100 property 646 must not pose as End Volume
    assert m["646"] == "-646"
    keys = [
        {"key_id": "1", "property_id": "1285"},
        {"key_id": "2", "property_id": "646"},
        {"key_id": "3", "property_id": "77"},
    ]
    out = pv.canonicalize_keys(keys, m)
    assert [k["property_id"] for k in out] == ["1412", "-646", "77"]
    assert keys[0]["property_id"] == "1285"  # input untouched
    assert pv.canonicalize_keys(keys, {})[0]["property_id"] == "1285"


_META = ("t_class", "t_collection", "t_property")


def test_find_solution_db() -> None:
    assert pv.find_solution_db(["a/Log.txt", "a/S.mdb", "a/S.accdb"]) == "a/S.accdb"
    assert pv.find_solution_db(["a/Log.txt", "a/S.MDB"]) == "a/S.MDB"
    assert pv.find_solution_db(["a/Log.txt"]) is None
    assert pv.is_solution_db("x.mdb") and pv.is_solution_db(Path("x.accdb"))
    assert not pv.is_solution_db("x.zip")


# --------------------------------------------------------------------------
# solution readers: .mdb discovery + canonical t_key
# --------------------------------------------------------------------------


def test_extract_mdb_from_res_zip(tmp_path: Path) -> None:
    res = tmp_path / "RES20211017.zip"
    inner = "Model M Solution/Model M Solution.mdb"
    with zipfile.ZipFile(res, "w") as zf:
        zf.writestr("Model M Solution/Model ( M ) Log.txt", "log")
        zf.writestr(inner, b"mdb")
    out = pbl.extract_accdb_from_res_zip(res)
    assert out is not None
    assert out.name == "Model M Solution.mdb"
    assert out.read_bytes() == b"mdb"


def test_compare_extract_mdb_from_res_zip(tmp_path: Path) -> None:
    res = tmp_path / "RES.zip"
    with zipfile.ZipFile(res, "w") as zf:
        zf.writestr("S/S.mdb", b"m")
    assert cwp._extract_accdb_from_res_zip(res, tmp_path).name == "S.mdb"
    empty = tmp_path / "E.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("S/log.txt", b"l")
    with pytest.raises(FileNotFoundError, match=r"\.mdb"):
        cwp._extract_accdb_from_res_zip(empty, tmp_path)


def _fake_mdb(monkeypatch: pytest.MonkeyPatch, tables: dict[str, Any]) -> list[str]:
    calls: list[str] = []

    def fake_check_output(cmd: list[str], **_kw: Any) -> str:
        assert cmd[0] == "mdb-export"
        calls.append(cmd[2])
        return _csv(tables.get(cmd[2], []))

    monkeypatch.setattr("subprocess.check_output", fake_check_output)
    return calls


def test_compare_mdb_dump_canonicalizes_8100_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tables: dict[str, Any] = dict(_solution_meta("8.100"))
    tables["t_key"] = [
        {"key_id": "10", "membership_id": "5", "property_id": "1285"},
        {"key_id": "11", "membership_id": "5", "property_id": "646"},
    ]
    calls = _fake_mdb(monkeypatch, tables)
    db = tmp_path / "S.mdb"
    db.write_bytes(b"x")
    keys = cwp._mdb_dump(db, "t_key")
    assert [k["property_id"] for k in keys] == [
        str(cwp.PLEXOS_PROP_NODE_PRICE),
        "-646",
    ]
    cwp._mdb_dump(db, "t_key")  # map cached per (path, mtime)
    assert calls.count("t_property") == 1
    assert not cwp._mdb_dump(db, "t_object")


def test_compare_mdb_dump_10000_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tables: dict[str, Any] = dict(_solution_meta("10.000"))
    tables["t_key"] = [{"key_id": "1", "membership_id": "5", "property_id": "1412"}]
    _fake_mdb(monkeypatch, tables)
    keys = cwp._mdb_dump(tmp_path / "missing.accdb", "t_key")
    assert keys[0]["property_id"] == "1412"


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd not installed")
def test_cached_t_key_canonical_for_8100(tmp_path: Path) -> None:
    """``extract_storage_solution_efin`` reads End Volume from an 8.100 cache."""
    tables: dict[str, Any] = dict(_solution_meta("8.100"))
    tables["t_object"] = [{"object_id": "7", "class_id": "16", "name": "RES1"}]
    tables["t_membership"] = [
        {"membership_id": "5", "child_object_id": "7", "parent_object_id": "1"}
    ]
    tables["t_key"] = [{"key_id": "10", "membership_id": "5", "property_id": "343"}]
    tables["t_data_0"] = [
        {"key_id": "10", "period_id": "1", "value": "3.5"},
        {"key_id": "10", "period_id": "2", "value": "4.25"},
    ]
    for name, rows in tables.items():
        subprocess.run(
            ["zstd", "-q", "-f", "-o", str(tmp_path / f"{name}.csv.zst")],
            input=_csv(rows).encode(),
            check=True,
        )
    assert pbl.extract_storage_solution_efin(tmp_path) == {"RES1": 4.25}
    traj = pbl.extract_storage_volume_trajectory(tmp_path)
    assert traj == {"RES1": {1: 3.5, 2: 4.25}}
    # an old cache without t_collection keeps the raw ids
    (tmp_path / "t_collection.csv.zst").unlink()
    assert not pbl._cached_property_id_map(tmp_path)


def test_cache_tables_include_version_metadata() -> None:
    assert {"t_collection", "t_config", "t_property", "t_class"} <= set(
        pbl._CACHED_PLEXOS_TABLES
    )


# --------------------------------------------------------------------------
# end to end: provenance
# --------------------------------------------------------------------------


def _stamp_version(bundle: Path, version: str) -> None:
    xml = bundle / "DBSEN_PRGDIARIO.xml"
    text = xml.read_text(encoding="utf-8")
    cfg = f"<t_config><element>Version</element><value>{version}</value></t_config>"
    text = re.sub(r"(<MasterDataSet[^>]*>)", rf"\g<1>{cfg}", text, count=1)
    xml.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    ("stamp", "override", "expected", "source"),
    [
        ("8.100", None, "8.100", "detected"),
        ("10.000", "9.000", "9.000", "override"),
        ("7.400", None, "8.100", "closest"),
    ],
)
def test_convert_records_version_in_provenance(
    tmp_path: Path, stamp: str, override: str | None, expected: str, source: str
) -> None:
    bundle = fx.write_bundle(tmp_path / "bundle")
    _stamp_version(bundle, stamp)
    out = tmp_path / "out"
    rc = convert_plexos_bundle(
        {
            "input_bundle": bundle,
            "output_dir": out,
            "output_file": out / "mini.json",
            "horizon_mode": "hourly",
            "horizon_days": 1,
            "lp_relax": True,
            "run_check": False,
            "plexos_version": override,
        }
    )
    assert rc == 0
    prov = json.loads((out / "mini.provenance.json").read_text())
    sf = prov[PROVENANCE_KEY]
    assert sf["format"] == "PLEXOS"
    assert sf["version"] == expected
    assert sf["detected"] == stamp
    assert sf["override"] == override
    assert sf["supported_versions"] == list(pv.SOURCE_FORMAT.supported)
    assert prov["plexos_profile"]["plexos_version"] == expected
    assert prov["plexos_profile"]["source"] == source


# --------------------------------------------------------------------------
# 8.100 Data Files: ';' delimiter, change points starting before the horizon
# --------------------------------------------------------------------------


def test_sniff_delimiter_semicolon_nod_load(tmp_path: Path) -> None:
    """CEN 2021 Nod_Load.csv is ';'-separated; read_wide must see its buses."""
    p = tmp_path / "Nod_Load.csv"
    rows = ["YEAR;MONTH;DAY;PERIOD;BusA;BusB"]
    rows += [f"2021;10;17;{h};{100 + h};5.5" for h in range(1, 25)]
    p.write_text("\n".join(rows) + "\n")
    with p.open() as fh:
        assert pcsv.sniff_delimiter(fh) == ";"
        assert fh.tell() == 0
    out = pcsv.read_wide(p)
    assert set(out) == {"BusA", "BusB"}
    assert out["BusA"][0] == pytest.approx(101.0)
    assert out["BusB"][23] == pytest.approx(5.5)
    comma = tmp_path / "c.csv"
    comma.write_text("NAME,YEAR,MONTH,DAY,PERIOD,VALUE\nG,2021,10,17,1,3\n")
    assert pcsv.read_long(comma)["G"][0] == pytest.approx(3.0)


def test_densify_long_change_points(tmp_path: Path) -> None:
    """Change points carry forward; rows before the horizon seed it."""
    src = tmp_path / "Gen_Rating.csv"
    src.write_text(
        "NAME,YEAR,MONTH,DAY,PERIOD,BAND,VALUE\n"
        "BAT,2021,10,16,1,1,10\n"  # before the horizon: seeds day 1
        "BAT,2021,10,18,9,1,0\n"  # out from day 2 h9
        "BAT,2021,10,18,19,1,10\n"  # back at h19
        "LATE,2021,10,17,5,1,7\n"  # first change point inside day 1
        "LATE,2021,10,17,5,2,9\n"  # band 2 is its own series
    )
    dst = tmp_path / "out.csv"
    assert pcsv.densify_dated_csv(src, dst, start=date(2021, 10, 17), n_days=2)
    first = dst.read_text().splitlines()[1]
    assert first.split(",")[1:5] == ["2021", "10", "17", "1"]  # day 1 first
    bat = pcsv.read_long(dst, n_days=2)["BAT"]
    assert bat[:24] == [10.0] * 24
    assert bat[24:32] == [10.0] * 8
    assert bat[32:42] == [0.0] * 10
    assert bat[42:] == [10.0] * 6
    late = pcsv.read_long(dst, n_days=2)["LATE"]
    assert late[:4] == [0.0] * 4
    assert late[4:] == [7.0] * 44
    assert pcsv.read_long(dst, band=2, n_days=2)["LATE"][4] == pytest.approx(9.0)


def test_densify_wide_semicolon_drops_pre_horizon_day(tmp_path: Path) -> None:
    src = tmp_path / "Nod_Load.csv"
    rows = ["YEAR;MONTH;DAY;PERIOD;B1"]
    rows += [f"2021;10;16;{h};1" for h in range(1, 25)]
    rows += [f"2021;10;17;{h};{h}" for h in range(1, 25)]
    src.write_text("\n".join(rows) + "\n")
    dst = tmp_path / "out.csv"
    assert pcsv.densify_dated_csv(src, dst, start=date(2021, 10, 17), n_days=1)
    assert dst.read_text().splitlines()[0] == "YEAR,MONTH,DAY,PERIOD,B1"
    assert pcsv.read_wide(dst)["B1"] == [float(h) for h in range(1, 25)]


def test_densify_skips_undated_and_empty(tmp_path: Path) -> None:
    undated = tmp_path / "Gen_AuxUse.csv"
    undated.write_text("NAME,VALUE\nG,0.1\n")
    empty = tmp_path / "e.csv"
    empty.write_text("")
    bad = tmp_path / "b.csv"
    bad.write_text("NAME,YEAR,MONTH,DAY,PERIOD,VALUE\nG,x,10,17,1,1\nG,2021,10,17,2\n")
    dst = tmp_path / "o.csv"
    start = date(2021, 10, 17)
    assert not pcsv.densify_dated_csv(undated, dst, start=start, n_days=1)
    assert not pcsv.densify_dated_csv(empty, dst, start=start, n_days=1)
    assert pcsv.densify_dated_csv(bad, dst, start=start, n_days=1)
    # the short row is padded (empty VALUE) and carried; the bad date dropped
    assert len(dst.read_text().splitlines()) == 1 + 23


def test_bundle_normalize_dated_csvs_overrides(tmp_path: Path) -> None:
    root = tmp_path / "b"
    root.mkdir()
    (root / "DBSEN_PRGDIARIO.xml").write_text(_xml("8.100"))
    (root / "Gen_Rating.csv").write_text(
        "NAME,YEAR,MONTH,DAY,PERIOD,BAND,VALUE\nG,2021,10,16,1,1,5\n"
    )
    (root / "Gen_AuxUse.csv").write_text("NAME,VALUE\nG,0.1\n")
    with locate_bundle(root) as bundle:
        done = bundle.normalize_dated_csvs(date(2021, 10, 17), 1)
        assert done == ["Gen_Rating.csv"]
        assert bundle.csv("Gen_Rating.csv") != root / "Gen_Rating.csv"
        assert bundle.csv("Gen_AuxUse.csv") == root / "Gen_AuxUse.csv"
        assert bundle.has("Gen_Rating.csv")
        assert pcsv.read_long(bundle.csv("Gen_Rating.csv"))["G"] == [5.0] * 24
    # the source file is untouched
    assert "2021,10,16" in (root / "Gen_Rating.csv").read_text()


def test_densify_hook_warns_without_horizon(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "b"
    root.mkdir()
    (root / "DBSEN_PRGDIARIO.xml").write_text(_xml("8.100"))
    with locate_bundle(root) as bundle, caplog.at_level(logging.WARNING):
        conv._densify_change_point_csvs(bundle)
        assert not bundle.csv_overrides
    assert "no Horizon 'Date From'" in caplog.text
    old = pv.profile_for(pv.PlexosVersion(8, 100))
    new = pv.profile_for(pv.PlexosVersion(10, 0))
    assert old is not None and old.change_point_csvs
    assert new is not None and not new.change_point_csvs


def test_convert_8100_bundle_densifies(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An 8.100-stamped bundle goes through the densify step end to end."""
    bundle = fx.write_bundle(tmp_path / "bundle")
    _stamp_version(bundle, "8.100")
    out = tmp_path / "out"
    with caplog.at_level(logging.INFO):
        rc = convert_plexos_bundle(
            {
                "input_bundle": bundle,
                "output_dir": out,
                "output_file": out / "mini.json",
                "horizon_mode": "hourly",
                "horizon_days": 1,
                "lp_relax": True,
                "run_check": False,
            }
        )
    assert rc == 0
    assert "densified onto the horizon" in caplog.text


# --------------------------------------------------------------------------
# 8.100 XML: Constraint -> X terms, renamed collections
# --------------------------------------------------------------------------


def _layout_xml(version: str) -> str:
    """Generator G1 with coefficient 2.5 in constraint C1, battery B1 at N1."""
    cls = [(1, "System"), (2, "Generator"), (3, "Constraint"), (4, "Battery")]
    cls.append((5, "Node"))
    objs = [(11, 2, "G1"), (12, 3, "C1"), (13, 4, "B1"), (14, 5, "N1")]
    if version == "8.100":
        colls = [(10, 3, 2, "Generators"), (20, 4, 5, "Node"), (30, 3, 3, "Cs")]
        mems = [(50, 10, 12, 11), (60, 20, 13, 14), (70, 30, 12, 12)]
        props = [(100, 10, "Generation Coefficient"), (101, 30, "X")]
    else:
        colls = [(10, 2, 3, "Constraints"), (20, 4, 5, "Nodes")]
        mems = [(50, 10, 11, 12), (60, 20, 13, 14)]
        props = [(100, 10, "Generation Coefficient")]
    body = "".join(
        f"<t_class><class_id>{i}</class_id><name>{n}</name></t_class>" for i, n in cls
    )
    body += "".join(
        f"<t_object><object_id>{i}</object_id><class_id>{c}</class_id>"
        f"<name>{n}</name></t_object>"
        for i, c, n in objs
    )
    body += "".join(
        f"<t_collection><collection_id>{i}</collection_id>"
        f"<parent_class_id>{p}</parent_class_id><child_class_id>{c}"
        f"</child_class_id><name>{n}</name></t_collection>"
        for i, p, c, n in colls
    )
    body += "".join(
        f"<t_membership><membership_id>{i}</membership_id><collection_id>{c}"
        f"</collection_id><parent_object_id>{p}</parent_object_id>"
        f"<child_object_id>{ch}</child_object_id></t_membership>"
        for i, c, p, ch in mems
    )
    body += "".join(
        f"<t_property><property_id>{i}</property_id><collection_id>{c}"
        f"</collection_id><name>{n}</name></t_property>"
        for i, c, n in props
    )
    body += (
        "<t_data><data_id>1</data_id><membership_id>50</membership_id>"
        "<property_id>100</property_id><value>2.5</value></t_data>"
    )
    return (
        f'<?xml version="1.0" standalone="yes"?>\n<MasterDataSet xmlns="{NS[1:-1]}">'
        f"{body}</MasterDataSet>\n"
    )


@pytest.mark.parametrize("version", ["8.100", "10.000"])
def test_constraint_terms_read_alike(tmp_path: Path, version: str) -> None:
    """Both layouts expose G1 -> C1 'Generation Coefficient' = 2.5."""
    p = tmp_path / f"{version}.xml"
    p.write_text(_layout_xml(version))
    db = load_xml(p)
    coll = db.collection_for_named("Generator", "Constraint", "Constraints")
    assert coll is not None
    (m,) = db.memberships_of(coll.collection_id)
    objs = db.object_by_id()
    pair = (objs[m.parent_object_id].name, objs[m.child_object_id].name)
    assert pair == ("G1", "C1")
    pid = db.property_by_name(coll.collection_id, "Generation Coefficient")
    rows = [d for d in db.data_rows if d.membership_id == m.membership_id]
    assert [(d.property_id, d.value) for d in rows] == [(pid, 2.5)]
    bat = db.collection_for_named("Battery", "Node", "Nodes")
    assert bat is not None
    assert db.parent_to_children(bat.collection_id) == {13: [14]}
    # no alias and no match stays None
    assert db.collection_for_named("Battery", "Node", "Missing") is None


def test_mirror_is_noop_without_constraint_parent(tmp_path: Path) -> None:
    p = tmp_path / "x.xml"
    p.write_text(_layout_xml("10.000"))
    assert pv.mirror_constraint_memberships(load_xml(p)) == 0
    p2 = tmp_path / "y.xml"
    p2.write_text(_xml("8.100"))  # no Constraint class at all
    assert pv.mirror_constraint_memberships(load_xml(p2)) == 0


def test_mirror_reuses_existing_terms_collection(tmp_path: Path) -> None:
    """An 8.x file that also declares Generator -> Constraint 'Constraints'."""
    text = _layout_xml("8.100").replace(
        "</MasterDataSet>",
        "<t_collection><collection_id>40</collection_id><parent_class_id>2"
        "</parent_class_id><child_class_id>3</child_class_id><name>Constraints"
        "</name></t_collection><t_property><property_id>140</property_id>"
        "<collection_id>40</collection_id><name>Generation Coefficient</name>"
        "</t_property></MasterDataSet>",
    )
    p = tmp_path / "x.xml"
    p.write_text(text)
    db = load_xml(p)
    coll = db.collection_for_named("Generator", "Constraint", "Constraints")
    assert coll is not None
    assert coll.collection_id == 40
    (m,) = db.memberships_of(40)
    (row,) = [d for d in db.data_rows if d.membership_id == m.membership_id]
    assert row.property_id == 140
