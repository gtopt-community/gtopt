# SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""No CEN API key is bundled: keys come from the environment only."""

from __future__ import annotations

from pathlib import Path

import pytest

from cen2gtopt import pcp_archive
from cen2gtopt._cen_client import (
    CenApiClient,
    CenApiConfig,
    CenAuthError,
    env_user_key,
)


def test_env_user_key_unset_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CEN_USER_KEY", raising=False)
    assert env_user_key() == ""


def test_env_user_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CEN_USER_KEY", " abc ")
    assert env_user_key() == "abc"


def test_empty_sip_key_raises_with_instructions() -> None:
    client = CenApiClient(CenApiConfig(user_keys={"sip": ""}))
    with pytest.raises(CenAuthError, match="CEN_USER_KEY"):
        client.get("sip", "/centrales/v4/findByDate")


def test_portal_key_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(pcp_archive.PORTAL_USER_KEY_ENV, raising=False)
    with pytest.raises(pcp_archive.MissingPortalKeyError, match="CEN_PORTAL"):
        pcp_archive.portal_user_key()


def test_portal_key_explicit_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pcp_archive.PORTAL_USER_KEY_ENV, "from-env")
    assert pcp_archive.portal_user_key() == "from-env"
    assert pcp_archive.portal_user_key("explicit") == "explicit"


def test_fetch_index_without_key_fails_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(pcp_archive.PORTAL_USER_KEY_ENV, raising=False)

    def _no_network(*_args, **_kwargs):
        raise AssertionError("network must not be reached without a key")

    monkeypatch.setattr(pcp_archive.requests, "get", _no_network)
    with pytest.raises(pcp_archive.MissingPortalKeyError):
        pcp_archive.fetch_index(cache_path=tmp_path / "idx.json", force=True)
