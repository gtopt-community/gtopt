# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Offline tests for ``gtopt-fetch`` (index and HTTP mocked)."""

from __future__ import annotations

import json
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from cen2gtopt import pcp_archive
from cen2gtopt.pcp_archive import PcpFile
from gtopt_operators.fetch import backends, cache, cli
from gtopt_operators.fetch.backends import (
    FetchError,
    FetchRequest,
    ManualActionRequired,
    fetch,
    http_download,
    select_portal_files,
)
from gtopt_operators.registry import DatasetSpec, load_registry, parse_sources


def _row(nombre: str, categoria: str, pub: str = "2026-04-06T12:00") -> PcpFile:
    return PcpFile.from_dict(
        {
            "id": 1,
            "nombre": nombre,
            "categoria": categoria,
            "fechaPublicacion": pub,
            "enlaceDescarga": f"https://s3.example/{nombre}?X-Amz-Signature=SIG",
        }
    )


INDEX = [
    _row("PLEXOS20260407.zip", "PCP"),
    _row("DATOS20260408.zip", "PCP"),
    _row("RES20260408.zip", "PCP_RES"),
    _row("DATOS20260407_05.zip", "PID"),
    _row("DATOS20260407_06.zip", "PID"),
    _row("IPLP_20260301.zip", "PLP"),
    _row("IPLP_20260401.zip", "PLP", "2026-04-01T00:00"),
    _row("IPLP_20260401.zip", "PLP", "2026-04-02T00:00"),
    _row("IPLP20260415.zip", "PMP"),
    _row("Manual.pdf", "RE"),
]


