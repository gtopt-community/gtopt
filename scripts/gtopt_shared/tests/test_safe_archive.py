# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for :mod:`gtopt_shared.safe_archive`."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from gtopt_shared import safe_archive as sa


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


# --------------------------------------------------------------- limits ---


def test_env_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sa.max_extract_bytes({}) == sa.DEFAULT_MAX_BYTES
    assert sa.max_archive_members({}) == sa.DEFAULT_MAX_MEMBERS
    assert sa.max_extract_bytes({sa.ENV_MAX_BYTES: " 10 "}) == 10
    monkeypatch.setenv(sa.ENV_MAX_MEMBERS, "3")
    assert sa.max_archive_members() == 3
    with pytest.raises(sa.ArchiveLimitError, match="not an integer"):
        sa.max_extract_bytes({sa.ENV_MAX_BYTES: "lots"})
    with pytest.raises(sa.ArchiveLimitError, match="positive"):
        sa.max_extract_bytes({sa.ENV_MAX_BYTES: "0"})


def test_check_zip_ok_and_limits(tmp_path: Path) -> None:
    z = _zip(tmp_path / "a.zip", {"a.txt": b"x" * 100, "b.txt": b"y" * 50})
    with zipfile.ZipFile(z) as zf:
        assert sa.check_zip(zf) == 150
        with pytest.raises(sa.ArchiveLimitError, match="members exceed"):
            sa.check_zip(zf, max_members=1)
        with pytest.raises(sa.ArchiveLimitError, match="expands to 150"):
            sa.check_zip(zf, max_bytes=149)


def test_extractall_zip_respects_env_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    z = _zip(tmp_path / "bomb.zip", {"big.bin": b"\0" * 10_000})
    monkeypatch.setenv(sa.ENV_MAX_BYTES, "1000")
    with zipfile.ZipFile(z) as zf, pytest.raises(sa.ArchiveLimitError):
        sa.extractall_zip(zf, tmp_path / "out")
    assert not (tmp_path / "out" / "big.bin").exists()


def test_extractall_zip_members_and_traversal(tmp_path: Path) -> None:
    z = _zip(
        tmp_path / "a.zip",
        {"keep/a.txt": b"a", "skip.txt": b"s", "../evil.txt": b"e"},
    )
    out = tmp_path / "out"
    with zipfile.ZipFile(z) as zf:
        sa.extractall_zip(zf, out, ["keep/a.txt"])
        assert (out / "keep" / "a.txt").read_bytes() == b"a"
        assert not (out / "skip.txt").exists()
        sa.extractall_zip(zf, out)
    # zipfile drops ``..`` components: nothing lands outside ``out``.
    assert not (tmp_path / "evil.txt").exists()
    assert (out / "evil.txt").exists()


def test_extract_zip_member(tmp_path: Path) -> None:
    z = _zip(tmp_path / "a.zip", {"d/a.txt": b"abc"})
    with zipfile.ZipFile(z) as zf:
        got = sa.extract_zip_member(zf, "d/a.txt", tmp_path / "o")
        assert Path(got).read_bytes() == b"abc"
        info = zf.getinfo("d/a.txt")
        assert Path(sa.extract_zip_member(zf, info, tmp_path / "p")).is_file()


# ----------------------------------------------------------- copy_limited ---


def test_copy_limited() -> None:
    dst = io.BytesIO()
    assert sa.copy_limited(io.BytesIO(b"x" * 10), dst, max_bytes=10) == 10
    assert dst.getvalue() == b"x" * 10
    with pytest.raises(sa.ArchiveLimitError, match="blob: decompressed"):
        sa.copy_limited(io.BytesIO(b"x" * 11), io.BytesIO(), max_bytes=10, what="blob")


def test_copy_file_limited_unlinks_on_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "f.bin"
    assert sa.copy_file_limited(io.BytesIO(b"ok"), out) == 2
    assert out.read_bytes() == b"ok"
    monkeypatch.setenv(sa.ENV_MAX_BYTES, "1")
    with pytest.raises(sa.ArchiveLimitError):
        sa.copy_file_limited(io.BytesIO(b"too big"), out, what="x")
    assert not out.exists()


