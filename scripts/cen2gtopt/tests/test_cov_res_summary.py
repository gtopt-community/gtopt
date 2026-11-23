# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Extra coverage for :mod:`cen2gtopt._res_summary` (I/O edge cases)."""

from __future__ import annotations

import json
import lzma
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from cen2gtopt import _res_summary as rs


def _zip_bytes(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_extract_accdb_from_xz(tmp_path: Path) -> None:
    plain = tmp_path / "src.zip"
    _zip_bytes(plain, {"sub/Model.MDB": b"payload"})
    xz = tmp_path / "RES20250101.zip.xz"
    xz.write_bytes(lzma.compress(plain.read_bytes()))
    work = tmp_path / "work"
    work.mkdir()
    out = rs.extract_accdb(xz, work)
    assert out == work / "Model.MDB"
    assert out.read_bytes() == b"payload"
    assert (work / "RES20250101.zip").exists()


def test_extract_accdb_without_database_raises(tmp_path: Path) -> None:
    res = tmp_path / "RES.zip"
    _zip_bytes(res, {"readme.txt": b"x"})
    with pytest.raises(FileNotFoundError, match="no .accdb"):
        rs.extract_accdb(res, tmp_path)


def test_db_rows_cached_and_unknown_unit() -> None:
    calls: list[str] = []

    def table(name: str) -> pd.DataFrame:
        calls.append(name)
        if name == "t_unit":
            return pd.DataFrame({"unit_id": [1], "value": ["MW"]})
        return pd.DataFrame(
            {"property_id": [5.0], "unit_id": [7], "collection_id": [1], "name": ["P"]}
        )

    db = rs._Db(table)
    assert db.rows("t_property") == db.rows("t_property")
    assert calls.count("t_property") == 1
    # property_id "5.0" is normalised to "5"; unit 7 is unknown → "?".
    assert db.unit("5") == "?"
    # Property id not present at all → "?".
    assert db.unit("999") == "?"


def test_write_summary_without_source(tmp_path: Path) -> None:
    cmg = pd.DataFrame({"bus": ["b1"], "cmg": [1.0]})
    rs.write_summary({"k": 1}, cmg, tmp_path / "o", None)
    data = json.loads((tmp_path / "o" / "plexos_summary.json").read_text())
    assert data == {"k": 1}
    assert (tmp_path / "o" / "plexos_cmg_by_bus.csv").exists()
