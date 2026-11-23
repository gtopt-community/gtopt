# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Credentials for operator APIs: environment variables only.

API keys are never stored in the code, the registry, logs or caches.
:func:`resolve_credential` reads them from the environment variables an
``[auth.<name>]`` table names, and :func:`redact` scrubs any key-like
query parameter (and pre-signed S3 signatures) out of text before it is
logged or raised.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from gtopt_operators.registry import AuthSpec, load_registry


class MissingCredentialError(RuntimeError):
    """No environment variable of an ``[auth.*]`` table is set."""


#: Query parameters whose values are secrets (case-insensitive).
_SECRET_PARAMS = (
    "user_key",
    "userkey",
    "api_key",
    "apikey",
    "access_token",
    "token",
    "key",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "signature",
)
_SECRET_RX = re.compile(
    r"(?i)\b(" + "|".join(re.escape(p) for p in _SECRET_PARAMS) + r")=([^&\s'\"]+)"
)


def redact(text: str) -> str:
    """Replace the value of every secret-looking query parameter by ``***``."""
    return _SECRET_RX.sub(r"\1=***", text)


class _RedactFilter(logging.Filter):
    """Rewrite log records so that no secret query value survives."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        return True


_REDACT_FILTER = _RedactFilter()

#: Loggers of the HTTP stacks that print full request lines at DEBUG.
_HTTP_LOGGERS = ("urllib3.connectionpool", "requests")


def install_http_log_redaction() -> None:
    """Redact keys in urllib3/requests DEBUG request lines (idempotent)."""
    for name in _HTTP_LOGGERS:
        logger = logging.getLogger(name)
        if _REDACT_FILTER not in logger.filters:
            logger.addFilter(_REDACT_FILTER)


def strip_query(url: str) -> str:
    """Drop query string and fragment (pre-signed URLs carry signatures)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def credential_hint(spec: AuthSpec) -> str:
    """Human message telling how to provide the credential of ``spec``."""
    names = " or ".join(f"${v}" for v in spec.env) or "(none)"
    msg = f"credential '{spec.name}' not set: export {names}"
    if spec.doc:
        msg += f" ({spec.doc})"
    return msg


def resolve_credential(spec: AuthSpec, environ: Mapping[str, str] | None = None) -> str:
    """Return the secret of ``spec`` from the environment.

    Returns ``""`` for ``kind = "none"``.  Raises
    :class:`MissingCredentialError` (whose message names the variables,
    never a value) when none of the variables is set.
    """
    if spec.kind == "none":
        return ""
    env = os.environ if environ is None else environ
    for var in spec.env:
        value = env.get(var, "").strip()
        if value:
            return value
    raise MissingCredentialError(credential_hint(spec))


def operator_credential(
    operator: str, auth: str, environ: Mapping[str, str] | None = None
) -> str:
    """Shortcut: resolve ``[auth.<auth>]`` of ``operator``'s registry."""
    reg = load_registry(operator)
    try:
        spec = reg.auth[auth]
    except KeyError:
        raise MissingCredentialError(
            f"operator {operator!r} defines no [auth.{auth}]"
        ) from None
    return resolve_credential(spec, environ)
