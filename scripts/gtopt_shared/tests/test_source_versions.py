# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for gtopt_shared.source_versions."""

from __future__ import annotations

import argparse
import json
import logging

import pytest

from gtopt_shared.source_versions import (
    PROVENANCE_KEY,
    SourceFormat,
    add_source_version_argument,
    apply_source_version,
    format_source_versions,
    normalize_version,
    record_source_version,
    resolve_version,
    version_key,
)

PSSE = SourceFormat(
    name="PSS/E RAW", supported=("33", "34", "35"), tested=("33", "35"), default="35"
)
SERIES = SourceFormat(
    name="lib", supported=("2.13", "3.0", "3.4"), tested=("3.4",), default="3.4"
)
SHA = "a2ecae9ff3cfe6d219744f1b10e4edd4eeecabc9"
COMMIT = SourceFormat(
    name="data", supported=(SHA,), tested=(), default=SHA, ordered=False
)


def test_normalize_and_key() -> None:
    assert normalize_version(" V4 ") == "4"
    assert normalize_version("rev33") == "33"
    assert normalize_version("vintage") == "vintage"
    assert version_key("3.4.0") == (3, 4, 0)
    assert version_key("cen65") == (65,)
    assert version_key("legacy") is None


def test_format_validation() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        SourceFormat(name="x", supported=(), tested=(), default="1")
    with pytest.raises(ValueError, match="duplicate"):
        SourceFormat(name="x", supported=("1", "v1"), tested=(), default="1")
    with pytest.raises(ValueError, match="not in supported"):
        SourceFormat(name="x", supported=("1",), tested=("2",), default="1")
    with pytest.raises(ValueError, match="not in supported"):
        SourceFormat(name="x", supported=("1",), tested=(), default="2")


def test_match_exact_series_and_sha() -> None:
    assert PSSE.match("v34") == "34"
    assert PSSE.match("") is None
    assert PSSE.match("36") is None
    assert PSSE.match("abc") is None
    assert SERIES.match("3.4.0") == "3.4"
    assert SERIES.match("3.4") == "3.4"
    assert SERIES.match("3.5.1") is None
    assert COMMIT.match(SHA[:7]) == SHA
    assert COMMIT.match(SHA.upper()) == SHA
    assert COMMIT.match("deadbeef12") is None
    # A SHA never series-matches a numeric declaration.
    assert SERIES.match("3abcdef1") is None


def test_closest_rule() -> None:
    assert PSSE.closest("34") == "34"  # known
    assert PSSE.closest("36") == "35"  # newest not newer
    assert PSSE.closest("32") == "33"  # all newer -> oldest
    assert PSSE.closest("unknown") == "35"  # no digits -> default
    assert SERIES.closest("3.5.2") == "3.4"
    assert SERIES.closest("2.14.1") == "2.13"
    assert COMMIT.closest("1234567") == SHA  # unordered -> default
    mixed = SourceFormat(name="m", supported=("legacy",), tested=(), default="legacy")
    assert mixed.closest("7") == "legacy"  # no numeric declarations


def test_is_tested_and_describe() -> None:
    assert PSSE.is_tested("33")
    assert not PSSE.is_tested("34")
    assert not PSSE.is_tested("99")
    text = "Supported: 33, 34, 35 (tested: 33, 35; default 35)"
    assert PSSE.describe() == text
    assert format_source_versions(COMMIT).endswith(f"(tested: none; default {SHA})")


def test_resolve_detected_tested() -> None:
    assert resolve_version("33", None, PSSE) == ("33", [])


def test_resolve_default_when_undetectable() -> None:
    assert resolve_version(None, None, PSSE) == ("35", [])
    assert resolve_version("  ", None, PSSE) == ("35", [])


def test_resolve_untested_warns() -> None:
    version, warnings = resolve_version("34", None, PSSE)
    assert version == "34"
    assert len(warnings) == 1 and "untested" in warnings[0]


def test_resolve_unknown_falls_back() -> None:
    version, warnings = resolve_version("36", None, PSSE)
    assert version == "35"
    assert any("unknown version '36'" in w for w in warnings)
    assert any("closest known version 35" in w for w in warnings)
    version, warnings = resolve_version("31", None, PSSE)
    assert version == "33"
    assert len(warnings) == 1


def test_resolve_override_wins() -> None:
    version, warnings = resolve_version("33", "35", PSSE)
    assert version == "35"
    assert len(warnings) == 1 and "overrides" in warnings[0]
    # Same version spelled differently: no override warning.
    assert resolve_version("33", "v33", PSSE) == ("33", [])
    # Override without detection.
    assert resolve_version(None, "33", PSSE) == ("33", [])
    # Unknown override also falls back.
    version, warnings = resolve_version(None, "99", PSSE)
    assert version == "35" and "unknown" in warnings[0]


def test_resolve_commit_empty_tested() -> None:
    version, warnings = resolve_version(None, None, COMMIT)
    assert version == SHA
    assert "tested: none" in warnings[0]


def test_record_source_version() -> None:
    prov: dict[str, object] = {"other": 1}
    rec = record_source_version(
        prov, PSSE, "34", detected="34", override=None, warnings=["w"]
    )
    assert prov[PROVENANCE_KEY] is rec
    assert prov["other"] == 1
    assert rec == {
        "format": "PSS/E RAW",
        "version": "34",
        "detected": "34",
        "override": None,
        "tested": False,
        "supported_versions": ["33", "34", "35"],
        "tested_versions": ["33", "35"],
        "warnings": ["w"],
    }
    json.dumps(prov)  # JSON-serialisable


def test_apply_source_version_logs_and_records(
    caplog: pytest.LogCaptureFixture,
) -> None:
    prov: dict[str, object] = {}
    log = logging.getLogger("test.sv")
    with caplog.at_level(logging.INFO, logger="test.sv"):
        ver = apply_source_version(PSSE, "36", None, provenance=prov, log=log)
    assert ver == "35"
    assert "PSS/E RAW source version: 35 (detected)" in caplog.text
    assert "unknown version" in caplog.text
    assert prov[PROVENANCE_KEY]["version"] == "35"  # type: ignore[index]


def test_apply_source_version_default_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="gtopt_shared.source_versions"):
        assert apply_source_version(PSSE, None, "33") == "33"
        assert apply_source_version(PSSE, None, None) == "35"
    assert "(override)" in caplog.text
    assert "(default)" in caplog.text


def test_add_source_version_argument() -> None:
    parser = argparse.ArgumentParser(prog="x")
    group = parser.add_argument_group("source")
    add_source_version_argument(group, PSSE)
    assert parser.parse_args([]).source_version is None
    assert parser.parse_args(["--source-version", "34"]).source_version == "34"
    assert "Supported: 33, 34, 35 (tested: 33, 35; default 35)" in " ".join(
        parser.format_help().split()
    )
    other = argparse.ArgumentParser()
    add_source_version_argument(other, PSSE, dest="sv")
    assert other.parse_args(["--source-version", "33"]).sv == "33"
