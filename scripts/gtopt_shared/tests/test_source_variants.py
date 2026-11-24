# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Variant formats of gtopt_shared.source_versions."""

from __future__ import annotations

import argparse
import logging

import pytest

from gtopt_shared.source_versions import (
    PROVENANCE_KEY,
    SourceFormat,
    add_source_version_argument,
    apply_source_variants,
    format_source_versions,
    resolve_variants,
)

PLP = SourceFormat(
    name="PLP",
    supported=("CEN65", "ESS", "BESS", "laja"),
    tested=("CEN65", "ESS", "BESS"),
    default="CEN65",
    ordered=False,
    variants=True,
    descriptions=(("CEN65", "core files"), ("ESS", "plpess.dat")),
)


def test_descriptions_validated() -> None:
    with pytest.raises(ValueError, match="not in supported"):
        SourceFormat(
            name="x",
            supported=("a",),
            tested=(),
            default="a",
            descriptions=(("b", "?"),),
        )


def test_format_lists_variants() -> None:
    assert format_source_versions(PLP) == (
        "Supports the known PLP variants: CEN65 (core files), ESS (plpess.dat), "
        "BESS, laja (tested: CEN65, ESS, BESS; default CEN65)"
    )
    bare = SourceFormat(
        name="y", supported=("a",), tested=(), default="a", variants=True
    )
    assert format_source_versions(bare).endswith("(tested: none; default a)")


def test_resolve_detected_and_default() -> None:
    assert resolve_variants(["CEN65", "ess", "ESS"], None, PLP) == (
        ["CEN65", "ESS"],
        [],
    )
    assert resolve_variants(None, None, PLP) == (["CEN65"], [])
    assert resolve_variants([" "], "  ", PLP) == (["CEN65"], [])


def test_resolve_unsupported_and_untested() -> None:
    variants, warnings = resolve_variants(["CEN65", "PLPv6.1", "laja"], None, PLP)
    assert variants == ["CEN65", "laja"]
    assert any("unsupported variant 'PLPv6.1' is ignored" in w for w in warnings)
    assert any("variant laja is supported but untested" in w for w in warnings)
    variants, warnings = resolve_variants(["nope"], None, PLP)
    assert variants == ["CEN65"] and len(warnings) == 1
    bare = SourceFormat(name="z", supported=("a",), tested=(), default="a")
    assert "tested: none" in resolve_variants(None, None, bare)[1][0]


def test_resolve_override() -> None:
    variants, warnings = resolve_variants(["CEN65"], "CEN65+ESS, BESS", PLP)
    assert variants == ["CEN65", "ESS", "BESS"]
    assert len(warnings) == 1 and "overrides the variants CEN65" in warnings[0]
    assert resolve_variants(["CEN65", "ESS"], "ess+cen65", PLP) == (
        ["ESS", "CEN65"],
        [],
    )
    assert resolve_variants(None, "BESS", PLP) == (["BESS"], [])


def test_apply_records_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    prov: dict[str, object] = {}
    log = logging.getLogger("test.variants")
    with caplog.at_level(logging.INFO, logger="test.variants"):
        out = apply_source_variants(
            PLP, ["CEN65", "laja"], None, provenance=prov, log=log
        )
    assert out == ["CEN65", "laja"]
    assert "PLP source variants: CEN65 + laja (detected)" in caplog.text
    rec = prov[PROVENANCE_KEY]
    assert isinstance(rec, dict)
    assert rec["version"] == "CEN65 + laja"
    assert rec["variants"] == ["CEN65", "laja"]
    assert rec["detected_variants"] == ["CEN65", "laja"]
    assert rec["detected"] == "CEN65 + laja"
    assert rec["tested"] is False


def test_apply_default_logger(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="gtopt_shared.source_versions"):
        assert apply_source_variants(PLP, None, "ESS") == ["ESS"]
        assert apply_source_variants(PLP, [], None) == ["CEN65"]
    assert "(override)" in caplog.text and "(default)" in caplog.text
    prov: dict[str, object] = {}
    apply_source_variants(PLP, ["CEN65"], None, provenance=prov)
    rec = prov[PROVENANCE_KEY]
    assert isinstance(rec, dict)
    assert rec["tested"] is True and rec["detected"] == "CEN65"


def test_variant_help() -> None:
    parser = argparse.ArgumentParser()
    add_source_version_argument(parser, PLP)
    text = " ".join(parser.format_help().split())
    assert "joined with '+'" in text
    assert "Supports the known PLP variants: CEN65 (core files)" in text
