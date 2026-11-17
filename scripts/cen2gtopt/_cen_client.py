# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``CenApiClient`` — unified HTTP client for CEN's 3scale-managed
public APIs.

Six services (one per gateway hostname):

* ``operacion``     → ``operacion.api.coordinador.cl``
* ``sip``           → ``sipub.api.coordinador.cl``
* ``medidas``       → ``medidas.api.coordinador.cl/medidas-v2``
* ``planificacion`` → ``planificacion.api.coordinador.cl``
* ``mercados``      → ``mercados.api.coordinador.cl``  (write-only)
* ``infotecnica``   → ``infotecnica.api.coordinador.cl/info-uni/v1/public``

Each service has its own user_key issued from the CEN portal. The
client is configured with one user_key per service via the
``user_keys`` dict.

Auth scheme (3scale default): ``?user_key=<key>`` query parameter.
The key is appended to every outgoing request automatically; callers
pass the canonical params they care about.

Pagination: most SIP/operación endpoints carry a
``{"data": [...], "page": N, "totalPages": M}`` envelope (variants
exist — operación uses ``content`` instead of ``data``). The
client's ``paginate(...)`` helper concatenates pages transparently
and stops at ``page >= totalPages``.

TLS: certificate verification is ON by default.  If the gateway
chain is not trusted by your system bundle, point ``REQUESTS_CA_BUNDLE``
at a bundle that contains it.  As a last resort for local probes,
verification can be disabled explicitly with the ``--insecure`` CLI
flag or ``CEN2GTOPT_INSECURE_TLS=1`` — a WARNING is logged whenever
that happens.

Secrets: the ``user_key`` travels as a query parameter, so every URL
or exception text that could reach a log is passed through
:func:`redact_secrets` first.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlencode

import requests

#: Environment variable holding the CEN API (SIP) ``user_key``.
CEN_USER_KEY_ENV = "CEN_USER_KEY"

#: How to obtain / configure a CEN API key (appended to auth errors).
CEN_KEY_HELP = (
    "CEN API calls need a personal user_key.  Register (free) at "
    "https://portal.api.coordinador.cl/, subscribe to the API you need "
    "(e.g. SIP), and export it: `export CEN_USER_KEY=<your-key>` "
    "(per-service keys: CEN_OPERACION_KEY, CEN_MERCADOS_KEY, "
    "CEN_PLANIFICACION_KEY)."
)


def env_user_key(var: str = CEN_USER_KEY_ENV) -> str:
    """Return the CEN ``user_key`` stored in env var *var* ("" if unset).

    No key is bundled with gtopt: the value must come from the
    environment.  An empty result is accepted here so that clients can
    be constructed offline (cache-only use, tests); the actual network
    call raises :class:`CenAuthError` with :data:`CEN_KEY_HELP`.
    """
    return os.environ.get(var, "").strip()


_LOG = logging.getLogger("cen2gtopt.client")

#: Environment variable that, when truthy, disables TLS certificate
#: verification for every cen2gtopt HTTP call (explicit opt-out only).
INSECURE_TLS_ENV = "CEN2GTOPT_INSECURE_TLS"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

_insecure_warned = False  # pylint: disable=invalid-name


def insecure_tls_requested() -> bool:
    """Return True iff ``$CEN2GTOPT_INSECURE_TLS`` is set to a truthy value."""
    return os.environ.get(INSECURE_TLS_ENV, "").strip().lower() in _TRUTHY


def tls_verify_default() -> bool:
    """Return the ``verify=`` value to use for outgoing HTTPS requests.

    Verification is ON unless the user explicitly opted out through
    ``--insecure`` / ``$CEN2GTOPT_INSECURE_TLS``; the opt-out is logged
    once as a WARNING.
    """
    global _insecure_warned  # pylint: disable=global-statement
    if not insecure_tls_requested():
        return True
    if not _insecure_warned:
        _LOG.warning(
            "TLS certificate verification DISABLED (%s is set / --insecure) — "
            "connections are vulnerable to interception; prefer pointing "
            "REQUESTS_CA_BUNDLE at a bundle that trusts the CEN gateways",
            INSECURE_TLS_ENV,
        )
        _insecure_warned = True
    return False


