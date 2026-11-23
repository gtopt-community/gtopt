# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for the plp_compress_case split-retry loop (identity codec)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from plp_compress_case import main as pcc


def _copy_codec(src: Path, dst: Path, _codec: str, _args: list[str]) -> None:
    """Identity 'codec': compressed size == raw size."""
    shutil.copyfile(src, dst)


def test_split_retries_with_more_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    monkeypatch.setattr(pcc, "_compress_file", _copy_codec)
    src = tmp_path / "f.dat"
    data = b"\n".join(b"%09d" % i for i in range(100))  # 100 lines * 10 bytes
    src.write_bytes(data)
    out = tmp_path / "out"
    out.mkdir()
    caplog.set_level("INFO", logger=pcc.log.name)
    # Initial estimate (2 parts, ~500 B each) exceeds 300 B -> one retry.
    pcc._split_and_compress(src, out, "xz", [], 300, 300)
    parts = sorted(out.iterdir(), key=lambda p: int(p.name.split(".")[2]))
    assert len(parts) > 2
    assert all(p.stat().st_size <= 300 for p in parts)
    assert b"".join(p.read_bytes() for p in parts) == data
    assert "retrying with" in caplog.text
    # no raw (uncompressed) chunks left behind
    assert all(p.name.endswith(".xz") for p in parts)


def test_split_gives_up_after_max_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """Regression: the last attempt's parts used to be deleted before the
    final size report, crashing with FileNotFoundError."""
    monkeypatch.setattr(pcc, "_compress_file", _copy_codec)
    src = tmp_path / "f.dat"
    # Single lines of 100 bytes can never fit under a 50-byte limit.
    data = b"\n".join(b"x" * 100 for _ in range(4))
    src.write_bytes(data)
    out = tmp_path / "out"
    out.mkdir()
    pcc._split_and_compress(src, out, "xz", [], 50, 400)
    assert "could not fit all parts" in caplog.text
    parts = sorted(out.iterdir(), key=lambda p: int(p.name.split(".")[2]))
    assert b"".join(p.read_bytes() for p in parts) == data


@pytest.mark.parametrize("n_parts", [2, 3, 4, 5, 7])
@pytest.mark.parametrize("data", [b"a\nb\nc\nd", b"a\nb\nc\nd\n", b"one"])
def test_split_chunks_concatenate_to_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, n_parts: int, data: bytes
) -> None:
    """Regression: when fewer chunks than *n_parts* were produced, the last
    chunk got a spurious trailing newline appended."""
    monkeypatch.setattr(pcc, "_compress_file", _copy_codec)
    paths = pcc._do_split_compress(
        "f.dat", data, data.split(b"\n"), n_parts, ".xz", tmp_path, "xz", []
    )
    assert b"".join(p.read_bytes() for p in paths) == data
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(p.name for p in paths)
