# SPDX-License-Identifier: BSD-3-Clause
"""Tests for the PLP-in-force selection and PCP-day layout (no network)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from cen2gtopt import pcp_archive as pa


def _f(nombre: str, categoria: str, published: str) -> pa.PcpFile:
    return pa.PcpFile.from_dict(
        {"nombre": nombre, "categoria": categoria, "fechaPublicacion": published}
    )


LISTING = [
    _f("IPLP20260401.zip", "PLP", "2026-03-06T20:21:32.04"),
    _f("IPLP_20260401.zip", "PLP", "2026-04-06T14:58:09.357"),
    _f("IPLP_20260401.zip", "PLP", "2026-04-01T15:00:29.247"),
    _f("OPLP_20260401.zip", "PLP", "2026-04-06T14:58:09.357"),
    _f("IPLP20261001.zip", "PLP", "2026-09-07T22:02:30.987"),
    _f("PLEXOS20260407.zip", "PCP", "2026-04-06T18:06:06.53"),
]


def test_select_latest_on_or_before() -> None:
    pick = pa.select_plp_bundle(LISTING, "2026-06-15")
    assert pick is not None
    assert pick.nombre == "IPLP_20260401.zip"
    assert pick.fecha_publicacion == "2026-04-06T14:58:09.357"


def test_select_exact_date_and_before_any() -> None:
    pick = pa.select_plp_bundle(LISTING, "2026-10-01")
    assert pick is not None and pick.nombre == "IPLP20261001.zip"
    assert pa.select_plp_bundle(LISTING, "2026-03-31") is None


def test_select_ignores_results_and_other_categories() -> None:
    names = set()
    for d in ("2026-04-01", "2026-04-08", "2026-09-30"):
        pick = pa.select_plp_bundle(LISTING, d)
        assert pick is not None
        names.add(pick.nombre)
    assert names == {"IPLP_20260401.zip"}


def test_select_forced_name() -> None:
    pick = pa.select_plp_bundle(LISTING, "2026-01-01", name="IPLP20261001.zip")
    assert pick is not None and pick.nombre == "IPLP20261001.zip"
    assert pa.select_plp_bundle(LISTING, "2026-01-01", name="nope.zip") is None


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_extract_plp_inputs_nested_zip(tmp_path: Path) -> None:
    inner = _zip_bytes(
        {
            "Datos/plpblo.dat": b"blo",
            "Datos/plpcnfce.dat": b"ce",
            "Datos/indhor.csv": b"h",
            "Datos/plpaflce.dat.1.xz": b"x",
            "Datos/readme.txt": b"no",
            "Datos/sub/plpblo.dat": b"deeper copy ignored",
        }
    )
    bundle = tmp_path / "IPLP_20260401.zip"
    bundle.write_bytes(_zip_bytes({"IPLP/inner.zip": inner}))
    got = pa.extract_plp_inputs(bundle, tmp_path / "src")
    assert sorted(p.name for p in got) == [
        "indhor.csv",
        "plpaflce.dat.1.xz",
        "plpblo.dat",
        "plpcnfce.dat",
    ]
    assert (tmp_path / "src" / "plpblo.dat").read_bytes() == b"blo"


def test_extract_plp_inputs_without_marker(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x.dat").write_text("x")
    with pytest.raises(FileNotFoundError):
        pa.extract_plp_inputs(tmp_path / "d", tmp_path / "out")


def _fake_download(store: dict[str, bytes]):
    def download_one(f, *, output_dir, **_kw):
        out = Path(output_dir) / f.categoria / f.nombre
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(store[f.nombre])
        return out

    return download_one


def test_fetch_pcp_day_unwraps_plexos_wrapper(tmp_path, monkeypatch) -> None:
    store = {
        "PLEXOS20251019.zip": _zip_bytes(
            {
                "PLEXOS20251019/DATOS20251019.zip": b"datos",
                "PLEXOS20251019/RES20251019.zip": b"res",
            }
        )
    }
    monkeypatch.setattr(pa, "download_one", _fake_download(store))
    files = [_f("PLEXOS20251019.zip", "PCP", "2025-10-18")]
    datos, res = pa.fetch_pcp_day(
        "2025-10-19", tmp_path / "day", files=files, download_root=tmp_path / "dl"
    )
    assert datos.read_bytes() == b"datos"
    assert res is not None and res.read_bytes() == b"res"


def test_fetch_pcp_day_separate_files(tmp_path, monkeypatch) -> None:
    store = {"DATOS20261103.zip": b"d", "RES20261103.zip": b"r"}
    monkeypatch.setattr(pa, "download_one", _fake_download(store))
    files = [
        _f("DATOS20261103.zip", "PCP", "2026-11-02"),
        _f("RES20261103.zip", "PCP_RES", "2026-11-02"),
    ]
    datos, res = pa.fetch_pcp_day(
        "2026-11-03", tmp_path / "day", files=files, download_root=tmp_path / "dl"
    )
    assert datos.name == "DATOS20261103.zip" and datos.read_bytes() == b"d"
    assert res is not None and res.read_bytes() == b"r"
    files = files[:1]
    _, res = pa.fetch_pcp_day(
        "2026-11-03", tmp_path / "day2", files=files, download_root=tmp_path / "dl"
    )
    assert res is None
    with pytest.raises(FileNotFoundError):
        pa.fetch_pcp_day("2026-11-04", tmp_path / "x", files=files)


def test_cli_plp_in_force_dry_run(monkeypatch, capsys) -> None:
    monkeypatch.setattr(pa, "fetch_index", lambda **_kw: LISTING)
    assert pa.main(["plp-in-force", "2026-06-15", "--dry-run"]) == 0
    assert capsys.readouterr().out.strip() == "IPLP_20260401.zip"
    assert pa.main(["plp-in-force", "2020-01-01", "--dry-run"]) == 1
