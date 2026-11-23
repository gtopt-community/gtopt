# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for LP-file parsing and the ``gtopt_check_fingerprint`` CLI."""

from __future__ import annotations

import gzip
import json
import lzma
import runpy
import sys
from pathlib import Path

import pytest

from gtopt_check_fingerprint._cli import main
from gtopt_check_fingerprint._fingerprint import (
    _parse_lp_name,
    _read_lp_file,
    compute_from_lp_file,
    compute_from_names,
    write_fingerprint_json,
)

_LP_TEXT = """\\ comment line
Minimize
 obj: 2 generator_generation_1_0_0_0 + bus_theta_1_0_0_0
Subject To
 bus_balance_1_0_0_0: generator_generation_1_0_0_0 = 10
 generator_capacity_1_0_0: generator_generation_1_0_0_0 <= 50
   + bus_theta_1_0_0_0 >= 0
 r_unnamed: generator_generation_1_0_0_0 >= 0
Bounds
 0 <= generator_generation_1_0_0_0 <= 100
 -inf <= bus_theta_1_0_0_0 <= inf
 generator_generation_1_0_0_0 <= 90
 freevar free
Generals
 generator_generation_1_0_0_0
End
 after_end_1_0: x = 1
"""


def _write(path: Path, text: str = _LP_TEXT) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


class TestParseLpName:
    """Edge cases of the label parser."""

    def test_too_few_parts(self) -> None:
        assert _parse_lp_name("a_b") is None

    def test_no_numeric_suffix(self) -> None:
        assert _parse_lp_name("gen_power_x") is None

    def test_no_variable_name(self) -> None:
        assert _parse_lp_name("gen_1_2") is None

    def test_unknown_context_width(self) -> None:
        assert _parse_lp_name("gen_p_1_0_0_0_0_0") == ("gen", "p", "Context5")

    def test_monostate(self) -> None:
        assert _parse_lp_name("gen_multi_word_7") == ("gen", "multi_word", "monostate")


class TestUntracked:
    """Names that cannot be parsed are counted as untracked."""

    def test_untracked_cols_and_rows(self) -> None:
        fp = compute_from_names(["x", "gen_p_1_0_0"], ["c1", "bus_b_1_0_0"])
        assert fp.untracked_cols == 1
        assert fp.untracked_rows == 1
        assert fp.stats.cols_by_class == {"gen": 1}
        assert fp.stats.rows_by_class == {"bus": 1}


