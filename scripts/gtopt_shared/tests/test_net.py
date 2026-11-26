# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for :mod:`gtopt_shared.net`."""

from __future__ import annotations

import pytest

from gtopt_shared.net import require_http_url


@pytest.mark.parametrize("url", ["https://a.b/c?d=1", "http://h:8080/x", "HTTPS://X/"])
def test_http_urls_pass(url: str) -> None:
    assert require_http_url(url) == url


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://h/x", "data:,x", "https:///nohost", "x"]
)
def test_other_schemes_refused(url: str) -> None:
    with pytest.raises(ValueError, match="refusing"):
        require_http_url(url)


def test_https_only_and_query_not_echoed() -> None:
    with pytest.raises(ValueError, match="non-https") as exc:
        require_http_url("http://h/x?user_key=SECRET", https_only=True)
    assert "SECRET" not in str(exc.value)
    assert require_http_url("https://h/x", https_only=True)
