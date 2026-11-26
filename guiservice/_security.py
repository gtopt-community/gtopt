"""Authentication, security headers and log redaction for the GUI service.

Authentication
--------------
The GUI has a single shared access token (no user accounts):

* ``GTOPT_GUI_AUTH`` = ``auto`` (default) | ``on`` | ``off``.

  - ``auto``: authentication is required when the service listens on a
    non-loopback address (``GTOPT_GUI_HOST``) or when a token is set;
    a loopback-only service (the default ``127.0.0.1``) stays open, as
    before, for single-user local use.
  - ``on``: always required (e.g. on a shared multi-user host).
  - ``off``: never required (only behind an authenticating reverse proxy).

* ``GTOPT_GUI_AUTH_TOKEN``: the token.  When authentication is required
  and no token is set, :func:`resolve_auth_token` generates a random one
  at start-up and the launcher prints a login URL (``/?token=...``),
  like Jupyter.

A browser logs in once with ``/?token=<token>``: the token is checked,
an ``HttpOnly`` / ``SameSite=Strict`` session cookie is set and the
browser is redirected to the same page without the token.  Scripted
clients send ``Authorization: Bearer <token>`` (or ``X-GTOpt-Auth``).

CSRF: every state-changing request must also carry the ``X-GTOpt-GUI``
header (see ``app._request_guards``); browsers cannot add it cross-origin
without a CORS preflight, which this service never grants.

Security headers
----------------
:func:`apply_security_headers` adds a Content-Security-Policy (scripts
from this origin plus the pinned, SRI-checked vis-network CDN file;
``connect-src 'self'``; no framing), ``X-Content-Type-Options``,
``X-Frame-Options``, ``Referrer-Policy`` and ``Cache-Control: no-store``
on API responses.

Log redaction
-------------
:func:`redact` scrubs bearer tokens, ``token=`` / ``user_key=`` style
values and the configured secrets from log lines served by ``/api/logs``.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlencode

AUTH_MODE_ENV = "GTOPT_GUI_AUTH"
AUTH_TOKEN_ENV = "GTOPT_GUI_AUTH_TOKEN"  # noqa: S105 - env var name
SECRET_KEY_ENV = "GTOPT_GUI_SECRET_KEY"  # noqa: S105 - env var name
COOKIE_SECURE_ENV = "GTOPT_GUI_COOKIE_SECURE"
AUTH_HEADER = "X-GTOpt-Auth"
SESSION_KEY = "gtopt_auth"

_AUTH_MODES = frozenset({"auto", "on", "off"})
_TRUE = frozenset({"1", "true", "yes", "on"})

#: CDN origin of the one remote script (vis-network, SRI-pinned).
CDN_ORIGIN = "https://cdn.jsdelivr.net"

#: The bundled UI uses inline event handlers (``onclick=``), so scripts
#: keep ``'unsafe-inline'``; the policy still blocks scripts from any
#: other origin, plugins, framing and requests to other hosts.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        f"script-src 'self' 'unsafe-inline' {CDN_ORIGIN}",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def is_loopback_host(host: str | None) -> bool:
    """Return True when *host* (a hostname or IP literal) is loopback."""
    if not host:
        return False
    h = host.strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def auth_mode(environ: Mapping[str, str] | None = None) -> str:
    """Return the validated ``GTOPT_GUI_AUTH`` mode (``auto`` by default)."""
    env = os.environ if environ is None else environ
    mode = env.get(AUTH_MODE_ENV, "auto").strip().lower() or "auto"
    if mode not in _AUTH_MODES:
        raise ValueError(f"{AUTH_MODE_ENV}={mode!r} is invalid; use one of {sorted(_AUTH_MODES)}")
    return mode


def resolve_auth_token(
    host: str, environ: Mapping[str, str] | None = None
) -> tuple[str | None, bool]:
    """Return ``(token, generated)`` for a service bound to *host*.

    ``token`` is ``None`` when no authentication is required.
    """
    env = os.environ if environ is None else environ
    mode = auth_mode(env)
    if mode == "off":
        return None, False
    token = env.get(AUTH_TOKEN_ENV, "").strip()
    if token:
        return token, False
    if mode == "on" or not is_loopback_host(host):
        return secrets.token_urlsafe(32), True
    return None, False


def token_digest(token: str) -> str:
    """Session marker for *token* (the token itself never goes in a cookie)."""
    return hashlib.sha256(b"gtopt-gui-session:" + token.encode()).hexdigest()


def token_matches(presented: str | None, expected: str) -> bool:
    """Constant-time token comparison."""
    if not presented:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


def presented_bearer(headers: Any) -> str:
    """Token presented in ``X-GTOpt-Auth`` or ``Authorization: Bearer``."""
    token = headers.get(AUTH_HEADER, "")
    if token:
        return token.strip()
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def login_url(host: str, port: int, token: str) -> str:
    """URL that logs a browser in (printed by the launcher)."""
    shown = "localhost" if host in ("", "0.0.0.0", "::") else host  # noqa: S104
    if ":" in shown and not shown.startswith("["):
        shown = f"[{shown}]"
    return f"http://{shown}:{port}/?{urlencode({'token': token})}"


def without_token_query(path: str, args: Iterable[tuple[str, str]]) -> str:
    """*path* plus every query argument except ``token``."""
    kept = [(k, v) for k, v in args if k != "token"]
    return f"{path}?{urlencode(kept)}" if kept else path


def cookie_secure(environ: Mapping[str, str] | None = None) -> bool:
    """``Secure`` session cookies (set when served over TLS)."""
    env = os.environ if environ is None else environ
    return env.get(COOKIE_SECURE_ENV, "").strip().lower() in _TRUE


def secret_key(environ: Mapping[str, str] | None = None) -> bytes:
    """Flask session key: ``GTOPT_GUI_SECRET_KEY`` or a random per-process key.

    Set the variable when several worker processes must accept the same
    session cookie.
    """
    env = os.environ if environ is None else environ
    raw = env.get(SECRET_KEY_ENV, "")
    return raw.encode() if raw else secrets.token_bytes(32)


def apply_security_headers(response: Any, path: str) -> Any:
    """Add the security headers (and ``no-store`` on ``/api/``) to *response*."""
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b((?:auth_?)?token|user_key|api_key|apikey|password|secret)"
        r"(\s*[=:]\s*)[^\s&,;\"']+"
    ),
)


def redact(text: str, secrets_: Iterable[str] = ()) -> str:
    """Scrub credentials (and the literal *secrets_*) out of *text*."""
    for value in secrets_:
        if value and len(value) >= 4:
            text = text.replace(value, "***")
    text = _SECRET_PATTERNS[0].sub(r"\1***", text)
    return _SECRET_PATTERNS[1].sub(r"\1\2***", text)