class TestReadLpFile:
    """LP parsing over the supported compressions."""

    def _check(self, cols: list[str], rows: list[str]) -> None:
        assert cols == [
            "generator_generation_1_0_0_0",
            "bus_theta_1_0_0_0",
            "freevar",
        ]
        assert rows == ["bus_balance_1_0_0_0", "generator_capacity_1_0_0", "r_unnamed"]

    def test_plain(self, tmp_path: Path) -> None:
        self._check(*_read_lp_file(_write(tmp_path / "m.lp")))

    def test_gzip(self, tmp_path: Path) -> None:
        p = tmp_path / "m.lp.gz"
        with gzip.open(p, "wt") as f:
            f.write(_LP_TEXT)
        self._check(*_read_lp_file(p))

    def test_xz(self, tmp_path: Path) -> None:
        p = tmp_path / "m.lp.xz"
        with lzma.open(p, "wt") as f:
            f.write(_LP_TEXT)
        self._check(*_read_lp_file(p))

    def test_zst(self, tmp_path: Path) -> None:
        zstd = pytest.importorskip("zstandard")
        p = tmp_path / "m.lp.zst"
        p.write_bytes(zstd.ZstdCompressor().compress(_LP_TEXT.encode()))
        self._check(*_read_lp_file(p))

    def test_zst_without_module(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = tmp_path / "m.lp.zst"
        p.write_bytes(b"")
        monkeypatch.setitem(sys.modules, "zstandard", None)
        with pytest.raises(ImportError, match="zstandard package required"):
            _read_lp_file(p)

    def test_alternate_section_keywords(self, tmp_path: Path) -> None:
        text = "min\n obj: x\nst\n a_b_1: x >= 1\nbinary\n x\n"
        cols, rows = _read_lp_file(_write(tmp_path / "a.lp", text))
        assert not cols
        assert rows == ["a_b_1"]
        text2 = "min\ns.t.\n c_d_1: x >= 1\n"
        _, rows2 = _read_lp_file(_write(tmp_path / "b.lp", text2))
        assert rows2 == ["c_d_1"]

    def test_compute_from_lp_file(self, tmp_path: Path) -> None:
        fp = compute_from_lp_file(_write(tmp_path / "m.lp"))
        assert fp.stats.total_cols == 3
        assert fp.untracked_cols == 1  # "freevar"
        assert fp.untracked_rows == 1  # "r_unnamed"


class TestCli:
    """End-to-end CLI behaviour."""

    def test_compute_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["compute", str(_write(tmp_path / "m.lp"))]) == 0
        out = capsys.readouterr().out
        assert "Structural hash:" in out
        assert "Untracked: 1 cols, 1 rows" in out

    def test_compute_stdout_no_untracked(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        text = "Subject To\n bus_b_1_0_0: x >= 0\nBounds\n gen_p_1_0_0 <= 1\nEnd\n"
        assert main(["compute", str(_write(tmp_path / "m.lp", text))]) == 0
        assert "Untracked" not in capsys.readouterr().out

    def test_compute_to_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_json = tmp_path / "fp.json"
        lp = _write(tmp_path / "m.lp")
        assert main(["compute", str(lp), "-o", str(out_json)]) == 0
        assert f"Fingerprint written to {out_json}" in capsys.readouterr().out
        data = json.loads(out_json.read_text())
        assert data["structural"]["columns"]["untracked_count"] == 1

    def test_verify_pass_and_fail(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        lp = _write(tmp_path / "m.lp")
        golden = tmp_path / "golden.json"
        assert main(["compute", str(lp), "-o", str(golden)]) == 0
        assert golden.exists()
        assert main(["verify", "--lp-file", str(lp), "--golden", str(golden)]) == 0
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "Warning: 1 untracked cols, 1 untracked rows" in out

        # A golden with every kind of structural difference.
        other = compute_from_names(
            ["generator_generation_1_0_0_0", "line_flow_1_0_0_0"],
            ["bus_balance_1_0_0_0", "line_limit_1_0_0_0"],
        )
        write_fingerprint_json(other, golden)
        assert main(["verify", "--lp-file", str(lp), "--golden", str(golden)]) == 1
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "Added columns (1)" in out
        assert "+ bus.theta" in out
        assert "Removed columns (1)" in out
        assert "- line.flow" in out
        assert "Added rows (1)" in out
        assert "Removed rows (1)" in out

    def test_verify_pass_without_untracked(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        text = "Subject To\n bus_b_1_0_0: x >= 0\nBounds\n gen_p_1_0_0 <= 1\nEnd\n"
        lp = _write(tmp_path / "m.lp", text)
        golden = tmp_path / "g.json"
        write_fingerprint_json(compute_from_lp_file(lp), golden)
        assert main(["verify", "--lp-file", str(lp), "--golden", str(golden)]) == 0
        assert "Warning" not in capsys.readouterr().out

    def test_compare(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        c = tmp_path / "c.json"
        write_fingerprint_json(compute_from_names(["gen_p_1_0_0"], []), a)
        write_fingerprint_json(
            compute_from_names(["gen_p_1_0_0", "gen_p_2_0_0"], []), b
        )
        write_fingerprint_json(compute_from_names(["gen_q_1_0_0"], ["bus_b_1_0"]), c)

        # Match, same stats -> no stats block.
        assert main(["compare", "--actual", str(a), "--expected", str(a)]) == 0
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "Stats" not in out

        # Match, different element counts -> informational stats.
        assert main(["compare", "--actual", str(a), "--expected", str(b)]) == 0
        out = capsys.readouterr().out
        assert "Cols: 1 (actual) vs 2 (expected)" in out

        # Structural mismatch, different row counts.
        assert main(["compare", "--actual", str(a), "--expected", str(c)]) == 1
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "Rows: 0 (actual) vs 1 (expected)" in out

    def test_missing_subcommand(self) -> None:
        with pytest.raises(SystemExit):
            main([])

    def test_dunder_main(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        lp = _write(tmp_path / "m.lp")
        out = tmp_path / "fp.json"
        monkeypatch.setattr(sys, "argv", ["prog", "compute", str(lp), "-o", str(out)])
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("gtopt_check_fingerprint", run_name="__main__")
        assert exc.value.code == 0
        assert json.loads(out.read_text())["version"] == 1


def test_print_diff_only_added_rows(capsys: pytest.CaptureFixture[str]) -> None:
    """Sections with no differences are omitted from the diff report."""
    from gtopt_check_fingerprint._cli import _print_diff  # noqa: PLC0415
    from gtopt_check_fingerprint._fingerprint import (  # noqa: PLC0415
        ComparisonResult,
        FingerprintEntry,
    )

    _print_diff(
        ComparisonResult(
            match=False,
            added_rows=[FingerprintEntry("bus", "balance", "BlockContext")],
        )
    )
    out = capsys.readouterr().out
    assert "Added rows (1)" in out
    assert "columns" not in out
    assert "Removed" not in out
