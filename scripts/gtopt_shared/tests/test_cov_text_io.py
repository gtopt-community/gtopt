# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Coverage for compressed_open, base_parser and json_planning."""

from __future__ import annotations

import builtins
import bz2
import gzip
import json
import lzma
import math
from pathlib import Path

import pytest

from gtopt_shared import compressed_open as co
from gtopt_shared.base_parser import BaseTextParser
from gtopt_shared.json_planning import build_json_item, write_planning

_TEXT = "line one\nline two\n"


def _write_compressed(path: Path, data: bytes) -> Path:
    suffix = path.suffix
    if suffix == ".gz":
        path.write_bytes(gzip.compress(data))
    elif suffix == ".bz2":
        path.write_bytes(bz2.compress(data))
    elif suffix == ".xz":
        path.write_bytes(lzma.compress(data))
    elif suffix == ".zst":
        import zstandard

        path.write_bytes(zstandard.ZstdCompressor().compress(data))
    elif suffix == ".lz4":
        import lz4.frame

        path.write_bytes(lz4.frame.compress(data))
    else:
        path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# compressed_open
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ext", [".gz", ".bz2", ".xz", ".zst", ".lz4"])
def test_compressed_variants_roundtrip(tmp_path, ext):
    plain = tmp_path / "foo.dat"
    _write_compressed(tmp_path / f"foo.dat{ext}", _TEXT.encode())
    resolved = co.resolve_compressed_path(plain)
    assert resolved.name == f"foo.dat{ext}"
    assert co.find_compressed_path(plain) == resolved
    with co.compressed_open(resolved) as fh:
        assert fh.read() == _TEXT
    assert co.read_text(plain) == _TEXT
    with co.open_binary(plain) as fh:
        assert fh.read() == _TEXT.encode()


def test_plain_file_paths(tmp_path):
    plain = tmp_path / "bar.txt"
    plain.write_text(_TEXT, encoding="utf-8")
    assert co.resolve_compressed_path(plain) == plain
    assert co.find_compressed_path(plain) == plain
    with co.open_binary(plain) as fh:
        assert fh.read() == _TEXT.encode()
    assert co.read_text(plain) == _TEXT


def test_missing_file(tmp_path):
    missing = tmp_path / "nope.dat"
    assert co.find_compressed_path(missing) is None
    with pytest.raises(FileNotFoundError, match="also tried"):
        co.resolve_compressed_path(missing)


def test_plain_split_parts(tmp_path):
    for i, chunk in enumerate(["a\n", "b\n", "c\n"], start=1):
        (tmp_path / f"s.dat.{i}").write_text(chunk, encoding="ascii")
    (tmp_path / "s.dat.10").write_text("z\n", encoding="ascii")
    base = tmp_path / "s.dat"
    first = co.resolve_compressed_path(base)
    assert first.name == "s.dat.1"
    assert co.find_compressed_path(base) == first
    # Numeric (not lexicographic) ordering: 1, 2, 3, 10.
    assert co.read_text(base) == "a\nb\nc\nz\n"


def test_compressed_split_parts(tmp_path):
    _write_compressed(tmp_path / "c.dat.1.xz", b"x1\n")
    _write_compressed(tmp_path / "c.dat.2.xz", b"x2\n")
    base = tmp_path / "c.dat"
    first = co.resolve_compressed_path(base)
    assert first.name == "c.dat.1.xz"
    with co.compressed_open(first) as fh:
        assert fh.read() == "x1\nx2\n"


def test_single_split_part_is_opened_directly(tmp_path):
    _write_compressed(tmp_path / "one.dat.1.gz", b"only\n")
    first = co.find_compressed_path(tmp_path / "one.dat")
    assert first is not None and first.name == "one.dat.1.gz"
    with co.compressed_open(first) as fh:
        assert fh.read() == "only\n"


def test_open_split_parts_single_and_plain(tmp_path):
    part = tmp_path / "p.dat.1"
    part.write_bytes(b"raw")
    assert co._open_split_parts([part]).read() == b"raw"


def test_split_base_path():
    assert co._split_base_path(Path("/x/foo.dat.1.xz")) == Path("/x/foo.dat")
    assert co._split_base_path(Path("/x/foo.dat.3")) == Path("/x/foo.dat")
    assert co._split_base_path(Path("/x/foo.dat.xz")) is None
    assert co._split_base_path(Path("/x/xz")) is None


def test_find_split_parts_none(tmp_path):
    assert co._find_split_parts(tmp_path / "absent.dat") is None


