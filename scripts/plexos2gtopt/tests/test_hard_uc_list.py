# SPDX-License-Identifier: BSD-3-Clause
"""The bundled PLEXOS-HARD UC list loads, has the audited size, and a
missing list is a hard error (never a silent HARD→SOFT downgrade)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gtopt_shared import resources
from gtopt_shared.resources import MissingDataFileError
from plexos2gtopt import parsers
from plexos2gtopt._uc_policy import read_uc_name_list

#: Names in ``data/cen_pcp_hard_ucs.txt`` (RES20260422 audit).
N_LISTED = 204
#: Hydro per-plant min/max/ramp names dropped under the default
#: ``--hydro-min-mode soft`` (see ``parsers._HYDRO_GATED_UC_RE``).
N_HYDRO_GATED = 12


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("GTOPT_HYDRO_MIN_MODE", raising=False)
    parsers._hydro_min_mode.cache_clear()
    parsers._load_plexos_hard_uc_list.cache_clear()
    yield
    parsers._hydro_min_mode.cache_clear()
    parsers._load_plexos_hard_uc_list.cache_clear()


def test_bundled_list_raw_count() -> None:
    path = resources.package_data_file(
        "plexos2gtopt", *parsers.PLEXOS_HARD_UC_LIST_PARTS
    )
    names = read_uc_name_list(path)
    assert len(names) == N_LISTED
    assert "BatMaxCycDay_BAT_DEL_DESIERTO" in names


def test_hard_list_default_soft_hydro_mode() -> None:
    hard = parsers._load_plexos_hard_uc_list()
    assert len(hard) == N_LISTED - N_HYDRO_GATED
    # Far more than the 2-name legacy fallback that used to mask a
    # missing file.
    assert "MACHICURA_GENT4def" in hard
    assert "MACHICURAmin" not in hard


def test_hard_list_hard_hydro_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GTOPT_HYDRO_MIN_MODE", "hard")
    hard = parsers._load_plexos_hard_uc_list()
    assert len(hard) == N_LISTED
    assert "MACHICURAmin" in hard


def test_missing_hard_list_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(resources, "_package_dir", lambda _pkg: tmp_path)
    with pytest.raises(MissingDataFileError, match="cen_pcp_hard_ucs.txt"):
        parsers._load_plexos_hard_uc_list()


def test_empty_hard_list_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "cen_pcp_hard_ucs.txt").write_text("# only comments\n")
    monkeypatch.setattr(resources, "_package_dir", lambda _pkg: tmp_path)
    with pytest.raises(ValueError, match="no constraint names"):
        parsers._load_plexos_hard_uc_list()
