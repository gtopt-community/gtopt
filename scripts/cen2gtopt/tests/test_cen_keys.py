# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""No CEN API key is bundled: keys come from the environment only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cen2gtopt import pcp_archive
from cen2gtopt._cen_client import (
    CenApiClient,
    CenApiConfig,
    CenAuthError,
    user_keys_from_env,
)
from gtopt_operators.auth import MissingCredentialError


def test_no_key_without_environment() -> None:
    assert not user_keys_from_env({})


def test_sip_key_from_environment() -> None:
    assert user_keys_from_env({"CEN_USER_KEY": "abc"}).get("sip") == "abc"


def test_empty_sip_key_raises_with_instructions() -> None:
    client = CenApiClient(CenApiConfig(user_keys={"sip": ""}))
    with pytest.raises(CenAuthError, match="CEN_USER_KEY"):
        client.get("sip", "/centrales/v4/findByDate")


def test_fetch_index_without_key_fails_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("GTOPT_CEN_PORTAL_KEY", "CEN_PORTAL_USER_KEY"):
        monkeypatch.delenv(var, raising=False)

    def _no_network(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("network must not be reached without a key")

    monkeypatch.setattr(pcp_archive.requests, "get", _no_network)
    with pytest.raises(MissingCredentialError):
        pcp_archive.fetch_index(cache_path=tmp_path / "idx.json", force=True)
