# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for :mod:`gtopt_shared.safe_xml`."""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from gtopt_shared import safe_xml

PLAIN = b'<?xml version="1.0"?>\n<!-- c --><root><a>1</a><a>2</a></root>'
DTD = b'<?xml version="1.0"?>\n<!DOCTYPE r [<!ENTITY x "xx">]>\n<r>&x;</r>'
EXTERNAL = b'<?xml version="1.0"?><!DOCTYPE r SYSTEM "http://x/y.dtd"><r/>'


def test_parse_path_and_str(tmp_path: Path) -> None:
    f = tmp_path / "m.xml"
    f.write_bytes(PLAIN)
    assert len(safe_xml.parse(f).getroot().findall("a")) == 2
    assert safe_xml.parse(str(f)).getroot().tag == "root"


def test_parse_stream_is_not_closed() -> None:
    buf = io.BytesIO(PLAIN)
    assert safe_xml.parse(buf).getroot().tag == "root"
    assert not buf.closed


def test_iterparse_yields_events(tmp_path: Path) -> None:
    f = tmp_path / "m.xml"
    f.write_bytes(PLAIN)
    tags = [elem.tag for _ev, elem in safe_xml.iterparse(f)]
    assert tags == ["a", "a", "root"]
    starts = [e for e, _ in safe_xml.iterparse(io.BytesIO(PLAIN), ("start",))]
    assert starts == ["start"] * 3


@pytest.mark.parametrize("payload", [DTD, EXTERNAL])
def test_doctype_refused(tmp_path: Path, payload: bytes) -> None:
    f = tmp_path / "bad.xml"
    f.write_bytes(payload)
    with pytest.raises(safe_xml.UnsafeXMLError, match="DOCTYPE"):
        safe_xml.parse(f)
    with pytest.raises(safe_xml.UnsafeXMLError):
        list(safe_xml.iterparse(f))
    stream = io.BytesIO(payload)
    with pytest.raises(safe_xml.UnsafeXMLError):
        safe_xml.parse(stream)
    assert not stream.closed


def test_large_prolog_is_scanned_in_chunks() -> None:
    comment = b"<!--" + b"x" * (3 * 64 * 1024) + b"-->"
    good = b'<?xml version="1.0"?>' + comment + b"<r/>"
    assert safe_xml.parse(io.BytesIO(good)).getroot().tag == "r"
    bad = b'<?xml version="1.0"?>' + comment + b'<!DOCTYPE r [<!ENTITY a "b">]><r/>'
    with pytest.raises(safe_xml.UnsafeXMLError):
        safe_xml.parse(io.BytesIO(bad))


def test_malformed_prolog_raises_parse_error() -> None:
    with pytest.raises(ET.ParseError):
        safe_xml.parse(io.BytesIO(b"<<not xml"))


def test_empty_document_raises_parse_error() -> None:
    with pytest.raises(ET.ParseError):
        safe_xml.parse(io.BytesIO(b""))


def test_old_expat_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    safe_xml.check_expat((2, 4, 1))
    with pytest.raises(safe_xml.UnsafeXMLError, match="too old"):
        safe_xml.check_expat((2, 2, 10))
    monkeypatch.setattr(safe_xml.pyexpat, "version_info", (2, 4, 0))
    with pytest.raises(safe_xml.UnsafeXMLError):
        safe_xml.parse(io.BytesIO(PLAIN))
