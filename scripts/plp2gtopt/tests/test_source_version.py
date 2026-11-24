# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Variant detection and ``--source-version`` for ``plp2gtopt``."""

from __future__ import annotations

import importlib
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from gtopt_shared.source_versions import PROVENANCE_KEY, format_source_versions
from plp2gtopt.main import make_parser
from plp2gtopt.source_version import SOURCE_FORMAT, detect_version

main_mod = importlib.import_module("plp2gtopt.main")

_CASES = Path(__file__).resolve().parents[2] / "cases"


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("plp_hydro_4b", ["CEN65", "cuts-plpplem"]),
        ("plp_min_ess", ["CEN65", "ESS"]),
        ("plp_min_battery", ["CEN65", "BESS"]),
        ("plp_min_cenre", ["cenfi-legacy"]),  # parser fixture: no core files
        ("plp_min_irrigation", ["CEN65", "maule-agreement", "laja-agreement"]),
    ],
)
def test_detect_repo_cases(case: str, expected: list[str]) -> None:
    assert detect_version(_CASES / case) == expected


def test_detect_edge_inputs(tmp_path: Path) -> None:
    assert not detect_version(None)
    assert not detect_version(tmp_path / "missing")
    assert not detect_version(tmp_path)


def test_detect_storage_priority_and_strcen(tmp_path: Path) -> None:
    (tmp_path / "plpcenbat.dat").write_text("0\n")
    (tmp_path / "plpess.dat.xz").write_bytes(b"")  # compressed: still ESS
    (tmp_path / "plpstrcen.dat").write_text("1\n")
    # plpcenbat.dat is ignored by the parser when plpess.dat exists.
    assert detect_version(tmp_path) == ["ESS", "PLPv6.1-strcen"]


def test_detect_cenfi_layouts_and_plaem(tmp_path: Path) -> None:
    (tmp_path / "plpcenfi.dat").write_text(
        " 2\n'A'\n'R'\n0.001 5.0\n'B'\n'R'\n 1\n 1 0.0 0.001 2.0\n"
    )
    (tmp_path / "plpplaem1.dat").write_text("")
    (tmp_path / "plpplaem2.dat").write_text("")
    assert detect_version(tmp_path) == [
        "cenfi-legacy",
        "cenfi-segmented",
        "cuts-plpplaem",
    ]
    (tmp_path / "plpcenfi.dat").write_text(" 3\n'A'\n")  # broken: no tag
    assert detect_version(tmp_path) == ["cuts-plpplaem"]


def test_help_lists_variants(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        make_parser().parse_args(["--help"])
    # argparse wraps at spaces and hyphens: compare without whitespace.
    out = "".join(capsys.readouterr().out.split())
    assert "".join(format_source_versions(SOURCE_FORMAT).split()) in out
    assert "SupportstheknownPLPvariants:CEN65(coreCENPLPfiles" in out


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_convert(opts: dict[str, Any]) -> int:
        seen.update(opts)
        return 0

    monkeypatch.setattr(main_mod, "convert_plp_case", fake_convert)
    monkeypatch.setattr(main_mod, "write_state_snapshot", lambda **_kw: None)
    monkeypatch.setattr(main_mod, "write_plp2gtopt_readme", lambda _d: None)
    monkeypatch.setattr(sys, "argv", ["plp2gtopt", "x"])
    main_mod.main(argv)
    return seen[PROVENANCE_KEY]


def test_main_records_all_variants(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    case = tmp_path / "case"
    shutil.copytree(_CASES / "plp_min_irrigation", case)
    (case / "plpstrcen.dat").write_text("1\n")
    with caplog.at_level(logging.INFO, logger="plp2gtopt"):
        record = _run_main(monkeypatch, [str(case), "-o", str(tmp_path / "o")])
    assert record["variants"] == ["CEN65", "maule-agreement", "laja-agreement"]
    assert record["version"] == "CEN65 + maule-agreement + laja-agreement"
    assert "PLPv6.1-strcen" in record["detected_variants"]
    assert any("unsupported variant 'PLPv6.1-strcen'" in w for w in record["warnings"])
    assert "PLP source variants: CEN65 + maule-agreement" in caplog.text


def test_main_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    record = _run_main(
        monkeypatch,
        [
            str(_CASES / "plp_hydro_4b"),
            "-o",
            str(tmp_path / "o"),
            "--source-version",
            "CEN65+BESS",
        ],
    )
    assert record["variants"] == ["CEN65", "BESS"]
    assert record["override"] == "CEN65+BESS"
    assert any("overrides the variants" in w for w in record["warnings"])