# -------------------------------------------------------------------- tar ---


def _tar(path: Path, build) -> Path:  # type: ignore[no-untyped-def]
    with tarfile.open(path, "w") as tar:
        build(tar)
    return path


def _add(tar: tarfile.TarFile, name: str, data: bytes = b"d") -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def test_extractall_tar_regular_files(tmp_path: Path) -> None:
    def build(tar: tarfile.TarFile) -> None:
        d = tarfile.TarInfo("pkg")
        d.type = tarfile.DIRTYPE
        tar.addfile(d)
        _add(tar, "pkg/a.txt", b"hello")

    t = _tar(tmp_path / "a.tar", build)
    with tarfile.open(t) as tar:
        sa.extractall_tar(tar, tmp_path / "out")
    assert (tmp_path / "out" / "pkg" / "a.txt").read_bytes() == b"hello"


@pytest.mark.parametrize("bad", ["../evil", "/abs/evil", "link"])
def test_extractall_tar_refuses_unsafe_members(tmp_path: Path, bad: str) -> None:
    def build(tar: tarfile.TarFile) -> None:
        if bad == "link":
            info = tarfile.TarInfo("ln")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        else:
            _add(tar, bad)

    t = _tar(tmp_path / "bad.tar", build)
    out = tmp_path / "out"
    out.mkdir()
    with tarfile.open(t) as tar, pytest.raises(sa.ArchiveLimitError, match="unsafe"):
        sa.extractall_tar(tar, out)
    assert not list(out.iterdir())


def test_extractall_tar_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def build(tar: tarfile.TarFile) -> None:
        _add(tar, "a", b"x" * 100)
        _add(tar, "b", b"y")

    t = _tar(tmp_path / "a.tar", build)
    monkeypatch.setenv(sa.ENV_MAX_MEMBERS, "1")
    with tarfile.open(t) as tar, pytest.raises(sa.ArchiveLimitError, match="members"):
        sa.extractall_tar(tar, tmp_path / "o1")
    monkeypatch.delenv(sa.ENV_MAX_MEMBERS)
    monkeypatch.setenv(sa.ENV_MAX_BYTES, "50")
    with tarfile.open(t) as tar, pytest.raises(sa.ArchiveLimitError, match="expands"):
        sa.extractall_tar(tar, tmp_path / "o2")


def test_extractall_tar_without_filter_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = _tar(tmp_path / "a.tar", lambda tar: _add(tar, "f.txt", b"z"))
    monkeypatch.setattr(sa, "_TAR_HAS_FILTERS", False)
    with tarfile.open(t) as tar:
        sa.extractall_tar(tar, tmp_path / "out")
    assert (tmp_path / "out" / "f.txt").read_bytes() == b"z"


def test_extractall_tar_fileobj_without_name(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        _add(tar, "../x")
    buf.seek(0)
    with tarfile.open(fileobj=buf) as tar, pytest.raises(sa.ArchiveLimitError):
        sa.extractall_tar(tar, tmp_path)


# ------------------------------------------------------------------ names ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a.csv", "a.csv"),
        ("dir/sub/a.csv", "a.csv"),
        ("..\\..\\win\\a.csv", "a.csv"),
        ("/abs/path/x.zip", "x.zip"),
    ],
)
def test_safe_member_name(raw: str, expected: str) -> None:
    assert sa.safe_member_name(raw) == expected
    assert sa.safe_child("/base", raw) == Path("/base") / expected


@pytest.mark.parametrize("raw", ["", ".", "..", "a/..", "bad\nname", "x/\x00y"])
def test_safe_member_name_refuses(raw: str) -> None:
    with pytest.raises(sa.ArchiveLimitError, match="unsafe file name"):
        sa.safe_member_name(raw)
