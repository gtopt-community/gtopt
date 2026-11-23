# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for ``--operator`` selection and the operator profile interface."""

from __future__ import annotations

import argparse
import logging
import zipfile
from pathlib import Path

import pytest

from gtopt_shared.operator import (
    BaseOperatorProfile,
    OperatorProfile,
    add_operator_argument,
    get_profile,
    operator_choices,
    select_operator,
)


def test_choices_and_flag() -> None:
    choices = operator_choices()
    assert choices[:2] == ["auto", "none"] and "cen" in choices
    parser = argparse.ArgumentParser()
    add_operator_argument(parser)
    assert parser.parse_args([]).operator == "auto"
    assert parser.parse_args(["--operator", "amm"]).operator == "amm"
    with pytest.raises(SystemExit):
        parser.parse_args(["--operator", "nope"])


def test_profiles() -> None:
    assert isinstance(get_profile("cen"), OperatorProfile)
    assert get_profile("cen").operator_id == "cen"
    xm = get_profile("xm")  # package without PROFILE -> no-op profile
    assert isinstance(xm, BaseOperatorProfile)
    assert xm.detect("sddp", None, None) is None and xm.flag_defaults("sddp") == {}
    with pytest.raises(ValueError, match="unknown operator"):
        get_profile("nope")


def test_explicit_none_and_bad_format(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="gtopt_shared.operator"):
        sel = select_operator("amm", "plexos")
    assert (sel.operator, sel.mode) == ("amm", "explicit")
    assert "operator: amm [explicit]" in caplog.text
    sel = select_operator("none", "plp", log=False)
    assert sel.operator is None and sel.describe().startswith("operator: none")
    with pytest.raises(ValueError, match="unknown converter format"):
        select_operator("auto", "xlsx")
    with pytest.raises(ValueError, match="unknown operator"):
        select_operator("nope", "plp")


def test_auto_plp_is_cen() -> None:
    sel = select_operator(None, "plp", "/nonexistent")
    assert sel.operator == "cen" and sel.mode == "auto"


def test_auto_plexos(tmp_path: Path) -> None:
    generic = tmp_path / "generic"
    generic.mkdir()
    (generic / "model.xml").write_text("<x/>", encoding="utf-8")
    assert select_operator("auto", "plexos", generic).operator is None
    assert select_operator("auto", "plexos", tmp_path / "missing").operator is None

    cen_dir = tmp_path / "cen"
    cen_dir.mkdir()
    (cen_dir / "DBSEN_PRGDIARIO.xml").write_text("<x/>", encoding="utf-8")
    sel = select_operator("auto", "plexos", cen_dir)
    assert sel.operator == "cen" and "DBSEN_PRGDIARIO.xml" in sel.reason

    named = tmp_path / "DATOS20260407.zip"
    named.write_bytes(b"")
    assert select_operator("auto", "plexos", named).operator == "cen"

    inner = tmp_path / "bundle.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("x/DBSEN_PRGDIARIO.xml", "<x/>")
    assert "inside bundle.zip" in select_operator("auto", "plexos", inner).reason

    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("model.xml", "<x/>")
    assert select_operator("auto", "plexos", other).operator is None

    xml = tmp_path / "DBSEN_X.xml"
    xml.write_text("<x/>", encoding="utf-8")
    assert select_operator("auto", "plexos", xml).operator == "cen"


def test_auto_sddp(tmp_path: Path) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "sddp.dat").write_text("", encoding="utf-8")
    assert select_operator("auto", "sddp", case).operator is None
    assert select_operator("auto", "sddp", None).operator is None
    (case / "cgndgu.dat").write_text("", encoding="utf-8")
    sel = select_operator("auto", "sddp", case)
    assert sel.operator == "amm" and "gu" in sel.reason
    (case / "RESTMEX.csv").write_text("", encoding="utf-8")
    assert "RESTMEX.csv" in select_operator("auto", "sddp", case).reason


def test_auto_psse() -> None:
    ns = argparse.Namespace(nomenclatura=None, ldm=None)
    assert select_operator("auto", "psse", "x.raw", ns).operator is None
    assert select_operator("auto", "psse", "x.raw", None).operator is None
    ns.ldm = "LDM enero 2026.xlsx"
    sel = select_operator("auto", "psse", "x.raw", ns)
    assert sel.operator == "amm" and "--ldm" in sel.reason


@pytest.mark.parametrize(
    ("module", "argv"),
    [
        ("plexos2gtopt.main", ["bundle"]),
        ("plp2gtopt.main", ["in"]),
        ("sddp2gtopt.main", ["in"]),
        ("psse2gtopt.main", ["in.raw"]),
    ],
)
def test_converters_accept_operator_flag(module: str, argv: list[str]) -> None:
    import importlib

    parser = importlib.import_module(module).make_parser()
    assert parser.parse_args(argv).operator == "auto"
    assert parser.parse_args([*argv, "--operator", "none"]).operator == "none"
