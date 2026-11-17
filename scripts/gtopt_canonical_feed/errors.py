# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Typed errors raised by the canonical-feed reader."""

from __future__ import annotations


class FeedError(Exception):
    """Base class for all canonical-feed errors."""


class SchemaVersionError(FeedError):
    """Manifest schema_version disagrees with the loaded package."""


class FeedHashError(FeedError):
    """A parquet file's sha256 does not match the manifest record."""


class FeedSchemaError(FeedError):
    """A parquet file is missing a required column or has the wrong dtype."""
