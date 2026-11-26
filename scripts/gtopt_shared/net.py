# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Small guards for outgoing network requests made with :mod:`urllib`.

``urllib.request.urlopen`` also opens ``file:``, ``ftp:`` and ``data:``
URLs; :func:`require_http_url` restricts a URL that may come from a
configuration file, an environment variable or a registry to
``http(s)``.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_ALLOWED = frozenset({"https", "http"})


def require_http_url(url: str, *, https_only: bool = False) -> str:
    """Return *url* unchanged when its scheme is http(s); raise otherwise."""
    parts = urlsplit(url)
    allowed = {"https"} if https_only else _ALLOWED
    if parts.scheme.lower() not in allowed or not parts.netloc:
        want = "https" if https_only else "http(s)"
        raise ValueError(f"refusing non-{want} URL: {url.split('?', 1)[0]!r}")
    return url
