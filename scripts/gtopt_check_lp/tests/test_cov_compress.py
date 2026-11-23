# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Branch coverage for ``gtopt_check_lp._compress``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from gtopt_check_lp import _compress as cz

_LP = "Minimize\n obj: x\nEnd\n"


def test_read_zst_roundtrip(tmp_path: Path) -> None:
    zstd = pytest.importorskip("zstandard")
    path = tmp_path / "m.lp.zst"
    path.write_bytes(zstd.ZstdCompressor().compress(_LP.encode()))
    assert cz.read_lp_text(path) == _LP
    with cz.as_plain_lp(path) as plain:
        assert plain.suffix == ".lp"
        assert plain.read_text() == _LP
    assert not plain.exists()


def test_module_without_zstandard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh copy of the module imported without ``zstandard``."""
    monkeypatch.setitem(sys.modules, "zstandard", None)
    spec = importlib.util.spec_from_file_location("_compress_nozstd", cz.__file__)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._HAS_ZSTD is False  # noqa: SLF001
    path = tmp_path / "m.lp.zst"
    path.write_bytes(b"")
    with pytest.raises(ImportError, match="zstandard package required"):
        mod.read_lp_text(path)


def test_sanitize_all_sections() -> None:
    text = (
        "\\ header comment\n"
        "Minimize\n"
        "\n"
        " obj: 2 a:b + c\n"
        "   + d:e\n"
        "Subject To\n"
        " r:1: x:1 + y >= 1\r\n"
        "  \\ inner comment\n"
        "Bounds\n"
        " 0 <= x:1 <= 5\n"
        "\n"
        "Generals\n"
        " y:2\n"
        "End\n"
    )
    out = cz.sanitize_lp_names(text)
    assert " obj: 2 a_b + c\n" in out
    assert "   + d_e\n" in out
    assert " r_1: x_1 + y >= 1\r\n" in out
    assert "  \\ inner comment\n" in out
    assert " 0 <= x_1 <= 5\n" in out
    assert " y_2\n" in out
    assert out.startswith("\\ header comment\nMinimize\n\n")


def test_as_sanitized_lp(tmp_path: Path) -> None:
    path = tmp_path / "m.lp"
    path.write_text("Subject To\n c:1: x >= 1\nEnd\n")
    with cz.as_sanitized_lp(path) as plain:
        assert " c_1: x >= 1" in plain.read_text(encoding="utf-8")
    assert not plain.exists()
