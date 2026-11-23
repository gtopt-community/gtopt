# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""TLS verification is ON by default; secrets never reach logs/errors."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Any

import pytest
import requests

from cen2gtopt import _cen_client, pcp_archive
from cen2gtopt._cen_client import (
    INSECURE_TLS_ENV,
    CenApiClient,
    CenApiConfig,
    CenServerError,
    add_insecure_tls_argument,
    apply_insecure_tls_argument,
    redact_secrets,
    tls_verify_default,
)

# Low-entropy placeholder, built at runtime so secret scanners stay quiet.
SECRET = "-".join(["not", "a", "real", "key"])


# ---------------------------------------------------------------------------
# TLS default + explicit opt-out
# ---------------------------------------------------------------------------


def test_tls_verification_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(INSECURE_TLS_ENV, raising=False)
    assert tls_verify_default() is True
    assert CenApiConfig(user_keys={}).verify_tls is True


@pytest.mark.parametrize("value", ["0", "", "no", "false"])
def test_falsy_env_keeps_verification(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(INSECURE_TLS_ENV, value)
    assert tls_verify_default() is True


def test_env_opt_out_disables_verification_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(INSECURE_TLS_ENV, "1")
    monkeypatch.setattr(_cen_client, "_insecure_warned", False)
    with caplog.at_level(logging.WARNING, logger="cen2gtopt.client"):
        assert tls_verify_default() is False
        assert CenApiConfig(user_keys={}).verify_tls is False
    assert any("verification DISABLED" in r.message for r in caplog.records)


def test_insecure_cli_flag_exports_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(INSECURE_TLS_ENV, raising=False)
    parser = argparse.ArgumentParser()
    add_insecure_tls_argument(parser)

    apply_insecure_tls_argument(parser.parse_args([]))
    assert INSECURE_TLS_ENV not in os.environ

    apply_insecure_tls_argument(parser.parse_args(["--insecure"]))
    assert os.environ[INSECURE_TLS_ENV] == "1"
    assert tls_verify_default() is False


def test_main_cli_exposes_insecure_flag() -> None:
    from cen2gtopt.main import _build_parser  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    args = _build_parser().parse_args(
        ["--start", "2026-01-01", "--end", "2026-01-02", "--out", "o", "--insecure"]
    )
    assert args.insecure is True


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"https://sipub.api.coordinador.cl/x?user_key={SECRET}",
        f"https://h/x?page=1&user_key={SECRET}&limit=5",
        f"Max retries exceeded with url: /x?USER_KEY={SECRET} (Caused by …)",
        f"https://bucket.s3/f.zip?X-Amz-Signature={SECRET}&X-Amz-Expires=1",
        f"api_key={SECRET}",
    ],
)
def test_redact_secrets(text: str) -> None:
    out = redact_secrets(text)
    assert SECRET not in out
    assert "=***" in out


def test_redact_secrets_keeps_other_params() -> None:
    out = redact_secrets(f"/x?page=3&user_key={SECRET}&limit=5")
    assert out == "/x?page=3&user_key=***&limit=5"


class _FailingSession:
    """Stand-in for ``requests.Session`` whose GET always fails with the
    full URL (incl. ``user_key``) embedded in the error, as urllib3 does."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.verify_seen: list[Any] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.verify_seen.append(kwargs.get("verify"))
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    def close(self) -> None:
        pass


def test_client_never_logs_or_raises_the_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv(INSECURE_TLS_ENV, raising=False)
    monkeypatch.setattr(_cen_client.time, "sleep", lambda _s: None)
    client = CenApiClient(
        CenApiConfig(user_keys={"sip": SECRET}, max_retries=1, backoff_base=0.0)
    )
    session = _FailingSession()
    client._session = session  # type: ignore[assignment]  # pylint: disable=protected-access
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(CenServerError) as excinfo:
            client.get("sip", "/centrales/v4/findByDate", {"page": 1})
    assert SECRET not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert SECRET not in caplog.text
    assert "user_key=***" in caplog.text
    assert session.verify_seen == [True, True]


def test_fetch_index_error_redacts_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(INSECURE_TLS_ENV, raising=False)
    seen: dict[str, Any] = {}

    def _fail(url: str, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise requests.HTTPError(
            f"403 Client Error: Forbidden for url: {url}?user_key={SECRET}"
        )

    monkeypatch.setattr(pcp_archive.requests, "get", _fail)
    with pytest.raises(requests.RequestException) as excinfo:
        pcp_archive.fetch_index(
            user_key=SECRET, cache_path=tmp_path / "idx.json", force=True
        )
    assert SECRET not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    assert seen["verify"] is True
