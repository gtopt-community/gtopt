# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for :mod:`plexos2gtopt.plexos_loader`."""

from __future__ import annotations

import lzma
import zipfile
from pathlib import Path

import pytest

from plexos2gtopt.plexos_loader import DBSEN_FILENAME, PlexosBundle, locate_bundle

_XML = "<MasterDataSet xmlns='http://tempuri.org/MasterDataSet.xsd'/>"


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_csv_accessors(tmp_path: Path) -> None:
    (tmp_path / DBSEN_FILENAME).write_text(_XML)
    (tmp_path / "Gen_Rating.csv").write_text("x\n")
    (tmp_path / "notes.txt").write_text("x\n")
    with locate_bundle(tmp_path) as bundle:
        assert bundle.csv_paths() == [tmp_path / "Gen_Rating.csv"]
        assert bundle.csv("Gen_Rating.csv") == tmp_path / "Gen_Rating.csv"
        assert bundle.has("Gen_Rating.csv")
        assert bundle.param_xml_path.name == "PLEXOS_Param.xml"
        with pytest.raises(FileNotFoundError, match="Missing.csv"):
            bundle.csv("Missing.csv")


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        locate_bundle(tmp_path / "nope")


def test_xml_xz_corrupt_cleans_up(tmp_path: Path) -> None:
    bad = tmp_path / "model.xml.xz"
    bad.write_bytes(b"not xz at all")
    with pytest.raises(lzma.LZMAError):
        locate_bundle(bad)


def test_xml_xz_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "model.xml.xz"
    with lzma.open(src, "wb") as fh:
        fh.write(_XML.encode())
    bundle = locate_bundle(src)
    try:
        assert bundle.xml_path.read_text() == _XML
    finally:
        bundle.close()
    assert not bundle.root.exists()


def test_dir_without_archives(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no DATOS"):
        locate_bundle(tmp_path)


def test_dir_with_several_archives(tmp_path: Path) -> None:
    _zip(tmp_path / "DATOS20250101.zip", {DBSEN_FILENAME: _XML.encode()})
    _zip(tmp_path / "DATOS20250102.zip", {DBSEN_FILENAME: _XML.encode()})
    with pytest.raises(FileNotFoundError, match="2 DATOS"):
        locate_bundle(tmp_path)


def test_dir_with_single_xz_archive(tmp_path: Path) -> None:
    raw = _zip(tmp_path / "inner.zip", {DBSEN_FILENAME: _XML.encode()})
    sub = tmp_path / "case"
    sub.mkdir()
    with lzma.open(sub / "DATOS20250101.zip.xz", "wb") as fh:
        fh.write(raw.read_bytes())
    (sub / "RES20250101.zip.xz").write_bytes(b"ignored")
    with locate_bundle(sub) as bundle:
        assert bundle.xml_path.is_file()
        assert bundle.source.name == "DATOS20250101.zip.xz"


def test_unsupported_archive_type(tmp_path: Path) -> None:
    bad = tmp_path / "bundle.tar"
    bad.write_bytes(b"x")
    with pytest.raises(ValueError, match="unsupported archive"):
        locate_bundle(bad)


def test_unknown_zip_name_is_extracted(tmp_path: Path) -> None:
    z = _zip(tmp_path / "whatever.zip", {DBSEN_FILENAME: _XML.encode()})
    with locate_bundle(z) as bundle:
        assert bundle.xml_path.is_file()


def test_zip_without_xml_rejected(tmp_path: Path) -> None:
    z = _zip(tmp_path / "DATOS20250101.zip", {"a.csv": b"x"})
    with pytest.raises(FileNotFoundError, match="extracted bundle missing"):
        locate_bundle(z)


def test_outer_wrapper(tmp_path: Path) -> None:
    inner = _zip(tmp_path / "DATOS20250101.zip", {DBSEN_FILENAME: _XML.encode()})
    outer = _zip(
        tmp_path / "PLEXOS20250101.zip", {"DATOS20250101.zip": inner.read_bytes()}
    )
    with locate_bundle(outer) as bundle:
        assert bundle.xml_path.is_file()


def test_outer_wrapper_without_datos(tmp_path: Path) -> None:
    outer = _zip(tmp_path / "PLEXOS20250101.zip", {"other.txt": b"x"})
    with pytest.raises(ValueError, match="outer wrapper missing"):
        locate_bundle(outer)


def test_close_releases_extra_cleanup(tmp_path: Path) -> None:
    import tempfile  # pylint: disable=import-outside-toplevel

    extra = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
    bundle = PlexosBundle(root=tmp_path, source=tmp_path, _extra_cleanup=[extra])
    bundle.close()
    assert not Path(extra.name).exists()
