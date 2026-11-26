# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Tests for :mod:`gtopt_shared.gtopt_binary` (gtopt / converter discovery)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gtopt_shared import gtopt_binary
from gtopt_shared.gtopt_binary import (
    _standard_build_paths,
    find_gtopt_binary,
    find_plexos2gtopt,
    find_plp2gtopt,
)


def _exe(path: Path, mode: int = 0o755) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(mode)
    return path


def test_standard_build_paths_shape() -> None:
    paths = _standard_build_paths()
    assert len(paths) == 3
    assert paths[0].parts[-3:] == ("build", "standalone", "gtopt")
    assert paths[1].parts[-2:] == ("standalone", "gtopt")
    # Repo root is two levels above the gtopt_shared package.
    repo = Path(gtopt_binary.__file__).resolve().parent.parent.parent
    assert paths[0] == repo / "build" / "standalone" / "gtopt"
    # The CI artifact lives in a per-user cache dir, never in the old
    # shared /tmp/gtopt-ci-bin (the test session's XDG_CACHE_HOME may
    # itself sit under /tmp, so only the shared path is ruled out).
    assert paths[2] == gtopt_binary.ci_bin_dir() / "gtopt"
    assert paths[2] != Path("/tmp/gtopt-ci-bin/gtopt")


def test_ci_bin_dir_honours_xdg_cache_home(tmp_path: Path) -> None:
    env = {"XDG_CACHE_HOME": str(tmp_path)}
    assert gtopt_binary.ci_bin_dir(env) == tmp_path / "gtopt" / "ci-bin"
    assert gtopt_binary.ci_bin_dir({}) == Path.home() / ".cache" / "gtopt" / "ci-bin"


def test_env_var_takes_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _exe(tmp_path / "gtopt")
    monkeypatch.setenv("GTOPT_BIN", str(fake))
    monkeypatch.setattr(gtopt_binary.shutil, "which", lambda _n: "/elsewhere")
    assert find_gtopt_binary() == str(fake)


def test_env_var_missing_file_falls_back_to_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GTOPT_BIN", str(tmp_path / "no-such-gtopt"))
    monkeypatch.setattr(gtopt_binary.shutil, "which", lambda n: f"/usr/bin/{n}")
    assert find_gtopt_binary() == "/usr/bin/gtopt"


def test_build_dir_fallback_skips_missing_and_non_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    not_exec = _exe(tmp_path / "a" / "gtopt", mode=0o644)
    exe = _exe(tmp_path / "b" / "gtopt")
    monkeypatch.delenv("GTOPT_BIN", raising=False)
    monkeypatch.setattr(gtopt_binary.shutil, "which", lambda _n: None)
    monkeypatch.setattr(
        gtopt_binary,
        "_standard_build_paths",
        lambda: [tmp_path / "missing", not_exec, exe],
    )
    assert find_gtopt_binary() == str(exe)


def test_not_found_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plain = _exe(tmp_path / "gtopt", mode=0o644)
    monkeypatch.setenv("GTOPT_BIN", "")
    monkeypatch.setattr(gtopt_binary.shutil, "which", lambda _n: None)
    monkeypatch.setattr(gtopt_binary, "_standard_build_paths", lambda: [plain])
    assert find_gtopt_binary() is None


def test_converter_scripts_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gtopt_binary.shutil, "which", lambda n: f"/opt/{n}")
    assert find_plp2gtopt() == "/opt/plp2gtopt"
    assert find_plexos2gtopt() == "/opt/plexos2gtopt"


def test_converter_scripts_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gtopt_binary.shutil, "which", lambda _n: None)
    assert find_plp2gtopt() is None
    assert find_plexos2gtopt() is None
