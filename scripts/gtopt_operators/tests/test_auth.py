# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for env-only credentials and secret redaction."""

from __future__ import annotations

import pytest

from gtopt_operators.auth import (
    MissingCredentialError,
    credential_hint,
    operator_credential,
    redact,
    resolve_credential,
    strip_query,
)
from gtopt_operators.registry import AuthSpec


def test_resolve_first_set_variable() -> None:
    spec = AuthSpec(name="sip", kind="env", env=("A_KEY", "B_KEY"))
    assert resolve_credential(spec, {"B_KEY": "b"}) == "b"
    assert resolve_credential(spec, {"A_KEY": "a", "B_KEY": "b"}) == "a"
    # Blank values do not count.
    assert resolve_credential(spec, {"A_KEY": "  ", "B_KEY": "b"}) == "b"


def test_missing_credential_names_variables_only() -> None:
    spec = AuthSpec(name="sip", kind="env", env=("A_KEY",), doc="see portal")
    with pytest.raises(MissingCredentialError) as info:
        resolve_credential(spec, {"OTHER": "secretvalue"})
    msg = str(info.value)
    assert "$A_KEY" in msg and "see portal" in msg
    assert "secretvalue" not in msg


def test_kind_none() -> None:
    assert resolve_credential(AuthSpec(name="n", kind="none"), {}) == ""


def test_credential_hint_without_env() -> None:
    assert "(none)" in credential_hint(AuthSpec(name="x", kind="env"))


def test_operator_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GTOPT_CEN_PORTAL_KEY", "k")
    assert operator_credential("cen", "portal") == "k"
    monkeypatch.delenv("GTOPT_CEN_PORTAL_KEY")
    with pytest.raises(MissingCredentialError, match="GTOPT_CEN_PORTAL_KEY"):
        operator_credential("cen", "portal")
    with pytest.raises(MissingCredentialError, match="no \\[auth.nope\\]"):
        operator_credential("cen", "nope")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "403 for url https://h/p?user_key=abc123&x=1",
            "403 for url https://h/p?user_key=***&x=1",
        ),
        ("GET /x?api_key=zzz HTTP/1.1", "GET /x?api_key=*** HTTP/1.1"),
        (
            "https://s3/b?X-Amz-Credential=A%2FB&X-Amz-Signature=deadbeef",
            "https://s3/b?X-Amz-Credential=***&X-Amz-Signature=***",
        ),
        ("no secrets here", "no secrets here"),
    ],
)
def test_redact(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_strip_query() -> None:
    assert (
        strip_query("https://s3.example/b/f.zip?X-Amz-Signature=x#frag")
        == "https://s3.example/b/f.zip"
    )


def test_http_log_redaction(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    from gtopt_operators.auth import install_http_log_redaction

    install_http_log_redaction()
    install_http_log_redaction()  # idempotent
    log = logging.getLogger("urllib3.connectionpool")
    with caplog.at_level(logging.DEBUG, logger="urllib3.connectionpool"):
        log.debug('%s "GET /x?user_key=%s HTTP/1.1" 200', "https://h:443", "SECRET")
        log.debug("plain %s", "line")
    assert "SECRET" not in caplog.text and "user_key=***" in caplog.text
    assert "plain line" in caplog.text
    assert sum(type(f).__name__ == "_RedactFilter" for f in log.filters) == 1