def test_mixed_plain_and_compressed_split_parts(tmp_path):
    """Regression: ``m.dat.1`` + ``m.dat.2.xz`` used to raise ValueError.

    The plain glob ``m.dat.[0-9]*`` also matched ``m.dat.2.xz`` and the
    numeric sort key then evaluated ``int("xz")``.
    """
    (tmp_path / "m.dat.1").write_text("p1\n", encoding="ascii")
    _write_compressed(tmp_path / "m.dat.2.xz", b"x2\n")
    _write_compressed(tmp_path / "m.dat.3.gz", b"g3\n")
    (tmp_path / "m.dat.4.bak").write_text("junk\n", encoding="ascii")
    parts = co._find_split_parts(tmp_path / "m.dat")
    assert parts is not None
    assert [p.name for p in parts] == ["m.dat.1", "m.dat.2.xz", "m.dat.3.gz"]
    assert co.read_text(tmp_path / "m.dat") == "p1\nx2\ng3\n"


def test_split_parts_prefer_plain_copy_of_same_part(tmp_path):
    """A plain part and its compressed twin resolve to the plain one."""
    (tmp_path / "d.dat.1").write_text("a\n", encoding="ascii")
    _write_compressed(tmp_path / "d.dat.1.xz", b"a\n")
    _write_compressed(tmp_path / "d.dat.2.gz", b"b-gz\n")
    _write_compressed(tmp_path / "d.dat.2.xz", b"b\n")
    parts = co._find_split_parts(tmp_path / "d.dat")
    assert parts is not None
    assert [p.name for p in parts] == ["d.dat.1", "d.dat.2.xz"]
    assert co.read_text(tmp_path / "d.dat") == "a\nb\n"


def test_split_parts_require_part_one(tmp_path):
    _write_compressed(tmp_path / "n.dat.2.xz", b"x2\n")
    assert co._find_split_parts(tmp_path / "n.dat") is None


@pytest.mark.parametrize(
    ("module", "opener", "msg"),
    [("zstandard", co._open_zst, "zstandard"), ("lz4.frame", co._open_lz4, "lz4")],
)
def test_optional_codec_missing(monkeypatch, tmp_path, module, opener, msg):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == module:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ImportError, match=f"{msg} package required"):
        opener(tmp_path / "f")


# ---------------------------------------------------------------------------
# BaseTextParser
# ---------------------------------------------------------------------------


class _Parser(BaseTextParser):
    pass


def test_base_parser_item_store():
    p = _Parser("unused.dat")
    assert p.num_items == 0
    p._append({"name": "a", "number": 7})
    p._append({"value": 1})
    assert p.num_items == 2
    assert p.items == p.get_all()
    assert p.get_item_by_name("a") == {"name": "a", "number": 7}
    assert p.get_item_by_number(7) == {"name": "a", "number": 7}
    assert p.get_item_by_name("zz") is None
    assert p.get_item_by_number(99) is None


def test_base_parser_scalars():
    p = _Parser("x")
    assert p._parse_int("007") == 7
    assert p._parse_int("000") == 0
    assert p._parse_float("1.5") == 1.5
    assert p._parse_name("'GEN A'  1 2") == "GEN A"
    with pytest.raises(ValueError, match="Invalid name"):
        p._parse_name("no quotes")
    assert p._parse_array_line("1 2 3", 2) == ["1", "2", "3"]
    with pytest.raises(ValueError, match="Expected 4 fields"):
        p._parse_array_line("1 2", 4)
    assert p._parse_float_array(["1", "2.5"]) == [1.0, 2.5]
    assert p._parse_int_array(["01", "2"]) == [1, 2]
    assert p._next_idx(0) == 1
    assert p._next_idx(0, ["a", "b"]) == 1
    with pytest.raises(IndexError):
        p._next_idx(1, ["a", "b"])


def test_base_parser_validate_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        _Parser(tmp_path / "missing.dat").validate_file()
    (tmp_path / "dir.dat").mkdir()
    with pytest.raises(ValueError, match="not a file"):
        _Parser(tmp_path / "dir.dat").validate_file()
    _write_compressed(tmp_path / "ok.dat.gz", b"1\n")
    _Parser(tmp_path / "ok.dat").validate_file()


def test_base_parser_read_data_lines(tmp_path):
    content = "! comment\n\n  # hash comment\n  1 2 3  \nname\xe9\n"
    _write_compressed(tmp_path / "psr.dat.xz", content.encode("latin-1"))
    lines = _Parser(tmp_path / "psr.dat").read_data_lines()
    assert lines == ["  1 2 3", "name\xe9"]
    only_bang = _Parser(tmp_path / "psr.dat").read_data_lines(("!",))
    assert "  # hash comment" in only_bang


# ---------------------------------------------------------------------------
# json_planning
# ---------------------------------------------------------------------------


def test_build_json_item_drops_none():
    assert build_json_item(uid=1, name="g", pmax=None) == {"uid": 1, "name": "g"}


def test_write_planning_sanitises(tmp_path):
    planning = {
        "_internal": {"x": 1},
        "options": {},
        "system": {"generator_array": [{"uid": 1, "pmax": math.inf}]},
    }
    out = write_planning(planning, tmp_path / "deep" / "case.json")
    text = out.read_text(encoding="utf-8")
    assert text.endswith("\n")
    data = json.loads(text)
    assert "_internal" not in data
    assert math.isfinite(data["system"]["generator_array"][0]["pmax"])
