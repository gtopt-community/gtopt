# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for :mod:`gtopt_operators.tls`."""

from __future__ import annotations

import logging
from pathlib import Path

import certifi
import pytest

from gtopt_operators import tls


def test_default_verifies() -> None:
    assert tls.tls_verify({}) is True


def test_ca_bundle_env_wins(tmp_path: Path) -> None:
    env = {tls.ENV_CA_BUNDLE: " /etc/ssl/custom.pem ", tls.ENV_INSECURE: "1"}
    assert tls.tls_verify(env) == "/etc/ssl/custom.pem"


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_insecure_env_disables_and_warns(
    value: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="gtopt_operators.tls"):
        assert tls.tls_verify({tls.ENV_INSECURE: value}) is False
    assert "DISABLED" in caplog.text


@pytest.mark.parametrize("value", ["", "0", "no", "false"])
def test_insecure_env_other_values_keep_verification(value: str) -> None:
    assert tls.tls_verify({tls.ENV_INSECURE: value}) is True


def test_extra_intermediates_build_cached_bundle(tmp_path: Path) -> None:
    pem = tmp_path / "inter.pem"
    pem.write_bytes(b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
    env = {"XDG_CACHE_HOME": str(tmp_path / "cache")}
    out = tls.tls_verify(env, extra_intermediates=[pem])
    assert isinstance(out, str)
    path = Path(out)
    assert path.parent == tmp_path / "cache" / "gtopt" / "tls"
    blob = path.read_bytes()
    assert blob.startswith(Path(certifi.where()).read_bytes())
    assert blob.endswith(pem.read_bytes())
    mtime = path.stat().st_mtime_ns
    # Same content -> same file, not rewritten.
    assert tls.tls_verify(env, extra_intermediates=[pem]) == out
    assert path.stat().st_mtime_ns == mtime
    # No temp leftovers.
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_bundle_rewritten_when_corrupted(tmp_path: Path) -> None:
    pem = tmp_path / "inter.pem"
    pem.write_bytes(b"PEM")
    env = {"XDG_CACHE_HOME": str(tmp_path)}
    out = Path(tls.bundle_with_intermediates([pem], env))
    out.write_bytes(b"garbage")
    assert Path(tls.bundle_with_intermediates([pem], env)) == out
    assert out.read_bytes().endswith(b"PEM")


def test_bundle_default_cache_dir_uses_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    pem = tmp_path / "inter.pem"
    pem.write_bytes(b"PEM")
    out = Path(tls.bundle_with_intermediates([pem]))
    assert out.parent == tmp_path / ".cache" / "gtopt" / "tls"


def test_bundle_write_failure_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pem = tmp_path / "inter.pem"
    pem.write_bytes(b"PEM")

    def _boom(_src: str, _dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(tls.os, "replace", _boom)
    env = {"XDG_CACHE_HOME": str(tmp_path / "c")}
    with pytest.raises(OSError, match="disk full"):
        tls.bundle_with_intermediates([pem], env)
    assert not list((tmp_path / "c" / "gtopt" / "tls").iterdir())


def test_shipped_cen_intermediate_is_a_pem_certificate() -> None:
    from gtopt_shared.resources import package_data_file

    pem = package_data_file(
        "gtopt_operators", "cen", "certs", "rapidssl_tls_rsa_ca_g1.pem"
    )
    text = pem.read_text(encoding="ascii")
    assert text.startswith("-----BEGIN CERTIFICATE-----")
    assert text.rstrip().endswith("-----END CERTIFICATE-----")