def add_insecure_tls_argument(parser: argparse.ArgumentParser) -> None:
    """Add the ``--insecure`` opt-out flag to a cen2gtopt CLI parser."""
    parser.add_argument(
        "--insecure",
        action="store_true",
        help=(
            "Disable TLS certificate verification for CEN HTTPS calls "
            f"(not recommended; equivalent to {INSECURE_TLS_ENV}=1)."
        ),
    )


def apply_insecure_tls_argument(args: argparse.Namespace) -> None:
    """Honour ``--insecure`` by exporting ``$CEN2GTOPT_INSECURE_TLS``.

    Exporting the variable (rather than threading a flag through every
    call) also reaches worker processes spawned afterwards.
    """
    if getattr(args, "insecure", False):
        os.environ[INSECURE_TLS_ENV] = "1"


#: Query-string / header-like parameters whose values must never be logged.
_SECRET_PARAM_RE = re.compile(
    r"(?i)\b(user_key|api_key|apikey|access_token|token|"
    r"x-amz-signature|x-amz-credential|x-amz-security-token)=([^&\s'\"<>]+)"
)


def redact_secrets(text: object) -> str:
    """Return ``str(text)`` with secret query-parameter values replaced by ``***``.

    Applied to every URL / exception message that may end up in a log
    or a raised error (``requests`` errors embed the full request URL,
    including ``?user_key=<KEY>``).
    """
    return _SECRET_PARAM_RE.sub(r"\1=***", str(text))


# Gateway hostnames per master plan §9.2.2 + the live-probe findings
# of 2026-05-06.
SERVICE_BASE_URLS: dict[str, str] = {
    "operacion": "https://operacion.api.coordinador.cl",
    "sip": "https://sipub.api.coordinador.cl",
    "medidas": "https://medidas.api.coordinador.cl",
    "planificacion": "https://planificacion.api.coordinador.cl",
    "mercados": "https://mercados.api.coordinador.cl",
    "infotecnica": "https://infotecnica.api.coordinador.cl",
}


class CenApiError(Exception):
    """Base error for CEN API problems."""


class CenAuthError(CenApiError):
    """3scale gateway returned 'Authentication failed' (HTTP 403)."""


class CenNotFoundError(CenApiError):
    """3scale gateway returned 'No Mapping Rule matched' (HTTP 404)."""


class CenServerError(CenApiError):
    """The upstream service returned 5xx after retries."""


@dataclass(slots=True)
class CenApiConfig:
    """Configuration for one CenApiClient instance."""

    user_keys: dict[str, str]  # service → user_key (provide what you have)
    timeout: float = 30.0
    max_retries: int = 3
    backoff_base: float = 0.5  # seconds; doubles each retry
    verify_tls: bool = field(default_factory=tls_verify_default)
    user_agent: str = "cen2gtopt/1.0 (+https://github.com/gtopt-community/gtopt)"


