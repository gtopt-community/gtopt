"""Tests for the fetch / cache / extract logic of ``sienna_to_gtopt._bundle``.

The offline tests replace the network download with the manifest's own
content check; the round-trip tests use the real (fetched or cached)
upstream data and skip when it is unavailable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from sienna_to_gtopt import _bundle
from sienna_to_gtopt._bundle import (
    MANIFEST,
    UPSTREAM_COMMIT,
    BundleUnavailableError,
    available_bundles,
    bundle_path,
    extract_bundle,
    fetch_bundle,
)


EXPECTED_5BUS_FILES = {
    "branch.csv",
    "bus.csv",
    "gen.csv",
    "generator_mapping.yaml",
    "modifier.jl",
    "reserves.csv",
    "storage.csv",
    "Hydro_Upstream_Input.csv",
    "user_descriptors.yaml",
    "user_descriptors_var_cost.yaml",
    "timeseries_pointers_da.json",
    "timeseries_pointers_rt.json",
    "data_5bus_pu.jl",
}


def test_manifest_is_pinned():
    assert set(MANIFEST) == {"5bus", "14bus"}
    assert len(UPSTREAM_COMMIT) == 40
    for files in MANIFEST.values():
        for path, sha in files:
            assert path and len(sha) == 64
    assert {Path(p).name for p, _ in MANIFEST["5bus"]} == EXPECTED_5BUS_FILES


def test_unknown_bundle_name():
    with pytest.raises(KeyError):
        bundle_path("does_not_exist")


def test_cache_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("GTOPT_SIENNA_DATA_DIR", str(tmp_path))
    assert bundle_path("5bus") == tmp_path / "sienna_5bus.tar.zst"
    assert not available_bundles()


def test_offline_without_cache_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("GTOPT_SIENNA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GTOPT_OFFLINE", "1")
    with pytest.raises(BundleUnavailableError):
        fetch_bundle("14bus")


def _fake_download(content: dict[str, bytes]):
    def download(url: str) -> bytes:
        name = url.rsplit("/", 1)[-1]
        if name not in content:
            raise OSError(f"no route to {url}")
        return content[name]

    return download


def test_fetch_packs_verified_files(tmp_path, monkeypatch):
    """A download whose SHA-256 matches is packed into the cache archive."""
    monkeypatch.delenv("GTOPT_OFFLINE", raising=False)
    payload = b"fake 14-bus data\n"
    monkeypatch.setenv("GTOPT_SIENNA_DATA_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.setitem(
        MANIFEST,
        "14bus",
        [("psy_data/data_14bus_pu.jl", hashlib.sha256(payload).hexdigest())],
    )
    monkeypatch.setattr(
        _bundle, "_download", _fake_download({"data_14bus_pu.jl": payload})
    )
    archive = fetch_bundle("14bus")
    assert archive == tmp_path / "cache" / "sienna_14bus.tar.zst"
    assert available_bundles() == ["14bus"]

    extract_bundle.cache_clear()
    try:
        case_dir = extract_bundle("14bus")
        assert (case_dir / "data_14bus_pu.jl").read_bytes() == payload
    finally:
        extract_bundle.cache_clear()


def test_fetch_rejects_hash_mismatch(tmp_path, monkeypatch):
    monkeypatch.delenv("GTOPT_OFFLINE", raising=False)
    monkeypatch.setenv("GTOPT_SIENNA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        _bundle, "_download", _fake_download({"data_14bus_pu.jl": b"tampered"})
    )
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        fetch_bundle("14bus")
    assert not bundle_path("14bus").exists()


def test_fetch_network_error_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("GTOPT_OFFLINE", raising=False)
    monkeypatch.setenv("GTOPT_SIENNA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(_bundle, "_download", _fake_download({}))
    with pytest.raises(BundleUnavailableError):
        fetch_bundle("14bus")


def test_extract_bundle_5bus_layout(require_sienna_5bus):
    files = {p.name for p in require_sienna_5bus.iterdir()}
    missing = EXPECTED_5BUS_FILES - files
    assert not missing, f"Missing from bundle: {sorted(missing)}"


def test_extract_bundle_cached(require_sienna_5bus):
    """Second call returns the same dir (lru_cache contract)."""
    assert extract_bundle("5bus") == require_sienna_5bus
