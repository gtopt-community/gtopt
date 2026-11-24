# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Supported / tested source-format versions for the gtopt input converters.

Every converter (``plp2gtopt``, ``sddp2gtopt``, ``psse2gtopt``, …)
declares a module-level :class:`SourceFormat` constant naming the
versions of its source tool or format it supports and the subset that
the repository's tests or cases actually exercise.  At run time the
converter

1. detects the version of its input when the input carries one
   (``detect_version(...) -> str | None`` in each converter),
2. resolves it against the declaration with :func:`resolve_version`
   (CLI override > detected > default, with the closest-known fallback
   for unknown versions and a warning for untested ones),
3. logs the result and records it in its provenance dict with
   :func:`record_source_version` (or does all of 2-3 at once with
   :func:`apply_source_version`).

:func:`add_source_version_argument` adds the uniform ``--source-version``
CLI override; its help text is generated from the declaration by
:func:`format_source_versions`, so ``--help`` never drifts from it.

Version matching
----------------
Versions are compared after :func:`normalize_version` (trimmed,
lower-cased, leading ``v`` / ``rev`` dropped), so ``"V4"``, ``"v4"`` and
``"4"`` are the same version.  A detected version also matches a
declared *series*: ``"3.4.0"`` matches a declared ``"3.4"`` (the declared
numeric key is a prefix of the detected one).  For hexadecimal commit
ids, an abbreviation of at least 7 characters matches the full id.

Variant formats
---------------
Some sources have no version numbers but several known *variants*
(optional files or alternative layouts an input may combine, e.g. PLP
battery models).  A :class:`SourceFormat` with ``variants=True`` declares
them (with one-line ``descriptions``); :func:`resolve_variants` /
:func:`apply_source_variants` resolve a *list* of detected variant tags
(an override is a ``+`` / ``,`` separated list), drop unsupported tags
with a warning instead of a closest fallback, and record them joined as
``"CEN65 + ESS + maule-agreement"``.