class CenApiClient:
    """Multi-service HTTP client for the CEN public APIs."""

    def __init__(self, config: CenApiConfig) -> None:
        self.config = config
        self._session = requests.Session()
        self._session.headers["Accept"] = "application/json"
        self._session.headers["User-Agent"] = config.user_agent
        if not config.verify_tls:
            _LOG.warning(
                "TLS certificate verification DISABLED for CEN API calls — "
                "only use this for local probes"
            )
            # The WARNING above replaces urllib3's per-request
            # InsecureRequestWarning spam.
            try:
                import urllib3  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except ImportError:  # pragma: no cover
                pass

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "CenApiClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Core request
    # ------------------------------------------------------------------

    def get(
        self,
        service: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """One GET request against the named service.

        Returns the parsed JSON body. Raises ``CenAuthError`` /
        ``CenNotFoundError`` / ``CenServerError`` for the typed
        failure modes. Honours ``self.config.max_retries`` with
        exponential back-off on 429/502/503/504.
        """
        if service not in SERVICE_BASE_URLS:
            raise CenApiError(
                f"unknown service {service!r}; "
                f"expected one of {sorted(SERVICE_BASE_URLS.keys())}"
            )
        if not self.config.user_keys.get(service):
            raise CenAuthError(
                f"no user_key configured for service {service!r}; {CEN_KEY_HELP}"
            )

        merged: dict[str, Any] = dict(params or {})
        merged["user_key"] = self.config.user_keys[service]
        url = f"{SERVICE_BASE_URLS[service]}{path}?{urlencode(merged)}"

        last_exc: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            try:
                resp = self._session.get(
                    url,
                    timeout=self.config.timeout,
                    verify=self.config.verify_tls,
                )
            except requests.RequestException as exc:
                last_exc = exc
                wait = self.config.backoff_base * (2**attempt)
                _LOG.warning(
                    "request error on %s%s (attempt %d/%d): %s — retrying in %.1fs",
                    service,
                    path,
                    attempt + 1,
                    self.config.max_retries + 1,
                    redact_secrets(exc),
                    wait,
                )
                time.sleep(wait)
                continue

            # Classify response.
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise CenApiError(
                        f"expected JSON body from {service}{path}, got "
                        f"{resp.headers.get('content-type')}"
                    ) from exc
            if resp.status_code == 403:
                # 3scale "Authentication failed" — typically 21 bytes body.
                raise CenAuthError(
                    f"403 from {service}{path}: "
                    f"{redact_secrets(resp.text[:120])} (key probably not subscribed to {service})"
                )
            if resp.status_code == 404:
                raise CenNotFoundError(
                    f"404 from {service}{path}: {redact_secrets(resp.text[:120])}"
                )
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = self.config.backoff_base * (2**attempt)
                _LOG.warning(
                    "%d on %s%s (attempt %d/%d): %s — retrying in %.1fs",
                    resp.status_code,
                    service,
                    path,
                    attempt + 1,
                    self.config.max_retries + 1,
                    redact_secrets(resp.text[:80]),
                    wait,
                )
                time.sleep(wait)
                continue
            # Other 4xx/5xx — give up.
            raise CenApiError(
                f"unexpected HTTP {resp.status_code} from {service}{path}: "
                f"{redact_secrets(resp.text[:200])}"
            )

        raise CenServerError(
            f"{self.config.max_retries + 1} attempts exhausted for "
            f"{service}{path}; last error: {redact_secrets(last_exc)}"
        )

    # ------------------------------------------------------------------
    # Pagination helper
    # ------------------------------------------------------------------

    def paginate(
        self,
        service: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        *,
        page_param: str = "page",
        limit_param: str = "limit",
        page_size: int = 1000,
        max_pages: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        """Yield each page's parsed JSON body until exhausted.

        Auto-detects the response envelope:
          * SIP-style:        ``{"data": [...], ...}``
          * operación-style:  ``{"content": [...], "totalPages": M, ...}``

        Stops when:
          * ``totalPages`` is known and we've hit it, or
          * the response's ``data``/``content`` array is empty, or
          * ``max_pages`` is reached (safety fuse).
        """
        merged: dict[str, Any] = dict(params or {})
        merged.setdefault(limit_param, page_size)
        page = merged.get(page_param, 0)

        for _ in range(max_pages):
            merged[page_param] = page
            body = self.get(service, path, params=merged)
            yield body
            total_pages = body.get("totalPages")
            content = body.get("content") or body.get("data") or []
            if not content:
                return
            if total_pages is not None and page + 1 >= int(total_pages):
                return
            page += 1
