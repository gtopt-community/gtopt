# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Offline tests for :mod:`cen2gtopt.pcp_archive` (HTTP mocked)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from cen2gtopt import pcp_archive as pa
from cen2gtopt.tests.cov_plexos_helpers import write_zip

ROWS = [
    {
        "id": 1,
        "nombre": "PLEXOS20260407.zip",
        "descripcion": "PCP bundle 07-04",
        "categoria": "PCP",
        "path": "pcp/2026",
        "fechaPublicacion": "2026-04-06T18:00",
        "fechaVigencia": "2026-04-07",
        "enlaceDescarga": "https://s3.example/PLEXOS20260407.zip?sig=1",
        "vencimiento": "2026-04-07T06:00",
        "estado": "OK",
    },
    {"id": 2, "nombre": "PLEXOS20260408.zip", "categoria": "PCP"},
    {"id": 3, "nombre": "PID_20260407_05.zip", "categoria": "PID"},
    {"id": 4, "nombre": "Manual.pdf", "categoria": "RE"},
    {"id": 5, "nombre": "IPLP_20260401.zip", "categoria": "PLP"},
]


class _Resp:
    def __init__(self, payload: Any = None, chunks: list[bytes] | None = None):
        self._payload = payload
        self._chunks = chunks or []
        self.raised = False

    def raise_for_status(self) -> None:
        self.raised = True

    def json(self) -> Any:
        return self._payload

    def iter_content(self, chunk_size: int) -> list[bytes]:
        assert chunk_size == 1 << 20
        return self._chunks

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *_: object) -> None:
        return None


