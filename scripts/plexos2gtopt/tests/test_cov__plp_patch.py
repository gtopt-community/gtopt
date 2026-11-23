# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Edge-case coverage for :mod:`plexos2gtopt._plp_patch`."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from plexos2gtopt._plp_patch import (
    auto_detect_embalses,
    load_embalses_csv,
    patch_case_with_plp_embalses,
)
from plexos2gtopt.entities import JunctionSpec
from plexos2gtopt.tests.test_plp_patch import (
    _EMB_CSV_TEXT,
    _make_case_with_missing_pilmaiquen,
)


def test_blank_and_bad_rows(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    p = tmp_path / "embalses.csv"
    p.write_text(
        "\n\n" + _EMB_CSV_TEXT + "  11,BAD,E,abc,1.0,0.0,1.0,0.0,8,0.5,\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        entries = load_embalses_csv(p)
    assert "BAD" not in {e.name for e in entries}
    assert "unparseable" in caplog.text


def test_patch_keeps_existing_junction(tmp_path: Path) -> None:
    p = tmp_path / "embalses.csv"
    p.write_text(_EMB_CSV_TEXT, encoding="utf-8")
    case = _make_case_with_missing_pilmaiquen()
    from dataclasses import replace  # pylint: disable=import-outside-toplevel

    case = replace(case, junctions=case.junctions + (JunctionSpec(name="PILMAIQUEN"),))
    patched = patch_case_with_plp_embalses(case, p)
    assert [j.name for j in patched.junctions].count("PILMAIQUEN") == 1
    assert any(r.name == "PILMAIQUEN" for r in patched.reservoirs)


def test_auto_detect_inside_bundle_dir(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "embalses.csv").write_text(_EMB_CSV_TEXT, encoding="utf-8")
    assert auto_detect_embalses(bundle) == bundle / "embalses.csv"
