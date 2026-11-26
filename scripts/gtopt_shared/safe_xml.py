# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Hardened ``xml.etree.ElementTree`` entry points for input files.

The converters read XML model files (PLEXOS ``DataSet`` exports) that
come from outside the repository.  Those files never carry a document
type declaration, so every reader goes through :func:`parse` /
:func:`iterparse`, which

* refuse any file whose prolog holds a ``<!DOCTYPE`` (and with it every
  entity declaration: no entity expansion, no external entities), and
* require expat >= 2.4.1, whose built-in amplification limits are the
  second line of defence.

No third-party dependency (``defusedxml``) is needed.
"""

from __future__ import annotations

import pyexpat
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import IO, Any, Literal, Union

#: Oldest expat with billion-laughs / amplification protection.
MIN_EXPAT_VERSION: tuple[int, int, int] = (2, 4, 1)

_CHUNK = 64 * 1024

Source = Union[str, Path, IO[bytes]]
Event = Literal["start", "end", "comment", "pi", "start-ns", "end-ns"]


class UnsafeXMLError(ValueError):
    """The XML input or the XML library is not acceptable."""


class _RootReached(Exception):
    """Internal: the prolog scan reached the root element."""


def check_expat(version: tuple[int, int, int] | None = None) -> None:
    """Raise :class:`UnsafeXMLError` when expat is older than 2.4.1."""
    ver = tuple(pyexpat.version_info if version is None else version)
    if ver < MIN_EXPAT_VERSION:
        raise UnsafeXMLError(
            f"expat {'.'.join(map(str, ver))} is too old to parse untrusted XML "
            f"safely; need >= {'.'.join(map(str, MIN_EXPAT_VERSION))} "
            "(upgrade Python / libexpat)"
        )


def _forbid_doctype(*_args: Any) -> None:
    raise UnsafeXMLError("XML document type declarations (<!DOCTYPE>) are refused")


def _stop(*_args: Any) -> None:
    raise _RootReached


def check_prolog(stream: IO[bytes]) -> None:
    """Scan *stream* up to its root element; refuse a ``<!DOCTYPE``.

    The stream is rewound to its start afterwards.
    """
    parser = pyexpat.ParserCreate()
    parser.StartDoctypeDeclHandler = _forbid_doctype
    parser.EntityDeclHandler = _forbid_doctype
    parser.StartElementHandler = _stop
    start = stream.tell()
    try:
        while chunk := stream.read(_CHUNK):
            parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except _RootReached:
        pass
    except pyexpat.ExpatError as exc:
        raise ET.ParseError(str(exc)) from exc
    finally:
        stream.seek(start)


def _checked(source: Source) -> IO[bytes]:
    """Open *source* (path or binary stream) and validate its prolog."""
    check_expat()
    if isinstance(source, (str, Path)):
        # The caller (parse/iterparse) owns and closes the handle.
        stream: IO[bytes] = open(  # pylint: disable=consider-using-with
            source, "rb"
        )
    else:
        stream = source
    try:
        check_prolog(stream)
    except BaseException:
        if stream is not source:
            stream.close()
        raise
    return stream


def parse(source: Source) -> "ET.ElementTree[ET.Element]":
    """Safe replacement for :func:`xml.etree.ElementTree.parse`."""
    stream = _checked(source)
    try:
        return ET.parse(stream)  # noqa: S314 - prolog checked above
    finally:
        if stream is not source:
            stream.close()


def iterparse(
    source: Source, events: Sequence[Event] = ("end",)
) -> Iterator[tuple[str, Any]]:
    """Safe replacement for :func:`xml.etree.ElementTree.iterparse`."""
    stream = _checked(source)
    try:
        yield from ET.iterparse(stream, events=events)  # noqa: S314
    finally:
        if stream is not source:
            stream.close()