@pytest.fixture(name="http")
def _http(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {"calls": [], "payload": ROWS, "chunks": [b"ab", b"", b"c"]}

    def _get(url: str, **kw: Any) -> _Resp:
        state["calls"].append((url, kw))
        if kw.get("stream"):
            return _Resp(chunks=state["chunks"])
        return _Resp(payload=state["payload"])

    monkeypatch.setattr(pa.requests, "get", _get)
    monkeypatch.setattr(pa, "DEFAULT_INDEX_PATH", tmp_path / "idx" / "pcp_index.json")
    monkeypatch.setenv("GTOPT_CEN_PORTAL_KEY", "K")
    return state


def _files() -> list[pa.PcpFile]:
    return [pa.PcpFile.from_dict(r) for r in ROWS]


# ---------------------------------------------------------------------------
# PcpFile / index
# ---------------------------------------------------------------------------


def test_pcpfile_from_dict_and_date() -> None:
    f = pa.PcpFile.from_dict(ROWS[0])
    assert f.id == 1 and f.fecha_vigencia == "2026-04-07"
    assert f.date_iso() == "2026-04-07"
    assert pa.PcpFile.from_dict({}).date_iso() is None


def test_fetch_index_needs_env_key_only_on_cache_miss(
    http: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GTOPT_CEN_PORTAL_KEY", raising=False)
    with pytest.raises(pa.MissingCredentialError, match="GTOPT_CEN_PORTAL_KEY"):
        pa.fetch_index()
    assert not http["calls"]
    assert pa.main(["refresh"]) == 2


def test_fetch_index_redacts_key_in_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _get(url: str, **kw: Any) -> Any:
        raise pa.requests.HTTPError(
            f"403 for url: {url}?user_key={kw['params']['user_key']}"
        )

    monkeypatch.setattr(pa.requests, "get", _get)
    monkeypatch.setattr(pa, "DEFAULT_INDEX_PATH", tmp_path / "pcp_index.json")
    with pytest.raises(pa.requests.RequestException) as info:
        pa.fetch_index(user_key="SECRETKEY")
    assert "SECRETKEY" not in str(info.value) and "user_key=***" in str(info.value)


def test_download_one_redacts_signature(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _get(url: str, **_: Any) -> Any:
        raise pa.requests.ConnectionError(f"failed {url}")

    monkeypatch.setattr(pa.requests, "get", _get)
    f = pa.PcpFile.from_dict(
        {**ROWS[0], "enlaceDescarga": "https://s3.example/f.zip?X-Amz-Signature=S1G"}
    )
    with pytest.raises(pa.requests.RequestException) as info:
        pa.download_one(f, output_dir=tmp_path)
    assert "S1G" not in str(info.value)


def test_fetch_index_live_then_cache(http: dict[str, Any], tmp_path: Path) -> None:
    files = pa.fetch_index(user_key="K")
    assert [f.nombre for f in files][:2] == ["PLEXOS20260407.zip", "PLEXOS20260408.zip"]
    url, kw = http["calls"][0]
    assert url == pa.LIST_URL and kw["params"] == {"user_key": "K"}
    cache = tmp_path / "idx" / "pcp_index.json"
    assert json.loads(cache.read_text())[0]["id"] == 1

    # Fresh cache → no new HTTP call.
    assert len(pa.fetch_index()) == len(ROWS)
    assert len(http["calls"]) == 1

    # force → refetch; stale cache → refetch.
    pa.fetch_index(force=True)
    assert len(http["calls"]) == 2
    old = time.time() - pa.INDEX_FRESH_S - 10
    os.utime(cache, (old, old))
    pa.fetch_index()
    assert len(http["calls"]) == 3


# ---------------------------------------------------------------------------
# Filtering + download
# ---------------------------------------------------------------------------


def test_filter_files() -> None:
    files = _files()
    assert len(pa.filter_files(files)) == len(files)
    assert [f.id for f in pa.filter_files(files, name_pattern="plexos")] == [1, 2]
    assert [f.id for f in pa.filter_files(files, category="pid")] == [3]
    assert [f.id for f in pa.filter_files(files, since="2026-04-08")] == [2]
    assert [f.id for f in pa.filter_files(files, until="2026-04-01")] == [5]
    got = pa.filter_files(files, since="2026-04-02", until="2026-04-07")
    assert [f.id for f in got] == [1, 3]


def test_download_one(http: dict[str, Any], tmp_path: Path) -> None:
    f = _files()[0]
    out = pa.download_one(f, output_dir=tmp_path)
    assert out == tmp_path / "PCP" / "PLEXOS20260407.zip"
    assert out.read_bytes() == b"abc"
    assert not out.with_suffix(".zip.part").exists()
    n = len(http["calls"])
    # Existing non-empty file is skipped …
    pa.download_one(f, output_dir=tmp_path)
    assert len(http["calls"]) == n
    # … unless skip_existing=False.
    http["chunks"] = [b"new"]
    pa.download_one(f, output_dir=tmp_path, skip_existing=False)
    assert out.read_bytes() == b"new"


def test_is_plp_input_and_extract_from_directory(tmp_path: Path) -> None:
    assert pa._is_plp_input("plpblo.dat")
    assert pa._is_plp_input("plpaflce.dat.2.xz")
    assert not pa._is_plp_input("readme.txt")
    src = tmp_path / "bundle" / "Datos"
    src.mkdir(parents=True)
    (src / "plpblo.dat").write_text("b")
    (src / "notes.txt").write_text("n")
    (src / "sub").mkdir()
    got = pa.extract_plp_inputs(tmp_path / "bundle", tmp_path / "dest")
    assert [p.name for p in got] == ["plpblo.dat"]


def test_extract_member_missing(tmp_path: Path) -> None:
    z = write_zip(tmp_path / "w.zip", {"a/b.txt": b"x"})
    assert pa._extract_member(z, "c.txt", tmp_path) is None
    assert pa._extract_member(z, "B.TXT", tmp_path) == tmp_path / "b.txt"


def test_fetch_pcp_day_uses_index_and_detects_missing_datos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = pa.PcpFile.from_dict(
        {"nombre": "PLEXOS20260407.zip", "categoria": "PCP", "fechaPublicacion": "1"}
    )
    new = pa.PcpFile.from_dict(
        {"nombre": "PLEXOS20260407.zip", "categoria": "PCP", "fechaPublicacion": "2"}
    )
    monkeypatch.setattr(pa, "fetch_index", lambda **_k: [new, old])
    picked: list[str] = []

    def _dl(f: pa.PcpFile, *, output_dir: Path, **_k: Any) -> Path:
        picked.append(f.fecha_publicacion)
        return write_zip(output_dir / f.nombre, {"RES20260407.zip": b"r"})

    monkeypatch.setattr(pa, "download_one", _dl)
    with pytest.raises(FileNotFoundError, match="DATOS20260407.zip not inside"):
        pa.fetch_pcp_day("2026-04-07", tmp_path / "d", download_root=tmp_path)
    assert picked == ["2"]  # latest publication wins


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture(name="index")
def _index(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    forces: list[bool] = []

    def _fetch(*, force: bool = False, **_k: Any) -> list[pa.PcpFile]:
        forces.append(force)
        return _files()

    monkeypatch.setattr(pa, "fetch_index", _fetch)
    return forces


def test_cli_list(index: list[bool], capsys: pytest.CaptureFixture[str]) -> None:
    assert pa.main(["-q", "list", "--pattern", "PLEXOS", "--refresh"]) == 0
    out = capsys.readouterr().out
    assert out.index("PLEXOS20260408") < out.index("PLEXOS20260407")
    assert "2 files matched" in out
    assert index == [True]

    assert pa.main(["-v", "list", "--limit", "2"]) == 0
    out = capsys.readouterr().out
    assert "… and 3 more" in out and "5 files matched" in out
    assert pa.main(["list", "--limit", "0"]) == 0
    out = capsys.readouterr().out
    assert "more" not in out and "Manual.pdf" in out and "?" in out


def test_cli_info(index: list[bool], capsys: pytest.CaptureFixture[str]) -> None:
    assert pa.main(["info", "PLEXOS20260407.zip"]) == 0
    out = capsys.readouterr().out
    assert "=== PLEXOS20260407.zip ===" in out and "state:     OK" in out
    assert pa.main(["info", "plexos2026"]) == 0
    assert capsys.readouterr().out.count("===") == 4
    assert pa.main(["info", "nothing"]) == 1
    assert "no file matched" in capsys.readouterr().out
    assert index


def test_cli_download(
    index: list[bool],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    got: list[tuple[str, bool]] = []

    def _dl(f: pa.PcpFile, *, output_dir: Path, skip_existing: bool) -> Path:
        if f.nombre == "PLEXOS20260408.zip":
            raise RuntimeError("expired URL")
        got.append((f.nombre, skip_existing))
        return output_dir / f.nombre

    monkeypatch.setattr(pa, "download_one", _dl)
    out_dir = str(tmp_path)
    assert (
        pa.main(["download", "--name", "PLEXOS20260407.zip", "--output", out_dir]) == 0
    )
    assert got == [("PLEXOS20260407.zip", True)]
    assert "1 ok, 0 failed" in capsys.readouterr().out

    assert pa.main(["download", "--name", "nope.zip", "--output", out_dir]) == 1
    assert "no file matched" in capsys.readouterr().out

    rc = pa.main(
        ["download", "--pattern", "PLEXOS", "--force", "--output", out_dir, "--refresh"]
    )
    assert rc == 1
    assert "1 ok, 1 failed" in capsys.readouterr().out
    assert got[-1] == ("PLEXOS20260407.zip", False)

    assert pa.main(["download", "--category", "IM", "--output", out_dir]) == 1
    assert "no files matched the filter" in capsys.readouterr().out


def test_cli_refresh(index: list[bool], capsys: pytest.CaptureFixture[str]) -> None:
    assert pa.main(["refresh"]) == 0
    assert "index refreshed — 5 files" in capsys.readouterr().out
    assert index == [True]


def test_cli_pcp_day(
    index: list[bool],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    results = iter(
        [
            (tmp_path / "DATOS.zip", tmp_path / "RES.zip"),
            (tmp_path / "DATOS.zip", None),
        ]
    )
    monkeypatch.setattr(pa, "fetch_pcp_day", lambda *_a, **_k: next(results))
    args = ["pcp-day", "2026-04-07", "--dest", str(tmp_path), "--output", str(tmp_path)]
    assert pa.main(args) == 0
    assert capsys.readouterr().out.split() == [
        str(tmp_path / "DATOS.zip"),
        str(tmp_path / "RES.zip"),
    ]
    assert pa.main(args) == 0
    assert capsys.readouterr().out.split() == [str(tmp_path / "DATOS.zip")]
    assert index == [False, False]


def test_cli_plp_in_force_download_and_extract(
    index: list[bool],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = write_zip(
        tmp_path / "PLP" / "IPLP_20260401.zip",
        {"Datos/plpblo.dat": b"b", "Datos/plpcnfce.dat": b"c"},
    )
    monkeypatch.setattr(pa, "download_one", lambda *_a, **_k: bundle)
    base = ["plp-in-force", "2026-05-15", "--output", str(tmp_path)]
    assert pa.main(base) == 0
    cap = capsys.readouterr()
    assert cap.out.strip() == str(bundle)
    assert "selected IPLP_20260401.zip" in cap.err

    dest = tmp_path / "plp"
    assert pa.main([*base, "--extract", str(dest)]) == 0
    assert "extracted 2 PLP input files" in capsys.readouterr().err
    assert sorted(p.name for p in dest.iterdir()) == ["plpblo.dat", "plpcnfce.dat"]