Closest-known fallback
----------------------
For an ``ordered`` format, an unknown version is mapped to the newest
declared version that is not newer than it; when every declared version
is newer, to the oldest declared one.  A version without any digits, or
any unknown version of an unordered format (commit ids), falls back to
``default``.
"""

from __future__ import annotations

import argparse
import logging
import re
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "PROVENANCE_KEY",
    "SourceFormat",
    "VARIANT_SEPARATOR",
    "add_source_version_argument",
    "apply_source_variants",
    "apply_source_version",
    "format_source_versions",
    "normalize_version",
    "record_source_version",
    "resolve_variants",
    "resolve_version",
    "version_key",
]

#: Key under which :func:`record_source_version` stores its record.
PROVENANCE_KEY = "source_format"
#: Joins the variant tags of a variant format in logs and provenance.
VARIANT_SEPARATOR = " + "

_PREFIX_RE = re.compile(r"^(?:rev|v)\s*(?=\d)")
_HEX_RE = re.compile(r"^[0-9a-f]{7,40}$")
_MIN_SHA_ABBREV = 7


def normalize_version(version: object) -> str:
    """Canonical comparison form of *version* (``"V4 "`` -> ``"4"``)."""
    text = str(version).strip().lower()
    return _PREFIX_RE.sub("", text)


def version_key(version: object) -> tuple[int, ...] | None:
    """Numeric sort key of *version* (``"3.4.0"`` -> ``(3, 4, 0)``).

    Returns ``None`` when the version has no digit group at all.
    """
    groups = re.findall(r"\d+", normalize_version(version))
    return tuple(int(g) for g in groups) if groups else None


class _ArgumentContainer(Protocol):  # pylint: disable=too-few-public-methods
    """Anything with ``add_argument`` (a parser or an argument group)."""

    def add_argument(self, *args: Any, **kwargs: Any) -> argparse.Action:
        """Register one argument."""


def _is_sha(text: str) -> bool:
    return bool(_HEX_RE.match(text)) and any(c.isalpha() for c in text)


@dataclass(frozen=True)
class SourceFormat:
    """Declared versions of one converter's source tool or file format.

    Attributes:
        name: Human-readable format name (``"PSS/E RAW"``).
        supported: Every version the converter's parsers accept, oldest
            first.
        tested: The subset exercised by the repository's tests or cases
            (may be empty when nothing in the repo exercises the
            format).
        default: Version assumed when the input carries none; must be
            one of ``supported``.
        ordered: ``True`` when versions have a meaningful numeric order
            (release numbers).  ``False`` for commit ids, where an
            unknown version falls back to ``default`` instead of the
            closest one.
        variants: ``True`` when the labels are independent known
            variants an input may combine (see "Variant formats" above)
            rather than alternative versions.
        descriptions: ``(label, one-line description)`` pairs shown by
            :func:`format_source_versions`; labels must be supported.
    """

    name: str
    supported: tuple[str, ...]
    tested: tuple[str, ...]
    default: str
    ordered: bool = True
    variants: bool = False
    descriptions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.supported:
            raise ValueError(f"{self.name}: 'supported' must not be empty")
        norm = [normalize_version(v) for v in self.supported]
        if len(set(norm)) != len(norm):
            raise ValueError(f"{self.name}: duplicate supported versions")
        for ver in (*self.tested, self.default, *(k for k, _ in self.descriptions)):
            if normalize_version(ver) not in norm:
                raise ValueError(
                    f"{self.name}: version {ver!r} is not in supported "
                    f"{list(self.supported)}"
                )

    def match(self, version: object) -> str | None:
        """Declared version that *version* denotes, else ``None``."""
        text = normalize_version(version)
        if not text:
            return None
        for ver in self.supported:
            if normalize_version(ver) == text:
                return ver
        if _is_sha(text):
            for ver in self.supported:
                full = normalize_version(ver)
                if _is_sha(full) and (full.startswith(text) or text.startswith(full)):
                    return ver
            return None
        key = version_key(text)
        if key is None:
            return None
        # Series match: declared "3.4" covers detected "3.4.0".
        best: str | None = None
        best_len = 0
        for ver in self.supported:
            vkey = version_key(ver)
            if (
                vkey is not None
                and not _is_sha(normalize_version(ver))
                and len(vkey) < len(key)
                and key[: len(vkey)] == vkey
                and len(vkey) > best_len
            ):
                best, best_len = ver, len(vkey)
        return best

    def closest(self, version: object) -> str:
        """Closest declared version to an unknown *version*.

        See the module docstring for the rule.
        """
        matched = self.match(version)
        if matched is not None:
            return matched
        key = version_key(version)
        if not self.ordered or key is None or _is_sha(normalize_version(version)):
            return self.default
        keyed = [(k, v) for v in self.supported if (k := version_key(v)) is not None]
        if not keyed:
            return self.default
        older = [(k, v) for k, v in keyed if k <= key]
        if older:
            return max(older)[1]
        return min(keyed)[1]

    def is_tested(self, version: object) -> bool:
        """Whether *version* denotes one of the tested versions."""
        matched = self.match(version)
        return matched is not None and matched in self.tested

    def describe(self) -> str:
        """One-line summary, e.g. ``Supported: 32, 33 (tested: 33; default 33)``."""
        return format_source_versions(self)


def format_source_versions(fmt: SourceFormat) -> str:
    """``"Supported: 33, 34, 35 (tested: 33, 35; default 35)"``.

    A variant format reads ``"Supports the known PLP variants: CEN65
    (core files), ESS (...) (tested: CEN65, ESS; default CEN65)"``.
    """
    tested = ", ".join(fmt.tested) if fmt.tested else "none"
    if fmt.variants:
        desc = dict(fmt.descriptions)
        listed = ", ".join(
            f"{v} ({desc[v]})" if v in desc else v for v in fmt.supported
        )
        return (
            f"Supports the known {fmt.name} variants: {listed} "
            f"(tested: {tested}; default {fmt.default})"
        )
    return (
        f"Supported: {', '.join(fmt.supported)} "
        f"(tested: {tested}; default {fmt.default})"
    )


def resolve_version(
    detected: str | None,
    override: str | None,
    fmt: SourceFormat,
) -> tuple[str, list[str]]:
    """Pick the version to convert with, plus the warnings to report.

    Precedence: *override* (CLI ``--source-version``) > *detected* (from
    the input) > ``fmt.default``.  An unknown version is replaced by the
    closest declared one (see module docstring) with a warning; a
    supported but untested version is kept with a warning; an override
    that contradicts the detected version is kept with a warning.

    Returns:
        ``(version, warnings)`` where *version* is always one of
        ``fmt.supported``.
    """
    warnings: list[str] = []
    override = override.strip() if override is not None else None
    detected = detected.strip() if detected is not None else None
    requested = override or detected or None
    if (
        override
        and detected
        and (fmt.match(override) or normalize_version(override))
        != (fmt.match(detected) or normalize_version(detected))
    ):
        warnings.append(
            f"{fmt.name}: --source-version {override} overrides the "
            f"version {detected} detected in the input"
        )
    if requested is None:
        version = fmt.default
    else:
        matched = fmt.match(requested)
        if matched is None:
            version = fmt.closest(requested)
            warnings.append(
                f"{fmt.name}: unknown version {requested!r} "
                f"({format_source_versions(fmt)}); "
                f"falling back to the closest known version {version}"
            )
        else:
            version = matched
    if version not in fmt.tested:
        tested = ", ".join(fmt.tested) if fmt.tested else "none"
        warnings.append(
            f"{fmt.name}: version {version} is supported but untested "
            f"(tested: {tested}); check the conversion carefully"
        )
    return version, warnings


def record_source_version(
    provenance: MutableMapping[str, Any],
    fmt: SourceFormat,
    version: str,
    *,
    detected: str | None = None,
    override: str | None = None,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    """Store the resolved version in *provenance* under ``"source_format"``.

    Returns the stored (JSON-serialisable) record::

        {"format": "PSS/E RAW", "version": "33", "detected": "33",
         "override": null, "tested": true,
         "supported_versions": ["32", "33"], "tested_versions": ["32", "33"],
         "warnings": []}
    """
    record: dict[str, Any] = {
        "format": fmt.name,
        "version": version,
        "detected": detected,
        "override": override,
        "tested": fmt.is_tested(version),
        "supported_versions": list(fmt.supported),
        "tested_versions": list(fmt.tested),
        "warnings": list(warnings),
    }
    provenance[PROVENANCE_KEY] = record
    return record


def _split_variants(text: str) -> list[str]:
    return [t.strip() for t in re.split(r"[+,]", text) if t.strip()]


def resolve_variants(
    detected: Sequence[str] | None,
    override: str | None,
    fmt: SourceFormat,
) -> tuple[list[str], list[str]]:
    """Resolve the variant tags of a ``variants=True`` format.

    Precedence: *override* (``+`` / ``,`` separated tags) > *detected* >
    ``[fmt.default]``.  Unsupported tags are dropped with a warning (there
    is no closest variant); supported but untested tags are kept with a
    warning; an override that differs from the detected set is kept with
    a warning.

    Returns:
        ``(variants, warnings)``: supported tags in first-seen order,
        never empty.
    """
    warnings: list[str] = []
    found = [t.strip() for t in (detected or ()) if t.strip()]
    requested = _split_variants(override) if override and override.strip() else found

    def _canon(tags: Sequence[str]) -> set[str]:
        return {fmt.match(t) or normalize_version(t) for t in tags}

    if override and override.strip() and found and _canon(requested) != _canon(found):
        warnings.append(
            f"{fmt.name}: --source-version {override.strip()} overrides the "
            f"variants {VARIANT_SEPARATOR.join(found)} detected in the input"
        )
    variants: list[str] = []
    for tag in requested:
        matched = fmt.match(tag)
        if matched is None:
            warnings.append(
                f"{fmt.name}: unsupported variant {tag!r} is ignored "
                f"({format_source_versions(fmt)})"
            )
        elif matched not in variants:
            variants.append(matched)
    if not variants:
        variants.append(fmt.default)
    for tag in variants:
        if tag not in fmt.tested:
            tested = ", ".join(fmt.tested) if fmt.tested else "none"
            warnings.append(
                f"{fmt.name}: variant {tag} is supported but untested "
                f"(tested: {tested}); check the conversion carefully"
            )
    return variants, warnings


def apply_source_variants(
    fmt: SourceFormat,
    detected: Sequence[str] | None,
    override: str | None,
    *,
    provenance: MutableMapping[str, Any] | None = None,
    log: logging.Logger | None = None,
) -> list[str]:
    """Variant-format counterpart of :func:`apply_source_version`.

    Logs ``"<name> source variants: CEN65 + ESS (detected)"`` plus one
    WARNING per warning, and records the joined tags as ``version`` with
    the extra keys ``variants`` (list) and ``detected_variants`` (list);
    ``tested`` is true only when every resolved variant is tested.
    """
    logger = log if log is not None else logging.getLogger(__name__)
    variants, warnings = resolve_variants(detected, override, fmt)
    found = [t for t in (detected or ()) if t.strip()]
    forced = bool(override and override.strip())
    origin = "override" if forced else "detected" if found else "default"
    joined = VARIANT_SEPARATOR.join(variants)
    logger.info("%s source variants: %s (%s)", fmt.name, joined, origin)
    for msg in warnings:
        logger.warning("%s", msg)
    if provenance is not None:
        record = record_source_version(
            provenance,
            fmt,
            joined,
            detected=VARIANT_SEPARATOR.join(found) or None,
            override=override,
            warnings=warnings,
        )
        record["variants"] = variants
        record["detected_variants"] = found
        record["tested"] = all(v in fmt.tested for v in variants)
    return variants


def apply_source_version(
    fmt: SourceFormat,
    detected: str | None,
    override: str | None,
    *,
    provenance: MutableMapping[str, Any] | None = None,
    log: logging.Logger | None = None,
) -> str:
    """Resolve, log and (optionally) record the source version.

    Convenience wrapper over :func:`resolve_version` +
    :func:`record_source_version`: logs one INFO line with the resolved
    version and one WARNING per resolution warning on *log* (default:
    this module's logger).  Returns the resolved version.
    """
    logger = log if log is not None else logging.getLogger(__name__)
    version, warnings = resolve_version(detected, override, fmt)
    origin = "override" if override else "detected" if detected else "default"
    logger.info("%s source version: %s (%s)", fmt.name, version, origin)
    for msg in warnings:
        logger.warning("%s", msg)
    if provenance is not None:
        record_source_version(
            provenance,
            fmt,
            version,
            detected=detected,
            override=override,
            warnings=warnings,
        )
    return version


def add_source_version_argument(
    parser: _ArgumentContainer,
    fmt: SourceFormat,
    *,
    dest: str = "source_version",
) -> argparse.Action:
    """Add ``--source-version X`` to *parser* (default ``None``).

    The help text lists the supported / tested versions of *fmt*
    (:func:`format_source_versions`).
    """
    if fmt.variants:
        text = (
            f"{fmt.name} variants to assume instead of those detected in the "
            f"input, joined with '+' (unsupported variants are ignored, with "
            f"a warning). {format_source_versions(fmt)}."
        )
    else:
        text = (
            f"{fmt.name} version to assume instead of the one detected in "
            f"the input (unknown versions fall back to the closest known "
            f"one, with a warning). {format_source_versions(fmt)}."
        )
    return parser.add_argument(
        "--source-version",
        dest=dest,
        default=None,
        metavar="X",
        help=text,
    )