def _wrapper(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("PLEXOS20260407/DATOS20260407.zip", b"datos")
        zf.writestr("PLEXOS20260407/RES20260407.zip", b"res")
        zf.writestr("PLEXOS20260407/readme.txt", b"x")
        zf.writestr("PLEXOS20260407/", b"")
    return path


@pytest.fixture(name="env")
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Isolated caches + a fake HTTP layer that serves bytes per URL."""
    state: dict[str, Any] = {"index_calls": 0, "downloads": []}
    monkeypatch.setenv(cache.CACHE_ENV, str(tmp_path / "fetch"))
    monkeypatch.setattr(pcp_archive, "DEFAULT_DOWNLOAD_ROOT", tmp_path / "legacy")

    def _index(force: bool = False) -> list[PcpFile]:
        state["index_calls"] += 1
        state["force"] = force
        return list(INDEX)

    def _download(url: str, dest: Path, *, timeout: int = 600) -> Path:
        del timeout
        state["downloads"].append(url)
        name = dest.name
        if name.startswith("PLEXOS"):
            _wrapper(dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(name.encode())
        return dest

    monkeypatch.setattr(pcp_archive, "fetch_index", _index)
    monkeypatch.setattr(backends, "http_download", _download)
    state["root"] = tmp_path / "fetch"
    return state


def _req(dataset: str, day: date | None, op: str = "cen", **kw: Any) -> FetchRequest:
    reg = load_registry(op)
    return FetchRequest(registry=reg, dataset=reg.dataset(dataset), day=day, **kw)


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def test_select_exact_first_pattern_wins() -> None:
    ds = load_registry("cen").dataset("pcp")
    assert [f.nombre for f in select_portal_files(INDEX, ds, date(2026, 4, 7))] == [
        "PLEXOS20260407.zip"
    ]
    assert [f.nombre for f in select_portal_files(INDEX, ds, date(2026, 4, 8))] == [
        "DATOS20260408.zip"
    ]
    assert not select_portal_files(INDEX, ds, date(2026, 4, 9))


def test_select_multi() -> None:
    ds = load_registry("cen").dataset("pid_inputs")
    got = select_portal_files(INDEX, ds, date(2026, 4, 7))
    assert [f.nombre for f in got] == ["DATOS20260407_05.zip", "DATOS20260407_06.zip"]


def test_select_latest_rules() -> None:
    ds = load_registry("cen").dataset("plp_inputs")
    pick = select_portal_files(INDEX, ds, date(2026, 4, 10))
    assert [(f.nombre, f.fecha_publicacion) for f in pick] == [
        ("IPLP_20260401.zip", "2026-04-02T00:00")
    ]
    assert select_portal_files(INDEX, ds, date(2026, 4, 1))[0].nombre == (
        "IPLP_20260401.zip"
    )
    assert select_portal_files(INDEX, ds, date(2026, 4, 20))[0].nombre == (
        "IPLP20260415.zip"
    )
    assert not select_portal_files(INDEX, ds, date(2026, 1, 1))
    strict = DatasetSpec(
        name="x",
        backend="cen_portal",
        patterns=(r"IPLP_?{D}.*\.zip",),
        date_rule="latest_before",
    )
    assert select_portal_files(INDEX, strict, date(2026, 4, 1))[0].nombre == (
        "IPLP_20260301.zip"
    )


# ---------------------------------------------------------------------------
# cen_portal backend
# ---------------------------------------------------------------------------


def test_pcp_wrapper_is_unwrapped_and_manifested(env: dict[str, Any]) -> None:
    res = fetch(_req("pcp", date(2026, 4, 7)))
    assert [p.name for p in res.files] == ["DATOS20260407.zip", "RES20260407.zip"]
    assert res.files[0].read_bytes() == b"datos"
    assert len(env["downloads"]) == 1
    manifest = cache.read_manifest(env["root"] / "cen" / "pcp")
    assert set(manifest) == {
        "PLEXOS20260407.zip",
        "DATOS20260407.zip",
        "RES20260407.zip",
    }
    wrapper = manifest["PLEXOS20260407.zip"]
    assert wrapper.url == "https://s3.example/PLEXOS20260407.zip"  # no signature
    assert wrapper.published_at == "2026-04-06T12:00"
    assert manifest["DATOS20260407.zip"].source == "unwrap"
    assert manifest["DATOS20260407.zip"].url.endswith(
        "#PLEXOS20260407/DATOS20260407.zip"
    )
    assert "SIG" not in (env["root"] / "cen" / "pcp" / "MANIFEST.jsonl").read_text()
    # Second call: cache hit, no new download.
    fetch(_req("pcp", date(2026, 4, 7)))
    assert len(env["downloads"]) == 1


def test_pcp_separate_files_with_companion(env: dict[str, Any]) -> None:
    res = fetch(_req("pcp", date(2026, 4, 8)))
    assert [p.name for p in res.files] == ["DATOS20260408.zip", "RES20260408.zip"]
    assert len(env["downloads"]) == 2


def test_legacy_cache_is_reused(env: dict[str, Any], tmp_path: Path) -> None:
    legacy = tmp_path / "legacy" / "PLP" / "IPLP_20260401.zip"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"old")
    res = fetch(_req("plp_inputs", date(2026, 4, 10)))
    assert res.files[0].read_bytes() == b"old"
    assert not env["downloads"]
    entry = cache.read_manifest(env["root"] / "cen" / "plp_inputs")["IPLP_20260401.zip"]
    assert entry.source == "legacy_cache" and entry.date == "2026-04-10"
    assert legacy.exists()  # never removed


def test_portal_dry_run_and_errors(env: dict[str, Any]) -> None:
    res = fetch(_req("pcp", date(2026, 4, 8), dry_run=True, refresh=True))
    assert res.planned == ["DATOS20260408.zip", "RES20260408.zip"]
    assert env["force"] is True and not env["downloads"]
    with pytest.raises(FetchError, match="needs --date"):
        fetch(_req("pcp", None))
    with pytest.raises(FetchError, match="no cen/pcp file for 2026-05-01"):
        fetch(_req("pcp", date(2026, 5, 1)))


# ---------------------------------------------------------------------------
# static_url / manual / unsupported
# ---------------------------------------------------------------------------


def _registry(**dataset: Any) -> Any:
    return parse_sources(
        {
            "schema_version": 1,
            "operator": {"id": "zz", "name": "Z", "country": "zz", "timezone": "UTC"},
            "dataset": {"d": dataset},
        }
    )


def test_static_url(env: dict[str, Any]) -> None:
    reg = _registry(backend="static_url", url="https://h.example/f_{date:%Y%m%d}.csv")
    req = FetchRequest(registry=reg, dataset=reg.dataset("d"), day=date(2026, 1, 2))
    dry = FetchRequest(
        registry=reg, dataset=reg.dataset("d"), day=date(2026, 1, 2), dry_run=True
    )
    assert fetch(dry).planned == ["f_20260102.csv"]
    res = fetch(req)
    assert res.files[0].name == "f_20260102.csv"
    assert env["downloads"] == ["https://h.example/f_20260102.csv"]
    fetch(req)  # cached
    assert len(env["downloads"]) == 1


def test_static_url_pinned_sha_and_bad_name(env: dict[str, Any]) -> None:
    reg = _registry(
        backend="static_url", url="https://h.example/a.bin", sha256="0" * 64
    )
    with pytest.raises(FetchError, match="sha256 does not match"):
        fetch(FetchRequest(registry=reg, dataset=reg.dataset("d")))
    reg = _registry(backend="static_url", url="https://h.example/")
    with pytest.raises(FetchError, match="cannot derive a file name"):
        fetch(FetchRequest(registry=reg, dataset=reg.dataset("d")))
    assert env["downloads"] == ["https://h.example/a.bin"]


def test_manual_registers_user_file(env: dict[str, Any], tmp_path: Path) -> None:
    req = _req("iasr_workbook", None, op="aemo", version="2026")
    with pytest.raises(ManualActionRequired) as info:
        fetch(req)
    text = str(info.value)
    assert "NOT redistributable" in text and "iasr_workbook_2026.xlsx" in text
    assert "--version 2026 --file" in text
    src = tmp_path / "download.xlsx"
    src.write_bytes(b"workbook")
    with pytest.raises(FetchError, match="not a file"):
        fetch(_req("iasr_workbook", None, op="aemo", manual_file=tmp_path / "nope"))
    dry = fetch(
        _req(
            "iasr_workbook",
            None,
            op="aemo",
            version="2026",
            manual_file=src,
            dry_run=True,
        )
    )
    assert dry.planned == ["iasr_workbook_2026.xlsx"]
    res = fetch(_req("iasr_workbook", None, op="aemo", version="2026", manual_file=src))
    assert res.files[0].name == "iasr_workbook_2026.xlsx"
    entry = cache.read_manifest(res.files[0].parent)["iasr_workbook_2026.xlsx"]
    assert entry.source == "manual" and entry.url.startswith("https://aemo.com.au")
    assert not env["downloads"]


def test_html_index_without_filename(env: dict[str, Any], tmp_path: Path) -> None:
    src = tmp_path / "bundle.zip"
    src.write_bytes(b"z")
    res = fetch(_req("planning", None, op="amm", manual_file=src))
    assert res.files[0].name == "bundle.zip"
    reg = _registry(backend="manual", url="https://h.example/x_{version}.zip")
    req = FetchRequest(registry=reg, dataset=reg.dataset("d"), version="7")
    with pytest.raises(ManualActionRequired, match="https://h.example/x_7.zip"):
        fetch(req)
    assert not env["downloads"]


def test_unsupported_backend() -> None:
    reg = _registry(backend="rest_api")
    with pytest.raises(FetchError, match="has no downloader"):
        fetch(FetchRequest(registry=reg, dataset=reg.dataset("d")))


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, chunks: list[bytes], fail: Exception | None = None) -> None:
        self._chunks = chunks
        self._fail = fail

    def raise_for_status(self) -> None:
        if self._fail:
            raise self._fail

    def iter_content(self, chunk_size: int) -> list[bytes]:
        del chunk_size
        return self._chunks

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_http_download_ok_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import requests

    monkeypatch.setattr(requests, "get", lambda url, **_: _Resp([b"ab", b"", b"c"]))
    out = http_download("https://h/x?user_key=K", tmp_path / "d" / "x")
    assert out.read_bytes() == b"abc"
    err = requests.HTTPError("403 for url https://h/x?user_key=SECRET")
    monkeypatch.setattr(requests, "get", lambda url, **_: _Resp([], err))
    with pytest.raises(FetchError) as info:
        http_download("https://h/x?user_key=SECRET", tmp_path / "y")
    assert "SECRET" not in str(info.value)
    assert not (tmp_path / "y.part").exists()


# ---------------------------------------------------------------------------
# cache / verify
# ---------------------------------------------------------------------------


def test_manifest_lock_and_verify(tmp_path: Path) -> None:
    d = tmp_path / "case"
    d.mkdir()
    a = d / "a.bin"
    a.write_bytes(b"aaa")
    b = d / "b.bin"
    b.write_bytes(b"bbb")
    cache.write_manifest(
        d, [cache.ManifestEntry.for_file(a), cache.ManifestEntry.for_file(b)]
    )
    (d / cache.MANIFEST_NAME).write_text(
        (d / cache.MANIFEST_NAME).read_text() + "\n", encoding="utf-8"
    )
    assert cache.verify_dir(d) == (2, [])
    lock = cache.write_lock(d)
    assert lock.read_text().splitlines()[0].endswith("  a.bin")
    lock.write_text("# pinned\n" + lock.read_text(), encoding="utf-8")
    assert cache.read_lock(lock)["b.bin"] == cache.sha256_file(b)
    b.write_bytes(b"BBB")
    n, issues = cache.verify_dir(d)
    assert n == 2 and [(i.file, i.problem) for i in issues] == [
        ("b.bin", "sha256 mismatch")
    ]
    b.write_bytes(b"bb")
    assert cache.verify_dir(d)[1][0].problem == "size mismatch"
    b.unlink()
    assert cache.verify_dir(d)[1][0].problem == "missing"
    other = tmp_path / "other.lock"
    other.write_text(f"{'0' * 64}  a.bin\n{'1' * 64} *c.bin\n", encoding="utf-8")
    problems = {i.problem for i in cache.verify_dir(d, other)[1]}
    assert "manifest and lock disagree on sha256" in problems
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cache.verify_dir(empty)[0] == 0


def test_cache_root_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(cache.CACHE_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cache.cache_root() == tmp_path / ".cache" / "gtopt" / "fetch"


def test_place_link_copy_and_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "s"
    src.write_bytes(b"1")
    dest = cache.place(src, tmp_path / "o" / "s")
    assert dest.samefile(src)
    assert cache.place(src, dest) == dest
    other = tmp_path / "o" / "t"
    other.write_bytes(b"old")

    def _nolink(*_: Any) -> None:
        raise OSError("cross-device")

    monkeypatch.setattr(cache.os, "link", _nolink)
    assert cache.place(src, other).read_bytes() == b"1"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_get_out_and_verify(
    env: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "case" / "source"
    assert (
        cli.main(["-q", "cen", "get", "pcp", "--date", "2026-04-07", "--out", str(out)])
        == 0
    )
    printed = capsys.readouterr().out.split()
    assert printed == [str(out / "DATOS20260407.zip"), str(out / "RES20260407.zip")]
    rows = [json.loads(r) for r in (out / "MANIFEST.jsonl").read_text().splitlines()]
    assert {r["file"] for r in rows} == {"DATOS20260407.zip", "RES20260407.zip"}
    assert cli.main(["verify", str(out)]) == 0
    assert "2/2 files ok" in capsys.readouterr().out
    assert cli.main(["verify", str(out), "--write-lock"]) == 0
    (out / "RES20260407.zip").write_bytes(b"tampered")
    assert cli.main(["verify", str(out)]) == 1
    assert "FAIL  RES20260407.zip" in capsys.readouterr().out
    assert (
        cli.main(["-v", "cen", "get", "pcp", "--date", "2026-04-08", "--dry-run"]) == 0
    )
    assert capsys.readouterr().out.split() == ["DATOS20260408.zip", "RES20260408.zip"]


def test_cli_listing_and_info(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    del env
    assert cli.main(["operators"]) == 0
    assert "cen" in capsys.readouterr().out
    assert cli.main(["cen", "list"]) == 0
    assert "plp_inputs" in capsys.readouterr().out
    assert cli.main(["xm", "list"]) == 0
    assert "no datasets registered" in capsys.readouterr().out
    assert cli.main(["cen", "list", "plp_inputs", "--since", "2026-04-01"]) == 0
    out = capsys.readouterr().out
    assert "IPLP_20260401.zip" in out and "IPLP_20260301.zip" not in out
    assert "3 files matched" in out  # both publications of 0401 listed
    assert cli.main(["aemo", "list", "iasr_workbook", "--version", "2026"]) == 0
    assert "iasr_workbook_2026.xlsx" in capsys.readouterr().out
    assert cli.main(["cen", "info", "pcp"]) == 0
    out = capsys.readouterr().out
    assert "$GTOPT_CEN_PORTAL_KEY" in out and "geo-restricted" in out
    assert cli.main(["aemo", "info", "iasr_workbook"]) == 0
    assert "redistributable: NO" in capsys.readouterr().out


def test_cli_static_list(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = _registry(backend="static_url", url="https://h.example/f.csv")
    monkeypatch.setattr(cli, "load_registry", lambda _op: reg)
    assert cli.main(["cen", "list", "d"]) == 0
    assert "https://h.example/f.csv" in capsys.readouterr().out


def test_cli_exit_codes(
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del env
    assert cli.main(["aemo", "get", "isp_model", "--version", "2026"]) == 3
    assert "manual download required" in capsys.readouterr().err
    assert cli.main(["cen", "get", "pcp", "--date", "2030-01-01"]) == 1
    assert cli.main(["cen", "get", "nope"]) == 1
    assert "no dataset 'nope'" in capsys.readouterr().err

    def _no_key(force: bool = False) -> list[PcpFile]:
        del force
        from gtopt_operators.auth import MissingCredentialError

        raise MissingCredentialError("credential 'portal' not set: export $X")

    monkeypatch.setattr(pcp_archive, "fetch_index", _no_key)
    assert cli.main(["cen", "get", "pcp", "--date", "2026-04-07"]) == 2
    assert "$X" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["cen", "get", "pcp", "--date", "not-a-date"])


def test_cli_skips_broken_third_party_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from gtopt_operators.registry import RegistryError

    real = cli.load_registry

    def _load(op: str) -> Any:
        if op == "xm":
            raise RegistryError("broken")
        return real(op)

    monkeypatch.setattr(cli, "load_registry", _load)
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["xm", "list"])
